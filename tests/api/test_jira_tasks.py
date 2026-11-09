"""Task Jira (docs/specs/27 §18, user 2026-11-09): issues with their MRs and reviews."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, BusinessProject, Commit, IssueMrLink, MergeRequest, Project
from tests.api.conftest import World
from tests.api.test_dpi_jira import jira_issue, jira_project

API = "/api/v1/jira/tasks"
OPENED = datetime(2026, 10, 1, tzinfo=UTC)


def _setup(session: Session, world: World) -> dict[str, Any]:
    """ABC feeds the dự án everyone sees: ABC-1 (an's, linked to the world's MR, reviewed with
    a critical finding), ABC-2 (its sub-task, closed, no MR). XYZ feeds another dự án nobody
    here is granted; NEW is routed to no dự án (admins only)."""
    abc = jira_project(session, "ABC", world.project.business_project_id)
    other = BusinessProject(name="Other")
    session.add(other)
    session.flush()
    xyz = jira_project(session, "XYZ", other.id)
    new = jira_project(session, "NEW", None)
    jira_an = Account(kind="jira_user", external_key="JIRAUSER9", user_id=world.an.id)
    session.add(jira_an)
    session.flush()
    one = jira_issue(
        session,
        abc,
        1,
        state="opened",
        type="feature",
        story_points=Decimal(5),
        assignee_account_ids=[jira_an.id],
        created_at_src=OPENED,
        web_url="https://jira.example/browse/ABC-1",
    )
    two = jira_issue(
        session,
        abc,
        2,
        state="closed",
        type="bug",
        parent_key="ABC-1",
        created_at_src=OPENED,
        closed_at_src=datetime(2026, 10, 10, tzinfo=UTC),
    )
    jira_issue(session, xyz, 1, state="opened", created_at_src=OPENED)
    jira_issue(session, new, 1, state="opened", created_at_src=OPENED)
    session.add(IssueMrLink(issue_id=one.id, mr_id=world.mr.id, source="closes"))
    session.flush()
    return {"one": one, "two": two}


def _keys(body: dict[str, Any]) -> list[str]:
    return [i["key"] for i in body["items"]]


def test_issues_come_with_their_mrs_and_reviews(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _setup(db_session, world)
    body = client.get(API, headers=as_user(world.admin)).json()
    assert sorted(_keys(body)) == ["ABC-1", "ABC-2", "NEW-1", "XYZ-1"]
    one = next(i for i in body["items"] if i["key"] == "ABC-1")
    assert (one["mrs"], one["reviews"], one["score"], one["critical"]) == (1, 1, 50.0, 1)
    assert one["assignees"] == [{"user_id": world.an.id, "name": "An"}]
    assert one["url"] == "https://jira.example/browse/ABC-1" and one["story_points"] == 5.0
    assert body["kpis"] == {
        "issues": 4,
        "open": 3,
        "closed_in_period": 1,
        "with_mr": 1,
        "with_points": 1,
        "severe_open": 1,
        "bugs": 1,
        "bugs_fixed": 0,
    }


def test_a_member_sees_their_dự_án_and_only_the_reviews_they_may_see(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _setup(db_session, world)
    # An: their own review is visible.
    an = client.get(API, headers=as_user(world.an)).json()
    assert sorted(_keys(an)) == ["ABC-1", "ABC-2"]  # not XYZ (no grant), not NEW (unrouted)
    assert next(i for i in an["items"] if i["key"] == "ABC-1")["score"] == 50.0
    # Bình sees the issue and the MR, not An's review.
    binh = client.get(API, headers=as_user(world.binh)).json()
    one = next(i for i in binh["items"] if i["key"] == "ABC-1")
    assert (one["mrs"], one["reviews"], one["score"], one["critical"]) == (1, 0, None, 0)
    assert binh["kpis"]["severe_open"] == 0
    other = db_session.query(BusinessProject).filter_by(name="Other").one()
    res = client.get(API, params={"business_project_id": other.id}, headers=as_user(world.binh))
    assert res.status_code == 404


def test_filters(client: TestClient, world: World, as_user: Any, db_session: Session) -> None:
    _setup(db_session, world)
    admin = as_user(world.admin)

    def keys(**params: Any) -> list[str]:
        return sorted(_keys(client.get(API, params=params, headers=admin).json()))

    assert keys(state="closed") == ["ABC-2"]
    assert keys(linked="with_mr") == ["ABC-1"]
    assert keys(linked="without_mr") == ["ABC-2", "NEW-1", "XYZ-1"]
    assert keys(q="abc-2") == ["ABC-2"]
    assert keys(jira_project="XYZ") == ["XYZ-1"]
    assert keys(type="bug") == ["ABC-2"]
    assert keys(assignee=world.an.id) == ["ABC-1"]
    assert keys(business_project_id=world.project.business_project_id) == ["ABC-1", "ABC-2"]
    # Worked on in the period: ABC-2 closed on 10/10 is out of a period starting 15/10.
    assert keys(start="2026-10-15", end="2026-10-31") == ["ABC-1", "NEW-1", "XYZ-1"]
    assert (
        client.get(
            API, params={"start": "2026-10-31", "end": "2026-10-01"}, headers=admin
        ).status_code
        == 422
    )


def test_an_issue_with_its_family_and_mrs(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _setup(db_session, world)
    one = client.get(f"{API}/abc-1", headers=as_user(world.an)).json()
    assert one["key"] == "ABC-1" and [c["key"] for c in one["children"]] == ["ABC-2"]
    mr = one["merge_requests"][0]
    assert mr["iid"] == 7 and mr["url"] == "https://gitlab.example.com/demo/p/-/merge_requests/7"
    assert mr["review"]["id"] == world.an_review.id and mr["review"]["critical"] == 1
    two = client.get(f"{API}/ABC-2", headers=as_user(world.an)).json()
    assert two["parent"]["key"] == "ABC-1"
    # Not routed to a dự án one sees: as if it did not exist.
    assert client.get(f"{API}/XYZ-1", headers=as_user(world.an)).status_code == 404
    assert client.get(f"{API}/XYZ-1", headers=as_user(world.admin)).status_code == 200


def test_mrs_that_name_no_issue(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _setup(db_session, world)
    lonely = MergeRequest(
        project_id=world.project.id,
        iid=8,
        title="Sửa nhanh",
        source_branch="hotfix",
        created_at_src=datetime(2026, 10, 5, tzinfo=UTC),
        jira_keys=["ZZZ-9"],
    )
    db_session.add(lonely)
    db_session.flush()
    body = client.get(f"{API}/unlinked-mrs", headers=as_user(world.binh)).json()
    assert [m["iid"] for m in body["items"]] == [8]  # MR 7 names ABC-1
    assert body["items"][0]["keys"] == ["ZZZ-9"] and body["total"] == 1
    out = client.get(
        f"{API}/unlinked-mrs", params={"start": "2026-10-06"}, headers=as_user(world.binh)
    ).json()
    assert out["total"] == 0
    # A repo outside every dự án one sees: not listed.
    hidden = Project(
        connection_id=world.project.connection_id, external_id=9, path_with_namespace="x/y"
    )
    db_session.add(hidden)
    db_session.flush()
    db_session.add(MergeRequest(project_id=hidden.id, iid=1))
    db_session.flush()
    assert client.get(f"{API}/unlinked-mrs", headers=as_user(world.binh)).json()["total"] == 1


def test_options_follow_what_one_sees(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _setup(db_session, world)
    binh = client.get(f"{API}/options", headers=as_user(world.binh)).json()
    assert [p["key"] for p in binh["jira_projects"]] == ["ABC"]
    assert [b["name"] for b in binh["business_projects"]] == ["Demo"]
    assert binh["types"] == ["bug", "feature"] and binh["can_admin"] is False
    admin = client.get(f"{API}/options", headers=as_user(world.admin)).json()
    assert [p["key"] for p in admin["jira_projects"]] == ["ABC", "NEW", "XYZ"]


def test_commits_that_fix_an_issue(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """§19: a commit naming the bug ABC-2 with `Fixes:` is its fix; one without a key is listed
    under the commits that name no issue."""
    from app.jira.commit_links import link_commits

    _setup(db_session, world)
    admin = as_user(world.admin)
    unfixed = client.get(API, params={"linked": "bug_unfixed"}, headers=admin).json()
    assert sorted(_keys(unfixed)) == ["ABC-2"]
    fix = Commit(
        sha="9" * 40,
        project_id=world.project.id,
        message="fix(export): căn cột [ABC-2]\n\nFixes: ABC-2",
        authored_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    bare = Commit(
        sha="8" * 40,
        project_id=world.project.id,
        message="sửa nhanh",
        authored_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    db_session.add_all([fix, bare])
    db_session.flush()
    link_commits(db_session, [fix.sha, bare.sha])

    body = client.get(API, headers=admin).json()
    two = next(i for i in body["items"] if i["key"] == "ABC-2")
    assert (two["commit_count"], two["fix_commits"]) == (1, 1)
    assert body["kpis"]["bugs_fixed"] == 1
    assert client.get(API, params={"linked": "bug_unfixed"}, headers=admin).json()["items"] == []

    detail = client.get(f"{API}/ABC-2", headers=as_user(world.binh)).json()
    assert [(c["sha"], c["kind"]) for c in detail["commits"]] == [(fix.sha, "fixes")]
    assert detail["commits"][0]["url"] == f"https://gitlab.example.com/demo/p/-/commit/{fix.sha}"

    out = client.get(f"{API}/unlinked-commits", headers=as_user(world.binh)).json()
    shas = [c["sha"] for c in out["items"]]
    assert bare.sha in shas and fix.sha not in shas
    assert out["with_issue"] == 1 and out["commits_in_period"] == out["total"] + 1
