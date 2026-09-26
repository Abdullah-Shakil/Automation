import ipaddress
import json
import time
from contextlib import nullcontext
from urllib.parse import quote, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.normalize import clean_email, clean_website, clip, extract_postcode
from app.sources.base import (
    FatalSourceError,
    FetchContext,
    FetchResult,
    Pacer,
    RawLead,
    SourceAdapter,
    SourceQuota,
    TransientSourceError,
    http_headers,
)

BUSINESS_HINTS = (
    "LocalBusiness",
    "Organization",
    "ProfessionalService",
    "HomeAndConstructionBusiness",
    "LegalService",
    "Attorney",
    "AccountingService",
    "Electrician",
    "Plumber",
    "HousePainter",
    "RoofingContractor",
    "GeneralContractor",
    "Locksmith",
    "HVACBusiness",
)
SKIP_TYPES = {"WebSite", "WebPage", "SearchResultsPage", "BreadcrumbList", "ImageObject", "ListItem"}


def parse_directory_html(html: str, page_url: str, profession: str, source_key: str = "directory") -> list[RawLead]:
    soup = BeautifulSoup(html, "html.parser")
    leads = _leads_from_jsonld(soup, page_url, profession, source_key)
    if leads:
        return leads
    return _leads_from_microdata(soup, page_url, profession, source_key)


def _leads_from_jsonld(soup: BeautifulSoup, page_url: str, profession: str, source_key: str) -> list[RawLead]:
    found: list[RawLead] = []
    seen: set[str] = set()
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _iter_nodes(payload):
            if not isinstance(node, dict) or not _is_business(node):
                continue
            lead = _lead_from_mapping(node, page_url, profession, source_key)
            if lead is None:
                continue
            marker = lead.external_id or lead.business_name
            if marker in seen:
                continue
            seen.add(marker)
            found.append(lead)
    return found


def _iter_nodes(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_nodes(item)
        return
    if not isinstance(payload, dict):
        return
    if "@graph" in payload:
        yield from _iter_nodes(payload["@graph"])
    if "itemListElement" in payload:
        yield from _iter_nodes(payload["itemListElement"])
    yield payload


def _types(node: dict) -> list[str]:
    value = node.get("@type")
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _is_business(node: dict) -> bool:
    types = _types(node)
    if not types or set(types) <= SKIP_TYPES:
        return False
    for item in types:
        short = item.split("/")[-1]
        if short in BUSINESS_HINTS or short.endswith("Business"):
            return True
    return False


def _lead_from_mapping(node: dict, page_url: str, profession: str, source_key: str) -> RawLead | None:
    name = node.get("name")
    if isinstance(name, dict):
        name = name.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    address_text, postcode = _read_address(node.get("address"))
    website = clean_website(node.get("url") if isinstance(node.get("url"), str) else None)
    email = clean_email(node.get("email") if isinstance(node.get("email"), str) else None)
    phone = node.get("telephone") if isinstance(node.get("telephone"), str) else None
    description = node.get("description") if isinstance(node.get("description"), str) else None
    external = website or f"{name.strip()}|{postcode or address_text or page_url}"
    return RawLead(
        business_name=name.strip(),
        profession=profession,
        address=address_text or None,
        postcode=postcode,
        phone=(phone or "").strip() or None,
        website=website,
        email=email,
        description=(description or "").strip() or None,
        source=source_key,
        source_url=page_url,
        external_id=external[:200],
    )


def _read_address(address) -> tuple[str, str | None]:
    if isinstance(address, str):
        return address.strip(), extract_postcode(address)
    if not isinstance(address, dict):
        return "", None
    parts = []
    for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode"):
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    text = ", ".join(parts)
    postcode = None
    if isinstance(address.get("postalCode"), str):
        postcode = extract_postcode(address["postalCode"]) or address["postalCode"].strip()
    return text, postcode or extract_postcode(text)


def _leads_from_microdata(soup: BeautifulSoup, page_url: str, profession: str, source_key: str) -> list[RawLead]:
    found: list[RawLead] = []
    for element in soup.select("[itemtype]"):
        itemtype = " ".join(element.get("itemtype", []) if isinstance(element.get("itemtype"), list) else [element.get("itemtype", "")])
        if "schema.org" not in itemtype:
            continue
        if not any(hint in itemtype for hint in BUSINESS_HINTS):
            continue
        name_el = element.select_one("[itemprop=name]")
        if name_el is None:
            continue
        name = name_el.get_text(" ", strip=True)
        if not name:
            continue
        street = _prop_text(element, "streetAddress")
        locality = _prop_text(element, "addressLocality")
        postcode_text = _prop_text(element, "postalCode")
        address = ", ".join(part for part in (street, locality, postcode_text) if part)
        website = clean_website(_prop_text(element, "url"))
        found.append(
            RawLead(
                business_name=name,
                profession=profession,
                address=address or None,
                postcode=extract_postcode(postcode_text or address),
                phone=_prop_text(element, "telephone") or None,
                website=website,
                email=clean_email(_prop_text(element, "email")),
                description=_prop_text(element, "description") or None,
                source=source_key,
                source_url=page_url,
                external_id=(website or f"{name}|{postcode_text or page_url}")[:200],
            )
        )
    return found


def _prop_text(element, name: str) -> str:
    node = element.select_one(f"[itemprop={name}]")
    if node is None:
        return ""
    if node.has_attr("href") and name in {"url", "email"}:
        return str(node.get("href") or "")
    if node.has_attr("content"):
        return str(node.get("content") or "")
    return node.get_text(" ", strip=True)


class DirectoryAdapter(SourceAdapter):
    key = "directory"
    label = "Business directory"
    description = (
        "Reads a public directory search page you configure with DIRECTORY_SEARCH_URL_TEMPLATE. "
        "It checks robots.txt first and stops if that URL is disallowed. "
        "It only picks up schema.org business details (JSON-LD or microdata). "
        "It does not log in, solve challenges, or ignore a crawl delay."
    )
    quota = SourceQuota(
        requests=200,
        period="day",
        timezone="Europe/London",
        title="200 page fetches / day",
        detail=(
            "A directory site does not publish a free API quota. "
            "This cap is politeness: robots.txt is checked first, crawl-delay is honoured, "
            "and directory bots together fetch at most 200 pages per UK day. "
            "The window resets at midnight UK time. There is no charge."
        ),
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()
        self._robots: dict[str, tuple[float, RobotFileParser]] = {}

    def estimated_requests(self, ctx: FetchContext) -> int:
        template = ctx.settings.directory_search_url_template
        if not template:
            return 1
        try:
            url = _build_url(template, "trade", ctx.location or "london", 1)
        except FatalSourceError:
            return 1
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        return 1 if origin in self._robots else 2

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not settings.directory_search_url_template:
            return False, "Set DIRECTORY_SEARCH_URL_TEMPLATE to a public search URL that uses {keyword}, {location}, and {page}."
        if "{keyword}" not in settings.directory_search_url_template or "{location}" not in settings.directory_search_url_template:
            return False, "The directory URL template must include {keyword} and {location}."
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=30.0, follow_redirects=True)

    def fetch(self, ctx: FetchContext) -> FetchResult:
        available, reason = self.is_available(ctx.settings)
        if not available:
            raise FatalSourceError(reason)
        checkpoint = dict(ctx.checkpoint or {})
        profession_index = int(checkpoint.get("profession_index", 0))
        if profession_index >= len(ctx.professions):
            note = "Directory search finished."
            return FetchResult([], checkpoint, True, 0, note, note)
        profession = ctx.professions[profession_index]
        keywords = list(profession.get("keywords") or []) or [profession["label"]]
        keyword_index = int(checkpoint.get("keyword_index", 0))
        if keyword_index >= len(keywords):
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "page": 1}
            done = profession_index + 1 >= len(ctx.professions)
            note = f"Finished directory search for {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)
        page = int(checkpoint.get("page") or 1)
        keyword = str(keywords[keyword_index])
        url = _build_url(ctx.settings.directory_search_url_template, keyword, ctx.location, page)
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        robots_cached = origin in self._robots
        with self._session() as client:
            self._ensure_allowed(ctx, client, url)
            delay = self._crawl_delay(url, ctx.user_agent)
            if delay:
                time.sleep(min(delay, 20))
            self._pacer.wait(ctx.settings.directory_min_interval_seconds)
            response = client.get(url, headers=http_headers(ctx.user_agent))
        requests_made = 1 if robots_cached else 2
        if response.status_code == 429:
            raise TransientSourceError("The directory asked us to slow down.", retry_after=30)
        if response.status_code >= 500:
            raise TransientSourceError(f"Directory returned HTTP {response.status_code}.")
        if response.status_code >= 400:
            raise FatalSourceError(f"Directory returned HTTP {response.status_code} for the search page.")
        leads = parse_directory_html(response.text, url, profession["label"])
        signature = sorted(lead.external_id or lead.business_name for lead in leads)
        if not leads or signature == checkpoint.get("last_signature"):
            nxt = {
                "profession_index": profession_index,
                "keyword_index": keyword_index + 1,
                "page": 1,
            }
            if keyword_index + 1 >= len(keywords):
                nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "page": 1}
            note = f"Directory · {profession['label']} · no further listings for “{keyword}”."
        else:
            nxt = {
                "profession_index": profession_index,
                "keyword_index": keyword_index,
                "page": page + 1,
                "last_signature": signature[:50],
            }
            note = f"Directory · {profession['label']} · “{keyword}” · page {page} · {len(leads)} listings"
        done = int(nxt["profession_index"]) >= len(ctx.professions)
        return FetchResult(leads, nxt, done, requests_made, clip(note, 400) or note, note)

    def _ensure_allowed(self, ctx: FetchContext, client: httpx.Client, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots_for(origin, ctx, client)
        if not parser.can_fetch(ctx.user_agent, url):
            raise FatalSourceError(
                f"robots.txt for {parsed.netloc} disallows {parsed.path or '/'}. "
                "The directory was not fetched."
            )

    def _robots_for(self, origin: str, ctx: FetchContext, client: httpx.Client) -> RobotFileParser:
        cached = self._robots.get(origin)
        now = time.time()
        if cached and now - cached[0] < 3600:
            return cached[1]
        parser = RobotFileParser()
        robots_url = origin + "/robots.txt"
        response = client.get(robots_url, headers=http_headers(ctx.user_agent))
        if response.status_code >= 500:
            raise TransientSourceError(f"Could not read robots.txt (HTTP {response.status_code}).")
        if response.status_code >= 400:
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())
        self._robots[origin] = (now, parser)
        return parser

    def _crawl_delay(self, url: str, user_agent: str) -> float:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        cached = self._robots.get(origin)
        if not cached:
            return 0
        parser = cached[1]
        delay = parser.crawl_delay(user_agent)
        if delay is None:
            delay = parser.crawl_delay("*")
        return float(delay or 0)


def _build_url(template: str, keyword: str, location: str, page: int) -> str:
    try:
        url = template.format(keyword=quote(keyword, safe=""), location=quote(location, safe=""), page=int(page))
    except KeyError as exc:
        raise FatalSourceError(f"Directory URL template has an unknown placeholder {exc}.") from exc
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FatalSourceError("The directory URL must be an http or https address.")
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_link_local or ip.is_reserved or ip.is_multicast):
        raise FatalSourceError("Refusing to fetch a link-local or reserved address.")
    return url
