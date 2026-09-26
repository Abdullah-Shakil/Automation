"""Smoke-test Serper, Tavily, and SerpApi: start each worker, tick until 1 new lead, stop."""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import init_db, session_scope
from app.models import Bot, Lead, Worker
from app.seed import ensure_bots, ensure_workers
from app.services.runner import require_cloud_worker, tick
from app.sources.registry import default_registry

SOURCES = ("serper", "tavily", "serpapi")


def main() -> int:
    require_cloud_worker()
    get_settings.cache_clear()
    settings = get_settings()
    init_db()

    with session_scope() as db:
        ensure_bots(db)
        ensure_workers(db)

    results: dict[str, str] = {}
    for source in SOURCES:
        with session_scope() as db:
            bot = db.scalars(select(Bot).where(Bot.key == source)).first()
            worker = db.scalars(select(Worker).where(Worker.key == source)).first()
            if bot is None or worker is None:
                results[source] = "SKIP missing bot/worker row"
                continue
            adapter = default_registry.get(source)
            available, reason = adapter.is_available(settings)
            if not available:
                results[source] = f"SKIP not available: {reason}"
                continue
            before = int(bot.leads_found or 0)
            worker.status = "running"
            worker.progress_note = "Smoke test starting."
            worker.last_error = ""
            bot.checkpoint = {}
            bot.status = "idle"
            bot.progress_note = "Smoke test starting."
            bot.last_error = ""
            bot.error_count = 0
            bot.next_run_at = None
            bot_id = bot.id
            print(f"=== {source} worker start (leads_found={before}) ===", flush=True)

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
            worker = db.scalars(select(Worker).where(Worker.key == source)).first()
            assert bot is not None
            if worker is not None:
                worker.status = "stopped"
            after = int(bot.leads_found or 0)
            lead = db.scalars(
                select(Lead).where(Lead.primary_source == source).order_by(Lead.id.desc())
            ).first()
            sample = lead.business_name if lead else "(none)"
            if after > before:
                results[source] = f"OK +{after - before} lead(s), sample={sample!r}"
            else:
                results[source] = f"FAIL no new lead (action={last_action}, status={bot.status})"

    print("=== summary ===", flush=True)
    for key, value in results.items():
        print(f"{key}: {value}", flush=True)
    failed = [k for k, v in results.items() if v.startswith("FAIL")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
