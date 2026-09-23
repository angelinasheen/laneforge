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
