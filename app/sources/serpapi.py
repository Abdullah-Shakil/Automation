"""SerpApi Google search worker — free plan key from https://serpapi.com/"""

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

SEARCH_URL = "https://serpapi.com/search.json"

_GENERIC_TITLES = frozenset({"united kingdom", "uk", "england", "great britain", "home", "search", "results"})


def _name_from_title(title: str) -> str | None:
    name = (title.split(" - ")[0].split(" | ")[0] or "").strip()
    if not name or name.casefold() in _GENERIC_TITLES:
        return None
    return name


def parse_serpapi_organic(payload: dict, profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in payload.get("organic_results") or []:
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
        leads.append(
            RawLead(
                business_name=clip(name, 300) or name[:300],
                profession=profession,
                address=snippet if extract_postcode(snippet) else None,
                postcode=extract_postcode(snippet),
                description=clip(snippet, 500) or None,
                website=link if link.startswith("http") else None,
                source="serpapi",
                source_url=link,
                external_id=host or link[:200],
            )
        )
    return leads


class SerpApiAdapter(SourceAdapter):
    key = "serpapi"
    label = "SerpApi"
    description = (
        "Google SERP via SerpApi. Free monthly searches after signup. "
        "Stores organic titles, links, and snippets for UK trade searches."
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
                "Add SERPAPI_API_KEY to .env. Free key: https://serpapi.com/ (sign up -> API key).",
            )
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=40.0)

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
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "start": 0}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished SerpApi search for {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)
        keyword = keywords[keyword_index]
        start = int(checkpoint.get("start", 0))
        place = "UK" if is_nationwide(ctx.location) else f"{ctx.location}, UK"
        query = f"{keyword} {place}"
        self._pacer.wait(ctx.settings.serpapi_min_interval_seconds)
        with self._session() as client:
            response = client.get(
                SEARCH_URL,
                params={
                    "api_key": ctx.settings.serpapi_api_key,
                    "engine": "google",
                    "q": query,
                    "gl": "uk",
                    "hl": "en",
                    "num": 10,
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
        if payload.get("error"):
            raise FatalSourceError(str(payload["error"])[:400])
        leads = parse_serpapi_organic(payload, profession["label"])
        if not leads or start >= 20:
            nxt = {"profession_index": profession_index, "keyword_index": keyword_index + 1, "start": 0}
            if nxt["keyword_index"] >= len(keywords):
                nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "start": 0}
            note = f"SerpApi · {profession['label']} · no further results for '{keyword}'."
        else:
            nxt = {"profession_index": profession_index, "keyword_index": keyword_index, "start": start + 10}
            note = f"SerpApi · {profession['label']} · '{keyword}' · start {start} · {len(leads)} results"
        done = int(nxt["profession_index"]) >= len(ctx.professions)
        return FetchResult(leads, nxt, done, 1, note, note)
