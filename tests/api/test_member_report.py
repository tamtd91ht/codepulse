"""Báo cáo thành viên (docs/specs/23): the JSON report and the Excel export."""

import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Commit, Project, Review
from app.review.rubric import RUBRIC_VERSION
from app.scoring.member_report import assess
from tests.api.conftest import World
from tests.api.test_criteria_api import assign, bp, make

pytestmark = pytest.mark.integration

API = "/api/v1"
OCT = {"from": "2026-10-01", "to": "2026-10-31"}


def binh_done(db_session: Session, world: World, score: int = 90, loc: int = 200) -> Review:
    """A finished review of Binh in October, so the peers have someone besides An."""
    db_session.add(Commit(sha="c" * 40, project_id=world.project.id, message="feat: ok\n"))
    review = Review(
        target_type="commit",
        commit_sha="c" * 40,
        reviewed_sha="c" * 40,
        status="done",
        author_user_id=world.binh.id,
        score=Decimal(score),
        loc_reviewed=loc,
        verdict="approve",
        finished_at=datetime(2026, 10, 8, 9, tzinfo=UTC),
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review)
    db_session.flush()
    return review


def test_the_report_judges_against_peers_and_the_period_before(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.an_review.loc_reviewed = 100
    world.an_review.verdict = "block"
    binh_done(db_session, world)
    lead = as_user(world.lead)
    res = client.get(f"{API}/members/{world.an.id}/report", params=OCT, headers=lead)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from"] == "2026-10-01" and body["to"] == "2026-10-31"
    assert body["previous"]["from"] == "2026-08-31" and body["previous"]["to"] == "2026-09-30"
    assert body["summary"]["reviews"] == 1 and body["summary"]["critical"] == 1
    assert body["peers"]["members"] == 2 and body["peers"]["reviews"] == 2
    a = body["assessment"]
    assert a["level"] == "support" and a["label"] == "Cần hỗ trợ" and a["thin"] is True
    assert any("so với mặt bằng" in r for r in a["reasons"])
    assert any("1 critical" in r for r in a["reasons"])
    (severe,) = body["severe_reviews"]
    assert severe["ref"] == "!7" and severe["critical"] == 1
    assert body["categories"][0]["category"] == "security"
    assert body["categories"][0]["share"] == 100.0
    assert len(body["weeks"]) == len(body["trend"]) == len(body["peer_trend"])
    assert "_facts" not in body
    # Reading someone else's report is audited.
    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "member_report.read")).one()
    assert row.target == f"user:{world.an.id}"


def test_filters_by_repo_and_refuses_what_one_may_not_see(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    other = Project(
        connection_id=world.project.connection_id,
        external_id=2002,
        path_with_namespace="demo/other",
        business_project_id=world.project.business_project_id,
    )
    db_session.add(other)
    db_session.flush()
    lead = as_user(world.lead)
    only_other = client.get(
        f"{API}/members/{world.an.id}/report",
        params={**OCT, "project_id": other.id},
        headers=lead,
    ).json()
    assert only_other["summary"]["reviews"] == 0
    assert only_other["assessment"]["level"] == "none"
    assert only_other["filters"]["project_path"] == "demo/other"
    assert only_other["filters"]["business_project_name"] == "Demo"
    # A repo outside the dự án chosen.
    assert (
        client.get(
            f"{API}/members/{world.an.id}/report",
            params={**OCT, "project_id": other.id, "business_project_id": 999999},
            headers=lead,
        ).status_code
        == 404  # a dự án that does not exist: 404 before any mismatch
    )
    # A member reads only their own report.
    assert (
        client.get(
            f"{API}/members/{world.an.id}/report", params=OCT, headers=as_user(world.binh)
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{API}/members/{world.an.id}/report", params=OCT, headers=as_user(world.an)
        ).status_code
        == 200
    )
    bad = client.get(
        f"{API}/members/{world.an.id}/report",
        params={"from": "2026-10-31", "to": "2026-10-01"},
        headers=lead,
    )
    assert bad.status_code == 422


def test_the_workbook_has_every_sheet_and_the_criteria(
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

    report = client.get(
        f"{API}/members/{world.an.id}/report",
        params={**OCT, "business_project_id": bp(world)},
        headers=as_user(world.lead),
    ).json()
    (crit,) = report["criteria_sets"]
    assert crit["name"] == "Chuẩn OmiCRM" and crit["is_primary"] and crit["reviews"] == 1

    res = client.get(
        f"{API}/members/{world.an.id}/report.xlsx", params=OCT, headers=as_user(world.lead)
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "codepulse-bao-cao-" in res.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == [
        "Tổng quan",
        "Theo tuần",
        "Theo repo",
        "Loại lỗi",
        "Theo tiêu chí",
        "Review",
    ]
    overview = wb["Tổng quan"]
    assert str(overview["A1"].value).startswith("Báo cáo thành viên:")
    assert "Cần hỗ trợ" in str(overview["A4"].value)
    crit_rows = list(wb["Theo tiêu chí"].iter_rows(values_only=True))
    assert crit_rows[1][0] == "Chuẩn OmiCRM v1 (bộ chính)" and crit_rows[1][3] == 85.0
    reviews = list(wb["Review"].iter_rows(values_only=True))
    assert reviews[0][-2] == "Tiêu chí: Chuẩn OmiCRM"
    assert reviews[1][3] == "!7" and reviews[1][-2] == 85.0
    assert str(reviews[1][-1]).endswith(f"/reviews/{world.an_review.id}")
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "member_report.export")
    ).one()


def test_assess_levels() -> None:
    base = {
        "reviews": 6,
        "avg_score": 88.0,
        "pass_rate": 80.0,
        "critical": 0,
        "high": 1,
        "findings_per_100_loc": 0.5,
    }
    peers = {"members": 4, "avg_score": 80.0, "pass_rate": 70.0, "findings_per_100_loc": 1.0}
    good = assess(base, {"avg_score": 85.0}, peers)
    assert good.level == "good" and not good.thin
    assert (
        good.reasons[0]
        == "Điểm TB 88.0/100 (theo LOC), +8 so với mặt bằng 80.0, +3 so với kỳ trước."
    )
    assert "thấp hơn mặt bằng" in good.reasons[-1]
    assert assess({**base, "avg_score": 75.0}, {}, peers).level == "fair"
    assert assess({**base, "pass_rate": 40.0}, {}, peers).level == "fair"
    assert assess({**base, "critical": 1}, {}, peers).level == "support"
    assert assess({**base, "avg_score": 60.0}, {}, peers).level == "support"
    assert assess({**base, "reviews": 0}, {}, peers).level == "none"


def test_hidden_repos_and_formulas_never_leak(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    hidden = Project(
        connection_id=world.project.connection_id,
        external_id=3003,
        path_with_namespace="secret/unassigned",
        business_project_id=None,
    )
    db_session.add(hidden)
    # A commit title that Excel would run as a formula.
    db_session.add(Commit(sha="d" * 40, project_id=world.project.id, message="=1+1\n"))
    db_session.add(
        Review(
            target_type="commit",
            commit_sha="d" * 40,
            reviewed_sha="d" * 40,
            status="done",
            author_user_id=world.an.id,
            score=Decimal(70),
            loc_reviewed=10,
            finished_at=datetime(2026, 10, 9, 9, tzinfo=UTC),
            rubric_version=RUBRIC_VERSION,
        )
    )
    db_session.flush()
    me = as_user(world.an)
    for params in ({"project_id": hidden.id}, {"business_project_id": 999999}):
        res = client.get(
            f"{API}/members/{world.an.id}/report", params={**OCT, **params}, headers=me
        )
        assert res.status_code == 404, params
    res = client.get(f"{API}/members/{world.an.id}/report.xlsx", params=OCT, headers=me)
    assert res.status_code == 200
    sheet = load_workbook(io.BytesIO(res.content))["Review"]
    titles = [row[4] for row in sheet.iter_rows(min_row=2)]
    formula = next(c for c in titles if c.value == "=1+1")
    assert formula.data_type == "s"
