"""Jira issue <-> commit links (docs/specs/27 §19, user 2026-11-09)."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.jira import commit_links
from app.jira.commit_links import commit_issues, link_commits, link_issue_commits, link_kinds
from app.jira.settings import JIRA_ID_OFFSET
from app.models import AppSetting, Commit, Issue, IssueCommitLink, JiraProject, Project


@pytest.mark.parametrize(
    ("message", "kinds"),
    [
        # The standard: key at the end of the subject, the fix said in a footer line.
        ("fix(export): căn đúng cột [CRM-415]\n\nFixes: CRM-415", {"CRM-415": "fixes"}),
        # A fix subject naming the key is a fix even without the footer.
        ("fix(export): căn đúng cột [CRM-415]", {"CRM-415": "fixes"}),
        ("hotfix: timeout Zalo CRM-9", {"CRM-9": "fixes"}),
        # Work on an issue, not its fix.
        ("feat(crm): đồng bộ danh bạ [CRM-412]", {"CRM-412": "refs"}),
        (
            "refactor: tách service\n\nRefs: CRM-412, CRM-415",
            {"CRM-412": "refs", "CRM-415": "refs"},
        ),
        # Footer lines in any case, several keys, words in a sentence.
        ("chore: dọn\n\ncloses: CRM-1, CRM-2", {"CRM-1": "fixes", "CRM-2": "fixes"}),
        ("feat: OTP. Fixes CRM-7, refs CRM-8", {"CRM-7": "fixes", "CRM-8": "refs"}),
        (
            "chore: x\n\nFixes CRM-8, CRM-9 and CRM-10",
            {"CRM-8": "fixes", "CRM-9": "fixes", "CRM-10": "fixes"},
        ),
        ("fix: login\n\nfixes CRM-4_x", {"CRM-4": "fixes"}),
        # Prose that names a fix it does not make is not one.
        ("chore: x\n\nThis does not fix CRM-2 yet", {"CRM-2": "refs"}),
        ("feat: partial fix CRM-1, will fix CRM-3 later", {"CRM-1": "refs", "CRM-3": "refs"}),
        ("feat: x\n\nResolves: CRM-3\nRefs: CRM-4", {"CRM-3": "fixes", "CRM-4": "refs"}),
        # A revert never fixes; lower-case keys and look-alikes are not keys.
        ('Revert "fix: lệch cột [CRM-415]"', {"CRM-415": "refs"}),
        ("fix: utf-8 crm-415", {}),
        ("docs: SHA-256 vs UTF-8", {"SHA-256": "refs", "UTF-8": "refs"}),  # filtered later
        ("prefix CRM-5 done", {"CRM-5": "refs"}),
        ("", {}),
    ],
)
def test_link_kinds(message: str, kinds: dict[str, str]) -> None:
    assert link_kinds(message) == kinds


@pytest.fixture
def setup(db_session: Session) -> tuple[Project, Issue]:
    jira = JiraProject(key="CRM", name="Crm")
    repo = Project(external_id=78, path_with_namespace="demo/commits")
    db_session.add_all([jira, repo])
    db_session.flush()
    issue = Issue(
        id=JIRA_ID_OFFSET + 415,
        source="jira",
        jira_project_id=jira.id,
        iid=415,
        key="CRM-415",
        title="Lệch cột khi export",
        type="bug",
    )
    db_session.add(issue)
    db_session.flush()
    return repo, issue


def links(session: Session, issue: Issue) -> dict[str, str]:
    rows = session.scalars(select(IssueCommitLink).where(IssueCommitLink.issue_id == issue.id))
    return {link.sha: link.kind for link in rows}


@pytest.mark.integration
def test_commits_link_both_ways_and_merges_never(
    db_session: Session, setup: tuple[Project, Issue]
) -> None:
    repo, issue = setup
    fix = Commit(sha="f" * 40, project_id=repo.id, message="fix: lệch [CRM-415]\n\nFixes: CRM-415")
    ref = Commit(sha="e" * 40, project_id=repo.id, message="test: thêm ca CRM-415")
    merge = Commit(
        sha="d" * 40,
        project_id=repo.id,
        message="Merge branch 'bugfix/CRM-415-lech' into 'main'",
        is_merge=True,
    )
    other = Commit(sha="c" * 40, project_id=repo.id, message="docs: UTF-8 SHA-256 ZZZ-1")
    db_session.add_all([fix, ref, merge, other])
    db_session.flush()

    link_commits(db_session, [fix.sha, ref.sha, merge.sha, other.sha])
    assert links(db_session, issue) == {fix.sha: "fixes", ref.sha: "refs"}
    db_session.refresh(other)
    assert other.jira_keys == ["UTF-8", "SHA-256", "ZZZ-1"]  # stored, linked only if registered

    # From the other side: an issue synced after its commits finds them by their stored keys.
    db_session.execute(IssueCommitLink.__table__.delete())
    assert link_issue_commits(db_session, [issue.id]) == 2
    assert links(db_session, issue) == {fix.sha: "fixes", ref.sha: "refs"}

    # A reworded message (amended, re-pushed) is linked again from scratch.
    ref.message = "test: thêm ca"
    db_session.flush()
    link_commits(db_session, [ref.sha])
    assert links(db_session, issue) == {fix.sha: "fixes"}


@pytest.mark.integration
def test_the_backfill_reads_stored_commits_once(
    db_session: Session, setup: tuple[Project, Issue]
) -> None:
    repo, issue = setup
    old = Commit(sha="a" * 40, project_id=repo.id, message="fix(x): y\n\nFixes: CRM-415")
    db_session.add(old)
    db_session.flush()
    assert old.jira_keys is None
    after, seen, written = "", 0, 0
    while True:  # keyset-paged, as the job does
        after, n, w = commit_links.backfill_batch(db_session, after, batch=2)
        if not n:
            break
        seen, written = seen + n, written + w
    assert seen >= 1 and written >= 1
    assert links(db_session, issue) == {old.sha: "fixes"}
    assert commit_links.backfill_batch(db_session)[1] == 0  # nothing left with NULL keys
    assert db_session.get(AppSetting, commit_links.DONE_KEY) is None  # the job writes it


@pytest.mark.integration
def test_a_commit_review_shows_its_issues(
    db_session: Session, setup: tuple[Project, Issue]
) -> None:
    repo, _issue = setup
    sha = "b" * 40
    db_session.add(
        Commit(sha=sha, project_id=repo.id, message="fix: lệch [CRM-415]\n\nRefs: CRM-999")
    )
    db_session.flush()
    link_commits(db_session, [sha])
    # The repo is in no dự án: the issue shows as a key only, as for MRs.
    out = commit_issues(db_session, get_settings(), sha, None)
    assert out["issues"] == [] and out["other_keys"] == ["CRM-415"]
    assert out["missing_keys"] == ["CRM-999"] and out["target"] == "commit"
