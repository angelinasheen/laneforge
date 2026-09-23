"""Read-only pages: matchup form and answer, catalogue, matches."""
from __future__ import annotations

from flask import Blueprint, request

from laneforge.queries import browse, catalog
from laneforge.queries.builds import core_builds
from laneforge.queries.errors import ValidationError
from laneforge.queries.models import ROLES
from laneforge.queries.situational import situational_items
from laneforge.web.blueprints import dataset, get_conn, not_found, render
from laneforge.web.forms import MatchupQuery, parse_matchup

bp = Blueprint("pages", __name__)

MATCH_ID_MAX = 20
TIER_MAX = 12
MATCHES_PER_PAGE = 50


def _resolve_matchup(conn):
    """Validated query plus the champion refs it names (400 on unknown ids)."""
    query: MatchupQuery = parse_matchup(request.args)
    ids = (query.champion_id, query.opponent_champion_id, *query.enemy_ids)
    refs = catalog.champions_by_id(conn, ids)
    missing = [i for i in ids if i not in refs]
    if missing:
        raise ValidationError(f"There is no champion with id {missing[0]}.")
    return (query, refs[query.champion_id], refs[query.opponent_champion_id],
            tuple(refs[i] for i in query.enemy_ids))


def _ladder(conn, query: MatchupQuery):
    return core_builds(conn, query.champion_id, query.role, query.opponent_champion_id)


def _situational(conn, query: MatchupQuery):
    return situational_items(conn, query.champion_id, query.role,
                             query.opponent_champion_id, query.enemy_ids)


@bp.get("/")
def index():
    conn = get_conn()
    return render("index.html", champions=catalog.list_champions(conn), roles=ROLES,
                  dataset=dataset())


@bp.get("/matchup")
def matchup():
    conn = get_conn()
    query, champion, opponent, enemies = _resolve_matchup(conn)
    return render("matchup.html", champion=champion, role=query.role, opponent=opponent,
                  enemies=enemies, ladder=_ladder(conn, query),
                  situational=_situational(conn, query), query_string=query.query_string())


@bp.get("/matchup/core")
def matchup_core():
    conn = get_conn()
    query, champion, opponent, enemies = _resolve_matchup(conn)
    return render("partials/core.html", ladder=_ladder(conn, query), champion=champion,
                  role=query.role, opponent=opponent, enemies=enemies)


@bp.get("/matchup/situational")
def matchup_situational():
    conn = get_conn()
    query, champion, opponent, enemies = _resolve_matchup(conn)
    return render("partials/situational.html", situational=_situational(conn, query),
                  champion=champion, role=query.role, opponent=opponent, enemies=enemies)


@bp.get("/champions")
def champions():
    return render("champions.html", champions=catalog.list_champions(get_conn()))


@bp.get("/champions/<int:champion_id>")
def champion(champion_id: int):
    conn = get_conn()
    ref = catalog.get_champion(conn, champion_id)
    if ref is None:
        not_found(f"There is no champion with id {champion_id}.")
    return render("champion.html", champion=ref,
                  overview=browse.champion_overview(conn, champion_id))


@bp.get("/items")
def items():
    return render("items.html", items=catalog.list_items(get_conn()))


@bp.get("/items/<int:item_id>")
def item(item_id: int):
    conn = get_conn()
    ref = catalog.get_item(conn, item_id)
    if ref is None:
        not_found(f"There is no item with id {item_id}.")
    usage = catalog.item_usage(conn, ref)
    return render("item.html", item=ref, usage=usage.top_champions)


@bp.get("/matches")
def matches():
    tier = (request.args.get("tier") or "").strip().upper() or None
    if tier is not None and len(tier) > TIER_MAX:
        raise ValidationError("That is not a tier.")
    return render("matches.html",
                  matches=browse.recent_matches(get_conn(), MATCHES_PER_PAGE, tier))


@bp.get("/matches/<match_id>")
def match(match_id: str):
    detail = browse.get_match(get_conn(), match_id) if len(match_id) <= MATCH_ID_MAX else None
    if detail is None:
        not_found(f"There is no loaded match {match_id}.")
    return render("match.html", match=detail)
