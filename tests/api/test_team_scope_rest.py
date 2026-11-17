"""docs/specs/31 §21.5 (R1b): the "team" scope of the dự án-wide permissions (tests.edit /
approve / manage / delete, tasks.sprint / manage) and "Quyền của tôi" (GET /me/permissions)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.security import Principal, load_principal
from app.config import Settings
from app.models import BusinessProject, ProjectGrant, User
from app.permissions.catalog import BUILTIN_SETS, BY_KEY, PROJECT_TEAM_KEYS
from app.tasks import service as ts
from app.testcases import approval
from app.testcases import service as tc
from tests.api.conftest import World, grant
from tests.api.test_permission_sets_api import give
from tests.api.test_tasks import API, bp_of, create, enable

pytestmark = pytest.mark.integration


def project(db: Session, name: str) -> int:
    bp = BusinessProject(name=name)
    db.add(bp)
    db.flush()
    return int(bp.id)


def who(db: Session, settings: Settings, user: User) -> Principal:
    return load_principal(db, user.id, settings)


@pytest.fixture
def places(db_session: Session, world: World) -> dict[str, int]:
    """Other: an (led by lead, managed by manager) works there; Far: nobody of a team does.
    The lead and the manager see both (a view grant)."""
    other, far = project(db_session, "Other"), project(db_session, "Far")
    grant(db_session, world.an, other)
    for user in (world.lead, world.manager):
        grant(db_session, user, other)
        grant(db_session, user, far)
    return {"other": other, "far": far}


R1B = {
    "tests.view": "flag",
    "tasks.view": "flag",
    "tests.edit": "team",
    "tests.approve": "team",
    "tests.manage": "team",
    "tests.delete": "team",
    "tasks.sprint": "team",
    "tasks.manage": "team",
}


def test_catalog_scopes_in_order_and_lead_set_unchanged() -> None:
    for key in PROJECT_TEAM_KEYS:
        assert BY_KEY[key].scopes == ("team", "project", "all"), key
    # Decision (§21.5): a lead coaches, they do not own the dự án their people work in; the
    # built-in Lead set gets none of these (an admin gives them in a set when wanted).
    lead = next(b for b in BUILTIN_SETS if b.auto_role == "lead")
    assert not PROJECT_TEAM_KEYS & set(lead.items)


def test_team_reaches_the_dru_an_of_ones_people(
    db_session: Session, world: World, settings: Settings, places: dict[str, int]
) -> None:
    give(db_session, world, world.lead, R1B)
    p = who(db_session, settings, world.lead)
    other, far = places["other"], places["far"]
    assert p.team_projects("tests.edit") >= {other}
    assert far not in p.team_projects("tests.edit")
    for bp, expected in ((other, True), (far, False)):
        assert tc.may_edit(p, bp) is expected
        assert tc.may_manage(p, bp) is expected
        assert approval.may_delete(p, bp) is expected
        assert approval.may_approve(p, bp) is expected
        assert ts.can_sprint(p, bp) is expected
        assert ts.can_manage_space(p, bp) is expected
    # One's own grant is not the team's: the lead sees Far, still no reach there.
    assert p.can_view_project(far)


def test_team_needs_the_dru_an_visible_and_an_active_person(
    db_session: Session, world: World, settings: Settings, places: dict[str, int]
) -> None:
    give(db_session, world, world.lead, R1B)
    other = places["other"]
    # The lead loses the view grant on Other: the team's grant does not open it.
    db_session.execute(
        ProjectGrant.__table__.delete().where(
            ProjectGrant.user_id == world.lead.id, ProjectGrant.business_project_id == other
        )
    )
    db_session.flush()
    assert not tc.may_edit(who(db_session, settings, world.lead), other)
    grant(db_session, world.lead, other)
    assert tc.may_edit(who(db_session, settings, world.lead), other)
    world.an.active = False
    db_session.flush()
    assert not tc.may_edit(who(db_session, settings, world.lead), other)


def test_managed_team_reaches_the_people_one_manages(
    db_session: Session, world: World, settings: Settings, places: dict[str, int]
) -> None:
    give(db_session, world, world.manager, R1B)
    solo = project(db_session, "Solo")  # only the manager works there
    grant(db_session, world.manager, solo)
    p = who(db_session, settings, world.manager)
    assert ts.can_sprint(p, places["other"]) and tc.may_edit(p, places["other"])
    # The lead is in a sub-team of Engineering, which the manager manages: Far is reached too.
    assert ts.can_sprint(p, places["far"])
    assert not ts.can_sprint(p, solo) and not tc.may_edit(p, solo)


def test_tests_run_team_stays_per_test(
    db_session: Session, world: World, settings: Settings, places: dict[str, int]
) -> None:
    give(db_session, world, world.lead, {"tests.view": "flag", "tests.run": "team"})
    assert not tc.may_run(who(db_session, settings, world.lead), places["other"])


def test_merging_with_project_keeps_the_team_but_project_alone_does_not_reach_it(
    db_session: Session, world: World, settings: Settings, places: dict[str, int]
) -> None:
    """§20.0 lesson: a lead who manages one dự án (the built-in Quản lý dự án set gives
    tasks.sprint / tests.edit at "project") must not lose the team's dự án; but "project" alone
    does not make the lead the owner of the dự án their people work in."""
    demo = bp_of(world)
    grant(db_session, world.lead, demo, "manage")
    p = who(db_session, settings, world.lead)
    assert p.scope("tasks.sprint") == "project" and p.scope("tests.edit") == "project"
    assert ts.can_sprint(p, demo) and tc.may_edit(p, demo)
    assert not ts.can_sprint(p, places["other"]) and not tc.may_edit(p, places["other"])
    give(db_session, world, world.lead, R1B)
    p = who(db_session, settings, world.lead)
    assert p.scope("tasks.sprint") == "project"  # merged to the widest
    assert ts.can_sprint(p, demo) and ts.can_sprint(p, places["other"])
    assert tc.may_edit(p, places["other"]) and not tc.may_edit(p, places["far"])


def test_sprint_endpoint_follows_the_team_scope(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    places: dict[str, int],
) -> None:
    admin = as_user(world.admin)
    for bp, key in ((places["other"], "OTH"), (places["far"], "FAR")):
        res = client.put(f"{API}/tasks/spaces/{bp}", headers=admin, json={"key": key})
        assert res.status_code == 200, res.text
    give(db_session, world, world.lead, R1B)
    lead = as_user(world.lead)
    ok = client.post(f"{API}/tasks/spaces/{places['other']}/sprints", headers=lead, json={})
    assert ok.status_code == 201, ok.text
    no = client.post(f"{API}/tasks/spaces/{places['far']}/sprints", headers=lead, json={})
    assert no.status_code == 403


# -- Quyền của tôi ---------------------------------------------------------------------------


def test_my_permissions_without_admin_rights(
    client: TestClient, world: World, as_user: Any
) -> None:
    an = as_user(world.an)
    assert client.get(f"{API}/admin/users/{world.an.id}/permissions", headers=an).status_code == 403
    res = client.get(f"{API}/me/permissions", headers=an)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["id"] == world.an.id and body["check"] is None
    assert [p["name"] for p in body["projects"]] == ["Demo"]
    edit = next(p for p in body["permissions"] if p["key"] == "tasks.edit")
    assert edit["scope"] == "own" and edit["meaning"] is None
    assert any(s["name"] == "Thành viên" for s in edit["sources"])
    explained = client.get(f"{API}/me/permissions?explain=1", headers=an).json()
    edit = next(p for p in explained["permissions"] if p["key"] == "tasks.edit")
    assert edit["meaning"] == "chỉ task bạn tạo hoặc được giao" and edit["label"]
    assert client.get(f"{API}/me/permissions").status_code == 401


def test_why_on_a_task(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    an, binh = as_user(world.an), as_user(world.binh)
    mine = create(client, an, world)["key"]
    theirs = create(client, binh, world)["key"]

    def check(headers: dict[str, str], query: str) -> dict[str, Any]:
        res = client.get(f"{API}/me/permissions?{query}", headers=headers)
        assert res.status_code == 200, res.text
        return dict(res.json()["check"])

    yes = check(an, f"check=tasks.edit&task={mine}")
    assert yes["allowed"] is True and "Đây là task của bạn" in yes["reason"]
    no = check(an, f"check=tasks.edit&task={theirs}")
    assert no["allowed"] is False
    assert "mức own (của mình)" in no["reason"] and "Binh" in no["reason"]
    assert "Thành viên" in no["reason"] and no["reason"].endswith("Kết luận: không được.")
    # The lead (built-in Lead set: tasks.edit at team) edits Binh's task: Binh is in the team.
    lead = check(as_user(world.lead), f"check=tasks.edit&task={theirs}")
    assert lead["allowed"] is True and "người trong team bạn" in lead["reason"]
    # Not held at all.
    gone = check(an, f"check=tasks.delete&task={theirs}")
    assert gone["allowed"] is False and "không có quyền tasks.delete" in gone["reason"]
    # A whole dự án for a task right: depends on the task.
    some = check(an, f"check=tasks.edit&project={bp_of(world)}")
    assert some["allowed"] is None
    admin = check(as_user(world.admin), f"check=tasks.delete&task={theirs}")
    assert admin["allowed"] is True and "admin" in admin["reason"]
    # Unknown key, unseen task or dự án.
    assert client.get(f"{API}/me/permissions?check=nope.x", headers=an).status_code == 422
    out = as_user(world.outsider)
    assert (
        client.get(f"{API}/me/permissions?check=tasks.edit&task=CRM-999", headers=an).status_code
        == 404
    )
    ungranted = client.get(
        f"{API}/me/permissions?check=tests.edit&project={bp_of(world) + 999}", headers=out
    )
    assert ungranted.status_code == 404


def test_why_on_a_dru_an(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    places: dict[str, int],
) -> None:
    give(db_session, world, world.lead, R1B)
    lead = as_user(world.lead)
    res = client.get(
        f"{API}/me/permissions?check=tests.edit&project={places['other']}", headers=lead
    )
    got = res.json()["check"]
    assert got["allowed"] is True and "An" in got["reason"] and got["target"] == "dự án Other"
    far = client.get(
        f"{API}/me/permissions?check=tasks.sprint&project={places['far']}", headers=lead
    ).json()["check"]
    assert far["allowed"] is False and "không có ai như vậy" in far["reason"]
    # No target: a "team" scope depends on the dự án, never a flat "được".
    loose = client.get(f"{API}/me/permissions?check=tests.edit", headers=lead).json()["check"]
    assert loose["allowed"] is None and loose["reason"].endswith("tuỳ task / dự án.")
