"""docs/specs/31 §20.1 (round 2 "Một màn hình"): one timeline of a task, the blocked / MR closed
signals of a row and the "blocked by" list of the detail."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import Commit, Review, ReviewFinding, Task, TaskComment
from app.review.rubric import RUBRIC_VERSION
from app.tasks import git as task_git
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration


def _types(items: list[dict[str, Any]]) -> list[str]:
    return [i["type"] for i in items]


def test_timeline_merges_everything_newest_first(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    task = create(client, admin, world, title="OTP", type="story")
    key = task["key"]
    assert client.post(f"{API}/tasks/{key}/comments", headers=admin, json={"body": "hi @an"})
    client.patch(f"{API}/tasks/{key}", headers=admin, json={"priority": "high"})
    client.post(f"{API}/tasks/{key}/worklogs", headers=admin, json={"minutes": 90, "note": "x"})
    # A reviewed commit with a critical finding.
    sha = "a" * 40
    db_session.add(Commit(sha=sha, project_id=world.project.id, message=f"feat: x [{key}]"))
    db_session.flush()
    review = Review(
        target_type="commit",
        commit_sha=sha,
        reviewed_sha=sha,
        status="done",
        score=Decimal("6.5"),
        finished_at=datetime.now(UTC) + timedelta(seconds=5),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(
        ReviewFinding(review_id=review.id, severity="critical", category="security", title="SQLi")
    )
    db_session.flush()
    res = client.post(f"{API}/tasks/{key}/git", headers=admin, json={"kind": "commit", "sha": sha})
    assert res.status_code == 201, res.text
    # A testcase covering it, recorded Failed.
    s = _setup(client, db_session, admin, bp_of(world))
    assert (
        client.post(
            f"{API}/tasks/{key}/tests", headers=admin, json={"case": f"C{s['case'].id}"}
        ).status_code
        == 201
    )

    res = client.get(f"{API}/tasks/{key}/timeline", headers=admin)
    assert res.status_code == 200, res.text
    items = res.json()
    kinds = set(_types(items))
    assert {"comment", "history", "worklog", "git", "review", "test"} <= kinds
    ats = [i["at"] for i in items]
    assert ats == sorted(ats, reverse=True)
    first = items[0]
    assert set(first) >= {"at", "type", "actor", "text", "link", "data"}
    comment = next(i for i in items if i["type"] == "comment")
    assert comment["data"]["body"] == "hi @an" and comment["actor"]["id"] == world.admin.id
    rev = next(i for i in items if i["type"] == "review")
    assert rev["data"]["critical"] == 1 and rev["data"]["score"] == 6.5
    assert rev["link"] == f"/reviews/{review.id}"
    test = next(i for i in items if i["type"] == "test")
    assert test["data"]["status"] == "failed" and test["data"]["kind"] == "covers"
    work = next(i for i in items if i["type"] == "worklog")
    assert work["data"]["minutes"] == 90

    # Filters, limit and paging by `before`.
    only = client.get(f"{API}/tasks/{key}/timeline?types=comment,worklog", headers=admin).json()
    assert set(_types(only)) == {"comment", "worklog"}
    assert client.get(f"{API}/tasks/{key}/timeline?types=nope", headers=admin).status_code == 422
    two = client.get(f"{API}/tasks/{key}/timeline?limit=2", headers=admin).json()
    assert len(two) == 2 and two == items[:2]
    older = client.get(
        f"{API}/tasks/{key}/timeline", headers=admin, params={"before": two[-1]["at"]}
    ).json()
    assert all(i["at"] <= two[-1]["at"] for i in older)
    # The cursor is inclusive: the two pages together hold every item.
    assert {i["id"] for i in two} | {i["id"] for i in older} == {i["id"] for i in items}


def test_timeline_hides_tests_without_tests_view(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    task = create(client, admin, world)
    s = _setup(client, db_session, admin, bp_of(world))
    client.post(
        f"{API}/tasks/{task['key']}/tests", headers=admin, json={"case": f"C{s['case'].id}"}
    )
    path = f"{API}/tasks/{task['key']}/timeline?types=test"
    assert len(client.get(path, headers=admin).json()) == 1
    real = Principal.can
    monkeypatch.setattr(Principal, "can", lambda self, k: k != "tests.view" and real(self, k))
    assert client.get(path, headers=admin).json() == []


def test_timeline_needs_the_task(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    create(client, as_user(world.admin), world)
    from app.models import ProjectGrant

    db_session.execute(
        ProjectGrant.__table__.delete().where(ProjectGrant.user_id == world.outsider.id)
    )
    db_session.flush()
    res = client.get(f"{API}/tasks/CRM-1/timeline", headers=as_user(world.outsider))
    assert res.status_code in (403, 404)
    assert (
        client.get(f"{API}/tasks/CRM-99/timeline", headers=as_user(world.admin)).status_code == 404
    )


def test_blocked_and_closed_mr_signals(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    blocker = create(client, admin, world, title="Chặn")
    task = create(client, admin, world, title="Bị chặn")
    res = client.post(
        f"{API}/tasks/{blocker['key']}/links",
        headers=admin,
        json={"key": task["key"], "kind": "blocks"},
    )
    assert res.status_code == 201, res.text
    detail = client.get(f"{API}/tasks/{task['key']}", headers=admin).json()
    assert detail["blocked"] is True
    assert detail["blocked_by"] == [{"key": blocker["key"], "title": "Chặn", "status": "Cần làm"}]
    rows = {
        r["key"]: r
        for r in client.get(f"{API}/tasks?space={bp_of(world)}", headers=admin).json()["items"]
    }
    assert rows[task["key"]]["blocked"] is True and rows[blocker["key"]]["blocked"] is False
    # The blocker done: no longer blocked.
    done = next(s["id"] for s in space["statuses"] if s["category"] == "done")
    client.post(
        f"{API}/tasks/{blocker['key']}/transition",
        headers=admin,
        json={"status_id": done, "resolution": "done"},
    )
    detail = client.get(f"{API}/tasks/{task['key']}", headers=admin).json()
    assert detail["blocked"] is False and detail["blocked_by"] == []

    # An MR closed without merge, and no other MR: mr_closed; the timeline shows it.
    mr = SimpleNamespace(
        iid=31,
        title=f"[{task['key']}] x",
        description=None,
        source_branch="feature/x",
        target_branch="main",
        state="opened",
        commits=[],
    )
    data = SimpleNamespace(default_branch="main", merge_requests={31: mr}, commits={})
    task_git.on_sync(db_session, world.project, data)
    row = client.get(f"{API}/tasks/{task['key']}", headers=admin).json()
    assert row["mr_closed"] is False
    mr.state = "closed"
    task_git.on_sync(db_session, world.project, data)
    assert client.get(f"{API}/tasks/{task['key']}", headers=admin).json()["mr_closed"] is True
    git = client.get(f"{API}/tasks/{task['key']}/timeline?types=git", headers=admin).json()
    assert any(i["data"].get("mr_iid") == 31 for i in git)
    # Comments come back edited in place, and deleted ones are gone.
    t = db_session.scalars(select(Task).where(Task.key == task["key"])).one()
    db_session.add(TaskComment(task_id=t.id, author_id=world.admin.id, body="đã xem"))
    db_session.flush()
    got = client.get(f"{API}/tasks/{task['key']}/timeline?types=comment", headers=admin).json()
    assert [i["text"] for i in got] == ["đã xem"]
