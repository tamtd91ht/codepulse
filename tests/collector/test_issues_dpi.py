"""docs/specs/25 §4: issues, issue <-> MR links, MR checklist, pipeline coverage and tests."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.collect import GitLabCollector
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.extract import branch_issue, extract, issue_refs, tasks
from app.models import Issue, IssueMrLink, MergeRequest, Pipeline, ProdDefect, Project, RawGitlab
from tests.collector.fake_gitlab import API, BASE, mount
from tests.collector.test_collect import make_project
from tests.collector.test_extract_convert import IGNORE

pytestmark = pytest.mark.integration
P = f"{API}/projects/1001"
NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_issue_refs_branch_and_tasks() -> None:
    assert issue_refs("Closes #5 and #7, see #6 and other/repo#9") == ([5, 7], [6])
    assert issue_refs("fixes: #12\nRelated to #3") == ([12], [3])
    assert issue_refs(None) == ([], [])
    assert branch_issue("feature/123-login") == 123
    assert branch_issue("42_fix") == 42 and branch_issue("main") is None
    assert tasks("- [x] link\n- [ ] test\n* [X] rollback\ntext") == (3, 2)


def raw(project: Project, endpoint: str, payload: Any, **params: Any) -> RawGitlab:
    return RawGitlab(
        project_id=project.id,
        endpoint=endpoint,
        params=params or None,
        payload=payload,
        fetched_at=NOW,
    )


def gl_issue(issue_id: int, iid: int, labels: list[str], state: str = "closed") -> dict[str, Any]:
    return {
        "id": issue_id,
        "iid": iid,
        "title": f"Task {iid}",
        "state": state,
        "labels": labels,
        "assignees": [{"id": 77, "username": "an", "name": "An"}],
        "milestone": {"title": "Sprint 9", "due_date": "2026-10-31"},
        "due_date": None,
        "created_at": "2026-10-01T02:00:00Z",
        "closed_at": "2026-10-20T02:00:00Z" if state == "closed" else None,
        "time_stats": {"time_estimate": 3600, "total_time_spent": 7200},
    }


def test_issues_links_checklist_and_pipeline_tests_are_converted(db_session: Session) -> None:
    project = make_project(db_session)
    mr = {
        "iid": 3,
        "title": "Login",
        "author": {"id": 77, "username": "an", "name": "An"},
        "state": "merged",
        "source_branch": "feature/7-login",
        "target_branch": "main",
        "sha": "a" * 40,
        "created_at": "2026-10-10T02:00:00Z",
        "merged_at": "2026-10-12T02:00:00Z",
        "description": "Closes #5\nsee #6\n\n- [x] Đã liên kết issue\n- [ ] Có test",
        "labels": ["type::feature"],
    }
    rows = [
        raw(project, ep.EP_MR, mr, iid=3),
        raw(project, ep.EP_ISSUE, gl_issue(105, 5, ["complexity::M", "type::feature"]), iid=5),
        raw(project, ep.EP_ISSUE, gl_issue(106, 6, ["prod-bug::P2", "type::bug"]), iid=6),
        raw(project, ep.EP_ISSUE, gl_issue(107, 7, ["complexity::XL"], "opened"), iid=7),
        raw(project, ep.EP_ISSUE_CLOSED_BY, [3], iid=6),
        raw(
            project,
            ep.EP_PIPELINE,
            {
                "id": 900,
                "sha": "a" * 40,
                "ref": "main",
                "status": "success",
                "coverage": "81.5",
                "source": "push",
            },
            id=900,
        ),
        raw(
            project,
            ep.EP_PIPELINE_TESTS,
            {"count": 40, "failed": 1, "error": 1, "skipped": 2},
            id=900,
        ),
    ]
    db_session.add_all(rows)
    db_session.flush()
    for _ in range(2):  # idempotent
        Converter(db_session, project, IGNORE).run(
            extract(db_session.scalars(raw_rows_query(project)).all())
        )
    db_session.flush()

    issues = {i.iid: i for i in db_session.scalars(select(Issue))}
    assert (issues[5].type, issues[5].complexity, issues[5].complexity_points) == (
        "feature",
        "M",
        3,
    )
    assert issues[5].due_date is not None and str(issues[5].due_date) == "2026-10-31"
    assert issues[6].prod_severity == "P2" and issues[7].complexity_points == 8
    assert len(issues[5].assignee_account_ids or []) == 1
    merge_request = db_session.scalars(select(MergeRequest)).one()
    assert (merge_request.tasks_total, merge_request.tasks_done) == (2, 1)
    assert merge_request.labels == ["type::feature"]
    links = {
        (link.issue_id, link.source)
        for link in db_session.scalars(
            select(IssueMrLink).where(IssueMrLink.mr_id == merge_request.id)
        )
    }
    # #6 is mentioned but GitLab says the MR closes it: the strongest source wins.
    assert links == {(105, "closes"), (106, "closes"), (107, "branch")}
    defect = db_session.scalars(select(ProdDefect)).one()
    assert (defect.issue_id, defect.severity, defect.rca_status) == (106, "P2", "pending")
    pipeline = db_session.get(Pipeline, 900)
    assert pipeline is not None and float(pipeline.coverage or 0) == 81.5
    assert (pipeline.tests_total, pipeline.tests_failed, pipeline.tests_skipped) == (40, 2, 2)


def test_collector_asks_for_issues_their_closers_and_test_reports(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)

        def issues(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("issue_type") or request.url.params.get("labels"):
                return httpx.Response(200, json=[])  # the incidents pass
            return httpx.Response(200, json=[gl_issue(105, 5, ["complexity::S"])])

        router.get(f"{P}/issues").mock(side_effect=issues)
        closers = router.get(f"{P}/issues/5/closed_by").mock(
            return_value=httpx.Response(200, json=[{"iid": 1}])
        )
        with GitLabClient(BASE, "t", max_per_second=1000) as client:
            GitLabCollector(client).collect(db_session, project, None)
    assert closers.called
    stored = {r.endpoint for r in db_session.scalars(raw_rows_query(project))}
    assert {ep.EP_ISSUE, ep.EP_ISSUE_CLOSED_BY} <= stored
