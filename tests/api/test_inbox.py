"""In-app notifications (docs/specs/24): who hears about what, the history and read marks."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserNotification
from app.notifications import inbox
from app.notifications.stream import Hub, sse
from tests.api.conftest import World

pytestmark = pytest.mark.integration


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(inbox, "publisher", sent.extend)
    return sent


def notes(session: Session, user_id: int) -> list[UserNotification]:
    return list(
        session.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == user_id)
            .order_by(UserNotification.id)
        )
    )


def raise_dispute(client: TestClient, world: World, as_user: Any) -> dict[str, Any]:
    raised = client.post(
        f"/api/v1/findings/{world.an_finding.id}/dispute",
        headers=as_user(world.an),
        json={"reason": "The input is validated by the caller."},
    )
    assert raised.status_code == 201
    result: dict[str, Any] = raised.json()
    return result


def test_new_dispute_reaches_who_may_decide_it_and_is_pushed_after_commit(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    published: list[dict[str, Any]],
) -> None:
    raised = raise_dispute(client, world, as_user)
    # The developer's lead and the admin decide it; the manager (no dispute.decide), a peer
    # and the person who raised it do not hear about it.
    told = {n.user_id for n in db_session.scalars(select(UserNotification))}
    assert told == {world.lead.id, world.admin.id}
    (note,) = notes(db_session, world.lead.id)
    assert (note.kind, note.link, note.ref, note.actor_id) == (
        "dispute_new",
        "/disputes",
        f"dispute:{raised['id']}",
        world.an.id,
    )
    assert "An" in note.title and "SQL injection" in (note.body or "")
    assert {m["user_id"] for m in published} == told  # live, once committed


def test_decided_dispute_tells_who_raised_it(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    published: list[dict[str, Any]],
) -> None:
    raised = raise_dispute(client, world, as_user)
    decided = client.post(
        f"/api/v1/disputes/{raised['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "accepted"},
    )
    assert decided.status_code == 200
    (note,) = notes(db_session, world.an.id)
    assert note.kind == "dispute_decided" and note.tone == "good"
    assert note.title == "Khiếu nại được chấp nhận"
    assert note.link == f"/reviews/{world.an_review.id}#finding-{world.an_finding.id}"
    assert published[-1]["user_id"] == world.an.id


def test_admin_close_tells_the_author(
    client: TestClient, world: World, as_user: Any, db_session: Session, published: list[Any]
) -> None:
    closed = client.post(
        f"/api/v1/findings/{world.an_finding.id}/close",
        headers=as_user(world.admin),
        json={"note": "Rủi ro đã được chấp nhận", "outcome": "medium"},
    )
    assert closed.status_code == 200
    (note,) = notes(db_session, world.an.id)
    assert note.kind == "finding_closed"
    assert note.title == "Finding đã đóng (chấp nhận rủi ro, hạ critical → medium)"
    assert note.actor_id == world.admin.id
    assert notes(db_session, world.admin.id) == []  # never one's own action


def test_review_finished_tells_the_author_and_who_asked(
    world: World, db_session: Session, published: list[Any]
) -> None:
    world.an_review.requested_by_user_id = world.lead.id
    world.an_review.trigger = "dashboard"
    db_session.flush()
    inbox.review_finished(db_session, world.an_review)
    (mine,) = notes(db_session, world.an.id)
    assert mine.kind == "review_done" and mine.title == "Review xong: p !7"
    assert mine.body is not None and mine.body.startswith("Điểm 50 · 1 critical")
    assert mine.tone == "bad" and mine.link == f"/reviews/{world.an_review.id}"
    assert len(notes(db_session, world.lead.id)) == 1


def test_backfill_reviews_ping_nobody_and_failures_go_to_who_asked(
    world: World, db_session: Session, published: list[Any]
) -> None:
    world.an_review.trigger = "backfill"
    world.an_review.requested_by_user_id = world.admin.id  # who started the backfill
    inbox.review_finished(db_session, world.an_review)
    assert db_session.scalars(select(UserNotification)).all() == []
    world.binh_review.requested_by_user_id = world.lead.id
    world.binh_review.error = "LLM timeout"
    inbox.review_finished(db_session, world.binh_review)  # failed
    assert notes(db_session, world.binh.id) == []
    (failed,) = notes(db_session, world.lead.id)
    assert (failed.kind, failed.tone, failed.body) == ("review_failed", "bad", "LLM timeout")


def test_findings_closed_by_a_later_change_link_to_the_authors_own_review(
    world: World, db_session: Session, published: list[Any]
) -> None:
    # Binh's change fixed An's finding: An hears, linked to An's review (Binh's may be hidden).
    inbox.findings_closed_by_review(db_session, world.binh_review, [world.an_finding.id])
    (note,) = notes(db_session, world.an.id)
    assert note.link == f"/reviews/{world.an_review.id}#finding-{world.an_finding.id}"
    assert note.title.startswith("Finding đã tự đóng nhờ p ")


def test_rolled_back_event_publishes_nothing(
    world: World, db_session: Session, published: list[Any]
) -> None:
    inbox.notify(db_session, [world.binh.id], kind="review_done", title="kept")
    nested = db_session.begin_nested()
    inbox.notify(db_session, [world.an.id], kind="review_done", title="dropped")
    nested.rollback()  # a savepoint: what was added before it still goes out
    db_session.commit()
    assert [m["title"] for m in published] == ["kept"]
    published.clear()
    inbox.notify(db_session, [world.an.id], kind="review_done", title="x")
    db_session.rollback()
    db_session.commit()
    assert published == []


def test_history_pages_filters_and_marks_read(
    client: TestClient, world: World, as_user: Any, db_session: Session, published: list[Any]
) -> None:
    for i in range(5):
        inbox.notify(db_session, [world.an.id], kind="review_done", title=f"n{i}")
    inbox.notify(db_session, [world.an.id], kind="dispute_decided", title="d")
    inbox.notify(db_session, [world.binh.id], kind="review_done", title="not mine")
    db_session.flush()
    me = as_user(world.an)
    first = client.get("/api/v1/inbox?limit=4", headers=me).json()
    assert [i["title"] for i in first["items"]] == ["d", "n4", "n3", "n2"]
    assert first["unread"] == 6 and first["next_before"] is not None
    rest = client.get(f"/api/v1/inbox?limit=4&before={first['next_before']}", headers=me).json()
    assert [i["title"] for i in rest["items"]] == ["n1", "n0"] and rest["next_before"] is None
    only = client.get("/api/v1/inbox?kind=dispute_decided", headers=me).json()
    assert [i["title"] for i in only["items"]] == ["d"]

    one = first["items"][0]["id"]
    assert client.post("/api/v1/inbox/read", headers=me, json={"ids": [one]}).json() == {
        "unread": 5
    }
    unread = client.get("/api/v1/inbox?unread=true", headers=me).json()
    assert one not in [i["id"] for i in unread["items"]]
    # Someone else's id is never touched.
    theirs = notes(db_session, world.binh.id)[0].id
    client.post("/api/v1/inbox/read", headers=me, json={"ids": [theirs]})
    db_session.expire_all()
    assert notes(db_session, world.binh.id)[0].read_at is None
    assert client.post("/api/v1/inbox/read", headers=me, json={}).json() == {"unread": 0}
    assert client.get("/api/v1/inbox/unread-count", headers=me).json() == {"unread": 0}


def test_inbox_needs_sign_in(client: TestClient) -> None:
    assert client.get("/api/v1/inbox").status_code == 401


def test_hub_delivers_only_to_the_recipient() -> None:
    import asyncio

    async def scenario() -> tuple[int, int]:
        hub = Hub()
        hub._task = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
        mine, theirs = hub.open(1), hub.open(2)
        hub.deliver({"id": 9, "user_id": 1, "title": "x"})
        hub.close(2, theirs)
        hub.deliver({"id": 10, "user_id": 2})
        return mine.qsize(), theirs.qsize()

    assert asyncio.run(scenario()) == (1, 0)
    frame = sse({"id": 9, "user_id": 1, "title": "Review xong"})
    assert frame.startswith("id: 9\nevent: notification\ndata: ") and frame.endswith("\n\n")
    assert "Review xong" in frame
