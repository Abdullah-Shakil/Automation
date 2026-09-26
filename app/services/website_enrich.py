"""Read a company's own website: homepage, contact, and about pages."""

import json
import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from app.normalize import clean_email, clean_website, extract_postcode, split_uk_phones
from app.services.contacts import contacts_from_text

_SOCIAL_HOSTS = (
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "tiktok.com",
    "youtube.com",
    "nextdoor.com",
    "nextdoor.co.uk",
)
_PAGE_HINTS = ("contact", "about", "find-us", "get-in-touch")
_LD_TYPES = {"localbusiness", "organization", "organisation", "store", "professionalservice"}


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_social_url(url: str) -> bool:
    host = host_of(url)
    return any(host == name or host.endswith("." + name) for name in _SOCIAL_HOSTS)


def robots_allows(robots_body: str, url: str, user_agent: str) -> bool:
    parser = RobotFileParser()
    parser.parse(robots_body.splitlines())
    return parser.can_fetch(user_agent, url)


def parse_company_site(html: str, page_url: str) -> dict:
    """Pull tel, mailto, JSON-LD, address, and social links from one HTML page."""
    soup = BeautifulSoup(html or "", "html.parser")
    phones: list[str] = []
    emails: list[str] = []
    social: list[str] = []
    address_bits: dict[str, str | None] = {}
    description = None
    trading_name = None

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if href.lower().startswith("tel:"):
            phones.append(href[4:].split("?")[0].strip())
        elif href.lower().startswith("mailto:"):
            email = clean_email(href)
            if email:
                emails.append(email)
        else:
            absolute = urljoin(page_url, href)
            if absolute.startswith("http") and is_social_url(absolute):
                social.append(absolute.split("?")[0])

    for node in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        extracted = _from_jsonld(node.string or node.get_text() or "")
        phones.extend(extracted["phones"])
        emails.extend(extracted["emails"])
        social.extend(extracted["social"])
        if extracted["description"] and not description:
            description = extracted["description"]
        if extracted["name"] and not trading_name:
            trading_name = extracted["name"]
        for key, value in extracted["address"].items():
            if value and not address_bits.get(key):
                address_bits[key] = value

    landline, mobile = split_uk_phones(*phones)
    email = next((item for item in emails if item), None)
    # Also scan visible text — many trade sites put numbers in paragraphs, not tel: links.
    text_contacts = contacts_from_text(soup.get_text(" ", strip=True))
    if not landline and text_contacts.get("phone"):
        landline = text_contacts["phone"]
    if not mobile and text_contacts.get("mobile"):
        mobile = text_contacts["mobile"]
    if not email and text_contacts.get("email"):
        email = text_contacts["email"]
    postcode = address_bits.get("postcode") or extract_postcode(" ".join(filter(None, address_bits.values())))
    return {
        "landline": landline,
        "mobile": mobile,
        "email": email,
        "social_links": "\n".join(_unique(social)[:12]),
        "description": (description or "")[:4000] or None,
        "trading_name": (trading_name or "")[:300] or None,
        "address_line1": address_bits.get("address_line1"),
        "address_line2": address_bits.get("address_line2"),
        "town": address_bits.get("town"),
        "county": address_bits.get("county"),
        "postcode": postcode,
        "address": _join_address(address_bits, postcode),
    }


def same_site_pages(html: str, page_url: str) -> list[str]:
    """Contact and about links on the same host, plus the usual paths if linked."""
    soup = BeautifulSoup(html or "", "html.parser")
    home = host_of(page_url)
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        absolute = urljoin(page_url, href).split("#")[0]
        if host_of(absolute) != home:
            continue
        path = urlparse(absolute).path.lower()
        if any(hint in path for hint in _PAGE_HINTS):
            found.append(absolute)
    return _unique(found)[:2]


def _from_jsonld(raw: str) -> dict:
    phones: list[str] = []
    emails: list[str] = []
    social: list[str] = []
    description = None
    name = None
    address: dict[str, str | None] = {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "phones": phones,
            "emails": emails,
            "social": social,
            "description": description,
            "name": name,
            "address": address,
        }
    for node in _walk_ld(payload):
        types = node.get("@type") or []
        if isinstance(types, str):
            types = [types]
        type_names = {str(item).lower() for item in types}
        if type_names and not (type_names & _LD_TYPES) and not any(
            item.endswith("business") for item in type_names
        ):
            continue
        telephone = node.get("telephone") or node.get("phone")
        if isinstance(telephone, list):
            phones.extend(str(item) for item in telephone)
        elif telephone:
            phones.append(str(telephone))
        email = clean_email(str(node.get("email") or ""))
        if email:
            emails.append(email)
        same = node.get("sameAs") or []
        if isinstance(same, str):
            same = [same]
        for link in same:
            if isinstance(link, str) and link.startswith("http") and is_social_url(link):
                social.append(link.split("?")[0])
        if not description and node.get("description"):
            description = str(node["description"]).strip()
        if not name and node.get("name"):
            name = str(node["name"]).strip()
        parsed = _ld_address(node.get("address"))
        for key, value in parsed.items():
            if value and not address.get(key):
                address[key] = value
    return {
        "phones": phones,
        "emails": emails,
        "social": social,
        "description": description,
        "name": name,
        "address": address,
    }


def _walk_ld(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _walk_ld(item)
        return
    if not isinstance(payload, dict):
        return
    yield payload
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _walk_ld(item)


def _ld_address(value) -> dict[str, str | None]:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str):
        postcode = extract_postcode(value)
        return {
            "address_line1": value[:200],
            "address_line2": None,
            "town": None,
            "county": None,
            "postcode": postcode,
        }
    if not isinstance(value, dict):
        return {}
    street = str(value.get("streetAddress") or "").strip() or None
    return {
        "address_line1": street[:200] if street else None,
        "address_line2": None,
        "town": str(value.get("addressLocality") or "").strip() or None,
        "county": str(value.get("addressRegion") or "").strip() or None,
        "postcode": str(value.get("postalCode") or "").strip() or extract_postcode(street or ""),
    }


def _join_address(parts: dict, postcode: str | None) -> str | None:
    ordered = [
        parts.get("address_line1"),
        parts.get("address_line2"),
        parts.get("town"),
        parts.get("county"),
        postcode,
    ]
    text = ", ".join(piece for piece in ordered if piece)
    return text or None


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def normalise_start_url(website: str | None) -> str | None:
    cleaned = clean_website(website)
    if not cleaned or is_social_url(cleaned):
        return None
    return cleaned
