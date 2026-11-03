"""docs/specs/20 §6 (decisions D1, D4): evidence packs through the API, the monthly job and
retention."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.evidence.jobs import create_drafts, previous_month
from app.models import AuditLog, EvidenceItem, EvidencePack
from app.retention import purge
from tests.api.conftest import World, grant
from tests.api.seeding import Seeder

pytestmark = pytest.mark.integration

MONTH = "2026-10"
VN = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seeded month stays in reach whenever the suite runs."""
    monkeypatch.setattr("app.api.routers.evidence.today", lambda settings: date(2026, 11, 3))


@pytest.fixture
def seeded(db_session: Session, world: World) -> None:
    sd = Seeder(db_session, world.project)
    an, binh = sd.account(world.an, "an"), sd.account(world.binh, "binh")
    big = sd.mr(an, opened=0, merged=30, size=1200, title="Big change")
    sd.note(big, binh, 5)  # binh reviewed an's MR
    sd.mr(an, opened=40, merged=42, title="Small fix")
    # The world's critical finding on an's review (October), confirmed by the second check.
    world.an_finding.verified = "confirmed"
    db_session.flush()


def path(world: World, period: str = MONTH, user: Any = None) -> str:
    return f"/api/v1/evidence/{(user or world.an).id}/{period}"


def test_who_sees_a_pack(
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
) -> None:
    for who in (world.an, world.lead, world.admin):
        res = client.get(path(world), headers=as_user(who))
        assert res.status_code == 200, (who.full_name, res.text)
    body = client.get(path(world), headers=as_user(world.an)).json()
    assert body["persisted"] is False and body["status"] == "draft" and body["role"] == "self"
    # Not a teammate, not a team manager, not even a manager of the dự án (D4).
    grant(db_session, world.manager, world.project.business_project_id, "manage")
    for who in (world.binh, world.outsider, world.manager):
        assert client.get(path(world), headers=as_user(who)).status_code == 403
    views = db_session.scalars(select(AuditLog).where(AuditLog.action == "evidence.view")).all()
    assert {v.actor_user_id for v in views} == {world.lead.id, world.admin.id}  # never your own


def test_snapshot_gathers_the_month(
    client: TestClient, world: World, seeded: None, as_user: Any
) -> None:
    snap = client.get(path(world), headers=as_user(world.an)).json()["snapshot"]
    assert snap["period"] == MONTH and snap["merged"]["count"] == 2
    titles = [m["title"] for m in snap["merged"]["items"]]
    assert set(titles) == {"Big change", "Small fix"}
    assert snap["notable"][0]["why"] == "Lớn nhất trong tháng"
    assert snap["quality"]["confirmed_severe"] == 1
    assert snap["quality"]["items"][0]["title"] == "SQL injection"
    binh = client.get(path(world, user=world.binh), headers=as_user(world.binh)).json()["snapshot"]
    assert binh["reviews_given"]["count"] == 1 and binh["reviews_given"]["authors"] == 1
    assert binh["reviews_given"]["items"][0]["title"] == "Big change"
    assert binh["merged"]["count"] == 0
    assert "score" not in str(snap)  # D1: no score anywhere in a pack


def test_the_monthly_workflow(client: TestClient, world: World, seeded: None, as_user: Any) -> None:
    me, lead = as_user(world.an), as_user(world.lead)

    def patch(h: dict[str, str], **body: str) -> int:
        return int(client.patch(path(world), headers=h, json=body).status_code)

    def act(h: dict[str, str], action: str) -> Any:
        return client.post(f"{path(world)}/{action}", headers=h)

    assert patch(lead, lead_notes="too early") == 409  # nothing submitted yet
    assert patch(me, self_notes="Thiết kế lại luồng thanh toán (ngoài Git)") == 200
    assert act(lead, "review").status_code == 409
    submitted = act(me, "submit").json()
    assert submitted["status"] == "submitted" and submitted["submitted_at"]
    assert patch(me, self_notes="late edit") == 409
    assert patch(lead, lead_notes="Tốt, xem MR !101") == 200
    assert act(lead, "return").json()["status"] == "draft"
    assert act(me, "submit").json()["status"] == "submitted"
    reviewed = act(lead, "review").json()
    assert reviewed["status"] == "reviewed" and reviewed["reviewed_by_name"] == "Lead"
    assert patch(me, goals_next="Viết test cho nhánh lỗi") == 200
    assert act(me, "share").status_code == 409  # the lead closes the 1:1
    shared = act(lead, "share").json()
    assert shared["status"] == "shared" and shared["goals_next"] == "Viết test cho nhánh lỗi"
    assert shared["lead_notes"] == "Tốt, xem MR !101"
    # After the 1:1 the person can only add a reply; what was written stays (§6.3.5).
    assert patch(me, self_notes="rewrite history") == 409
    replied = client.patch(path(world), headers=me, json={"reply": "Đồng ý mục tiêu"}).json()
    assert replied["self_notes"].startswith("Thiết kế lại luồng thanh toán (ngoài Git)")
    assert replied["self_notes"].endswith("Phản hồi sau 1:1 (03/11/2026): Đồng ý mục tiêu")
    assert patch(lead, reply="not theirs") == 409
    assert patch(lead, lead_notes="change after") == 409
    assert act(lead, "share").status_code == 409


def test_a_lead_does_not_review_their_own_pack(
    client: TestClient, world: World, seeded: None, as_user: Any
) -> None:
    mine = client.get(path(world, user=world.lead), headers=as_user(world.lead)).json()
    assert mine["role"] == "self" and not mine["can"]["review"]
    assert client.post(
        f"{path(world, user=world.lead)}/submit", headers=as_user(world.lead)
    ).is_success
    assert (
        client.post(
            f"{path(world, user=world.lead)}/review", headers=as_user(world.lead)
        ).status_code
        == 409
    )


def test_evidence_items(
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
) -> None:
    me, lead = as_user(world.an), as_user(world.lead)
    item = {"kind": "mentoring", "title": "Kèm bạn mới onboard", "note": "2 buổi"}
    assert client.post(f"{path(world)}/items", headers=lead, json=item).status_code == 409
    body = client.post(f"{path(world)}/items", headers=me, json=item).json()
    assert body["items"][0]["title"] == "Kèm bạn mới onboard" and body["items"][0]["mine"]
    assert (
        client.post(f"{path(world)}/items", headers=me, json={**item, "kind": "x"}).status_code
        == 422
    )
    client.post(f"{path(world)}/submit", headers=me)
    by_lead = client.post(
        f"{path(world)}/items", headers=lead, json={"kind": "design", "title": "Đề xuất cache"}
    ).json()
    lead_item = by_lead["items"][-1]
    assert lead_item["added_by_name"] == "Lead" and lead_item["mine"]
    assert client.delete(f"{path(world)}/items/{lead_item['id']}", headers=me).status_code == 409
    assert client.delete(f"{path(world)}/items/{lead_item['id']}", headers=lead).status_code == 200
    assert len(db_session.scalars(select(EvidenceItem)).all()) == 1


def test_period_rules(client: TestClient, world: World, as_user: Any) -> None:
    me = as_user(world.an)
    for bad in ("2026-13", "oct", "2099-01", "2020-01"):
        assert client.get(path(world, bad), headers=me).status_code == 422, bad
    listing = client.get(f"/api/v1/evidence/{world.an.id}", headers=me).json()
    assert listing["packs"] == []


def test_monthly_job_creates_drafts_once(db_session: Session, world: World, seeded: None) -> None:
    assert previous_month(date(2026, 11, 1)) == date(2026, 10, 1)
    assert previous_month(date(2026, 1, 15)) == date(2025, 12, 1)
    created = create_drafts(db_session, date(2026, 10, 1), VN, 48)
    assert created == 2  # an merged, binh reviewed; nobody else was active
    people = set(db_session.scalars(select(EvidencePack.user_id)))
    assert people == {world.an.id, world.binh.id}
    assert create_drafts(db_session, date(2026, 10, 1), VN, 48) == 0


def test_retention_clears_old_notes(db_session: Session, world: World) -> None:
    old = EvidencePack(
        user_id=world.an.id,
        period_start=date(2024, 1, 1),
        status="shared",
        snapshot={},
        self_notes="x",
        lead_notes="y",
        insights_version="1",
    )
    recent = EvidencePack(
        user_id=world.an.id,
        period_start=date(2026, 9, 1),
        status="shared",
        snapshot={},
        lead_notes="keep",
        insights_version="1",
    )
    db_session.add_all([old, recent])
    db_session.flush()
    db_session.add(EvidenceItem(pack_id=old.id, kind="other", title="t"))
    db_session.flush()
    result = purge(db_session, 365, now=datetime(2026, 11, 3, tzinfo=UTC), evidence_months=24)
    db_session.refresh(old)
    db_session.refresh(recent)
    assert result.evidence_cleared == 1
    assert (old.self_notes, old.lead_notes, old.status) == (None, None, "shared")
    assert recent.lead_notes == "keep"
    assert db_session.scalars(select(EvidenceItem)).all() == []


def test_only_a_direct_lead_not_a_co_lead_or_a_global_manager(
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
) -> None:
    from app.models import TeamMember

    # A second lead of the same team is a peer: no access to the first lead's pack.
    co = world.binh
    member = db_session.get(TeamMember, (world.backend.id, co.id, date(2025, 1, 1)))
    assert member is not None
    member.role = "lead"
    world.manager.manage_all = True  # "Quản lý tổng" manages every dự án, not people's 1:1s
    db_session.flush()
    assert client.get(path(world, user=world.lead), headers=as_user(co)).status_code == 403
    assert client.get(path(world), headers=as_user(co)).status_code == 200  # An is a member
    assert client.get(path(world), headers=as_user(world.manager)).status_code == 403
    assert (
        client.get(f"/api/v1/evidence/{world.an.id}", headers=as_user(world.manager)).status_code
        == 403
    )


def test_unconfirmed_findings_stay_out(
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
) -> None:
    world.an_finding.verified = "downgraded"
    db_session.flush()
    snap = client.get(path(world), headers=as_user(world.an)).json()["snapshot"]
    assert snap["quality"]["confirmed_severe"] == 0


def test_a_failing_draft_does_not_cost_the_others_theirs(
    db_session: Session, world: World, seeded: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.evidence import snapshot

    real = snapshot.build

    def flaky(session: Session, user_id: int, *args: Any, **kwargs: Any) -> Any:
        if user_id == world.an.id:
            raise RuntimeError("boom")
        return real(session, user_id, *args, **kwargs)

    monkeypatch.setattr(snapshot, "build", flaky)
    assert create_drafts(db_session, date(2026, 10, 1), VN, 48) == 1
    assert set(db_session.scalars(select(EvidencePack.user_id))) == {world.binh.id}


def test_single_owner_module_direct_commits_and_unmapped_accounts(
    client: TestClient, db_session: Session, world: World, settings: Settings, as_user: Any
) -> None:
    """docs/specs/20 §6.2 (9.10b)."""
    from app.models import Account, Commit, CommitFile, MrCommit
    from tests.api.seeding import at

    settings.knowledge_min_commits = 1
    sd = Seeder(db_session, world.project)
    an = sd.account(world.an, "an")
    mail = Account(kind="git_email", external_key="an@x.vn", user_id=world.an.id)
    stranger = Account(kind="git_email", external_key="who@else", display_name="Ai đó")
    db_session.add_all([mail, stranger])
    db_session.flush()
    sd.mr(an, opened=0, merged=20, size=900, title="Big refactor")  # the largest one
    payment = sd.mr(an, opened=0, merged=30, size=10, title="Payment fix")
    commits = [
        Commit(
            sha="1" * 40,
            project_id=world.project.id,
            author_account_id=mail.id,
            authored_at=at(-2),
            is_merge=False,
        ),
        Commit(
            sha="2" * 40,
            project_id=world.project.id,
            author_account_id=mail.id,
            authored_at=at(50),
            is_merge=False,
            message="hotfix: typo\n\nlong body",
        ),
        Commit(
            sha="3" * 40,
            project_id=world.project.id,
            author_account_id=stranger.id,
            authored_at=at(51),
            is_merge=False,
        ),
    ]
    db_session.add_all(commits)
    db_session.flush()
    db_session.add_all(
        [
            MrCommit(mr_id=payment.id, sha="1" * 40),
            CommitFile(sha="1" * 40, path="core/pay/a.py", additions=40, deletions=2),
            CommitFile(sha="2" * 40, path="docs/x.md", additions=1, deletions=0),
        ]
    )
    db_session.flush()
    snap = client.get(path(world), headers=as_user(world.an)).json()["snapshot"]
    reasons = {n["id"]: n["why"] for n in snap["notable"]}
    assert reasons[payment.id] == "Chạm module chỉ một người nắm (core/pay)"
    direct = snap["direct_commits"]
    assert direct["count"] == 1 and direct["items"][0]["title"] == "hotfix: typo"
    assert direct["items"][0]["web_url"].endswith("/-/commit/" + "2" * 40)
    assert snap["unmapped_accounts"] == ["Ai đó"]


def test_ai_draft_for_the_lead_only(
    client: TestClient,
    db_session: Session,
    world: World,
    settings: Settings,
    seeded: None,
    as_user: Any,
) -> None:
    """docs/specs/20 §6.4 (9.11)."""
    from pydantic import SecretStr

    from app.api.main import app
    from app.api.routers.evidence import get_draft_runner
    from app.evidence.draft import DraftOutcome

    settings.llm_provider = "claude_code"
    settings.claude_code_oauth_token = SecretStr("tok")
    seen: dict[str, Any] = {}

    def fake(prompt: str, known: Any, s: Any, token: str) -> DraftOutcome:
        seen.update(prompt=prompt, known=dict(known), token=token)
        return DraftOutcome(ok=True, text="### Đã làm\n- Việc lớn [MR !101]", cost_usd=0.02)

    app.dependency_overrides[get_draft_runner] = lambda: fake
    me, lead = as_user(world.an), as_user(world.lead)
    url = f"{path(world)}/ai-draft"
    assert client.post(url, headers=lead).status_code == 409  # nothing submitted yet
    client.post(f"{path(world)}/submit", headers=me)
    assert client.post(url, headers=me).status_code == 409  # never the person themselves
    res = client.post(url, headers=lead)
    assert res.status_code == 200, res.text
    assert res.json()["ai_draft"].startswith("### Đã làm")
    assert seen["token"] == "tok" and "<du_lieu>" in seen["prompt"]
    assert any(k.startswith("mr:") for k in seen["known"])
    # The person never sees the lead's draft.
    assert client.get(path(world), headers=me).json()["ai_draft"] is None
    # Its cost is in the month's one LLM budget.
    from app.workers.knowledge_jobs import month_spend_breakdown

    assert month_spend_breakdown(db_session)["evidence_drafts_usd"] == 0.02
    # A repo that refuses external LLMs blocks it.
    world.project.allow_external_llm = False
    db_session.flush()
    assert client.post(url, headers=lead).status_code == 409
