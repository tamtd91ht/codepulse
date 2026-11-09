"""Task Jira §20 (docs/specs/27): commits linked by hand, Review lại, moving the issue on Jira."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers import jira_tasks
from app.jira.commit_links import link_commits
from app.models import Account, AuditLog, Commit, IssueCommitLink, ProjectGrant, Review
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import World
from tests.api.test_jira_tasks import _setup

API = "/api/v1/jira/tasks"


def reviewed_commit(
    session: Session, world: World, sha: str, message: str, author_user: int
) -> Commit:
    account = Account(kind="gitlab_user", external_key=f"a-{sha[:6]}", user_id=author_user)
    session.add(account)
    session.flush()
    commit = Commit(
        sha=sha,
        project_id=world.project.id,
        message=message,
        author_account_id=account.id,
        authored_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    session.add(commit)
    session.flush()
    session.add(
        Review(
            target_type="commit",
            commit_sha=sha,
            reviewed_sha=sha,
            status="done",
            author_user_id=author_user,
            score=Decimal(88),
            rubric_version=RUBRIC_VERSION,
        )
    )
    session.flush()
    return commit


def test_a_reviewed_commit_is_linked_by_hand_and_survives_rereading(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _setup(db_session, world)
    mine = reviewed_commit(db_session, world, "1a" * 20, "sửa nhanh", world.an.id)
    theirs = reviewed_commit(db_session, world, "2b" * 20, "chore: dọn", world.binh.id)
    db_session.add(Commit(sha="3c" * 20, project_id=world.project.id, message="chưa review"))
    db_session.flush()
    an = as_user(world.an)

    found = client.get(f"{API}/ABC-2/commit-candidates", params={"q": "1a1a"}, headers=an).json()
    assert [c["sha"] for c in found["items"]] == [mine.sha]
    every = client.get(f"{API}/ABC-2/commit-candidates", headers=an).json()["items"]
    assert {c["sha"] for c in every} == {mine.sha, theirs.sha}  # not the unreviewed one

    # A member links their own commit, not someone else's.
    ok = client.post(f"{API}/ABC-2/commits", json={"sha": mine.sha[:10]}, headers=an)
    assert ok.status_code == 201 and ok.json()["kind"] == "fixes"
    other = client.post(f"{API}/ABC-2/commits", json={"sha": theirs.sha}, headers=an)
    assert other.status_code == 403
    unreviewed = client.post(f"{API}/ABC-2/commits", json={"sha": "3c" * 20}, headers=an)
    assert unreviewed.status_code == 409

    # A link read from a message is not turned into a manual (removable) one.
    auto = reviewed_commit(db_session, world, "4d" * 20, "fix: [ABC-2] sửa", world.an.id)
    link_commits(db_session, [auto.sha])
    assert (
        client.post(f"{API}/ABC-2/commits", json={"sha": auto.sha}, headers=an).status_code == 409
    )
    assert client.delete(f"{API}/ABC-2/commits/{auto.sha}", headers=an).status_code == 409

    detail = client.get(f"{API}/ABC-2", headers=an).json()
    linked = [c for c in detail["commits"] if c["sha"] == mine.sha]
    assert linked and linked[0]["manual"] is True and linked[0]["kind"] == "fixes"
    assert detail["can_move"] is False

    # Reading the message again (it names no key) keeps the link made by hand.
    link_commits(db_session, [mine.sha])
    kept = db_session.scalars(select(IssueCommitLink).where(IssueCommitLink.sha == mine.sha)).one()
    assert kept.manual and kept.kind == "fixes"
    unfixed = client.get(API, params={"linked": "bug_unfixed"}, headers=an).json()["items"]
    assert "ABC-2" not in [i["key"] for i in unfixed]  # the bug has its fix now

    assert client.delete(f"{API}/ABC-2/commits/{mine.sha}", headers=an).status_code == 204
    actions = set(
        db_session.scalars(select(AuditLog.action).where(AuditLog.action.like("jira.commit.%")))
    )
    assert actions == {"jira.commit.link", "jira.commit.unlink"}


class FakeJira:
    def __init__(self) -> None:
        self.moved: list[tuple[str, str, str | None]] = []

    def __enter__(self) -> "FakeJira":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def transitions(self, key: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "31",
                "name": "Resolve",
                "to": {"name": "Resolved", "statusCategory": {"key": "done"}},
            },
            {
                "id": "21",
                "name": "Start",
                "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
            },
        ]

    def transition(self, key: str, transition_id: str, comment: str | None = None) -> None:
        self.moved.append((key, transition_id, comment))

    def issue_status(self, key: str) -> dict[str, Any]:
        return {
            "status": {"name": "Resolved", "statusCategory": {"key": "done"}},
            "resolutiondate": "2026-11-10T08:00:00.000+0700",
        }


def test_managers_and_admins_move_the_issue_on_jira(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issues = _setup(db_session, world)
    fake = FakeJira()
    monkeypatch.setattr(jira_tasks, "_jira", lambda session, settings: fake)
    assert client.get(f"{API}/ABC-1/transitions", headers=as_user(world.an)).status_code == 403
    grant = db_session.scalars(
        select(ProjectGrant).where(
            ProjectGrant.user_id == world.lead.id,
            ProjectGrant.business_project_id == world.project.business_project_id,
        )
    ).one()
    grant.role = "manage"  # the lead manages the dự án now
    db_session.flush()
    lead = as_user(world.lead)
    moves = client.get(f"{API}/ABC-1/transitions", headers=lead).json()["items"]
    assert moves[0] == {"id": "31", "name": "Resolve", "to": "Resolved", "done": True}

    res = client.post(
        f"{API}/ABC-1/transition", json={"transition_id": "31", "comment": "fix ok"}, headers=lead
    )
    assert res.json() == {"key": "ABC-1", "status": "Resolved", "state": "closed"}
    assert fake.moved[0][:2] == ("ABC-1", "31") and "fix ok" in (fake.moved[0][2] or "")
    db_session.refresh(issues["one"])
    assert issues["one"].state == "closed" and issues["one"].closed_at_src is not None
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "jira.issue.transition")
    ).first()
