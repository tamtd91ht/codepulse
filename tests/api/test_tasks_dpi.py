"""docs/specs/31 §15: CodePulse tasks as the DPI task source."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dpi.facts import active_people
from app.models import (
    Issue,
    IssueMrLink,
    JiraProject,
    MergeRequest,
    ProdDefect,
    TaskGitLink,
)
from app.tasks.dpi import TASK_ID_OFFSET
from tests.api.conftest import World

pytestmark = pytest.mark.integration

API = "/api/v1"


def bp_of(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def setup_space(client: TestClient, world: World, as_user: Any) -> dict[str, Any]:
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}", headers=as_user(world.admin), json={"key": "CRM"}
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def create(client: TestClient, admin: dict[str, str], world: World, **fields: Any) -> Any:
    res = client.post(
        f"{API}/tasks", headers=admin, json={"space": bp_of(world), "title": "Việc", **fields}
    )
    assert res.status_code == 201, res.text
    return res.json()


def rows(session: Session) -> dict[str, Issue]:
    session.expire_all()
    return {
        str(i.key): i for i in session.scalars(select(Issue).where(Issue.source == "codepulse"))
    }


def test_switching_projects_every_task_and_edits_follow(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = setup_space(client, world, as_user)
    admin = as_user(world.admin)
    epic = create(client, admin, world, type="epic", title="Epic")
    story = create(
        client,
        admin,
        world,
        type="story",
        title="OTP",
        story_points=4,
        assignee_id=world.an.id,
        parent_key=epic["key"],
    )
    create(client, admin, world, type="subtask", title="API", parent_key=story["key"])
    # A Jira issue of old with the same key stays beside it (keys are unique per source).
    jira = JiraProject(key="CRM")
    db_session.add(jira)
    db_session.flush()
    db_session.add(Issue(id=10**15 + 1, source="jira", jira_project_id=jira.id, iid=2, key="CRM-2"))
    db_session.flush()
    # Before the switch nothing is written.
    assert rows(db_session) == {}
    res = client.put(f"{API}/dpi/jira/source", headers=admin, json={"source": "codepulse"})
    assert res.status_code == 200, res.text
    assert res.json()["tasks_projected"] == 2  # the epic is no DPI task
    got = rows(db_session)
    assert set(got) == {"CRM-2", "CRM-3"}
    row = got["CRM-2"]
    assert row.id == TASK_ID_OFFSET + story["id"]
    assert (row.type, row.complexity, row.complexity_points) == ("feature", "L", 5)
    assert row.parent_key is None  # a story under an epic is a task of its own
    assert row.business_project_ids == [bp_of(world)]
    assert got["CRM-3"].parent_key == "CRM-2" and got["CRM-3"].type == "feature"
    assert row.state == "opened" and row.assignee_account_ids
    # Done: closed this month, and An is someone the DPI cycle scores.
    done = next(s["id"] for s in space["statuses"] if s["name"] == "Xong")
    res = client.post(f"{API}/tasks/CRM-2/transition", headers=admin, json={"status_id": done})
    assert res.status_code == 200
    row = rows(db_session)["CRM-2"]
    assert row.state == "closed" and row.closed_at_src is not None
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    month = datetime.now(tz).date().replace(day=1)
    assert world.an.id in active_people(db_session, month, tz, "codepulse")
    # Closed as "won't do" is no output: its row goes (docs/specs/31 §19.1).
    done = next(s["id"] for s in space["statuses"] if s["category"] == "done")
    res = client.post(
        f"{API}/tasks/CRM-3/transition",
        headers=admin,
        json={"status_id": done, "resolution": "wont_do"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["resolution"] == "wont_do"
    assert "CRM-3" not in rows(db_session)


def test_production_bugs_links_and_deletion(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    setup_space(client, world, as_user)
    admin = as_user(world.admin)
    assert (
        client.put(
            f"{API}/dpi/jira/source", headers=admin, json={"source": "codepulse"}
        ).status_code
        == 200
    )
    bug = create(
        client,
        admin,
        world,
        type="bug",
        title="Lỗi thanh toán",
        priority="highest",
        labels=["production"],
    )
    child = create(client, admin, world, type="subtask", title="Sửa", parent_key=bug["key"])
    ident = TASK_ID_OFFSET + bug["id"]
    defect = db_session.scalars(select(ProdDefect).where(ProdDefect.issue_id == ident)).one()
    assert defect.severity == "P1"
    # A linked MR becomes a DPI "closes" link.
    mr = MergeRequest(id=77001, project_id=world.project.id, iid=31, state="merged")
    db_session.add(mr)
    db_session.flush()
    res = client.post(
        f"{API}/tasks/{bug['key']}/git",
        headers=admin,
        json={"kind": "mr", "project_id": world.project.id, "mr_iid": 31, "relation": "fixes"},
    )
    assert res.status_code == 201, res.text
    db_session.expire_all()
    link = db_session.scalars(select(IssueMrLink).where(IssueMrLink.issue_id == ident)).one()
    assert (link.mr_id, link.source) == (mr.id, "closes")
    assert db_session.scalars(select(TaskGitLink)).first() is not None
    # Deleting the bug takes its sub-task and both DPI rows with it.
    assert client.delete(f"{API}/tasks/{bug['key']}", headers=admin).status_code == 204
    assert rows(db_session) == {}
    assert child["key"] == "CRM-2"


def test_back_to_gitlab_stops_projection(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    setup_space(client, world, as_user)
    admin = as_user(world.admin)
    client.put(f"{API}/dpi/jira/source", headers=admin, json={"source": "codepulse"})
    res = client.put(f"{API}/dpi/jira/source", headers=admin, json={"source": "gitlab"})
    assert res.status_code == 200 and res.json()["full_syncs_queued"] >= 1
    create(
        client, admin, world, title="Sau khi chuyển về GitLab", due_date=date.today().isoformat()
    )
    assert rows(db_session) == {}
    # A member cannot switch the source.
    res = client.put(
        f"{API}/dpi/jira/source", headers=as_user(world.an), json={"source": "codepulse"}
    )
    assert res.status_code == 403
    assert datetime.now(UTC)


def test_the_switch_lives_on_the_dpi_page(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """docs/specs/31 §16: GET / PUT /dpi/task-source, for dpi.admin (admins)."""
    setup_space(client, world, as_user)
    admin = as_user(world.admin)
    create(client, admin, world, title="Một việc")
    view = client.get(f"{API}/dpi/task-source", headers=admin).json()
    assert view["task_source"] == "gitlab" and view["can_change"] is True
    options = {o["value"]: o["available"] for o in view["options"]}
    assert options == {"gitlab": True, "jira": False, "codepulse": True}
    # Jira is not connected: it cannot be chosen.
    res = client.put(f"{API}/dpi/task-source", headers=admin, json={"source": "jira"})
    assert res.status_code == 409
    res = client.put(f"{API}/dpi/task-source", headers=admin, json={"source": "codepulse"})
    assert res.status_code == 200, res.text
    assert res.json()["tasks_projected"] == 1
    assert set(rows(db_session)) == {"CRM-1"}
    # A member reads it (dpi.view) but cannot switch.
    an = as_user(world.an)
    member_view = client.get(f"{API}/dpi/task-source", headers=an).json()
    assert member_view["task_source"] == "codepulse" and member_view["can_change"] is False
    assert (
        client.put(f"{API}/dpi/task-source", headers=an, json={"source": "gitlab"}).status_code
        == 403
    )
