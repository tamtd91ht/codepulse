"""docs/specs/31 §21.6 (round 3, stage 3.5 "Chuyển đổi"): old Jira keys still used in git are
recorded and listed, "Ngừng dùng Jira" per dự án, the Lead checklist, demo data."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Issue,
    JiraProject,
    Sprint,
    Task,
    TaskImport,
    TaskSpace,
    TaskStatus,
    TaskVersion,
    UserNotification,
)
from app.models.tasks import TaskKeyAliasSeen
from app.tasks import git as task_git
from app.testcases import bugs
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable
from tests.api.test_testcase_bugs import _project, _setup

pytestmark = pytest.mark.integration


def _imported(db: Session, task_id: int, key: str = "OMX-5") -> None:
    db.add(TaskImport(source="jira", external_key=key, task_id=task_id))
    db.flush()


def _state(client: TestClient, h: dict[str, str], world: World) -> Any:
    got = client.get(f"{API}/tasks/spaces/{bp_of(world)}/switchover", headers=h)
    assert got.status_code == 200, got.text
    return got.json()


def test_old_jira_keys_in_git_are_recorded_once_and_listed(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    task = create(client, admin, world, title="Gửi OTP")
    _imported(db_session, task["id"])
    project = world.project
    commit = {
        "id": "a" * 40,
        "message": "fix(otp): hạn mã [OMX-5]",
        "author": {"email": "an@x.vn"},
        "timestamp": datetime.now(UTC).isoformat(),
    }
    for _ in range(2):  # the webhook may deliver twice: one row per commit / branch
        task_git.on_push(
            db_session, project, "feature/omx-5-otp", "main", [commit], pusher_email="an@x.vn"
        )
    mr = SimpleNamespace(
        iid=9,
        title="OMX-5 hạn OTP",
        description="",
        source_branch="feature/omx-5-otp",
        target_branch="main",
        state="opened",
        commits=[],
        author=SimpleNamespace(id=777, username="an.gl"),
        created_at=datetime.now(UTC),
    )
    data = SimpleNamespace(default_branch="main", merge_requests={9: mr}, commits={})
    task_git.on_sync(db_session, project, data)
    task_git.on_sync(db_session, project, data)
    rows = db_session.scalars(select(TaskKeyAliasSeen)).all()
    assert sorted(r.kind for r in rows) == ["branch", "commit", "mr"]
    by_kind = {r.kind: r for r in rows}
    assert by_kind["commit"].sha == "a" * 40 and by_kind["commit"].author_user_id == world.an.id
    assert by_kind["mr"].mr_iid == 9 and by_kind["mr"].author == "an.gl"
    assert all(r.external_key == "OMX-5" and r.task_id == task["id"] for r in rows)
    # Its author is told once a day which key to use.
    notes = db_session.scalars(
        select(UserNotification).where(UserNotification.user_id == world.an.id)
    ).all()
    alias_notes = [n for n in notes if (n.ref or "").startswith("jira-alias:")]
    assert len(alias_notes) == 1
    assert alias_notes[0].title == f"Dùng {task['key']} thay cho OMX-5"
    # The task's own key is no alias: nothing recorded.
    task_git.on_push(
        db_session,
        project,
        "main",
        "main",
        [{"id": "b" * 40, "message": f"x [{task['key']}]", "author": {"email": "an@x.vn"}}],
    )
    assert len(db_session.scalars(select(TaskKeyAliasSeen)).all()) == 3
    state = _state(client, admin, world)
    uses = state["uses"]
    assert uses["total"] == 3 and uses["days"] == 7
    commit_use = next(i for i in uses["items"] if i["kind"] == "commit")
    assert commit_use["task_key"] == task["key"] and commit_use["repo"] == "demo/p"
    assert commit_use["author"]["id"] == world.an.id
    # Only who manages Task on the dự án.
    url = f"{API}/tasks/spaces/{bp_of(world)}/switchover"
    assert client.get(url, headers=as_user(world.an)).status_code == 403


def test_retire_jira_needs_readiness_or_a_reason_and_forces_codepulse_bugs(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bugs.clear_cache()
    enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    # Jira chosen for bugs, one synced Jira issue not imported, an old key used today.
    _project(client, admin, bp, bug_target="jira", jira_project_key="OMX")
    jp = JiraProject(key="OMX", name="OMX")
    db_session.add(jp)
    db_session.flush()
    db_session.add(
        Issue(
            id=55_001,
            source="jira",
            jira_project_id=jp.id,
            iid=1,
            key="OMX-1",
            title="Việc cũ",
            web_url="https://jira.example.vn/browse/OMX-1",
            state="opened",
            created_at_src=datetime(2026, 10, 1, tzinfo=UTC),
            business_project_ids=[bp],
        )
    )
    task = create(client, admin, world, title="Việc")
    _imported(db_session, task["id"], "OMX-9")
    task_git.on_push(
        db_session,
        world.project,
        "main",
        "main",
        [{"id": "c" * 40, "message": "x OMX-9", "author": {"email": "an@x.vn"}}],
    )
    state = _state(client, admin, world)
    ready = state["readiness"]
    assert ready["ready"] is False and ready["not_imported"] == 1 and ready["alias_uses"] == 1
    assert state["jira_retired_at"] is None
    url = f"{API}/tasks/spaces/{bp}/switchover/retire-jira"
    assert client.post(url, headers=as_user(world.an), json={}).status_code == 403
    refused = client.post(url, headers=admin, json={})
    assert refused.status_code == 409 and "lý do" in refused.json()["detail"]
    assert client.post(url, headers=admin, json={"reason": "ngắn"}).status_code == 409
    done = client.post(url, headers=admin, json={"reason": "Đã chạy song song đủ một sprint"})
    assert done.status_code == 200, done.text
    assert done.json()["jira_retired_at"] is not None
    assert done.json()["jira_retired_by"]["id"] == world.admin.id
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "task.jira.retire")).one()
    assert row.details is not None and row.details["override"] is True
    assert row.details["reason"] == "Đã chạy song song đủ một sprint"
    assert client.post(url, headers=admin, json={"reason": "lần hai cũng vậy"}).status_code == 409
    # Testcase settings: Jira cannot be chosen; a bug goes to CodePulse.
    settings = client.get(
        f"{API}/testcases/project-settings", headers=admin, params={"business_project_id": bp}
    ).json()
    assert settings["jira_retired"] is True
    assert settings["bug_target"] == "codepulse" and settings["bug_target_effective"] == "codepulse"
    put = client.put(
        f"{API}/testcases/project-settings",
        headers=admin,
        json={"business_project_id": bp, "bug_target": "jira"},
    )
    assert put.status_code == 422
    s = _setup(client, db_session, admin, bp)
    got = client.post(f"{API}/testcases/tests/{s['test']['id']}/bug", headers=admin, json={})
    assert got.status_code == 201, got.text
    assert got.json()["key"].startswith("CRM-")
    # Undo: Jira is chosen again.
    back = client.delete(url, headers=admin)
    assert back.status_code == 200 and back.json()["jira_retired_at"] is None
    settings = client.get(
        f"{API}/testcases/project-settings", headers=admin, params={"business_project_id": bp}
    ).json()
    assert settings["jira_retired"] is False and settings["bug_target"] == "jira"


def test_retire_when_ready_needs_no_reason(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    state = _state(client, admin, world)
    assert state["readiness"]["ready"] is True
    url = f"{API}/tasks/spaces/{bp_of(world)}/switchover/retire-jira"
    got = client.post(url, headers=admin, json={})
    assert got.status_code == 200, got.text


def test_lead_checklist_ticks_from_data(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    items = {i["key"]: i for i in _state(client, admin, world)["checklist"]["items"]}
    assert items["wip"]["done"] is False
    assert items["sprint"]["done"] is False and items["sprint"]["applies"] is True
    assert items["jira_import"]["applies"] is False  # no Jira synced for the dự án
    assert items["members"]["done"] is True  # five people granted, no committer left out
    doing = db_session.scalars(
        select(TaskStatus).where(
            TaskStatus.business_project_id == bp, TaskStatus.category == "doing"
        )
    ).first()
    assert doing is not None
    doing.wip_limit, doing.age_warning_hours = 3, 48
    space = db_session.get(TaskSpace, bp)
    assert space is not None
    space.rules = {**(space.rules or {}), "dod": {"checks": ["mr_merged"], "min_score": 7}}
    db_session.add(Sprint(business_project_id=bp, name="Sprint 1"))
    db_session.flush()
    rules = client.post(f"{API}/tasks/spaces/{bp}/switchover/recommended-rules", headers=admin)
    assert rules.status_code == 200, rules.text
    checklist = rules.json()["checklist"]
    items = {i["key"]: i for i in checklist["items"]}
    assert items["wip"]["done"] and items["automation"]["done"] and items["sprint"]["done"]
    assert items["dod"]["done"]
    assert checklist["complete"] is True and checklist["done"] == checklist["total"]


def test_demo_data_is_created_and_removed_without_touching_other_tasks(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    url = f"{API}/tasks/spaces/{bp}/demo"
    assert client.post(url, headers=as_user(world.an)).status_code == 403
    real = create(client, admin, world, title="Việc thật", labels=["mau"])
    got = client.post(url, headers=admin)
    assert got.status_code == 201, got.text
    out = got.json()
    assert out["created"] >= 7
    demo = db_session.scalars(select(Task).where(Task.key.in_(out["keys"]))).all()
    assert all(t.title.startswith("[Mẫu] ") and "mau-demo" in t.labels for t in demo)
    assert {t.type for t in demo} >= {"epic", "story", "task", "bug", "subtask"}
    assert db_session.get(Sprint, out["sprint_id"]) is not None
    assert _state(client, admin, world)["demo_tasks"] == out["created"]
    # A second set while one exists: refused.
    assert client.post(url, headers=admin).status_code == 409
    # A real sub-task under a demo story survives the removal (detached).
    story = next(t for t in demo if t.type == "story")
    child = create(client, admin, world, title="Con thật", type="subtask", parent_key=story.key)
    assert client.delete(url, headers=as_user(world.an)).status_code == 403
    gone = client.delete(url, headers=admin)
    assert gone.status_code == 200, gone.text
    # The real sub-task still sits in the demo sprint / version (from its parent): they stay.
    assert gone.json() == {"deleted": out["created"], "sprints": 0, "versions": 0}
    db_session.expire_all()
    left = {
        t.key: t for t in db_session.scalars(select(Task).where(Task.business_project_id == bp))
    }
    assert set(left) == {real["key"], child["key"]}
    assert left[child["key"]].parent_id is None
    assert client.delete(f"{API}/tasks/{child['key']}", headers=admin).status_code == 204
    again = client.delete(url, headers=admin).json()
    assert again == {"deleted": 0, "sprints": 1, "versions": 1}
    db_session.expire_all()
    assert db_session.get(Sprint, out["sprint_id"]) is None
    assert db_session.get(TaskVersion, out["version_id"]) is None
    assert _state(client, admin, world)["demo_tasks"] == 0


def test_demo_removal_follows_the_dpi_projection(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from tests.api.test_tasks_dpi import rows

    enable(client, world, as_user)
    admin = as_user(world.admin)
    res = client.put(f"{API}/dpi/jira/source", headers=admin, json={"source": "codepulse"})
    assert res.status_code == 200, res.text
    url = f"{API}/tasks/spaces/{bp_of(world)}/demo"
    out = client.post(url, headers=admin).json()
    # A task labelled by hand without the "[Mẫu]" title is real work: never removed.
    real = create(client, admin, world, title="Việc thật", labels=["mau-demo"])
    assert set(out["keys"]) & set(rows(db_session))
    assert client.delete(url, headers=admin).json()["deleted"] == out["created"]
    left = set(rows(db_session))
    assert not left & set(out["keys"])
    assert db_session.scalars(select(Task).where(Task.key == real["key"])).one()
