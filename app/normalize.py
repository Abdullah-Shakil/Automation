import re
from urllib.parse import urlparse

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TAG_RE = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")
SIC_RE = re.compile(r"^\d{4,5}$")

_NAME_DROP = {"ltd", "limited", "plc", "llp", "cic", "uk", "co", "company", "the", "and"}
_SOCIAL_HOSTS = {
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "tiktok.com",
    "nextdoor.com",
    "nextdoor.co.uk",
}


def extract_postcode(text: str | None) -> str | None:
    if not text:
        return None
    match = POSTCODE_RE.search(text.upper())
    if not match:
        return None
    compact = re.sub(r"\s+", "", match.group(1).upper())
    return compact[:-3] + " " + compact[-3:]


def norm_postcode(text: str | None) -> str | None:
    pretty = extract_postcode(text)
    if not pretty:
        return None
    return pretty.replace(" ", "")


def norm_name(name: str | None) -> str:
    if not name:
        return ""
    text = name.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    parts = [part for part in text.split() if part not in _NAME_DROP]
    return "".join(parts)


def norm_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0044"):
        digits = "0" + digits[4:]
    elif digits.startswith("44"):
        digits = "0" + digits[2:]
    if len(digits) < 10 or len(digits) > 15:
        return None
    return digits


def clean_email(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().removeprefix("mailto:").strip()
    if not EMAIL_RE.match(text):
        return None
    return text.lower()


def clean_website(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.lower().startswith("mailto:"):
        return None
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    parsed = urlparse(text)
    if not parsed.hostname:
        return None
    return text


def norm_domain(value: str | None) -> str | None:
    website = clean_website(value)
    if not website:
        return None
    host = (urlparse(website).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in _SOCIAL_HOSTS:
        return None
    return host or None


def clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def format_address(address: dict | None) -> str:
    if not address:
        return ""
    ordered = []
    premises = (address.get("premises") or "").strip()
    line1 = (address.get("address_line_1") or "").strip()
    if premises and line1:
        ordered.append(f"{premises} {line1}".strip())
    elif premises:
        ordered.append(premises)
    elif line1:
        ordered.append(line1)
    for key in ("address_line_2", "locality", "region", "postal_code", "country"):
        piece = (address.get(key) or "").strip()
        if piece and piece not in ordered:
            ordered.append(piece)
    return ", ".join(ordered)


def slugify(label: str) -> str:
    text = label.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or "profession")[:60]
