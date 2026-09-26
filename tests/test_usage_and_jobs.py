from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import Bot, Lead, UsageWindow, WorkerHeartbeat
from app.services.runner import tick
from app.services.usage import ensure_window, has_capacity, window_bounds, window_key
from app.sources.base import FatalSourceError, FetchResult, RawLead, SourceQuota, TransientSourceError
from app.sources.registry import Registry

DAY = SourceQuota(
    requests=1000,
    period="day",
    timezone="Europe/London",
    title="1000 requests / day",
    detail="Test quota",
)


def _settings(**updates):
    return get_settings().model_copy(update=updates)


class ScriptedAdapter:
    key = "scripted"
    label = "Scripted"
    description = "Test source"
    group = "collect"
    quota = DAY

    def __init__(self, script, quota=None):
        self.script = list(script)
        self.calls = 0
        if quota is not None:
            self.quota = quota

    def is_available(self, settings):
        return True, ""

    def estimated_requests(self, ctx):
        return 1

    def fetch(self, ctx):
        self.calls += 1
        if not self.script:
            raise AssertionError("scripted adapter ran out of steps")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _registry(*adapters):
    registry = Registry()
    for adapter in adapters:
        registry.register(adapter)
    return registry


def _bot(db, **kwargs):
    now = kwargs.get("created_at", datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc))
    bot = Bot(
        location=kwargs.get("location", "Hackney, London"),
        source=kwargs.get("source", "scripted"),
        status=kwargs.get("status", "running"),
        professions=[{"slug": "plumber", "label": "Plumbers", "keywords": ["plumber"], "sic_codes": ["43220"], "osm_tags": []}],
        checkpoint=kwargs.get("checkpoint", {}),
        progress_note="",
        last_error="",
        leads_found=0,
        duplicates_found=0,
        requests_made=0,
        steps_succeeded=0,
        steps_failed=0,
        run_seconds=0,
        error_count=0,
        next_run_at=kwargs.get("next_run_at"),
        last_run_at=None,
        created_at=now,
        updated_at=kwargs.get("updated_at", now),
    )
    db.add(bot)
    db.flush()
    return bot


def _lead(name="Pipes Ltd", external_id="1", description=None):
    return RawLead(
        business_name=name,
        profession="Plumbers",
        address=f"{external_id} High Street, London",
        postcode=f"E8 {int(external_id):02d}A",
        phone=f"0207946{int(external_id):04d}",
        description=description,
        source="scripted",
        source_url=f"https://example.test/{external_id}",
        external_id=external_id,
    )


def _page(leads, done=False, checkpoint=None):
    return FetchResult(
        leads=leads,
        checkpoint=checkpoint if checkpoint is not None else {"page": 1},
        done=done,
        requests_made=1,
        progress_note="Fetched a page",
        log_message="Fetched a page",
    )


def test_london_day_window_changes_at_uk_midnight_not_utc():
    quota = DAY
    still_today = datetime(2026, 7, 15, 22, 30, tzinfo=timezone.utc)
    next_uk_day = datetime(2026, 7, 15, 23, 30, tzinfo=timezone.utc)
    assert window_bounds(still_today, quota)[0].date().isoformat() == "2026-07-15"
    assert window_key(next_uk_day, quota).endswith("2026-07-16")


def test_five_minute_window_is_a_utc_block():
    quota = SourceQuota(requests=1, period="5min", timezone="UTC", title="1 / 5 min", detail="")
    inside = datetime(2026, 1, 15, 12, 4, tzinfo=timezone.utc)
    boundary = datetime(2026, 1, 15, 12, 5, tzinfo=timezone.utc)
    assert window_key(inside, quota) == "UTC:2026-01-15T12:00"
    assert window_key(boundary, quota) == "UTC:2026-01-15T12:05"


def test_bot_step_stores_a_lead_and_survives_a_new_session():
    adapter = ScriptedAdapter([_page([_lead(description="Boiler repairs")], done=False, checkpoint={"page": 2})])
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        action = tick(db, _settings(), _registry(adapter), now=now)
        assert action == "stepped"
        bot_id = bot.id
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        assert bot.status == "running"
        assert bot.checkpoint == {"page": 2}
        assert bot.leads_found == 1
        assert bot.duplicates_found == 0
        assert bot.requests_made == 1
        assert bot.steps_succeeded == 1
        lead = db.scalar(select(Lead))
        assert lead.description == "Boiler repairs"
        assert lead.first_bot_id == bot_id
        usage = db.scalar(select(UsageWindow).where(UsageWindow.source == "scripted"))
        assert usage.requests_used == 1
        assert db.get(WorkerHeartbeat, 1) is not None


def test_stop_prevents_further_collection():
    adapter = ScriptedAdapter([_page([_lead()])])
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        _bot(db, status="stopped")
        assert tick(db, _settings(), _registry(adapter), now=now) == "idle"
    assert adapter.calls == 0


def test_pause_is_not_collected_until_started():
    adapter = ScriptedAdapter([_page([_lead()])])
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        _bot(db, status="paused")
        assert tick(db, _settings(), _registry(adapter), now=now) == "idle"
    assert adapter.calls == 0


def test_daily_request_limit_pauses_and_resumes_next_uk_day():
    adapter = ScriptedAdapter(
        [
            _page([_lead("One Ltd", "1")], checkpoint={"page": 1}),
            _page([_lead("Two Ltd", "2")], done=True, checkpoint={"page": 2}),
        ],
        quota=SourceQuota(requests=1, period="day", timezone="Europe/London", title="1 / day", detail=""),
    )
    day_one = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    later_same_day = day_one + timedelta(hours=3)
    next_day = datetime(2026, 1, 16, 8, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        assert tick(db, _settings(), _registry(adapter), now=day_one) == "stepped"
        db.refresh(bot)
        assert bot.status == "limit_reached"
        assert adapter.calls == 1
        assert tick(db, _settings(), _registry(adapter), now=later_same_day) == "idle"
        assert adapter.calls == 1
        assert tick(db, _settings(), _registry(adapter), now=next_day) == "stepped"
        db.refresh(bot)
        assert bot.status == "completed"
        assert adapter.calls == 2
        assert db.scalar(select(func.count()).select_from(Lead)) == 2


def test_five_minute_quota_resumes_in_the_next_utc_block():
    adapter = ScriptedAdapter(
        [
            _page([_lead("One Ltd", "1")], checkpoint={"page": 1}),
            _page([_lead("Two Ltd", "2")], done=True, checkpoint={"page": 2}),
        ],
        quota=SourceQuota(requests=1, period="5min", timezone="UTC", title="1 / 5 min", detail=""),
    )
    first = datetime(2026, 1, 15, 12, 1, tzinfo=timezone.utc)
    still = datetime(2026, 1, 15, 12, 4, tzinfo=timezone.utc)
    nxt = datetime(2026, 1, 15, 12, 5, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        assert tick(db, _settings(), _registry(adapter), now=first) == "stepped"
        db.refresh(bot)
        assert bot.status == "limit_reached"
        assert tick(db, _settings(), _registry(adapter), now=still) == "idle"
        assert tick(db, _settings(), _registry(adapter), now=nxt) == "stepped"
        db.refresh(bot)
        assert bot.status == "completed"


def test_quota_is_shared_across_bots_on_the_same_source():
    adapter = ScriptedAdapter(
        [
            _page([_lead("One Ltd", "1")], checkpoint={"page": 1}),
            _page([_lead("Two Ltd", "2")], done=True, checkpoint={"page": 2}),
        ],
        quota=SourceQuota(requests=1, period="day", timezone="Europe/London", title="1 / day", detail=""),
    )
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        first = _bot(db, updated_at=now)
        second = _bot(db, updated_at=now + timedelta(seconds=1))
        assert tick(db, _settings(), _registry(adapter), now=now) == "stepped"
        db.refresh(first)
        assert first.status == "limit_reached"
        assert tick(db, _settings(), _registry(adapter), now=now + timedelta(minutes=1)) == "limited"
        db.refresh(second)
        assert second.status == "limit_reached"
        assert adapter.calls == 1


def test_a_different_source_is_not_blocked_by_another_quota():
    limited = ScriptedAdapter(
        [_page([_lead("One Ltd", "1")])],
        quota=SourceQuota(requests=1, period="day", timezone="Europe/London", title="1 / day", detail=""),
    )
    other = ScriptedAdapter(
        [_page([_lead("Two Ltd", "2")], done=True)],
        quota=SourceQuota(requests=5, period="day", timezone="Europe/London", title="5 / day", detail=""),
    )
    other.key = "other"
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        _bot(db, source="scripted", updated_at=now)
        second = _bot(db, source="other", updated_at=now + timedelta(seconds=1))
        tick(db, _settings(), _registry(limited, other), now=now)
        assert tick(db, _settings(), _registry(limited, other), now=now + timedelta(minutes=1)) == "stepped"
        db.refresh(second)
        assert second.status == "completed"
        assert other.calls == 1


def test_duplicate_sighting_is_counted_separately_from_new_leads():
    adapter = ScriptedAdapter(
        [
            _page([_lead("One Ltd", "1")]),
            _page([_lead("One Ltd", "1")], done=True),
        ]
    )
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        tick(db, _settings(), _registry(adapter), now=now)
        tick(db, _settings(), _registry(adapter), now=now + timedelta(minutes=1))
        db.refresh(bot)
        assert bot.leads_found == 1
        assert bot.duplicates_found == 1
        assert db.scalar(select(func.count()).select_from(Lead)) == 1


def test_stop_during_request_is_kept_and_the_lead_is_still_stored():
    class Stopper(ScriptedAdapter):
        def fetch(self, ctx):
            self.calls += 1
            with session_scope() as other:
                row = other.scalar(select(Bot))
                row.status = "stopped"
            return _page([_lead()], checkpoint={"page": 4})

    adapter = Stopper([])
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        tick(db, _settings(), _registry(adapter), now=now)
        bot_id = bot.id
    with session_scope() as db:
        bot = db.get(Bot, bot_id)
        assert bot.status == "stopped"
        assert bot.checkpoint == {"page": 4}
        assert db.scalar(select(func.count()).select_from(Lead)) == 1


def test_backoff_then_error_after_repeated_failures():
    adapter = ScriptedAdapter([TransientSourceError("busy", retry_after=30) for _ in range(5)])
    settings = _settings(max_consecutive_errors=3)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        assert tick(db, settings, _registry(adapter), now=now) == "backoff"
        db.refresh(bot)
        assert bot.status == "running"
        assert bot.next_run_at is not None
        assert bot.steps_failed == 1
        assert tick(db, settings, _registry(adapter), now=now + timedelta(seconds=5)) == "idle"
        assert tick(db, settings, _registry(adapter), now=now + timedelta(seconds=31)) == "backoff"
        assert tick(db, settings, _registry(adapter), now=now + timedelta(minutes=5)) == "error"
        db.refresh(bot)
        assert bot.status == "error"


def test_fatal_source_error_stops_the_bot():
    adapter = ScriptedAdapter([FatalSourceError("robots.txt disallows this URL")])
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        assert tick(db, _settings(), _registry(adapter), now=now) == "error"
        db.refresh(bot)
        assert bot.status == "error"
        assert "robots.txt" in bot.last_error


def test_completed_bot_is_left_alone():
    adapter = ScriptedAdapter([_page([], done=True)])
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        bot = _bot(db)
        tick(db, _settings(), _registry(adapter), now=now)
        db.refresh(bot)
        assert bot.status == "completed"
        assert tick(db, _settings(), _registry(adapter), now=now) == "idle"
        assert adapter.calls == 1


def test_has_capacity_treats_the_cap_as_inclusive_until_it_is_reached():
    quota = SourceQuota(requests=2, period="day", timezone="Europe/London", title="", detail="")
    window = UsageWindow(source="scripted", window_key="x", window_start=datetime(2026, 1, 15, tzinfo=timezone.utc), requests_used=1)
    assert has_capacity(window, quota, 1) is True
    window.requests_used = 2
    assert has_capacity(window, quota, 1) is False


def test_ensure_window_is_stable_within_a_day_and_changes_after_midnight():
    quota = DAY
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    with session_scope() as db:
        first = ensure_window(db, "scripted", now, quota)
        second = ensure_window(db, "scripted", now + timedelta(hours=1), quota)
        assert first.id == second.id
        later = ensure_window(db, "scripted", now + timedelta(days=1), quota)
        assert later.id != first.id
