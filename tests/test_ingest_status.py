"""The `status` report works offline and without a key."""
from __future__ import annotations

import json

from laneforge import db
from laneforge.ingest.storage import DataPaths


def test_status_reports_files_rows_and_skips_key_check(tmp_path, conn, test_dsn):
    # Arrange
    from laneforge.ingest.status import status_lines
    paths = DataPaths(tmp_path)
    paths.seeds.write_text(json.dumps({"puuid": "p", "tier": "GOLD", "division": "I"}) + "\n")

    # Act
    lines = status_lines(paths, api_key=None, connect=lambda: db.connect(test_dsn))

    # Assert
    text = "\n".join(lines)
    assert "seeds:            1" in text
    assert "raw matches:      0" in text
    assert "rows in match:" in text
    assert "not set" in text


def test_status_survives_an_unreachable_database(tmp_path):
    from laneforge.ingest.status import status_lines

    lines = status_lines(DataPaths(tmp_path), api_key=None,
                         connect=lambda: db.connect("postgresql:///no_such_db_laneforge"))

    assert any(line.startswith("database:         unavailable") for line in lines)
