from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db import session_scope
from app.models import Lead
from app.services.dedup import upsert_lead
from app.sources.base import RawLead


def _raw(**kwargs):
    data = dict(
        business_name="Acme Plumbing Ltd",
        profession="Plumbers",
        address="1 High Street, London SW13 9LW",
        postcode="SW13 9LW",
        phone="+44 20 7946 0000",
        website="https://www.acme-plumbing.example",
        email="hello@acme-plumbing.example",
        source="overpass",
        source_url="https://www.openstreetmap.org/node/1",
        external_id="node:1",
    )
    data.update(kwargs)
    return RawLead(**data)


def test_same_phone_and_company_name_collapse_across_sources():
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as db:
        assert upsert_lead(db, _raw(), None, now) == "created"
        second = _raw(
            business_name="ACME PLUMBING LIMITED",
            phone="020 7946 0000",
            source="companies_house",
            source_url="https://find-and-update.company-information.service.gov.uk/company/12345678",
            external_id="12345678",
            website=None,
            email=None,
        )
        assert upsert_lead(db, second, None, now) == "merged"
        assert db.scalar(select(func.count()).select_from(Lead)) == 1
        lead = db.scalar(select(Lead))
        assert "overpass" in lead.sources
        assert "companies_house" in lead.sources
        assert lead.email == "hello@acme-plumbing.example"
        assert lead.website == "https://www.acme-plumbing.example"


def test_different_postcodes_stay_separate_when_nothing_else_matches():
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as db:
        upsert_lead(
            db,
            _raw(phone=None, website=None, email=None, external_id="a", postcode="SW13 9LW"),
            None,
            now,
        )
        upsert_lead(
            db,
            _raw(
                phone=None,
                website=None,
                email=None,
                external_id="b",
                source="companies_house",
                postcode="E8 1AA",
                address="2 Mare Street, London E8 1AA",
            ),
            None,
            now,
        )
        assert db.scalar(select(func.count()).select_from(Lead)) == 2
