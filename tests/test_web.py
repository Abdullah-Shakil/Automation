from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.main import app
from app.models import Bot
from app.services.dedup import upsert_lead
from app.sources.base import RawLead
from app.sources.google_places import GooglePlacesAdapter
from app.sources.wikidata import parse_wikidata_bindings, sparql_literal


def test_health_and_robots_are_public():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/robots.txt").status_code == 200
        home = client.get("/")
        assert home.status_code == 200
        assert "Sign in" not in home.text
        assert "Log out" not in home.text


def test_bot_start_stop_round_trip():
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "OpenStreetMap" in home.text
        assert "COMPANIES_HOUSE_API_KEY" in home.text
        assert "600 requests / 5 minutes" in home.text
        assert "100 requests / day" in home.text
        assert "billing account" in home.text
        assert "Facebook" in home.text
        assert "Email drafts" not in home.text
        started = client.post(
            "/bots",
            data={
                "location": "Hackney, London",
                "source": "overpass",
                "professions": "plumber",
            },
            follow_redirects=False,
        )
        assert started.status_code == 303
        bot_url = started.headers["location"].split("?", 1)[0]
        detail = client.get(bot_url)
        assert "Running" in detail.text
        assert "What this bot collects" in detail.text
        assert "Leads per hour" in detail.text
        assert "Errors" in detail.text
        bot_id = bot_url.rsplit("/", 1)[-1]
        stopped = client.post(f"/bots/{bot_id}/stop", follow_redirects=True)
        assert "Stopped" in stopped.text

    with session_scope() as db:
        bot = db.get(Bot, int(bot_id))
        assert bot.status == "stopped"
        assert bot.location == "Hackney, London"
        assert bot.professions[0]["slug"] == "plumber"


def test_leads_search_includes_description_and_has_no_export():
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
            None,
            now,
        )
    with TestClient(app) as client:
        leads = client.get("/leads?q=Boiler")
        assert "River Plumbing" in leads.text
        assert "Boiler repairs in Barnes" in leads.text
        assert "Export CSV" not in leads.text
        assert "PECR" not in leads.text
        assert client.get("/leads/export.csv").status_code == 404
        assert client.get("/emails").status_code == 404
        hidden = client.get("/leads?q=nobody")
        assert "River Plumbing" not in hidden.text


def test_add_profession_shows_up_on_the_form():
    with TestClient(app) as client:
        page = client.get("/professions")
        assert page.status_code == 200
        saved = client.post(
            "/professions",
            data={
                "label": "Glaziers",
                "keywords": "glazier, glazing",
                "sic_codes": "43342",
                "osm_tags": "craft=glazier",
            },
            follow_redirects=True,
        )
        assert "Glaziers" in saved.text
        home = client.get("/")
        assert "Glaziers" in home.text


def test_google_places_stays_off_when_a_key_is_set():
    from app.config import get_settings

    adapter = GooglePlacesAdapter()
    settings = get_settings().model_copy(update={"google_places_api_key": "test-key", "google_places_enabled": False})
    available, reason = adapter.is_available(settings)
    assert available is False
    assert "billing" in reason.lower()
    enabled = settings.model_copy(update={"google_places_enabled": True})
    available, reason = adapter.is_available(enabled)
    assert available is True
    assert adapter.quota.requests == 1000
    assert adapter.quota.period == "month"


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
