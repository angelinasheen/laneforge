"""Input checks shared by the query modules and the web forms. Pure, no DB."""
from __future__ import annotations

from typing import Iterable

from laneforge.queries.errors import ValidationError
from laneforge.queries.models import ROLES

MAX_ENEMIES = 4


def require_role(role: str | None) -> str:
    if role not in ROLES:
        raise ValidationError("Pick a role: Top, Jungle, Mid, Bot or Support.")
    return role


def require_distinct_matchup(champion_id: int, opponent_id: int) -> None:
    if champion_id == opponent_id:
        raise ValidationError("Your champion and your lane opponent must be different champions.")


def require_enemies(champion_id: int, opponent_id: int,
                    enemy_ids: Iterable[int]) -> tuple[int, ...]:
    """0..4 distinct enemies, none equal to the champion or the lane opponent."""
    enemies = tuple(enemy_ids)
    if len(enemies) > MAX_ENEMIES:
        raise ValidationError(f"Name at most {MAX_ENEMIES} other enemy champions.")
    if len(set(enemies)) != len(enemies):
        raise ValidationError("Each enemy champion can be listed only once.")
    if champion_id in enemies:
        raise ValidationError("Your own champion cannot also be an enemy.")
    if opponent_id in enemies:
        raise ValidationError("The lane opponent is already on the enemy team; "
                              "list only the other four enemies.")
    return enemies
