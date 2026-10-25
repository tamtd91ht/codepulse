"""Reviews in progress on the dashboard (docs/specs/17 §Tiến trình)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.main import app
from app.api.routers.activity import get_backlog, get_progress
from app.models import Account, Commit, Project
from app.review.progress import MemoryProgress
from tests.api.conftest import World

pytestmark = pytest.mark.integration


@pytest.fixture
def live(
    client: TestClient, db_session: Session, world: World
) -> Iterator[tuple[MemoryProgress, list[dict[str, Any]]]]:
    an = Account(kind="gitlab_user", external_key="an", user_id=world.an.id)
    binh = Account(kind="git_email", external_key="binh@x.vn", user_id=world.binh.id)
    db_session.add_all([an, binh])
    db_session.flush()
    world.mr.author_account_id = an.id
    world.mr.title = "Add parser"
    commit = db_session.get(Commit, "b" * 40)
    assert commit is not None
    commit.author_account_id, commit.message = binh.id, "  \n"  # blank message: short sha as title
    db_session.commit()

    progress = MemoryProgress()
    progress.start(
        900,
        {
            "kind": "mr",
            "project_id": world.project.id,
            "project": "demo/p",
            "ref": "7",
            "sha": "a" * 40,
            "title": "Add parser",
            "author_user_id": world.an.id,
            "author": "An",
            "requested_by": "Lead",
            "trigger": "telegram",
            "round": 2,
            "started_at": "2026-10-27T09:00:00+00:00",
        },
    )
    progress.stage(900, "deep", "agent đang đọc code", loc=120, files=4)
    progress.start(
        901,
        {
            "kind": "commit",
            "project_id": world.project.id,
            "ref": "c" * 40,
            "sha": "c" * 40,
            "title": "secret work",
            "author_user_id": None,  # unmapped author: leads and admins only
            "trigger": "webhook",
            "started_at": "2026-10-27T09:01:00+00:00",
        },
    )
    progress.start(902, {"kind": "mr", "project_id": 99999, "ref": "1", "author_user_id": None})
    progress.start(
        903,
        {
            "kind": "commit",
            "project_id": world.project.id,
            "ref": "d" * 40,
            "author_user_id": world.an.id,
        },
    )
    progress.end(903, "done")
    backlog: list[dict[str, Any]] = [
        {"kind": "commit", "project_id": None, "ref": "b" * 12, "sha": "b" * 12, "trigger": ""},
        {
            "kind": "mr",
            "project_id": world.project.id,
            "ref": "55",
            "sha": "e" * 12,
            "trigger": "rerun",
        },  # MR not synced yet: its author is unknown
        {"kind": "commit", "project_id": None, "ref": "f" * 12, "sha": "f" * 12, "trigger": ""},
    ]
    app.dependency_overrides[get_progress] = lambda: progress
    app.dependency_overrides[get_backlog] = lambda: lambda: backlog
    yield progress, backlog


def test_lead_sees_running_waiting_and_ended_reviews_with_details(
    client: TestClient, world: World, as_user: Any, live: Any
) -> None:
    body = client.get("/api/v1/activity/reviews", headers=as_user(world.lead)).json()
    first, unmapped = body["running"]
    assert (first["review_id"], first["stage"], first["detail"], first["round"]) == (
        900,
        "deep",
        "agent đang đọc code",
        2,
    )
    assert first["web_url"] == "https://gitlab.example.com/demo/p/-/merge_requests/7"
    assert (first["author"], first["requested_by"], first["loc"]) == ("An", "Lead", 120)
    assert unmapped["review_id"] == 901
    (waiting,) = body["queued"]  # the unsynced MR is for admins; the unknown commit is skipped
    assert (waiting["ref"], waiting["title"], waiting["author"]) == ("b" * 40, "b" * 8, "Binh")
    assert waiting["web_url"].endswith("/-/commit/" + "b" * 40)
    (ended,) = body["recent"]
    assert (ended["review_id"], ended["status"], ended["state"]) == (903, "done", "ended")
    assert body["queued_total"] == 1
    assert body["hidden"] == 2  # the untracked repo's review, the unsynced MR


def test_admin_also_sees_work_that_is_not_synced_yet(
    client: TestClient, world: World, as_user: Any, live: Any
) -> None:
    body = client.get("/api/v1/activity/reviews", headers=as_user(world.admin)).json()
    assert [q["ref"] for q in body["queued"]] == ["b" * 40, "55"]
    assert body["hidden"] == 1  # a repo CodePulse does not track is never shown


def test_member_sees_only_their_own_work_and_a_count_of_the_rest(
    client: TestClient, world: World, as_user: Any, live: Any
) -> None:
    body = client.get("/api/v1/activity/reviews", headers=as_user(world.an)).json()
    assert [r["review_id"] for r in body["running"]] == [900]
    assert [r["review_id"] for r in body["recent"]] == [903]
    assert body["queued"] == [] and body["queued_total"] == 0
    assert body["hidden"] == 4
    outsider = client.get("/api/v1/activity/reviews", headers=as_user(world.outsider)).json()
    assert outsider["running"] == [] and outsider["recent"] == [] and outsider["hidden"] == 5


def test_untracked_repo_and_old_endings_are_left_out(
    client: TestClient, world: World, as_user: Any, live: Any, db_session: Session
) -> None:
    progress, _ = live
    progress.ended[0]["ended_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    project = db_session.get(Project, world.project.id)
    assert project is not None
    project.tracked = False
    db_session.commit()
    body = client.get("/api/v1/activity/reviews", headers=as_user(world.admin)).json()
    assert body["running"] == [] and body["queued"] == [] and body["recent"] == []


def test_nothing_running(client: TestClient, world: World, as_user: Any, live: Any) -> None:
    progress, backlog = live
    progress.items.clear()
    progress.ended.clear()
    backlog.clear()
    body = client.get("/api/v1/activity/reviews", headers=as_user(world.an)).json()
    assert body == {"running": [], "queued": [], "queued_total": 0, "recent": [], "hidden": 0}
    assert client.get("/api/v1/activity/reviews").status_code == 401


def test_live_view_of_a_review_follows_its_visibility(
    client: TestClient, world: World, as_user: Any, live: Any
) -> None:
    progress, _ = live
    progress.event(900, [{"kind": "tool", "tool": "Read", "target": "src/app.py", "turn": 1}])
    progress.event(901, [{"kind": "tool", "tool": "Grep", "target": "tenant_id", "turn": 1}])

    body = client.get("/api/v1/activity/reviews/900/live", headers=as_user(world.an)).json()
    assert body["running"] is True
    assert [(e["tool"], e["target"]) for e in body["events"]] == [("Read", "src/app.py")]

    # The unmapped author's review: leads and admins only, invisible (404) to a member.
    assert (
        client.get("/api/v1/activity/reviews/901/live", headers=as_user(world.an)).status_code
        == 404
    )
    lead = client.get("/api/v1/activity/reviews/901/live", headers=as_user(world.lead))
    assert lead.status_code == 200 and lead.json()["events"][0]["target"] == "tenant_id"

    ended = client.get("/api/v1/activity/reviews/903/live", headers=as_user(world.an)).json()
    assert ended == {"review_id": 903, "running": False, "events": []}
    missing = client.get("/api/v1/activity/reviews/123456/live", headers=as_user(world.admin))
    assert missing.status_code == 404
