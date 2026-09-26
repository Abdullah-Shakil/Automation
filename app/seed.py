"""Ensure bot + worker rows exist. Trades come from app.trades (no professions table)."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bot, Worker
from app.sources.base import SEARCH_LOCATION
from app.sources.registry import default_registry
from app.workers.registry import default_workers


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seed(db: Session) -> None:
    """Create missing bots/workers for the current registries."""
    ensure_bots(db)
    ensure_workers(db)


def ensure_bots(db: Session) -> None:
    now = _utcnow()
    existing = {row.key: row for row in db.scalars(select(Bot)).all()}
    collect_keys = {adapter.key for adapter in default_registry.all() if adapter.group == "collect"}
    for key, row in list(existing.items()):
        if key not in collect_keys:
            db.delete(row)
    for adapter in default_registry.all():
        if adapter.group != "collect":
            continue
        row = existing.get(adapter.key)
        if row is None:
            db.add(
                Bot(
                    key=adapter.key,
                    name=adapter.label,
                    location=SEARCH_LOCATION,
                    status="idle",
                    checkpoint={},
                    progress_note="Ready. Start a runner on the Runners tab to collect.",
                    last_error="",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            # Only write when something changed — avoids StaleDataError races while polls overlap.
            changed = False
            if row.name != adapter.label:
                row.name = adapter.label
                changed = True
            if not (row.location or "").strip():
                row.location = SEARCH_LOCATION
                changed = True
            if changed:
                row.updated_at = now


def ensure_workers(db: Session) -> None:
    now = _utcnow()
    existing = {row.key: row for row in db.scalars(select(Worker)).all()}
    wanted = {w.key for w in default_workers.all() if w.can_collect}
    for key, row in list(existing.items()):
        if key not in wanted:
            db.delete(row)
    for worker in default_workers.all():
        if not worker.can_collect:
            continue
        row = existing.get(worker.key)
        if row is None:
            db.add(
                Worker(
                    key=worker.key,
                    name=worker.label,
                    status="stopped",
                    progress_note="Stopped. Start to let the cloud collector use this API.",
                    last_error="",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            if row.name != worker.label:
                row.name = worker.label
                row.updated_at = now


# Kept for older imports / scripts that called restore_builtins
def restore_builtins(db: Session) -> int:
    ensure_bots(db)
    ensure_workers(db)
    return 0
