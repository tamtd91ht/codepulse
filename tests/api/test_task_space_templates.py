"""docs/specs/31 §20.4 Nhân rộng: saved templates, applying a template, the wizard's members step
and the adoption table."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Account, BusinessProject, Commit, ProjectGrant
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, status_id

pytestmark = pytest.mark.integration


def other_bp(session: Session, name: str = "Khác") -> int:
    bp = BusinessProject(name=name)
    session.add(bp)
    session.flush()
    return bp.id


def configure(client: TestClient, world: World, as_user: Any) -> dict[str, Any]:
    """A Kanban-ish CRM space with WIP, a custom priority, DoD, automation and a field."""
    admin = as_user(world.admin)
    bp = bp_of(world)
    space = enable(client, world, as_user)
    statuses = [
        {"id": s["id"], "name": s["name"], "category": s["category"], "transitions": None}
        for s in space["statuses"]
    ]
    statuses[1]["wip_limit"] = 3
    statuses[1]["age_warning_hours"] = 24
    res = client.put(
        f"{API}/tasks/spaces/{bp}/statuses", headers=admin, json={"statuses": statuses}
    )
    assert res.status_code == 200, res.text
    prios = [
        {
            "id": p["id"],
            "name": p["name"],
            "level": p["level"],
            "color": p["color"],
            "is_default": p["is_default"],
        }
        for p in space["priorities"]
    ] + [{"name": "Khẩn cấp", "level": "highest", "color": "purple"}]
    res = client.put(
        f"{API}/tasks/spaces/{bp}/priorities", headers=admin, json={"priorities": prios}
    )
    assert res.status_code == 200, res.text
    rules = {"dod": {"checks": ["has_mr"], "min_score": 7, "enforce": True}}
    res = client.put(f"{API}/tasks/spaces/{bp}", headers=admin, json={"key": "CRM", "rules": rules})
    assert res.status_code == 200, res.text
    res = client.put(
        f"{API}/tasks/spaces/{bp}/automation",
        headers=admin,
        json={"overdue_remind": {"on": True}},
    )
    assert res.status_code == 200, res.text
    res = client.put(
        f"{API}/tasks/spaces/{bp}/fields",
        headers=admin,
        json={"fields": [{"name": "Khách hàng", "kind": "text"}]},
    )
    assert res.status_code == 200, res.text
    return space


def test_builtin_templates_include_mobile(client: TestClient, world: World, as_user: Any) -> None:
    res = client.get(f"{API}/tasks/templates", headers=as_user(world.an))
    assert res.status_code == 200, res.text
    keys = [t["key"] for t in res.json()]
    assert keys[:4] == ["scrum", "kanban", "qa", "mobile"]
    mobile = next(t for t in res.json() if t["key"] == "mobile")
    assert mobile["builtin"] and mobile["fields"] == ["Nền tảng"]
    assert mobile["board_mode"] == "scrum"


def test_save_and_apply_to_a_new_space(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    configure(client, world, as_user)
    admin = as_user(world.admin)
    bp = bp_of(world)
    # A plain member cannot save.
    denied = client.post(
        f"{API}/tasks/spaces/{bp}/save-template", headers=as_user(world.an), json={"name": "X"}
    )
    assert denied.status_code == 403
    saved = client.post(
        f"{API}/tasks/spaces/{bp}/save-template",
        headers=admin,
        json={"name": "Cấu hình CRM", "description": "dùng lại"},
    )
    assert saved.status_code == 201, saved.text
    tpl = saved.json()
    assert tpl["key"] == "cau-hinh-crm" and not tpl["builtin"] and tpl["can_delete"]
    assert tpl["has_dod"] and tpl["has_automation"]
    assert "Khẩn cấp" in tpl["priorities"] and tpl["fields"] == ["Khách hàng"]
    again = client.post(
        f"{API}/tasks/spaces/{bp}/save-template", headers=admin, json={"name": "cấu hình crm"}
    )
    assert again.status_code == 409

    # Another dự án turned on (Scrum), then the saved template applied: everything copies.
    other = other_bp(db_session)
    res = client.put(f"{API}/tasks/spaces/{other}", headers=admin, json={"key": "FE"})
    assert res.status_code == 200, res.text
    assert res.json()["template_key"] == "scrum"
    res = client.post(
        f"{API}/tasks/spaces/{other}/apply-template", headers=admin, json={"key": tpl["key"]}
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["mode"] == "full" and out["skipped"] == []
    space = out["space"]
    assert space["template_key"] == "cau-hinh-crm"
    doing = next(s for s in space["statuses"] if s["name"] == "Đang làm")
    assert doing["wip_limit"] == 3 and doing["age_warning_hours"] == 24
    assert "Khẩn cấp" in [p["name"] for p in space["priorities"]]
    assert space["rules"]["dod"]["enforce"] is True
    assert space["rules"]["start"] == status_id(space, "Đang làm")
    fields = client.get(f"{API}/tasks/spaces/{other}/fields", headers=admin).json()
    assert [f["name"] for f in fields] == ["Khách hàng"]
    auto = client.get(f"{API}/tasks/spaces/{other}/automation", headers=admin)
    assert auto.status_code == 200, auto.text
    assert "overdue_remind" in auto.text

    # Deleting: only its creator or an admin.
    tid = tpl["id"]
    assert client.delete(f"{API}/tasks/templates/{tid}", headers=as_user(world.an)).status_code == (
        403
    )
    assert client.delete(f"{API}/tasks/templates/{tid}", headers=admin).status_code == 204
    keys = [t["key"] for t in client.get(f"{API}/tasks/templates", headers=admin).json()]
    assert "cau-hinh-crm" not in keys


def test_apply_with_tasks_keeps_the_workflow(
    client: TestClient, world: World, as_user: Any
) -> None:
    admin = as_user(world.admin)
    bp = bp_of(world)
    space = enable(client, world, as_user)
    create(client, admin, world)
    before = space["statuses"]
    res = client.post(
        f"{API}/tasks/spaces/{bp}/apply-template", headers=admin, json={"key": "mobile"}
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["mode"] == "partial"
    assert any("Trạng thái" in s for s in out["skipped"])
    assert out["space"]["statuses"] == before
    assert out["space"]["template_key"] == "mobile"
    fields = client.get(f"{API}/tasks/spaces/{bp}/fields", headers=admin).json()
    assert [(f["name"], f["options"]) for f in fields] == [
        ("Nền tảng", ["iOS", "Android", "Cả hai"])
    ]
    # QA's rules apply where the names match nothing: the git targets stay as they were.
    res = client.post(f"{API}/tasks/spaces/{bp}/apply-template", headers=admin, json={"key": "qa"})
    assert res.status_code == 200, res.text
    rules = res.json()["space"]["rules"]
    assert rules["done_on_merge"] is False
    assert rules["done"] == status_id(space, "Xong")
    unknown = client.post(
        f"{API}/tasks/spaces/{bp}/apply-template", headers=admin, json={"key": "nope"}
    )
    assert unknown.status_code == 422
    member = client.post(
        f"{API}/tasks/spaces/{bp}/apply-template", headers=as_user(world.an), json={"key": "qa"}
    )
    assert member.status_code == 403


def test_enable_with_mobile_seeds_the_field(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    bp = bp_of(world)
    enable(client, world, as_user, key="APP")
    res = client.post(
        f"{API}/tasks/spaces/{bp}/apply-template",
        headers=admin,
        json={"key": "mobile", "types": ["story", "bug"], "board_mode": "kanban"},
    )
    assert res.status_code == 200, res.text
    space = res.json()["space"]
    assert space["types"] == ["story", "bug"] and space["board_mode"] == "kanban"
    fields = client.get(f"{API}/tasks/spaces/{bp}/fields", headers=admin).json()
    assert [f["name"] for f in fields] == ["Nền tảng"]


def test_members_and_suggestions(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = bp_of(world)
    db_session.execute(
        ProjectGrant.__table__.delete().where(ProjectGrant.user_id == world.outsider.id)
    )
    acc = Account(kind="git_email", external_key="outsider@x.vn", user_id=world.outsider.id)
    db_session.add(acc)
    db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Commit(
                sha="c" * 40,
                project_id=world.project.id,
                author_account_id=acc.id,
                authored_at=now - timedelta(days=3),
            ),
            Commit(
                sha="d" * 40,
                project_id=world.project.id,
                author_account_id=acc.id,
                authored_at=now - timedelta(days=200),
            ),
        ]
    )
    db_session.flush()
    res = client.get(f"{API}/tasks/spaces/{bp}/members", headers=as_user(world.admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert world.an.id in [m["user_id"] for m in body["members"]]
    assert [(s["user_id"], s["commits"]) for s in body["suggestions"]] == [(world.outsider.id, 1)]
    assert body["can_grant"] is True
    denied = client.get(f"{API}/tasks/spaces/{bp}/members", headers=as_user(world.an))
    assert denied.status_code == 403


def test_adoption_table(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    enable(client, world, as_user)
    create(client, admin, world)
    off = other_bp(db_session, "Chưa bật")
    res = client.get(f"{API}/tasks/adoption", headers=admin)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["all_projects"] is True
    rows = {r["business_project_id"]: r for r in body["rows"]}
    on = rows[bp_of(world)]
    assert on["enabled"] and on["key"] == "CRM" and on["template_key"] == "scrum"
    assert on["template_name"] == "Scrum"
    assert on["tasks"] == 1 and on["open"] == 1 and on["last_activity"]
    assert on["viewers"] >= 1
    assert rows[off]["enabled"] is False and rows[off]["tasks"] == 0
    assert client.get(f"{API}/tasks/adoption", headers=as_user(world.an)).status_code == 403
