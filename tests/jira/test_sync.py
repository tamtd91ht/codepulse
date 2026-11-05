"""Jira sync (docs/specs/27 §3, §7, §8, §14)."""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.jira.client import JiraError
from app.jira.settings import JIRA_ID_OFFSET
from app.jira.sync import LOCK_NAMESPACE, jql, sync_all, sync_project, window_minutes
from app.models import (
    Account,
    Issue,
    IssueMrLink,
    JiraProject,
    MergeRequest,
    ProdDefect,
    Project,
    User,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 5, 9, tzinfo=UTC)
FIELDS = [
    {"id": "customfield_10002", "name": "Story Points", "custom": True},
    {"id": "customfield_10004", "name": "Sprint", "custom": True},
    {"id": "customfield_10010", "name": "Flagged", "custom": True},
]


def settings(**extra: Any) -> Settings:
    values: dict[str, Any] = {
        "jira_base_url": "https://jira.example.vn",
        "jira_token": SecretStr("pat-" + "fake"),
        "allowed_email_domains": ["x.vn"],
        **extra,
    }
    return Settings(**values)


def jira_issue(
    jira_id: int,
    key: str,
    *,
    assignee: dict[str, Any] | None = None,
    kind: str = "Story",
    labels: list[str] | None = None,
    points: float | None = 3,
) -> dict[str, Any]:
    return {
        "id": str(jira_id),
        "key": key,
        "fields": {
            "summary": f"Việc {key}",
            "status": {"name": "Done", "statusCategory": {"key": "done"}},
            "issuetype": {"name": kind},
            "labels": labels or [],
            "priority": {"name": "High"},
            "assignee": assignee,
            "resolutiondate": "2026-11-01T10:00:00.000+0700",
            "updated": "2026-11-01T10:00:00.000+0700",
            "customfield_10002": points,
        },
    }


class FakeJira:
    def __init__(self, issues: dict[str, list[dict[str, Any]]]) -> None:
        self.issues = issues  # project key -> its issues; a JiraError value fails the search
        self.queries: list[str] = []
        self.fail: set[str] = set()

    def fields(self) -> list[dict[str, Any]]:
        return FIELDS

    def search_all(self, jql: str, fields: Sequence[str]) -> Iterator[dict[str, Any]]:
        self.queries.append(jql)
        key = jql.split('"')[1]
        items = self.issues.get(key, [])
        for n, item in enumerate(items):
            if key in self.fail and n == 1:
                raise JiraError(503, "POST /search -> HTTP 503")
            yield item


@pytest.fixture
def people(db_session: Session) -> dict[str, User]:
    users = {name: User(full_name=name.title(), primary_email=f"{name}@x.vn") for name in "ab"}
    users["an"] = User(full_name="An", primary_email="an.nguyen@x.vn")
    users["binh"] = User(full_name="Bình", primary_email="binh@x.vn")
    db_session.add_all(users.values())
    db_session.flush()
    return users


def project(session: Session, key: str = "ABC", **kw: Any) -> JiraProject:
    row = JiraProject(key=key, name=key.title(), **{"enabled": True, **kw})
    session.add(row)
    session.flush()
    return row


def test_window_and_jql() -> None:
    p = JiraProject(key="ABC")
    assert window_minutes(p, NOW, 180) == 180 * 1440
    p.synced_until = NOW - timedelta(minutes=30, seconds=10)
    assert window_minutes(p, NOW, 180) == 31 + 5
    assert jql(p, 36) == (
        'project = "ABC" AND issuetype != Epic AND updated >= -36m ORDER BY updated DESC'
    )


def test_upsert_accounts_and_marks(db_session: Session, people: dict[str, User]) -> None:
    p = project(db_session)
    an = {
        "key": "JIRAUSER1",
        "name": "an",
        "emailAddress": "AN.NGUYEN@x.vn",
        "displayName": "Nguyễn An",
    }
    binh = {"key": "JIRAUSER2", "name": "binh", "displayName": "Trần Bình"}
    ghost = {"key": "JIRAUSER3", "name": "nobody", "emailAddress": "nobody@else.vn"}
    bot = {"key": "renovate-bot", "name": "renovate-bot", "displayName": "Renovate"}
    fake = FakeJira(
        {
            "ABC": [
                jira_issue(1, "ABC-1", assignee=an),
                jira_issue(2, "ABC-2", assignee=binh),
                jira_issue(3, "ABC-3", assignee=ghost),
                jira_issue(4, "ABC-4", assignee=bot),
                jira_issue(5, "ABC-5"),
            ]
        }
    )
    assert sync_project(db_session, fake, p, settings(), NOW) == 5
    assert "updated >= -259200m" in fake.queries[0]
    issue = db_session.get(Issue, JIRA_ID_OFFSET + 1)
    assert issue is not None
    assert (issue.source, issue.key, issue.jira_project_id, issue.state) == (
        "jira",
        "ABC-1",
        p.id,
        "closed",
    )
    assert (issue.complexity, issue.complexity_points) == ("M", 3)
    accounts = {
        a.external_key: a
        for a in db_session.scalars(select(Account).where(Account.kind == "jira_user"))
    }
    assert set(accounts) == {"JIRAUSER1", "JIRAUSER2", "JIRAUSER3", "renovate-bot"}
    assert accounts["JIRAUSER1"].user_id == people["an"].id  # by email
    assert accounts["JIRAUSER1"].display_name == "Nguyễn An"
    assert accounts["JIRAUSER2"].user_id == people["binh"].id  # <name>@<allowed domain>
    assert accounts["JIRAUSER3"].user_id is None
    bot_user = db_session.get(User, accounts["renovate-bot"].user_id)
    assert bot_user is not None and bot_user.active is False
    assert issue.assignee_account_ids == [accounts["JIRAUSER1"].id]
    no_one = db_session.get(Issue, JIRA_ID_OFFSET + 5)
    assert no_one is not None and no_one.assignee_account_ids == []
    assert (p.synced_until, p.last_synced_at, p.last_error, p.issues_count) == (NOW, NOW, None, 5)

    # The next run: a short window, the row updated in place, the link never overridden.
    accounts["JIRAUSER1"].user_id = people["a"].id
    db_session.flush()
    fake.issues["ABC"] = [jira_issue(1, "ABC-1", assignee=an, points=8)]
    later = NOW + timedelta(minutes=15)
    assert sync_project(db_session, fake, p, settings(), later) == 1
    assert "updated >= -20m" in fake.queries[1]
    db_session.expire_all()
    issue = db_session.get(Issue, JIRA_ID_OFFSET + 1)
    assert issue is not None and issue.complexity == "XL"
    assert db_session.get(Account, accounts["JIRAUSER1"].id).user_id == people["a"].id  # type: ignore[union-attr]
    assert p.issues_count == 5


def test_one_projects_failure_keeps_its_mark_and_others_go_on(db_session: Session) -> None:
    before = NOW - timedelta(days=1)
    a = project(db_session, "AAA", synced_until=before)
    b = project(db_session, "BBB", synced_until=before)
    project(db_session, "OFF", enabled=False)
    db_session.commit()
    fake = FakeJira(
        {
            "AAA": [jira_issue(11, "AAA-1"), jira_issue(12, "AAA-2")],
            "BBB": [jira_issue(21, "BBB-1"), jira_issue(22, "BBB-2")],
        }
    )
    fake.fail.add("AAA")
    out = sync_all(db_session, settings(), fake)
    assert set(out["projects"]) == {"AAA", "BBB"}  # the disabled one is not fetched
    assert "503" in out["projects"]["AAA"]["error"]
    assert out["projects"]["BBB"] == {"issues": 2}
    db_session.expire_all()
    a, b = db_session.get(JiraProject, a.id), db_session.get(JiraProject, b.id)  # type: ignore[assignment]
    assert a is not None and b is not None
    assert a.synced_until == before and a.last_error and "503" in a.last_error
    assert db_session.get(Issue, JIRA_ID_OFFSET + 11) is None  # rolled back with its project
    assert b.synced_until is not None and b.synced_until > before and b.last_error is None
    assert b.issues_count == 2
    # The next run starts again from the old mark and clears the error.
    fake.fail.clear()
    sync_all(db_session, settings(), fake)
    db_session.expire_all()
    a = db_session.get(JiraProject, a.id)  # type: ignore[assignment]
    assert a is not None and a.last_error is None and a.issues_count == 2


def test_no_op_without_configuration_or_projects(db_session: Session) -> None:
    fake = FakeJira({})
    assert sync_all(db_session, settings(jira_base_url=""), fake) == {"skipped": "not_configured"}
    assert sync_all(db_session, settings(), fake) == {"skipped": "no_project"}
    assert fake.queries == []


def test_production_defect_created_then_withdrawn(db_session: Session) -> None:
    p = project(db_session)
    fake = FakeJira({"ABC": [jira_issue(7, "ABC-7", kind="Bug", labels=["production"])]})
    sync_project(db_session, fake, p, settings(), NOW)
    issue_id = JIRA_ID_OFFSET + 7
    defect = db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == issue_id)).one()
    assert (defect.severity, defect.rca_status) == ("P2", "pending")
    # The label goes while pending: the defect goes.
    fake.issues["ABC"] = [jira_issue(7, "ABC-7", kind="Bug")]
    sync_project(db_session, fake, p, settings(), NOW)
    assert db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == issue_id)).all() == []
    # Back, confirmed, then gone: withdrawn, kept.
    fake.issues["ABC"] = [jira_issue(7, "ABC-7", kind="Bug", labels=["prod-bug"])]
    sync_project(db_session, fake, p, settings(), NOW)
    defect = db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == issue_id)).one()
    defect.rca_status = "confirmed"
    db_session.flush()
    fake.issues["ABC"] = [jira_issue(7, "ABC-7", kind="Bug")]
    sync_project(db_session, fake, p, settings(), NOW)
    db_session.expire_all()
    defect = db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == issue_id)).one()
    assert defect.rca_status == "withdrawn"


def test_issues_link_to_merge_requests_naming_their_key(db_session: Session) -> None:
    p = project(db_session)
    repo = Project(external_id=99, path_with_namespace="demo/repo")
    db_session.add(repo)
    db_session.flush()
    closing = MergeRequest(
        project_id=repo.id, iid=1, title="ABC-9 OTP", source_branch="x", jira_keys=["ABC-9"]
    )
    mention = MergeRequest(
        project_id=repo.id, iid=2, title="Refactor", source_branch="y", jira_keys=["ABC-9"]
    )
    other = MergeRequest(project_id=repo.id, iid=3, title="ABC-1", jira_keys=["ABC-1"])
    db_session.add_all([closing, mention, other])
    db_session.flush()
    sync_project(db_session, FakeJira({"ABC": [jira_issue(9, "ABC-9")]}), p, settings(), NOW)
    links = {
        (link.mr_id, link.source)
        for link in db_session.scalars(
            select(IssueMrLink).where(IssueMrLink.issue_id == JIRA_ID_OFFSET + 9)
        )
    }
    assert links == {(closing.id, "closes"), (mention.id, "mention")}


def test_a_project_being_synced_is_skipped_not_failed(
    db_session: Session, migrated_engine: Engine
) -> None:
    p = project(db_session, synced_until=NOW - timedelta(days=1))
    db_session.commit()
    fake = FakeJira({"ABC": [jira_issue(31, "ABC-31")]})
    with migrated_engine.connect() as other, other.begin():
        # Another worker's sync of the same project holds its lock.
        other.execute(select(func.pg_advisory_xact_lock(LOCK_NAMESPACE, p.id)))
        assert sync_project(db_session, fake, p, settings(), NOW) is None
        db_session.rollback()
        out = sync_all(db_session, settings(), fake)
    assert out["projects"] == {"ABC": {"skipped": "busy"}}
    assert fake.queries == []
    db_session.expire_all()
    again = db_session.get(JiraProject, p.id)
    assert again is not None and again.last_error is None
    assert again.synced_until == NOW - timedelta(days=1)
    # The lock released: the next run syncs it.
    assert sync_all(db_session, settings(), fake)["projects"] == {"ABC": {"issues": 1}}


def test_unlinked_accounts_are_retried_on_every_run(db_session: Session) -> None:
    p = project(db_session)
    late = {"key": "JIRAUSER7", "name": "chi", "emailAddress": "Chi.Le@x.vn"}
    by_name = {"key": "JIRAUSER8", "name": "dung", "displayName": "Dũng"}
    fake = FakeJira(
        {
            "ABC": [
                jira_issue(41, "ABC-41", assignee=late),
                jira_issue(42, "ABC-42", assignee=by_name),
            ]
        }
    )
    sync_project(db_session, fake, p, settings(), NOW)
    accounts = {
        a.external_key: a
        for a in db_session.scalars(select(Account).where(Account.kind == "jira_user"))
    }
    assert accounts["JIRAUSER7"].user_id is None and accounts["JIRAUSER8"].user_id is None
    assert (accounts["JIRAUSER7"].email, accounts["JIRAUSER7"].login) == ("Chi.Le@x.vn", "chi")
    db_session.commit()
    # The people are added later; their issues are not updated again in Jira.
    chi = User(full_name="Chi", primary_email="chi.le@x.vn")
    dung = User(full_name="Dũng", primary_email="dung@x.vn")
    db_session.add_all([chi, dung])
    db_session.commit()
    fake.issues["ABC"] = []
    out = sync_all(db_session, settings(), fake)
    assert out["relinked"] == 2
    db_session.expire_all()
    assert db_session.get(Account, accounts["JIRAUSER7"].id).user_id == chi.id  # type: ignore[union-attr]
    assert db_session.get(Account, accounts["JIRAUSER8"].id).user_id == dung.id  # type: ignore[union-attr]
