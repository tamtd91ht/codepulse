"""docs/specs/31: CodePulse's own task tracker, tied to git and code review."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Commit,
    ProjectGrant,
    Review,
    ReviewFinding,
    Task,
    TaskGitLink,
    TaskStatus,
    UserNotification,
)
from app.review.rubric import RUBRIC_VERSION
from app.tasks import git as task_git
from tests.api.conftest import World, grant

pytestmark = pytest.mark.integration

API = "/api/v1"


def bp_of(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def enable(client: TestClient, world: World, as_user: Any, key: str = "CRM") -> dict[str, Any]:
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}", headers=as_user(world.admin), json={"key": key}
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def create(client: TestClient, headers: dict[str, str], world: World, **fields: Any) -> Any:
    res = client.post(
        f"{API}/tasks", headers=headers, json={"space": bp_of(world), "title": "Việc", **fields}
    )
    assert res.status_code == 201, res.text
    return res.json()


def ungrant(session: Session, world: World) -> None:
    """The world grants every person the Demo dự án; the outsider loses it here."""
    session.execute(
        ProjectGrant.__table__.delete().where(ProjectGrant.user_id == world.outsider.id)
    )
    session.flush()


def status_id(space: dict[str, Any], name: str) -> int:
    return next(s["id"] for s in space["statuses"] if s["name"] == name)


def test_enable_a_space_and_number_tasks(client: TestClient, world: World, as_user: Any) -> None:
    an = as_user(world.an)
    # Only one who manages Task on the dự án turns it on.
    res = client.put(f"{API}/tasks/spaces/{bp_of(world)}", headers=an, json={"key": "CRM"})
    assert res.status_code == 403
    bad = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}", headers=as_user(world.admin), json={"key": "crm 1"}
    )
    assert bad.status_code == 422
    space = enable(client, world, as_user)
    assert [s["name"] for s in space["statuses"]] == [
        "Cần làm",
        "Đang làm",
        "Đang review",
        "Xong",
    ]
    assert space["rules"]["start"] == status_id(space, "Đang làm")
    # A member sees it and creates tasks; numbers follow on.
    spaces = client.get(f"{API}/tasks/spaces", headers=an).json()
    assert [(s["key"], s["can_create"], s["can_manage"]) for s in spaces] == [("CRM", True, False)]
    first = create(client, an, world, title="Đăng nhập OTP", type="story")
    second = create(client, an, world, title="Màn hình", parent_key=first["key"], type="subtask")
    assert (first["key"], second["key"]) == ("CRM-1", "CRM-2")
    assert second["parent_key"] == "CRM-1"
    assert first["status"]["name"] == "Cần làm"
    assert first["reporter"]["id"] == world.an.id
    # A sub-task needs a parent; the key cannot change once tasks exist.
    res = client.post(
        f"{API}/tasks", headers=an, json={"space": bp_of(world), "title": "x", "type": "subtask"}
    )
    assert res.status_code == 422
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}", headers=as_user(world.admin), json={"key": "OMI"}
    )
    assert res.status_code == 409
    detail = client.get(f"{API}/tasks/CRM-1", headers=an).json()
    assert [t["key"] for t in detail["subtasks"]] == ["CRM-2"]
    assert detail["subtasks"] and detail["can"]["edit"] is True


def test_member_edits_own_tasks_only(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    an, binh = as_user(world.an), as_user(world.binh)
    task = create(client, an, world, title="Của An")
    # Binh (member, own scope) cannot edit An's task, but can comment on it.
    assert client.patch(f"{API}/tasks/CRM-1", headers=binh, json={"title": "x"}).status_code == 403
    res = client.post(f"{API}/tasks/CRM-1/comments", headers=binh, json={"body": "@an xem giúp"})
    assert res.status_code == 201
    # Given to Binh, it becomes Binh's to edit, and Binh hears of it.
    res = client.patch(
        f"{API}/tasks/CRM-1", headers=an, json={"assignee_id": world.binh.id, "priority": "high"}
    )
    assert res.status_code == 200, res.text
    assert (
        client.patch(f"{API}/tasks/CRM-1", headers=binh, json={"labels": ["api"]}).status_code
        == 200
    )
    kinds = set(
        db_session.scalars(
            select(UserNotification.kind).where(UserNotification.user_id == world.binh.id)
        )
    )
    assert "task.assigned" in kinds
    mentions = db_session.scalars(
        select(UserNotification).where(
            UserNotification.user_id == world.an.id, UserNotification.kind == "task.mentioned"
        )
    ).all()
    assert len(mentions) == 1
    # A manager of the dự án edits any task; only a Task manager deletes.
    grant(db_session, world.lead, bp_of(world), "manage")
    lead = as_user(world.lead)
    assert (
        client.patch(f"{API}/tasks/CRM-1", headers=lead, json={"title": "Đổi"}).status_code == 200
    )
    assert client.delete(f"{API}/tasks/CRM-1", headers=an).status_code == 403
    done = status_id(space, "Xong")
    res = client.post(f"{API}/tasks/CRM-1/transition", headers=binh, json={"status_id": done})
    assert res.status_code == 200
    body = res.json()
    assert body["status"]["name"] == "Xong" and body["resolved_at"]
    fields = [h["field"] for h in body["history"]]
    assert "status" in fields and "assignee" in fields
    assert task["key"] == "CRM-1"


def test_workflow_transitions_and_status_replacement(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    create(client, admin, world)
    todo, doing, review, done = (s["id"] for s in space["statuses"])
    statuses = [
        {"id": todo, "name": "Cần làm", "category": "todo", "transitions": [doing]},
        {"id": doing, "name": "Đang làm", "category": "doing", "transitions": None},
        {"id": done, "name": "Xong", "category": "done", "transitions": None},
    ]
    url = f"{API}/tasks/spaces/{bp_of(world)}/statuses"
    # Dropping Đang review is fine (no task there); renaming keeps ids.
    res = client.put(url, headers=admin, json={"statuses": statuses})
    assert res.status_code == 200, res.text
    assert [s["name"] for s in res.json()] == ["Cần làm", "Đang làm", "Xong"]
    # To Do may only go to Đang làm.
    res = client.post(f"{API}/tasks/CRM-1/transition", headers=admin, json={"status_id": done})
    assert res.status_code == 409
    assert (
        client.post(
            f"{API}/tasks/CRM-1/transition", headers=admin, json={"status_id": doing}
        ).status_code
        == 200
    )
    # Removing a status that holds a task needs a destination.
    res = client.put(url, headers=admin, json={"statuses": [statuses[0], statuses[2]]})
    assert res.status_code == 409
    res = client.put(
        url,
        headers=admin,
        json={"statuses": [statuses[0], statuses[2]], "move": {str(doing): done}},
    )
    assert res.status_code == 200, res.text
    assert client.get(f"{API}/tasks/CRM-1", headers=admin).json()["status"]["name"] == "Xong"
    assert review


def test_git_drives_the_task(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    an = as_user(world.an)
    create(client, an, world, title="OTP", assignee_id=world.an.id)
    db_session.add(Account(kind="git_email", external_key="an@x.vn", user_id=world.an.id))
    db_session.flush()
    project = world.project
    # A branch for the task starts it.
    task_git.on_push(db_session, project, "feature/crm-1-otp", "main", [])
    task = db_session.scalars(select(Task).where(Task.key == "CRM-1")).one()
    assert db_session.get(TaskStatus, task.status_id).name == "Đang làm"  # type: ignore[union-attr]
    # A commit on it: linked once, with its smart commands (by its author, who may edit).
    commit = {
        "id": "c" * 40,
        "message": "feat(auth): gửi OTP [CRM-1] #time 1h #comment xong API",
        "author": {"email": "an@x.vn"},
    }
    task_git.on_push(
        db_session, project, "feature/crm-1-otp", "main", [commit], pusher_email="an@x.vn"
    )
    task_git.on_push(
        db_session, project, "feature/crm-1-otp", "main", [commit], pusher_email="an@x.vn"
    )
    db_session.refresh(task)
    assert task.spent_min == 60
    links = db_session.scalars(select(TaskGitLink).where(TaskGitLink.task_id == task.id)).all()
    assert sorted(link.kind for link in links) == ["branch", "commit"]
    # Another dự án's key, or a key that is no task, links nothing.
    task_git.on_push(
        db_session, project, "main", "main", [{"id": "d" * 40, "message": "x [OMI-1] [CRM-99]"}]
    )
    # The MR opens: In Review. It merges into main with the key in its title: Done.
    mr = SimpleNamespace(
        iid=7,
        title="[CRM-1] Gửi OTP",
        description="",
        source_branch="feature/crm-1-otp",
        target_branch="main",
        state="opened",
        commits=[],
    )
    data = SimpleNamespace(default_branch="main", merge_requests={7: mr}, commits={})
    task_git.on_sync(db_session, project, data)
    db_session.refresh(task)
    assert db_session.get(TaskStatus, task.status_id).name == "Đang review"  # type: ignore[union-attr]
    mr.state = "merged"
    task_git.on_sync(db_session, project, data)
    db_session.refresh(task)
    assert db_session.get(TaskStatus, task.status_id).name == "Xong"  # type: ignore[union-attr]
    assert task.resolved_at is not None
    # The detail shows the code.
    git = client.get(f"{API}/tasks/CRM-1", headers=an).json()["git"]
    assert git["summary"]["commits"] == 1 and git["summary"]["mrs"] == 1
    assert git["branches"][0]["branch"] == "feature/crm-1-otp"
    history = client.get(f"{API}/tasks/CRM-1", headers=an).json()["history"]
    assert {h["via"] for h in history} >= {"git", "smart_commit"}
    assert space["rules"]["done_on_merge"] is True


def test_a_closed_mr_reopens_and_fixes_on_main_finish(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    create(client, admin, world)
    create(client, admin, world)
    project = world.project
    mr = SimpleNamespace(
        iid=8,
        title="[CRM-1] x",
        description=None,
        source_branch="feature/CRM-1-x",
        target_branch="main",
        state="opened",
        commits=[],
    )
    data = SimpleNamespace(default_branch="main", merge_requests={8: mr}, commits={})
    task_git.on_sync(db_session, project, data)
    mr.state = "closed"
    task_git.on_sync(db_session, project, data)
    first = client.get(f"{API}/tasks/CRM-1", headers=admin).json()
    assert first["status"]["name"] == "Đang làm"
    # A fixing commit pushed straight to main finishes CRM-2.
    task_git.on_push(
        db_session, project, "main", "main", [{"id": "e" * 40, "message": "fix: lỗi [CRM-2]"}]
    )
    assert client.get(f"{API}/tasks/CRM-2", headers=admin).json()["status"]["name"] == "Xong"


def test_critical_findings_gate(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={"key": "CRM", "rules": {"block_done_on_critical": True}},
    )
    create(client, admin, world)
    sha = "f" * 40
    db_session.add(Commit(sha=sha, project_id=world.project.id, message="feat: x [CRM-1]"))
    db_session.flush()
    review = Review(
        target_type="commit",
        commit_sha=sha,
        reviewed_sha=sha,
        status="done",
        score=Decimal(40),
        finished_at=datetime.now(UTC),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(
        ReviewFinding(review_id=review.id, severity="critical", category="security", title="SQLi")
    )
    db_session.flush()
    res = client.post(
        f"{API}/tasks/CRM-1/git", headers=admin, json={"kind": "commit", "sha": sha[:8]}
    )
    assert res.status_code == 201, res.text
    row = client.get(f"{API}/tasks?space={bp_of(world)}", headers=admin).json()["items"][0]
    assert row["git"]["open_critical"] == 1
    done = status_id(space, "Xong")
    res = client.post(f"{API}/tasks/CRM-1/transition", headers=admin, json={"status_id": done})
    assert res.status_code == 409
    detail = client.get(f"{API}/tasks/CRM-1", headers=admin).json()
    assert detail["git"]["commits"][0]["review"]["open_critical"] == 1
    # The review page finds the task of its commit.
    linked = client.get(
        f"{API}/tasks/linked?project_id={world.project.id}&sha={sha}", headers=admin
    )
    assert [t["key"] for t in linked.json()] == ["CRM-1"]


def test_board_sprints_reports_versions(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    for title, points in (("A", 3), ("B", 5), ("C", 2)):
        create(client, admin, world, title=title, story_points=points)
    bp = bp_of(world)
    # Board order: C before A.
    res = client.post(f"{API}/tasks/CRM-3/move", headers=admin, json={"before_key": "CRM-1"})
    assert res.status_code == 200, res.text
    keys = [
        t["key"]
        for t in client.get(f"{API}/tasks?space={bp}&sort=rank", headers=admin).json()["items"]
    ]
    assert keys == ["CRM-3", "CRM-1", "CRM-2"]
    # A sprint with A and B, started, A done, completed: B goes back to the backlog.
    sprint = client.post(f"{API}/tasks/spaces/{bp}/sprints", headers=admin, json={}).json()
    assert sprint["name"] == "CRM Sprint 1"
    res = client.post(
        f"{API}/tasks/bulk",
        headers=admin,
        json={"keys": ["CRM-1", "CRM-2"], "set": {"sprint_id": sprint["id"]}},
    )
    assert res.json()["updated"] == 2
    today = date.today()
    res = client.post(
        f"{API}/tasks/sprints/{sprint['id']}/start",
        headers=admin,
        json={
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=13)).isoformat(),
        },
    )
    assert res.status_code == 200 and res.json()["committed_points"] == 8.0
    active = client.get(f"{API}/tasks?space={bp}&sprint=active", headers=admin).json()
    assert active["total"] == 2
    client.post(
        f"{API}/tasks/CRM-1/transition",
        headers=admin,
        json={"status_id": status_id(space, "Xong")},
    )
    burn = client.get(f"{API}/tasks/sprints/{sprint['id']}/burndown", headers=admin).json()
    assert burn["committed_points"] == 8.0
    assert burn["days"][0]["remaining_points"] == 5.0
    res = client.post(
        f"{API}/tasks/sprints/{sprint['id']}/complete", headers=admin, json={"move_to": None}
    )
    assert res.json()["moved"] == 1
    backlog = client.get(f"{API}/tasks?space={bp}&sprint=backlog", headers=admin).json()
    assert {t["key"] for t in backlog["items"]} == {"CRM-2", "CRM-3"}
    report = client.get(f"{API}/tasks/spaces/{bp}/reports", headers=admin).json()
    assert report["velocity"] == [
        {"sprint_id": sprint["id"], "name": "CRM Sprint 1", "committed": 8.0, "completed": 3.0}
    ]
    assert sum(d["created"] for d in report["flow"]) == 3
    # A version's release notes list its finished tasks.
    version = client.post(
        f"{API}/tasks/spaces/{bp}/versions", headers=admin, json={"name": "1.0"}
    ).json()
    client.patch(f"{API}/tasks/CRM-1", headers=admin, json={"version_id": version["id"]})
    notes = client.get(f"{API}/tasks/versions/{version['id']}/notes", headers=admin).json()
    assert "**CRM-1** A" in notes["markdown"]


def test_attachments_filters_links_worklogs(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TASK_FILES_DIR", str(tmp_path))
    enable(client, world, as_user)
    an = as_user(world.an)
    create(client, an, world)
    create(client, an, world)
    res = client.post(
        f"{API}/tasks/CRM-1/attachments?filename=log.txt",
        headers={**an, "content-type": "text/plain"},
        content=b"hello",
    )
    assert res.status_code == 201, res.text
    got = client.get(f"{API}/tasks/attachments/{res.json()['id']}", headers=an)
    assert got.content == b"hello"
    ungrant(db_session, world)
    assert client.get(
        f"{API}/tasks/attachments/{res.json()['id']}", headers=as_user(world.outsider)
    ).status_code in (403, 404)
    res = client.post(
        f"{API}/tasks/CRM-1/links", headers=an, json={"key": "CRM-2", "kind": "blocks"}
    )
    assert res.status_code == 201
    other = client.get(f"{API}/tasks/CRM-2", headers=an).json()
    assert other["links"][0]["direction"] == "in" and other["links"][0]["task"]["key"] == "CRM-1"
    res = client.post(f"{API}/tasks/CRM-1/worklogs", headers=an, json={"minutes": 45})
    assert res.json()["spent_min"] == 45
    res = client.post(
        f"{API}/tasks/filters", headers=an, json={"name": "Của tôi", "query": {"assignee": ["me"]}}
    )
    assert res.status_code == 201
    assert [f["name"] for f in client.get(f"{API}/tasks/filters", headers=an).json()] == ["Của tôi"]
    mine = client.get(f"{API}/tasks?reporter=me&q=crm-2", headers=an).json()
    assert [t["key"] for t in mine["items"]] == ["CRM-2"]


def test_no_task_view_no_tasks(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    # The outsider is not granted the dự án: it does not exist for them.
    out = as_user(world.outsider)
    ungrant(db_session, world)
    create(client, as_user(world.admin), world)
    assert client.get(f"{API}/tasks/CRM-1", headers=out).status_code in (403, 404)
    assert client.get(f"{API}/tasks", headers=out).json()["total"] == 0


def test_git_rules_resist_forgery_old_history_and_branches(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """Review findings: smart commands need the author to push; commits of other branches
    seen by the collector never finish a task; a fix reaching main later does; history older
    than the Task space is not linked."""
    enable(client, world, as_user)
    admin = as_user(world.admin)
    create(client, admin, world, assignee_id=world.an.id)
    create(client, admin, world)
    db_session.add(Account(kind="git_email", external_key="an@x.vn", user_id=world.an.id))
    db_session.flush()
    project = world.project
    fix = {"id": "1" * 40, "message": "fix: x [CRM-1] #done", "author": {"email": "an@x.vn"}}
    # Pushed by someone else with An's email: linked, but #done is not run as An.
    task_git.on_push(db_session, project, "feature/x", "main", [fix], pusher_email="binh@x.vn")
    first = client.get(f"{API}/tasks/CRM-1", headers=admin).json()
    assert first["status"]["name"] == "Đang làm"
    # The collector lists commits of every branch: a fix there does not finish CRM-2.
    gl = SimpleNamespace(
        sha="2" * 40,
        message="fix: y [CRM-2]",
        author_email="an@x.vn",
        authored_at=datetime.now(UTC),
        is_merge=False,
    )
    task_git.on_sync(
        db_session,
        project,
        SimpleNamespace(default_branch="main", merge_requests={}, commits={gl.sha: gl}),
    )
    assert client.get(f"{API}/tasks/CRM-2", headers=admin).json()["status"]["name"] == "Đang làm"
    # The same fix pushed to main later finishes it.
    task_git.on_push(db_session, project, "main", "main", [{"id": gl.sha, "message": gl.message}])
    assert client.get(f"{API}/tasks/CRM-2", headers=admin).json()["status"]["name"] == "Xong"
    # A commit from long before Task was turned on (a reused key) is not linked.
    old = {
        "id": "3" * 40,
        "message": "feat: cũ [CRM-1]",
        "timestamp": (datetime.now(UTC) - timedelta(days=400)).isoformat(),
    }
    task_git.on_push(db_session, project, "main", "main", [old])
    shas = {
        c["sha"] for c in client.get(f"{API}/tasks/CRM-1", headers=admin).json()["git"]["commits"]
    }
    assert "3" * 40 not in shas and "1" * 40 in shas
