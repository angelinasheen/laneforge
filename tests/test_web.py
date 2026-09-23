"""Flask routes against the real test database (test client, no server)."""
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from laneforge.queries import saved
from laneforge.queries.errors import ValidationError
from laneforge.web import ddragon
from laneforge.web.app import create_app
from laneforge.web.forms import parse_item_ids, parse_matchup, safe_next
from tests import factories as f
from tests.query_fixtures import (
    BANSHEES, DEATHCAP, GameMaker, LUDENS, MERCS, SHADOWFLAME, ZHONYAS, seed_catalogue,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "laneforge" / "web" / "templates"
needs_templates = pytest.mark.skipif(not (TEMPLATES / "base.html").exists(),
                                     reason="templates not ready")

AHRI, ZED, LUX = 103, 238, 99
CORE = [LUDENS, SHADOWFLAME, DEATHCAP]
MATCHUP = f"champion={AHRI}&role=MIDDLE&opponent={ZED}"


@pytest.fixture
def app(conn, test_dsn):
    return create_app({"TESTING": True, "DATABASE_URL": test_dsn, "SECRET_KEY": "test",
                       "TOLERATE_MISSING_TEMPLATES": True})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def data(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed", LUX: "Lux"})
    games = GameMaker(conn)
    games.games(AHRI, ZED, 35, CORE, wins=20,
                red_measures=dict(physical_damage=0, magic_damage=20000, true_damage=0))
    games.done()
    return conn


def _sign_in(client, name="angie"):
    return client.post("/signin", data={"display_name": name, "next": "/builds"})


def _flashes(client):
    with client.session_transaction() as sess:
        return [m for _, m in sess.get("_flashes", [])]


def _save(client, items=CORE, **extra):
    form = MultiDict([("champion", AHRI), ("role", "MIDDLE"), ("opponent", ZED),
                      ("enemy", LUX), ("enemy", ""), ("observed", "1"), ("name", "Anti-Zed")]
                     + [("item", i) for i in items] + list(extra.items()))
    return client.post("/builds", data=form, headers={"Referer": f"/matchup?{MATCHUP}"})


# --- pure helpers --------------------------------------------------------------

def test_filters_format_numbers_times_and_roles():
    assert ddragon.pct(0.542) == "54%" and ddragon.pct1(0.542) == "54.2%"
    assert ddragon.num(1840) == "1,840" and ddragon.gold(3000) == "3,000"
    assert ddragon.mmss(754_000) == "12:34" and ddragon.role_label("UTILITY") == "Support"
    assert ddragon.pct(None) == "—"


def test_image_urls_use_ddragon_version(monkeypatch):
    monkeypatch.delenv("DDRAGON_VERSION", raising=False)
    assert ddragon.champion_img("Ahri").endswith("/cdn/16.18.1/img/champion/Ahri.png")
    assert ddragon.item_img(3089).endswith("/cdn/16.18.1/img/item/3089.png")


def test_parse_matchup_accepts_labels_and_skips_blank_enemies():
    q = parse_matchup(MultiDict([("champion", "103"), ("role", "mid"), ("opponent", "238"),
                                 ("enemy", ""), ("enemy", "99")]))
    assert (q.champion_id, q.role, q.opponent_champion_id, q.enemy_ids) == (103, "MIDDLE", 238, (99,))
    assert q.query_string() == "champion=103&role=MIDDLE&opponent=238&enemy=99"


@pytest.mark.parametrize("args", [
    {"role": "MIDDLE", "opponent": "238"},
    {"champion": "x", "role": "MIDDLE", "opponent": "238"},
    {"champion": "103", "role": "ADC", "opponent": "238"},
    {"champion": "103", "role": "MIDDLE", "opponent": "103"},
    {"champion": "-4", "role": "MIDDLE", "opponent": "238"},
    {"champion": "103", "role": "MIDDLE", "opponent": "238", "enemy": ["238"]},
    {"champion": "103", "role": "MIDDLE", "opponent": "238", "enemy": ["1", "1"]},
    {"champion": "103", "role": "MIDDLE", "opponent": "238", "enemy": ["1", "2", "3", "4", "5"]},
])
def test_parse_matchup_rejects_bad_input(args):
    with pytest.raises(ValidationError):
        parse_matchup(MultiDict([(k, x) for k, v in args.items()
                                 for x in (v if isinstance(v, list) else [v])]))


def test_parse_item_ids_and_safe_next():
    assert parse_item_ids(MultiDict([("item1", "1"), ("item2", "2"), ("item3", "3")])) == (1, 2, 3)
    assert safe_next("//evil.com") == "/" and safe_next("/builds") == "/builds"
    assert safe_next("https://evil.com/x", "/builds") == "/builds"


# --- pages ---------------------------------------------------------------------

@needs_templates
def test_index_on_empty_database(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"No matches are loaded yet" in response.data


@needs_templates
def test_index_lists_champions(client, data):
    response = client.get("/")
    assert response.status_code == 200 and b"Ahri" in response.data


@needs_templates
def test_matchup_page_shows_ladder_and_situational(client, data):
    response = client.get(f"/matchup?{MATCHUP}&enemy={LUX}")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Zed" in body and "Luden" in body and "Mercury" in body


@needs_templates
@pytest.mark.parametrize("path", ["/matchup/core", "/matchup/situational"])
def test_matchup_partials(client, data, path):
    response = client.get(f"{path}?{MATCHUP}")
    assert response.status_code == 200
    assert b"<html" not in response.data


@pytest.mark.parametrize("query", [
    "champion=103&role=ADC&opponent=238",
    "champion=103&role=MIDDLE",
    "champion=103&role=MIDDLE&opponent=103",
    "champion=424242&role=MIDDLE&opponent=238",
    f"{MATCHUP}&enemy=238",
    f"{MATCHUP}&enemy=900&enemy=901&enemy=902&enemy=903&enemy=904",
    "champion=abc&role=MIDDLE&opponent=238",
])
@pytest.mark.parametrize("path", ["/matchup", "/matchup/core", "/matchup/situational"])
def test_matchup_bad_input_is_400(client, data, path, query):
    assert client.get(f"{path}?{query}").status_code == 400


@needs_templates
def test_bad_input_page_explains(client, data):
    response = client.get("/matchup?champion=103&role=ADC&opponent=238")
    assert b"Pick a role" in response.data


@needs_templates
def test_catalogue_pages(client, data):
    assert b"Ahri" in client.get("/champions").data
    champion = client.get(f"/champions/{AHRI}")
    assert champion.status_code == 200 and b"Zed" in champion.data
    assert b"Shadowflame" in client.get("/items").data
    item = client.get(f"/items/{SHADOWFLAME}")
    assert item.status_code == 200 and b"Ahri" in item.data


@needs_templates
def test_match_pages(client, data):
    listing = client.get("/matches")
    assert listing.status_code == 200 and b"NA1_1" in listing.data
    match = client.get("/matches/NA1_1")
    assert match.status_code == 200 and b"Luden" in match.data


@pytest.mark.parametrize("path", ["/champions/5", "/champions/abc", "/items/1",
                                  "/matches/NA1_nope", "/matches/" + "x" * 40, "/nowhere"])
def test_unknown_ids_are_404(client, data, path):
    assert client.get(path).status_code == 404


@needs_templates
def test_404_page_uses_site_template(client, data):
    response = client.get("/champions/5")
    assert b"no champion with id 5" in response.data


def test_matches_tier_filter_validation(client, data):
    assert client.get("/matches?tier=gold").status_code == 200
    assert client.get("/matches?tier=" + "X" * 30).status_code == 400


# --- account and saved builds ----------------------------------------------------

def test_sign_in_and_out(client, data):
    response = _sign_in(client)
    assert response.status_code == 302 and response.headers["Location"] == "/builds"
    with client.session_transaction() as sess:
        assert set(sess) - {"_flashes"} == {"user_id"}
    assert client.post("/signout").status_code == 302
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_sign_in_rejects_short_name(client, data):
    response = client.post("/signin", data={"display_name": " a "}, headers={"Referer": "/signin"})
    assert response.status_code == 302 and response.headers["Location"] == "/signin"
    assert any("Display name" in m for m in _flashes(client))


@needs_templates
def test_signin_page_renders(client, data):
    assert client.get("/signin").status_code == 200


def test_builds_require_sign_in(client, data):
    assert client.get("/builds").headers["Location"].startswith("/signin")
    assert _save(client).headers["Location"].startswith("/signin")


@needs_templates
def test_save_view_edit_rename_delete_flow(client, data, conn):
    _sign_in(client)
    created = _save(client)
    assert created.status_code == 302
    build_id = int(created.headers["Location"].rsplit("/", 1)[1])
    assert saved.get_build(conn, build_id).is_customized is False

    assert b"Anti-Zed" in client.get("/builds").data
    page = client.get(f"/builds/{build_id}")
    assert page.status_code == 200 and b"Anti-Zed" in page.data

    swap = client.post(f"/builds/{build_id}/items",
                       data=MultiDict([("item", DEATHCAP), ("item", LUDENS), ("item", ZHONYAS)]))
    assert swap.status_code == 302
    build = saved.get_build(conn, build_id)
    assert [i.item_id for i in build.items] == [DEATHCAP, LUDENS, ZHONYAS] and build.is_customized

    client.post(f"/builds/{build_id}/rename", data={"name": "Renamed", "notes": "n"})
    assert saved.get_build(conn, build_id).name == "Renamed"

    client.post(f"/builds/{build_id}/delete", data={})
    assert saved.get_build(conn, build_id) is not None      # no confirmation box
    client.post(f"/builds/{build_id}/delete", data={"confirm": "1"})
    assert saved.get_build(conn, build_id) is None


@pytest.mark.parametrize("items", [[LUDENS, SHADOWFLAME], [LUDENS, LUDENS, DEATHCAP],
                                   [LUDENS, MERCS, DEATHCAP], [LUDENS, BANSHEES, 1]])
def test_save_bad_items_flashes_and_redirects_back(client, data, conn, items):
    _sign_in(client)
    response = _save(client, items=items)
    assert response.status_code == 302
    assert response.headers["Location"] == f"/matchup?{MATCHUP}"
    assert _flashes(client)
    assert conn.execute("SELECT COUNT(*) AS n FROM saved_build").fetchone()["n"] == 0


def test_bad_edit_flashes(client, data, conn):
    _sign_in(client)
    build_id = int(_save(client).headers["Location"].rsplit("/", 1)[1])
    response = client.post(f"/builds/{build_id}/items", data={"item": [LUDENS, MERCS, DEATHCAP]},
                           headers={"Referer": f"/builds/{build_id}"})
    assert response.headers["Location"] == f"/builds/{build_id}"
    assert "not a legendary" in _flashes(client)[-1]
    response = client.post(f"/builds/{build_id}/rename", data={"name": " "})
    assert response.status_code == 302


def test_other_users_build_is_404(client, data):
    _sign_in(client, "owner")
    build_id = int(_save(client).headers["Location"].rsplit("/", 1)[1])
    client.post("/signout")
    _sign_in(client, "intruder")
    assert client.get(f"/builds/{build_id}").status_code == 404
    assert client.post(f"/builds/{build_id}/delete", data={"confirm": "1"}).status_code == 404
    assert client.get("/builds/99999").status_code == 404


def test_stale_session_user_is_signed_out(client, data, conn):
    user_id = f.user(conn, "ghost")
    conn.commit()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id + 1000
    assert client.get("/builds").headers["Location"].startswith("/signin")


def test_missing_template_is_an_error_outside_tests(test_dsn, conn):
    app = create_app({"DATABASE_URL": test_dsn, "SECRET_KEY": "x",
                      "TOLERATE_MISSING_TEMPLATES": True})
    assert app.config["TOLERATE_MISSING_TEMPLATES"] is False
