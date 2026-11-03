"""docs/specs/22 §5-6: the criteria API, its permissions, the scores on a review."""

import copy
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, ReviewCriteriaScore
from app.scoring.criteria import TEMPLATES
from app.workers.criteria_jobs import JOB, recompute_criteria
from tests.api.conftest import FakeQueue, World, grant

pytestmark = pytest.mark.integration

API = "/api/v1"


def parts(template: str = "chuan_chung") -> list[dict[str, Any]]:
    return copy.deepcopy(TEMPLATES[template]["components"])


def make(client: TestClient, headers: dict[str, str], name: str = "Chuẩn", **kw: Any) -> Any:
    body = {"name": name, "description": "d", "components": parts(), "apply_caps": True, **kw}
    res = client.post(f"{API}/criteria-sets", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def assign(
    client: TestClient, headers: dict[str, str], bp_id: int, sets: list[dict[str, Any]]
) -> Any:
    return client.put(
        f"{API}/business-projects/{bp_id}/criteria-sets", json={"sets": sets}, headers=headers
    )


def bp(world: World) -> int:
    assert world.project.business_project_id is not None
    return world.project.business_project_id


def test_meta_lists_categories_limits_and_templates(
    client: TestClient, world: World, as_user: Any
) -> None:
    res = client.get(f"{API}/criteria/meta", headers=as_user(world.an))
    assert res.status_code == 200
    meta = res.json()
    assert {c["key"] for c in meta["categories"]} >= {"security", "bug", "docs"}
    assert meta["limits"]["min_components"] == 2 and meta["limits"]["max_components"] == 12
    assert {t["key"] for t in meta["templates"]} == set(TEMPLATES)


def test_create_from_template_and_validation_errors(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    made = client.post(
        f"{API}/criteria-sets",
        json={"name": "Bảo mật", "from_template": "bao_mat_cao"},
        headers=admin,
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["version"] == 1 and len(body["components"]) == 4
    assert body["description"] == TEMPLATES["bao_mat_cao"]["description"]
    assert body["used"] is False and body["versions"][0]["version"] == 1

    def error(components: Any, name: str = "X") -> Any:
        res = client.post(
            f"{API}/criteria-sets", json={"name": name, "components": components}, headers=admin
        )
        assert res.status_code == 422, res.text
        return res.json()["detail"]

    bad = parts()
    bad[0]["weight"] = 31
    assert "tổng trọng số" in error(bad)[0]["msg"]
    bad = parts()
    bad[1]["key"] = bad[0]["key"]
    assert "trùng" in error(bad)[0]["msg"]
    bad = parts()
    bad[0]["categories"] = []
    assert "bug" in error(bad)[0]["msg"]
    bad = parts()
    bad[0]["key"] = "Bad-Key"
    assert error(bad)[0]["loc"] == ["body", "components", 0, "key"]
    assert "cần 2-12" in error(parts()[:1])[0]["msg"]
    assert error(parts(), name="   ")[0]["loc"] == ["body", "name"]
    duplicate = client.post(
        f"{API}/criteria-sets", json={"name": "bảo mật", "components": parts()}, headers=admin
    )
    assert duplicate.status_code == 409


def test_a_new_version_only_when_components_or_caps_change(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    made = make(client, admin)
    url = f"{API}/criteria-sets/{made['id']}"
    body = {"name": "Chuẩn 2", "description": "mới", "components": parts(), "apply_caps": True}
    renamed = client.put(url, json=body, headers=admin).json()
    assert (renamed["name"], renamed["version"], renamed["new_version"]) == ("Chuẩn 2", 1, False)
    changed = parts()
    changed[0]["weight"], changed[1]["weight"] = 35, 20
    res = client.put(
        url, json={**body, "components": changed, "note": "nặng chức năng"}, headers=admin
    )
    assert res.json()["version"] == 2 and res.json()["new_version"] is True
    caps = client.put(url, json={**body, "components": changed, "apply_caps": False}, headers=admin)
    assert caps.json()["version"] == 3
    detail = client.get(url, headers=admin).json()
    assert [v["version"] for v in detail["versions"]] == [3, 2, 1]
    assert detail["versions"][1]["note"] == "nặng chức năng"
    first = client.get(f"{url}/versions/1", headers=admin).json()
    assert first["components"][0]["weight"] == 30 and first["apply_caps"] is True
    assert client.get(f"{url}/versions/9", headers=admin).status_code == 404


def test_clone_archive_and_delete_rules(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    admin = as_user(world.admin)
    made = make(client, admin)
    clone = client.post(
        f"{API}/criteria-sets/{made['id']}/clone", json={"name": "Bản sao"}, headers=admin
    )
    assert clone.status_code == 201
    assert clone.json()["components"] == made["components"] and clone.json()["version"] == 1
    assert assign(client, admin, bp(world), [{"set_id": made["id"], "is_primary": True}]).is_success
    # The primary of a dự án cannot be archived; a used set cannot be deleted.
    assert (
        client.post(f"{API}/criteria-sets/{made['id']}/archive", headers=admin).status_code == 409
    )
    assert client.delete(f"{API}/criteria-sets/{made['id']}", headers=admin).status_code == 409
    archived = client.post(f"{API}/criteria-sets/{clone.json()['id']}/archive", headers=admin)
    assert archived.status_code == 200 and archived.json()["archived"] is True
    listed = client.get(f"{API}/criteria-sets?archived=false", headers=admin).json()
    assert [s["id"] for s in listed] == [made["id"]]
    assert listed[0]["projects_count"] == 1 and listed[0]["primary_count"] == 1
    # An archived set is not given to a dự án anew.
    res = assign(
        client,
        admin,
        bp(world),
        [{"set_id": made["id"], "is_primary": True}, {"set_id": clone.json()["id"]}],
    )
    assert res.status_code == 422
    client.post(f"{API}/criteria-sets/{clone.json()['id']}/unarchive", headers=admin)
    assert (
        client.delete(f"{API}/criteria-sets/{clone.json()['id']}", headers=admin).status_code == 204
    )
    assert client.get(f"{API}/criteria-sets/{clone.json()['id']}", headers=admin).status_code == 404


def test_assignment_needs_exactly_one_primary_and_queues_new_sets(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue, db_session: Session
) -> None:
    admin = as_user(world.admin)
    a, b = make(client, admin, "A"), make(client, admin, "B")
    two = [{"set_id": a["id"], "is_primary": True}, {"set_id": b["id"], "is_primary": True}]
    assert assign(client, admin, bp(world), two).status_code == 422
    none = [{"set_id": a["id"]}, {"set_id": b["id"]}]
    assert assign(client, admin, bp(world), none).status_code == 422
    dup = [{"set_id": a["id"], "is_primary": True}, {"set_id": a["id"]}]
    assert assign(client, admin, bp(world), dup).status_code == 422
    res = assign(
        client, admin, bp(world), [{"set_id": b["id"]}, {"set_id": a["id"], "is_primary": True}]
    )
    assert res.status_code == 200, res.text
    assert [(s["set_id"], s["is_primary"], s["position"]) for s in res.json()] == [
        (b["id"], False, 0),
        (a["id"], True, 1),
    ]
    assert [(f, args) for f, args, _ in queue.jobs] == [
        (JOB, (bp(world), b["id"], None)),
        (JOB, (bp(world), a["id"], None)),
    ]
    # Same sets again, primary moved: nothing new to recompute.
    queue.jobs.clear()
    again = [{"set_id": a["id"]}, {"set_id": b["id"], "is_primary": True}]
    assert assign(client, admin, bp(world), again).status_code == 200
    assert queue.jobs == []
    listed = client.get(
        f"{API}/business-projects/{bp(world)}/criteria-sets", headers=as_user(world.an)
    )
    assert [s["is_primary"] for s in listed.json()] == [False, True]
    assert db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "project.criteria_sets")
    )


def test_permissions_admin_manages_qlda_assigns_member_is_refused(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    made = make(client, as_user(world.admin))
    grant(db_session, world.an, bp(world), "manage")  # an: Quản lý dự án of Demo
    qlda, member = as_user(world.an), as_user(world.binh)
    body = {"name": "Của QLDA", "components": parts()}
    assert client.post(f"{API}/criteria-sets", json=body, headers=qlda).status_code == 403
    assert client.post(f"{API}/criteria-sets", json=body, headers=member).status_code == 403
    sets = [{"set_id": made["id"], "is_primary": True}]
    assert assign(client, member, bp(world), sets).status_code == 403
    assert assign(client, qlda, bp(world), sets).status_code == 200
    # QLDA picks among the sets, and recomputes in their dự án.
    assert client.get(f"{API}/criteria-sets", headers=qlda).status_code == 200
    assert client.get(f"{API}/criteria-sets", headers=member).status_code == 403
    url = f"{API}/business-projects/{bp(world)}/criteria-sets/{made['id']}/recompute"
    res = client.post(url, json={"since": "2026-10-01"}, headers=qlda)
    assert res.status_code == 202 and res.json()["since"].startswith("2026-10-01T00:00:00")
    assert client.post(url, headers=member).status_code == 403
    preview = {"components": parts(), "business_project_id": bp(world)}
    assert client.post(f"{API}/criteria/preview", json=preview, headers=qlda).status_code == 403


def test_preview_scores_without_writing(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    body = {"components": parts(), "apply_caps": False, "business_project_id": bp(world)}
    res = client.post(f"{API}/criteria/preview", json=body, headers=as_user(world.admin))
    assert res.status_code == 200, res.text
    (row,) = res.json()["items"]
    assert row["review_id"] == world.an_review.id and row["score"] == 50.0
    # The critical security finding costs the Bảo mật part (25%) only: 25 * 50 / 100 + 75.
    assert row["criteria_score"] == 87.5 and row["capped"] is False
    security = next(c for c in row["breakdown"]["components"] if c["key"] == "security")
    assert security["findings"] == [{"id": world.an_finding.id, "source": "category"}]
    capped = client.post(
        f"{API}/criteria/preview", json={**body, "apply_caps": True}, headers=as_user(world.admin)
    )
    assert capped.json()["items"][0]["criteria_score"] == 50.0
    assert db_session.scalar(select(func.count()).select_from(ReviewCriteriaScore)) == 0


def test_recompute_job_then_review_detail_summary_and_dispute_rescore(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    made = make(client, admin, apply_caps=False)
    assign(client, admin, bp(world), [{"set_id": made["id"], "is_primary": True}])
    world.an_finding.criteria = {str(made["id"]): "correctness"}  # the model's placement
    world.an_review.loc_reviewed = 100
    db_session.flush()
    assert recompute_criteria(db_session, bp(world), made["id"]) == 1

    detail = client.get(f"{API}/reviews/{world.an_review.id}", headers=as_user(world.an)).json()
    (scored,) = detail["criteria_scores"]
    assert (scored["set_id"], scored["set_name"], scored["version"], scored["is_primary"]) == (
        made["id"],
        "Chuẩn",
        1,
        True,
    )
    # Placed in Đúng chức năng (30%) by the model: 30 * 50 / 100 + 70.
    assert Decimal(scored["score"]) == Decimal("85.00")
    correctness = next(c for c in scored["breakdown"]["components"] if c["key"] == "correctness")
    assert correctness["findings"] == [{"id": world.an_finding.id, "source": "ai"}]
    assert detail["findings"][0]["criteria"] == {str(made["id"]): "correctness"}

    summary = client.get(
        f"{API}/criteria/summary?business_project_id={bp(world)}", headers=as_user(world.an)
    ).json()
    assert (summary["set_id"], summary["reviews"], summary["score"]) == (
        made["id"],
        1,
        85.0,
    )
    assert summary["weeks"] == [{"week_start": "2026-10-05", "score": 85.0, "reviews": 1}]
    parts_by_key = {c["key"]: c for c in summary["components"]}
    assert parts_by_key["correctness"]["score"] == 50.0
    # binh sees only their own work: nothing here.
    other = client.get(
        f"{API}/criteria/summary?set_id={made['id']}", headers=as_user(world.binh)
    ).json()
    assert other["reviews"] == 0 and other["score"] is None and other["components"] == []

    # A dispute accepted: the finding stops counting, the criteria score follows.
    raised = client.post(
        f"{API}/findings/{world.an_finding.id}/dispute",
        headers=as_user(world.an),
        json={"reason": "Validated by the caller."},
    ).json()
    decided = client.post(
        f"{API}/disputes/{raised['id']}/decide",
        headers=as_user(world.lead),
        json={"decision": "accepted"},
    )
    assert decided.status_code == 200, decided.text
    row = db_session.get(ReviewCriteriaScore, (world.an_review.id, made["id"]))
    assert row is not None
    db_session.refresh(row)
    assert row.score == Decimal("100.00")
