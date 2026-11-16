"""docs/specs/31 §19.2 (roadmap P1, leaving Jira): a bug from a failed test becomes a CodePulse
task, task bugs answer the bug states and "Cần retest", and synced Jira issues import as tasks
whose old keys keep resolving."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Issue, JiraProject, TaskImport, TaskTestLink, TestResult
from app.tasks import git as task_git
from app.testcases import bugs
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, enable
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration


def test_a_failed_test_raises_a_codepulse_bug(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bugs.clear_cache()
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp_of(world))
    settings = client.get(
        f"{API}/testcases/project-settings",
        headers=admin,
        params={"business_project_id": bp_of(world)},
    ).json()
    assert settings["bug_target_effective"] == "codepulse" and settings["task_space_key"] == "CRM"
    url = f"{API}/testcases/tests/{s['test']['id']}/bug"
    got = client.post(url, headers=admin, json={"priority": "high", "assignee_id": world.an.id})
    assert got.status_code == 201, got.text
    out = got.json()
    key = out["key"]
    assert key.startswith("CRM-") and out["web_url"] == f"/tasks/{key}"
    task = client.get(f"{API}/tasks/{key}", headers=admin).json()
    assert task["type"] == "bug" and task["priority"] == "high"
    assert task["assignee"]["id"] == world.an.id
    assert "### Các bước" in task["description"] and "Lỗi 500" in task["description"]
    assert "Tạo từ testcase" in task["description"]
    # The result names it; a link ties the task to the case.
    defects = db_session.scalar(
        select(TestResult.defects).where(TestResult.test_id == s["test"]["id"])
    )
    assert defects == [key]
    link = db_session.scalars(select(TaskTestLink)).one()
    assert link.kind == "bug" and link.case_id == s["case"].id
    # One bug per failed result.
    assert client.post(url, headers=admin, json={}).status_code == 409
    # Its state comes from the task: open, then closed with the run asking for a retest.
    states = client.get(
        f"{API}/testcases/bugs/status",
        headers=admin,
        params={"business_project_id": bp_of(world), "keys": key},
    ).json()
    assert states[0]["state"] == "opened" and states[0]["source"] == "codepulse"
    done = next(st["id"] for st in space["statuses"] if st["category"] == "done")
    client.post(f"{API}/tasks/{key}/transition", headers=admin, json={"status_id": done})
    states = client.get(
        f"{API}/testcases/bugs/status",
        headers=admin,
        params={"business_project_id": bp_of(world), "keys": key},
    ).json()
    assert states[0]["state"] == "closed"
    tests = client.get(f"{API}/testcases/runs/{s['run']['id']}/tests", headers=admin).json()
    assert tests["items"][0]["needs_retest"] is True


def test_linking_an_existing_task_as_the_bug(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp_of(world))
    bug = client.post(
        f"{API}/tasks", headers=admin, json={"space": bp_of(world), "title": "Lỗi lưu"}
    ).json()
    got = client.post(
        f"{API}/testcases/tests/{s['test']['id']}/bug/link", headers=admin, json={"key": bug["key"]}
    )
    assert got.status_code == 200, got.text
    assert got.json()["verified"] is True
    assert db_session.scalars(select(TaskTestLink)).one().task_id == bug["id"]


def _jira_issue(db: Session, world: World, jp: JiraProject, n: int, **kw: Any) -> Issue:
    issue = Issue(
        id=10_000 + n + jp.id * 1000,
        source="jira",
        jira_project_id=jp.id,
        iid=n,
        key=kw.pop("key", f"{jp.key}-{n}"),
        title=kw.pop("title", f"Việc {n}"),
        state=kw.pop("state", "opened"),
        created_at_src=datetime(2026, 10, n, tzinfo=UTC),
        web_url=f"https://jira.example.vn/browse/{jp.key}-{n}",
        business_project_ids=[bp_of(world)],
        **kw,
    )
    db.add(issue)
    return issue


def test_import_jira_issues_as_tasks(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.models import TaskSpace

    enable(client, world, as_user)
    admin = as_user(world.admin)
    jp = JiraProject(key="OMX", name="OMX")
    db_session.add(jp)
    db_session.flush()
    _jira_issue(db_session, world, jp, 1, type="feature", story_points=5, labels=["api"])
    _jira_issue(
        db_session,
        world,
        jp,
        2,
        type="bug",
        state="closed",
        closed_at_src=datetime(2026, 10, 9, tzinfo=UTC),
    )
    _jira_issue(db_session, world, jp, 3, parent_key="OMX-1")
    db_session.flush()
    base = f"{API}/tasks/spaces/{bp_of(world)}/import/jira"
    # Only who manages Task on the dự án.
    assert client.get(base, headers=as_user(world.an)).status_code == 403
    listed = client.get(base, headers=admin).json()
    assert listed["sources"] == [{"project_key": "OMX", "issues": 3, "imported": 0}]
    preview = client.post(base, headers=admin, json={"project_key": "OMX"}).json()
    assert preview["to_create"] == 3 and preview["preview"][0]["jira_key"] == "OMX-1"
    assert db_session.scalars(select(TaskImport)).first() is None  # a dry run writes nothing
    done = client.post(base, headers=admin, json={"project_key": "OMX", "dry_run": False}).json()
    assert done["created"] == 3, done
    mapping = {m["jira_key"]: m["task_key"] for m in done["mapping"]}
    story = client.get(f"{API}/tasks/{mapping['OMX-1']}", headers=admin).json()
    assert story["type"] == "story" and story["story_points"] == 5 and story["labels"] == ["api"]
    assert "OMX-1" in story["description"]
    closed = client.get(f"{API}/tasks/{mapping['OMX-2']}", headers=admin).json()
    assert closed["type"] == "bug" and closed["status"]["category"] == "done"
    assert closed["resolution"] == "done"
    child = client.get(f"{API}/tasks/{mapping['OMX-3']}", headers=admin).json()
    assert child["type"] == "subtask" and child["parent_key"] == mapping["OMX-1"]
    # Again: nothing new.
    again = client.post(base, headers=admin, json={"project_key": "OMX", "dry_run": False}).json()
    assert again["created"] == 0 and again["skipped"] == 3
    # The old key still names the task in git.
    space = db_session.get(TaskSpace, bp_of(world))
    assert space is not None
    found = task_git._tasks(db_session, space, ["OMX-1"])
    assert found["OMX-1"].key == mapping["OMX-1"]


def test_import_reads_epics_and_refuses_clashing_keys(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    jp = JiraProject(key="OMX", name="OMX")
    db_session.add(jp)
    db_session.flush()
    # Epic OMX-1 > Story OMX-2 > Sub-task OMX-3 (Jira's `parent` for both levels).
    _jira_issue(db_session, world, jp, 1, title="Thanh toán")
    _jira_issue(db_session, world, jp, 2, type="feature", parent_key="OMX-1", story_points=3)
    _jira_issue(db_session, world, jp, 3, parent_key="OMX-2")
    db_session.flush()
    base = f"{API}/tasks/spaces/{bp_of(world)}/import/jira"
    done = client.post(base, headers=admin, json={"project_key": "OMX", "dry_run": False})
    assert done.status_code == 200, done.text
    mapping = {m["jira_key"]: m["task_key"] for m in done.json()["mapping"]}
    epic = client.get(f"{API}/tasks/{mapping['OMX-1']}", headers=admin).json()
    story = client.get(f"{API}/tasks/{mapping['OMX-2']}", headers=admin).json()
    sub = client.get(f"{API}/tasks/{mapping['OMX-3']}", headers=admin).json()
    assert epic["type"] == "epic"
    assert story["type"] == "story" and story["parent_key"] == epic["key"]
    assert sub["type"] == "subtask" and sub["parent_key"] == story["key"]
    # A Jira project whose key is a Task key: refused (old keys would name two things).
    crm = JiraProject(key="CRM", name="CRM")
    db_session.add(crm)
    db_session.flush()
    _jira_issue(db_session, world, crm, 9, key="CRM-900")
    db_session.flush()
    res = client.post(base, headers=admin, json={"project_key": "CRM", "dry_run": False})
    assert res.status_code == 409
    # And a new Task key equal to an imported Jira prefix is refused.
    other = client.put(f"{API}/tasks/spaces/{bp_of(world)}", headers=admin, json={"key": "OMX"})
    assert other.status_code == 409
