import json
from contextlib import nullcontext

import httpx

from app.config import Settings
from app.normalize import clip, extract_postcode, format_address, split_address
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
    is_nationwide,
)

PAGE_SIZE = 20
PROFILE_URL = "https://find-and-update.company-information.service.gov.uk/company/{number}"


def parse_companies_house_items(items: list[dict], profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    for item in items:
        status = (item.get("company_status") or "active").lower()
        if status != "active":
            continue
        name = (item.get("company_name") or item.get("title") or "").strip()
        number = str(item.get("company_number") or "").strip()
        if not name or not number:
            continue
        address_obj = item.get("registered_office_address") or item.get("address") or {}
        address = format_address(address_obj)
        parts = split_address(address_obj)
        postcode = parts["postcode"] or extract_postcode(address)
        sic_codes = [str(code).strip() for code in (item.get("sic_codes") or []) if str(code).strip()]
        company_type = str(item.get("company_type") or "").replace("-", " ").strip()
        incorporated = str(item.get("date_of_creation") or "").strip()[:20]
        leads.append(
            RawLead(
                business_name=name,
                profession=profession,
                address=address or None,
                postcode=postcode or None,
                description=_company_description(item),
                source="companies_house",
                source_url=PROFILE_URL.format(number=number),
                external_id=number,
                company_number=number,
                company_type=company_type or None,
                company_status=status,
                category=profession,
                sic_codes=", ".join(sic_codes[:8]) or None,
                incorporated_on=incorporated or None,
                address_line1=parts["address_line1"],
                address_line2=parts["address_line2"],
                town=parts["town"],
                county=parts["county"],
            )
        )
    return leads


def parse_officers(payload: dict) -> str:
    """Names, roles, and appointment dates only. Officer home addresses are not stored."""
    lines: list[str] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        role = str(item.get("officer_role") or "").replace("-", " ").replace("_", " ").strip()
        appointed = str(item.get("appointed_on") or "").strip()[:20]
        bits = [name]
        if role:
            bits.append(role)
        if appointed:
            bits.append(appointed)
        lines.append(" · ".join(bits))
        if len(lines) >= 40:
            break
    return "\n".join(lines)


class CompaniesHouseAdapter(SourceAdapter):
    key = "companies_house"
    label = "Companies House"
    description = (
        "UK government register. Searches active companies by SIC code, or by keyword "
        "when a trade has no SIC code. "
        "The register has the company name, address, and company type. It does not list a phone, website, or email."
    )
    quota = SourceQuota(
        requests=600,
        period="5min",
        timezone="UTC",
        title="600 requests / 5 minutes",
        detail=(
            "Companies House allows 600 requests in each 5-minute period per API key, shared by every bot using that key. "
            "Leadlane uses fixed UTC blocks (00–05, 05–10, and so on), which stay inside that allowance, then pauses until the block ends. "
            "The key is free. There is no charge."
        ),
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not settings.companies_house_api_key:
            return (
                False,
                "Add COMPANIES_HOUSE_API_KEY to .env. Free key: https://developer.company-information.service.gov.uk/",
            )
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=30.0)

    def fetch(self, ctx: FetchContext) -> FetchResult:
        if not ctx.settings.companies_house_api_key:
            raise FatalSourceError("Companies House API key is not configured.")
        checkpoint = dict(ctx.checkpoint or {})
        profession_index = int(checkpoint.get("profession_index", 0))
        if profession_index >= len(ctx.professions):
            return FetchResult([], checkpoint, True, 0, "Finished", "Companies House search finished.")

        profession = ctx.professions[profession_index]
        sic_codes = [code for code in profession.get("sic_codes") or [] if code]
        if sic_codes:
            mode = "sic"
            queries = sic_codes
        else:
            mode = "keyword"
            queries = list(profession.get("keywords") or []) or [profession["label"]]
        query_index = int(checkpoint.get("query_index", 0))
        start_index = int(checkpoint.get("start_index", 0))

        if query_index >= len(queries):
            nxt = {"profession_index": profession_index + 1, "query_index": 0, "start_index": 0}
            done = nxt["profession_index"] >= len(ctx.professions)
            note = f"Finished {profession['label']}."
            return FetchResult([], nxt, done, 0, note, note)

        query = str(queries[query_index])
        params: dict[str, str | int] = {
            "company_status": "active",
            "size": PAGE_SIZE,
            "start_index": start_index,
        }
        if not is_nationwide(ctx.location):
            params["location"] = ctx.location
        if mode == "sic":
            params["sic_codes"] = query
        else:
            params["company_name_includes"] = query

        self._pacer.wait(ctx.settings.companies_house_min_interval_seconds)
        url = ctx.settings.companies_house_api_base.rstrip("/") + "/advanced-search/companies"
        with self._session() as client:
            response = client.get(
                url,
                params=params,
                auth=(ctx.settings.companies_house_api_key, ""),
                headers=http_headers(ctx.user_agent),
            )

        if response.status_code == 401:
            raise FatalSourceError("Companies House rejected the API key.")
        if response.status_code == 429:
            retry = _retry_after(response, default=60)
            raise TransientSourceError("Companies House rate limit. Backing off.", retry_after=retry)
        if response.status_code == 404:
            payload = {"items": [], "hits": 0}
        elif response.status_code >= 500:
            raise TransientSourceError(f"Companies House returned HTTP {response.status_code}.")
        elif response.status_code >= 400:
            detail = response.text[:180].replace("\n", " ")
            raise FatalSourceError(f"Companies House returned HTTP {response.status_code}: {detail}")
        else:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise TransientSourceError("Companies House returned a non-JSON response.") from exc

        items = payload.get("items") or []
        leads = parse_companies_house_items(items, profession["label"])
        hits = _as_int(payload.get("hits", payload.get("total_results")))
        next_start = start_index + len(items)
        exhausted = len(items) < PAGE_SIZE or (hits is not None and next_start >= hits)
        if exhausted:
            nxt = {
                "profession_index": profession_index,
                "query_index": query_index + 1,
                "start_index": 0,
            }
            if query_index + 1 >= len(queries):
                nxt = {"profession_index": profession_index + 1, "query_index": 0, "start_index": 0}
        else:
            nxt = {
                "profession_index": profession_index,
                "query_index": query_index,
                "start_index": next_start,
            }
        done = nxt["profession_index"] >= len(ctx.professions)
        label = "SIC" if mode == "sic" else "name"
        note = (
            f"Companies House · {profession['label']} · {label} {query} · "
            f"offset {start_index} · {len(leads)} companies"
        )
        return FetchResult(leads, nxt, done, 1, clip(note, 400) or note, note)


def _company_description(item: dict) -> str | None:
    parts: list[str] = []
    company_type = str(item.get("company_type") or "").replace("-", " ").strip()
    if company_type:
        parts.append(f"Active {company_type}")
    elif str(item.get("company_status") or "").lower() == "active":
        parts.append("Active company")
    sic_codes = [str(code) for code in (item.get("sic_codes") or []) if code]
    if sic_codes:
        parts.append("SIC " + ", ".join(sic_codes[:6]))
    text = ". ".join(parts)
    return text or None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after(response: httpx.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default
