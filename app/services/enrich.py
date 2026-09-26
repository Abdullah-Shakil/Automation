"""Fill empty company fields from Companies House officers and the company's website.

Runs only from the cloud worker, one company per idle tick, and stops at the free quota.
"""

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Lead
from app.normalize import clip
from app.services.usage import ensure_window, has_capacity, limit_message, window_bounds
from app.services.website_enrich import (
    normalise_start_url,
    parse_company_site,
    robots_allows,
    same_site_pages,
)
from app.sources.base import SourceQuota, http_headers
from app.sources.companies_house import parse_officers
from app.sources.unlockers import ScrapingBeeAdapter

logger = logging.getLogger(__name__)

WEBSITE_QUOTA = SourceQuota(
    requests=80,
    period="day",
    timezone="Europe/London",
    title="80 page fetches / day",
    detail=(
        "The cloud worker reads each company's own homepage, contact page, and about page, "
        "after robots.txt. Eighty fetches per UK day, shared across every company, then it pauses until midnight. "
        "When ScrapingBee is Connected, blocked pages can retry through that unlocker."
    ),
)

SCRAPINGBEE_URL = "https://app.scrapingbee.com/api/v1/"
_BLOCKED_STATUS = {401, 403, 429, 503}


def website_usage(db: Session, now: datetime) -> dict:
    window = ensure_window(db, "website", now, WEBSITE_QUOTA)
    _start, reset_at = window_bounds(now, WEBSITE_QUOTA)
    return {
        "used": window.requests_used,
        "limit": WEBSITE_QUOTA.requests,
        "title": WEBSITE_QUOTA.title,
        "reset_at": reset_at,
        "detail": WEBSITE_QUOTA.detail,
    }


def enrich_one(
    db: Session,
    settings: Settings,
    now: datetime,
    client: httpx.Client | None = None,
) -> bool:
    """Enrich one lead. Returns True when a step was taken (including a recorded skip)."""
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        if settings.companies_house_api_key:
            officer_lead = _next_officer_lead(db, now)
            if officer_lead is not None and _enrich_officers(db, settings, officer_lead, now, client):
                return True
        site_lead = _next_website_lead(db, now)
        if site_lead is None:
            return False
        return _enrich_website(db, settings, site_lead, now, client)
    finally:
        if owns_client and client is not None:
            client.close()


def _next_officer_lead(db: Session, now: datetime) -> Lead | None:
    return db.scalars(
        select(Lead)
        .where(
            Lead.company_number.is_not(None),
            Lead.company_number != "",
            Lead.officers.is_(None),
            or_(Lead.enrichment_after.is_(None), Lead.enrichment_after <= now),
        )
        .order_by(Lead.id.asc())
        .limit(1)
    ).first()


def _next_website_lead(db: Session, now: datetime) -> Lead | None:
    return db.scalars(
        select(Lead)
        .where(
            Lead.website.is_not(None),
            Lead.website != "",
            Lead.enriched_at.is_(None),
            or_(Lead.enrichment_after.is_(None), Lead.enrichment_after <= now),
        )
        .order_by(Lead.id.asc())
        .limit(1)
    ).first()


def _enrich_officers(db: Session, settings: Settings, lead: Lead, now: datetime, client: httpx.Client) -> bool:
    from app.sources.registry import default_registry

    adapter = default_registry.get("companies_house")
    window = ensure_window(db, "companies_house", now, adapter.quota)
    if not has_capacity(window, adapter.quota, 1):
        return False
    number = (lead.company_number or "").strip()
    url = settings.companies_house_api_base.rstrip("/") + f"/company/{number}/officers"
    try:
        response = client.get(url, auth=(settings.companies_house_api_key, ""), headers=http_headers(settings.user_agent))
    except httpx.HTTPError as exc:
        _defer(lead, now, f"Officers request failed: {exc}")
        return True
    window.requests_used += 1
    if response.status_code == 404:
        lead.officers = ""
        lead.enrichment_error = ""
        lead.updated_at = now
        return True
    if response.status_code == 429 or response.status_code >= 500:
        _defer(lead, now, f"Companies House officers returned HTTP {response.status_code}.")
        return True
    if response.status_code >= 400:
        _defer(lead, now, f"Companies House officers returned HTTP {response.status_code}.")
        return True
    try:
        payload = response.json()
    except ValueError:
        _defer(lead, now, "Companies House officers returned a non-JSON response.")
        return True
    lead.officers = parse_officers(payload)
    lead.enrichment_error = ""
    lead.updated_at = now
    return True


def _fetch_via_scrapingbee(
    db: Session,
    settings: Settings,
    client: httpx.Client,
    url: str,
    now: datetime,
) -> tuple[str | None, str | None]:
    adapter = ScrapingBeeAdapter()
    ok, reason = adapter.is_available(settings)
    if not ok:
        return None, reason
    bee = ensure_window(db, adapter.key, now, adapter.quota)
    if not has_capacity(bee, adapter.quota, 1):
        return None, limit_message(adapter.quota)
    try:
        response = client.get(
            SCRAPINGBEE_URL,
            params={
                "api_key": settings.scrapingbee_api_key.strip(),
                "url": url,
                "render_js": "false",
            },
            timeout=40.0,
        )
    except httpx.HTTPError as exc:
        bee.requests_used += 1
        return None, f"ScrapingBee request failed: {exc}"
    bee.requests_used += 1
    if response.status_code >= 400:
        return None, f"ScrapingBee HTTP {response.status_code}."
    return response.text, None


def _fetch_page_html(
    db: Session,
    settings: Settings,
    client: httpx.Client,
    url: str,
    now: datetime,
    *,
    allow_unlocker: bool,
) -> tuple[str | None, str | None]:
    """Return (html, error). Direct fetch first; ScrapingBee if Connected and blocked."""
    try:
        response = client.get(url, headers=http_headers(settings.user_agent))
    except httpx.HTTPError as exc:
        if not allow_unlocker:
            return None, f"Website request failed: {exc}"
        html, err = _fetch_via_scrapingbee(db, settings, client, url, now)
        if html is not None:
            return html, None
        return None, err or f"Website request failed: {exc}"

    if response.status_code < 400:
        return response.text, None

    if allow_unlocker and response.status_code in _BLOCKED_STATUS:
        html, err = _fetch_via_scrapingbee(db, settings, client, url, now)
        if html is not None:
            return html, None
        return None, err or f"Homepage returned HTTP {response.status_code}."

    return None, f"Homepage returned HTTP {response.status_code}."


def _enrich_website(db: Session, settings: Settings, lead: Lead, now: datetime, client: httpx.Client) -> bool:
    start = normalise_start_url(lead.website)
    if start is None:
        lead.enriched_at = now
        lead.enrichment_error = "Website is missing or is a social profile, so it was not fetched."
        lead.updated_at = now
        return True
    window = ensure_window(db, "website", now, WEBSITE_QUOTA)
    if not has_capacity(window, WEBSITE_QUOTA, 1):
        return False
    robots_url = f"{url_origin(start)}/robots.txt"
    try:
        robots = client.get(robots_url, headers=http_headers(settings.user_agent))
    except httpx.HTTPError as exc:
        _defer(lead, now, f"Could not read robots.txt: {exc}")
        window.requests_used += 1
        return True
    window.requests_used += 1
    if robots.status_code >= 500 or robots.status_code in {401, 403}:
        _defer(lead, now, f"robots.txt returned HTTP {robots.status_code}. The site was not fetched.")
        return True
    body = robots.text if robots.status_code == 200 else ""
    if body and not robots_allows(body, start, settings.user_agent):
        lead.enriched_at = now
        lead.enrichment_error = "robots.txt disallows this site for the Leadlane user agent."
        lead.updated_at = now
        return True
    pages = [start]
    merged: dict = {}
    first_html = ""
    for index, page in enumerate(pages):
        if index > 0 and body and not robots_allows(body, page, settings.user_agent):
            continue
        if not has_capacity(window, WEBSITE_QUOTA, 1):
            lead.enrichment_error = limit_message(WEBSITE_QUOTA)[:500]
            lead.updated_at = now
            return True
        html, err = _fetch_page_html(
            db,
            settings,
            client,
            page,
            now,
            allow_unlocker=(index == 0),
        )
        window.requests_used += 1
        if html is None:
            if index == 0:
                _defer(lead, now, err or "Homepage could not be fetched.")
                return True
            continue
        if index == 0:
            first_html = html
        _merge_site(merged, parse_company_site(html, page))
        if index == 0:
            for extra in same_site_pages(first_html, page):
                if extra not in pages:
                    pages.append(extra)
            if len(pages) == 1:
                for suffix in ("/contact", "/about"):
                    candidate = start.rstrip("/") + suffix
                    if candidate not in pages:
                        pages.append(candidate)
            pages = pages[:3]
    _write_site(lead, merged, now)
    return True


def _merge_site(into: dict, found: dict) -> None:
    for key, value in found.items():
        if not value:
            continue
        if key == "social_links":
            current = into.get("social_links") or ""
            lines = [line for line in (current + "\n" + value).splitlines() if line.strip()]
            unique: list[str] = []
            for line in lines:
                if line not in unique:
                    unique.append(line)
            into["social_links"] = "\n".join(unique[:12])
        elif not into.get(key):
            into[key] = value


def _write_site(lead: Lead, found: dict, now: datetime) -> None:
    if found.get("landline") and not lead.phone:
        lead.phone = found["landline"]
    if found.get("mobile") and not lead.mobile:
        lead.mobile = found["mobile"]
    if found.get("email") and not lead.email:
        lead.email = found["email"]
    if found.get("trading_name") and not lead.trading_name and found["trading_name"].lower() != lead.business_name.lower():
        lead.trading_name = clip(found["trading_name"], 300)
    if found.get("description") and not lead.description:
        lead.description = clip(found["description"], 4000)
    if found.get("address") and not lead.address:
        lead.address = clip(found["address"], 2000)
    for field in ("address_line1", "address_line2", "town", "county", "postcode"):
        if found.get(field) and not getattr(lead, field):
            setattr(lead, field, clip(str(found[field]), 200))
    if found.get("social_links"):
        lead.social_links = found["social_links"][:4000]
    if "website" not in (lead.sources or ""):
        lead.sources = (lead.sources + ", website").strip(", ")
    if lead.website and lead.website not in (lead.source_urls or ""):
        extra = lead.website
        lead.source_urls = f"{lead.source_urls} | {extra}" if lead.source_urls else extra
    lead.enriched_at = now
    lead.enrichment_error = ""
    lead.updated_at = now


def _defer(lead: Lead, now: datetime, message: str) -> None:
    lead.enrichment_error = message[:500]
    lead.enrichment_after = now + timedelta(hours=6)
    lead.updated_at = now
    logger.info("Enrichment deferred for lead %s: %s", lead.id, message)


def url_origin(url: str) -> str:
    parsed = httpx.URL(url)
    return f"{parsed.scheme}://{parsed.host}"
