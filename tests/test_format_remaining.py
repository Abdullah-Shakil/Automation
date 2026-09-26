from datetime import datetime, timedelta, timezone

from app.templating import format_remaining


def test_format_remaining_units():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert format_remaining(now + timedelta(seconds=30), now=now) == "<1m"
    assert format_remaining(now + timedelta(minutes=12), now=now) == "12m"
    assert format_remaining(now + timedelta(hours=3, minutes=5), now=now) == "3h 5m"
    assert format_remaining(now + timedelta(days=2, hours=4), now=now) == "2d 4h"
    assert format_remaining(now - timedelta(minutes=1), now=now) == "now"
    assert format_remaining(None, now=now) == "—"
