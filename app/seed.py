from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profession

BUILTIN_PROFESSIONS: list[dict] = [
    {
        "slug": "plumber",
        "label": "Plumbers",
        "keywords": ["plumber", "plumbing"],
        "sic_codes": ["43220"],
        "osm_tags": [{"key": "craft", "value": "plumber"}],
    },
    {
        "slug": "electrician",
        "label": "Electricians",
        "keywords": ["electrician", "electrical"],
        "sic_codes": ["43210"],
        "osm_tags": [{"key": "craft", "value": "electrician"}],
    },
    {
        "slug": "gardener",
        "label": "Gardeners",
        "keywords": ["gardener", "landscaping"],
        "sic_codes": ["81300"],
        "osm_tags": [{"key": "craft", "value": "gardener"}],
    },
    {
        "slug": "solicitor",
        "label": "Solicitors",
        "keywords": ["solicitor"],
        "sic_codes": ["69102"],
        "osm_tags": [{"key": "office", "value": "lawyer"}],
    },
    {
        "slug": "painter",
        "label": "Painters and decorators",
        "keywords": ["painter", "decorator"],
        "sic_codes": ["43341"],
        "osm_tags": [{"key": "craft", "value": "painter"}],
    },
    {
        "slug": "builder",
        "label": "Builders",
        "keywords": ["builder", "building"],
        "sic_codes": ["41202"],
        "osm_tags": [{"key": "craft", "value": "builder"}],
    },
    {
        "slug": "roofer",
        "label": "Roofers",
        "keywords": ["roofer", "roofing"],
        "sic_codes": ["43910"],
        "osm_tags": [{"key": "craft", "value": "roofer"}],
    },
    {
        "slug": "carpenter",
        "label": "Carpenters and joiners",
        "keywords": ["carpenter", "joiner"],
        "sic_codes": ["43320"],
        "osm_tags": [{"key": "craft", "value": "carpenter"}],
    },
    {
        "slug": "plasterer",
        "label": "Plasterers",
        "keywords": ["plasterer"],
        "sic_codes": ["43310"],
        "osm_tags": [{"key": "craft", "value": "plasterer"}],
    },
    {
        "slug": "locksmith",
        "label": "Locksmiths",
        "keywords": ["locksmith"],
        "sic_codes": [],
        "osm_tags": [{"key": "craft", "value": "locksmith"}],
    },
    {
        "slug": "accountant",
        "label": "Accountants",
        "keywords": ["accountant", "accountancy"],
        "sic_codes": ["69201"],
        "osm_tags": [{"key": "office", "value": "accountant"}],
    },
    {
        "slug": "cleaner",
        "label": "Cleaners",
        "keywords": ["cleaner", "cleaning"],
        "sic_codes": ["81210"],
        "osm_tags": [{"key": "craft", "value": "window_cleaner"}],
    },
    {
        "slug": "estate-agent",
        "label": "Estate agents",
        "keywords": ["estate agent"],
        "sic_codes": ["68310"],
        "osm_tags": [{"key": "office", "value": "estate_agent"}],
    },
    {
        "slug": "mechanic",
        "label": "Vehicle mechanics",
        "keywords": ["mechanic", "garage"],
        "sic_codes": ["45200"],
        "osm_tags": [{"key": "shop", "value": "car_repair"}],
    },
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seed(db: Session) -> None:
    existing = {row.slug: row for row in db.scalars(select(Profession)).all()}
    now = _utcnow()
    for item in BUILTIN_PROFESSIONS:
        if item["slug"] in existing:
            continue
        db.add(
            Profession(
                slug=item["slug"],
                label=item["label"],
                keywords=list(item["keywords"]),
                sic_codes=list(item["sic_codes"]),
                osm_tags=list(item["osm_tags"]),
                is_builtin=True,
                created_at=now,
            )
        )


def restore_builtins(db: Session) -> int:
    existing = {row.slug: row for row in db.scalars(select(Profession)).all()}
    now = _utcnow()
    added = 0
    for item in BUILTIN_PROFESSIONS:
        row = existing.get(item["slug"])
        if row is None:
            db.add(
                Profession(
                    slug=item["slug"],
                    label=item["label"],
                    keywords=list(item["keywords"]),
                    sic_codes=list(item["sic_codes"]),
                    osm_tags=list(item["osm_tags"]),
                    is_builtin=True,
                    created_at=now,
                )
            )
            added += 1
        elif row.is_builtin:
            row.label = item["label"]
            row.keywords = list(item["keywords"])
            row.sic_codes = list(item["sic_codes"])
            row.osm_tags = list(item["osm_tags"])
    return added
