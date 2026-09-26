"""Collect a few real leads from OpenStreetMap, and Companies House when a key is set.

Run from the repo root:  python scripts/smoke_live.py
This talks to the public Overpass and Nominatim services. Keep it occasional.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/smoke.db")
os.environ.setdefault("ENVIRONMENT", "development")

from sqlalchemy import func, select

from app.db import init_db, session_scope
from app.models import Bot, Lead
from app.services.runner import tick
from app.sources.registry import default_registry
from app.config import get_settings


def main() -> int:
    settings = get_settings()
    init_db()
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        from app.models import Profession

        electrician = db.scalar(select(Profession).where(Profession.slug == "electrician"))
        bot = Bot(
            location="Mortlake, London",
            source="overpass",
            status="running",
            professions=[
                {
                    "slug": electrician.slug,
                    "label": electrician.label,
                    "keywords": list(electrician.keywords),
                    "sic_codes": list(electrician.sic_codes),
                    "osm_tags": list(electrician.osm_tags),
                }
            ],
            checkpoint={},
            progress_note="",
            last_error="",
            leads_found=0,
            requests_made=0,
            error_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(bot)
        db.flush()
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
            from app.models import Profession

            plumber = db.scalar(select(Profession).where(Profession.slug == "plumber"))
            ch_bot = Bot(
                location="Barnes",
                source="companies_house",
                status="running",
                professions=[
                    {
                        "slug": plumber.slug,
                        "label": plumber.label,
                        "keywords": list(plumber.keywords),
                        "sic_codes": list(plumber.sic_codes),
                        "osm_tags": list(plumber.osm_tags),
                    }
                ],
                checkpoint={},
                progress_note="",
                last_error="",
                leads_found=0,
                requests_made=0,
                error_count=0,
                created_at=now,
                updated_at=now,
            )
            db.add(ch_bot)
            db.flush()
            ch_id = ch_bot.id
        else:
            print("Companies House skipped: COMPANIES_HOUSE_API_KEY is not set.")
            ch_id = None

    if ch_id is not None:
        with session_scope() as db:
            action = tick(db, settings, default_registry)
            job = db.get(Bot, ch_id)
            print(f"{action:8} {job.status:16} {job.progress_note} {job.last_error}")
            total = db.scalar(select(func.count()).select_from(Lead).where(Lead.primary_source == "companies_house"))
            print(f"Companies House leads stored: {total}")
            if not total:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
