"""docs/specs/31 §4.1 and §17: Task rights split as Jira's permission scheme; attachments
(images shown in place, pasted screenshots)."""

import importlib.util
import pathlib
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PermissionSet, PermissionSetItem
from tests.api.conftest import World
from tests.api.test_permission_sets_api import give
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def member_set(db: Session) -> PermissionSet:
    return db.scalars(select(PermissionSet).where(PermissionSet.auto_role == "member")).one()


def drop(db: Session, key: str) -> None:
    """Take `key` out of the built-in member set (as an admin would)."""
    db.execute(
        delete(PermissionSetItem).where(
            PermissionSetItem.set_id == member_set(db).id, PermissionSetItem.permission == key
        )
    )
    db.flush()


def held(db: Session, set_id: int) -> dict[str, str]:
    rows = db.execute(
        select(PermissionSetItem.permission, PermissionSetItem.scope).where(
            PermissionSetItem.set_id == set_id, PermissionSetItem.permission.like("tasks.%")
        )
    ).all()
    return {k: v for k, v in rows}


def test_the_member_set_keeps_what_it_allowed(db_session: Session) -> None:
    """The migration carried tasks.view / tasks.edit into the new rights."""
    assert held(db_session, member_set(db_session).id) == {
        "tasks.view": "flag",
        "tasks.create": "flag",
        "tasks.edit": "own",
        "tasks.assign": "own",
        "tasks.transition": "own",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.worklog": "own",
        "tasks.link": "own",
    }


def test_the_migration_carries_custom_sets(db_session: Session) -> None:
    row = PermissionSet(name="Bộ riêng", builtin=False)
    db_session.add(row)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=row.id, permission="tasks.view", scope="flag"),
            PermissionSetItem(set_id=row.id, permission="tasks.edit", scope="project"),
            PermissionSetItem(set_id=row.id, permission="tasks.manage", scope="all"),
        ]
    )
    db_session.flush()
    path = sorted(pathlib.Path("migrations/versions").glob("*_e1a3c5e7b9d2_*.py"))[-1]
    spec = importlib.util.spec_from_file_location("task_perm_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db_session.connection())):
        module.upgrade()
    assert held(db_session, row.id) == {
        "tasks.view": "flag",
        "tasks.edit": "project",
        "tasks.manage": "all",
        "tasks.create": "flag",
        "tasks.assign": "project",
        "tasks.transition": "project",
        "tasks.worklog": "project",
        "tasks.link": "project",
        "tasks.comment": "flag",
        "tasks.attach": "flag",
        "tasks.delete": "all",
        "tasks.moderate": "all",
        "tasks.sprint": "all",
    }


def test_each_right_guards_its_action(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    an, binh = as_user(world.an), as_user(world.binh)
    mine = create(client, an, world)
    theirs = create(client, binh, world)
    assert mine["can"] == {
        "edit": True,
        "assign": True,
        "transition": True,
        "delete": False,  # a member deletes nothing by default
        "comment": True,
        "attach": True,
        "worklog": True,
        "link": True,
        "moderate": False,
        "manage": False,
    }
    # own: only tasks one reported or is assigned.
    other = client.get(f"{API}/tasks/{theirs['key']}", headers=an).json()
    assert not other["can"]["edit"] and not other["can"]["transition"]
    assert other["can"]["comment"] and other["can"]["attach"]  # flags: any task one sees
    assert client.delete(f"{API}/tasks/{mine['key']}", headers=an).status_code == 403
    assert (
        client.post(
            f"{API}/tasks/{theirs['key']}/worklogs", headers=an, json={"minutes": 5}
        ).status_code
        == 403
    )

    # Without tasks.assign: may change the rest, not the assignee, nor name one on create.
    drop(db_session, "tasks.assign")
    path = f"{API}/tasks/{mine['key']}"
    assert client.patch(path, headers=an, json={"priority": "high"}).status_code == 200
    res = client.patch(path, headers=an, json={"assignee_id": world.binh.id})
    assert res.status_code == 403 and "giao" in res.json()["detail"]
    res = client.post(
        f"{API}/tasks",
        headers=an,
        json={"space": bp_of(world), "title": "x", "assignee_id": world.binh.id},
    )
    assert res.status_code == 403
    create(client, an, world, assignee_id=world.an.id)  # oneself is fine
    space = next(
        s for s in client.get(f"{API}/tasks/spaces", headers=an).json() if s["key"] == "CRM"
    )
    assert space["can_assign"] is False and space["can_sprint"] is False
    assert space["can_attach"] is True

    # Without tasks.transition: no status change; the rest of editing stays.
    drop(db_session, "tasks.transition")
    got = client.get(path, headers=an).json()
    done = next(s for s in got["transitions"] if s["id"] != got["status"]["id"])
    res = client.post(f"{path}/transition", headers=an, json={"status_id": done["id"]})
    assert res.status_code == 403
    # Nor through the plain PATCH (the review's H1).
    res = client.patch(path, headers=an, json={"status_id": done["id"]})
    assert res.status_code == 403
    # Without tasks.comment / tasks.attach.
    drop(db_session, "tasks.comment")
    assert client.post(f"{path}/comments", headers=an, json={"body": "hi"}).status_code == 403
    drop(db_session, "tasks.attach")
    res = client.post(
        f"{path}/attachments?filename=a.txt",
        headers={**an, "content-type": "text/plain"},
        content=b"x",
    )
    assert res.status_code == 403
    # Without tasks.create.
    drop(db_session, "tasks.create")
    res = client.post(f"{API}/tasks", headers=an, json={"space": bp_of(world), "title": "y"})
    assert res.status_code == 403


def test_delete_own_and_moderate(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TASK_FILES_DIR", str(tmp_path))
    enable(client, world, as_user)
    an, binh = as_user(world.an), as_user(world.binh)
    task = create(client, an, world)
    path = f"{API}/tasks/{task['key']}"
    comment = client.post(f"{path}/comments", headers=an, json={"body": "của An"}).json()
    # binh may comment, not touch An's comment.
    assert client.delete(f"{path}/comments/{comment['id']}", headers=binh).status_code == 403
    give(db_session, world, world.binh, {"tasks.moderate": "all"})
    assert client.delete(f"{path}/comments/{comment['id']}", headers=binh).status_code == 204
    # tasks.delete own: the tasks one reported.
    give(db_session, world, world.an, {"tasks.delete": "own"})
    assert client.delete(path, headers=an).status_code == 204


def test_sprints_need_tasks_sprint(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    an = as_user(world.an)
    url = f"{API}/tasks/spaces/{bp_of(world)}/sprints"
    assert client.post(url, headers=an, json={"name": "S1"}).status_code == 403
    give(db_session, world, world.an, {"tasks.sprint": "all"})
    assert client.post(url, headers=an, json={"name": "S1"}).status_code == 201
    # tasks.sprint is not tasks.manage: the settings stay closed.
    res = client.put(f"{API}/tasks/spaces/{bp_of(world)}", headers=an, json={"key": "CRM"})
    assert res.status_code == 403


def test_images_show_in_place_other_files_download(
    client: TestClient,
    world: World,
    as_user: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TASK_FILES_DIR", str(tmp_path))
    enable(client, world, as_user)
    an, binh = as_user(world.an), as_user(world.binh)
    task = create(client, an, world)
    # A pasted screenshot: sent as octet-stream, the type comes from its name.
    res = client.post(
        f"{API}/tasks/{task['key']}/attachments?filename=anh-chup-1.png",
        headers={**an, "content-type": "application/octet-stream"},
        content=PNG,
    )
    assert res.status_code == 201, res.text
    shot = res.json()
    assert shot["content_type"] == "image/png"
    assert shot["url"] == f"/api/v1/tasks/attachments/{shot['id']}"
    shown = client.get(f"{API}/tasks/attachments/{shot['id']}?inline=1", headers=an)
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline")
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in shown.headers["content-security-policy"]
    saved = client.get(f"{API}/tasks/attachments/{shot['id']}", headers=an)
    assert saved.headers["content-disposition"].startswith("attachment")
    assert saved.content == PNG
    # HTML / SVG never open in place, even when asked.
    for name, kind in (("x.html", "text/html"), ("x.svg", "image/svg+xml")):
        up = client.post(
            f"{API}/tasks/{task['key']}/attachments?filename={name}",
            headers={**an, "content-type": kind},
            content=b"<script>alert(1)</script>",
        ).json()
        got = client.get(f"{API}/tasks/attachments/{up['id']}?inline=1", headers=an)
        assert got.headers["content-type"] == "application/octet-stream"
        assert got.headers["content-disposition"].startswith("attachment")
    # Someone who only sees the task may attach (paste into a comment), not delete others'.
    res = client.post(
        f"{API}/tasks/{task['key']}/attachments?filename=b.png",
        headers={**binh, "content-type": "image/png"},
        content=PNG,
    )
    assert res.status_code == 201
    files = {
        a["name"]: a
        for a in client.get(f"{API}/tasks/{task['key']}", headers=binh).json()["attachments"]
    }
    assert files["b.png"]["can_delete"] is True and files["anh-chup-1.png"]["can_delete"] is False
    assert client.delete(f"{API}/tasks/attachments/{shot['id']}", headers=binh).status_code == 403
    assert (
        client.delete(f"{API}/tasks/attachments/{files['b.png']['id']}", headers=binh).status_code
        == 204
    )
