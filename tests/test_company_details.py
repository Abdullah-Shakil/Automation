from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import inspect, select, text

from app.config import get_settings
from app.db import ensure_schema, get_engine, session_scope
from app.models import Lead, UsageWindow
from app.normalize import split_uk_phones
from app.services.dedup import upsert_lead
from app.services.enrich import WEBSITE_QUOTA, enrich_one
from app.services.runner import require_cloud_worker
from app.services.usage import ensure_window
from app.services.website_enrich import parse_company_site, robots_allows
from app.sources.base import RawLead
from app.sources.companies_house import parse_officers


def test_uk_07_is_mobile_and_landline_stays_separate():
    landline, mobile = split_uk_phones("+44 20 7946 0000", "07700 900123")
    assert landline == "02079460000"
    assert mobile == "07700900123"
    assert split_uk_phones("00447700900123") == (None, "07700900123")


def test_company_number_merges_across_sources():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    with session_scope() as db:
        assert (
            upsert_lead(
                db,
                RawLead(
                    business_name="Pipes Ltd",
                    profession="Plumbers",
                    company_number="12345678",
                    phone="02079460001",
                    source="companies_house",
                    external_id="12345678",
                    postcode="SW13 9LW",
                ),
                None,
                now,
            )
            == "created"
        )
        assert (
            upsert_lead(
                db,
                RawLead(
                    business_name="Pipes Trading",
                    profession="Plumbers",
                    company_number="12345678",
                    website="https://pipes.example",
                    phone="07700900999",
                    source="serper",
                    external_id="other",
                    postcode="E1 1AA",
                ),
                None,
                now,
            )
            == "merged"
        )
        lead = db.scalar(select(Lead))
        assert lead.company_number == "12345678"
        assert lead.phone == "02079460001"
        assert lead.mobile == "07700900999"
        assert lead.website == "https://pipes.example"


def test_website_parser_reads_jsonld_tel_and_social():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"LocalBusiness","name":"Pipes Ltd","telephone":"020 7946 0099",
     "email":"office@pipes.example",
     "address":{"@type":"PostalAddress","streetAddress":"1 High Street","addressLocality":"London","addressRegion":"Greater London","postalCode":"SW13 9LW"},
     "sameAs":["https://www.facebook.com/pipes"]}
    </script>
    </head><body>
    <a href="tel:+447700900123">Call</a>
    <a href="mailto:hello@pipes.example">Mail</a>
    <a href="https://www.instagram.com/pipes">Instagram</a>
    <a href="/contact">Contact</a>
    </body></html>
    """
    found = parse_company_site(html, "https://pipes.example/")
    assert found["landline"] == "02079460099"
    assert found["mobile"] == "07700900123"
    assert found["email"] in {"office@pipes.example", "hello@pipes.example"}
    assert "facebook.com/pipes" in found["social_links"]
    assert "instagram.com/pipes" in found["social_links"]
    assert found["town"] == "London"
    assert found["postcode"] == "SW13 9LW"
    robots = "User-agent: *\nDisallow: /private\n"
    assert robots_allows(robots, "https://pipes.example/", "LeadlaneTest/1.0") is True
    assert robots_allows("User-agent: *\nDisallow: /\n", "https://pipes.example/", "LeadlaneTest/1.0") is False


def test_officers_parser_skips_addresses():
    text = parse_officers(
        {
            "items": [
                {
                    "name": "DOE, Jane",
                    "officer_role": "director",
                    "appointed_on": "2020-01-02",
                    "address": {"address_line_1": "Secret House", "postal_code": "SW1A 1AA"},
                }
            ]
        }
    )
    assert "DOE, Jane" in text
    assert "director" in text
    assert "2020-01-02" in text
    assert "Secret" not in text
    assert "SW1A" not in text


def test_ensure_schema_adds_company_columns():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS lead_sightings"))
        conn.execute(text("DROP TABLE IF EXISTS lead_identities"))
        conn.execute(text("DROP TABLE IF EXISTS leads"))
        conn.execute(text("CREATE TABLE leads (id INTEGER PRIMARY KEY)"))
    ensure_schema(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("leads")}
    assert {"company_number", "mobile", "officers", "sic_codes", "enriched_at", "town"} <= columns


def test_worker_refuses_to_collect_without_the_cloud_flag(monkeypatch):
    monkeypatch.delenv("LEADLANE_CLOUD_WORKER", raising=False)
    with pytest.raises(SystemExit):
        require_cloud_worker()


def test_website_enrichment_respects_the_daily_cap(monkeypatch):
    monkeypatch.setenv("LEADLANE_CLOUD_WORKER", "1")
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    settings = get_settings()

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("quota pause must not fetch")

    with session_scope() as db:
        upsert_lead(
            db,
            RawLead(
                business_name="Quiet Pipes",
                profession="Plumbers",
                website="https://quiet.example",
                source="overpass",
                external_id="n1",
            ),
            None,
            now,
        )
        window = ensure_window(db, "website", now, WEBSITE_QUOTA)
        window.requests_used = WEBSITE_QUOTA.requests
        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert enrich_one(db, settings, now, client=client) is False
        lead = db.scalar(select(Lead))
        assert lead.enriched_at is None


def test_website_enrichment_fills_contact_from_the_homepage():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    settings = get_settings()
    html = """
    <html><body>
    <a href="tel:02079460111">Landline</a>
    <a href="mailto:desk@quiet.example">Email</a>
    <a href="https://www.facebook.com/quietpipes">Facebook</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(200, text=html)

    with session_scope() as db:
        upsert_lead(
            db,
            RawLead(
                business_name="Quiet Pipes",
                profession="Plumbers",
                website="https://quiet.example",
                source="overpass",
                external_id="n2",
            ),
            None,
            now,
        )
        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert enrich_one(db, settings, now, client=client) is True
        lead = db.scalar(select(Lead))
        assert lead.phone == "02079460111"
        assert lead.email == "desk@quiet.example"
        assert "facebook.com/quietpipes" in (lead.social_links or "")
        assert lead.enriched_at is not None
        used = db.scalar(select(UsageWindow.requests_used).where(UsageWindow.source == "website"))
        assert used >= 2
