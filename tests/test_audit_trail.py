"""The audit trail listener (app/audit_trail.py, docs/specs/30 §15.14, G12): create / update /
delete with before and after, the dự án of each row, the actor, long values cut, secrets masked,
bulk deletes, rollback, speed on bulk work, and that retention never purges the trail."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from app import audit_trail as at
from app.models import (
    AuditLog,
    BusinessProject,
    PermissionSet,
    PermissionSetItem,
    TestCase,
    TestConfig,
    TestConfigGroup,
    TestResult,
    TestRun,
    TestRunTest,
    TestSection,
    TestSuite,
    User,
)
from app.retention import purge


def _rows(db: Session, action: str | None = None) -> list[AuditLog]:
    query = select(AuditLog).where(AuditLog.details["changes"].is_not(None)).order_by(AuditLog.id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    return list(db.scalars(query))


def _changes(row: AuditLog) -> dict[str, tuple[object, object]]:
    assert row.details is not None
    return {c["field"]: (c["before"], c["after"]) for c in row.details["changes"]}


def _project(db: Session) -> BusinessProject:
    bp = BusinessProject(name="Trail")
    db.add(bp)
    db.flush()
    return bp


def test_create_update_delete_with_project_and_actor(db_session: Session) -> None:
    db = db_session
    user = User(full_name="Ánh", primary_email="anh@x.vn")
    db.add(user)
    db.flush()
    bp = _project(db)
    at.set_actor(db, user.id)
    suite = TestSuite(business_project_id=bp.id, name="S")
    db.add(suite)
    db.flush()
    section = TestSection(suite_id=suite.id, name="Auth")
    case = TestCase(suite_id=suite.id, title="A", steps="x" * 3000)
    group = TestConfigGroup(business_project_id=bp.id, name="Trình duyệt")
    db.add_all([section, case, group])
    db.flush()
    config = TestConfig(group_id=group.id, name="Chrome")
    db.add(config)
    db.flush()

    made = _rows(db, "case.create")
    assert len(made) == 1 and made[0].target == f"case:{case.id}"
    assert made[0].actor_user_id == user.id and made[0].business_project_id == bp.id
    steps = _changes(made[0])["steps"][1]
    assert isinstance(steps, str) and len(steps) == at.VALUE_LIMIT + 3 and steps.endswith("...")
    assert _rows(db, "section.create")[0].business_project_id == bp.id  # via its suite
    assert _rows(db, "config.create")[0].business_project_id == bp.id  # via its group

    case.title = "B"
    case.search = "b"  # never recorded
    case.revision = 2
    db.flush()
    upd = _rows(db, "case.update")
    assert len(upd) == 1 and _changes(upd[0]) == {"title": ("A", "B"), "revision": (1, 2)}
    # Nothing really changed: no row.
    case.title = "B"
    db.flush()
    assert len(_rows(db, "case.update")) == 1

    # Background work: no actor.
    at.set_actor(db, None)
    db.delete(case)
    db.delete(section)
    db.flush()
    gone = _rows(db, "case.delete")
    assert len(gone) == 1 and gone[0].actor_user_id is None
    assert gone[0].business_project_id == bp.id and _changes(gone[0])["title"] == ("B", None)
    assert _rows(db, "section.delete")[0].business_project_id == bp.id


def test_results_are_not_tracked_and_global_rows(db_session: Session) -> None:
    db = db_session
    bp = _project(db)
    suite = TestSuite(business_project_id=bp.id, name="S")
    db.add(suite)
    db.flush()
    case = TestCase(suite_id=suite.id, title="A")
    run = TestRun(business_project_id=bp.id, name="R")
    db.add_all([case, run])
    db.flush()
    test = TestRunTest(
        run_id=run.id, case_id=case.id, title="A", priority="medium", type="functional"
    )
    db.add(test)
    db.flush()
    before = db.scalar(select(func.count()).select_from(AuditLog))
    db.add(TestResult(test_id=test.id, status="passed"))
    db.flush()
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before

    pset = PermissionSet(name="Kiểm toán")
    db.add(pset)
    db.flush()
    db.add(PermissionSetItem(set_id=pset.id, permission="review.view", scope="all"))
    db.flush()
    item = _rows(db, "permission_set_item.create")
    assert item[0].target == f"permission_set_item:{pset.id}:review.view"
    assert item[0].business_project_id is None


def test_bulk_delete_rewrite_and_rollback(db_session: Session) -> None:
    db = db_session
    pset = PermissionSet(name="Bộ")
    db.add(pset)
    db.flush()
    db.add_all(
        [
            PermissionSetItem(set_id=pset.id, permission="review.view", scope="all"),
            PermissionSetItem(set_id=pset.id, permission="insights.view", scope="flag"),
        ]
    )
    db.commit()
    # Rewritten: one changed, one the same, one dropped... and one added.
    db.execute(delete(PermissionSetItem).where(PermissionSetItem.set_id == pset.id))
    db.add_all(
        [
            PermissionSetItem(set_id=pset.id, permission="review.view", scope="team"),
            PermissionSetItem(set_id=pset.id, permission="members.view", scope="flag"),
        ]
    )
    db.commit()
    acts = [(r.action, r.target) for r in _rows(db) if r.action.startswith("permission_set_item.")]
    assert sorted(acts[2:]) == [
        ("permission_set_item.create", f"permission_set_item:{pset.id}:members.view"),
        ("permission_set_item.delete", f"permission_set_item:{pset.id}:insights.view"),
        ("permission_set_item.update", f"permission_set_item:{pset.id}:review.view"),
    ]
    # A bulk delete alone is written at the commit.
    db.execute(delete(PermissionSetItem).where(PermissionSetItem.permission == "members.view"))
    db.commit()
    assert _rows(db, "permission_set_item.delete")[-1].target.endswith("members.view")  # type: ignore[union-attr]

    # Rolled back: nothing kept for later.
    count = db.scalar(select(func.count()).select_from(AuditLog))
    db.execute(delete(PermissionSetItem).where(PermissionSetItem.set_id == pset.id))
    db.rollback()
    assert at.PENDING_KEY not in db.info
    db.commit()
    assert db.scalar(select(func.count()).select_from(AuditLog)) == count


def test_savepoint_rollback_keeps_the_outer_capture(db_session: Session) -> None:
    db = db_session
    pset = PermissionSet(name="Lồng")
    db.add(pset)
    db.flush()
    db.add_all(
        [
            PermissionSetItem(set_id=pset.id, permission="review.view", scope="all"),
            PermissionSetItem(set_id=pset.id, permission="insights.view", scope="flag"),
        ]
    )
    db.commit()
    mine = PermissionSetItem.set_id == pset.id
    db.execute(delete(PermissionSetItem).where(mine, PermissionSetItem.permission == "review.view"))
    nested = db.begin_nested()
    db.execute(
        delete(PermissionSetItem).where(mine, PermissionSetItem.permission == "insights.view")
    )
    nested.rollback()
    # Only what the SAVEPOINT captured is forgotten; the outer bulk delete is still pending.
    pending = db.info.get(at.PENDING_KEY) or {}
    assert [ident for _entity, ident in pending] == [f"{pset.id}:review.view"]
    db.commit()
    gone = [
        r.target
        for r in _rows(db, "permission_set_item.delete")
        if (r.target or "").startswith(f"permission_set_item:{pset.id}:")
    ]
    assert gone == [f"permission_set_item:{pset.id}:review.view"]
    assert at.SAVED_KEY not in db.info


def test_values_are_plain_and_secrets_masked() -> None:
    assert at.plain(datetime(2026, 1, 2, tzinfo=UTC)) == "2026-01-02T00:00:00+00:00"
    assert at.plain(["a", 1]) == ["a", 1]
    big = at.plain({"k": "v" * 3000})
    assert isinstance(big, str) and big.endswith("...")
    assert at.plain(b"abc") == "<3 bytes>"
    spec = at.TRACKED[next(c for c in at.TRACKED if c.__name__ == "ApiToken")]
    assert "token_hash" in spec.secrets and "last_used_at" not in spec.columns
    assert at._shown(spec, "token_hash", "deadbeef") == "***"


def test_bulk_speed(db_session: Session) -> None:
    """1 000 cases imported, then all edited: the trail adds well under 3 s."""
    db = db_session
    bp = _project(db)
    suite = TestSuite(business_project_id=bp.id, name="S")
    db.add(suite)
    db.flush()

    def work() -> float:
        start = time.perf_counter()
        cases = [
            TestCase(suite_id=suite.id, title=f"Case {i}", steps="bước " * 50, position=i)
            for i in range(1000)
        ]
        db.add_all(cases)
        db.flush()
        for c in cases:
            c.priority = "low"
            c.title += " (sửa)"
        db.flush()
        return time.perf_counter() - start

    work()  # warm up
    with_trail = work()
    try:
        event.remove(Session, "after_flush", at.after_flush)
        without = work()
    finally:
        at.install()
    assert with_trail - without < 3.0, (with_trail, without)
    assert len(_rows(db, "case.create")) == 3000 - 1000  # two runs with the trail on
    assert len(_rows(db, "case.update")) == 2000


def test_retention_never_purges_the_trail(db_session: Session) -> None:
    db = db_session
    old = datetime.now(UTC) - timedelta(days=800)
    db.add(AuditLog(action="case.update", target="case:1", at=old, details={"changes": []}))
    db.add(AuditLog(action="project.update", target="project:1", at=old))
    db.flush()
    purge(db, retention_days=1)
    db.flush()
    kept = db.scalars(select(AuditLog.action).where(AuditLog.at <= old + timedelta(days=1))).all()
    assert sorted(kept) == ["case.update", "project.update"]
