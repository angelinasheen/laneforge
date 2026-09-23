"""Match admission rules against the recorded fixture and targeted edits of it."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from laneforge.ingest.admission import (
    REASON_MALFORMED,
    REASON_PATCH,
    REASON_POSITIONS,
    REASON_QUEUE,
    REASON_REMAKE,
    REASON_SHORT,
    admit,
    duration_seconds,
    patch_from_version,
)

FIXTURES = Path(__file__).parent / "fixtures" / "riot"
PATCH = "16.18"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def match() -> dict:
    return copy.deepcopy(fixture("match.json"))


def test_clean_ranked_match_on_patch_is_admitted(match):
    assert admit(match, PATCH) is None


def test_other_queue_is_rejected(match):
    match["info"]["queueId"] = 440

    assert admit(match, PATCH).reason == REASON_QUEUE


def test_other_patch_is_rejected():
    assert admit(fixture("wrong_patch.json"), PATCH).reason == REASON_PATCH


def test_patch_prefix_needs_a_dot_boundary(match):
    match["info"]["gameVersion"] = "16.180.1.1"

    assert admit(match, PATCH).reason == REASON_PATCH


def test_remake_is_rejected_as_early_surrender():
    rejection = admit(fixture("remake.json"), PATCH)

    assert rejection.reason == REASON_REMAKE


def test_early_surrender_by_one_participant_is_enough(match):
    match["info"]["participants"][7]["gameEndedInEarlySurrender"] = True

    assert admit(match, PATCH).reason == REASON_REMAKE


def test_game_under_sixteen_minutes_is_rejected(match):
    match["info"]["gameDuration"] = 16 * 60 - 1

    assert admit(match, PATCH).reason == REASON_SHORT


def test_game_of_exactly_sixteen_minutes_is_admitted(match):
    match["info"]["gameDuration"] = 16 * 60

    assert admit(match, PATCH) is None


def test_duration_in_milliseconds_without_end_timestamp(match):
    del match["info"]["gameEndTimestamp"]
    match["info"]["gameDuration"] = 1_850_400

    assert duration_seconds(match["info"]) == 1850
    assert admit(match, PATCH) is None


def test_missing_team_position_is_rejected(match):
    match["info"]["participants"][2]["teamPosition"] = ""

    assert admit(match, PATCH).reason == REASON_POSITIONS


def test_duplicate_role_on_a_team_is_rejected(match):
    match["info"]["participants"][8]["teamPosition"] = "TOP"

    assert admit(match, PATCH).reason == REASON_POSITIONS


def test_nine_participants_is_malformed(match):
    match["info"]["participants"].pop()

    assert admit(match, PATCH).reason == REASON_MALFORMED


def test_rejection_reads_as_reason_and_detail():
    rejection = admit(fixture("wrong_patch.json"), PATCH)

    assert str(rejection).startswith("wrong_patch: gameVersion 16.17")


def test_patch_from_ddragon_version():
    assert patch_from_version("16.18.1") == "16.18"
    with pytest.raises(ValueError):
        patch_from_version("lolpatch_16")
