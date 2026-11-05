"""docs/specs/27 §8-9 on the GitLab side: Jira keys of an MR, their links surviving a re-convert,
and no GitLab issues fetched while Jira is the task source."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.extract import extract
from app.collector.sync import sync_project
from app.jira.settings import JIRA_ID_OFFSET, TASK_SOURCE_KEY
from app.models import AppSetting, Connection, Issue, IssueMrLink, JiraProject, MergeRequest
from tests.collector.fake_gitlab import API, mount
from tests.collector.test_collect import make_project
from tests.collector.test_extract_convert import IGNORE
from tests.collector.test_issues_dpi import gl_issue, raw

pytestmark = pytest.mark.integration
P = f"{API}/projects/1001"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def gl_mr(title: str, description: str, branch: str, iid: int = 3) -> dict[str, Any]:
    return {
        "iid": iid,
        "title": title,
        "author": {"id": 77, "username": "an", "name": "An"},
        "state": "merged",
        "source_branch": branch,
        "target_branch": "main",
        "sha": "a" * 40,
        "created_at": "2026-10-10T02:00:00Z",
        "merged_at": "2026-10-12T02:00:00Z",
        "description": description,
        "labels": [],
    }


def jira_issue(session: Session, project: JiraProject, number: int) -> Issue:
    issue = Issue(
        id=JIRA_ID_OFFSET + 10_000 + number,
        source="jira",
        jira_project_id=project.id,
        iid=number,
        key=f"{project.key}-{number}",
        title=f"Jira {number}",
        state="opened",
    )
    session.add(issue)
    session.flush()
    return issue


def convert(session: Session, project: Any) -> None:
    Converter(session, project, IGNORE).run(extract(session.scalars(raw_rows_query(project)).all()))
    session.flush()


def test_keys_of_title_description_branch_are_stored(db_session: Session) -> None:
    project = make_project(db_session)
    mr = gl_mr("ABC-1 đăng nhập", "Liên quan ABC-2, UTF-8 và SHA-256", "feature/ABC-3-login")
    db_session.add(raw(project, ep.EP_MR, mr, iid=3))
    db_session.flush()
    data = extract(db_session.scalars(raw_rows_query(project)).all())
    # Every key-looking token is kept; only registered projects' keys are linked later.
    assert data.merge_requests[3].jira_keys == ["ABC-1", "ABC-2", "UTF-8", "SHA-256", "ABC-3"]
    convert(db_session, project)
    stored = db_session.scalars(select(MergeRequest)).one()
    assert stored.jira_keys == ["ABC-1", "ABC-2", "UTF-8", "SHA-256", "ABC-3"]


def test_jira_links_survive_a_reconvert_and_gitlab_links_are_rederived(
    db_session: Session,
) -> None:
    project = make_project(db_session)
    jp = JiraProject(key="ABC", name="Team ABC")
    db_session.add(jp)
    db_session.flush()
    closes, mentioned = jira_issue(db_session, jp, 1), jira_issue(db_session, jp, 2)
    mr = gl_mr("ABC-1 đăng nhập", "see ABC-2 and #6", "feature/ABC-1-login")
    db_session.add_all(
        [
            raw(project, ep.EP_MR, mr, iid=3),
            raw(project, ep.EP_ISSUE, gl_issue(106, 6, ["type::bug"]), iid=6),
        ]
    )
    db_session.flush()
    for _ in range(2):  # the second run re-derives the links of the same MR
        convert(db_session, project)

    def links() -> set[tuple[int, str]]:
        return {(link.issue_id, link.source) for link in db_session.scalars(select(IssueMrLink))}

    assert links() == {(closes.id, "closes"), (mentioned.id, "mention"), (106, "mention")}
    # The description drops ABC-2 and #6: both mentions go; the Jira closes link stays.
    db_session.add(raw(project, ep.EP_MR, gl_mr("ABC-1 đăng nhập", "", "main"), iid=3))
    db_session.flush()
    convert(db_session, project)
    assert links() == {(closes.id, "closes")}


def test_no_gitlab_issues_are_fetched_while_jira_is_the_task_source(
    db_session: Session,
) -> None:
    project = make_project(db_session)
    db_session.add(AppSetting(key=TASK_SOURCE_KEY, value="jira"))
    db_session.flush()

    def make_client(connection: Connection) -> GitLabClient:
        return GitLabClient(connection.base_url, "t", max_per_second=1000)

    with respx.mock(assert_all_called=False) as router:
        mount(router)
        asked: list[httpx.Request] = []

        def issues(request: httpx.Request) -> httpx.Response:
            asked.append(request)
            return httpx.Response(200, json=[gl_issue(105, 5, ["complexity::S"])])

        router.get(f"{P}/issues").mock(side_effect=issues)
        closers = router.get(f"{P}/issues/5/closed_by").mock(
            return_value=httpx.Response(200, json=[])
        )
        sync_project(db_session, project, make_client, backfill_days=90, ignore_paths=IGNORE)
    # The incidents pass still asks (incident issue type / labels), the DPI pass does not.
    assert asked and all(
        r.url.params.get("issue_type") or r.url.params.get("labels") for r in asked
    )
    assert not closers.called
    stored = {r.endpoint for r in db_session.scalars(raw_rows_query(project))}
    assert ep.EP_ISSUE not in stored and ep.EP_ISSUE_CLOSED_BY not in stored
    assert db_session.scalars(select(Issue)).first() is None
