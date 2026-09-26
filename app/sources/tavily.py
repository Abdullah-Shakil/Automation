"""Tavily search worker — free plan key from https://app.tavily.com/home"""

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

SEARCH_URL = "https://api.tavily.com/search"

_GENERIC_TITLES = frozenset({"united kingdom", "uk", "england", "great britain", "home", "search", "results"})


def _name_from_title(title: str) -> str | None:
    name = (title.split(" - ")[0].split(" | ")[0] or "").strip()
    if not name or name.casefold() in _GENERIC_TITLES:
        return None
    return name


def parse_tavily_results(payload: dict, profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in payload.get("results") or []:
        title = (row.get("title") or "").strip()
        link = (row.get("url") or "").strip()
        snippet = (row.get("content") or row.get("snippet") or "").strip()
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
                source="tavily",
                source_url=link,
                external_id=host or link[:200],
            )
        )
    return leads


class TavilyAdapter(SourceAdapter):
    key = "tavily"
    label = "Tavily"
    description = (
        "Tavily AI search. Free monthly credits after you sign in and create a key. "
        "Stores search result titles, URLs, and snippets for UK trade queries."
    )
    quota = SourceQuota(
        requests=50,
        period="day",
        timezone="Europe/London",
        title="50 searches / day",
        detail="Free plan is about 1,000 credits a month. Cap keeps the month usable.",
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not settings.tavily_api_key:
            return (
                False,
                "Add TAVILY_API_KEY to .env. Free key: https://app.tavily.com/home",
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
            note = "Tavily search finished."
            return FetchResult([], checkpoint, True, 0, note, note)
        profession = ctx.professions[profession_index]
        keywords = list(profession.get("keywords") or []) or [profession["label"]]
        keyword_index = int(checkpoint.get("keyword_index", 0))
        if keyword_index >= len(keywords):
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished Tavily search for {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)
        keyword = keywords[keyword_index]
        place = "United Kingdom" if is_nationwide(ctx.location) else f"{ctx.location}, United Kingdom"
        query = f"{keyword} businesses in {place}"
        self._pacer.wait(ctx.settings.tavily_min_interval_seconds)
        with self._session() as client:
            response = client.post(
                SEARCH_URL,
                json={
                    "api_key": ctx.settings.tavily_api_key,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": False,
                    "max_results": 8,
                },
            )
        if response.status_code in {401, 403}:
            raise FatalSourceError("Tavily rejected the API key. Check TAVILY_API_KEY at https://app.tavily.com/home")
        if response.status_code == 429:
            raise TransientSourceError("Tavily rate limit. Retry shortly.", retry_after=60)
        if response.status_code >= 500:
            raise TransientSourceError(f"Tavily server error {response.status_code}.", retry_after=30)
        if response.status_code >= 400:
            detail = (response.text or "")[:200].strip()
            raise FatalSourceError(
                f"Tavily request failed ({response.status_code})"
                + (f": {detail}" if detail else ".")
            )
        leads = parse_tavily_results(response.json(), profession["label"])
        nxt = {"profession_index": profession_index, "keyword_index": keyword_index + 1}
        if nxt["keyword_index"] >= len(keywords):
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0}
        note = f"Tavily · {profession['label']} · '{keyword}' · {len(leads)} results"
        done = int(nxt["profession_index"]) >= len(ctx.professions)
        return FetchResult(leads, nxt, done, 1, note, note)
