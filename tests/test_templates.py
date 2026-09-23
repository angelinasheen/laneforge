"""Template tests: render every template through the preview harness's fake
data, and guard the template <-> route contract (docs/CONTRACT.md, Web layer).

No database is needed; the preview harness builds dataclasses directly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from jinja2 import meta, nodes
from jinja2.defaults import DEFAULT_FILTERS

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "laneforge" / "web" / "templates"


def _load_preview():
    spec = importlib.util.spec_from_file_location("ui_preview", ROOT / "scripts" / "ui_preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preview = _load_preview()

# --- the contract, transcribed from docs/CONTRACT.md -------------------------

CONTRACT_CONTEXT: dict[str, set[str]] = {
    "base.html": {"current_user", "dataset"},
    "index.html": {"champions", "roles", "dataset", "form_error"},
    "matchup.html": {"champion", "role", "opponent", "enemies", "ladder", "situational", "query_string"},
    "partials/core.html": {"ladder", "champion", "role", "opponent"},
    "partials/situational.html": {"situational", "champion", "role", "opponent", "enemies"},
    "champions.html": {"champions"},
    "champion.html": {"champion", "overview"},
    "items.html": {"items"},
    "item.html": {"item", "usage"},
    "matches.html": {"matches"},
    "match.html": {"match"},
    "signin.html": set(),
    "builds.html": {"builds"},
    "build.html": {"build", "legendary_items"},
    "errors/404.html": {"message"},
    "errors/400.html": {"message"},
}
# UI-internal includes/imports (not rendered by routes directly).
INTERNAL_CONTEXT: dict[str, set[str]] = {
    "_macros.html": set(),
    "partials/answered.html": {"ladder", "opponent", "oob"},   # oob is set by partials/core.html
}
# Available in every template: Flask defaults, the base context
# (current_user/dataset via context processor), and the CONTRACT Jinja globals.
EVERYWHERE = {"request", "url_for", "get_flashed_messages", "config", "session", "g",
              "current_user", "dataset", "champion_img", "item_img", "ddragon_version"}
CONTRACT_GLOBALS = {"champion_img", "item_img", "ddragon_version"}
CONTRACT_FILTERS = {"pct", "pct1", "num", "gold", "mmss", "role_label"}


@pytest.fixture(scope="module")
def app():
    flask_app = preview.create_app()
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _get(client, name: str, **kwargs):
    _, _, _, qs = preview.PAGES[name]
    return client.get(f"/preview/{name}" + (f"?{qs}" if qs else ""), **kwargs)


def _locally_bound(tree: nodes.Template) -> set[str]:
    """Names a template binds itself ({% import %} targets, {% set %} / loop targets).
    meta.find_undeclared_variables reports some of these (e.g. a top-level import
    in a child template) even though they never come from the route."""
    bound = {n.target for n in tree.find_all(nodes.Import)}
    for node in tree.find_all((nodes.Assign, nodes.For)):
        bound |= {n.name for n in node.target.find_all(nodes.Name)}
        if isinstance(node.target, nodes.Name):
            bound.add(node.target.name)
    return bound


def _all_template_names() -> list[str]:
    return sorted(str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.html"))


# --- rendering ------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(preview.PAGES))
def test_every_preview_page_renders_with_expected_status(client, name):
    # Arrange
    expected = int(name) if name.isdigit() else 200
    # Act
    response = _get(client, name)
    # Assert
    assert response.status_code == expected
    assert b"LaneForge" in response.data or name in {"core", "situational"}


def test_every_contract_template_has_a_preview_page():
    previewed = {template for template, _, _, _ in preview.PAGES.values()}
    assert set(CONTRACT_CONTEXT) <= previewed


def test_matchup_page_states_answer_level_and_insufficient_rows(client):
    html = _get(client, "matchup").get_data(as_text=True)
    assert "answered at level 1" in html
    assert 'aria-live="polite"' in html
    assert "212</span> games" in html
    assert "insufficient data" in html
    assert html.count('class="ci"') == 4 + 2      # four sufficient core rows + two evidence lines
    assert "Ahri <span class=\"sep\">·</span> Mid" in html


def test_save_form_carries_matchup_and_items_as_hidden_fields(client):
    html = _get(client, "matchup").get_data(as_text=True)
    assert 'action="/builds"' in html
    for field in ('name="champion" value="103"', 'name="role" value="MIDDLE"', 'name="opponent" value="238"',
                  'name="enemy" value="64"', 'name="item" value="6655"', 'name="observed" value="1"'):
        assert field in html


def test_signed_out_save_links_to_signin_with_next(client):
    html = client.get("/preview/matchup?anon=1&" + preview.MATCHUP_QS).get_data(as_text=True)
    assert "to save any of these." in html
    assert html.count("/signin?next=/matchup%3F") == 1       # one footnote, not one link per row
    assert "champion%3D103" in html
    assert 'action="/builds"' not in html
    assert "Save this build" not in html


def test_fallback_page_says_it_fell_back_and_comp_is_partial(client):
    html = _get(client, "fallback").get_data(as_text=True)
    assert "answered at level 3" in html
    assert "not enough Ahri vs Zed games; showing Ahri, mid, all opponents" in html
    assert "comp profile is partial" in html


def test_per_item_level_uses_share_label_and_single_icons(client):
    html = _get(client, "fallback-item").get_data(as_text=True)
    assert "In first three" in html
    assert "Save this build" not in html          # a single item cannot be saved as a build


def test_situational_block_shows_score_evidence_and_stat_model_only(client):
    html = _get(client, "situational").get_data(as_text=True)
    assert "stat model only, no sample" in html
    assert "In <span class=\"num\">236</span> games where Ahri faced" in html
    assert "above 75th pct" in html
    assert "+1,412 effective HP per 1,000 gold" in html
    assert "75th percentile of comps: <span class=\"num\">842/min</span>" in html
    assert "The comp is 74% physical damage." in html and "The The" not in html


def test_htmx_shell_requests_both_partials_with_same_query(client):
    html = _get(client, "matchup-htmx").get_data(as_text=True)
    assert f'hx-get="/matchup/core?{preview.MATCHUP_QS.replace("&", "&amp;")}"' in html
    assert 'hx-get="/matchup/situational?' in html
    assert "…compiling" in html


def test_core_partial_swaps_answered_line_out_of_band_only_for_htmx(client):
    url = "/matchup/core?" + preview.MATCHUP_QS
    with_htmx = client.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
    plain = client.get(url).get_data(as_text=True)
    assert 'hx-swap-oob="innerHTML"' in with_htmx
    assert "hx-swap-oob" not in plain


def test_saved_builds_are_tagged_observed_or_customized(client):
    html = _get(client, "builds").get_data(as_text=True)
    assert "CUSTOMIZED" in html and "OBSERVED" in html


def test_build_page_has_edit_rename_and_checkbox_confirmed_delete(client):
    html = _get(client, "build").get_data(as_text=True)
    assert 'action="/builds/9/items"' in html and html.count('name="item"') == 3
    assert 'action="/builds/9/rename"' in html
    assert 'action="/builds/9/delete"' in html and "I mean it" in html and "required" in html
    assert "confirm(" not in html
    assert "Items saved." in html                 # flash rendered as the notice line


def test_match_page_marks_winner_and_uses_minute_marks(client):
    html = _get(client, "match").get_data(as_text=True)
    assert "Blue side won" in html
    assert "13:58" in html                         # Ahri's Luden's completion
    assert html.count('class="player"') == 10


def test_empty_dataset_index_explains_how_to_load(client):
    html = _get(client, "index-empty").get_data(as_text=True)
    assert "No matches are loaded yet" in html
    assert "Compile dossier" not in html


def test_footer_names_data_source_and_patch(client):
    html = _get(client, "index").get_data(as_text=True)
    assert "data: Riot Games match-v5, patch 16.18" in html


def test_unknown_page_renders_site_404(client):
    response = client.get("/preview/no-such-page")
    assert response.status_code == 404
    assert b"No such file." in response.data


# --- contract drift guards -------------------------------------------------------

def test_template_set_matches_contract_plus_internal():
    assert set(_all_template_names()) == set(CONTRACT_CONTEXT) | set(INTERNAL_CONTEXT)


@pytest.mark.parametrize("template", _all_template_names())
def test_template_uses_only_contract_context_variables(app, template):
    # Arrange
    source = (TEMPLATES / template).read_text()
    allowed = CONTRACT_CONTEXT.get(template, INTERNAL_CONTEXT.get(template, set())) | EVERYWHERE
    # Act
    tree = app.jinja_env.parse(source)
    used = meta.find_undeclared_variables(tree) - _locally_bound(tree)
    # Assert
    assert used - allowed == set(), f"{template} uses variables outside CONTRACT: {sorted(used - allowed)}"


@pytest.mark.parametrize("template", _all_template_names())
def test_template_uses_only_known_filters(app, template):
    tree = app.jinja_env.parse((TEMPLATES / template).read_text())
    used = {f.name for f in tree.find_all(nodes.Filter)}
    unknown = used - set(DEFAULT_FILTERS) - CONTRACT_FILTERS
    assert unknown == set(), f"{template} uses filters not in CONTRACT: {sorted(unknown)}"


def test_preview_registers_exactly_the_contract_helpers():
    assert set(preview.GLOBALS) == CONTRACT_GLOBALS
    assert set(preview.FILTERS) == CONTRACT_FILTERS


def test_preview_filters_format_like_the_design():
    assert preview.f_pct1(0.5690) == "56.9%"
    assert preview.f_pct(0.61) == "61%"
    assert preview.f_num(1840) == "1,840"
    assert preview.f_mmss(838_000) == "13:58"
    assert preview.f_role_label("MIDDLE") == "Mid"


# --- critique fixes: trust line, thin samples, rule notes, form errors ---------------

def test_unanswered_ladder_never_claims_an_answer_level(client):
    html = _get(client, "matchup-unanswered").get_data(as_text=True)
    assert "answered at level" not in html
    assert "no level reached 30 games" in html
    assert 'aria-current="step"' not in html
    assert "not enough games at any level; showing per-item shares" in html


def test_empty_ladder_says_plainly_there_are_no_games(client):
    html = _get(client, "matchup-empty").get_data(as_text=True)
    assert "No Ahri games at Bot in this dataset." in html
    assert "answered at level" not in html
    assert 'class="ladder"' not in html
    assert "There is nothing to rank" not in html


def test_thin_sample_line_and_rule_notes_replace_repeated_no_sample_lines(client):
    html = _get(client, "matchup-unanswered").get_data(as_text=True)
    assert "Ahri has fewer than 30 games at Support here, so these are stat-model picks without evidence." in html
    assert "stat model only, no sample" not in html          # said once, not per row
    assert html.count('class="suggestions compact"') == 3


def test_rule_note_compacts_only_its_own_rule(client):
    html = _get(client, "situational").get_data(as_text=True)
    assert html.count('class="rule-note"') == 1
    assert "+18% damage to Zed" in html
    assert "stat model only, no sample" in html          # the armor rule has no note


def test_evidence_sentence_reads_with_interval_percent(client):
    html = _get(client, "situational").get_data(as_text=True)
    assert "players who completed Zhonya&#39;s Hourglass won <span class=\"num\">55.5%</span>" in html
    assert "(49–62%)" in html


def test_core_interval_labels_use_en_dash_and_percent(client):
    html = _get(client, "matchup").get_data(as_text=True)
    assert "44–69%" in html


def test_signed_in_save_form_is_an_inline_row_with_notes(client):
    html = _get(client, "matchup").get_data(as_text=True)
    assert html.count('class="save-row"') == 5
    assert 'name="notes"' in html and 'maxlength="2000"' in html
    assert 'name="name" required minlength="1" maxlength="60"' in html


def test_form_error_renders_under_sentence_with_prefilled_selects(client):
    html = _get(client, "index-error").get_data(as_text=True)
    assert "Not compiled:</strong> Pick two different champions" in html
    assert '<option value="103" selected>Ahri</option>' in html
    assert "and they also have" in html and ">anyone</option>" in html


def test_index_without_form_error_has_no_error_line(client):
    html = _get(client, "index").get_data(as_text=True)
    assert "Not compiled" not in html


def test_matchup_kicker_links_back_to_prefilled_form(client):
    html = _get(client, "matchup").get_data(as_text=True)
    assert 'href="/?champion=103&amp;role=MIDDLE' in html and "change matchup" in html
    assert "103-MIDDLE-238" not in html


def test_insufficient_rows_on_champion_and_item_pages(client):
    champ_html = _get(client, "champion").get_data(as_text=True)
    item_html = _get(client, "item").get_data(as_text=True)
    assert "insufficient data" in champ_html and ">1–1</span>" in champ_html
    assert "insufficient data" in item_html and ">1–0</span>" in item_html


def test_observed_build_shows_its_matchup_row(client):
    html = _get(client, "build-observed").get_data(as_text=True)
    assert "Observed in <span class=\"num\">58</span> of <span class=\"num\">212</span> games against Zed" in html
    assert "(44–69%)" in html
    assert "Save name &amp; notes" in html


def test_error_flash_renders_beside_swap_form_not_on_top(client):
    html = _get(client, "build-error").get_data(as_text=True)
    assert 'id="swap-error"' in html
    assert "Not saved:</strong> A build needs three different legendary items." in html
    assert html.count("A build needs three different legendary items.") == 1


def test_items_catalogue_names_kind_in_words(client):
    html = _get(client, "items").get_data(as_text=True)
    assert '<span class="kind">legendary</span>' in html and '<span class="kind">boots</span>' in html
    assert ">L</abbr>" not in html


def test_match_headline_names_winner_and_length(client):
    html = _get(client, "match").get_data(as_text=True)
    assert "Blue side won in" in html and "33:41" in html
    assert "legendaries have a red rule under them" in html


def test_signin_page_hides_nav_signin_link(client):
    html = client.get("/signin?anon=1").get_data(as_text=True)       # the real path, so request.path matches
    assert "/signin?next=" not in html
