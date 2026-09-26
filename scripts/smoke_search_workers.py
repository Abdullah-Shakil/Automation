"""Smoke-test Serper, Tavily, and SerpApi: start each, tick until 1 new lead, stop."""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import init_db, session_scope
from app.models import Bot, Lead
from app.services.runner import require_cloud_worker, tick
from app.sources.registry import default_registry

SOURCES = ("serper", "tavily", "serpapi")


def _bot_for(db, source: str) -> Bot:
    bot = db.scalars(select(Bot).where(Bot.source == source).order_by(Bot.id.asc())).first()
    if bot is None:
        raise SystemExit(f"No bot for {source}. Open /?tab=bots once so bots are created.")
    return bot


def main() -> int:
    require_cloud_worker()
    get_settings.cache_clear()
    settings = get_settings()
    init_db()
    from app.routes import _ensure_worker_bots

    with session_scope() as db:
        _ensure_worker_bots(db)

    results: dict[str, str] = {}
    for source in SOURCES:
        with session_scope() as db:
            bot = _bot_for(db, source)
            adapter = default_registry.get(source)
            available, reason = adapter.is_available(settings)
            if not available:
                results[source] = f"SKIP not available: {reason}"
                continue
            before = int(bot.leads_found or 0)
            bot.status = "running"
            bot.selected_worker = ""
            bot.checkpoint = {}
            bot.progress_note = "Smoke test starting."
            bot.last_error = ""
            bot.error_count = 0
            bot.next_run_at = None
            bot_id = bot.id
            print(f"=== {source} bot #{bot_id} start (leads_found={before}) ===", flush=True)

        last_action = "idle"
        for step in range(1, 8):
            with session_scope() as db:
                last_action = tick(db, settings, default_registry)
                bot = db.get(Bot, bot_id)
                assert bot is not None
                leads_now = int(bot.leads_found or 0)
                print(
                    f"  step {step}: action={last_action} status={bot.status} "
                    f"leads_found={leads_now} note={(bot.progress_note or '')[:120]!r} "
                    f"err={(bot.last_error or '')[:120]!r}",
                    flush=True,
                )
                if leads_now > before or bot.status in {"error", "completed", "limit_reached"}:
                    break

        with session_scope() as db:
            bot = db.get(Bot, bot_id)
            assert bot is not None
            bot.status = "stopped"
            after = int(bot.leads_found or 0)
            lead = db.scalars(
                select(Lead).where(Lead.primary_source == source).order_by(Lead.id.desc())
            ).first()
            sample = lead.business_name if lead else "(none)"
            if after > before:
                results[source] = f"OK +{after - before} lead(s), sample={sample!r}, stopped"
            elif bot.last_error:
                results[source] = f"FAIL {bot.last_error}"
            else:
                results[source] = f"FAIL no new lead (leads_found {before}->{after}, last={last_action})"

    print("---", flush=True)
    failed = 0
    for source in SOURCES:
        line = results.get(source, "FAIL missing")
        print(f"{source}: {line}", flush=True)
        if not line.startswith("OK") and not line.startswith("SKIP"):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
