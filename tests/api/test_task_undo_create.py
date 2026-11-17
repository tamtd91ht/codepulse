"""docs/specs/31 §22.10: "Hoàn tác" on the "Đã tạo OCX-12" toast."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AuditLog, Issue, Task
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1"


def bp_of(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def enable(client: TestClient, world: World, as_user: Any) -> None:
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}", headers=as_user(world.admin), json={"key": "CRM"}
    )
    assert res.status_code == 200, res.text


def create(client: TestClient, headers: dict[str, str], world: World, **fields: Any) -> Any:
    res = client.post(
        f"{API}/tasks", headers=headers, json={"space": bp_of(world), "title": "Việc", **fields}
    )
    assert res.status_code == 201, res.text
    return res.json()


def undo(client: TestClient, headers: dict[str, str], key: str) -> Any:
    return client.post(f"{API}/tasks/{key}/undo-create", headers=headers)


def test_creator_takes_a_fresh_task_back(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    # As the DPI task source, the task's issue row goes with it.
    assert (
        client.put(
            f"{API}/dpi/jira/source", headers=admin, json={"source": "codepulse"}
        ).status_code
        == 200
    )
    an = as_user(world.an)
    task = create(client, an, world, title="Nhầm", type="bug")
    db_session.expire_all()
    assert db_session.scalars(select(Issue).where(Issue.key == task["key"])).first() is not None
    # Someone else cannot, even an admin who may delete it.
    res = undo(client, admin, task["key"])
    assert res.status_code == 409
    assert "người tạo" in res.json()["detail"]
    # The creator can; it is gone and audited.
    assert undo(client, an, task["key"]).status_code == 204
    assert client.get(f"{API}/tasks/{task['key']}", headers=an).status_code == 404
    db_session.expire_all()
    assert db_session.scalars(select(Issue).where(Issue.key == task["key"])).first() is None
    actions = db_session.scalars(
        select(AuditLog.action).where(AuditLog.target == f"task:{task['key']}")
    ).all()
    assert "task.undo_create" in actions


def test_undo_refused_once_the_task_has_a_life(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    an, admin = as_user(world.an), as_user(world.admin)
    # A comment.
    commented = create(client, an, world, title="Có bình luận")
    res = client.post(f"{API}/tasks/{commented['key']}/comments", headers=an, json={"body": "x"})
    assert res.status_code == 201, res.text
    res = undo(client, an, commented["key"])
    assert res.status_code == 409 and "bình luận" in res.json()["detail"]
    # A sub-task.
    parent = create(client, an, world, title="Cha", type="story")
    create(client, an, world, title="Con", type="subtask", parent_key=parent["key"])
    res = undo(client, an, parent["key"])
    assert res.status_code == 409 and "sub-task" in res.json()["detail"]
    # Edited by someone else.
    edited = create(client, an, world, title="Người khác sửa")
    res = client.patch(f"{API}/tasks/{edited['key']}", headers=admin, json={"priority": "high"})
    assert res.status_code == 200, res.text
    res = undo(client, an, edited["key"])
    assert res.status_code == 409 and "người khác" in res.json()["detail"]
    # The creator's own edit does not count.
    mine = create(client, an, world, title="Tự sửa")
    res = client.patch(f"{API}/tasks/{mine['key']}", headers=an, json={"priority": "high"})
    assert res.status_code == 200, res.text
    # Past two minutes.
    db_session.execute(
        update(Task)
        .where(Task.key == mine["key"])
        .values(created_at=datetime.now(UTC) - timedelta(minutes=3))
    )
    db_session.flush()
    res = undo(client, an, mine["key"])
    assert res.status_code == 409 and "2 phút" in res.json()["detail"]
    # Still there; an unknown key is a 404.
    assert client.get(f"{API}/tasks/{mine['key']}", headers=an).status_code == 200
    assert undo(client, an, "CRM-999").status_code == 404
