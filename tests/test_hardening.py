"""Transport-level hardening added after the security review."""
from __future__ import annotations

import pytest

from laneforge.ingest.crawl import _checked_match_id
from laneforge.web.app import MAX_CONTENT_LENGTH, create_app


@pytest.fixture
def app(test_dsn):
    return create_app({"TESTING": True, "DATABASE_URL": test_dsn, "SECRET_KEY": "test"})


def test_oversized_request_body_is_refused_with_413(app):
    client = app.test_client()

    response = client.post("/signin", data={"display_name": "x" * (MAX_CONTENT_LENGTH + 1)})

    assert response.status_code == 413


def test_cross_site_post_is_refused_with_403(app):
    client = app.test_client()

    response = client.post("/signin", data={"display_name": "someone"},
                           headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


def test_same_site_post_passes_the_origin_check(app):
    client = app.test_client()

    response = client.post("/signin", data={"display_name": "someone"},
                           headers={"Origin": "http://localhost"})

    assert response.status_code in (302, 303)


def test_missing_secret_key_is_fatal_outside_tests(monkeypatch, test_dsn):
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
        create_app({"DATABASE_URL": test_dsn})


def test_flood_of_enemy_params_is_rejected_before_parsing(app):
    client = app.test_client()
    query = "champion=103&role=MIDDLE&opponent=238&" + "&".join(f"enemy={n}" for n in range(1, 41))

    response = client.get(f"/matchup/core?{query}")

    assert response.status_code == 400
    assert b"Name at most 4" in response.data


@pytest.mark.parametrize("bad", ["../etc/passwd", "NA1_1/../x", "", "na1_123", "NA1-123"])
def test_match_id_shape_is_enforced(bad):
    with pytest.raises(ValueError):
        _checked_match_id(bad)


def test_real_match_id_shape_is_accepted():
    assert _checked_match_id("NA1_5646690146") == "NA1_5646690146"
