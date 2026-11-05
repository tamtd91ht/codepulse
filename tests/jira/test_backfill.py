"""The one-off `merge_requests.jira_keys` backfill (docs/specs/27 §8)."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.jira.backfill import DONE_KEY, backfill_keys
from app.models import AppSetting, IssueMrLink, JiraProject, MergeRequest
from tests.collector.test_collect import make_project
from tests.collector.test_issues_dpi import raw
from tests.collector.test_jira_keys import gl_mr, jira_issue

pytestmark = pytest.mark.integration


def test_keys_come_from_the_latest_raw_payload_then_links(db_session: Session) -> None:
    project = make_project(db_session)
    jp = JiraProject(key="ABC", name="Abc")
    db_session.add(jp)
    db_session.flush()
    closes, mentioned = jira_issue(db_session, jp, 1), jira_issue(db_session, jp, 2)
    db_session.add_all(
        [
            # An older payload of MR 3, then the latest: the latest wins.
            raw(project, ep.EP_MR, gl_mr("Old", "", "main", iid=3), iid=3),
            raw(project, ep.EP_MR, gl_mr("Login", "see ABC-2", "feature/abc-1_login"), iid=3),
        ]
    )
    with_raw = MergeRequest(
        project_id=project.id, iid=3, title="Login", source_branch="feature/abc-1_login"
    )
    # No raw rows left (retention): the stored title and branch.
    no_raw = MergeRequest(project_id=project.id, iid=4, title="ABC-2 fix", source_branch="y")
    done = MergeRequest(project_id=project.id, iid=5, title="ABC-1", jira_keys=[])
    db_session.add_all([with_raw, no_raw, done])
    db_session.flush()

    out = backfill_keys(db_session, batch=1)  # one MR per batch: the paging is exercised
    assert out == {"merge_requests": 2, "links": 3}
    db_session.expire_all()
    assert with_raw.jira_keys == ["ABC-2", "ABC-1"]
    assert no_raw.jira_keys == ["ABC-2"]
    assert done.jira_keys == []  # converted after the upgrade: left as it is
    links = {
        (link.issue_id, link.mr_id, link.source) for link in db_session.scalars(select(IssueMrLink))
    }
    assert links == {
        (closes.id, with_raw.id, "closes"),
        (mentioned.id, with_raw.id, "mention"),
        (mentioned.id, no_raw.id, "closes"),
    }
    assert backfill_keys(db_session) == {"merge_requests": 0, "links": 0}
    assert db_session.get(AppSetting, DONE_KEY) is None  # only the job records it
