"""Báo cáo nhóm (docs/specs/23 §8): several people side by side, and the Excel export."""

import io
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog
from tests.api.conftest import World
from tests.api.test_criteria_api import assign, bp, make
from tests.api.test_member_report import OCT, binh_done

pytestmark = pytest.mark.integration

API = "/api/v1"


def test_a_team_side_by_side_weakest_first(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.an_review.loc_reviewed = 100
    binh_done(db_session, world)
    lead = as_user(world.lead)
    res = client.get(
        f"{API}/members/team-report", params={**OCT, "team_id": world.backend.id}, headers=lead
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["filters"]["team_name"] == world.backend.name
    names = [m["user"]["id"] for m in body["members"]]
    # An (critical) before Binh (90, approve); everyone of the team gets a row.
    assert names.index(world.an.id) < names.index(world.binh.id)
    an = next(m for m in body["members"] if m["user"]["id"] == world.an.id)
    binh = next(m for m in body["members"] if m["user"]["id"] == world.binh.id)
    assert an["assessment"]["level"] == "support" and binh["assessment"]["level"] == "good"
    assert body["levels"]["support"] >= 1 and body["levels"]["good"] >= 1
    assert body["summary"]["reviews"] == 2
    assert body["categories"][0]["category"] == "security"
    assert an["categories"]["security"] == 100.0 and an["top_category"]
    assert len(an["trend"]) == len(body["weeks"])
    assert body["criteria"] is None
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "member_report.team_read")
    ).first()


def test_who_may_be_compared(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    lead = as_user(world.lead)
    # Someone the lead does not cover cannot be named.
    res = client.get(
        f"{API}/members/team-report",
        params={**OCT, "user_ids": f"{world.an.id},{world.outsider.id}"},
        headers=lead,
    )
    assert res.status_code == 403
    assert (
        client.get(
            f"{API}/members/team-report", params={**OCT, "user_ids": "x"}, headers=lead
        ).status_code
        == 422
    )
    # A plain member has no Thành viên page, so no comparison either.
    assert (
        client.get(f"{API}/members/team-report", params=OCT, headers=as_user(world.an)).status_code
        == 403
    )
    named = client.get(
        f"{API}/members/team-report",
        params={**OCT, "user_ids": str(world.binh.id)},
        headers=lead,
    ).json()
    assert [m["user"]["id"] for m in named["members"]] == [world.binh.id]
    assert named["members"][0]["assessment"]["level"] == "none"


def test_criteria_matrix_and_workbook(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.workers.criteria_jobs import recompute_criteria

    admin = as_user(world.admin)
    made = make(client, admin, name="Chuẩn OmiCRM", apply_caps=False)
    assign(client, admin, bp(world), [{"set_id": made["id"], "is_primary": True}])
    world.an_finding.criteria = {str(made["id"]): "correctness"}
    world.an_review.loc_reviewed = 100
    binh_done(db_session, world)
    db_session.flush()
    assert recompute_criteria(db_session, bp(world), made["id"]) >= 1
    lead = as_user(world.lead)
    params = {**OCT, "team_id": world.backend.id}
    body = client.get(f"{API}/members/team-report", params=params, headers=lead).json()
    crit = body["criteria"]
    assert crit["name"] == "Chuẩn OmiCRM"
    an = next(m for m in body["members"] if m["user"]["id"] == world.an.id)
    assert an["criteria"]["score"] == 85.0
    assert an["criteria"]["components"]["correctness"] == 50.0
    assert crit["group"]["reviews"] == 2
    assert (
        client.get(
            f"{API}/members/team-report", params={**params, "set_id": 999999}, headers=lead
        ).status_code
        == 404
    )

    res = client.get(f"{API}/members/team-report.xlsx", params=params, headers=lead)
    assert res.status_code == 200, res.text
    assert "codepulse-bao-cao-nhom-" in res.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == [
        "Tổng quan nhóm",
        "So sánh thành viên",
        "Loại lỗi",
        "Tiêu chí - Chuẩn OmiCRM",
        "Review",
    ]
    compare = list(wb["So sánh thành viên"].iter_rows(values_only=True))
    assert compare[1][1] == "Cần hỗ trợ (ít dữ liệu)"
    crit_rows = list(wb["Tiêu chí - Chuẩn OmiCRM"].iter_rows(values_only=True))
    assert crit_rows[-1][0] == "Cả nhóm"
    reviews = list(wb["Review"].iter_rows(values_only=True))
    assert len(reviews) == 3 and {r[1] for r in reviews[1:]} == {"An", "Binh"}
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "member_report.team_export")
    ).first()


def test_awkward_names_never_break_or_inject_the_workbook(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    from app.models import Commit, CriteriaSetVersion, Review
    from app.review.rubric import RUBRIC_VERSION
    from app.workers.criteria_jobs import recompute_criteria
    from tests.api.test_criteria_api import parts

    admin = as_user(world.admin)
    components = parts()
    components[0]["name"] = "=1+1"
    made = make(client, admin, name="A/B [x]: y?", apply_caps=False, components=components)
    assign(client, admin, bp(world), [{"set_id": made["id"], "is_primary": True}])
    world.an_review.loc_reviewed = 100
    db_session.add(Commit(sha="e" * 40, project_id=world.project.id, message="fix \x1b[31mred\n"))
    db_session.add(
        Review(
            target_type="commit",
            commit_sha="e" * 40,
            reviewed_sha="e" * 40,
            status="done",
            author_user_id=world.an.id,
            score=70,
            loc_reviewed=10,
            finished_at=world.an_review.finished_at,
            rubric_version=RUBRIC_VERSION,
        )
    )
    db_session.flush()
    assert recompute_criteria(db_session, bp(world), made["id"]) >= 1
    assert db_session.scalars(select(CriteriaSetVersion)).first() is not None
    lead = as_user(world.lead)
    params = {**OCT, "team_id": world.backend.id}
    res = client.get(f"{API}/members/team-report.xlsx", params=params, headers=lead)
    assert res.status_code == 200, res.text
    wb = load_workbook(io.BytesIO(res.content))
    title = next(n for n in wb.sheetnames if n.startswith("Tiêu chí"))
    assert "/" not in title and "[" not in title and ":" not in title
    header = next(wb[title].iter_rows(min_row=1, max_row=1))
    injected = next(c for c in header if str(c.value).startswith("=1+1"))
    assert injected.data_type == "s"
    titles = [r[5] for r in wb["Review"].iter_rows(min_row=2, values_only=True)]
    assert "fix [31mred" in titles  # the escape character is dropped, the rest kept
    # An unknown set is 404 even when the group has scored reviews.
    assert (
        client.get(
            f"{API}/members/team-report", params={**params, "set_id": 999999}, headers=lead
        ).status_code
        == 404
    )


def test_a_hidden_team_is_refused(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    res = client.get(
        f"{API}/members/team-report",
        params={**OCT, "team_id": world.mobile.id},
        headers=as_user(world.lead),
    )
    assert res.status_code == 403
