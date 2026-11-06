"""Jira issue <-> MR links by key (docs/specs/27 §8)."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.jira.links import MAX_KEYS, extract_keys, link_issues, link_merge_requests, mr_issues
from app.jira.scopes import Rule, replace_scopes
from app.jira.settings import JIRA_ID_OFFSET, set_task_source
from app.models import BusinessProject, Issue, IssueMrLink, JiraProject, MergeRequest, Project


def test_extract_keys_in_order_without_duplicates() -> None:
    assert extract_keys("ABC-12 fix", "feature/ABC-12-otp", "see XY_Z-3, ABC-7") == [
        "ABC-12",
        "XY_Z-3",
        "ABC-7",
    ]
    # Not keys: lower case, a zero number, glued to a word, a single letter project.
    assert extract_keys("abc-1 ABC-0 xABC-2 A-5 ABC-3x", None) == []
    assert extract_keys("UTF-8") == ["UTF-8"]  # filtered later: UTF is no registered project
    many = " ".join(f"ABC-{n}" for n in range(1, 80))
    assert len(extract_keys(many)) == MAX_KEYS


def test_branch_keys_in_any_case_and_followed_by_an_underscore() -> None:
    assert extract_keys(branch="feature/ABC-123_login") == ["ABC-123"]
    assert extract_keys(branch="feature/abc-123-login") == ["ABC-123"]
    assert extract_keys("Fix", "abc-9 in text stays out", branch="bugfix/Abc-7_otp") == ["ABC-7"]
    assert extract_keys("ABC-5_x ABC-6x") == ["ABC-5"]  # `_` may follow, a letter may not


@pytest.fixture
def setup(db_session: Session) -> tuple[Project, Issue]:
    jira = JiraProject(key="ABC", name="Abc")
    repo = Project(external_id=77, path_with_namespace="demo/links")
    db_session.add_all([jira, repo])
    db_session.flush()
    issue = Issue(
        id=JIRA_ID_OFFSET + 5,
        source="jira",
        jira_project_id=jira.id,
        iid=5,
        key="ABC-5",
        title="OTP",
    )
    db_session.add(issue)
    db_session.flush()
    return repo, issue


def links(session: Session, issue: Issue) -> dict[int, str]:
    rows = session.scalars(select(IssueMrLink).where(IssueMrLink.issue_id == issue.id))
    return {link.mr_id: link.source for link in rows}


@pytest.mark.integration
def test_merge_requests_link_by_title_branch_or_description(
    db_session: Session, setup: tuple[Project, Issue]
) -> None:
    repo, issue = setup
    by_title = MergeRequest(project_id=repo.id, iid=1, title="ABC-5: OTP", jira_keys=["ABC-5"])
    by_branch = MergeRequest(
        project_id=repo.id,
        iid=2,
        title="OTP",
        source_branch="feature/ABC-5-otp",
        jira_keys=["ABC-5"],
    )
    by_text = MergeRequest(
        project_id=repo.id, iid=3, title="OTP", jira_keys=["ABC-5", "UTF-8", "ZZZ-1"]
    )
    db_session.add_all([by_title, by_branch, by_text])
    db_session.flush()
    assert link_merge_requests(db_session, [by_title.id, by_branch.id, by_text.id]) == 3
    assert links(db_session, issue) == {
        by_title.id: "closes",
        by_branch.id: "closes",
        by_text.id: "mention",
    }
    # The key left the description: the mention goes; a closes link stays.
    by_text.jira_keys = []
    by_title.jira_keys = []
    db_session.flush()
    link_merge_requests(db_session, [by_text.id, by_title.id])
    assert links(db_session, issue) == {by_title.id: "closes", by_branch.id: "closes"}
    assert link_merge_requests(db_session, []) == 0


@pytest.mark.integration
def test_issues_link_to_stored_merge_requests(
    db_session: Session, setup: tuple[Project, Issue]
) -> None:
    repo, issue = setup
    mr = MergeRequest(project_id=repo.id, iid=9, title="ABC-5", jira_keys=["ABC-5"])
    db_session.add(mr)
    db_session.flush()
    assert link_issues(db_session, [issue.id]) == 1
    assert links(db_session, issue) == {mr.id: "closes"}
    assert link_issues(db_session, []) == 0


@pytest.mark.integration
def test_issues_link_in_chunks_of_keys(
    db_session: Session, setup: tuple[Project, Issue], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, first = setup
    jira = db_session.get(JiraProject, first.jira_project_id)
    assert jira is not None
    issues = [first] + [
        Issue(
            id=JIRA_ID_OFFSET + n,
            source="jira",
            jira_project_id=jira.id,
            iid=n,
            key=f"ABC-{n}",
            title=str(n),
        )
        for n in (6, 7)
    ]
    db_session.add_all(issues[1:])
    by_branch = MergeRequest(
        project_id=repo.id,
        iid=1,
        title="OTP",
        source_branch="feature/abc-6_otp",
        jira_keys=["ABC-6", "ABC-7"],
    )
    by_title = MergeRequest(project_id=repo.id, iid=2, title="ABC-5", jira_keys=["ABC-5"])
    db_session.add_all([by_branch, by_title])
    db_session.flush()
    monkeypatch.setattr("app.jira.links.CHUNK", 2)  # ABC-5, ABC-6 | ABC-7
    assert link_issues(db_session, [i.id for i in issues]) == 3
    assert links(db_session, issues[0]) == {by_title.id: "closes"}
    assert links(db_session, issues[1]) == {by_branch.id: "closes"}  # lower-case branch
    assert links(db_session, issues[2]) == {by_branch.id: "mention"}


@pytest.mark.integration
def test_mr_issues_lists_linked_jira_issues_and_unsynced_keys(
    db_session: Session, setup: tuple[Project, Issue]
) -> None:
    repo, issue = setup
    shop, sec = BusinessProject(name="Shop"), BusinessProject(name="Sec")
    other_jira = JiraProject(key="SEC", name="Bảo mật")
    db_session.add_all([shop, sec, other_jira])
    db_session.flush()
    repo.business_project_id = shop.id
    abc = db_session.get(JiraProject, issue.jira_project_id)
    assert abc is not None
    replace_scopes(db_session, abc, [Rule(shop.id)])
    replace_scopes(db_session, other_jira, [Rule(sec.id)])
    issue.state, issue.type, issue.story_points = "opened", "feature", Decimal("3")
    issue.web_url = "https://jira.x.vn/browse/ABC-5"
    db_session.add_all([
        Issue(id=JIRA_ID_OFFSET + 2, source="jira", jira_project_id=abc.id, iid=2,
              key="ABC-2", title="Ghi chú", state="closed"),
        Issue(id=JIRA_ID_OFFSET + 10, source="jira", jira_project_id=abc.id, iid=10,
              key="ABC-10", title="Mười", state="opened"),
        Issue(id=JIRA_ID_OFFSET + 7, source="jira", jira_project_id=other_jira.id, iid=7,
              key="SEC-7", title="Lỗ hổng chưa công bố", state="opened"),
    ])  # fmt: skip
    mr = MergeRequest(
        project_id=repo.id,
        iid=9,
        title="OTP",
        source_branch="feature/ABC-5-otp",
        jira_keys=["ABC-10", "ABC-2", "ABC-5", "ABC-99", "SEC-7", "UTF-8"],
    )
    db_session.add(mr)
    db_session.flush()
    link_merge_requests(db_session, [mr.id])
    set_task_source(db_session, "jira", None)

    out = mr_issues(db_session, get_settings(), mr, shop.id)

    # The branch link first, then description-only ones by number (2 before 10).
    assert [(i["key"], i["link"]) for i in out["issues"]] == [
        ("ABC-5", "closes"),
        ("ABC-2", "mention"),
        ("ABC-10", "mention"),
    ]
    assert out["issues"][0] == {
        "key": "ABC-5",
        "title": "OTP",
        "state": "opened",
        "type": "feature",
        "story_points": 3.0,
        "url": "https://jira.x.vn/browse/ABC-5",
        "link": "closes",
    }
    # Another dự án's issue: key only, never its title (anyone can type a key in an MR).
    assert out["other_keys"] == ["SEC-7"]
    assert "Lỗ hổng" not in str(out)
    assert out["missing_keys"] == ["ABC-99"]  # UTF is no Jira project
    assert out["expected"] is True
    # A repo without a dự án: nothing in detail.
    bare_scope = mr_issues(db_session, get_settings(), mr, None)
    assert bare_scope["issues"] == [] and len(bare_scope["other_keys"]) == 4

    set_task_source(db_session, "gitlab", None)
    bare = MergeRequest(project_id=repo.id, iid=10, title="x")
    db_session.add(bare)
    db_session.flush()
    assert mr_issues(db_session, get_settings(), bare, shop.id) == {
        "issues": [],
        "other_keys": [],
        "missing_keys": [],
        "expected": False,
    }
