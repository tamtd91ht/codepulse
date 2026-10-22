import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import Base
from tests.conftest import alembic_config

pytestmark = pytest.mark.integration

SPEC_TABLES = {
    "connections",
    "projects",
    "raw_gitlab",
    "teams",
    "users",
    "team_members",
    "accounts",
    "commits",
    "merge_requests",
    "mr_commits",
    "mr_notes",
    "pipelines",
    "reviews",
    "review_findings",
    "finding_disputes",
    "metrics_user_period",
    "metrics_team_period",
}


def test_all_spec_tables_exist(migrated_engine: Engine) -> None:
    assert set(inspect(migrated_engine).get_table_names()) >= SPEC_TABLES


def test_every_table_has_timestamps(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for table in SPEC_TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert {"created_at", "updated_at"} <= columns, table


def test_spec_indexes_exist(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    expected = {
        "commits": ["project_id", "authored_at"],
        "merge_requests": ["project_id", "merged_at"],
        "review_findings": ["review_id", "severity"],
        "reviews": ["author_user_id", "finished_at"],
    }
    for table, columns in expected.items():
        assert columns in [ix["column_names"] for ix in inspector.get_indexes(table)], table


def test_models_match_migrations(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_review_unique_holds_when_target_column_is_null(migrated_engine: Engine) -> None:
    insert = text(
        "INSERT INTO reviews (target_type, commit_sha, reviewed_sha, status, rubric_version)"
        " VALUES ('commit', NULL, 'abc', 'queued', 'v1')"
    )
    with migrated_engine.connect() as conn, conn.begin():
        conn.execute(insert)
        with pytest.raises(IntegrityError):
            conn.execute(insert)
        conn.rollback()


def test_downgrade_then_upgrade(postgres_url: str, migrated_engine: Engine) -> None:
    config = alembic_config(postgres_url)
    command.downgrade(config, "base")
    assert not SPEC_TABLES & set(inspect(migrated_engine).get_table_names())
    command.upgrade(config, "head")
    assert set(inspect(migrated_engine).get_table_names()) >= SPEC_TABLES
