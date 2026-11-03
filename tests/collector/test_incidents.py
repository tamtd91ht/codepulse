"""docs/specs/20 §5.3 (D3): incidents from GitLab issues."""

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.collect import GitLabCollector
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.extract import extract
from app.models import Incident, Project
from tests.collector.fake_gitlab import API, BASE, mount
from tests.collector.test_collect import make_project
from tests.collector.test_extract_convert import IGNORE

pytestmark = pytest.mark.integration
P = f"{API}/projects/1001"


def issue(
    issue_id: int, iid: int, state: str = "opened", labels: list[str] | None = None
) -> dict[str, object]:
    return {
        "id": issue_id,
        "iid": iid,
        "title": f"Sự cố {iid}",
        "state": state,
        "labels": labels or [],
        "web_url": f"https://gitlab.example.com/demo/p/-/issues/{iid}",
        "created_at": "2026-10-05T02:00:00Z",
        "closed_at": "2026-10-05T06:00:00Z" if state == "closed" else None,
    }


def run(session: Session, project: Project, labels: tuple[str, ...] = ("incident",)) -> None:
    with GitLabClient(BASE, "t") as client:
        GitLabCollector(client).collect(session, project, None, incident_labels=labels)
    Converter(session, project, IGNORE).run(extract(session.scalars(raw_rows_query(project)).all()))
    session.flush()


def test_incidents_by_type_or_label_with_their_fixes(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)

        def issues(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            if params.get("issue_type") == "incident":
                return httpx.Response(200, json=[issue(1, 10, "closed"), issue(2, 11)])
            if params.get("labels") == "sev1":
                # Labelled, and one of them also typed incident: listed once.
                return httpx.Response(200, json=[issue(2, 11), issue(3, 12, labels=["sev1"])])
            return httpx.Response(200, json=[])

        router.get(f"{P}/issues").mock(side_effect=issues)
        fixes = router.get(f"{P}/issues/10/closed_by").mock(
            return_value=httpx.Response(200, json=[{"iid": 1}])
        )
        run(db_session, project, ("sev1",))
    assert fixes.called
    found = {i.iid: i for i in db_session.scalars(select(Incident))}
    assert set(found) == {10, 11, 12}
    assert found[10].fix_mr_iids == [1] and found[10].closed_at_src is not None
    assert found[11].fix_mr_iids is None and found[12].labels == ["sev1"]


def test_issues_off_skips_incidents(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{P}/issues").mock(return_value=httpx.Response(403, json={"message": "403"}))
        run(db_session, project)
    assert db_session.scalars(select(Incident)).all() == []
