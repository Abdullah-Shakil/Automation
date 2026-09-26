from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.main import app
from app.models import Bot, Worker
from app.services.dedup import upsert_lead
from app.sources.base import RawLead
from app.sources.wikidata import parse_wikidata_bindings, sparql_literal
from app.trades import snapshot_for_preset


def test_health_and_robots_are_public():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/robots.txt").status_code == 200
        home = client.get("/")
        assert home.status_code == 200
        assert ">Sign in<" not in home.text
        assert "Log out" not in home.text
        assert "Google Gemini" in home.text
        assert 'href="/?tab=workers"' in home.text
        assert 'href="/?tab=bots"' in home.text
        assert 'href="/leads"' in home.text


def test_worker_start_stop_and_bots_have_no_start(monkeypatch):
    from app.workers.base import FreeWorker
    from app.workers import checks as worker_checks

    monkeypatch.setattr(FreeWorker, "is_available", lambda self, settings: (True, ""))

    def fake_verify(worker, settings):
        key = worker.key_value(settings)
        if not key:
            return worker_checks._store(worker.key, False, f"Add {worker.env_name} to .env.")
        return worker_checks._store(worker.key, True, "")

    monkeypatch.setattr(worker_checks, "verify_worker", fake_verify)
    worker_checks.clear_check_cache()

    with TestClient(app) as client:
        home = client.get("/?tab=workers")
        assert home.status_code == 200
        assert "Workers" in home.text
        assert "Google Gemini" in home.text
        assert "Serper" in home.text
        assert "Tavily" in home.text
        assert "SerpApi" in home.text
        assert "Groq" in home.text
        assert "aistudio.google.com" in home.text
        assert "GEMINI_API_KEY" in home.text
        assert "Find" in home.text
        assert "All" in home.text
        assert "Electricians" in home.text
        assert "Email drafts" not in home.text
        assert "Add bot" not in home.text
        assert "/workers/" in home.text
        assert "Check connections" not in home.text
        assert "/workers/check" not in home.text
        assert "key works" not in home.text.lower()
        assert "Resets in" in home.text
        assert "Usage / remaining" in home.text
        assert "Resets " not in home.text.replace("Resets in", "")
        assert "Connected" in home.text
        assert 'td class="row-actions"' not in home.text
        assert 'class="col-actions"' in home.text
        assert 'class="row-actions"' in home.text
        assert home.text.count('class="col-actions"') >= 2
        assert 'styles.css?v=schema13' in home.text
        assert "Start / stop" in home.text
        assert 'data-sort-card' not in home.text
        assert "Leads found" in home.text
        assert "/profiles/" in home.text

        bots_tab = client.get("/?tab=bots")
        assert bots_tab.status_code == 200
        assert "OpenStreetMap" in bots_tab.text
        assert "COMPANIES_HOUSE_API_KEY" in bots_tab.text
        assert "600 requests / 5 minutes" in bots_tab.text
        assert "100 searches / day" in bots_tab.text or "100 requests / day" in bots_tab.text
        assert "Google Places" not in bots_tab.text
        assert "Business directory" not in bots_tab.text
        assert "Facebook" in bots_tab.text
        assert "Add bot" not in bots_tab.text
        assert "sortable-table" in bots_tab.text
        assert 'action="/bots/' not in bots_tab.text or "/start" not in bots_tab.text

        with session_scope() as db:
            bot = db.scalars(select(Bot).where(Bot.key == "overpass")).first()
            assert bot is not None
            assert bot.status == "idle"
            worker = db.scalars(select(Worker).where(Worker.key == "serper")).first()
            assert worker is not None
            assert worker.status == "stopped"

        started = client.post("/workers/serper/start", follow_redirects=False)
        assert started.status_code == 303
        assert "notice=" in started.headers.get("location", "")
        with session_scope() as db:
            assert db.scalars(select(Worker).where(Worker.key == "serper")).first().status == "running"
        stopped = client.post("/workers/serper/stop", follow_redirects=False)
        assert stopped.status_code == 303
        with session_scope() as db:
            assert db.scalars(select(Worker).where(Worker.key == "serper")).first().status == "stopped"

def test_trade_preset_is_stored():
    with TestClient(app) as client:
        saved = client.post("/trade-preset", data={"trade_preset": "plumber"}, follow_redirects=True)
        assert saved.status_code == 200
        assert "Plumbers" in saved.text
        from app.cloud.runners import get_trade_preset

        with session_scope() as db:
            assert get_trade_preset(db) == "plumber"
        client.post("/trade-preset", data={"trade_preset": "all"}, follow_redirects=True)
        with session_scope() as db:
            assert get_trade_preset(db) == ""


def test_leads_table_has_no_filter_form():
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as db:
        upsert_lead(
            db,
            RawLead(
                business_name="River Plumbing",
                profession="Plumbers",
                description="Boiler repairs in Barnes",
                address="1 High Street, London SW13 9LW",
                postcode="SW13 9LW",
                phone="02079460000",
                email="hello@river.example",
                website="https://river.example",
                source="overpass",
                source_url="https://www.openstreetmap.org/node/9",
                external_id="node:9",
            ),
            "overpass",
            now,
        )
    with TestClient(app) as client:
        leads = client.get("/leads")
        assert "River Plumbing" in leads.text
        assert "Boiler repairs in Barnes" in leads.text
        assert 'name="q"' not in leads.text
        assert "Has email" not in leads.text
        assert "sortable-table" in leads.text
        detail = client.get("/leads/1")
        assert detail.status_code == 200
        assert "Landline" in detail.text
        assert "Export CSV" not in leads.text
        assert client.get("/leads/export.csv").status_code == 404
        assert client.get("/emails").status_code == 404


def test_professions_page_redirects():
    with TestClient(app) as client:
        page = client.get("/professions", follow_redirects=False)
        assert page.status_code == 303
        assert snapshot_for_preset("electrician")[0]["label"] == "Electricians"


def test_wikidata_parser_and_literal_escaping():
    assert '"' not in sparql_literal('say "hi"; drop')
    leads = parse_wikidata_bindings(
        [
            {
                "item": {"value": "http://www.wikidata.org/entity/Q42"},
                "itemLabel": {"value": "Example Plumbers"},
                "desc": {"value": "plumbing company in London"},
                "phone": {"value": "+44 20 7946 0000"},
                "website": {"value": "https://example-plumbers.test"},
                "address": {"value": "1 High Street, London SW13 9LW"},
            }
        ],
        "Plumbers",
    )
    assert leads[0].business_name == "Example Plumbers"
    assert leads[0].description == "plumbing company in London"
    assert leads[0].external_id == "Q42"
    assert leads[0].postcode == "SW13 9LW"


def test_dashboard_survives_db_errors(monkeypatch):
    from app import routes

    def boom(*_a, **_k):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(routes, "session_scope", boom)
    with TestClient(app) as client:
        page = client.get("/?tab=workers")
        assert page.status_code == 200
        assert "Internal error" in page.text
        assert "simulated db failure" in page.text
        assert 'href="/?tab=workers"' in page.text
        assert 'href="/?tab=bots"' in page.text
        assert 'href="/leads"' in page.text
        leads = client.get("/leads")
        assert leads.status_code == 200
        assert "Internal error" in leads.text
        assert 'href="/?tab=workers"' in leads.text
