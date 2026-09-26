"""Pull emails and UK phones from free text; skip directory / junk hosts."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.normalize import clean_email, clean_website, extract_postcode, split_uk_phones

EMAIL_IN_TEXT = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.I,
)
# UK landline / mobile patterns common in SERP snippets and HTML
PHONE_IN_TEXT = re.compile(
    r"(?:\+44[\s\-]?(?:\(0\)|0)?|0)"
    r"(?:7\d{3}|\d{2,4})"
    r"[\s\-]?\d{3,4}"
    r"[\s\-]?\d{3,4}",
)

_JUNK_HOST_SUFFIXES = (
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "nextdoor.com",
    "nextdoor.co.uk",
    "wikipedia.org",
    "quora.com",
    "reddit.com",
    "gov.uk",
    "nhs.uk",
    "yell.com",
    "thomsonlocal.com",
    "cylex-uk.co.uk",
    "hotfrog.co.uk",
    "freeindex.co.uk",
    "checkatrade.com",
    "mybuilder.com",
    "trustatrader.com",
    "ratedpeople.com",
    "bark.com",
    "houzz.com",
    "gumtree.com",
    "ebay.co.uk",
    "amazon.co.uk",
    "tripadvisor.co.uk",
    "glassdoor.co.uk",
    "indeed.co.uk",
    "reed.co.uk",
    "totaljobs.com",
    "skillstg.co.uk",
    "cityandguilds.com",
    "ucas.com",
    "bbc.co.uk",
    "theguardian.com",
    "telegraph.co.uk",
    "independent.co.uk",
    "youtube.com",
)

# England towns / cities rotated for Places / local search coverage
UK_SEARCH_AREAS = (
    "London",
    "Birmingham",
    "Manchester",
    "Leeds",
    "Bristol",
    "Liverpool",
    "Sheffield",
    "Newcastle",
    "Nottingham",
    "Leicester",
    "Coventry",
    "Bradford",
    "Southampton",
    "Plymouth",
    "Reading",
    "Derby",
    "Portsmouth",
    "Brighton",
    "Norwich",
    "Exeter",
)


def is_junk_host(url_or_host: str | None) -> bool:
    if not url_or_host:
        return True
    host = url_or_host
    if "://" in host:
        host = (urlparse(host).hostname or "").lower()
    else:
        host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in _JUNK_HOST_SUFFIXES)


def contacts_from_text(*parts: str | None) -> dict[str, str | None]:
    """Best-effort email + landline/mobile from snippets or page text."""
    blob = " ".join(piece for piece in parts if piece)
    emails: list[str] = []
    for match in EMAIL_IN_TEXT.findall(blob):
        cleaned = clean_email(match)
        if cleaned and cleaned not in emails:
            emails.append(cleaned)
    phones: list[str] = []
    for match in PHONE_IN_TEXT.findall(blob):
        phones.append(match)
    landline, mobile = split_uk_phones(*phones)
    return {
        "email": emails[0] if emails else None,
        "phone": landline,
        "mobile": mobile,
    }


def town_from_address(address: str | None) -> str | None:
    if not address:
        return None
    text = address.strip()
    postcode = extract_postcode(text)
    if postcode:
        text = re.sub(re.escape(postcode), "", text, flags=re.I)
    parts = [part.strip(" ,") for part in text.split(",") if part.strip(" ,")]
    if not parts:
        return None
    # After the postcode is stripped, the last remaining comma part is usually the town.
    town = parts[-1]
    town = re.sub(r"\s+", " ", town).strip()
    if len(town) < 2 or len(town) > 80:
        return None
    # Skip pure street-looking single tokens when a better part exists earlier.
    if len(parts) >= 2 and re.match(r"^\d", town):
        town = parts[-2]
    return town


def usable_website(url: str | None) -> str | None:
    cleaned = clean_website(url)
    if not cleaned or is_junk_host(cleaned):
        return None
    return cleaned
