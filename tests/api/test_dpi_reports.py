"""DPI round 3 (docs/specs/25 §5, §8): item approval, B5 ranking, contribution kinds, R1 by
dự án, R3 / R4, P0 GitLab actions, the AI draft reading the scorecard."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.dpi import service
from app.evidence import draft
from app.models import (
    Account,
    Commit,
    CommitFile,
    EvidenceItem,
    EvidencePack,
    MergeRequest,
    Review,
    ReviewFinding,
)
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import World

pytestmark = pytest.mark.integration
API = "/api/v1/dpi"
MONTH = "2026-10"


@pytest.fixture
def account(world: World, db_session: Session) -> Account:
    a = Account(kind="gitlab_user", external_key="77", display_name="an", user_id=world.an.id)
    db_session.add(a)
    db_session.flush()
    return a


def test_the_lead_approves_items_one_by_one_and_only_approved_ones_count(
    client: TestClient, world: World, as_user: Any, db_session: Session, account: Account
) -> None:
    pack = EvidencePack(
        user_id=world.an.id,
        period_start=date(2026, 10, 1),
        status="submitted",
        snapshot={},
        insights_version="1",
    )
    db_session.add(pack)
    db_session.flush()
    good = EvidenceItem(pack_id=pack.id, kind="mentoring", title="Kèm bạn mới")
    bad = EvidenceItem(pack_id=pack.id, kind="design", title="Thiết kế không ai dùng")
    db_session.add_all([good, bad])
    db_session.flush()
    view = client.get(f"{API}/people/{world.an.id}?month={MONTH}", headers=as_user(world.lead))
    assert {i["title"] for i in view.json()["self_items"]} == {
        "Kèm bạn mới",
        "Thiết kế không ai dùng",
    }
    url = f"{API}/items/{good.id}/approve"
    assert client.post(url, headers=as_user(world.an), json={"approved": True}).status_code == 403
    assert client.post(url, headers=as_user(world.lead), json={"approved": True}).status_code == 200
    client.post(
        f"{API}/items/{bad.id}/approve", headers=as_user(world.lead), json={"approved": False}
    )
    pack.status = "reviewed"  # even then, a rejected item does not count
    db_session.flush()
    d2 = client.get(f"{API}/people/{world.an.id}?month={MONTH}", headers=as_user(world.an)).json()
    assert d2["card"]["metrics"]["D2"]["detail"]["shared_items"] == 1


def test_b5_suggests_the_mrs_with_most_severe_findings_first(
    world: World, db_session: Session, settings: Settings, account: Account
) -> None:
    merged = datetime(2026, 10, 9, tzinfo=UTC)
    small = MergeRequest(
        project_id=world.project.id,
        iid=20,
        title="nhỏ nhưng lỗi",
        author_account_id=account.id,
        state="merged",
        merged_at=merged,
        additions=10,
        deletions=0,
    )
    big = MergeRequest(
        project_id=world.project.id,
        iid=21,
        title="lớn",
        author_account_id=account.id,
        state="merged",
        merged_at=merged,
        additions=900,
        deletions=100,
    )
    db_session.add_all([small, big])
    db_session.flush()
    review = Review(
        target_type="mr",
        mr_id=small.id,
        reviewed_sha="9" * 40,
        status="done",
        author_user_id=world.an.id,
        finished_at=merged,
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(review)
    db_session.flush()
    db_session.add(
        ReviewFinding(
            review_id=review.id,
            severity="critical",
            category="security",
            file_path="a.py",
            line_start=1,
            title="x",
        )
    )
    db_session.flush()
    calc = service.Calculator(db_session, date(2026, 10, 1), settings)
    ranked = service.b5_suggestions(calc, world.an.id)
    assert [r["ref"] for r in ranked] == ["!20", "!21"]
    assert "1 finding critical/high" in ranked[0]["label"]


def test_contribution_kinds_tell_new_work_from_help_and_refactor(
    world: World, db_session: Session, settings: Settings, account: Account
) -> None:
    other = Account(kind="git_email", external_key="b@x.vn", user_id=world.binh.id)
    db_session.add(other)
    db_session.flush()

    def commit(
        sha: str, who: Account, day: int, path: str, adds: int, message: str = "feat"
    ) -> None:
        db_session.add(
            Commit(
                sha=sha,
                project_id=world.project.id,
                author_account_id=who.id,
                authored_at=datetime(2026, 9, day, tzinfo=UTC),
                message=message,
            )
        )
        db_session.flush()
        db_session.add(CommitFile(sha=sha, path=path, additions=adds, deletions=0))

    # Rework is measured on lines added in [Sep 10, Oct 10) for October (§4.2).
    commit("1" * 40, other, 15, "pay.py", 50)  # Binh wrote pay.py ...
    commit("2" * 40, account, 17, "pay.py", 20)  # ... An edits it two days later: help
    commit("3" * 40, account, 18, "new.py", 100)  # new work
    commit("4" * 40, account, 19, "old.py", 30, "refactor: tách hàm")
    db_session.flush()
    card = service.Calculator(db_session, date(2026, 10, 1), settings).card(
        service.draft_review(db_session, world.an.id, date(2026, 10, 1))
    )
    assert card["contribution"] == {"new_work": 100, "refactor": 30, "help_others": 20, "churn": 0}


def test_growth_readiness() -> None:
    trend = [
        {
            "month": f"2026-{m:02d}-01",
            "total": 88.0,
            "kind": "final",
            "groups": {"A": 0.9, "B": 0.85, "C": 0.8, "D": 0.9, "E": 0.75},
        }
        for m in (8, 9, 10)
    ]
    g = service.growth(trend, 88.0)
    assert g["promotion_readiness"] == "ready"
    trend[-1]["groups"]["E"] = 0.3  # one weak axis
    assert service.growth(trend, 88.0)["promotion_readiness"] == "near"
    assert service.growth(trend[:1], 88.0)["promotion_readiness"] == "unknown"


def test_team_reports_and_r1_by_project(
    client: TestClient, world: World, as_user: Any, db_session: Session, account: Account
) -> None:
    q = client.get(f"{API}/quality?month={MONTH}", headers=as_user(world.lead))
    assert q.status_code == 200
    body = q.json()
    assert body["findings_new"]["critical"] == 1  # An's SQL injection, reviewed in October
    assert {"hotspots", "debt", "coverage"} <= set(body)
    d = client.get(f"{API}/delivery", headers=as_user(world.lead))
    assert d.status_code == 200 and "aging" in d.json()
    assert client.get(f"{API}/quality", headers=as_user(world.an)).status_code == 403
    bp = world.project.business_project_id
    r1 = client.get(
        f"{API}/overview?month={MONTH}&business_project_id={bp}", headers=as_user(world.lead)
    )
    assert r1.status_code == 200


def test_gitlab_actions_need_the_projects_right(
    client: TestClient, world: World, as_user: Any
) -> None:
    for action in ("protect", "mr-template", "labels"):
        url = f"{API}/readiness/{world.project.id}/{action}"
        assert client.post(url, headers=as_user(world.lead)).status_code == 403


def test_the_ai_draft_sees_the_scorecard_without_the_total() -> None:
    card = {
        "total": 77.0,
        "rating": "meets",
        "metrics": {
            "B4": {
                "label": "Rework",
                "value": 0.3,
                "source": "auto",
                "detail": {"rework_rate": 31},
                "confidence": "high",
            },
            "A3": {"label": "Impact", "value": 1.0, "source": "manager"},
        },
    }
    prompt = draft.build_prompt({}, [], None, card)
    assert "dpi:B4" in prompt and "77" not in prompt and "meets" not in prompt
    assert "dpi:A3" not in prompt  # the lead's own points are not fed back
    assert draft.references({}, [], card)["dpi:B4"] == "DPI B4 Rework"
