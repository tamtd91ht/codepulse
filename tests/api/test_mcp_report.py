"""Tích hợp MCP report (docs/specs/26 §13): scoping by `report.mcp`, every metric on a small
world, the filters, the logs and the Excel export."""

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BusinessProject,
    Commit,
    FindingClosure,
    McpCall,
    PermissionSet,
    PermissionSetItem,
    Project,
    ProjectGrant,
    Review,
    ReviewFinding,
)
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1/mcp-report"
OCT = {"from": "2026-10-01", "to": "2026-10-31"}


def at(day: int, hour: int = 9, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@dataclass
class Facts:
    f1: ReviewFinding
    f2: ReviewFinding
    f3: ReviewFinding
    f4: ReviewFinding
    b1: ReviewFinding


def _finding(s: Session, review: Review, severity: str, status: str = "open") -> ReviewFinding:
    row = ReviewFinding(
        review_id=review.id,
        severity=severity,
        category="security",
        file_path="app/x.py",
        line_start=1,
        title=f"{severity} issue",
        status=status,
    )
    s.add(row)
    s.flush()
    return row


def _call(s: Session, user_id: int, when: datetime, tool: str, **kw: Any) -> None:
    s.add(
        McpCall(
            created_at=when,
            user_id=user_id,
            tool=tool,
            ok=kw.pop("ok", True),
            duration_ms=5,
            **kw,
        )
    )


def _request(
    s: Session,
    finding: ReviewFinding,
    user_id: int,
    when: datetime,
    status: str,
    *,
    via: str = "mcp",
    decided: datetime | None = None,
    tests: list[str] | None = None,
) -> FindingClosure:
    row = FindingClosure(
        finding_id=finding.id,
        kind="request",
        status=status,
        via=via,
        requested_by=user_id,
        created_at=when,
        decided_at=decided,
        shas=["f" * 40],
        reason="chưa đủ" if status == "unmet" else None,
        meta=(
            {
                "files_changed": ["app/x.py", "tests/test_x.py"],
                "tests": [{"command": "pytest", "result": r} for r in (tests or [])],
                "token_name": "laptop",
            }
            if via == "mcp"
            else None
        ),
    )
    s.add(row)
    s.flush()
    return row


@pytest.fixture
def facts(db_session: Session, world: World) -> Facts:
    """An: 6 calls in October over 2 days (1 error, 1 rate limited), 1 in September; requests
    on f1 (unmet, then closed), f2 (closed), f3 (pending); a web request on f4 (rejected).
    Binh: 1 call, 1 cancelled request. Outsider: 1 call."""
    s = db_session
    world.an_review.finished_at = at(6)
    f1 = world.an_finding  # critical
    f1.status = "fixed"
    f2 = _finding(s, world.an_review, "high", status="fixed")
    f3 = _finding(s, world.an_review, "critical")
    f4 = _finding(s, world.an_review, "high")
    s.add(Commit(sha="c" * 40, project_id=world.project.id))
    binh_review = Review(
        target_type="commit",
        commit_sha="c" * 40,
        reviewed_sha="c" * 40,
        status="done",
        author_user_id=world.binh.id,
        finished_at=at(4),
        rubric_version=RUBRIC_VERSION,
    )
    s.add(binh_review)
    s.flush()
    b1 = _finding(s, binh_review, "high")
    an, pid = world.an.id, world.project.id
    _call(s, an, at(7), "list_findings", project_id=pid, result_count=3)
    _call(s, an, at(7, 10), "get_finding", project_id=pid, finding_id=f1.id, result_count=1)
    _call(s, an, at(8), "get_finding", project_id=pid, finding_id=f1.id, result_count=1)
    _call(s, an, at(8, 10), "get_commit_findings", project_id=pid, result_count=2)
    _call(s, an, at(8, 11), "get_finding", ok=False, error_code="not_found", finding_id=9)
    _call(s, an, at(8, 12), "list_close_requests", ok=False, error_code="rate_limited")
    _call(s, an, at(15, month=9), "list_findings", result_count=1)
    _call(s, world.binh.id, at(5), "get_finding", project_id=pid, finding_id=b1.id)
    _call(s, world.outsider.id, at(5), "list_findings", result_count=0)
    _request(s, f1, an, at(7), "unmet", tests=["passed", "failed"])
    _request(s, f1, an, at(8), "closed", decided=at(9), tests=["passed"])
    _request(s, f2, an, at(10), "closed", decided=at(11))
    _request(s, f3, an, at(12), "pending", tests=["passed"])
    _request(s, f4, an, at(15), "rejected", via="web", decided=at(16))
    _request(s, b1, world.binh.id, at(6), "cancelled", decided=at(6, 10))
    s.flush()
    return Facts(f1=f1, f2=f2, f3=f3, f4=f4, b1=b1)


def _summary(client: TestClient, headers: dict[str, str], **params: Any) -> dict[str, Any]:
    res = client.get(f"{API}/summary", params={**OCT, **params}, headers=headers)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def _member(body: dict[str, Any], user_id: int) -> dict[str, Any]:
    return next(m for m in body["members"] if m["user_id"] == user_id)


def test_every_metric_of_a_member(
    client: TestClient, world: World, as_user: Any, facts: Facts
) -> None:
    body = _summary(client, as_user(world.an))
    assert body["period"] == {"from": "2026-10-01", "to": "2026-10-31"}
    assert body["previous"] == {"from": "2026-08-31", "to": "2026-09-30"}
    assert [m["user_id"] for m in body["members"]] == [world.an.id]
    m = _member(body, world.an.id)
    assert m["name"] == "An" and m["email"] == "an@x.vn"
    assert (m["calls"], m["active_days"], m["findings_read"]) == (6, 2, 3)
    assert m["error_rate"] == 0.2 and m["rate_limited"] == 1  # 1 error of 5, rate_limited apart
    assert m["requests"] == {
        "closed": 2,
        "unmet": 1,
        "rejected": 0,
        "pending": 1,
        "cancelled": 0,
        "failed": 0,
    }
    assert m["first_pass_rate"] == 0.5  # f1 unmet first, f2 closed first, f3 not settled
    assert m["resubmits"] == 1
    assert m["median_hours_to_request"] == 96.0  # first requests: 24h, 96h, 144h
    assert m["median_hours_to_close"] == 96.0  # closed: 72h, 120h
    assert m["with_tests_rate"] == 0.75 and m["tests_pass_rate"] == 0.6667
    assert m["backlog"] == 2  # f3 and f4 still open
    assert m["web"] == {"requests": 1, "closed": 0, "pass_rate": 0.0}
    assert body["total"]["user_id"] is None and body["total"]["calls"] == 6
    assert body["kpis"] == {
        "calls": 6,
        "active_users": 1,
        "requests": 4,
        "first_pass_rate": 0.5,
        "median_hours_to_close": 96.0,
        "prev": {
            "calls": 1,
            "active_users": 1,
            "requests": 0,
            "first_pass_rate": None,
            "median_hours_to_close": None,
        },
    }
    assert "DPI" in body["note"]


def test_scoping_by_report_mcp(
    client: TestClient, world: World, as_user: Any, facts: Facts, db_session: Session
) -> None:
    lead = _summary(client, as_user(world.lead))
    assert {m["user_id"] for m in lead["members"]} == {world.an.id, world.binh.id}
    assert lead["total"]["calls"] == 7 and lead["kpis"]["active_users"] == 2
    assert lead["total"]["requests"]["cancelled"] == 1
    assert lead["total"]["backlog"] == 3 and lead["total"]["first_pass_rate"] == pytest.approx(
        1 / 3, abs=1e-4
    )
    admin = _summary(client, as_user(world.admin))
    assert {m["user_id"] for m in admin["members"]} == {
        world.an.id,
        world.binh.id,
        world.outsider.id,
    }
    # A member sees only themselves: naming someone else is refused.
    res = client.get(
        f"{API}/summary", params={**OCT, "user_id": world.binh.id}, headers=as_user(world.an)
    )
    assert res.status_code == 403
    # Without report.mcp: 403 everywhere.
    member_set = select(PermissionSet.id).where(PermissionSet.auto_role == "member")
    db_session.execute(
        delete(PermissionSetItem).where(
            PermissionSetItem.permission == "report.mcp",
            PermissionSetItem.set_id.in_(member_set),
        )
    )
    db_session.flush()
    for path in ("summary", "requests", "export.xlsx"):
        assert client.get(f"{API}/{path}", headers=as_user(world.an)).status_code == 403, path
    reads = db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp_report.read")).all()
    assert len(reads) == 2  # the lead and the admin; refusals are not reads


def test_filters(
    client: TestClient, world: World, as_user: Any, facts: Facts, db_session: Session
) -> None:
    an = as_user(world.an)
    high = _member(_summary(client, an, severity="high"), world.an.id)
    assert high["requests"]["closed"] == 1 and sum(high["requests"].values()) == 1  # f2 only
    assert high["backlog"] == 1 and high["web"]["requests"] == 1  # f4
    assert high["calls"] == 6  # calls have no severity
    repo = _member(_summary(client, an, project_id=world.project.id), world.an.id)
    assert (repo["calls"], repo["error_rate"], repo["rate_limited"]) == (4, 0.0, 0)
    assert sum(repo["requests"].values()) == 4
    sept = _summary(client, an, **{"from": "2026-09-01", "to": "2026-09-30"})
    assert _member(sept, world.an.id)["calls"] == 1 and sept["kpis"]["requests"] == 0
    lead = as_user(world.lead)
    team = _summary(client, lead, team_id=world.backend.id)
    assert {m["user_id"] for m in team["members"]} == {world.an.id, world.binh.id}
    one = _summary(client, lead, user_id=world.binh.id)
    assert [m["user_id"] for m in one["members"]] == [world.binh.id]
    other = BusinessProject(name="Other")
    db_session.add(other)
    db_session.flush()
    admin = as_user(world.admin)
    empty = _summary(client, admin, business_project_id=other.id, user_id=world.an.id)
    m = _member(empty, world.an.id)  # the person asked for, even without activity
    assert m["calls"] == 0 and sum(m["requests"].values()) == 0 and m["backlog"] == 0
    mobile = _summary(client, admin, team_id=world.mobile.id)
    assert [m["user_id"] for m in mobile["members"]] == [world.outsider.id]
    bad = client.get(f"{API}/summary", params={"severity": "low"}, headers=an)
    assert bad.status_code == 422


def test_requests_log(client: TestClient, world: World, as_user: Any, facts: Facts) -> None:
    res = client.get(f"{API}/requests", params=OCT, headers=as_user(world.an))
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["total"], body["page"], body["page_size"]) == (4, 1, 50)
    first = body["items"][0]  # newest first: f3 pending
    assert first["finding"]["id"] == facts.f3.id and first["status"] == "pending"
    assert first["finding"]["severity"] == "critical" and first["finding"]["title"]
    assert first["project"] == {"id": world.project.id, "path": "demo/p"}
    assert first["user"] == {"id": world.an.id, "name": "An", "email": "an@x.vn"}
    assert first["files_changed"] == 2 and first["token_name"] == "laptop"
    assert first["tests"] == {"total": 1, "passed": 1, "failed": 0, "not_run": 0}
    assert first["review_id"] == world.an_review.id
    by_attempt = {(i["finding"]["id"], i["attempt"]): i["status"] for i in body["items"]}
    assert by_attempt[(facts.f1.id, 1)] == "unmet" and by_attempt[(facts.f1.id, 2)] == "closed"
    unmet = next(i for i in body["items"] if i["status"] == "unmet")
    assert unmet["reason"] == "chưa đủ"
    page2 = client.get(f"{API}/requests", params={**OCT, "page": 2}, headers=as_user(world.an))
    assert page2.json()["items"] == [] and page2.json()["total"] == 4


def test_calls_log_is_for_admins(
    client: TestClient, world: World, as_user: Any, facts: Facts
) -> None:
    assert client.get(f"{API}/calls", params=OCT, headers=as_user(world.lead)).status_code == 403
    assert client.get(f"{API}/calls", params=OCT, headers=as_user(world.an)).status_code == 403
    admin = as_user(world.admin)
    res = client.get(f"{API}/calls", params=OCT, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 8
    failed = client.get(
        f"{API}/calls", params={**OCT, "ok": "false", "user_id": world.an.id}, headers=admin
    ).json()
    assert {i["error_code"] for i in failed["items"]} == {"not_found", "rate_limited"}
    got = client.get(f"{API}/calls", params={**OCT, "tool": "get_commit_findings"}, headers=admin)
    [item] = got.json()["items"]
    assert item["result_count"] == 2 and item["project"]["path"] == "demo/p"
    assert item["user"]["id"] == world.an.id and item["ok"] is True


def test_export_has_both_sheets(
    client: TestClient, world: World, as_user: Any, facts: Facts, db_session: Session
) -> None:
    res = client.get(f"{API}/export.xlsx", params=OCT, headers=as_user(world.lead))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert (
        "codepulse-tich-hop-mcp-20261001-20261031.xlsx" in res.headers["content-disposition"]
    )
    wb = load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["Theo thành viên", "Yêu cầu đóng qua MCP"]
    members = [r for r in wb["Theo thành viên"].iter_rows(values_only=True) if r and r[0]]
    names = [r[0] for r in members]
    assert "An" in names and "Binh" in names and "Tổng" in names
    requests = [
        r for r in wb["Yêu cầu đóng qua MCP"].iter_rows(values_only=True) if isinstance(r[1], int)
    ]
    assert len(requests) == 5  # An's 4 + Binh's 1
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "mcp_report.export")).one()


def test_a_project_manager_sees_calls_of_managed_projects_only(
    client: TestClient, world: World, as_user: Any, facts: Facts, db_session: Session
) -> None:
    """The outsider manages dự án Other and only views Demo: an is in their person scope (a
    member of Other), so an's calls about Other's repos and about no repo count, never those
    about demo/p (a dự án they only view)."""
    s = db_session
    other = BusinessProject(name="Other")
    s.add(other)
    s.flush()
    repo = Project(
        connection_id=world.project.connection_id,
        external_id=3001,
        path_with_namespace="other/r",
        business_project_id=other.id,
    )
    s.add(repo)
    s.flush()
    s.add_all(
        [
            ProjectGrant(business_project_id=other.id, user_id=world.outsider.id, role="manage"),
            ProjectGrant(business_project_id=other.id, user_id=world.an.id, role="view"),
        ]
    )
    _call(s, world.an.id, at(9), "list_findings", project_id=repo.id, result_count=1)
    s.flush()
    pm = _member(_summary(client, as_user(world.outsider)), world.an.id)
    assert pm["calls"] == 3  # other/r + the 2 calls about no repo; demo/p's 4 left out
    assert _member(_summary(client, as_user(world.admin)), world.an.id)["calls"] == 7
