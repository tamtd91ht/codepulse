"""docs/specs/21 phase B2: Luồng làm việc, evaluation, metrics and evidence packs follow the
permission sets; the built-in sets keep the behaviour from before (the other tests)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import PermissionSet, PermissionSetItem, User, UserPermissionSet
from tests.api.conftest import World

pytestmark = pytest.mark.integration

RANGE = "from=2026-10-01&to=2026-10-31"
FLOW = "/api/v1/insights/flow?period=week&start=2026-10-05"


def give(session: Session, user: User, name: str, items: dict[str, str]) -> None:
    """A custom set holding `items`, given to `user` by hand."""
    custom = PermissionSet(name=name, builtin=False)
    session.add(custom)
    session.flush()
    session.add_all(
        [PermissionSetItem(set_id=custom.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user.id, set_id=custom.id)]
    )
    session.flush()


def test_a_custom_set_opens_luong_lam_viec_to_a_member(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    assert client.get(FLOW, headers=as_user(world.an)).status_code == 403
    give(db_session, world.an, "Xem luồng", {"insights.view": "flag"})
    assert client.get(FLOW, headers=as_user(world.an)).status_code == 200
    # Only that: the Thành viên page is another permission.
    members = client.get(f"/api/v1/members/evaluation?{RANGE}", headers=as_user(world.an))
    assert members.status_code == 403


def test_evaluation_at_scope_all_for_a_non_lead(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    detail = f"/api/v1/members/{world.an.id}/evaluation?{RANGE}"
    heatmap = f"/api/v1/metrics/users/{world.an.id}/categories"
    person = f"/api/v1/people/{world.an.id}"
    for path in (detail, heatmap, person):
        assert client.get(path, headers=as_user(world.outsider)).status_code == 403, path
    give(db_session, world.outsider, "Đánh giá", {"evaluation.view": "all"})
    for path in (detail, heatmap, person):
        assert client.get(path, headers=as_user(world.outsider)).status_code == 200, path
    # Everyone in a dự án they can access, and nothing else came with it: not the stored scores
    # (metrics.person), not the evidence pack.
    stored = client.get(f"/api/v1/metrics/users/{world.an.id}", headers=as_user(world.outsider))
    assert stored.status_code == 403
    pack = client.get(f"/api/v1/evidence/{world.an.id}", headers=as_user(world.outsider))
    assert pack.status_code == 403
    # With the page too, the rows are everyone of the dự án.
    give(db_session, world.outsider, "Trang Thành viên", {"members.view": "flag"})
    res = client.get(f"/api/v1/members/evaluation?{RANGE}", headers=as_user(world.outsider))
    assert res.status_code == 200
    rows = {m["user_id"] for m in res.json()["members"]}
    assert {world.an.id, world.binh.id, world.lead.id} <= rows


def test_removing_insights_from_the_lead_set_closes_it_for_leads(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    assert client.get(FLOW, headers=as_user(world.lead)).status_code == 200
    lead_set = db_session.scalars(
        select(PermissionSet).where(PermissionSet.auto_role == "lead")
    ).one()
    db_session.execute(
        delete(PermissionSetItem).where(
            PermissionSetItem.set_id == lead_set.id, PermissionSetItem.permission == "insights.view"
        )
    )
    db_session.flush()
    assert client.get(FLOW, headers=as_user(world.lead)).status_code == 403
    assert client.get(FLOW, headers=as_user(world.manager)).status_code == 200  # own set
    assert client.get(FLOW, headers=as_user(world.admin)).status_code == 200  # admin: full


def test_managing_a_team_never_opens_per_person_rows(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """Phase 0 (a hard rule, docs/specs/21 §3): even with a "team" scope on personal data, a
    manager sees the people they lead, not those of the teams they manage."""
    give(
        db_session,
        world.manager,
        "Manager mở rộng",
        {"evaluation.view": "team", "metrics.person": "team", "evidence.view": "team"},
    )
    for path in (
        f"/api/v1/members/{world.an.id}/evaluation?{RANGE}",
        f"/api/v1/metrics/users/{world.an.id}",
        f"/api/v1/evidence/{world.an.id}",
    ):
        assert client.get(path, headers=as_user(world.manager)).status_code == 403, path
    board = client.get(
        f"/api/v1/leaderboard?team_id={world.backend.id}&start=2026-10-05",
        headers=as_user(world.manager),
    )
    assert board.status_code == 200 and board.json()["members"] is None


def test_evidence_lead_follows_the_set(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """The lead's part of a pack is `evidence.lead`; the pack stays visible by `evidence.view`."""
    path = f"/api/v1/evidence/{world.an.id}/2026-10"
    lead_set = db_session.scalars(
        select(PermissionSet).where(PermissionSet.auto_role == "lead")
    ).one()
    db_session.execute(
        delete(PermissionSetItem).where(
            PermissionSetItem.set_id == lead_set.id, PermissionSetItem.permission == "evidence.lead"
        )
    )
    db_session.flush()
    res = client.get(path, headers=as_user(world.lead))
    assert res.status_code == 200
    assert not any(res.json()["can"].values())
    draft = client.post(f"{path}/ai-draft", headers=as_user(world.lead))
    assert draft.status_code == 403
