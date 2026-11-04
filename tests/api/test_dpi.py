"""DPI scorecard and its monthly cycle through the API (docs/specs/25)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dpi import service
from app.models import (
    Account,
    AuditLog,
    DpiReview,
    DpiSnapshot,
    Issue,
    PermissionSet,
    PermissionSetItem,
    ProdDefect,
    UserPermissionSet,
)
from tests.api.conftest import World

pytestmark = pytest.mark.integration
MONTH = "2026-10"
API = "/api/v1/dpi"
MANAGER = {"A3": 4, "B5": 4, "D3": 5, "E1": 4, "E2": 3, "E3": 3}


@pytest.fixture
def an_work(world: World, db_session: Session) -> Account:
    """An's GitLab account, two closed issues assigned to it in October (one with complexity)."""
    account = Account(kind="gitlab_user", external_key="77", display_name="an", user_id=world.an.id)
    db_session.add(account)
    db_session.flush()
    for i, size in ((1, 3), (2, None)):
        db_session.add(
            Issue(
                id=500 + i,
                project_id=world.project.id,
                iid=i,
                title=f"Task {i}",
                state="closed",
                labels=["type::feature"],
                assignee_account_ids=[account.id],
                type="feature",
                complexity_points=size,
                closed_at_src=datetime(2026, 10, 20, tzinfo=UTC),
                due_date=date(2026, 10, 25),
            )
        )
    db_session.flush()
    return account


def card(client: TestClient, as_user: Any, viewer: Any, user_id: int) -> Any:
    return client.get(f"{API}/people/{user_id}?month={MONTH}", headers=as_user(viewer))


def assessment(**overrides: Any) -> dict[str, Any]:
    items = [
        {"metric": k, "score": v, "comment": "Làm tốt phần này", "evidence": ["!7"]}
        for k, v in MANAGER.items()
    ]
    for item in items:
        item.update(overrides.get(item["metric"], {}))
    return {"items": items}


def test_the_whole_month_of_one_person(
    client: TestClient, world: World, as_user: Any, db_session: Session, an_work: Account
) -> None:
    mine = card(client, as_user, world.an, world.an.id)
    assert mine.status_code == 200
    body = mine.json()
    assert body["shadow"] is True and body["review"]["status"] == "draft"
    assert body["card"]["provisional"] is True
    a1 = body["card"]["metrics"]["A1"]
    assert a1["value"] is not None and a1["detail"]["points"] == 3  # the M issue only
    assert body["card"]["metrics"]["C2"]["value"] == 1.0  # closed before its due date
    assert body["can"]["self"] and not body["can"]["assess"]

    assert card(client, as_user, world.binh, world.an.id).status_code == 403  # a peer
    assert card(client, as_user, world.manager, world.an.id).status_code == 403  # aggregates only
    led = card(client, as_user, world.lead, world.an.id).json()
    assert led["can"]["assess"] and not led["can"]["self"]
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "dpi.view")).first()

    submit = client.post(
        f"{API}/people/{world.an.id}/months/{MONTH}/submit",
        headers=as_user(world.an),
        json={"leave_days": 2},
    )
    assert submit.status_code == 200 and submit.json()["review"]["status"] == "lead_review"
    assert submit.json()["card"]["metrics"]["A1"]["detail"]["leave_days"] == 2

    url = f"{API}/people/{world.an.id}/months/{MONTH}/assessment"
    no_evidence = assessment(A3={"evidence": [" "]})
    assert client.put(url, headers=as_user(world.lead), json=no_evidence).status_code == 422
    too_high = assessment(E1={"score": 6})
    assert client.put(url, headers=as_user(world.lead), json=too_high).status_code == 422
    assert client.put(url, headers=as_user(world.an), json=assessment()).status_code == 403
    assessed = client.put(url, headers=as_user(world.lead), json=assessment())
    assert assessed.status_code == 200
    assert assessed.json()["review"]["status"] == "calibration"
    assert assessed.json()["card"]["provisional"] is False

    raised = client.post(
        f"{API}/people/{world.an.id}/months/{MONTH}/disputes",
        headers=as_user(world.an),
        json={"metric": "B1", "reason": "Review điểm thấp do finding sai đã được duyệt."},
    )
    assert raised.status_code == 200
    finalize_url = f"{API}/people/{world.an.id}/months/{MONTH}/finalize"
    plan = {"one_on_one_at": "2026-11-06", "action_plan": "Viết test cho module thanh toán."}
    pending = client.post(finalize_url, headers=as_user(world.lead), json=plan)
    assert pending.status_code == 409  # a dispute waits for an answer
    decided = client.post(
        f"{API}/disputes/{raised.json()['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "accepted", "answer": "Đúng, tính lại", "value": 0.9},
    )
    assert decided.status_code == 200
    after = card(client, as_user, world.an, world.an.id).json()
    assert after["card"]["metrics"]["B1"]["value"] == 0.9
    assert after["card"]["metrics"]["B1"]["overridden"] is True

    calibrated = client.post(
        f"{API}/people/{world.an.id}/months/{MONTH}/calibrate",
        headers=as_user(world.admin),
        json={"adjustment": 2, "reason": "Đồng bộ mặt bằng giữa hai team"},
    )
    assert calibrated.status_code == 200 and calibrated.json()["review"]["adjustment"] == 2

    final = client.post(finalize_url, headers=as_user(world.lead), json=plan)
    assert final.status_code == 200 and final.json()["review"]["status"] == "finalized"
    kinds = set(
        db_session.scalars(select(DpiSnapshot.kind).where(DpiSnapshot.user_id == world.an.id))
    )
    assert kinds == {"final"}  # viewing stores nothing; finalizing keeps the final card
    # Closed: nothing more is assessed, the card shown is the final one.
    assert client.put(url, headers=as_user(world.lead), json=assessment()).status_code == 409


def test_hr_sees_finalized_cards_only(
    client: TestClient, world: World, as_user: Any, db_session: Session, an_work: Account
) -> None:
    hr_set = PermissionSet(name="HR", builtin=False)
    db_session.add(hr_set)
    db_session.flush()
    db_session.add(PermissionSetItem(set_id=hr_set.id, permission="dpi.final_view", scope="flag"))
    db_session.add(UserPermissionSet(user_id=world.outsider.id, set_id=hr_set.id))
    db_session.flush()
    assert card(client, as_user, world.outsider, world.an.id).status_code == 403
    review = service.ensure_review(db_session, world.an.id, date(2026, 10, 1))
    review.status = "finalized"
    db_session.flush()
    assert card(client, as_user, world.outsider, world.an.id).status_code == 200


def test_snapshots_are_reproducible_and_only_added_when_inputs_change(
    world: World, db_session: Session, settings: Settings, an_work: Account
) -> None:
    review = service.ensure_review(db_session, world.an.id, date(2026, 10, 1))
    calc = service.Calculator(db_session, date(2026, 10, 1), settings)
    first = calc.card(review)
    again = service.Calculator(db_session, date(2026, 10, 1), settings).card(review)
    assert first["inputs_hash"] == again["inputs_hash"] and first["total"] == again["total"]
    a = service.snapshot(db_session, review, first)
    b = service.snapshot(db_session, review, again)
    assert a.id == b.id
    review.leave_days = Decimal(10)  # a different A1 target
    changed = service.Calculator(db_session, date(2026, 10, 1), settings).card(review)
    assert service.snapshot(db_session, review, changed).id != a.id


def test_run_month_creates_reviews_for_active_people(
    world: World, db_session: Session, settings: Settings, an_work: Account
) -> None:
    from app.models import Commit

    db_session.add(
        Commit(
            sha="e" * 40,
            project_id=world.project.id,
            author_account_id=an_work.id,
            authored_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
    )
    db_session.flush()
    result = service.run_month(db_session, date(2026, 10, 1), settings)
    assert result["created"] >= 1
    review = db_session.scalars(select(DpiReview).where(DpiReview.user_id == world.an.id)).one()
    assert review.status == "self_review"


def test_defect_rca_counts_in_b2_once_confirmed(
    client: TestClient, world: World, as_user: Any, db_session: Session, an_work: Account
) -> None:
    bug = Issue(
        id=900,
        project_id=world.project.id,
        iid=90,
        title="Crash khi thanh toán",
        state="opened",
        labels=["prod-bug::P1"],
        prod_severity="P1",
        created_at_src=datetime(2026, 10, 15, tzinfo=UTC),
    )
    db_session.add(bug)
    db_session.flush()
    defect = ProdDefect(issue_id=bug.id, severity="P1")
    db_session.add(defect)
    db_session.flush()
    listed = client.get(f"{API}/defects", headers=as_user(world.lead)).json()["items"]
    assert [d["id"] for d in listed] == [defect.id]
    assert client.get(f"{API}/defects", headers=as_user(world.binh)).status_code == 403
    rca = f"{API}/defects/{defect.id}/rca"
    half = {"status": "confirmed", "responsible": [{"user_id": world.an.id, "share": 0.5}]}
    assert client.put(rca, headers=as_user(world.lead), json=half).status_code == 422
    full = {
        "status": "confirmed",
        "responsible": [{"user_id": world.an.id, "share": 1}],
        "process_gaps": ["missing_test"],
    }
    assert client.put(rca, headers=as_user(world.lead), json=full).status_code == 200
    b2 = card(client, as_user, world.an, world.an.id).json()["card"]["metrics"]["B2"]
    assert b2["value"] == 0.5 and b2["detail"]["defects"] == 1


def test_overview_distribution_settings_and_peer_feedback(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    an_work: Account,
    settings: Settings,
) -> None:
    review = service.ensure_review(db_session, world.an.id, date(2026, 10, 1))
    service.snapshot(
        db_session, review, service.Calculator(db_session, date(2026, 10, 1), settings).card(review)
    )
    overview = client.get(f"{API}/overview?month={MONTH}", headers=as_user(world.lead)).json()
    assert world.an.id in [r["user"]["id"] for r in overview["rows"]]
    assert overview["summary"]["people"] >= 1
    dist = client.get(f"{API}/distribution?month={MONTH}", headers=as_user(world.manager))
    assert dist.status_code == 200
    assert all(t["hidden"] or t["people"] >= 5 for t in dist.json()["teams"])
    assert client.get(f"{API}/distribution", headers=as_user(world.an)).status_code == 403

    bad = {"label": "X", "weights": {"A": 50, "B": 10}, "a1_target": 10}
    assert (
        client.put(f"{API}/settings/profiles/x", headers=as_user(world.admin), json=bad).status_code
        == 422
    )
    assert (
        client.put(
            f"{API}/settings/shadow", headers=as_user(world.lead), json={"shadow": False}
        ).status_code
        == 403
    )
    assert client.put(
        f"{API}/settings/shadow", headers=as_user(world.admin), json={"shadow": False}
    ).json() == {"shadow": False}
    set_level = client.put(
        f"{API}/user-profiles",
        headers=as_user(world.admin),
        json={"user_id": world.an.id, "profile": "junior", "effective_from": "2026-10-15"},
    )
    assert set_level.status_code == 200 and set_level.json()["effective_from"] == "2026-10-01"
    assert card(client, as_user, world.an, world.an.id).json()["card"]["profile"] == "junior"

    targets = client.get(f"{API}/peer-feedback?month={MONTH}", headers=as_user(world.binh)).json()
    assert world.an.id in [p["id"] for p in targets["people"]]
    given = {"to_user_id": world.an.id, "month": MONTH, "score": 4, "comment": "Hỗ trợ nhiệt tình"}
    assert (
        client.post(f"{API}/peer-feedback", headers=as_user(world.binh), json=given).status_code
        == 200
    )
    outsider = {**given, "score": 2}
    assert (
        client.post(
            f"{API}/peer-feedback", headers=as_user(world.outsider), json=outsider
        ).status_code
        == 403
    )
    peer_for_an = card(client, as_user, world.an, world.an.id).json()["peer"]
    # One feedback only: the person learns that it exists, not what it says (anonymity).
    assert peer_for_an["count"] == 1 and peer_for_an["items"] == [] and peer_for_an["hidden"]
    peer_for_lead = card(client, as_user, world.lead, world.an.id).json()["peer"]
    assert peer_for_lead["items"][0]["from"] == "Binh"


def test_viewing_writes_nothing_and_an_unfinished_month_takes_no_action(
    client: TestClient, world: World, as_user: Any, db_session: Session, an_work: Account
) -> None:
    body = card(client, as_user, world.an, world.an.id).json()
    assert body["review"]["id"] is None and body["finished"] is True
    assert db_session.scalars(select(DpiReview)).all() == []
    assert db_session.scalars(select(DpiSnapshot)).all() == []
    current = datetime.now(UTC).strftime("%Y-%m")
    now = client.get(f"{API}/people/{world.an.id}?month={current}", headers=as_user(world.an))
    assert now.json()["finished"] is False and not now.json()["can"]["self"]
    early = client.post(
        f"{API}/people/{world.an.id}/months/{current}/submit",
        headers=as_user(world.an),
        json={"leave_days": 0},
    )
    assert early.status_code == 409


def test_defects_stay_inside_the_leads_projects_and_shares_are_checked(
    client: TestClient, world: World, as_user: Any, db_session: Session, an_work: Account
) -> None:
    from app.models import BusinessProject, Connection, Project

    other_bp = BusinessProject(name="Khác")
    db_session.add(other_bp)
    db_session.flush()
    connection = db_session.scalars(select(Connection)).first()
    assert connection is not None
    elsewhere = Project(
        connection_id=connection.id,
        external_id=2002,
        path_with_namespace="other/q",
        business_project_id=other_bp.id,
    )
    db_session.add(elsewhere)
    db_session.flush()
    for issue_id, project_id in ((910, world.project.id), (911, elsewhere.id)):
        db_session.add(
            Issue(
                id=issue_id,
                project_id=project_id,
                iid=issue_id,
                title="bug",
                prod_severity="P2",
                created_at_src=datetime(2026, 10, 15, tzinfo=UTC),
            )
        )
        db_session.flush()
        db_session.add(ProdDefect(issue_id=issue_id, severity="P2"))
    db_session.flush()
    seen = client.get(f"{API}/defects", headers=as_user(world.lead)).json()["items"]
    assert [d["issue"]["id"] for d in seen] == [910]  # not the other dự án's bug
    hidden = db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == 911)).one()
    url = f"{API}/defects/{hidden.id}/rca"
    assert (
        client.put(url, headers=as_user(world.lead), json={"status": "process"}).status_code == 404
    )
    mine = db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == 910)).one()
    bad = {
        "status": "confirmed",
        "responsible": [
            {"user_id": world.an.id, "share": 2},
            {"user_id": world.binh.id, "share": -1},
        ],
    }
    response = client.put(f"{API}/defects/{mine.id}/rca", headers=as_user(world.lead), json=bad)
    assert response.status_code == 422
