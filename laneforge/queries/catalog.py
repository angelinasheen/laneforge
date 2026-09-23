"""Champion and item catalogue, dataset summary, and item usage."""
from __future__ import annotations

from laneforge.queries._rows import (
    CHAMPION_COLUMNS, ITEM_COLUMNS, champion_from_row, item_from_row, load_champions, rate,
)
from laneforge.queries.models import (
    MIN_GAMES, ChampionRef, DatasetSummary, ItemChampionUse, ItemRef, ItemUsage,
)

TIER_ORDER = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
              "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER")
ITEM_TOP_CHAMPIONS = 10

SQL_LIST_CHAMPIONS = f"SELECT {CHAMPION_COLUMNS} FROM champion ORDER BY name"
SQL_GET_CHAMPION = f"SELECT {CHAMPION_COLUMNS} FROM champion WHERE champion_id = %(champion_id)s"
# The catalogue page: purchasable items only (no wards, trinkets or Kalista's
# spear at 0 gold), one row per name (the lowest id wins, e.g. jungle pets).
SQL_LIST_ITEMS = f"""
SELECT DISTINCT ON (name) {ITEM_COLUMNS}
FROM item
WHERE gold_cost > 0
ORDER BY name, item_id
"""
SQL_LIST_LEGENDARY_ITEMS = f"""
SELECT DISTINCT ON (name) {ITEM_COLUMNS}
FROM item
WHERE is_legendary AND NOT is_boots AND gold_cost > 0
ORDER BY name, item_id
"""
SQL_GET_ITEM = f"SELECT {ITEM_COLUMNS} FROM item WHERE item_id = %(item_id)s"

# One round-trip; every aggregate is well-defined on empty tables.
SQL_DATASET_SUMMARY = """
SELECT (SELECT COUNT(*) FROM match)        AS matches,
       (SELECT COUNT(*) FROM participant)  AS participants,
       (SELECT MIN(start_time) FROM match) AS first_start,
       (SELECT MAX(start_time) FROM match) AS last_start,
       (SELECT split_part(game_version, '.', 1) || '.' || split_part(game_version, '.', 2)
          FROM match GROUP BY 1 ORDER BY COUNT(*) DESC, 1 LIMIT 1) AS patch,
       (SELECT array_agg(DISTINCT seed_tier) FROM match) AS tiers
"""

SQL_ITEM_TOTALS = """
SELECT (SELECT COUNT(*) FROM purchase_event WHERE item_id = %(item_id)s) AS completions,
       (SELECT COUNT(DISTINCT build_id) FROM build_item WHERE item_id = %(item_id)s) AS saved_builds
"""

SQL_ITEM_CORE_USE = """
SELECT pc.champion_id,
       pc.role,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins
FROM participant_core pc
WHERE pc.legendary_completions >= 3
  AND %(item_id)s IN (pc.item1, pc.item2, pc.item3)
GROUP BY pc.champion_id, pc.role
ORDER BY games DESC, pc.champion_id, pc.role
"""


def list_champions(conn) -> list[ChampionRef]:
    return [champion_from_row(r) for r in conn.execute(SQL_LIST_CHAMPIONS).fetchall()]


def get_champion(conn, champion_id: int) -> ChampionRef | None:
    row = conn.execute(SQL_GET_CHAMPION, {"champion_id": champion_id}).fetchone()
    return champion_from_row(row) if row else None


def list_items(conn, legendary_only: bool = False) -> list[ItemRef]:
    sql = SQL_LIST_LEGENDARY_ITEMS if legendary_only else SQL_LIST_ITEMS
    return [item_from_row(r) for r in conn.execute(sql).fetchall()]


def get_item(conn, item_id: int) -> ItemRef | None:
    row = conn.execute(SQL_GET_ITEM, {"item_id": item_id}).fetchone()
    return item_from_row(row) if row else None


def _tier_rank(tier: str) -> tuple[int, str]:
    head = tier.split()[0].upper() if tier else ""
    return (TIER_ORDER.index(head) if head in TIER_ORDER else len(TIER_ORDER), tier)


def dataset_summary(conn) -> DatasetSummary:
    row = conn.execute(SQL_DATASET_SUMMARY).fetchone()
    tiers = tuple(sorted(row["tiers"] or (), key=_tier_rank))
    return DatasetSummary(
        patch=row["patch"], matches=row["matches"], participants=row["participants"],
        tiers=tiers, first_start=row["first_start"], last_start=row["last_start"],
    )


def item_usage(conn, item: ItemRef) -> ItemUsage:
    """Which champions build the item in their first three, and how often it is completed."""
    params = {"item_id": item.item_id}
    totals = conn.execute(SQL_ITEM_TOTALS, params).fetchone()
    rows = conn.execute(SQL_ITEM_CORE_USE, params).fetchall()
    core_games = sum(r["games"] for r in rows)
    core_wins = sum(r["wins"] for r in rows)
    top = rows[:ITEM_TOP_CHAMPIONS]
    champions = load_champions(conn, (r["champion_id"] for r in top))
    uses = tuple(
        ItemChampionUse(champion=champions[r["champion_id"]], role=r["role"], games=r["games"],
                        wins=r["wins"], win_rate=rate(r["wins"], r["games"]),
                        sufficient=r["games"] >= MIN_GAMES)
        for r in top
    )
    return ItemUsage(item=item, completions=totals["completions"], core_games=core_games,
                     core_wins=core_wins, core_win_rate=rate(core_wins, core_games),
                     saved_builds=totals["saved_builds"], top_champions=uses)


def champions_by_id(conn, champion_ids) -> dict[int, ChampionRef]:
    """Batch lookup; ids that do not exist are simply absent from the result."""
    return load_champions(conn, champion_ids)
