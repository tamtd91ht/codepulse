import logging

import pytest

from app.startup_check import check_postgres, check_redis


def test_unreachable_dependencies_log_one_line_each_without_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.startup_check")
    assert check_postgres("postgresql+psycopg://u:hunter2@127.0.0.1:1/db") is False
    assert check_redis("redis://:hunter2@127.0.0.1:1/0") is False
    db, redis = caplog.records
    assert (db.message, db.target) == ("postgres unreachable", "127.0.0.1:1/db")  # type: ignore[attr-defined]
    assert (redis.message, redis.target) == ("redis unreachable", "127.0.0.1:1")  # type: ignore[attr-defined]
    assert "hunter2" not in caplog.text
