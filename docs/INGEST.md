# Ingest pipeline

`python -m laneforge.ingest <command>` gets data from Data Dragon and the Riot API into
Postgres. Every command can be resumed and run again safely. Progress goes to stderr
with timestamps; add `--verbose` (before the command) for debug output, including
every HTTP request.

## End to end with a development key

```bash
# 0. once: key + database
cp .env.example .env            # then set RIOT_API_KEY=RGAPI-... (and DATABASE_URL if not default)
scripts/db.sh reset             # empty schema on $DATABASE_URL (default postgresql:///laneforge)

# 1. catalogue (no network): champions + items from data/ddragon/
.venv/bin/python -m laneforge.ingest ddragon

# 2. seed players: GOLD IV .. PLATINUM I, NA1, ranked solo (8 bands x 5 pages ≈ 40 requests, ~8,000 seeds)
.venv/bin/python -m laneforge.ingest seed                     # --tiers GOLD PLATINUM --divisions IV III II I --max-pages 5

# 3. crawl: match + timeline for every seed's games in the patch window
.venv/bin/python -m laneforge.ingest crawl --since 2026-09-10 --limit 500   # try it small first
.venv/bin/python -m laneforge.ingest crawl --since 2026-09-10               # then let it run

# 4. load raw files into match / participant / purchase_event, then refresh views
.venv/bin/python -m laneforge.ingest load

# any time
.venv/bin/python -m laneforge.ingest status
```

`--since` / `--until` are UTC dates (`YYYY-MM-DD`, `--until` inclusive). Without them the
window is the last 21 days. Set `--since` to the patch's release day so the match-id
listing does not return games from the previous patch. The loader would reject those
anyway (`wrong_patch`), but they still cost two requests each to download.

`load` takes the patch from `--patch`, else from `DDRAGON_VERSION`, else from
`data/ddragon/VERSION` (`16.18.1` → `16.18`), and admits only matches whose
`gameVersion` starts with `16.18.`.

## Budget

The binding limit on a dev (or personal) key is **100 requests per 2 minutes**. The
second limit, 20 requests per second, only matters in bursts. Each match costs **2
requests** (the match, then its timeline), and each seed costs one more to list match
ids (a second one if the seed has more than 100 games in the window).

- 100 requests / 120 s = 3,000 requests/h ≈ **1,400–1,500 matches/h** after listing overhead
- 5,000 matches (the validation set) ≈ 3.5 h; 20,000 matches ≈ 14–16 h
- Raw files are about 100 KB per match gzipped (timeline ≈ 90 KB, match ≈ 12 KB),
  so 20,000 matches is roughly 2 GB

The limiter (`ratelimit.py`) enforces both windows locally before every request, sleeps
for `Retry-After` on a 429, and syncs with the `X-App-Rate-Limit-Count` header, so a
second process using the same key slows this one down instead of causing 429s.

## Resuming

The crawl writes `data/checkpoint.json` after **every** match, so stopping it at any point
is safe:

- **Ctrl-C**: saves the checkpoint and exits 0. Run the same command again to continue.
- **Key expired (HTTP 401/403)**: dev keys last 24 h. The crawl stops cleanly with a
  message and exits 2. Regenerate the key at https://developer.riotgames.com, paste it
  into `.env` as `RIOT_API_KEY=...`, and run the same `crawl` command again. Check the key
  first with `status`, which makes one cheap league-v4 request.
- **Riot 5xx that outlasts 3 retries**: the crawl stops (exit 1) with the checkpoint saved.
  Run it again later.
- **Lost checkpoint**: nothing is downloaded twice, because a match whose files are
  already on disk is skipped. Only the id listings are repeated.

The checkpoint holds the seed cursor (the next seed, in `seeds.jsonl` order), every match
id handled (fetched, or skipped because its timeline returned 404), the seed tier of every
fetched match, and counters. `seed` resumes the same way through
`data/seeds.progress.json` (pages already fetched). `load` skips match ids already in the
`match` table, so rerunning it only adds new files.

## Where files live

| Path | What |
|---|---|
| `data/ddragon/{champion,item}.json`, `VERSION` | Data Dragon for the loaded patch (checked in) |
| `data/seeds.jsonl` | one `{"puuid","tier","division"}` per line, deduplicated |
| `data/seeds.progress.json` | league-v4 pages already fetched |
| `data/checkpoint.json` | crawl cursor, seen ids, `tier_by_match`, counts |
| `data/raw/{match_id}.json.gz` | match-v5 match response, unmodified |
| `data/raw/{match_id}.timeline.json.gz` | match-v5 timeline response, unmodified |
| `data/raw/{match_id}.meta.json` | `{"seed_tier": "GOLD"}` (tier of the seed the match came from) |

Files are written to a temp file and renamed into place, so a crash never leaves half a
file behind. Each match is written meta first, then timeline, then match, so if the match
file exists, the other two do too. `data/raw/` and `data/checkpoint*` are git-ignored.

## What `load` does to each match

1. Skip it if its `match_id` is already in the database.
2. Admission (`admission.py`): queue 420; `gameVersion` on the loaded patch; no
   participant with `gameEndedInEarlySurrender` (remakes); duration ≥ 16 min
   (`gameDuration` is in seconds when `gameEndTimestamp` is present, otherwise ms);
   each team's `teamPosition` values are exactly TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY.
3. Rows (`rows.py`): `start_time` = UTC `gameStartTimestamp`, `winning_team` from
   `info.teams`, the five per-game measures per participant, and `seed_tier` from the
   meta file (`UNKNOWN` if the meta file is missing).
4. Purchases (`timeline.py`): the stack replay from draft 4. `ITEM_UNDO` pops the latest
   matching purchase and restores the components destroyed at that moment; a sale never
   removes a purchase. Only **completed** items are kept (`completion.py`): on Summoner's
   Rift, purchasable, no `into`, not Consumable or Trinket. Transformation results such as
   Muramana, Seraph's and Fimbulwinter (items with `specialRecipe`) are dropped. Events with
   `participantId` 0 and events after the end of the game are ignored.
5. One transaction per match; purchase events go in with `COPY`. A match that breaks a
   constraint is rolled back and counted as `constraint_violation`.

Every skip is logged at INFO as `skip <match_id>: <reason> (<detail>)` and counted in the
summary line. The reasons are `wrong_queue`, `wrong_patch`, `early_surrender`,
`too_short`, `bad_positions`, `malformed`, `missing_timeline`, `unknown_champion`,
`no_winner`, `id_mismatch`, `unreadable_file` and `constraint_violation`.
