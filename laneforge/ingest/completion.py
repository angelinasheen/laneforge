"""Which item ids count as a 'completed item' purchase (draft 4 cleaning rules).

Completed = on Summoner's Rift (maps["11"]), purchasable, no `into`, and not
tagged Consumable or Trinket. Transformation results (Muramana, Seraph's
Embrace, Fimbulwinter, ...) carry `specialRecipe` pointing at their base item;
the timeline emits a purchase when the transformation fires, but the base
item's completion is already stored, so those ids are excluded. They are
derived from the JSON, not hard-coded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

SUMMONERS_RIFT = "11"
EXCLUDED_TAGS = frozenset({"Consumable", "Trinket"})
BASIC_BOOTS_ID = "1001"
BOOTS_TAG = "Boots"


def transformation_ids(items: Mapping[str, Mapping[str, Any]]) -> frozenset[int]:
    """Items whose specialRecipe points at another catalogue item."""
    return frozenset(
        int(item_id) for item_id, item in items.items()
        if item.get("specialRecipe") and str(item["specialRecipe"]) in items
    )


def completed_item_ids(item_json: Mapping[str, Any]) -> frozenset[int]:
    """Completed item ids from a parsed Data Dragon item.json."""
    items = item_json.get("data")
    if not isinstance(items, Mapping):
        raise ValueError("item.json has no 'data' object")
    transformed = transformation_ids(items)
    return frozenset(
        int(item_id) for item_id, item in items.items()
        if _is_completed(item) and int(item_id) not in transformed
    )


def load_completed_ids(ddragon_dir: Path) -> frozenset[int]:
    path = ddragon_dir / "item.json"
    try:
        return completed_item_ids(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{path} is missing; download Data Dragon first") from exc


def _is_completed(item: Mapping[str, Any]) -> bool:
    return (
        bool(item.get("maps", {}).get(SUMMONERS_RIFT))
        and bool(item.get("gold", {}).get("purchasable"))
        and (not item.get("into") or _is_upgraded_boots(item))
        and not EXCLUDED_TAGS.intersection(item.get("tags", ()))
    )


def _is_upgraded_boots(item: Mapping[str, Any]) -> bool:
    """Tier-2 boots (Mercury's Treads, Plated Steelcaps, ...) are completed
    items even though this patch gives each an `into` tier-3 upgrade gated by
    Feats of Strength. Anything tagged Boots and built from basic Boots counts."""
    return BOOTS_TAG in item.get("tags", ()) and BASIC_BOOTS_ID in item.get("from", ())
