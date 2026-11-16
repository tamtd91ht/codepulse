"""docs/specs/31 §19.3 (roadmap P2, daily work): Việc của tôi, Board hints on statuses, sprint
capacity, MR reviewers from GitLab."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, MergeRequest
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration


def account(db: Session, user: Any, key: str) -> Account:
    row = Account(kind="gitlab_user", external_key=key, display_name=key, user_id=user.id)
    db.add(row)
    db.flush()
    return row


def test_my_work_gathers_what_needs_me(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin, an = as_user(world.admin), as_user(world.an)
    doing = next(s["id"] for s in space["statuses"] if s["category"] == "doing")
    done = next(s["id"] for s in space["statuses"] if s["category"] == "done")
    late = create(client, admin, world, assignee_id=world.an.id, due_date="2026-01-01")
    working = create(client, admin, world, assignee_id=world.an.id)
    client.post(
        f"{API}/tasks/{working['key']}/transition", headers=admin, json={"status_id": doing}
    )
    blocked = create(client, admin, world, assignee_id=world.an.id)
    blocker = create(client, admin, world, assignee_id=world.binh.id)
    client.post(
        f"{API}/tasks/{blocker['key']}/links",
        headers=admin,
        json={"key": blocked["key"], "kind": "blocks"},
    )
    finished = create(client, admin, world, assignee_id=world.an.id)
    client.post(
        f"{API}/tasks/{finished['key']}/transition", headers=admin, json={"status_id": done}
    )
    # An MR of Bình where An is a GitLab reviewer.
    an_acc, binh_acc = account(db_session, world.an, "101"), account(db_session, world.binh, "102")
    db_session.add(
        MergeRequest(
            project_id=world.project.id,
            iid=42,
            title="feat: OTP",
            state="opened",
            author_account_id=binh_acc.id,
            reviewer_account_ids=[an_acc.id],
            created_at_src=datetime.now(UTC) - timedelta(days=2),
        )
    )
    db_session.flush()
    # A mention.
    client.post(
        f"{API}/tasks/{working['key']}/comments", headers=admin, json={"body": "@an xem giúp"}
    )
    got = client.get(f"{API}/tasks/my-work", headers=an)
    assert got.status_code == 200, got.text
    work = got.json()
    kinds = [(a["kind"], (a.get("task") or {}).get("key")) for a in work["attention"]]
    assert ("overdue", late["key"]) in kinds
    review = next(a for a in work["attention"] if a["kind"] == "review")
    assert review["mr"]["iid"] == 42 and review["mr"]["age_hours"] >= 47
    assert any(a["kind"] == "mention" for a in work["attention"])
    assert [t["key"] for t in work["doing"]] == [working["key"]]
    assert [(t["key"], t["blocked_by"]) for t in work["waiting"]] == [
        (blocked["key"], [blocker["key"]])
    ]
    assert late["key"] in [t["key"] for t in work["todo"]]
    assert blocked["key"] not in [t["key"] for t in work["todo"]]
    assert [t["key"] for t in work["done_week"]] == [finished["key"]]
    # Bình (the author) is not asked to review his own MR; his page has no review item.
    other = client.get(f"{API}/tasks/my-work", headers=as_user(world.binh)).json()
    assert not any(a["kind"] == "review" for a in other["attention"])


def test_status_board_hints_and_sprint_capacity(
    client: TestClient, world: World, as_user: Any
) -> None:
    space = enable(client, world, as_user)
    admin = as_user(world.admin)
    statuses = [
        {
            "id": s["id"],
            "name": s["name"],
            "category": s["category"],
            "transitions": None,
            **({"wip_limit": 3, "age_warning_hours": 48} if s["category"] == "doing" else {}),
        }
        for s in space["statuses"]
    ]
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}/statuses", headers=admin, json={"statuses": statuses}
    )
    assert res.status_code == 200, res.text
    doing = [s for s in res.json() if s["category"] == "doing"]
    assert all(s["wip_limit"] == 3 and s["age_warning_hours"] == 48 for s in doing)
    # Capacity needs three closed sprints.
    done = next(s["id"] for s in space["statuses"] if s["category"] == "done")
    window = {
        "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=7)).isoformat(),
    }
    for n, points in enumerate((5, 7, 9), start=1):
        sid = client.post(
            f"{API}/tasks/spaces/{bp_of(world)}/sprints", headers=admin, json={"name": f"S{n}"}
        ).json()["id"]
        key = create(client, admin, world, sprint_id=sid, story_points=points)["key"]
        client.post(f"{API}/tasks/{key}/transition", headers=admin, json={"status_id": done})
        client.post(f"{API}/tasks/sprints/{sid}/start", headers=admin, json=window)
        client.post(f"{API}/tasks/sprints/{sid}/complete", headers=admin, json={})
    nxt = client.post(
        f"{API}/tasks/spaces/{bp_of(world)}/sprints", headers=admin, json={"name": "S4"}
    ).json()
    assert nxt["capacity"] == 7.0


def test_reviewers_are_read_from_gitlab() -> None:
    from app.collector.gitlab.extract import GlMergeRequest, GlUser

    mr = GlMergeRequest(
        iid=1,
        title="x",
        author=GlUser(id=1, username="a", name=None),
        state="opened",
        source_branch="f",
        target_branch="main",
        head_sha=None,
        created_at=None,
        merged_at=None,
        closed_at=None,
        reviewers=[GlUser(id=2, username="b", name="B")],
    )
    assert [r.username for r in mr.reviewers] == ["b"]
