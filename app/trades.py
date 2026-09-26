"""Fixed UK trades Leadlane searches. All (blank) or one of five."""

from __future__ import annotations

from typing import Any

ALLOWED_PRESET_IDS = (
    "electrician",
    "gardener",
    "painter",
    "plumber",
    "solicitor",
)

TRADE_PRESETS: dict[str, dict[str, Any]] = {
    "electrician": {
        "label": "Electricians",
        "keywords": ["electrician", "electrical"],
        "sic_codes": ["43210"],
        "osm_tags": [{"key": "craft", "value": "electrician"}],
    },
    "gardener": {
        "label": "Gardeners",
        "keywords": ["gardener", "landscaping"],
        "sic_codes": ["81300"],
        "osm_tags": [{"key": "craft", "value": "gardener"}],
    },
    "painter": {
        "label": "Painters",
        "keywords": ["painter", "decorator"],
        "sic_codes": ["43341"],
        "osm_tags": [{"key": "craft", "value": "painter"}],
    },
    "plumber": {
        "label": "Plumbers",
        "keywords": ["plumber", "plumbing"],
        "sic_codes": ["43220"],
        "osm_tags": [{"key": "craft", "value": "plumber"}],
    },
    "solicitor": {
        "label": "Solicitors",
        "keywords": ["solicitor"],
        "sic_codes": ["69102"],
        "osm_tags": [{"key": "office", "value": "lawyer"}],
    },
}


def list_presets() -> list[dict[str, Any]]:
    return [{"id": key, **TRADE_PRESETS[key]} for key in ALLOWED_PRESET_IDS]


def normalize_preset(preset_id: str | None) -> str:
    """Return '' for All, or a known preset id. Unknown → '' (All)."""
    raw = (preset_id or "").strip().lower()
    if not raw or raw in {"all", "all_trades", "*", "any"}:
        return ""
    return raw if raw in TRADE_PRESETS else ""


def snapshot_for_preset(preset_id: str | None) -> list[dict[str, Any]]:
    """Profession dicts for FetchContext. Blank preset → all five."""
    pid = normalize_preset(preset_id)
    keys = (pid,) if pid else ALLOWED_PRESET_IDS
    rows = []
    for key in keys:
        item = TRADE_PRESETS[key]
        rows.append(
            {
                "slug": key,
                "label": item["label"],
                "keywords": list(item["keywords"]),
                "sic_codes": list(item["sic_codes"]),
                "osm_tags": list(item["osm_tags"]),
            }
        )
    return rows


def preset_label(preset_id: str | None) -> str:
    pid = normalize_preset(preset_id)
    if not pid:
        return "All trades"
    return TRADE_PRESETS[pid]["label"]
