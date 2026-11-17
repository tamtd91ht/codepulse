"""docs/specs/31 §21.7: the live Board / task detail stream and what publishes to it."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers import task_live
from app.models import Task
from app.tasks import live
from tests.api.conftest import World
from tests.api.test_tasks import bp_of, create, enable, status_id, ungrant

pytestmark = pytest.mark.integration

API = "/api/v1"


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(live, "publisher", sent.extend)
    monkeypatch.setattr(live, "_paused_until", 0.0)
    return sent


@pytest.fixture
def quiet_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Redis in tests: the hub does not subscribe, and a stream ends at once."""

    async def no_listen(self: live.Hub) -> None:
        return None

    monkeypatch.setattr(live.Hub, "_listen", no_listen)
    monkeypatch.setattr(task_live, "STREAM_S", 0)


def of(sent: list[dict[str, Any]], key: str) -> set[str]:
    return {c for m in sent if m["key"] == key for c in m["changed"]}


def test_stream_opens_only_for_who_sees_task_on_the_du_an(
    client: TestClient, world: World, as_user: Any, db_session: Session, quiet_hub: None
) -> None:
    url = f"{API}/tasks/spaces/{bp_of(world)}/live"
    # Not signed in, and Task not turned on yet.
    assert client.get(url).status_code == 401
    assert client.get(url, headers=as_user(world.an)).status_code == 404
    enable(client, world, as_user)
    res = client.get(url, headers=as_user(world.an))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "event: ready" in res.text
    # One who cannot see the dự án gets no stream (no events of a dự án one cannot see).
    ungrant(db_session, world)
    assert client.get(url, headers=as_user(world.outsider)).status_code == 404


def test_status_change_comment_and_create_publish_after_commit(
    client: TestClient, world: World, as_user: Any, published: list[dict[str, Any]]
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    one = create(client, admin, world)
    two = create(client, admin, world)
    assert "created" in of(published, one["key"])
    assert all(m["type"] == "task" and m["bp"] == bp_of(world) for m in published)

    published.clear()
    doing = status_id(space, "Đang làm")
    res = client.post(
        f"{API}/tasks/{one['key']}/move",
        headers=admin,
        json={"status_id": doing, "after_key": two["key"]},
    )
    assert res.status_code == 200, res.text
    assert {"status_id", "rank"} <= of(published, one["key"])

    published.clear()
    res = client.post(f"{API}/tasks/{two['key']}/comments", headers=admin, json={"body": "Ok"})
    assert res.status_code == 201
    assert of(published, two["key"]) == {"comment"}

    published.clear()
    res = client.patch(f"{API}/tasks/{two['key']}", headers=admin, json={"title": "Đổi tên"})
    assert res.status_code == 200
    assert of(published, two["key"]) == {"title"}


def test_a_refused_change_publishes_nothing(
    client: TestClient, world: World, as_user: Any, published: list[dict[str, Any]]
) -> None:
    enable(client, world, as_user)
    task = create(client, as_user(world.admin), world)
    published.clear()
    res = client.post(
        f"{API}/tasks/{task['key']}/move", headers=as_user(world.admin), json={"status_id": 0}
    )
    assert res.status_code == 422
    assert published == []


def test_hub_delivers_only_to_streams_of_that_du_an() -> None:
    async def run() -> tuple[int, int]:
        hub = live.Hub()
        hub._task = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
        mine, other = hub.open(1), hub.open(2)
        hub.deliver({"type": "task", "bp": 1, "key": "A-1", "changed": ["status_id"]})
        hub.close(1, mine)
        hub.deliver({"type": "task", "bp": 1, "key": "A-2", "changed": ["title"]})
        return mine.qsize(), other.qsize()

    assert asyncio.run(run()) == (1, 0)
    event = live.sse({"type": "task", "bp": 1, "key": "A-1", "changed": ["status_id"]})
    assert event.startswith("event: task\n")
    assert '"bp"' not in event


def test_savepoints_publish_only_what_the_outer_commit_keeps(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    published: list[dict[str, Any]],
) -> None:
    enable(client, world, as_user)
    key = create(client, as_user(world.admin), world)["key"]
    published.clear()
    task = db_session.scalars(select(Task).where(Task.key == key)).one()
    nested = db_session.begin_nested()
    task.title = "Không giữ"
    db_session.flush()
    nested.rollback()
    with db_session.begin_nested():  # released: not the outer commit yet
        task.description = "Giữ"
    assert published == []
    db_session.commit()
    assert of(published, key) == {"description"}


def test_many_tasks_of_one_du_an_become_one_space_event() -> None:
    found = {(1, f"A-{i}"): {"rank"} for i in range(live.MANY + 1)}
    found[(2, "B-1")] = {"title"}
    assert live.as_messages(found) == [
        {"type": "space", "bp": 1, "key": "*", "changed": ["rank"]},
        {"type": "task", "bp": 2, "key": "B-1", "changed": ["title"]},
    ]
