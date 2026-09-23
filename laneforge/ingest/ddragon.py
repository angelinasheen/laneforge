"""Load Data Dragon champion.json and item.json into `champion` and `item`.

Implements draft 4's catalogue cleaning rules:
  * items: only `maps["11"]` and `gold.purchasable`; duplicate ids above 320000
    whose name matches a lower id are collapsed to the lower id;
  * `is_boots` = "Boots" tag; `is_legendary` = no `into`, no `requiredChampion`,
    not Boots/Consumable/Trinket, `gold.total >= 2000`;
  * ten stats from the `stats` object, five more parsed from the description's
    `<stats>` block; percentages stored as fractions (35% -> 0.350).
Everything is upserted in one transaction, so the loader is idempotent.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from laneforge.db import REPO_ROOT

log = logging.getLogger(__name__)

DEFAULT_DDRAGON_DIR = REPO_ROOT / "data" / "ddragon"
SUMMONERS_RIFT = "11"
DUPLICATE_ID_FLOOR = 320000
LEGENDARY_MIN_GOLD = 2000
NON_LEGENDARY_TAGS = frozenset({"Boots", "Consumable", "Trinket"})
MAX_ITEM_NAME = 60
FRACTION = Decimal("0.001")

# Data Dragon `stats` key -> (column, is_fraction)
STATS_OBJECT_COLUMNS: dict[str, tuple[str, bool]] = {
    "FlatPhysicalDamageMod": ("attack_damage", False),
    "FlatMagicDamageMod": ("ability_power", False),
    "FlatHPPoolMod": ("health", False),
    "FlatMPPoolMod": ("mana", False),
    "PercentAttackSpeedMod": ("attack_speed_pct", True),
    "FlatCritChanceMod": ("crit_chance_pct", True),
    "FlatMovementSpeedMod": ("move_speed", False),
    "PercentLifeStealMod": ("life_steal_pct", True),
    "FlatArmorMod": ("armor", False),
    "FlatSpellBlockMod": ("magic_resist", False),
}

# `<stats>` label -> (column when the value is flat, column when it is a percent).
# None means that form of the label is not a stat we store.
STATS_BLOCK_LABELS: dict[str, tuple[str | None, str | None]] = {
    "Ability Haste": ("ability_haste", None),
    "Lethality": ("lethality", None),
    "Armor Penetration": (None, "armor_pen_pct"),
    "Magic Penetration": ("magic_pen_flat", "magic_pen_pct"),
    "Tenacity": (None, "tenacity_pct"),
    # also carried by the stats object; used only when the object lacks them
    "Attack Damage": ("attack_damage", None),
    "Ability Power": ("ability_power", None),
    "Health": ("health", None),
    "Mana": ("mana", None),
    "Attack Speed": (None, "attack_speed_pct"),
    "Critical Strike Chance": (None, "crit_chance_pct"),
    "Move Speed": ("move_speed", None),
    "Life Steal": (None, "life_steal_pct"),
    "Armor": ("armor", None),
    "Magic Resist": ("magic_resist", None),
}
BLOCK_ONLY_COLUMNS = ("ability_haste", "lethality", "armor_pen_pct",
                      "magic_pen_flat", "magic_pen_pct", "tenacity_pct")
STAT_COLUMNS = tuple(col for col, _ in STATS_OBJECT_COLUMNS.values()) + BLOCK_ONLY_COLUMNS

STATS_BLOCK_RE = re.compile(r"<stats>(.*?)</stats>", re.S)
STAT_PAIR_RE = re.compile(r"<attention>\s*([\d.]+)\s*(%?)\s*</attention>\s*([^<]+)")

ITEM_COLUMNS = ("item_id", "name", "gold_cost", "is_legendary", "is_boots") + STAT_COLUMNS + (
    "applies_grievous_wounds",)
CHAMPION_COLUMNS = (
    "champion_id", "name", "ddragon_key", "base_health", "health_per_level", "base_armor",
    "armor_per_level", "base_magic_resist", "magic_resist_per_level", "base_attack_damage",
    "attack_damage_per_level", "base_attack_speed", "attack_speed_per_level_pct",
)
# champion column -> Data Dragon stats key
CHAMPION_STAT_KEYS = {
    "base_health": "hp", "health_per_level": "hpperlevel",
    "base_armor": "armor", "armor_per_level": "armorperlevel",
    "base_magic_resist": "spellblock", "magic_resist_per_level": "spellblockperlevel",
    "base_attack_damage": "attackdamage", "attack_damage_per_level": "attackdamageperlevel",
    "base_attack_speed": "attackspeed", "attack_speed_per_level_pct": "attackspeedperlevel",
}
INTEGER_CHAMPION_COLUMNS = frozenset({"base_health", "base_armor", "base_magic_resist",
                                      "base_attack_damage"})


class CatalogueError(RuntimeError):
    """Data Dragon files are missing or not shaped as expected."""


@dataclass(frozen=True)
class CatalogueReport:
    champions_loaded: int
    items_loaded: int
    items_skipped: int
    duplicates_collapsed: int
    unparsed_stat_labels: tuple[str, ...]


# --- parsing -----------------------------------------------------------------

def _fraction(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(FRACTION)


def _stat_value(raw: str, is_percent: bool) -> Any:
    number = Decimal(raw)
    return (number / 100).quantize(FRACTION) if is_percent else int(round(number))


def parse_stats_block(description: str) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Parse `<attention>VALUE</attention> LABEL` pairs from the `<stats>` block.
    Returns ({column: value}, unparsed labels)."""
    block = STATS_BLOCK_RE.search(description or "")
    if not block:
        return {}, ()
    parsed: dict[str, Any] = {}
    unparsed: list[str] = []
    for raw, percent, label in STAT_PAIR_RE.findall(block.group(1)):
        label = label.strip()
        flat_col, pct_col = STATS_BLOCK_LABELS.get(label, (None, None))
        column = pct_col if percent else flat_col
        if column is None:
            unparsed.append(f"{label} (%)" if percent and label in STATS_BLOCK_LABELS else label)
            continue
        parsed = {**parsed, column: _stat_value(raw, bool(percent))}
    return parsed, tuple(unparsed)


def _stats_object_values(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        column: (_fraction(stats[key]) if is_fraction else int(round(stats[key])))
        for key, (column, is_fraction) in STATS_OBJECT_COLUMNS.items()
        if stats.get(key)
    }


def _merge_stats(item_id: int, object_stats: dict, block_stats: dict) -> dict[str, Any]:
    """Stats object wins; the <stats> block fills block-only columns and any
    column the object lacks."""
    merged = {column: 0 for column in STAT_COLUMNS}
    for column, value in block_stats.items():
        if column not in BLOCK_ONLY_COLUMNS and column not in object_stats and value:
            log.info("item %s: %s=%s taken from <stats> block (missing in stats object)",
                     item_id, column, value)
    return {**merged, **block_stats, **object_stats}


def _is_legendary(item: dict) -> bool:
    tags = set(item.get("tags", ()))
    return (not item.get("into") and not item.get("requiredChampion")
            and not (tags & NON_LEGENDARY_TAGS)
            and item["gold"]["total"] >= LEGENDARY_MIN_GOLD)


def _item_row(item_id: int, item: dict) -> tuple[dict[str, Any], tuple[str, ...]]:
    block_stats, unparsed = parse_stats_block(item.get("description", ""))
    stats = _merge_stats(item_id, _stats_object_values(item.get("stats", {})), block_stats)
    row = {
        "item_id": item_id,
        "name": item["name"][:MAX_ITEM_NAME],
        "gold_cost": int(item["gold"]["total"]),
        "is_legendary": _is_legendary(item),
        "is_boots": "Boots" in item.get("tags", ()),
        **stats,
        "applies_grievous_wounds": "Grievous Wounds" in item.get("description", ""),
    }
    return row, unparsed


def _on_rift(item: dict) -> bool:
    return bool(item.get("maps", {}).get(SUMMONERS_RIFT)) and bool(
        item.get("gold", {}).get("purchasable"))


def _collapse_duplicates(items: dict[int, dict]) -> dict[int, dict]:
    lowest_by_name: dict[str, int] = {}
    for item_id in sorted(items):
        lowest_by_name.setdefault(items[item_id]["name"], item_id)
    return {
        item_id: item for item_id, item in items.items()
        if item_id <= DUPLICATE_ID_FLOOR or lowest_by_name[item["name"]] == item_id
    }


def parse_items(data: dict[str, dict]) -> tuple[tuple[dict, ...], int, int, tuple[str, ...]]:
    """Return (rows, skipped, collapsed, unparsed labels)."""
    on_rift = {int(key): item for key, item in data.items() if _on_rift(item)}
    kept = _collapse_duplicates(on_rift)
    parsed = [_item_row(item_id, kept[item_id]) for item_id in sorted(kept)]
    labels = sorted({label for _, unparsed in parsed for label in unparsed})
    rows = tuple(row for row, _ in parsed)
    return rows, len(data) - len(on_rift), len(on_rift) - len(kept), tuple(labels)


def _champion_value(column: str, value: Any) -> Any:
    return int(round(value)) if column in INTEGER_CHAMPION_COLUMNS else Decimal(str(value))


def parse_champions(data: dict[str, dict]) -> tuple[dict, ...]:
    return tuple(
        {
            "champion_id": int(champ["key"]),
            "name": champ["name"],
            "ddragon_key": champ["id"],
            **{column: _champion_value(column, champ["stats"][key])
               for column, key in CHAMPION_STAT_KEYS.items()},
        }
        for champ in sorted(data.values(), key=lambda c: int(c["key"]))
    )


# --- loading -----------------------------------------------------------------

def _read_data(path: Path) -> dict[str, dict]:
    try:
        return json.loads(path.read_text())["data"]
    except FileNotFoundError as exc:
        raise CatalogueError(f"Data Dragon file not found: {path}") from exc
    except (json.JSONDecodeError, KeyError) as exc:
        raise CatalogueError(f"Data Dragon file {path} is not valid: {exc}") from exc


def _upsert_sql(table: str, columns: tuple[str, ...]) -> str:
    key = columns[0]
    names = ", ".join(columns)
    values = ", ".join(f"%({c})s" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns[1:])
    return (f"INSERT INTO {table} ({names}) VALUES ({values}) "
            f"ON CONFLICT ({key}) DO UPDATE SET {updates}")


def load_catalogue(conn: psycopg.Connection, ddragon_dir: Path | str | None = None) -> CatalogueReport:
    """Parse champion.json and item.json and upsert them in one transaction."""
    directory = Path(ddragon_dir) if ddragon_dir else DEFAULT_DDRAGON_DIR
    champions = parse_champions(_read_data(directory / "champion.json"))
    items, skipped, collapsed, unparsed = parse_items(_read_data(directory / "item.json"))
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(_upsert_sql("champion", CHAMPION_COLUMNS), champions)
            cur.executemany(_upsert_sql("item", ITEM_COLUMNS), items)
    except psycopg.Error as exc:
        log.error("catalogue load failed: %s", exc)
        raise
    report = CatalogueReport(
        champions_loaded=len(champions), items_loaded=len(items), items_skipped=skipped,
        duplicates_collapsed=collapsed, unparsed_stat_labels=unparsed,
    )
    log.info("catalogue loaded: %s", report)
    return report
