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

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.websiteUri,places.googleMapsUri,nextPageToken"
)
PAGE_SIZE = 10


def parse_google_places(payload: dict, profession: str) -> tuple[list[RawLead], str | None]:
    leads: list[RawLead] = []
    for place in payload.get("places") or []:
        name = ((place.get("displayName") or {}).get("text") or "").strip()
        if not name:
            continue
        place_id = (place.get("id") or "").strip() or None
        address = (place.get("formattedAddress") or "").strip()
        maps_url = place.get("googleMapsUri")
        if not maps_url and place_id:
            maps_url = f"https://www.google.com/maps/search/?api=1&query=place_id:{place_id}"
        leads.append(
            RawLead(
                business_name=name,
                profession=profession,
                address=address or None,
                postcode=extract_postcode(address),
                phone=(place.get("nationalPhoneNumber") or "").strip() or None,
                website=clean_website(place.get("websiteUri")),
                source="google_places",
                source_url=maps_url,
                external_id=place_id,
            )
        )
    token = payload.get("nextPageToken") or None
    return leads, token


class GooglePlacesAdapter(SourceAdapter):
    key = "google_places"
    label = "Google Places"
    description = (
        "Google Places text search for the name, address, phone, and website published on the place. "
        "Off unless you explicitly enable it. Asking for a phone or website makes the call a Text Search Enterprise request."
    )
    quota = SourceQuota(
        requests=1000,
        period="month",
        timezone="America/Los_Angeles",
        title="1,000 requests / month",
        detail=(
            "Google requires a billing account before any Places call. "
            "Since March 2025 the old $200 credit is a per-SKU free monthly allowance: "
            "Essentials 10,000, Pro 5,000, Enterprise 1,000. "
            "Phone and website fields are Enterprise, so this bot caps at 1,000 requests a month, "
            "resetting at midnight Pacific time, which is inside the smallest relevant free allowance. "
            "Anything past that, or any other Google product on the same project, can be charged. "
            "Editorial summaries and reviews are not requested."
        ),
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not settings.google_places_enabled:
            return (
                False,
                "Off by default. Google Places needs a billing account. "
                "Text Search that asks for a phone or website is an Enterprise call: "
                "1,000 free calls a month, then Google charges. "
                "Set GOOGLE_PLACES_ENABLED=true and GOOGLE_PLACES_API_KEY only if you accept that.",
            )
        if not settings.google_places_api_key:
            return False, "GOOGLE_PLACES_ENABLED is on, but GOOGLE_PLACES_API_KEY is empty."
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=30.0)

    def fetch(self, ctx: FetchContext) -> FetchResult:
        if not ctx.settings.google_places_enabled or not ctx.settings.google_places_api_key:
            raise FatalSourceError(
                "Google Places is switched off. It needs a billing account and can charge once the free monthly calls are used."
            )
        checkpoint = dict(ctx.checkpoint or {})
        profession_index = int(checkpoint.get("profession_index", 0))
        if profession_index >= len(ctx.professions):
            note = "Google Places search finished."
            return FetchResult([], checkpoint, True, 0, note, note)
        profession = ctx.professions[profession_index]
        body: dict = {
            "textQuery": f"{profession['label']} in {ctx.location}, UK",
            "pageSize": PAGE_SIZE,
            "regionCode": "GB",
        }
        if checkpoint.get("page_token"):
            body["pageToken"] = checkpoint["page_token"]
        self._pacer.wait(1.0 if ctx.settings.google_places_api_key else 0)
        headers = http_headers(ctx.user_agent)
        headers["X-Goog-Api-Key"] = ctx.settings.google_places_api_key
        headers["X-Goog-FieldMask"] = FIELD_MASK
        with self._session() as client:
            response = client.post(TEXT_SEARCH_URL, json=body, headers=headers)
        if response.status_code in {401, 403}:
            raise FatalSourceError(
                "Google Places rejected the key, or Places API (New) is not enabled for it."
            )
        if response.status_code == 429:
            raise TransientSourceError("Google Places quota reached. Backing off.", retry_after=60)
        if response.status_code >= 500:
            raise TransientSourceError(f"Google Places returned HTTP {response.status_code}.")
        if response.status_code >= 400:
            detail = response.text[:180].replace("\n", " ")
            raise FatalSourceError(f"Google Places returned HTTP {response.status_code}: {detail}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TransientSourceError("Google Places returned a non-JSON response.") from exc
        leads, token = parse_google_places(payload, profession["label"])
        if token:
            nxt = {"profession_index": profession_index, "page_token": token}
            done = False
        else:
            nxt = {"profession_index": profession_index + 1}
            done = profession_index + 1 >= len(ctx.professions)
        note = f"Google Places · {profession['label']} · {len(leads)} places"
        return FetchResult(leads, nxt, done, 1, clip(note, 400) or note, note)
