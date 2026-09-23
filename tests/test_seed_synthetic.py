"""The synthetic match generator produces constraint-respecting, deterministic data."""
from __future__ import annotations

import pytest

from scripts.seed_synthetic import seed, tier_band

MATCHES = 40


def _roster(conn, match_id):
    rows = conn.execute(
        "SELECT participant_number, team, role, champion_id FROM participant "
        "WHERE match_id = %(m)s ORDER BY participant_number", {"m": match_id}).fetchall()
    return [tuple(r.values()) for r in rows]


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


@pytest.fixture
def seeded(conn):
    summary = seed(conn, MATCHES, 7, ("GOLD", "PLATINUM"))
    return conn, summary


def test_seed_counts(seeded):
    conn, summary = seeded
    assert (_count(conn, "match"), _count(conn, "participant")) == (MATCHES, MATCHES * 10)
    assert summary.participants == MATCHES * 10
    assert summary.purchase_events == _count(conn, "purchase_event")


def test_every_participant_has_four_to_eight_purchases(seeded):
    conn, _ = seeded
    row = conn.execute(
        "SELECT MIN(n) AS lo, MAX(n) AS hi, COUNT(*) AS participants FROM ("
        " SELECT COUNT(pe.event_number) AS n FROM participant p LEFT JOIN purchase_event pe"
        " USING (match_id, participant_number) GROUP BY p.match_id, p.participant_number) t"
    ).fetchone()
    assert row["participants"] == MATCHES * 10
    assert row["lo"] >= 4 and row["hi"] <= 8


def test_every_participant_has_full_core_and_boots_first(seeded):
    conn, _ = seeded
    assert conn.execute("SELECT MIN(legendary_completions) AS lo FROM participant_core"
                        ).fetchone()["lo"] >= 3
    first_not_boots = conn.execute(
        "SELECT COUNT(*) AS n FROM purchase_event pe JOIN item i USING (item_id) "
        "WHERE pe.event_number = 1 AND NOT i.is_boots").fetchone()["n"]
    assert first_not_boots == 0


def test_event_number_increases_with_game_time(seeded):
    conn, _ = seeded
    bad = conn.execute(
        "SELECT COUNT(*) AS n FROM purchase_event a JOIN purchase_event b "
        "ON a.match_id = b.match_id AND a.participant_number = b.participant_number "
        "AND a.event_number < b.event_number AND a.game_time_ms > b.game_time_ms").fetchone()["n"]
    assert bad == 0


def test_match_fields_are_plausible(seeded):
    conn, _ = seeded
    row = conn.execute(
        "SELECT MIN(duration_seconds) AS lo, MAX(duration_seconds) AS hi, "
        "BOOL_AND(game_version LIKE '16.18.%%') AS patch, "
        "BOOL_AND(match_id LIKE 'NA1\\_SYN\\_%%') AS ids, "
        "ARRAY_AGG(DISTINCT seed_tier) AS tiers FROM match").fetchone()
    assert 16 * 60 <= row["lo"] and row["hi"] <= 40 * 60
    assert row["patch"] and row["ids"]
    assert set(row["tiers"]) <= {"GOLD", "PLATINUM"}


def test_views_are_refreshed(seeded):
    conn, _ = seeded
    assert _count(conn, "participant_core") == MATCHES * 10


def test_same_seed_gives_same_first_roster(seeded):
    conn, _ = seeded
    first = _roster(conn, "NA1_SYN_1")
    seed(conn, MATCHES, 7, ("GOLD", "PLATINUM"))
    assert _roster(conn, "NA1_SYN_1") == first
    assert _count(conn, "match") == MATCHES          # reseeding replaces, not duplicates


def test_different_seed_gives_different_data(seeded):
    conn, _ = seeded
    first = [_roster(conn, f"NA1_SYN_{n}") for n in range(1, 6)]
    seed(conn, MATCHES, 8, ("GOLD", "PLATINUM"))
    assert [_roster(conn, f"NA1_SYN_{n}") for n in range(1, 6)] != first


def test_tier_band_parsing():
    assert tier_band("gold..platinum") == ("GOLD", "PLATINUM")
    assert tier_band("SILVER") == ("SILVER",)
    with pytest.raises(ValueError):
        tier_band("PLATINUM..GOLD")
    with pytest.raises(ValueError):
        tier_band("WOOD..GOLD")


def test_zero_matches_rejected(conn):
    with pytest.raises(ValueError):
        seed(conn, 0)
