"""Migration f9b1d3e5a7c0 (docs/specs/30 §15.9): the five sample sets exist with their items, and
splitting tests.delete / tests.approve off left nobody with less than before."""

import pytest
from sqlalchemy import Engine, bindparam, text

pytestmark = pytest.mark.integration

ALL = {"tests.view", "tests.edit", "tests.delete", "tests.approve", "tests.run", "tests.manage"}
SAMPLES = {
    "Testcase - Admin dự án": ALL,
    "Testcase - Lead": ALL,
    "Testcase - Tester": {"tests.view", "tests.edit", "tests.run"},
    "Testcase - Developer": {"tests.view", "tests.run"},
    "Testcase - Guest": {"tests.view"},
}


def test_sample_sets_after_migration(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT s.name, s.builtin, i.permission, i.scope FROM permission_sets s "
                "JOIN permission_set_items i ON i.set_id = s.id WHERE s.name IN :names"
            ).bindparams(bindparam("names", value=list(SAMPLES), expanding=True))
        ).all()
    got: dict[str, dict[str, str]] = {}
    for name, builtin, permission, scope in rows:
        assert builtin is False, name
        got.setdefault(name, {})[permission] = scope
    assert {name: set(items) for name, items in got.items()} == SAMPLES
    for items in got.values():
        assert all(
            scope == ("flag" if key == "tests.view" else "all") for key, scope in items.items()
        )


def test_every_set_with_edit_or_manage_holds_delete_or_approve(migrated_engine: Engine) -> None:
    """Sets that held tests.edit / tests.manage got tests.delete / tests.approve at the same scope;
    the samples are the exception by design (a Tester edits but does not delete)."""
    with migrated_engine.connect() as conn:
        missing = conn.execute(
            text(
                "SELECT s.id, s.name, old.permission FROM permission_sets s "
                "JOIN permission_set_items old ON old.set_id = s.id "
                "AND old.permission IN ('tests.edit', 'tests.manage') "
                "LEFT JOIN permission_set_items new ON new.set_id = s.id AND new.permission = "
                "CASE old.permission WHEN 'tests.edit' THEN 'tests.delete' "
                "ELSE 'tests.approve' END AND new.scope = old.scope "
                "WHERE new.set_id IS NULL AND s.name NOT IN :samples"
            ).bindparams(bindparam("samples", value=list(SAMPLES), expanding=True))
        ).all()
        holders = conn.execute(
            text("SELECT count(*) FROM permission_set_items WHERE permission = 'tests.edit'")
        ).scalar_one()
    assert holders > 0  # the built-in sets hold it
    assert missing == []
