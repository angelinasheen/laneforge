# LaneForge implementation contract

This is the agreement every module is built against. The product spec is
`../project1-part1-draft4.md` (read it first; the "User Interaction Plan" and
"Derived, not stored" sections are the ground truth for behaviour). The schema
is `sql/schema.sql`; the derived views are `sql/views.sql`. Neither is to be
changed by an implementor without telling the manager, because three other
agents are coding against them.

## Non-negotiable rules (from the course)

- Python 3.11, Flask, psycopg 3. **Raw SQL only. No ORM, no query builder.**
- Every SQL statement uses psycopg named parameters: `%(name)s`. Never f-string
  user input into SQL. Table and column names are constants in code, never input.
- Users never type SQL. All input arrives through forms and is validated.
- The app must not crash on bad input: unknown champion id, missing role,
  duplicate enemies, item that is not legendary, and so on all produce a
  friendly message and a 400 or a redirect with a flash, never a 500.
- DDL stays Postgres-10 compatible (the class server may be old).

## Repository layout and ownership

```
sql/                 schema.sql, views.sql, refresh.sql, reset.sql       (manager; db agent may add)
laneforge/db.py      connect(), rebuild_schema(), refresh_views()        (manager)
laneforge/queries/   pure query modules, one per concern                 (query agent)
laneforge/web/       Flask app factory, blueprints, templates, static    (routes: query agent; templates+static: ui agent)
laneforge/ingest/    Data Dragon loader, Riot crawler, timeline replay   (ingest agent)
scripts/             db.sh (reset/load/refresh), seed_synthetic.py       (db agent)
tests/               pytest; conftest.py + factories.py are shared       (each agent writes its own test files)
data/ddragon/        champion.json, item.json, VERSION for patch 16.18.1 (already downloaded)
data/raw/            gzipped raw Riot responses (git-ignored)
docs/                this file, DESIGN.md
```

Run tests with `.venv/bin/pytest`. The `conn` fixture gives a connection to
`laneforge_test` with the full schema and empty tables. `tests/factories.py`
inserts champions, items, matches with ten participants, and purchase events.
Call `laneforge.db.refresh_views(conn)` after inserting match data and before
querying any materialized view.

## Domain vocabulary

- **Role**: one of `TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY`. Display labels:
  Top, Jungle, Mid, Bot, Support.
- **Legendary item**: `is_legendary AND NOT is_boots`. Only these count toward
  a core build and only these may be saved in a build.
- **Core build** of a participant: `participant_core.item1..item3`, the first
  three legendary completions by game time. `legendary_completions >= 3` means
  the participant has a full core; that is the denominator for pick rate.
- **Matchup**: (champion_id, role, opponent_champion_id).
- **Sufficient data**: 30 games. Below that, win rate is shown greyed with the
  label "insufficient data" and never used for ordering.
- **Comp profile**: magic_share, physical_share, healing_pm, cc_pm. For a
  request, the lane opponent uses `champion_effective_profile` at the user's
  role; the other enemies (roles unknown) use the champion's all-role mean
  (`champion_profile`), falling back to NULL contributions when a champion has
  no games. Fewer than four other enemies => `partial = True` and the page says
  the comp profile is partial.

## Query layer (`laneforge/queries/`) — signatures the routes and UI consume

All functions take a psycopg connection first. Return plain dataclasses
(frozen) so templates and JSON can both use them. Money and percentages are
returned as numbers; formatting is the UI's job.

```python
# queries/catalog.py
list_champions(conn) -> list[ChampionRef]            # id, name, ddragon_key, sorted by name
get_champion(conn, champion_id) -> ChampionRef | None
list_items(conn, legendary_only=False) -> list[ItemRef]  # id, name, gold_cost, is_legendary, is_boots + stat line
get_item(conn, item_id) -> ItemRef | None
dataset_summary(conn) -> DatasetSummary  # patch (from match.game_version), match count, participant count, tiers, first/last start_time

# queries/builds.py  (the four-level ladder, draft 4 "Core builds")
core_builds(conn, champion_id, role, opponent_champion_id) -> LadderAnswer
#   LadderAnswer: level (1..4), scope ('matchup'|'champion'), granularity ('sequence'|'item'),
#                 sample_size (participants with a full core at that scope),
#                 label (e.g. 'vs Zed, mid: 212 games' / 'all opponents: 1,840 games'),
#                 rows: list[BuildRow] (max 5, ordered by pick rate desc, then games desc)
#   BuildRow: items: tuple[ItemRef, ...] (3 for sequence rows, 1 for item rows),
#             games, wins, pick_rate, win_rate, ci_low, ci_high, sufficient (games >= 30)
#   Ladder rule: answer at the first level whose TOP row has >= 30 games. If no level
#   qualifies, return level 4 with whatever rows exist (possibly none) and sufficient=False.
#   Level 2/4 "per item": for each legendary item, share of full-core participants at that
#   scope whose item1/item2/item3 includes it. pick_rate = games / sample_size.
# queries/stats.py
wilson_interval(wins, games, z=1.96) -> tuple[float, float]   # (0.0, 0.0) when games == 0

# queries/situational.py  (draft 4 "Situational items")
comp_profile(conn, role, opponent_champion_id, enemy_champion_ids) -> CompProfile
#   magic_share, physical_share, healing_pm, cc_pm, healing_pm_p75, cc_pm_p75, partial, members (per champion contribution)
situational_items(conn, champion_id, role, opponent_champion_id, enemy_champion_ids) -> SituationalAnswer
#   profile: CompProfile
#   triggered: list[Rule]  (rule key, human reason e.g. "comp is 61% magic")
#   suggestions: list[Suggestion] ordered by score desc within rule
#   Suggestion: item: ItemRef, rule, score (float), score_text (e.g. '+1,420 eHP per 1,000 gold'),
#               evidence: Evidence | None  (games, wins, win_rate, ci_low, ci_high, condition_text)
#   Rules (thresholds are constants in the module):
#     magic_share >= 0.55     -> items with magic_resist > 0
#     physical_share >= 0.55  -> items with armor > 0
#     healing_pm > p75        -> items with applies_grievous_wounds
#     cc_pm > p75             -> items with tenacity_pct > 0
#     'pen' (fifth, always evaluated): the lane opponent's relevant resist at level 11 >= 60
#        -> armor-pen/lethality items when the user's champion deals mostly physical damage
#           (its own threat profile at that role decides; tie -> most common first item), else
#           magic-pen items. Its evidence condition is class-sized like the others: games where
#           the lane opponent's champion has >= 60 of that resist at level 11.
#   Candidate items: purchasable catalogue items matching the class (boots allowed here, e.g. Mercury's Treads).
#   Evidence for item X under rule R: participant_core rows for (champion, role) where the rule
#   condition holds on enemy_* columns, joined to purchase_event on that participant with item_id = X.
#   Evidence only when games >= 30, else None and the UI shows "stat model only, no sample".
#   Class-level evidence line (e.g. "players who completed a magic-resist item won 54%") uses
#   participant_core.completed_* flags; expose it as SituationalAnswer.class_evidence[rule].

# queries/statmodel.py  (pure functions, no DB; draft 4 "Stat model")
resist_at_level(base, per_level, level) -> float          # base + growth * (level - 1)
damage_multiplier(resist) -> float                        # 100 / (100 + resist)
effective_hp(hp, armor, magic_resist, physical_share, magic_share) -> float
defensive_score(champion, item, profile, level=11) -> float   # eHP gained per 1,000 gold
penetration_score(item, opponent, level=11) -> float          # multiplier_after / multiplier_before - 1
#   REFERENCE_LEVEL = 11. Champion stats at level 11 from base + per_level * 10.

# queries/users.py
get_or_create_user(conn, display_name) -> UserRef       # validates 2..40 chars, trims
get_user(conn, user_id) -> UserRef | None

# queries/saved.py  (draft 4 "Saved builds"; constraints 1, 3, 8, 11)
create_build(conn, user_id, *, name, notes, champion_id, role, opponent_champion_id,
             enemy_champion_ids, item_ids, observed: bool) -> int
#   Exactly three distinct legendary item ids; 0..4 enemies excluding champion/opponent;
#   is_customized = not observed. Raise ValidationError(message) on any violation
#   (also translate psycopg check_violation / unique_violation into ValidationError).
#   Everything in one transaction.
list_builds(conn, user_id) -> list[SavedBuild]
get_build(conn, build_id) -> SavedBuild | None
#   SavedBuild: build_id, user, name, notes, champion, role, opponent, enemies, items (ordered by position), is_customized, created_at, stat_totals (dict, column-wise sum over the three items)
update_items(conn, build_id, item_ids) -> None          # sets is_customized = TRUE
rename_build(conn, build_id, name, notes) -> None
delete_build(conn, build_id) -> None
is_observed_sequence(conn, champion_id, role, item_ids) -> bool   # constraint 11: appears in participant_core for that champion+role

# queries/browse.py  (so every entity and relationship is reachable from the UI)
recent_matches(conn, limit=50, tier=None) -> list[MatchSummary]
get_match(conn, match_id) -> MatchDetail | None          # ten participants with champion, role, team, won, measures, and their purchase events in order
champion_overview(conn, champion_id) -> ChampionOverview   # games by role, win rate, threat profile per role (from champion_effective_profile), top matchups
```

`ValidationError(Exception)` lives in `laneforge/queries/errors.py` and carries a
user-facing message.

## Web layer (`laneforge/web/`)

App factory `create_app()` in `laneforge/web/app.py`; `python -m laneforge.web`
runs it on port 8000. Connection per request via `flask.g`, closed in teardown.
Secret key from `FLASK_SECRET_KEY`. Session holds `user_id` only.

Routes (blueprints: `pages`, `builds`, `account`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Matchup form (champion, role, opponent, up to four enemies) with dataset summary |
| GET | `/matchup?champion=&role=&opponent=&enemy=&enemy=...` | Full answer page: ladder + situational + save button |
| GET | `/matchup/core` (same params) | htmx partial: core builds block |
| GET | `/matchup/situational` (same params) | htmx partial: situational block |
| GET | `/champions`, `/champions/<id>` | Catalogue and overview |
| GET | `/items`, `/items/<id>` | Catalogue with stat line, and which builds/champions use it |
| GET | `/matches`, `/matches/<match_id>` | Browse loaded matches; match page shows ten participants and purchase timelines |
| GET/POST | `/signin`, `/signout` | Display-name sign-in |
| GET | `/builds` | Signed-in user's saved builds |
| POST | `/builds` | Save a displayed build (hidden fields carry the matchup; `observed=1` when saved unchanged) |
| GET | `/builds/<id>` | Build page with stat totals, edit controls |
| POST | `/builds/<id>/items` | Swap/reorder items (three selects), sets customized |
| POST | `/builds/<id>/rename`, `/builds/<id>/delete` | |

Every POST validates through the query layer; `ValidationError` becomes a
flash message and a redirect back. Unknown ids => 404 page in the site style.

Templates receive the dataclasses above directly. The UI agent owns
`templates/` and `static/`; the query agent owns `app.py` and the blueprints.
Both must agree on template names and context variable names, listed here:

| Template | Context |
|---|---|
| `base.html` | `current_user`, `dataset` (DatasetSummary) |
| `index.html` | `champions`, `roles`, `dataset` |
| `matchup.html` | `champion`, `role`, `opponent`, `enemies`, `ladder`, `situational`, `query_string` |
| `partials/core.html` | `ladder`, `champion`, `role`, `opponent` |
| `partials/situational.html` | `situational`, `champion`, `role`, `opponent`, `enemies` |
| `champions.html`, `champion.html` | `champions` / `champion`, `overview` |
| `items.html`, `item.html` | `items` / `item`, `usage` |
| `matches.html`, `match.html` | `matches` / `match` |
| `signin.html` | |
| `builds.html`, `build.html` | `builds` / `build`, `legendary_items` |
| `errors/404.html`, `errors/400.html` | `message` |

Image URLs (helper in `laneforge/web/ddragon.py`, registered as Jinja globals):
- champion square: `https://ddragon.leagueoflegends.com/cdn/{ver}/img/champion/{ddragon_key}.png`
- item icon: `https://ddragon.leagueoflegends.com/cdn/{ver}/img/item/{item_id}.png`
- `ver` from `DDRAGON_VERSION` env (default `16.18.1`).

## Ingest (`laneforge/ingest/`)

CLI: `python -m laneforge.ingest <command>`; commands `ddragon`, `seed`,
`crawl`, `load`, `refresh`, `status`. All resumable and idempotent.

- `ddragon`: parse `data/ddragon/champion.json` and `item.json` into `champion`
  and `item` per draft 4 cleaning rules (maps["11"], purchasable, collapse ids >
  320000 with the same name to the lowest id, `is_boots`, `is_legendary`, the
  ten `stats` fields, and the five parsed from `<stats>`; `Magic Penetration`
  is flat when the number has no `%`, percent when it does; fractions stored as
  0.xxx). Upsert with `INSERT ... ON CONFLICT DO UPDATE`.
- `seed`: league-v4 entries for a tier band (default GOLD IV .. PLATINUM I, NA1,
  RANKED_SOLO_5x5), collecting puuids into `data/seeds.jsonl` with their tier.
- `crawl`: for each seed, list match ids (queue 420, patch window, count 100),
  fetch match + timeline for unseen ids, gzip raw JSON to
  `data/raw/{match_id}.json.gz` and `data/raw/{match_id}.timeline.json.gz`.
  Checkpoint in `data/checkpoint.json`. Rate limiter honours 20 req/s and
  100 req/2 min and `Retry-After` on 429; a 403 (expired dev key) stops the
  run with a clear message and is resumable. Key from `RIOT_API_KEY`.
- `load`: replay raw files into `match`, `participant`, `purchase_event`
  applying the admission rules and the ITEM_UNDO stack replay; skipped matches
  are logged with the reason; loading is transactional per match and uses
  COPY for purchase events. Then `refresh`.

## Testing conventions

- Files `tests/test_<module>.py`, AAA layout, names describe behaviour.
- Query tests build data with `tests/factories.py`, call `db.refresh_views`,
  assert on returned dataclasses.
- Ingest tests use recorded fixture JSON in `tests/fixtures/` shaped exactly like
  Riot responses (hand-trimmed to one match), no network.
- Web tests use Flask's test client against the real test database.

## Working notes for implementors

- Before your first Bash command and first write to each new file, a
  PreToolUse gate may deny the call and ask for facts. State them in one short
  paragraph and retry the identical call; it passes on the retry.
- Use `.venv/bin/python` and `.venv/bin/pytest` in the repo root.
- Postgres binaries are at `/opt/homebrew/opt/postgresql@17/bin`.
- Do not edit files owned by another agent. If you need a change there, write
  what you need in your final report.
