from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UsageWindow
from app.sources.base import SourceQuota


def period_label(period: str) -> str:
    return {"5min": "5 minutes", "day": "day", "month": "month"}[period]


def window_bounds(now: datetime, quota: SourceQuota) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the quota window that contains `now`."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if quota.period == "5min":
        utc = now.astimezone(timezone.utc)
        floored = (utc.minute // 5) * 5
        start = utc.replace(minute=floored, second=0, microsecond=0)
        return start, start + timedelta(minutes=5)
    zone = ZoneInfo(quota.timezone)
    local = now.astimezone(zone)
    if quota.period == "day":
        start = datetime.combine(local.date(), time.min, tzinfo=zone)
        return start, start + timedelta(days=1)
    if local.month == 12:
        end = datetime(local.year + 1, 1, 1, tzinfo=zone)
    else:
        end = datetime(local.year, local.month + 1, 1, tzinfo=zone)
    start = datetime(local.year, local.month, 1, tzinfo=zone)
    return start, end


def window_key(now: datetime, quota: SourceQuota) -> str:
    start, _end = window_bounds(now, quota)
    if quota.period == "5min":
        return "UTC:" + start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    local = start.astimezone(ZoneInfo(quota.timezone))
    if quota.period == "day":
        return f"{quota.timezone}:{local.date().isoformat()}"
    return f"{quota.timezone}:{local.year:04d}-{local.month:02d}"


def ensure_window(db: Session, source: str, now: datetime, quota: SourceQuota) -> UsageWindow:
    key = window_key(now, quota)
    row = db.scalar(select(UsageWindow).where(UsageWindow.source == source, UsageWindow.window_key == key))
    if row is None:
        start, _end = window_bounds(now, quota)
        row = UsageWindow(
            source=source,
            window_key=key,
            window_start=start.astimezone(timezone.utc),
            requests_used=0,
        )
        db.add(row)
        db.flush()
    return row


def has_capacity(window: UsageWindow, quota: SourceQuota, requests_needed: int = 1) -> bool:
    if quota.requests <= 0:
        return False
    return window.requests_used + max(requests_needed, 0) <= quota.requests


def limit_message(quota: SourceQuota) -> str:
    return (
        f"Free quota reached ({quota.title}). "
        "This limit is shared by every bot on this source. "
        "Collection resumes when the quota resets. You can close this page."
    )
