# LaneForge

League of Legends core-build recommender keyed on lane matchup. COMS W4111
Project 1, Columbia, fall 2026. Spec: `../project1-part1-draft4.md`.

## Setup

```bash
brew install postgresql@17 && brew services start postgresql@17
createdb laneforge && createdb laneforge_test
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then fill in RIOT_API_KEY and FLASK_SECRET_KEY
scripts/db.sh reset             # applies sql/reset.sql, schema.sql, views.sql
.venv/bin/python -m laneforge.ingest ddragon
.venv/bin/pytest
.venv/bin/python -m laneforge.web   # http://localhost:8000
```

## Layout

See `docs/CONTRACT.md` for module boundaries and `docs/DESIGN.md` for the UI.

## Running the pipeline

```bash
.venv/bin/python -m laneforge.ingest status                  # seeds, raw files, row counts, key validity
.venv/bin/python -m laneforge.ingest seed                    # league-v4 Gold IV .. Platinum I, ~40 requests
.venv/bin/python -m laneforge.ingest crawl --since 2026-09-09 --limit 5000
.venv/bin/python -m laneforge.ingest load                    # admission rules + timeline replay, then refresh views
```

The crawl checkpoints after every match. A development key expires after 24 hours;
paste a fresh one into `.env` and rerun the same command to resume. See `docs/INGEST.md`.

## Deploying for the Part 3 demo

The course requires a VM with a stable IP. Two rules, both consequences of the
display-name sign-in, where the signed session cookie is the whole login:

1. **Never bind Flask's development server to a public address over plain HTTP.**
   Put a TLS-terminating reverse proxy in front (Caddy needs one line:
   `your-host.example { reverse_proxy 127.0.0.1:8000 }`) and run the app behind it
   with `LANEFORGE_HTTPS=1` so the cookie carries the `Secure` flag.
2. **`FLASK_SECRET_KEY` must be set** to a long random value (`python3 -c
   "import secrets; print(secrets.token_hex(32))"`). The app refuses to start
   without one outside tests, because a known key lets anyone forge a session.

For a production WSGI server use `gunicorn "laneforge.web.app:create_app()"`
(add it to the venv first); the app factory takes no arguments.

## Tests

`.venv/bin/pytest` runs the whole suite against the real `laneforge_test`
database: constraints and triggers, every materialized view, the Data Dragon
parser on the real catalogue, the crawler and loader on recorded fixtures, every
query, every route, and every template.
