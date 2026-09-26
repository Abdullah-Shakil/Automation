"""Wikidata Query Service. No API key. Coverage of small local trades is thin."""

import json
from contextlib import nullcontext

import httpx

from app.config import Settings
from app.normalize import clean_website, clip, extract_postcode
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

PAGE_SIZE = 20
ITEM_URL = "https://www.wikidata.org/wiki/{qid}"


def sparql_literal(value: str) -> str:
    """Keep a short plain fragment safe to drop inside a SPARQL string."""
    cleaned = []
    for char in value:
        if char.isalnum() or char in " '&-./":
            cleaned.append(char)
    return "".join(cleaned).strip()[:60]


def parse_wikidata_bindings(bindings: list[dict], profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for row in bindings:
        item = (row.get("item") or {}).get("value") or ""
        qid = item.rstrip("/").rsplit("/", 1)[-1]
        if not qid.startswith("Q") or qid in seen:
            continue
        name = ((row.get("itemLabel") or {}).get("value") or "").strip()
        if not name or name == qid:
            continue
        seen.add(qid)
        address = ((row.get("address") or {}).get("value") or "").strip()
        description = ((row.get("desc") or {}).get("value") or "").strip()
        leads.append(
            RawLead(
                business_name=name,
                profession=profession,
                address=address or None,
                postcode=extract_postcode(address),
                phone=((row.get("phone") or {}).get("value") or "").strip() or None,
                website=clean_website((row.get("website") or {}).get("value")),
                description=description or None,
                source="wikidata",
                source_url=ITEM_URL.format(qid=qid),
                external_id=qid,
            )
        )
    return leads


def build_query(keyword: str, place: str, offset: int) -> str:
    keyword = sparql_literal(keyword).lower()
    place = sparql_literal(place).lower()
    if not keyword or not place:
        raise FatalSourceError("Wikidata needs a profession keyword and a place name.")
    return f"""
SELECT ?item ?itemLabel ?desc ?website ?phone ?address WHERE {{
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
  ?item wdt:P31/wdt:P279* wd:Q4830453 .
  ?item wdt:P17 wd:Q145 .
  ?item rdfs:label ?itemLabel .
  FILTER(LANG(?itemLabel) = "en")
  FILTER(CONTAINS(LCASE(?itemLabel), "{keyword}"))
  OPTIONAL {{ ?item schema:description ?desc . FILTER(LANG(?desc) = "en") }}
  OPTIONAL {{ ?item wdt:P856 ?website }}
  OPTIONAL {{ ?item wdt:P1329 ?phone }}
  OPTIONAL {{ ?item wdt:P6375 ?address }}
  BIND(LCASE(CONCAT(STR(?itemLabel), " ", COALESCE(STR(?desc), ""), " ", COALESCE(STR(?address), ""))) AS ?hay)
  FILTER(CONTAINS(?hay, "{place}"))
}}
LIMIT {PAGE_SIZE}
OFFSET {int(offset)}
""".strip()


class WikidataAdapter(SourceAdapter):
    key = "wikidata"
    label = "Wikidata"
    description = (
        "UK organisations on Wikidata whose English name contains the trade and whose "
        "name, description, or address mentions the place. No API key. "
        "Small local trades are rarely in Wikidata; well-known firms are more likely."
    )
    quota = SourceQuota(
        requests=60,
        period="day",
        timezone="Europe/London",
        title="60 requests / day",
        detail=(
            "The public Wikidata Query Service has no billed quota. Fair use is roughly 30 queries a minute "
            "with a required User-Agent, and queries time out at 60 seconds. "
            "This bot stays at 60 queries per UK day so a background run remains light. "
            "The window resets at midnight UK time. There is no charge."
        ),
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=70.0)

    def fetch(self, ctx: FetchContext) -> FetchResult:
        checkpoint = dict(ctx.checkpoint or {})
        profession_index = int(checkpoint.get("profession_index", 0))
        if profession_index >= len(ctx.professions):
            note = "Wikidata search finished."
            return FetchResult([], checkpoint, True, 0, note, note)
        profession = ctx.professions[profession_index]
        keywords = [str(item) for item in (profession.get("keywords") or []) if str(item).strip()]
        if not keywords:
            keywords = [str(profession.get("label") or "")]
        keyword_index = int(checkpoint.get("keyword_index", 0))
        if keyword_index >= len(keywords):
            nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "offset": 0}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)

        place = (ctx.location or "").split(",")[0].strip()
        offset = int(checkpoint.get("offset", 0))
        query = build_query(keywords[keyword_index], place, offset)
        self._pacer.wait(ctx.settings.wikidata_min_interval_seconds)
        headers = http_headers(ctx.user_agent)
        headers["Accept"] = "application/sparql-results+json"
        with self._session() as client:
            response = client.get(ctx.settings.wikidata_sparql_url, params={"query": query}, headers=headers)
        if response.status_code in {403, 406}:
            raise FatalSourceError(
                "Wikidata refused the request. Set USER_AGENT to a name for this app. "
                "The query service rejects a missing or generic agent."
            )
        if response.status_code == 429:
            raise TransientSourceError("Wikidata asked us to slow down.", retry_after=30)
        if response.status_code >= 500:
            raise TransientSourceError(f"Wikidata returned HTTP {response.status_code}.")
        if response.status_code != 200:
            detail = response.text[:180].replace("\n", " ")
            raise FatalSourceError(f"Wikidata returned HTTP {response.status_code}: {detail}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TransientSourceError("Wikidata returned a non-JSON response.") from exc
        bindings = ((payload.get("results") or {}).get("bindings") or [])
        leads = parse_wikidata_bindings(bindings, profession["label"])
        if len(bindings) < PAGE_SIZE:
            nxt = {
                "profession_index": profession_index,
                "keyword_index": keyword_index + 1,
                "offset": 0,
            }
            if keyword_index + 1 >= len(keywords):
                nxt = {"profession_index": profession_index + 1, "keyword_index": 0, "offset": 0}
        else:
            nxt = {
                "profession_index": profession_index,
                "keyword_index": keyword_index,
                "offset": offset + PAGE_SIZE,
            }
        done = nxt["profession_index"] >= len(ctx.professions)
        note = (
            f"Wikidata · {profession['label']} · {keywords[keyword_index]} in {place} · "
            f"offset {offset} · {len(leads)} organisations"
        )
        return FetchResult(leads, nxt, done, 1, clip(note, 400) or note, note)
