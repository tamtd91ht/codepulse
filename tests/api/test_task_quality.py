"""docs/specs/31 §19.4 (roadmap P3, closing the quality loop): testcases covering a task, the
Definition of Done, release readiness, retest asked when a bug from a test is fixed."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, UserNotification
from app.testcases import bugs
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration


def _done(space: dict[str, Any]) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == "done")


def test_cases_cover_a_task(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp_of(world))  # its test is Failed
    story = create(client, admin, world, type="story", assignee_id=world.an.id)
    path = f"{API}/tasks/{story['key']}/tests"
    got = client.post(path, headers=admin, json={"case": f"C{s['case'].id}"})
    assert got.status_code == 201, got.text
    cov = got.json()
    assert cov["total"] == 1 and cov["failed"] == 1
    assert cov["cases"][0]["status"] == "failed"
    # The assignee's page flags it.
    work = client.get(f"{API}/tasks/my-work", headers=as_user(world.an)).json()
    assert any(a["kind"] == "test_failed" for a in work["attention"])
    # Unknown case: 404; removing works.
    assert client.post(path, headers=admin, json={"case": "C999999"}).status_code == 404
    assert client.delete(f"{path}/{s['case'].id}", headers=admin).status_code == 204
    assert client.get(path, headers=admin).json()["total"] == 0


def test_definition_of_done_gates_done(client: TestClient, world: World, as_user: Any) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={
            "key": "CRM",
            "rules": {"dod": {"checks": ["has_mr", "no_critical"], "enforce": True}},
        },
    )
    assert res.status_code == 200, res.text
    bad = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}",
        headers=admin,
        json={"key": "CRM", "rules": {"dod": {"checks": ["nope"]}}},
    )
    assert bad.status_code == 422
    task = create(client, admin, world)
    detail = client.get(f"{API}/tasks/{task['key']}", headers=admin).json()
    assert [(d["key"], d["ok"]) for d in detail["dod"]] == [
        ("has_mr", False),
        ("no_critical", True),
    ]
    assert detail["dod_enforced"] is True
    res = client.post(
        f"{API}/tasks/{task['key']}/transition", headers=admin, json={"status_id": _done(space)}
    )
    assert res.status_code == 409 and "Có MR" in res.json()["detail"]
    # A bulk move skips (and names) what the DoD keeps out instead of failing the batch.
    other = create(client, admin, world)
    res = client.post(
        f"{API}/tasks/bulk",
        headers=admin,
        json={"keys": [task["key"], other["key"]], "set": {"status_id": _done(space)}},
    )
    assert res.status_code == 200, res.text
    assert sorted(x["key"] for x in res.json()["skipped"]) == sorted([task["key"], other["key"]])
    # "Won't do" is not delivery: no DoD needed.
    res = client.post(
        f"{API}/tasks/{task['key']}/transition",
        headers=admin,
        json={"status_id": _done(space), "resolution": "wont_do"},
    )
    assert res.status_code == 200


def test_release_readiness(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    v = client.post(
        f"{API}/tasks/spaces/{bp_of(world)}/versions", headers=admin, json={"name": "v1.0"}
    ).json()
    open_bug = create(client, admin, world, type="bug", priority="highest", version_id=v["id"])
    ready = client.get(f"{API}/tasks/versions/{v['id']}/readiness", headers=admin).json()
    assert ready["ready"] is False
    failing = {c["key"]: c for c in ready["checks"] if not c["ok"]}
    assert set(failing) == {"tasks_done", "no_urgent_bugs"}
    assert failing["no_urgent_bugs"]["items"] == [open_bug["key"]]
    url = f"{API}/tasks/versions/{v['id']}/release"
    assert client.post(url, headers=admin).status_code == 409
    assert client.post(url, headers=admin, json={"force": True}).status_code == 422
    res = client.post(url, headers=admin, json={"force": True, "reason": "hotfix gấp"})
    assert res.status_code == 200, res.text
    logged = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "task.version.release")
    ).all()
    assert logged[-1].details["reason"] == "hotfix gấp"
    # A version with everything done ships without a reason.
    v2 = client.post(
        f"{API}/tasks/spaces/{bp_of(world)}/versions", headers=admin, json={"name": "v1.1"}
    ).json()
    t = create(client, admin, world, version_id=v2["id"])
    client.post(
        f"{API}/tasks/{t['key']}/transition", headers=admin, json={"status_id": _done(space)}
    )
    assert client.post(f"{API}/tasks/versions/{v2['id']}/release", headers=admin).status_code == 200


def test_fixing_a_test_bug_asks_for_a_retest(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bugs.clear_cache()
    space = enable(client, world, as_user)
    admin, an = as_user(world.admin), as_user(world.an)
    s = _setup(client, db_session, admin, bp_of(world))
    made = client.post(
        f"{API}/testcases/tests/{s['test']['id']}/bug",
        headers=admin,
        json={"assignee_id": world.an.id},
    ).json()
    res = client.post(
        f"{API}/tasks/{made['key']}/transition", headers=an, json={"status_id": _done(space)}
    )
    assert res.status_code == 200, res.text
    asked = db_session.scalars(
        select(UserNotification).where(
            UserNotification.kind == "task.retest", UserNotification.user_id == world.admin.id
        )
    ).all()
    assert len(asked) == 1 and made["key"] in asked[0].title
