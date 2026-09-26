"""Upsert leads into the single leads table (identity_keys + sightings as JSON)."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead
from app.normalize import (
    clean_email,
    clean_website,
    clip,
    extract_postcode,
    norm_domain,
    norm_name,
    norm_postcode,
    split_uk_phones,
)
from app.sources.base import RawLead


def identity_keys(raw: RawLead) -> list[str]:
    keys: list[str] = []
    if raw.source and raw.external_id:
        keys.append(f"ext:{raw.source}:{raw.external_id.strip().lower()}")
    number = (raw.company_number or "").strip().lower()
    if not number and raw.source == "companies_house":
        number = (raw.external_id or "").strip().lower()
    if number:
        keys.append(f"company:{number}")
    landline, mobile = split_uk_phones(raw.phone, raw.mobile)
    for phone in (landline, mobile):
        if phone:
            keys.append(f"phone:{phone}")
    domain = norm_domain(raw.website)
    if domain:
        keys.append(f"domain:{domain}")
    postcode = norm_postcode(raw.postcode or raw.address or "")
    name = norm_name(raw.business_name)
    if name and postcode:
        keys.append(f"namepc:{name}|{postcode}")
    return keys


def upsert_lead(db: Session, raw: RawLead, bot_key: str | None, now: datetime) -> str:
    name = clip(raw.business_name, 300)
    if not name:
        return "skipped"
    keys = identity_keys(raw)
    landline, mobile = split_uk_phones(raw.phone, raw.mobile)
    existing = _find(db, keys)
    sighting = {
        "source": raw.source,
        "source_url": clip(raw.source_url, 500),
        "external_id": clip(raw.external_id, 200),
        "found_at": now.isoformat(),
    }
    if existing is None:
        lead = Lead(
            business_name=name,
            profession=clip(raw.profession, 120) or "",
            address=clip(raw.address, 2000),
            postcode=extract_postcode(raw.postcode or raw.address or ""),
            phone=landline,
            website=clean_website(raw.website),
            email=clean_email(raw.email),
            description=_description(raw.description),
            primary_source=raw.source,
            primary_source_url=clip(raw.source_url, 500),
            sources=raw.source,
            source_urls=clip(raw.source_url, 2000),
            identity_keys=list(keys),
            sightings=[sighting],
            date_found=now,
            updated_at=now,
            first_bot_key=bot_key,
        )
        _apply_details(lead, raw, landline, mobile)
        db.add(lead)
        db.flush()
        return "created"

    _fill(existing, raw, now)
    _merge_keys(existing, keys)
    _merge_sighting(existing, sighting)
    return "merged"


def _find(db: Session, keys: list[str]) -> Lead | None:
    if not keys:
        return None
    # JSON contains any of the keys — scan recent candidates then fall back to full scan of keys
    key_set = set(keys)
    for lead in db.scalars(select(Lead).order_by(Lead.id.desc()).limit(5000)).all():
        owned = set(lead.identity_keys or [])
        if owned & key_set:
            return lead
    return None


def _merge_keys(lead: Lead, keys: list[str]) -> None:
    owned = list(lead.identity_keys or [])
    seen = set(owned)
    for key in keys:
        value = key[:400]
        if value not in seen:
            owned.append(value)
            seen.add(value)
    lead.identity_keys = owned[:40]


def _merge_sighting(lead: Lead, sighting: dict) -> None:
    rows = list(lead.sightings or [])
    for row in rows:
        if (
            row.get("source") == sighting.get("source")
            and row.get("external_id") == sighting.get("external_id")
            and row.get("source_url") == sighting.get("source_url")
        ):
            return
    rows.append(sighting)
    lead.sightings = rows[-30:]


def _apply_details(lead: Lead, raw: RawLead, landline: str | None, mobile: str | None) -> None:
    _set_missing(lead, "trading_name", clip(raw.trading_name, 300))
    _set_missing(lead, "company_type", clip(raw.company_type, 120))
    _set_missing(lead, "company_number", clip((raw.company_number or "").upper(), 32))
    _set_missing(lead, "company_status", clip(raw.company_status, 40))
    _set_missing(lead, "category", clip(raw.category or raw.profession, 120))
    _set_missing(lead, "sic_codes", clip(raw.sic_codes, 240))
    _set_missing(lead, "incorporated_on", clip(raw.incorporated_on, 20))
    _set_missing(lead, "mobile", mobile)
    _set_missing(lead, "address_line1", clip(raw.address_line1, 200))
    _set_missing(lead, "address_line2", clip(raw.address_line2, 200))
    _set_missing(lead, "town", clip(raw.town, 120))
    _set_missing(lead, "county", clip(raw.county, 120))
    _set_missing(lead, "officers", clip(raw.officers, 4000))
    if raw.social_links:
        lead.social_links = _append_lines(lead.social_links or "", raw.social_links)
    if landline and not lead.phone:
        lead.phone = landline
    if mobile and not lead.mobile:
        lead.mobile = mobile


def _set_missing(lead: Lead, field: str, value: str | None) -> None:
    if value and not getattr(lead, field):
        setattr(lead, field, value)


def _fill(lead: Lead, raw: RawLead, now: datetime) -> None:
    landline, mobile = split_uk_phones(raw.phone, raw.mobile)
    if not lead.address and raw.address:
        lead.address = clip(raw.address, 2000)
    if not lead.postcode:
        lead.postcode = extract_postcode(raw.postcode or raw.address or "")
    if not lead.phone and landline:
        lead.phone = landline
    if not lead.mobile and mobile:
        lead.mobile = mobile
    if not lead.website and raw.website:
        lead.website = clean_website(raw.website)
    if not lead.email and raw.email:
        lead.email = clean_email(raw.email)
    if not lead.description and raw.description:
        lead.description = _description(raw.description)
    if not lead.primary_source_url and raw.source_url:
        lead.primary_source_url = clip(raw.source_url, 500)
    _apply_details(lead, raw, landline, mobile)
    lead.sources = _append_token(lead.sources, raw.source, ",")
    if raw.source_url:
        lead.source_urls = _append_token(lead.source_urls or "", raw.source_url, "|")
    lead.updated_at = now


def _append_lines(existing: str, extra: str) -> str:
    parts = [line.strip() for line in (existing + "\n" + extra).splitlines() if line.strip()]
    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return "\n".join(unique[:20])[:4000]


def _description(value: str | None) -> str | None:
    text = clip(value, 4000)
    return text or None


def _append_token(existing: str, token: str, sep: str) -> str:
    if not token:
        return existing
    divider = ", " if sep == "," else " | "
    parts = [part.strip() for part in existing.split(divider if sep != "," else ",") if part.strip()]
    if token not in parts:
        parts.append(token)
    if sep == "|":
        parts = parts[:8]
    return divider.join(parts)
