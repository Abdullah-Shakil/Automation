"""Serper Google search worker — free trial key from https://serper.dev/"""

from contextlib import nullcontext
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.normalize import clip, extract_postcode
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

SEARCH_URL = "https://google.serper.dev/search"

_GENERIC_TITLES = frozenset({"united kingdom", "uk", "england", "great britain", "home", "search", "results"})


def _name_from_title(title: str) -> str | None:
    name = (title.split(" - ")[0].split(" | ")[0] or "").strip()
    if not name or name.casefold() in _GENERIC_TITLES:
        return None
    return name


def parse_serper_organic(payload: dict, profession: str, source_key: str = "serper") -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in payload.get("organic") or []:
        title = (row.get("title") or "").strip()
        link = (row.get("link") or "").strip()
        snippet = (row.get("snippet") or "").strip()
        name = _name_from_title(title)
        if not name or not link:
            continue
        host = (urlparse(link).hostname or "").lower()
        if host in seen:
            continue
        seen.add(host)
        address = snippet if extract_postcode(snippet) else None
        leads.append(
            RawLead(
                business_name=clip(name, 300) or name[:300],
                profession=profession,
                address=address,
                postcode=extract_postcode(snippet),
                description=clip(snippet, 500) or None,
                website=link if link.startswith("http") else None,
                source=source_key,
                source_url=link,
                external_id=host or link[:200],
            )
        )
    return leads


class SerperAdapter(SourceAdapter):
    key = "serper"
    label = "Serper"
    description = (
        "Google search results via Serper. Free trial key from serper.dev (no card). "
        "Stores organic titles, links, and snippets for UK trade searches — nothing invented."
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
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "page": 1}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished Serper search for {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)
        keyword = keywords[keyword_index]
        page = int(checkpoint.get("page", 1))
        place = "UK" if is_nationwide(ctx.location) else f"{ctx.location}, UK"
        query = f"{keyword} {place}"
        self._pacer.wait(ctx.settings.serper_min_interval_seconds)
        with self._session() as client:
            response = client.post(
                SEARCH_URL,
                headers={"X-API-KEY": ctx.settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "uk", "hl": "en", "num": 10, "page": page},
            )
        if response.status_code in {401, 403}:
            raise FatalSourceError("Serper rejected the API key. Check SERPER_API_KEY at https://serper.dev/")
        if response.status_code == 429:
            raise TransientSourceError("Serper rate limit. Retry shortly.", retry_after=60)
        if response.status_code >= 500:
            raise TransientSourceError(f"Serper server error {response.status_code}.", retry_after=30)
        if response.status_code >= 400:
            raise FatalSourceError(f"Serper request failed ({response.status_code}).")
        payload = response.json()
        leads = parse_serper_organic(payload, profession["label"])
        if not leads:
            nxt = {"profession_index": profession_index, "keyword_index": keyword_index + 1, "page": 1}
            if nxt["keyword_index"] >= len(keywords):
                nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "page": 1}
            note = f"Serper · {profession['label']} · no further results for '{keyword}'."
        else:
            nxt = {"profession_index": profession_index, "keyword_index": keyword_index, "page": page + 1}
            if page >= 3:
                nxt = {"profession_index": profession_index, "keyword_index": keyword_index + 1, "page": 1}
                if nxt["keyword_index"] >= len(keywords):
                    nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "page": 1}
            note = f"Serper · {profession['label']} · '{keyword}' · page {page} · {len(leads)} results"
        done = int(nxt["profession_index"]) >= len(ctx.professions)
        return FetchResult(leads, nxt, done, 1, note, note)
