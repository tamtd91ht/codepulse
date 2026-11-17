"""docs/specs/31 §21.4: find anything - the condition tree, full text without accents, saved
filters (scope, pin, subscription), CSV."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task, TaskFilter, TaskGitLink, TaskSpace, UserNotification
from app.models.tasks import TaskTestLink
from app.tasks import search as sx
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, status_id, ungrant
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration


def find(client: TestClient, h: dict[str, str], tree: Any, **kw: Any) -> list[str]:
    res = client.post(f"{API}/tasks/search", headers=h, json={"tree": tree, **kw})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == len(body["items"]) or "limit" in kw
    return sorted(t["key"] for t in body["items"])


def leaf(field: str, op: str = "is", value: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"field": field, "op": op}
    if value is not None:
        out["value"] = value
    return out


def test_fold() -> None:
    assert sx.fold("  Đăng   NHẬP bằng OTP ") == "dang nhap bang otp"
    assert sx.fold(None) == ""


def test_clean_tree_rejects_nonsense() -> None:
    assert sx.clean_tree(None) == {"all": []}
    assert sx.clean_tree(leaf("type", "is", "bug")) == {
        "all": [{"field": "type", "op": "is", "value": ["bug"]}]
    }
    for bad in (
        {"all": [leaf("nope")]},
        {"all": [leaf("type", "before", "2026-01-01")]},
        {"all": [leaf("due", "before", "hôm qua")]},
        {"all": [leaf("type", "is")]},
        {"all": [leaf("dod_met", "is", "maybe")]},
        {"all": "x"},
    ):
        with pytest.raises(HTTPException) as err:
            sx.clean_tree(bad)
        assert err.value.status_code == 422
    deep: dict[str, Any] = leaf("type", "is", "bug")
    for _ in range(6):
        deep = {"any": [deep]}
    with pytest.raises(HTTPException):
        sx.clean_tree(deep)
    assert sx.from_query({"q": "otp", "assignee": "me", "updated_from": "2026-11-16"}) == {
        "all": [
            {"field": "text", "op": "is", "value": "otp"},
            {"field": "assignee", "op": "is", "value": ["me"]},
            {"field": "updated", "op": "after", "value": "2026-11-15"},
        ]
    }


def test_conditions_and_groups(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    admin, an = as_user(world.admin), as_user(world.an)
    today = datetime.now(UTC).date()
    create(client, an, world, title="A", type="bug", labels=["api"], due_date=str(today))
    create(client, admin, world, title="B", type="story", assignee_id=world.binh.id)
    create(
        client,
        admin,
        world,
        title="C",
        type="task",
        labels=["ui", "api"],
        due_date=str(today + timedelta(days=20)),
        story_points=5,
    )
    bp = bp_of(world)
    # OR within a field, AND between fields.
    assert find(client, an, {"all": [leaf("type", "is", ["bug", "story"])]}) == ["CRM-1", "CRM-2"]
    assert find(
        client,
        an,
        {
            "all": [
                {"any": [leaf("type", "is", "bug"), leaf("type", "is", "task")]},
                leaf("label", "is", "api"),
            ]
        },
    ) == ["CRM-1", "CRM-3"]
    assert find(client, an, {"all": [leaf("type", "is_not", "bug")]}) == ["CRM-2", "CRM-3"]
    # Empty / has a value; people; reporter.
    assert find(client, an, {"all": [leaf("label", "empty")]}) == ["CRM-2"]
    assert find(client, an, {"all": [leaf("assignee", "not_empty")]}) == ["CRM-2"]
    assert find(client, an, {"all": [leaf("assignee", "is_not", str(world.binh.id))]}) == [
        "CRM-1",
        "CRM-3",
    ]
    assert find(client, an, {"all": [leaf("reporter", "is", "me")]}) == ["CRM-1"]
    # The watcher: the assignee and the reporter watch.
    assert find(client, as_user(world.binh), {"all": [leaf("watcher", "is", "me")]}) == ["CRM-2"]
    # Dates: before / after / within N days, "overdue".
    assert find(client, an, {"all": [leaf("due", "within_days", 7)]}) == ["CRM-1"]
    assert find(client, an, {"all": [leaf("due", "after", str(today))]}) == ["CRM-3"]
    assert find(client, an, {"all": [leaf("due", "before", str(today + timedelta(1)))]}) == [
        "CRM-1"
    ]
    assert find(client, an, {"all": [leaf("due", "empty")]}) == ["CRM-2"]
    assert find(client, an, {"all": [leaf("created", "within_days", 1)]}) == [
        "CRM-1",
        "CRM-2",
        "CRM-3",
    ]
    assert find(client, an, {"all": [leaf("points", "gte", 3)]}) == ["CRM-3"]
    # Status, category, sprint backlog, space.
    doing = status_id(space, "Đang làm")
    res = client.post(f"{API}/tasks/CRM-2/transition", headers=admin, json={"status_id": doing})
    assert res.status_code == 200, res.text
    assert find(client, an, {"all": [leaf("status", "is", doing)]}) == ["CRM-2"]
    assert find(client, an, {"all": [leaf("category", "is", "todo")]}) == ["CRM-1", "CRM-3"]
    assert find(client, an, {"all": [leaf("sprint", "is", "backlog")]}) == [
        "CRM-1",
        "CRM-2",
        "CRM-3",
    ]
    assert find(client, an, {"all": [leaf("space", "is", bp)]}, space=[bp]) == [
        "CRM-1",
        "CRM-2",
        "CRM-3",
    ]
    # Age in the column (status_changed_at).
    row = db_session.scalar(select(Task).where(Task.key == "CRM-2"))
    assert row is not None
    row.status_changed_at = datetime.now(UTC) - timedelta(hours=50)
    db_session.flush()
    assert find(client, an, {"all": [leaf("age_in_column_hours", "gte", 48)]}) == ["CRM-2"]
    assert "CRM-2" not in find(client, an, {"all": [leaf("age_in_column_hours", "lte", 48)]})
    # Blocked by an open task.
    res = client.post(
        f"{API}/tasks/CRM-1/links", headers=an, json={"key": "CRM-3", "kind": "blocks"}
    )
    assert res.status_code == 201
    assert find(client, an, {"all": [leaf("blocked", "is", True)]}) == ["CRM-3"]
    assert find(client, an, {"all": [leaf("blocked", "is", "false")]}) == ["CRM-1", "CRM-2"]
    # Paged and sorted like the List.
    res = client.post(
        f"{API}/tasks/search",
        headers=an,
        json={"tree": None, "sort": "key", "order": "desc", "limit": 2, "offset": 0},
    ).json()
    assert [t["key"] for t in res["items"]] == ["CRM-3", "CRM-2"] and res["total"] == 3
    # A bad tree is a 422.
    bad = client.post(f"{API}/tasks/search", headers=an, json={"tree": {"all": [leaf("x")]}})
    assert bad.status_code == 422


def test_custom_fields_resolution(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    res = client.put(
        f"{API}/tasks/spaces/{bp_of(world)}/fields",
        headers=admin,
        json={
            "fields": [
                {"name": "Khách", "kind": "select", "options": ["VIB", "MB"]},
                {"name": "Số ca", "kind": "number"},
            ]
        },
    )
    assert res.status_code == 200, res.text
    fields = {
        f["name"]: f["id"]
        for f in client.get(f"{API}/tasks/spaces/{bp_of(world)}/fields", headers=admin).json()
    }
    k, n = fields["Khách"], fields["Số ca"]
    create(client, admin, world, title="X", custom={str(k): "VIB", str(n): 3})
    create(client, admin, world, title="Y", custom={str(k): "MB", str(n): 12})
    create(client, admin, world, title="Z")
    assert find(client, admin, {"all": [leaf(f"custom:{k}", "is", "VIB")]}) == ["CRM-1"]
    assert find(client, admin, {"all": [leaf(f"custom:{k}", "empty")]}) == ["CRM-3"]
    assert find(client, admin, {"all": [leaf(f"custom:{n}", "gte", 10)]}) == ["CRM-2"]
    assert find(client, admin, {"all": [leaf(f"custom:{k}", "is_not", "VIB")]}) == [
        "CRM-2",
        "CRM-3",
    ]
    bad = client.post(
        f"{API}/tasks/search",
        headers=admin,
        json={"tree": {"all": [leaf(f"custom:{k}", "gte", 1)]}},
    )
    assert bad.status_code == 422
    for wrong in (
        leaf(f"custom:{n}", "is", "abc"),
        leaf(f"custom:{n}", "gte", "inf"),
        leaf("age_in_column_hours", "gte", "1e12"),
        leaf("points", "gte", "nan"),
    ):
        res = client.post(f"{API}/tasks/search", headers=admin, json={"tree": {"all": [wrong]}})
        assert res.status_code == 422, wrong
    # The chip bar's field list carries them.
    got = client.get(f"{API}/tasks/search/fields?space={bp_of(world)}", headers=admin).json()
    assert {c["field"] for c in got["custom"]} == {f"custom:{k}", f"custom:{n}"}
    assert "dod_met" in {f["field"] for f in got["fields"]}
    # Resolution.
    row = db_session.scalar(select(Task).where(Task.key == "CRM-3"))
    assert row is not None
    row.resolution = "wont_do"
    db_session.flush()
    assert find(client, admin, {"all": [leaf("resolution", "is", "wont_do")]}) == ["CRM-3"]
    assert find(client, admin, {"all": [leaf("resolution", "empty")]}) == ["CRM-1", "CRM-2"]


def test_code_tests_and_dod(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    one = create(client, admin, world, title="Có MR")
    two = create(client, admin, world, title="Có test")
    create(client, admin, world, title="Trống")
    # CRM-1 links the MR whose latest review has an open critical finding.
    db_session.add(
        TaskGitLink(task_id=one["id"], kind="mr", project_id=world.project.id, mr_iid=world.mr.iid)
    )
    made = _setup(client, db_session, admin, bp_of(world))
    db_session.add(TaskTestLink(task_id=two["id"], case_id=made["case"].id, kind="covers"))
    space = db_session.get(TaskSpace, bp_of(world))
    assert space is not None
    space.rules = {**space.rules, "dod": {"checks": ["has_mr"], "enforce": True}}
    db_session.flush()
    assert find(client, admin, {"all": [leaf("has_mr", "is", True)]}) == ["CRM-1"]
    assert find(client, admin, {"all": [leaf("has_critical", "is", True)]}) == ["CRM-1"]
    assert find(client, admin, {"all": [leaf("has_critical", "is", False)]}) == ["CRM-2", "CRM-3"]
    assert find(client, admin, {"all": [leaf("test_failed", "is", True)]}) == ["CRM-2"]
    assert find(client, admin, {"all": [leaf("dod_met", "is", "met")]}) == ["CRM-1"]
    assert find(client, admin, {"all": [leaf("dod_met", "is", "blocking")]}) == ["CRM-2", "CRM-3"]


def test_full_text_without_accents(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    an = as_user(world.an)
    create(client, an, world, title="Đăng nhập bằng OTP", description="Gửi mã qua SMS")
    create(client, an, world, title="Màn hình chính")
    before = db_session.scalar(select(Task.updated_at).where(Task.key == "CRM-2"))
    res = client.post(
        f"{API}/tasks/CRM-2/comments", headers=an, json={"body": "Mã hết hạn quá nhanh"}
    )
    assert res.status_code == 201
    row = db_session.scalar(select(Task).where(Task.key == "CRM-2"))
    assert row is not None
    db_session.refresh(row)
    assert "ma het han qua nhanh" in row.search_text
    assert before is not None
    text = lambda q: find(client, an, {"all": [leaf("text", "is", q)]})  # noqa: E731
    assert text("dang nhap") == ["CRM-1"]
    assert text("SMS otp") == ["CRM-1"]
    assert text("hết hạn") == ["CRM-2"]
    assert text("crm-2") == ["CRM-2"]
    assert text("100%") == []
    assert find(client, an, {"all": [leaf("text", "is_not", "otp")]}) == ["CRM-2"]
    # A title edit refreshes it too.
    assert (
        client.patch(f"{API}/tasks/CRM-1", headers=an, json={"title": "Quên mật khẩu"}).status_code
        == 200
    )
    assert text("dang nhap") == []
    assert text("quen mat khau") == ["CRM-1"]
    # The command palette: key first, then title, then text.
    got = client.get(f"{API}/tasks/search", headers=an, params={"q": "crm-1"}).json()["items"]
    assert got[0]["key"] == "CRM-1" and got[0]["match"] == "key"
    got = client.get(f"{API}/tasks/search", headers=an, params={"q": "het han"}).json()["items"]
    assert [(t["key"], t["match"]) for t in got] == [("CRM-2", "text")]
    assert got[0]["snippet"] and "het han" in got[0]["snippet"]
    got = client.get(f"{API}/tasks/search", headers=an, params={"q": "man hinh"}).json()["items"]
    assert [(t["key"], t["match"]) for t in got] == [("CRM-2", "title")]
    assert client.get(f"{API}/tasks/search", headers=an, params={"q": " "}).json()["items"] == []


def test_visibility(client: TestClient, world: World, as_user: Any, db_session: Session) -> None:
    enable(client, world, as_user)
    create(client, as_user(world.an), world, title="Bí mật")
    ungrant(db_session, world)
    out = as_user(world.outsider)
    assert find(client, out, None) == []
    assert client.get(f"{API}/tasks/search", headers=out, params={"q": "bi mat"}).json() == {
        "items": []
    }
    res = client.post(f"{API}/tasks/search", headers=out, json={"space": [bp_of(world)]})
    assert res.status_code == 404


def test_saved_filters_scope_pin_subscription(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    an, binh, lead, outsider = (
        as_user(world.an),
        as_user(world.binh),
        as_user(world.lead),
        as_user(world.outsider),
    )
    bp = bp_of(world)
    create(client, an, world, title="Bug 1", type="bug")
    tree = {"all": [leaf("type", "is", "bug")]}

    def save(h: dict[str, str], **body: Any) -> Any:
        return client.post(
            f"{API}/tasks/search/filters",
            headers=h,
            json={"name": "Bug", "space": bp, "tree": tree, **body},
        )

    me = save(an).json()
    team = save(an, name="Team", shared_scope="team").json()
    proj = save(an, name="Dự án", shared_scope="project", pinned=True).json()
    assert proj["shared"] is True and team["shared"] is False
    # Pinning needs a dự án.
    res = client.post(
        f"{API}/tasks/search/filters",
        headers=an,
        json={"name": "x", "tree": tree, "pinned": True},
    )
    assert res.status_code == 422

    def names(h: dict[str, str], **params: Any) -> list[str]:
        res = client.get(f"{API}/tasks/search/filters", headers=h, params=params)
        assert res.status_code == 200, res.text
        return sorted(f["name"] for f in res.json())

    assert names(an) == ["Bug", "Dự án", "Team"]
    assert names(binh) == ["Dự án", "Team"]  # same team (Backend)
    assert names(lead) == ["Dự án", "Team"]
    assert names(outsider) == ["Dự án"]  # Mobile: not the team
    assert names(binh, space=bp, pinned=True) == ["Dự án"]
    ungrant(db_session, world)
    assert names(outsider) == []
    # A filter shared with the older endpoint counts as "project".
    old = client.post(
        f"{API}/tasks/filters",
        headers=an,
        json={"name": "Cũ", "space": bp, "query": {"type": "bug"}, "shared": True},
    ).json()
    listed = {f["name"]: f for f in client.get(f"{API}/tasks/search/filters", headers=binh).json()}
    assert listed["Cũ"]["shared_scope"] == "project"
    assert listed["Cũ"]["tree"] == {"all": [leaf("type", "is", ["bug"])]}
    client.delete(f"{API}/tasks/filters/{old['id']}", headers=an)
    # The older list still shows a "project" filter to others.
    assert [f["name"] for f in client.get(f"{API}/tasks/filters", headers=binh).json()] == ["Dự án"]
    # Search by a saved filter; one you cannot see is a 404.
    res = client.post(f"{API}/tasks/search", headers=binh, json={"filter_id": team["id"]})
    assert [t["key"] for t in res.json()["items"]] == ["CRM-1"]
    res = client.post(f"{API}/tasks/search", headers=outsider, json={"filter_id": me["id"]})
    assert res.status_code == 404
    # Only the owner edits.
    url = f"{API}/tasks/search/filters/{me['id']}"
    assert client.patch(url, headers=binh, json={"name": "x"}).status_code == 404
    res = client.patch(
        url, headers=an, json={"subscription": {"cadence": "weekly"}, "pinned": True}
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["subscription"] == {"cadence": "weekly", "channel": "inbox"} and out["pinned"]
    assert (
        client.patch(url, headers=an, json={"subscription": {"cadence": "hourly"}}).status_code
        == 422
    )
    # The digest: Monday from 08:00 local, once.
    monday = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)  # 09:30 in Hà Nội
    assert sx.send_filter_digests(db_session, monday) == 1
    assert sx.send_filter_digests(db_session, monday + timedelta(minutes=15)) == 0
    assert sx.send_filter_digests(db_session, monday + timedelta(days=1)) == 0
    note = db_session.scalar(
        select(UserNotification).where(
            UserNotification.user_id == world.an.id, UserNotification.kind == sx.DIGEST_KIND
        )
    )
    assert note is not None and "CRM-1" in (note.body or "")
    assert note.link == f"/tasks?space={bp}&tab=list&filter={me['id']}"
    row = db_session.get(TaskFilter, me["id"])
    assert row is not None and row.subscription
    assert row.subscription["last_sent"] == date(2026, 11, 16).isoformat()
    res = client.patch(url, headers=an, json={"unsubscribe": True})
    assert res.json()["subscription"] is None


def test_csv_export(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    admin = as_user(world.admin)
    create(client, admin, world, title="=HYPERLINK(1)", labels=["a", "b"], story_points=3)
    create(client, admin, world, title="Khác", type="bug")
    res = client.post(
        f"{API}/tasks/search/export.csv",
        headers=admin,
        json={"tree": {"all": [leaf("type", "is", "task")]}, "sort": "key"},
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    text = res.content.decode("utf-8")
    assert text.startswith("﻿")
    lines = text.lstrip("﻿").splitlines()
    assert lines[0] == "key,title,type,status,priority,assignee,sprint,due,points,labels,resolution"
    assert lines[1].startswith("CRM-1,'=HYPERLINK(1),task,Cần làm,")
    assert '"a, b"' in lines[1] and len(lines) == 2
    import json

    res = client.get(
        f"{API}/tasks/search/export.csv",
        headers=admin,
        params={"tree": json.dumps({"all": [leaf("type", "is", "bug")]})},
    )
    assert res.status_code == 200
    assert "Khác" in res.content.decode("utf-8")
    bad = client.get(f"{API}/tasks/search/export.csv", headers=admin, params={"tree": "{"})
    assert bad.status_code == 422


def test_sentence_chips_carry_conditions(client: TestClient, world: World, as_user: Any) -> None:
    enable(client, world, as_user)
    an = as_user(world.an)
    res = client.post(
        f"{API}/tasks/nl-filter",
        headers=an,
        json={"space": bp_of(world), "text": "bug quá hạn của tôi tuần này otp"},
    )
    chips = {c["field"]: c for c in res.json()["chips"]}
    assert chips["type"]["cond"] == {"field": "type", "op": "is", "value": ["bug"]}
    assert chips["due"]["cond"] == {"field": "due", "op": "is", "value": "overdue"}
    assert chips["q"]["cond"] == {"field": "text", "op": "is", "value": "otp"}
    assert chips["updated_from"]["cond"]["op"] == "after"
    tree = {"all": [c["cond"] for c in chips.values()]}
    res = client.post(f"{API}/tasks/search", headers=an, json={"tree": tree})
    assert res.status_code == 200, res.text
