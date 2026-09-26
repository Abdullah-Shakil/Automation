"""Collect a few real leads from OpenStreetMap, and Companies House when a key is set.

Run from the repo root with LEADLANE_CLOUD_WORKER=1:
  python scripts/smoke_live.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/smoke.db")
os.environ.setdefault("ENVIRONMENT", "development")

from sqlalchemy import func, select

from app.config import get_settings
from app.db import init_db, session_scope
from app.models import Bot, Lead, Worker
from app.seed import ensure_bots, ensure_workers
from app.services.runner import require_cloud_worker, tick
from app.sources.registry import default_registry


def main() -> int:
    require_cloud_worker()
    settings = get_settings()
    init_db()
    now = datetime.now(timezone.utc)

    with session_scope() as db:
        ensure_bots(db)
        ensure_workers(db)
        # Start any collect worker so native bots (OSM / CH) may run.
        worker = db.scalars(select(Worker).where(Worker.key == "serper")).first()
        if worker is None:
            print("No serper worker row.", file=sys.stderr)
            return 1
        worker.status = "running"
        worker.updated_at = now
        bot = db.scalars(select(Bot).where(Bot.key == "overpass")).first()
        if bot is None:
            print("No overpass bot row.", file=sys.stderr)
            return 1
        bot.checkpoint = {}
        bot.status = "idle"
        bot.location = "Mortlake, London"
        bot.updated_at = now
        bot_id = bot.id

    for _ in range(12):
        with session_scope() as db:
            action = tick(db, settings, default_registry)
            bot = db.get(Bot, bot_id)
            print(f"{action:8} {bot.status:16} leads={bot.leads_found} {bot.progress_note}")
            if bot.status in {"completed", "error", "limit_reached"}:
                break

    with session_scope() as db:
        total = db.scalar(select(func.count()).select_from(Lead).where(Lead.primary_source == "overpass"))
        sample = db.scalars(select(Lead).where(Lead.primary_source == "overpass").limit(5)).all()
        print(f"OpenStreetMap leads stored: {total}")
        for lead in sample:
            print(f"  - {lead.business_name} | {lead.postcode or ''} | {lead.phone or ''}")
        if not total:
            print("No OpenStreetMap leads stored.", file=sys.stderr)
            return 1

        if settings.companies_house_api_key:
            ch_bot = db.scalars(select(Bot).where(Bot.key == "companies_house")).first()
            if ch_bot is None:
                print("No companies_house bot.", file=sys.stderr)
                return 1
            ch_bot.checkpoint = {}
            ch_bot.status = "idle"
            ch_bot.location = "Barnes"
            ch_bot.updated_at = now
            ch_id = ch_bot.id
        else:
            print("Companies House skipped: COMPANIES_HOUSE_API_KEY is not set.")
            ch_id = None

    if ch_id is not None:
        with session_scope() as db:
            action = tick(db, settings, default_registry)
            job = db.get(Bot, ch_id)
            print(f"{action:8} {job.status:16} {job.progress_note} {job.last_error}")
            total = db.scalar(
                select(func.count()).select_from(Lead).where(Lead.primary_source == "companies_house")
            )
            print(f"Companies House leads stored: {total}")
            if not total:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
