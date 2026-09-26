from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_scope
from app.main import app
from app.models import Bot
from app.services.dedup import upsert_lead
from app.sources.base import RawLead
from app.sources.wikidata import parse_wikidata_bindings, sparql_literal


def test_health_and_robots_are_public():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        assert client.get("/robots.txt").status_code == 200
        home = client.get("/")
        assert home.status_code == 200
        assert ">Sign in<" not in home.text
        assert "Log out" not in home.text
        assert "Google Gemini" in home.text


def test_bot_start_stop_round_trip():
    with TestClient(app) as client:
        home = client.get("/?tab=workers")
        assert home.status_code == 200
        assert "Workers" in home.text
        assert "Google Gemini" in home.text
        assert "Serper" in home.text
        assert "Tavily" in home.text
        assert "SerpApi" in home.text
        assert "Groq" in home.text
        assert "Check connections" in home.text
        assert "aistudio.google.com" in home.text
        assert "GEMINI_API_KEY" in home.text
        assert "Email drafts" not in home.text
        assert "Add bot" not in home.text
        bots_tab = client.get("/?tab=bots")
        assert bots_tab.status_code == 200
        assert "OpenStreetMap" in bots_tab.text
        assert "COMPANIES_HOUSE_API_KEY" in bots_tab.text
        assert "600 requests / 5 minutes" in bots_tab.text
        assert "100 requests / day" in bots_tab.text
        assert "Google Places" not in bots_tab.text
        assert "Business directory" not in bots_tab.text
        assert "Facebook" in bots_tab.text
        assert "Add bot" not in bots_tab.text
        with session_scope() as db:
            bot = db.scalars(select(Bot).where(Bot.source == "overpass")).first()
            assert bot is not None
            bot_id = bot.id
        started = client.post(f"/bots/{bot_id}/start?tab=bots", follow_redirects=True)
        assert "Started" in started.text or "started" in started.text.lower() or "Running" in started.text
        with session_scope() as db:
            assert db.get(Bot, bot_id).status == "running"
        stopped = client.post(f"/bots/{bot_id}/stop?tab=bots", follow_redirects=True)
        assert "Stopped" in stopped.text

    with session_scope() as db:
        bot = db.get(Bot, int(bot_id))
        assert bot.status == "stopped"
        assert bot.location == "England"
        assert bot.source == "overpass"
        assert any(p["slug"] == "plumber" for p in bot.professions)


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
        detail = client.get("/leads/1")
        assert detail.status_code == 200
        assert "Landline" in detail.text
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
        professions = client.get("/professions")
        assert "Glaziers" in professions.text
        client.get("/?tab=bots")
        with session_scope() as db:
            bot = db.scalars(select(Bot).where(Bot.source == "overpass")).first()
            assert bot is not None
            assert any(p.get("label") == "Glaziers" for p in bot.professions)


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
