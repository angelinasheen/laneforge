"""Ingest CLI: python -m laneforge.ingest {ddragon,seed,crawl,load,refresh,status}.

See docs/INGEST.md for the end-to-end run order.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from laneforge import db
from laneforge.ingest.admission import patch_from_version
from laneforge.ingest.riot import RiotAuthError
from laneforge.ingest.storage import DEFAULT_DATA_DIR, DataPaths

log = logging.getLogger("laneforge.ingest")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format=LOG_FORMAT, stream=sys.stderr)
    # httpx logs every request at INFO; keep crawl output readable unless --verbose.
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    paths = DataPaths(Path(args.data_dir))
    try:
        return args.handler(args, paths)
    except KeyboardInterrupt:
        log.warning("interrupted")
        return EXIT_OK
    except RiotAuthError as exc:
        log.error("%s", exc)
        return EXIT_AUTH
    except Exception as exc:  # top level of the CLI: log with traceback, exit non-zero
        log.exception("%s failed: %s", args.command, exc)
        return EXIT_ERROR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m laneforge.ingest")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="default: ./data")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ddragon", help="load champion and item catalogue from data/ddragon") \
        .set_defaults(handler=cmd_ddragon)

    seed = sub.add_parser("seed", help="collect seed players from league-v4")
    seed.add_argument("--tiers", nargs="+", default=["GOLD", "PLATINUM"])
    seed.add_argument("--divisions", nargs="+", default=["IV", "III", "II", "I"])
    seed.add_argument("--max-pages", type=int, default=5, help="pages per tier+division")
    seed.set_defaults(handler=cmd_seed)

    crawl = sub.add_parser("crawl", help="download match + timeline JSON for the seeds")
    crawl.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD (UTC), default 21 days ago")
    crawl.add_argument("--until", type=date.fromisoformat, help="YYYY-MM-DD inclusive, default now")
    crawl.add_argument("--limit", type=_non_negative, help="stop after N new matches")
    crawl.add_argument("--patch", help="reject off-patch matches before fetching their timeline; "
                                       "default from DDRAGON_VERSION or data/ddragon/VERSION")
    crawl.set_defaults(handler=cmd_crawl)

    load = sub.add_parser("load", help="load raw files into the database, then refresh views")
    load.add_argument("--patch", help="e.g. 16.18; default from DDRAGON_VERSION or data/ddragon/VERSION")
    load.add_argument("--limit", type=_non_negative, help="load at most N new matches")
    load.set_defaults(handler=cmd_load)

    sub.add_parser("refresh", help="refresh materialized views").set_defaults(handler=cmd_refresh)
    sub.add_parser("status", help="seeds, raw files, checkpoint, row counts, key validity") \
        .set_defaults(handler=cmd_status)
    return parser


def _non_negative(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def cmd_ddragon(args, paths: DataPaths) -> int:
    from laneforge.ingest.ddragon import load_catalogue  # owned by another module; lazy on purpose
    with db.connect() as conn:
        load_catalogue(conn, paths.ddragon)  # logs its own report
        conn.commit()
    return EXIT_OK


def _client():
    from laneforge.ingest.ratelimit import RateLimiter
    from laneforge.ingest.riot import RiotClient
    return RiotClient(os.environ.get("RIOT_API_KEY", ""), RateLimiter())


def cmd_seed(args, paths: DataPaths) -> int:
    from laneforge.ingest.seeds import collect_seeds
    with _client() as client:
        report = collect_seeds(client, paths, tuple(args.tiers), tuple(args.divisions),
                               args.max_pages)
    print(f"seeds: +{report.added} new, {report.total} total, {report.pages_fetched} pages fetched")
    return EXIT_OK


def cmd_crawl(args, paths: DataPaths) -> int:
    from laneforge.ingest.crawl import STOP_AUTH, STOP_SERVER, crawl, window_from_dates
    window = window_from_dates(args.since, args.until)
    log.info("crawl window %d..%d (epoch s)", window.start_s, window.end_s)
    with _client() as client:
        report = crawl(client, paths, window, args.limit, patch=_patch(args))
    print(f"crawl {report.stopped}: {report.fetched} fetched, {report.missing} missing, "
          f"seed cursor {report.seed_index}. {report.message}")
    if report.stopped == STOP_AUTH:
        return EXIT_AUTH
    return EXIT_ERROR if report.stopped == STOP_SERVER else EXIT_OK


def _patch(args) -> str:
    if args.patch:
        return args.patch
    version = os.environ.get("DDRAGON_VERSION")
    if not version:
        version_file = DataPaths(Path(args.data_dir)).ddragon / "VERSION"
        version = version_file.read_text(encoding="utf-8").strip()
    return patch_from_version(version)


def cmd_load(args, paths: DataPaths) -> int:
    from laneforge.ingest.completion import load_completed_ids
    from laneforge.ingest.load import load_raw_dir
    patch = _patch(args)
    completed = load_completed_ids(paths.ddragon)
    with db.connect() as conn:
        report = load_raw_dir(conn, paths.raw, patch, completed, limit=args.limit)
        log.info("refreshing materialized views")
        db.refresh_views(conn)
    skipped = ", ".join(f"{k}={v}" for k, v in report.skipped_by_reason.items()) or "none"
    print(f"load (patch {patch}): {report.loaded} loaded, {report.already_present} already present, "
          f"skipped: {skipped}")
    return EXIT_OK


def cmd_refresh(args, paths: DataPaths) -> int:
    with db.connect() as conn:
        db.refresh_views(conn)
    print("materialized views refreshed")
    return EXIT_OK


def cmd_status(args, paths: DataPaths) -> int:
    from laneforge.ingest.status import api_key_from_env, status_lines
    print("\n".join(status_lines(paths, api_key_from_env())))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
