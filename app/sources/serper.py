"""Serper Places worker — local businesses with phone / website when Google has them."""

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

PLACES_URL = "https://google.serper.dev/places"
SEARCH_URL = "https://google.serper.dev/search"

_GENERIC_TITLES = frozenset({"united kingdom", "uk", "england", "great britain", "home", "search", "results"})


def _name_from_title(title: str) -> str | None:
    name = (title.split(" - ")[0].split(" | ")[0] or "").strip()
    if not name or name.casefold() in _GENERIC_TITLES:
        return None
    return name


def parse_serper_places(payload: dict, profession: str, source_key: str = "serper") -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in payload.get("places") or []:
        title = (row.get("title") or "").strip()
        name = _name_from_title(title) or title.strip()
        if not name:
            continue
        address = (row.get("address") or "").strip() or None
        website = usable_website(row.get("website"))
        phone_raw = (row.get("phoneNumber") or row.get("phone") or "").strip() or None
        contacts = contacts_from_text(phone_raw, row.get("description"), address)
        cid = str(row.get("cid") or row.get("placeId") or "").strip()
        category = (row.get("category") or row.get("type") or "").strip() or None
        desc_bits = [category, row.get("description"), address]
        description = clip(" · ".join(str(bit) for bit in desc_bits if bit), 500)
        host = (urlparse(website).hostname or "").lower() if website else ""
        identity = cid or host or f"{name.casefold()}|{extract_postcode(address) or ''}"
        if identity in seen:
            continue
        seen.add(identity)
        source_url = website or (f"https://www.google.com/maps?cid={cid}" if cid else None)
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
                description=description,
                source=source_key,
                source_url=source_url,
                external_id=identity[:200],
            )
        )
    return leads


def parse_serper_organic(payload: dict, profession: str, source_key: str = "serper") -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in payload.get("organic") or []:
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
                source=source_key,
                source_url=website,
                external_id=host or link[:200],
            )
        )
    return leads


class SerperAdapter(SourceAdapter):
    key = "serper"
    label = "Serper"
    description = (
        "Google local businesses via Serper Places (phone, address, website when listed). "
        "Falls back to filtered organic results. Free trial key from serper.dev."
    )
    quota = SourceQuota(
        requests=100,
        period="day",
        timezone="Europe/London",
        title="100 searches / day",
        detail="Serper trial is about 2,500 queries once. Cap keeps a trial usable longer.",
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not settings.serper_api_key:
            return (
                False,
                "Add SERPER_API_KEY to .env. Free trial key: https://serper.dev/ (sign up, copy the key).",
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
            note = "Serper search finished."
            return FetchResult([], checkpoint, True, 0, note, note)
        profession = ctx.professions[profession_index]
        keywords = list(profession.get("keywords") or []) or [profession["label"]]
        keyword_index = int(checkpoint.get("keyword_index", 0))
        if keyword_index >= len(keywords):
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "area_index": 0, "page": 1}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished Serper search for {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)
        keyword = keywords[keyword_index]
        area_index = int(checkpoint.get("area_index", 0))
        page = int(checkpoint.get("page", 1))
        if is_nationwide(ctx.location):
            area = UK_SEARCH_AREAS[area_index % len(UK_SEARCH_AREAS)]
            query = f"{keyword} in {area}"
        else:
            area = ctx.location
            query = f"{keyword} in {area}, UK"
        self._pacer.wait(ctx.settings.serper_min_interval_seconds)
        with self._session() as client:
            response = client.post(
                PLACES_URL,
                headers={"X-API-KEY": ctx.settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "uk", "hl": "en", "page": page},
            )
        if response.status_code in {401, 403}:
            raise FatalSourceError("Serper rejected the API key. Check SERPER_API_KEY at https://serper.dev/")
        if response.status_code == 429:
            raise TransientSourceError("Serper rate limit. Retry shortly.", retry_after=60)
        if response.status_code >= 500:
            raise TransientSourceError(f"Serper server error {response.status_code}.", retry_after=30)
        if response.status_code >= 400:
            raise FatalSourceError(f"Serper Places request failed ({response.status_code}).")
        payload = response.json()
        leads = parse_serper_places(payload, profession["label"])
        # Fallback once to organic when Places is empty for this query.
        if not leads and page == 1:
            self._pacer.wait(ctx.settings.serper_min_interval_seconds)
            with self._session() as client:
                organic = client.post(
                    SEARCH_URL,
                    headers={"X-API-KEY": ctx.settings.serper_api_key, "Content-Type": "application/json"},
                    json={"q": f"{keyword} {area} contact phone", "gl": "uk", "hl": "en", "num": 10},
                )
            if organic.status_code == 200:
                leads = parse_serper_organic(organic.json(), profession["label"])
                requests_made = 2
            else:
                requests_made = 1
        else:
            requests_made = 1

        nxt = self._advance(profession_index, keyword_index, area_index, page, len(keywords), bool(leads))
        note = (
            f"Serper · {profession['label']} · {area} · '{keyword}' · page {page} · "
            f"{len(leads)} businesses"
        )
        done = int(nxt["profession_index"]) >= len(ctx.professions)
        return FetchResult(leads, nxt, done, requests_made, note, note)

    def _advance(
        self,
        profession_index: int,
        keyword_index: int,
        area_index: int,
        page: int,
        keyword_count: int,
        had_results: bool,
    ) -> dict:
        if had_results and page < 2:
            return {
                "profession_index": profession_index,
                "keyword_index": keyword_index,
                "area_index": area_index,
                "page": page + 1,
            }
        next_area = area_index + 1
        if next_area < len(UK_SEARCH_AREAS):
            return {
                "profession_index": profession_index,
                "keyword_index": keyword_index,
                "area_index": next_area,
                "page": 1,
            }
        next_keyword = keyword_index + 1
        if next_keyword < keyword_count:
            return {
                "profession_index": profession_index,
                "keyword_index": next_keyword,
                "area_index": 0,
                "page": 1,
            }
        return {
            "profession_index": profession_index + 1,
            "keyword_index": 0,
            "area_index": 0,
            "page": 1,
        }
