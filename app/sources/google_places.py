"""Google Places text search — discover UK trade listings (official API, no scraping)."""

import httpx

from app.config import Settings
from app.normalize import clip, extract_postcode
from app.services.contacts import UK_SEARCH_AREAS, contacts_from_text, town_from_address, usable_website
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

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.businessStatus",
        "places.formattedAddress",
        "places.googleMapsUri",
        "places.types",
    ]
)


def parse_places(payload: dict, profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for place in payload.get("places") or []:
        status = (place.get("businessStatus") or "").upper()
        if status and status not in {"OPERATIONAL", ""}:
            continue
        name = ((place.get("displayName") or {}).get("text") or "").strip()
        if not name:
            continue
        address = (place.get("formattedAddress") or "").strip() or None
        website = usable_website(place.get("websiteUri"))
        phone_raw = (place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or "").strip() or None
        contacts = contacts_from_text(phone_raw, address)
        place_id = str(place.get("id") or "").strip()
        identity = place_id or f"{name.casefold()}|{extract_postcode(address) or ''}"
        if identity in seen:
            continue
        seen.add(identity)
        types = place.get("types") or []
        category = next((str(t).replace("_", " ") for t in types if t), profession)
        leads.append(
            RawLead(
                business_name=clip(name, 300) or name[:300],
                profession=profession,
                category=clip(category, 120),
                address=clip(address, 2000),
                postcode=extract_postcode(address),
                town=town_from_address(address),
                phone=contacts["phone"] or phone_raw,
                mobile=contacts["mobile"],
                email=contacts["email"],
                website=website,
                description=clip(f"{category} · {status or 'listed'}", 500),
                source="google_places",
                source_url=place.get("googleMapsUri") or website,
                external_id=identity[:200],
            )
        )
    return leads


class GooglePlacesAdapter(SourceAdapter):
    key = "google_places"
    label = "Google Places"
    description = (
        "Google Maps Platform Places text search. Finds UK trade businesses with phone, "
        "website, and address when Google has them. Needs a free Maps API key with Places enabled."
    )
    group = "collect"
    quota = SourceQuota(
        requests=100,
        period="day",
        timezone="Europe/London",
        title="100 searches / day",
        detail="Maps Platform has monthly credit. Leadlane caps Places text searches at 100 / UK day.",
    )

    def __init__(self) -> None:
        self._pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        if not (settings.google_places_api_key or "").strip():
            return False, "Add GOOGLE_PLACES_API_KEY to .env (Google Cloud → Places API)."
        return True, ""

    def estimated_requests(self, ctx: FetchContext) -> int:
        areas = list(UK_SEARCH_AREAS)[:4] if is_nationwide(ctx.location) else [ctx.location]
        return max(1, len(ctx.professions) * len(areas))

    def fetch(self, ctx: FetchContext) -> FetchResult:
        key = (ctx.settings.google_places_api_key or "").strip()
        if not key:
            raise FatalSourceError("GOOGLE_PLACES_API_KEY is missing.")

        professions = ctx.professions or []
        areas = list(UK_SEARCH_AREAS) if is_nationwide(ctx.location) else [ctx.location]
        cp = dict(ctx.checkpoint or {})
        pi = int(cp.get("profession_index") or 0)
        ai = int(cp.get("area_index") or 0)
        if pi >= len(professions):
            return FetchResult([], {}, True, 0, "Google Places finished.", "Google Places done.")

        profession = professions[pi]
        label = profession.get("label") or profession.get("id") or "trade"
        area = areas[ai % len(areas)] if areas else "England"
        query = f"{label} in {area}, England"

        self._pacer.wait(ctx.settings.google_places_min_interval_seconds)
        try:
            with httpx.Client(timeout=25.0) as client:
                response = client.post(
                    PLACES_URL,
                    headers={
                        "X-Goog-Api-Key": key,
                        "X-Goog-FieldMask": FIELD_MASK,
                        "Content-Type": "application/json",
                    },
                    json={"textQuery": query, "regionCode": "GB", "pageSize": 10},
                )
        except httpx.TimeoutException as exc:
            raise TransientSourceError("Google Places timed out.", retry_after=30) from exc
        except httpx.HTTPError as exc:
            raise TransientSourceError(f"Google Places network error: {exc.__class__.__name__}") from exc

        if response.status_code in {401, 403}:
            raise FatalSourceError("Google Places rejected the API key.")
        if response.status_code == 429:
            raise TransientSourceError("Google Places rate limited.", retry_after=60)
        if response.status_code >= 400:
            raise TransientSourceError(f"Google Places HTTP {response.status_code}.", retry_after=30)

        leads = parse_places(response.json(), label)
        ai += 1
        if ai >= len(areas):
            ai = 0
            pi += 1
        done = pi >= len(professions)
        return FetchResult(
            leads=leads,
            checkpoint={"profession_index": pi, "area_index": ai},
            done=done,
            requests_made=1,
            progress_note=f"Places · {label} · {area} · {len(leads)} hits",
            log_message=f"Google Places searched {query!r}.",
        )
