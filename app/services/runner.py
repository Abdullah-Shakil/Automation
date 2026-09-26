"""Cloud collector: started workers cycle through bots that still have free quota."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_sessionmaker
from app.models import AppSetting, Bot, Log, Worker
from app.services.dedup import upsert_lead
from app.services.usage import ensure_window, has_capacity, limit_message
from app.sources.base import FatalSourceError, FetchContext, TransientSourceError
from app.sources.registry import Registry
from app.workers.registry import default_workers

logger = logging.getLogger(__name__)

# Search API bots that only run when their matching worker is started.
WORKER_BOT_KEYS = frozenset(default_workers.collect_keys())


def cloud_collection_enabled() -> bool:
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


def add_log(
    db: Session,
    *,
    kind: str,
    ref_key: str,
    level: str,
    message: str,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    db.add(
        Log(
            kind=kind,
            ref_key=ref_key,
            level=level,
            message=message[:2000],
            created_at=now,
        )
    )
    db.flush()
    total = db.scalar(select(func.count()).select_from(Log).where(Log.kind == kind, Log.ref_key == ref_key))
    if total and total > 300:
        extra = total - 250
        old_ids = db.scalars(
            select(Log.id).where(Log.kind == kind, Log.ref_key == ref_key).order_by(Log.id.asc()).limit(extra)
        ).all()
        if old_ids:
            db.execute(delete(Log).where(Log.id.in_(old_ids)))


def touch_heartbeat(db: Session, now: datetime) -> None:
    row = db.get(AppSetting, "heartbeat_at")
    stamp = now.isoformat()
    if row is None:
        db.add(AppSetting(key="heartbeat_at", value=stamp))
    else:
        row.value = stamp
    pid = db.get(AppSetting, "heartbeat_pid")
    pid_val = str(os.getpid())
    if pid is None:
        db.add(AppSetting(key="heartbeat_pid", value=pid_val))
    else:
        pid.value = pid_val


def heartbeat_view(db: Session, settings: Settings, now: datetime) -> dict:
    row = db.get(AppSetting, "heartbeat_at")
    if row is None or not row.value:
        return {
            "online": False,
            "last_seen": None,
            "detail": "The cloud collector has not reported in yet. Start a worker, then let GitHub Actions / cron run.",
        }
    try:
        last = datetime.fromisoformat(row.value)
    except ValueError:
        last = None
    last = as_utc(last)
    stale_after = max(30.0, settings.worker_poll_seconds * 4)
    online = last is not None and (now - last).total_seconds() <= stale_after
    if online:
        detail = "The cloud collector reported in just now. You can close this PC."
    else:
        detail = "The cloud collector is between runs. Started workers continue on the next cloud run."
    return {"online": online, "last_seen": last, "detail": detail}


def reload_bot(db: Session, bot_id: int) -> Bot | None:
    bot = db.get(Bot, bot_id)
    if bot is not None:
        db.refresh(bot)
    return bot


def fresh_worker_status(worker_key: str) -> str | None:
    maker = get_sessionmaker()
    with maker() as session:
        return session.scalar(select(Worker.status).where(Worker.key == worker_key))


def _apply_trade_preset(db: Session) -> str:
    from app.cloud.runners import get_trade_preset, set_trade_preset

    raw = os.environ.get("TRADE_PRESET", "").strip()
    if raw:
        return set_trade_preset(db, raw)
    return get_trade_preset(db)


def _resume_limits(db: Session, registry: Registry, now: datetime) -> None:
    resumed_any_bot = False
    for bot in db.scalars(select(Bot).where(Bot.status == "limit_reached")).all():
        try:
            adapter = registry.get(bot.key)
        except KeyError:
            continue
        window = ensure_window(db, bot.key, now, adapter.quota)
        if has_capacity(window, adapter.quota, 1):
            bot.status = "idle"
            bot.next_run_at = None
            bot.last_error = ""
            bot.updated_at = now
            resumed_any_bot = True
            add_log(db, kind="bot", ref_key=bot.key, level="info", message="Quota reset. Bot is ready again.", now=now)
    if not resumed_any_bot:
        return
    for worker in db.scalars(select(Worker).where(Worker.status == "limit_reached")).all():
        # Revive so GitHub / cron can keep cycling after a bot quota window resets.
        worker.status = "running"
        worker.last_error = ""
        worker.progress_note = "Quota window moved on. Collecting again."
        worker.updated_at = now
        add_log(
            db,
            kind="worker",
            ref_key=worker.key,
            level="info",
            message="Worker resumed after a bot quota reset.",
            now=now,
        )


def _running_workers(db: Session) -> list[Worker]:
    return list(
        db.scalars(select(Worker).where(Worker.status == "running").order_by(Worker.id.asc())).all()
    )


def _elapsed(started: float) -> int:
    return max(1, int(round(time.monotonic() - started)))


def _bot_due(bot: Bot, now: datetime) -> bool:
    due = as_utc(bot.next_run_at)
    return due is None or due <= now


def _worker_for_bot(bot: Bot, running: list[Worker]) -> Worker | None:
    """Return the started worker that may drive this bot, or None if none may."""
    running_keys = {w.key for w in running}
    if bot.key in WORKER_BOT_KEYS:
        if bot.key not in running_keys:
            return None
        return next(w for w in running if w.key == bot.key)
    if not running:
        return None
    # Native bots (Companies House, OSM, Wikidata, test adapters): any started worker enables them.
    return running[0]


def _workers_fully_spent(db: Session, running: list[Worker], registry: Registry, now: datetime) -> bool:
    """True when every bot these workers can drive is completed, errored, or out of quota (not merely waiting)."""
    bots = list(db.scalars(select(Bot).order_by(Bot.id.asc())).all())
    saw_eligible = False
    for bot in bots:
        if _worker_for_bot(bot, running) is None:
            continue
        saw_eligible = True
        if bot.status in {"completed", "error"}:
            continue
        try:
            adapter = registry.get(bot.key)
        except KeyError:
            continue
        window = ensure_window(db, bot.key, now, adapter.quota)
        if bot.status == "limit_reached" and not has_capacity(window, adapter.quota, 1):
            continue
        # Idle / active / backoff / still has quota → worker should stay running.
        return False
    return saw_eligible


def _maybe_enrich(db: Session, settings: Settings, now: datetime) -> str | None:
    if not cloud_collection_enabled():
        return None
    from app.services.enrich import enrich_one

    try:
        if enrich_one(db, settings, now):
            return "enriched"
    except Exception:
        logger.exception("Enrichment step failed")
    return None


def tick(db: Session, settings: Settings, registry: Registry, now: datetime | None = None) -> str:
    """One step: next eligible bot under a started worker. Skips bots with no quota left."""
    now = now or utcnow()
    touch_heartbeat(db, now)
    _apply_trade_preset(db)
    _resume_limits(db, registry, now)

    running = _running_workers(db)
    if not running:
        return _maybe_enrich(db, settings, now) or "idle"

    from app.cloud.runners import get_trade_preset
    from app.trades import snapshot_for_preset

    professions = snapshot_for_preset(get_trade_preset(db))
    bots = list(db.scalars(select(Bot).order_by(Bot.id.asc())).all())

    for bot in bots:
        if bot.status in {"completed", "error"}:
            continue
        if not _bot_due(bot, now):
            continue

        worker = _worker_for_bot(bot, running)
        if worker is None:
            continue

        try:
            adapter = registry.get(bot.key)
        except KeyError:
            bot.status = "error"
            bot.last_error = "Unknown data source."
            bot.updated_at = now
            add_log(db, kind="bot", ref_key=bot.key, level="error", message=bot.last_error, now=now)
            continue

        available, reason = adapter.is_available(settings)
        if not available:
            bot.status = "error"
            bot.last_error = reason[:500]
            bot.updated_at = now
            bot.steps_failed += 1
            add_log(db, kind="bot", ref_key=bot.key, level="error", message=reason, now=now)
            continue

        quota = adapter.quota
        window = ensure_window(db, bot.key, now, quota)
        ctx = FetchContext(
            location=bot.location or "England",
            professions=professions,
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
            add_log(db, kind="bot", ref_key=bot.key, level="warning", message=bot.progress_note, now=now)
            # Keep the worker running so it can move on to the next bot with quota.
            continue

        owning = worker
        return _run_step(db, settings, registry, bot, owning, adapter, ctx, window, quota, now)

    # No step this tick — park workers only when every eligible bot is spent (not waiting on backoff).
    if _workers_fully_spent(db, running, registry, now):
        for worker in running:
            worker.status = "limit_reached"
            worker.progress_note = "All eligible bots are out of free quota. Will resume when a window resets."
            worker.updated_at = now
            add_log(
                db,
                kind="worker",
                ref_key=worker.key,
                level="warning",
                message=worker.progress_note,
                now=now,
            )
        return _maybe_enrich(db, settings, now) or "exhausted"

    return _maybe_enrich(db, settings, now) or "idle"


def _run_step(db, settings, registry, bot, worker, adapter, ctx, window, quota, now) -> str:
    bot_id = bot.id
    worker_key = worker.key if worker is not None else None
    bot.status = "active"
    bot.updated_at = now
    db.commit()

    started = time.monotonic()
    try:
        result = adapter.fetch(ctx)
    except FatalSourceError as exc:
        return _fail(db, bot_id, worker_key, settings, now, str(exc), None, fatal=True, elapsed=_elapsed(started))
    except TransientSourceError as exc:
        return _fail(
            db, bot_id, worker_key, settings, now, str(exc), exc.retry_after, fatal=False, elapsed=_elapsed(started)
        )
    except Exception as exc:
        logger.exception("Bot %s failed", bot.key)
        return _fail(
            db,
            bot_id,
            worker_key,
            settings,
            now,
            f"Unexpected error: {exc}",
            None,
            fatal=False,
            elapsed=_elapsed(started),
        )

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
        action = upsert_lead(db, raw, bot.key, now)
        if action == "created":
            bot.leads_found += 1
            stored += 1
        elif action == "merged":
            bot.duplicates_found += 1
            merged += 1

    bot.checkpoint = result.checkpoint
    bot.progress_note = (result.progress_note or "")[:400]
    bot.error_count = 0
    bot.last_error = ""
    bot.next_run_at = None
    bot.updated_at = now
    if result.done:
        bot.status = "completed"
        bot.progress_note = "Collection finished for this bot."
    elif window.requests_used >= quota.requests:
        bot.status = "limit_reached"
        bot.progress_note = limit_message(quota)[:400]
    else:
        bot.status = "idle"

    stopped_mid = False
    if worker_key:
        preserved = fresh_worker_status(worker_key)
        if preserved == "stopped":
            stopped_mid = True

    worker_row = db.scalars(select(Worker).where(Worker.key == worker_key)).first() if worker_key else None
    if worker_row is not None and not stopped_mid:
        worker_row.requests_made += spent
        worker_row.run_seconds += elapsed
        # Only credit leads this worker's own API produced (not native bots it unlocked).
        if bot.key == worker_key:
            worker_row.leads_found += stored
        worker_row.last_run_at = now
        worker_row.progress_note = bot.progress_note
        worker_row.last_error = ""
        worker_row.updated_at = now
        # Do not flip the worker to limit_reached here — tick will park it when every bot is spent.

    add_log(
        db,
        kind="bot",
        ref_key=bot.key,
        level="info",
        message=f"{result.log_message} Stored {stored} new, {merged} already known.",
        now=now,
    )
    if worker_key:
        add_log(
            db,
            kind="worker",
            ref_key=worker_key,
            level="info",
            message=(
                f"Worker stopped mid-step; progress saved ({stored} new leads)."
                if stopped_mid
                else f"Stepped bot {bot.key}: {stored} new leads."
            ),
            now=now,
        )

    if stopped_mid:
        bot.status = "idle"
        return "stopped"

    if cloud_collection_enabled():
        try:
            from app.services.enrich import enrich_one

            if enrich_one(db, settings, now):
                return "stepped_enriched"
        except Exception:
            logger.exception("Enrichment after collect failed")
    return "stepped"


def _fail(
    db: Session,
    bot_id: int,
    worker_key: str | None,
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
    if worker_key and fresh_worker_status(worker_key) == "stopped":
        bot.status = "idle"
        add_log(db, kind="bot", ref_key=bot.key, level="warning", message=message, now=now)
        return "stopped"
    if fatal:
        bot.status = "error"
        bot.error_count += 1
        bot.last_error = message[:500]
        bot.next_run_at = None
        add_log(db, kind="bot", ref_key=bot.key, level="error", message=message, now=now)
        if worker_key:
            worker = db.scalars(select(Worker).where(Worker.key == worker_key)).first()
            if worker is not None:
                worker.last_error = message[:500]
                worker.updated_at = now
                add_log(db, kind="worker", ref_key=worker_key, level="error", message=message, now=now)
        return "error"
    return _backoff(db, bot, worker_key, settings, now, message, retry_after)


def _backoff(
    db: Session,
    bot: Bot,
    worker_key: str | None,
    settings: Settings,
    now: datetime,
    message: str,
    retry_after: float | None,
) -> str:
    bot.error_count += 1
    if retry_after:
        delay = min(max(retry_after, 1), 900)
    else:
        delay = min(60 * (2 ** max(bot.error_count - 1, 0)), 900)
    bot.next_run_at = now + timedelta(seconds=delay)
    bot.status = "idle"
    bot.updated_at = now
    bot.last_error = message[:500]
    if bot.error_count >= settings.max_consecutive_errors:
        bot.status = "error"
        add_log(
            db,
            kind="bot",
            ref_key=bot.key,
            level="error",
            message=f"Stopped after repeated problems. Last one: {message}",
            now=now,
        )
        return "error"
    add_log(
        db,
        kind="bot",
        ref_key=bot.key,
        level="warning",
        message=f"Temporary problem, retrying in {int(delay)}s. {message}",
        now=now,
    )
    return "backoff"
