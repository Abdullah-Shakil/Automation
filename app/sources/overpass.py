import json
import math
from contextlib import nullcontext

import httpx

from app.config import Settings
from app.normalize import TAG_RE, clean_email, clean_website, clip, extract_postcode
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


def make_tiles(south: float, north: float, west: float, east: float, max_span: float = 0.08, max_tiles: int = 48) -> list[dict]:
    lat_span = north - south
    lon_span = east - west
    if lat_span <= 0 or lon_span <= 0:
        raise FatalSourceError("The geocoder returned an empty area.")
    span = max_span
    rows = max(1, math.ceil(lat_span / span))
    cols = max(1, math.ceil(lon_span / span))
    while rows * cols > max_tiles:
        span *= 1.3
        rows = max(1, math.ceil(lat_span / span))
        cols = max(1, math.ceil(lon_span / span))
    lat_step = lat_span / rows
    lon_step = lon_span / cols
    tiles = []
    for row in range(rows):
        for col in range(cols):
            tiles.append(
                {
                    "south": round(south + row * lat_step, 6),
                    "north": round(south + (row + 1) * lat_step, 6),
                    "west": round(west + col * lon_step, 6),
                    "east": round(west + (col + 1) * lon_step, 6),
                }
            )
    return tiles


def osm_address(tags: dict) -> str:
    number = (tags.get("addr:housenumber") or "").strip()
    street = (tags.get("addr:street") or "").strip()
    street_line = " ".join(part for part in (number, street) if part)
    parts = [
        (tags.get("addr:housename") or "").strip(),
        street_line,
        (tags.get("addr:suburb") or "").strip(),
        (tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or "").strip(),
        (tags.get("addr:postcode") or "").strip(),
    ]
    seen = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)


def parse_overpass_elements(elements: list[dict], profession: str) -> list[RawLead]:
    leads: list[RawLead] = []
    seen: set[str] = set()
    for element in elements:
        tags = element.get("tags") or {}
        name = (tags.get("name") or tags.get("operator") or "").strip()
        if not name:
            continue
        external_id = f"{element.get('type') or 'node'}:{element.get('id')}"
        if external_id in seen:
            continue
        seen.add(external_id)
        address = osm_address(tags)
        postcode = (tags.get("addr:postcode") or "").strip() or extract_postcode(address)
        osm_type = element.get("type") or "node"
        osm_id = element.get("id")
        leads.append(
            RawLead(
                business_name=name,
                profession=profession,
                address=address or None,
                postcode=postcode or None,
                phone=(tags.get("phone") or tags.get("contact:phone") or "").strip() or None,
                website=clean_website(tags.get("website") or tags.get("contact:website") or tags.get("url")),
                email=clean_email(tags.get("email") or tags.get("contact:email")),
                description=(tags.get("description") or "").strip() or None,
                source="overpass",
                source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                external_id=external_id,
            )
        )
    return leads


def _validate_tag(tag: dict) -> tuple[str, str]:
    key = str(tag.get("key") or "")
    value = str(tag.get("value") or "")
    if not TAG_RE.match(key) or not TAG_RE.match(value):
        raise FatalSourceError(f"OpenStreetMap tag {key}={value} is not a plain key and value.")
    return key, value


class OverpassAdapter(SourceAdapter):
    key = "overpass"
    label = "OpenStreetMap"
    description = (
        "Places tagged on OpenStreetMap, via the public Overpass service. "
        "A plumber is craft=plumber, a solicitor is office=lawyer, and so on. "
        "Phone, website, email, and description are stored only when a mapper wrote them on the place. "
        "A mapper note is not stored. "
        "No API key. Requests are spaced out so the shared service stays usable."
    )
    quota = SourceQuota(
        requests=100,
        period="day",
        timezone="Europe/London",
        title="100 requests / day",
        detail=(
            "The public Overpass service asks regular automated use to stay under about 100 queries a day "
            "(one hundredth of the 10,000 one-off allowance) and under about 10 MB. "
            "Nominatim geocoding counts toward the same daily figure. "
            "The window resets at midnight UK time. No API key and no charge."
        ),
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client
        self._geocode_pacer = Pacer()
        self._overpass_pacer = Pacer()

    def is_available(self, settings: Settings) -> tuple[bool, str]:
        return True, ""

    def _session(self):
        if self.client is not None:
            return nullcontext(self.client)
        return httpx.Client(timeout=45.0)

    def fetch(self, ctx: FetchContext) -> FetchResult:
        checkpoint = dict(ctx.checkpoint or {})
        with self._session() as client:
            if "tiles" not in checkpoint:
                south, north, west, east, display = self._geocode(ctx, client)
                checkpoint["tiles"] = make_tiles(south, north, west, east)
                checkpoint["place_name"] = display
                checkpoint.setdefault("profession_index", 0)
                checkpoint.setdefault("tag_index", 0)
                checkpoint.setdefault("tile_index", 0)
                note = f"Mapped “{ctx.location}” to {display}. {len(checkpoint['tiles'])} map tiles."
                return FetchResult([], checkpoint, False, 1, clip(note, 400) or note, note)
            return self._query_tile(ctx, client, checkpoint)

    def _geocode(self, ctx: FetchContext, client: httpx.Client) -> tuple[float, float, float, float, str]:
        self._geocode_pacer.wait(ctx.settings.nominatim_min_interval_seconds)
        response = client.get(
            ctx.settings.nominatim_url,
            params={"q": ctx.location, "format": "json", "countrycodes": "gb", "limit": 1},
            headers=http_headers(ctx.user_agent),
        )
        if response.status_code == 429:
            raise TransientSourceError("The geocoder asked us to slow down.", retry_after=5)
        if response.status_code == 403:
            raise FatalSourceError(
                "The geocoder refused the request (HTTP 403). Set USER_AGENT to a name for this app. "
                "Nominatim blocks browser-like agents and placeholder addresses such as example.com."
            )
        if response.status_code >= 500:
            raise TransientSourceError(f"Geocoder returned HTTP {response.status_code}.")
        if response.status_code != 200:
            raise FatalSourceError(f"Geocoder returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TransientSourceError("Geocoder returned a non-JSON response.") from exc
        if not payload:
            raise FatalSourceError(
                f"No UK place found for “{ctx.location}”. Try a town, city, or postcode."
            )
        hit = payload[0]
        south, north, west, east = (float(part) for part in hit["boundingbox"])
        display = hit.get("display_name") or ctx.location
        return south, north, west, east, display

    def _query_tile(self, ctx: FetchContext, client: httpx.Client, checkpoint: dict) -> FetchResult:
        tiles: list[dict] = checkpoint.get("tiles") or []
        if not tiles:
            raise FatalSourceError("The saved map area is empty. Stop the job and start a new one.")

        while True:
            profession_index = int(checkpoint.get("profession_index", 0))
            if profession_index >= len(ctx.professions):
                note = "OpenStreetMap search finished."
                return FetchResult([], checkpoint, True, 0, note, note)
            profession = ctx.professions[profession_index]
            tags = list(profession.get("osm_tags") or [])
            tag_index = int(checkpoint.get("tag_index", 0))
            if not tags or tag_index >= len(tags):
                checkpoint["profession_index"] = profession_index + 1
                checkpoint["tag_index"] = 0
                checkpoint["tile_index"] = 0
                continue
            tile_index = int(checkpoint.get("tile_index", 0))
            if tile_index >= len(tiles):
                checkpoint["tag_index"] = tag_index + 1
                checkpoint["tile_index"] = 0
                continue
            break

        tag = tags[tag_index]
        key, value = _validate_tag(tag)
        tile = tiles[tile_index]
        elements = self._overpass(ctx, client, key, value, tile)
        leads = parse_overpass_elements(elements, profession["label"])
        checkpoint["tile_index"] = tile_index + 1
        done = _no_work_left(checkpoint, ctx.professions, len(tiles))
        note = (
            f"OpenStreetMap · {profession['label']} · {key}={value} · "
            f"tile {tile_index + 1}/{len(tiles)} · {len(leads)} places"
        )
        return FetchResult(leads, checkpoint, done, 1, clip(note, 400) or note, note)

    def _overpass(self, ctx: FetchContext, client: httpx.Client, key: str, value: str, tile: dict) -> list[dict]:
        self._overpass_pacer.wait(ctx.settings.overpass_min_interval_seconds)
        bbox = "{south:.6f},{west:.6f},{north:.6f},{east:.6f}".format(**tile)
        query = (
            "[out:json][timeout:25];\n"
            "(\n"
            f'  node["{key}"="{value}"]({bbox});\n'
            f'  way["{key}"="{value}"]({bbox});\n'
            f'  relation["{key}"="{value}"]({bbox});\n'
            ");\n"
            "out center tags;\n"
        )
        response = client.post(
            ctx.settings.overpass_url,
            data={"data": query},
            headers=http_headers(ctx.user_agent),
        )
        if response.status_code == 429:
            raise TransientSourceError("OpenStreetMap Overpass rate limit. Backing off.", retry_after=_retry(response))
        if response.status_code >= 500:
            raise TransientSourceError(f"Overpass returned HTTP {response.status_code}.")
        if response.status_code != 200:
            raise FatalSourceError(f"Overpass returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TransientSourceError("Overpass returned a non-JSON response.") from exc
        remark = str(payload.get("remark") or "")
        if remark and not payload.get("elements"):
            lowered = remark.lower()
            if "timeout" in lowered or "runtime error" in lowered or "too busy" in lowered:
                raise TransientSourceError(remark[:300])
        return payload.get("elements") or []


def _no_work_left(checkpoint: dict, professions: list[dict], tile_count: int) -> bool:
    """True when the cursor already sits past the final tile of the final tag."""
    probe = {
        "profession_index": checkpoint.get("profession_index", 0),
        "tag_index": checkpoint.get("tag_index", 0),
        "tile_index": checkpoint.get("tile_index", 0),
    }
    guard = 0
    while guard < 10000:
        guard += 1
        profession_index = int(probe["profession_index"])
        if profession_index >= len(professions):
            return True
        tags = list(professions[profession_index].get("osm_tags") or [])
        tag_index = int(probe["tag_index"])
        if not tags or tag_index >= len(tags):
            probe["profession_index"] = profession_index + 1
            probe["tag_index"] = 0
            probe["tile_index"] = 0
            continue
        if int(probe["tile_index"]) >= tile_count:
            probe["tag_index"] = tag_index + 1
            probe["tile_index"] = 0
            continue
        return False
    return False


def _retry(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else 30
    except ValueError:
        return 30
