#!/usr/bin/env bash
# LaneForge database helper.
#   scripts/db.sh reset       drop + recreate schema and views on $DATABASE_URL (default laneforge), then refresh
#   scripts/db.sh refresh     refresh the materialized views
#   scripts/db.sh psql        open psql on the database
#   scripts/db.sh test-reset  same as reset, on laneforge_test
set -euo pipefail

PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@17/bin}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_DIR="$REPO_ROOT/sql"
DEFAULT_URL="postgresql:///laneforge"
TEST_URL="${TEST_DATABASE_URL:-postgresql:///laneforge_test}"

die() { echo "db.sh: $*" >&2; exit 1; }

[ -x "$PG_BIN/psql" ] || die "psql not found at $PG_BIN (set PG_BIN)"

run_sql() {
  local url="$1"; shift
  local file
  for file in "$@"; do
    [ -f "$SQL_DIR/$file" ] || die "missing $SQL_DIR/$file"
    echo "db.sh: applying $file to $url"
    "$PG_BIN/psql" "$url" -X -q -v ON_ERROR_STOP=1 --single-transaction -f "$SQL_DIR/$file"
  done
}

reset_db() {
  run_sql "$1" reset.sql schema.sql views.sql refresh.sql
  echo "db.sh: $1 reset (empty tables; load data with python -m laneforge.ingest ddragon / load)"
}

usage() {
  sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

main() {
  local url="${DATABASE_URL:-$DEFAULT_URL}"
  case "${1:-}" in
    reset)      reset_db "$url" ;;
    refresh)    run_sql "$url" refresh.sql ;;
    psql)       exec "$PG_BIN/psql" "$url" ;;
    test-reset) reset_db "$TEST_URL" ;;
    *)          usage ;;
  esac
}

main "$@"
