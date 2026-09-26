"""SerpApi Google Maps / local worker — phone, address, website when available."""

from contextlib import nullcontext
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.normalize import clip, extract_postcode
from app.services.contacts import (
    UK_SEARCH_AREAS,
    contacts_from_text,
    is_junk_host,
    town_from_address,
    usable_website,
)
from app.sources.base import (
    FatalSourceError,
    FetchContext,
    FetchResult,
    Pacer,
    RawLead,
    SourceAdapter,
    SourceQuota,
    TransientSourceError,
    is_nationwide,
)

SEARCH_URL = "https://serpapi.com/search.json"

_GENERIC_TITLES = frozenset({"united kingdom", "uk", "england", "great britain", "home", "search", "results"})


def _name_from_title(title: str) -> str | None:
    name = (title.split(" - ")[0].split(" | ")[0] or "").strip()
    if not name or name.casefold() in _GENERIC_TITLES:
        return None
    return name


def parse_serpapi_local(payload: dict, profession: str) -> list[RawLead]:
    rows = payload.get("local_results") or payload.get("places_results") or []
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in rows:
        title = (row.get("title") or "").strip()
        name = _name_from_title(title) or title.strip()
        if not name:
            continue
        address = (row.get("address") or "").strip() or None
        website = usable_website(row.get("website") or row.get("link"))
        phone_raw = (row.get("phone") or "").strip() or None
        contacts = contacts_from_text(phone_raw, row.get("description"), address, row.get("snippet"))
        place_id = str(row.get("place_id") or row.get("data_id") or "").strip()
        host = (urlparse(website).hostname or "").lower() if website else ""
        identity = place_id or host or f"{name.casefold()}|{extract_postcode(address) or ''}"
        if identity in seen:
            continue
        seen.add(identity)
        category = None
        types = row.get("type") or row.get("types")
        if isinstance(types, list) and types:
            category = str(types[0])
        elif isinstance(types, str):
            category = types
        source_url = website or row.get("gps_coordinates") and None
        if not source_url and place_id:
            source_url = f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}"
        leads.append(
            RawLead(
                business_name=clip(name, 300) or name[:300],
                profession=profession,
                category=clip(category or profession, 120),
                address=clip(address, 2000),
                postcode=extract_postcode(address),
                town=town_from_address(address),
                phone=contacts["phone"] or phone_raw,
                mobile=contacts["mobile"],
                email=contacts["email"],
                website=website,
                description=clip(row.get("description") or row.get("snippet") or address, 500),
                source="serpapi",
                source_url=source_url,
                external_id=identity[:200],
            )
        )
    return leads


def parse_serpapi_organic(payload: dict, profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in payload.get("organic_results") or []:
        title = (row.get("title") or "").strip()
        link = (row.get("link") or "").strip()
        snippet = (row.get("snippet") or "").strip()
        name = _name_from_title(title)
        if not name or not link or is_junk_host(link):
            continue
        website = usable_website(link)
        if not website:
            continue
        host = (urlparse(website).hostname or "").lower()
        if host in seen:
            continue
        seen.add(host)
        contacts = contacts_from_text(snippet, title)
        address = snippet if extract_postcode(snippet) else None
        leads.append(
            RawLead(
                business_name=clip(name, 300) or name[:300],
                profession=profession,
                category=clip(profession, 120),
                address=address,
                postcode=extract_postcode(snippet),
                town=town_from_address(address),
                phone=contacts["phone"],
                mobile=contacts["mobile"],
                email=contacts["email"],
                description=clip(snippet, 500) or None,
                website=website,
                source="serpapi",
                source_url=website,
                external_id=host or link[:200],
            )
        )
    return leads


class SerpApiAdapter(SourceAdapter):
    key = "serpapi"
    label = "SerpApi"
    description = (
        "Google local / maps results via SerpApi (phone, address, website when listed). "
        "Falls back to filtered organic results. Free monthly searches after signup."
    )
    quota = SourceQuota(
        requests=20,
        period="day",
        timezone="Europe/London",
        title="20 searches / day",
        detail="Free plan is about 250 searches a month. Cap spreads that across the month.",
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not settings.serpapi_api_key:
            return (
                False,
                "Add SERPAPI_API_KEY to .env. Free plan: https://serpapi.com/ (sign up, copy the key).",
            )
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=30.0)

    def fetch(self, ctx: FetchContext) -> FetchResult:
        available, reason = self.is_available(ctx.settings)
        if not available:
            raise FatalSourceError(reason)
        checkpoint = dict(ctx.checkpoint or {})
        profession_index = int(checkpoint.get("profession_index", 0))
        if profession_index >= len(ctx.professions):
            note = "SerpApi search finished."
            return FetchResult([], checkpoint, True, 0, note, note)
        profession = ctx.professions[profession_index]
        keywords = list(profession.get("keywords") or []) or [profession["label"]]
        keyword_index = int(checkpoint.get("keyword_index", 0))
        if keyword_index >= len(keywords):
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "area_index": 0, "start": 0}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished SerpApi search for {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)
        keyword = keywords[keyword_index]
        area_index = int(checkpoint.get("area_index", 0))
        start = int(checkpoint.get("start", 0))
        if is_nationwide(ctx.location):
            area = UK_SEARCH_AREAS[area_index % len(UK_SEARCH_AREAS)]
            query = f"{keyword} in {area}"
        else:
            area = ctx.location
            query = f"{keyword} in {area}, UK"
        self._pacer.wait(ctx.settings.serpapi_min_interval_seconds)
        with self._session() as client:
            response = client.get(
                SEARCH_URL,
                params={
                    "engine": "google_maps",
                    "q": query,
                    "ll": "@52.5,-1.5,6z",
                    "type": "search",
                    "hl": "en",
                    "api_key": ctx.settings.serpapi_api_key,
                    "start": start,
                },
            )
        if response.status_code in {401, 403}:
            raise FatalSourceError("SerpApi rejected the API key. Check SERPAPI_API_KEY at https://serpapi.com/")
        if response.status_code == 429:
            raise TransientSourceError("SerpApi rate limit. Retry shortly.", retry_after=60)
        if response.status_code >= 500:
            raise TransientSourceError(f"SerpApi server error {response.status_code}.", retry_after=30)
        if response.status_code >= 400:
            raise FatalSourceError(f"SerpApi request failed ({response.status_code}).")
        payload = response.json()
        leads = parse_serpapi_local(payload, profession["label"])
        requests_made = 1
        if not leads and start == 0:
            self._pacer.wait(ctx.settings.serpapi_min_interval_seconds)
            with self._session() as client:
                organic = client.get(
                    SEARCH_URL,
                    params={
                        "engine": "google",
                        "q": f"{keyword} {area} contact phone",
                        "gl": "uk",
                        "hl": "en",
                        "num": 10,
                        "api_key": ctx.settings.serpapi_api_key,
                    },
                )
            if organic.status_code == 200:
                leads = parse_serpapi_organic(organic.json(), profession["label"])
                requests_made = 2
        nxt = self._advance(profession_index, keyword_index, area_index, start, len(keywords), bool(leads))
        note = (
            f"SerpApi · {profession['label']} · {area} · '{keyword}' · "
            f"{len(leads)} businesses"
        )
        done = int(nxt["profession_index"]) >= len(ctx.professions)
        return FetchResult(leads, nxt, done, requests_made, note, note)

    def _advance(
        self,
        profession_index: int,
        keyword_index: int,
        area_index: int,
        start: int,
        keyword_count: int,
        had_results: bool,
    ) -> dict:
        if had_results and start < 20:
            return {
                "profession_index": profession_index,
                "keyword_index": keyword_index,
                "area_index": area_index,
                "start": start + 20,
            }
        next_area = area_index + 1
        if next_area < len(UK_SEARCH_AREAS):
            return {
                "profession_index": profession_index,
                "keyword_index": keyword_index,
                "area_index": next_area,
                "start": 0,
            }
        next_keyword = keyword_index + 1
        if next_keyword < keyword_count:
            return {
                "profession_index": profession_index,
                "keyword_index": next_keyword,
                "area_index": 0,
                "start": 0,
            }
        return {
            "profession_index": profession_index + 1,
            "keyword_index": 0,
            "area_index": 0,
            "start": 0,
        }
