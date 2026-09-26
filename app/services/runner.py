import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_sessionmaker
from app.models import Bot, BotLog, WorkerHeartbeat
from app.services.dedup import upsert_lead
from app.services.usage import ensure_window, has_capacity, limit_message
from app.sources.base import FatalSourceError, FetchContext, TransientSourceError
from app.sources.registry import Registry

logger = logging.getLogger(__name__)


def cloud_collection_enabled() -> bool:
    """Collection is allowed only in the cloud runner, never from the dashboard process."""
    return os.environ.get("LEADLANE_CLOUD_WORKER", "").strip() == "1"


def require_cloud_worker() -> None:
    if cloud_collection_enabled():
        return
    raise SystemExit(
        "Collection runs only in the cloud worker. "
        "The dashboard on this PC reads and writes the shared database and does not collect. "
        "Set LEADLANE_CLOUD_WORKER=1 on the cloud runner (GitHub Actions sets this)."
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_log(db: Session, bot: Bot, level: str, message: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    db.add(BotLog(bot_id=bot.id, level=level, message=message[:2000], created_at=now))
    db.flush()
    total = db.scalar(select(func.count()).select_from(BotLog).where(BotLog.bot_id == bot.id))
    if total and total > 300:
        extra = total - 250
        old_ids = db.scalars(
            select(BotLog.id).where(BotLog.bot_id == bot.id).order_by(BotLog.id.asc()).limit(extra)
        ).all()
        if old_ids:
            db.execute(delete(BotLog).where(BotLog.id.in_(old_ids)))


def touch_heartbeat(db: Session, now: datetime) -> None:
    row = db.get(WorkerHeartbeat, 1)
    if row is None:
        db.add(WorkerHeartbeat(id=1, last_seen=now, pid=os.getpid()))
    else:
        row.last_seen = now
        row.pid = os.getpid()


def reload_bot(db: Session, bot_id: int) -> Bot | None:
    bot = db.get(Bot, bot_id)
    if bot is not None:
        db.refresh(bot)
    return bot


def fresh_status(bot_id: int) -> str | None:
    maker = get_sessionmaker()
    with maker() as session:
        return session.scalar(select(Bot.status).where(Bot.id == bot_id))


def adapter_key_for_bot(bot: Bot) -> str:
    selected = (bot.selected_worker or "").strip()
    return selected or bot.source


def resume_after_reset(db: Session, registry: Registry, now: datetime) -> int:
    bots = db.scalars(select(Bot).where(Bot.status == "limit_reached")).all()
    resumed = 0
    for bot in bots:
        try:
            adapter = registry.get(adapter_key_for_bot(bot))
        except KeyError:
            continue
        window = ensure_window(db, adapter.key, now, adapter.quota)
        if not has_capacity(window, adapter.quota, 1):
            continue
        bot.status = "running"
        bot.next_run_at = None
        bot.last_error = ""
        bot.updated_at = now
        add_log(db, bot, "info", "Quota reset. Collection is running again.", now)
        resumed += 1
    return resumed


def next_bot(db: Session, now: datetime) -> Bot | None:
    bots = db.scalars(select(Bot).where(Bot.status == "running").order_by(Bot.updated_at.asc(), Bot.id.asc())).all()
    for bot in bots:
        due = as_utc(bot.next_run_at)
        if due is None or due <= now:
            return bot
    return None


def _elapsed(started: float) -> int:
    return max(1, int(round(time.monotonic() - started)))


def tick(db: Session, settings: Settings, registry: Registry, now: datetime | None = None) -> str:
    """Advance at most one bot by one step. Safe to call again after a restart."""
    now = now or utcnow()
    touch_heartbeat(db, now)
    resume_after_reset(db, registry, now)
    bot = next_bot(db, now)
    if bot is None:
        if cloud_collection_enabled():
            from app.services.enrich import enrich_one

            try:
                if enrich_one(db, settings, now):
                    return "enriched"
            except Exception:
                logger.exception("Enrichment step failed")
        return "idle"

    try:
        adapter = registry.get(adapter_key_for_bot(bot))
    except KeyError:
        bot.status = "error"
        bot.last_error = "Unknown data source."
        bot.updated_at = now
        bot.last_run_at = now
        add_log(db, bot, "error", bot.last_error, now)
        return "error"

    available, reason = adapter.is_available(settings)
    if not available:
        bot.status = "error"
        bot.last_error = reason[:500]
        bot.updated_at = now
        bot.last_run_at = now
        bot.steps_failed += 1
        add_log(db, bot, "error", reason, now)
        return "error"

    quota = adapter.quota
    window = ensure_window(db, adapter.key, now, quota)
    ctx = FetchContext(
        location=bot.location,
        professions=list(bot.professions or []),
        checkpoint=dict(bot.checkpoint or {}),
        user_agent=settings.user_agent,
        settings=settings,
    )
    needed = adapter.estimated_requests(ctx)
    if needed > 0 and not has_capacity(window, quota, needed):
        bot.status = "limit_reached"
        bot.progress_note = limit_message(quota)[:400]
        bot.last_error = ""
        bot.updated_at = now
        add_log(db, bot, "warning", bot.progress_note, now)
        return "limited"

    bot_id = bot.id
    # Release the row before any network call so Stop can land while a request is in flight.
    db.commit()

    started = time.monotonic()
    try:
        result = adapter.fetch(ctx)
    except FatalSourceError as exc:
        return _fail(db, bot_id, settings, now, str(exc), None, fatal=True, elapsed=_elapsed(started))
    except TransientSourceError as exc:
        return _fail(db, bot_id, settings, now, str(exc), exc.retry_after, fatal=False, elapsed=_elapsed(started))
    except Exception as exc:
        logger.exception("Bot %s failed", bot_id)
        return _fail(db, bot_id, settings, now, f"Unexpected error: {exc}", None, fatal=False, elapsed=_elapsed(started))

    elapsed = _elapsed(started)
    bot = reload_bot(db, bot_id)
    window = ensure_window(db, adapter.key, now, quota)
    if bot is None:
        return "idle"

    spent = max(result.requests_made, 0)
    window.requests_used += spent
    bot.requests_made += spent
    bot.run_seconds += elapsed
    bot.last_run_at = now
    bot.steps_succeeded += 1
    stored = 0
    merged = 0
    for raw in result.leads:
        action = upsert_lead(db, raw, bot.id, now)
        if action == "created":
            bot.leads_found += 1
            stored += 1
        elif action == "merged":
            bot.duplicates_found += 1
            merged += 1

    preserved = fresh_status(bot.id)
    bot.checkpoint = result.checkpoint
    bot.progress_note = (result.progress_note or "")[:400]
    bot.error_count = 0
    bot.last_error = ""
    bot.next_run_at = None
    bot.updated_at = now
    if preserved in {"stopped", "paused"}:
        bot.status = preserved
    elif result.done:
        bot.status = "completed"
        bot.progress_note = "Collection finished."
    elif window.requests_used >= quota.requests:
        bot.status = "limit_reached"
        bot.progress_note = limit_message(quota)[:400]
    else:
        bot.status = "running"

    add_log(
        db,
        bot,
        "info",
        f"{result.log_message} Stored {stored} new, {merged} already known.",
        now,
    )
    return "stepped"


def _fail(
    db: Session,
    bot_id: int,
    settings: Settings,
    now: datetime,
    message: str,
    retry_after: float | None,
    fatal: bool,
    elapsed: int,
) -> str:
    bot = reload_bot(db, bot_id)
    if bot is None:
        return "idle"
    bot.run_seconds += elapsed
    bot.last_run_at = now
    bot.steps_failed += 1
    bot.updated_at = now
    if bot.status in {"stopped", "paused"}:
        add_log(db, bot, "error" if fatal else "warning", message, now)
        return "error" if fatal else "backoff"
    if fatal:
        bot.status = "error"
        bot.error_count += 1
        bot.last_error = message[:500]
        bot.next_run_at = None
        add_log(db, bot, "error", message, now)
        return "error"
    return _backoff(db, bot, settings, now, message, retry_after)


def _backoff(db: Session, bot: Bot, settings: Settings, now: datetime, message: str, retry_after: float | None) -> str:
    bot.error_count += 1
    if retry_after:
        delay = min(max(retry_after, 1), 900)
    else:
        delay = min(60 * (2 ** max(bot.error_count - 1, 0)), 900)
    bot.next_run_at = now + timedelta(seconds=delay)
    bot.updated_at = now
    bot.last_error = message[:500]
    if bot.error_count >= settings.max_consecutive_errors:
        bot.status = "error"
        add_log(db, bot, "error", f"Stopped after repeated problems. Last one: {message}", now)
        return "error"
    add_log(db, bot, "warning", f"Temporary problem, retrying in {int(delay)}s. {message}", now)
    return "backoff"
