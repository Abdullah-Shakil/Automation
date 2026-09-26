from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, LeadIdentity, LeadSighting
from app.normalize import clean_email, clean_website, clip, extract_postcode, norm_domain, norm_name, norm_phone, norm_postcode
from app.sources.base import RawLead


def identity_keys(raw: RawLead) -> list[str]:
    keys: list[str] = []
    if raw.source and raw.external_id:
        keys.append(f"ext:{raw.source}:{raw.external_id.strip().lower()}")
    phone = norm_phone(raw.phone)
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


def upsert_lead(db: Session, raw: RawLead, bot_id: int | None, now: datetime) -> str:
    name = clip(raw.business_name, 300)
    if not name:
        return "skipped"
    keys = identity_keys(raw)
    existing = _find(db, keys)
    if existing is None:
        lead = Lead(
            business_name=name,
            profession=clip(raw.profession, 120) or "",
            address=clip(raw.address, 2000),
            postcode=extract_postcode(raw.postcode or raw.address or ""),
            phone=clip(raw.phone, 80),
            website=clean_website(raw.website),
            email=clean_email(raw.email),
            description=_description(raw.description),
            primary_source=raw.source,
            primary_source_url=clip(raw.source_url, 500),
            sources=raw.source,
            source_urls=clip(raw.source_url, 2000),
            date_found=now,
            updated_at=now,
            first_bot_id=bot_id,
        )
        db.add(lead)
        db.flush()
        for key in keys:
            db.add(LeadIdentity(lead_id=lead.id, value=key[:400]))
        _add_sighting(db, lead.id, raw, now)
        return "created"

    _fill(existing, raw, now)
    _add_missing_keys(db, existing.id, keys)
    _add_sighting(db, existing.id, raw, now)
    return "merged"


def _find(db: Session, keys: list[str]) -> Lead | None:
    if not keys:
        return None
    identity = db.scalar(select(LeadIdentity).where(LeadIdentity.value.in_(keys)))
    if identity is None:
        return None
    return db.get(Lead, identity.lead_id)


def _fill(lead: Lead, raw: RawLead, now: datetime) -> None:
    if not lead.address and raw.address:
        lead.address = clip(raw.address, 2000)
    if not lead.postcode:
        lead.postcode = extract_postcode(raw.postcode or raw.address or "")
    if not lead.phone and raw.phone:
        lead.phone = clip(raw.phone, 80)
    if not lead.website and raw.website:
        lead.website = clean_website(raw.website)
    if not lead.email and raw.email:
        lead.email = clean_email(raw.email)
    if not lead.description and raw.description:
        lead.description = _description(raw.description)
    if not lead.primary_source_url and raw.source_url:
        lead.primary_source_url = clip(raw.source_url, 500)
    lead.sources = _append_token(lead.sources, raw.source, ",")
    if raw.source_url:
        lead.source_urls = _append_token(lead.source_urls or "", raw.source_url, "|")
    lead.updated_at = now


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


def _add_missing_keys(db: Session, lead_id: int, keys: list[str]) -> None:
    owned = set(db.scalars(select(LeadIdentity.value).where(LeadIdentity.lead_id == lead_id)).all())
    for key in keys:
        value = key[:400]
        if value in owned:
            continue
        taken = db.scalar(select(LeadIdentity.id).where(LeadIdentity.value == value))
        if taken is None:
            db.add(LeadIdentity(lead_id=lead_id, value=value))
            owned.add(value)


def _add_sighting(db: Session, lead_id: int, raw: RawLead, now: datetime) -> None:
    existing = db.scalar(
        select(LeadSighting.id).where(
            LeadSighting.lead_id == lead_id,
            LeadSighting.source == raw.source,
            LeadSighting.external_id == raw.external_id,
            LeadSighting.source_url == (clip(raw.source_url, 500)),
        )
    )
    if existing is not None:
        return
    db.add(
        LeadSighting(
            lead_id=lead_id,
            source=raw.source,
            source_url=clip(raw.source_url, 500),
            external_id=clip(raw.external_id, 200),
            found_at=now,
        )
    )
