"""Request-argument parsing. Everything a user sends passes through here and
comes out as typed, validated values or a ValidationError."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from laneforge.queries.errors import ValidationError
from laneforge.queries.models import ROLE_LABELS, ROLES
from laneforge.queries.validate import (
    MAX_ENEMIES, require_distinct_matchup, require_enemies, require_role,
)

CHAMPION_MESSAGE = "Pick a champion from the list."
OPPONENT_MESSAGE = "Pick a lane opponent from the list."
ENEMY_MESSAGE = "Pick each enemy champion from the list."

LABEL_TO_ROLE = {label.upper(): role for role, label in ROLE_LABELS.items()}
MAX_ID = 2**31 - 1


@dataclass(frozen=True)
class MatchupQuery:
    champion_id: int
    role: str
    opponent_champion_id: int
    enemy_ids: tuple[int, ...]

    def query_string(self) -> str:
        pairs = [("champion", self.champion_id), ("role", self.role),
                 ("opponent", self.opponent_champion_id)]
        pairs += [("enemy", e) for e in self.enemy_ids]
        return urlencode(pairs)


def parse_int(value, label: str) -> int:
    text = (value or "").strip() if isinstance(value, str) else value
    if text in (None, ""):
        raise ValidationError(f"Choose a {label}.")
    try:
        number = int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"{label.capitalize()} must be a number.") from None
    if not 0 < number <= MAX_ID:
        raise ValidationError(f"{label.capitalize()} {number} is out of range.")
    return number


def parse_choice(value, message: str) -> int:
    """A select-list id; any malformed value gets the one human message."""
    try:
        return parse_int(value, "choice")
    except ValidationError:
        raise ValidationError(message) from None


def parse_role(value) -> str:
    text = (value or "").strip().upper()
    return require_role(LABEL_TO_ROLE.get(text, text) if text not in ROLES else text)


def parse_enemies(values) -> tuple[int, ...]:
    """Blank selects are ignored; the rest must be champion ids. The count is
    checked before any value is parsed so a flood of repeated fields costs nothing."""
    present = [v for v in values if (v or "").strip()]
    if len(present) > MAX_ENEMIES:
        raise ValidationError(f"Name at most {MAX_ENEMIES} other enemy champions.")
    return tuple(parse_choice(v, ENEMY_MESSAGE) for v in present)


def parse_matchup(args) -> MatchupQuery:
    champion_id = parse_choice(args.get("champion"), CHAMPION_MESSAGE)
    role = parse_role(args.get("role"))
    opponent_id = parse_choice(args.get("opponent"), OPPONENT_MESSAGE)
    require_distinct_matchup(champion_id, opponent_id)
    enemies = require_enemies(champion_id, opponent_id, parse_enemies(args.getlist("enemy")))
    return MatchupQuery(champion_id, role, opponent_id, enemies)


def parse_item_ids(form) -> tuple[int, ...]:
    """Accepts repeated `item` fields (in order) or `item1`..`item3`."""
    values = form.getlist("item") or [form.get(f"item{n}") for n in (1, 2, 3)]
    return tuple(parse_int(v, "item") for v in values if (v or "").strip())


def parse_flag(value) -> bool:
    return (value or "").strip().lower() in ("1", "true", "on", "yes")


def safe_next(value, default: str = "/") -> str:
    """Only same-site relative paths are followed after sign-in."""
    target = (value or "").strip()
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default
