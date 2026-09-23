"""Data Dragon image URLs and display filters, registered on the Jinja env."""
from __future__ import annotations

import os

from laneforge.queries.models import ROLE_LABELS

DEFAULT_DDRAGON_VERSION = "16.18.1"
CDN = "https://ddragon.leagueoflegends.com/cdn"
MISSING = "—"


def ddragon_version() -> str:
    return os.environ.get("DDRAGON_VERSION", DEFAULT_DDRAGON_VERSION)


def champion_img(champion) -> str:
    key = getattr(champion, "ddragon_key", champion)
    return f"{CDN}/{ddragon_version()}/img/champion/{key}.png"


def item_img(item) -> str:
    item_id = getattr(item, "item_id", item)
    return f"{CDN}/{ddragon_version()}/img/item/{item_id}.png"


def pct(value) -> str:
    """0.542 -> '54%'."""
    return MISSING if value is None else f"{value * 100:.0f}%"


def pct1(value) -> str:
    """0.542 -> '54.2%'."""
    return MISSING if value is None else f"{value * 100:.1f}%"


def num(value) -> str:
    """1840 -> '1,840' (rounded to a whole number)."""
    return MISSING if value is None else f"{round(value):,}"


def gold(value) -> str:
    """3000 -> '3,000' (the template adds the unit)."""
    return MISSING if value is None else f"{int(value):,}"


def mmss(game_time_ms) -> str:
    """754000 -> '12:34'."""
    if game_time_ms is None:
        return MISSING
    seconds = int(game_time_ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def role_label(role) -> str:
    return ROLE_LABELS.get(role or "", role or MISSING)


FILTERS = {"pct": pct, "pct1": pct1, "num": num, "gold": gold, "mmss": mmss,
           "role_label": role_label}


def register(app) -> None:
    app.jinja_env.filters.update(FILTERS)
    app.jinja_env.globals.update(champion_img=champion_img, item_img=item_img,
                                 ddragon_version=ddragon_version(), role_label=role_label)
