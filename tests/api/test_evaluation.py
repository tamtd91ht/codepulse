"""Per-member evaluation: /members/evaluation and /members/{id}/evaluation."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, BusinessProject, Commit, Project, Review, ReviewFinding
from tests.api.conftest import World

pytestmark = pytest.mark.integration

RANGE = "from=2026-10-01&to=2026-10-31"  # holds the world's reviews (2026-10-06)


@pytest.fixture
def second_repo(db_session: Session, world: World) -> Project:
    """Binh gets a finished review in another repo of another dự án; An's repo joins OmiCRM."""
    bp = BusinessProject(name="OmiCRM")
    other_bp = BusinessProject(name="Other")
    db_session.add_all([bp, other_bp])
    db_session.flush()
    world.project.business_project_id = bp.id
    other = Project(
        connection_id=world.project.connection_id,
        external_id=1002,
        path_with_namespace="demo/q",
        business_project_id=other_bp.id,
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(Commit(sha="c" * 40, project_id=other.id))
    db_session.flush()
    done = Review(
        target_type="commit",
        commit_sha="c" * 40,
        reviewed_sha="c" * 40,
        status="done",
        author_user_id=world.binh.id,
        score=Decimal(90),
        loc_reviewed=200,
        verdict="approve",
        finished_at=datetime(2026, 10, 8, 9, tzinfo=UTC),
        rubric_version="v3",
    )
    db_session.add(done)
    db_session.flush()
    db_session.add_all(
        [
            ReviewFinding(review_id=done.id, severity="medium", category="testing", title="t"),
            ReviewFinding(
                review_id=done.id,
                severity="high",
                category="bug",
                title="disputed",
                status="false_positive",
            ),
        ]
    )
    db_session.flush()
    return other


def test_admin_sees_everyone_with_their_numbers(
    client: TestClient, world: World, second_repo: Project, as_user: Any, db_session: Session
) -> None:
    res = client.get(f"/api/v1/members/evaluation?{RANGE}", headers=as_user(world.admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from"] == "2026-10-01" and body["to"] == "2026-10-31"
    assert body["weeks"][0] == "2026-09-28" and body["weeks"][-1] == "2026-10-26"
    rows = {r["user"]["primary_email"]: r for r in body["members"]}
    # Everyone active is a row, reviewed or not; the rows with reviews come first (lowest avg).
    assert set(rows) >= {"an@x.vn", "binh@x.vn", "lead@x.vn", "outsider@x.vn"}
    assert [r["user"]["primary_email"] for r in body["members"][:2]] == ["an@x.vn", "binh@x.vn"]
    an = rows["an@x.vn"]
    assert an["reviews"] == 1 and an["mrs"] == 1 and an["avg_score"] == 50.0
    assert an["critical"] == 1 and an["needs_attention"] is True
    assert an["teams"] == ["Backend"]
    assert an["trend"][1] == 50.0  # the week of 2026-10-05
    binh = rows["binh@x.vn"]
    # The failed review of the world does not count; the finished one in demo/q does, without
    # the disputed high.
    assert binh["reviews"] == 1 and binh["commits"] == 1 and binh["avg_score"] == 90.0
    assert binh["medium"] == 1 and binh["high"] == 0 and binh["disputed"] == 1
    assert binh["verdicts"]["approve"] == 1 and binh["pass_rate"] == 100.0
    assert binh["findings_per_100_loc"] == 0.5
    assert rows["lead@x.vn"]["reviews"] == 0 and rows["lead@x.vn"]["avg_score"] is None
    assert body["totals"]["members"] == 2 and body["totals"]["reviews"] == 2
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "evaluation.read")).first()


def test_rows_follow_the_viewer_scope(
    client: TestClient, world: World, second_repo: Project, as_user: Any
) -> None:
    def emails(user: Any, extra: str = "") -> set[str]:
        res = client.get(f"/api/v1/members/evaluation?{RANGE}{extra}", headers=as_user(user))
        assert res.status_code == 200, res.text
        return {r["user"]["primary_email"] for r in res.json()["members"]}

    # A developer: only themselves. A lead: themselves and the people they lead. A manager:
    # nobody but themselves (Phase 0: aggregates only).
    assert emails(world.an) == {"an@x.vn"}
    assert emails(world.lead) == {"lead@x.vn", "an@x.vn", "binh@x.vn"}
    assert emails(world.manager) == {"manager@x.vn"}
    # The team filter narrows to its members at the time, within the viewer's scope.
    assert emails(world.admin, f"&team_id={world.backend.id}") == {
        "lead@x.vn",
        "an@x.vn",
        "binh@x.vn",
    }
    assert emails(world.lead, f"&team_id={world.backend.id}") == {
        "lead@x.vn",
        "an@x.vn",
        "binh@x.vn",
    }
    res = client.get(
        f"/api/v1/members/evaluation?{RANGE}&team_id={world.backend.id}",
        headers=as_user(world.outsider),
    )
    assert res.status_code == 403


def test_repo_and_project_filters_keep_only_people_reviewed_there(
    client: TestClient, world: World, second_repo: Project, as_user: Any
) -> None:
    res = client.get(
        f"/api/v1/members/evaluation?{RANGE}&project_id={second_repo.id}",
        headers=as_user(world.admin),
    )
    assert res.status_code == 200, res.text
    assert [r["user"]["primary_email"] for r in res.json()["members"]] == ["binh@x.vn"]
    bp = world.project.business_project_id
    res = client.get(
        f"/api/v1/members/evaluation?{RANGE}&business_project_id={bp}",
        headers=as_user(world.admin),
    )
    assert [r["user"]["primary_email"] for r in res.json()["members"]] == ["an@x.vn"]


def test_range_is_validated(client: TestClient, world: World, as_user: Any) -> None:
    headers = as_user(world.admin)
    assert (
        client.get(
            "/api/v1/members/evaluation?from=2026-10-31&to=2026-10-01", headers=headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/members/evaluation?from=2025-01-01&to=2026-10-01", headers=headers
        ).status_code
        == 422
    )
    # No range: the last 30 days up to today.
    res = client.get("/api/v1/members/evaluation", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert (date.fromisoformat(body["to"]) - date.fromisoformat(body["from"])).days == 29


def test_member_detail_breaks_down_by_repo(
    client: TestClient, world: World, second_repo: Project, as_user: Any
) -> None:
    res = client.get(
        f"/api/v1/members/{world.binh.id}/evaluation?{RANGE}", headers=as_user(world.lead)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["primary_email"] == "binh@x.vn"
    assert body["summary"]["reviews"] == 1 and body["summary"]["repos"] == 1
    assert body["by_repo"] == [
        {
            **body["summary"],
            "project_id": second_repo.id,
            "path": "demo/q",
        }
    ]
    assert body["categories"] == [{"category": "testing", "count": 1}]
    assert body["trend"][1] == 90.0 and body["trend_reviews"][1] == 1
    # Binh may read their own page; An may not read Binh's; a manager neither.
    assert (
        client.get(
            f"/api/v1/members/{world.binh.id}/evaluation?{RANGE}", headers=as_user(world.binh)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/members/{world.binh.id}/evaluation?{RANGE}", headers=as_user(world.an)
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/members/{world.binh.id}/evaluation?{RANGE}", headers=as_user(world.manager)
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/members/999999/evaluation?{RANGE}", headers=as_user(world.admin)
        ).status_code
        == 404
    )


def test_filters_never_widen_the_viewer_scope(
    client: TestClient, world: World, second_repo: Project, as_user: Any
) -> None:
    def emails(user: Any, extra: str) -> set[str]:
        res = client.get(f"/api/v1/members/evaluation?{RANGE}{extra}", headers=as_user(user))
        assert res.status_code == 200, res.text
        return {r["user"]["primary_email"] for r in res.json()["members"]}

    # A manager filtering on a team they manage still sees only themselves (Phase 0), and
    # nobody when they are not a member of the team filtered on.
    assert emails(world.manager, f"&team_id={world.engineering.id}") == {"manager@x.vn"}
    assert emails(world.manager, f"&team_id={world.backend.id}") == set()
    # A developer filtering on a repo / project where someone else was reviewed sees nobody.
    assert emails(world.an, f"&project_id={second_repo.id}") == set()
    assert emails(world.an, f"&business_project_id={second_repo.business_project_id}") == set()


def test_people_without_reviews_have_the_same_row_shape(
    client: TestClient, world: World, as_user: Any
) -> None:
    res = client.get(f"/api/v1/members/evaluation?{RANGE}", headers=as_user(world.lead))
    rows = {r["user"]["primary_email"]: r for r in res.json()["members"]}
    assert rows["lead@x.vn"]["verdicts"] == {
        "approve": 0,
        "needs_human": 0,
        "fix_required": 0,
        "block": 0,
    }


def test_inactive_people_are_left_out(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    world.an.active = False
    db_session.flush()
    res = client.get(f"/api/v1/members/evaluation?{RANGE}", headers=as_user(world.admin))
    assert "an@x.vn" not in {r["user"]["primary_email"] for r in res.json()["members"]}
    assert res.json()["totals"]["critical"] == 0


def test_out_of_range_dates_are_rejected(client: TestClient, world: World, as_user: Any) -> None:
    headers = as_user(world.admin)
    for query in ("to=9999-12-31", "from=0001-01-01&to=0001-12-31"):
        res = client.get(f"/api/v1/members/evaluation?{query}", headers=headers)
        assert res.status_code == 422
