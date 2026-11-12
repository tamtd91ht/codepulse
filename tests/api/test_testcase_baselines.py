"""Baselines (docs/specs/30 §15.13, §15.16, 30a G9): create from a suite (or folders), list,
detail, delete, compare (two baselines, or a baseline and the suite now), runs from a baseline
that keep the baseline's content (AC: editing a case after the baseline leaves the run as it
was), permissions, isolation between dự án, and a 5 000-case baseline made quickly."""

import time
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BusinessProject,
    PermissionSet,
    TestBaseline,
    TestBaselineCase,
    TestCase,
    TestMilestone,
    TestRun,
    TestSection,
    TestSuite,
    TestSuiteLink,
    User,
    UserPermissionSet,
)
from app.testcases import baselines as bl
from tests.api.conftest import World, grant

API = "/api/v1"


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _sample(db: Session, user: User, role: str) -> None:
    set_id = db.scalar(select(PermissionSet.id).where(PermissionSet.name == f"Testcase - {role}"))
    assert set_id is not None, role
    db.add(UserPermissionSet(user_id=user.id, set_id=set_id))
    db.flush()


def _tree(db: Session, bp: int) -> dict[str, Any]:
    """Auth (login, logout) > Social (google); Pay (card, obsolete one)."""
    suite = TestSuite(business_project_id=bp, name="OMICX", position=1)
    db.add(suite)
    db.flush()
    auth = TestSection(suite_id=suite.id, name="Auth", position=1)
    pay = TestSection(suite_id=suite.id, name="Pay", position=2)
    db.add_all([auth, pay])
    db.flush()
    social = TestSection(suite_id=suite.id, parent_id=auth.id, name="Social", position=1)
    db.add(social)
    db.flush()

    def case(title: str, section: TestSection, position: int = 1, **kw: Any) -> TestCase:
        c = TestCase(suite_id=suite.id, section_id=section.id, title=title, position=position, **kw)
        db.add(c)
        db.flush()
        return c

    return {
        "suite": suite,
        "auth": auth,
        "social": social,
        "pay": pay,
        "login": case(
            "Đăng nhập",
            auth,
            priority="high",
            tags=["smoke"],
            template="steps",
            steps_list=[{"content": "Mở trang", "expected": "Có form"}],
            custom={"browser": "chrome"},
        ),
        "logout": case("Đăng xuất", auth, 2, expected="Về trang đăng nhập"),
        "google": case("Google", social, priority="low"),
        "card": case("Thẻ", pay, type="security"),
        "old": case("Cũ", pay, 2, status="obsolete"),
    }


def _baseline(client: TestClient, h: dict[str, str], suite_id: int, **body: Any) -> Any:
    got = client.post(
        f"{API}/testcases/baselines",
        json={"suite_id": suite_id, "name": body.pop("name", "Release 1.0"), **body},
        headers=h,
    )
    assert got.status_code == 201, got.text
    return got.json()


def test_create_list_detail_delete(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    t = _tree(db_session, bp)
    h, viewer = as_user(world.manager), as_user(world.an)

    made = _baseline(client, h, t["suite"].id, description="Chốt 1.0")
    assert made["case_count"] == 4 and made["suite_name"] == "OMICX"
    assert made["business_project_id"] == bp and made["created_by"] == world.manager.id
    assert made["created_by_name"] == world.manager.full_name and made["created_at"]
    # Reading order (folders in tree order), obsolete case left out.
    assert [c["title"] for c in made["cases"]] == ["Đăng nhập", "Đăng xuất", "Google", "Thẻ"]
    assert made["cases"][0]["key"] == f"C{t['login'].id}"
    assert made["cases"][2]["section"] == "Auth > Social"
    row = db_session.scalar(
        select(TestBaselineCase).where(
            TestBaselineCase.baseline_id == made["id"], TestBaselineCase.case_id == t["login"].id
        )
    )
    assert row is not None
    snap = row.snapshot
    assert snap["steps_list"] == [{"content": "Mở trang", "expected": "Có form"}]
    assert snap["custom"] == {"browser": "chrome"} and snap["tags"] == ["smoke"]
    assert snap["params"] == {} and snap["position"] == 1 and snap["section"] == "Auth"

    # Only some folders (with the ones below them).
    part = _baseline(client, h, t["suite"].id, name="Auth only", section_ids=[t["auth"].id])
    assert [c["title"] for c in part["cases"]] == ["Đăng nhập", "Đăng xuất", "Google"]

    listed = client.get(
        f"{API}/testcases/baselines", params={"business_project_id": bp}, headers=viewer
    )
    assert listed.status_code == 200, listed.text
    assert [b["id"] for b in listed.json()] == [part["id"], made["id"]]
    assert listed.json()[0]["can_edit"] is False
    by_suite = client.get(
        f"{API}/testcases/baselines",
        params={"business_project_id": bp, "suite_id": t["suite"].id + 999},
        headers=viewer,
    ).json()
    assert by_suite == []
    detail = client.get(f"{API}/testcases/baselines/{made['id']}", headers=viewer)
    assert detail.status_code == 200 and len(detail.json()["cases"]) == 4

    # A viewer may not delete; the manager may; audited.
    assert (
        client.delete(f"{API}/testcases/baselines/{made['id']}", headers=viewer).status_code == 403
    )
    assert client.delete(f"{API}/testcases/baselines/{made['id']}", headers=h).status_code == 204
    assert client.get(f"{API}/testcases/baselines/{made['id']}", headers=h).status_code == 404
    # The endpoint's row and the audit trail's row are one row (same action, same target).
    logged = db_session.scalars(
        select(AuditLog).where(AuditLog.target == f"baseline:{made['id']}")
    ).all()
    assert sorted(r.action for r in logged) == ["baseline.create", "baseline.delete"]
    for r in logged:
        assert (r.details or {}).get("name") == "Release 1.0"
        assert (r.details or {}).get("entity") == "baseline"
        assert r.business_project_id == bp

    # Bad input.
    bad = client.post(
        f"{API}/testcases/baselines",
        json={"suite_id": t["suite"].id, "name": "x", "section_ids": [999_999]},
        headers=h,
    )
    assert bad.status_code == 422


def test_permissions_and_isolation(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    t = _tree(db_session, bp)
    _sample(db_session, world.an, "Tester")
    tester = as_user(world.an)
    refused = client.post(
        f"{API}/testcases/baselines", json={"suite_id": t["suite"].id, "name": "R1"}, headers=tester
    )
    assert refused.status_code == 403, refused.text

    # Another dự án the manager does not see: its baselines are invisible.
    other = BusinessProject(name="Other")
    db_session.add(other)
    db_session.flush()
    suite = TestSuite(business_project_id=other.id, name="Hidden", position=1)
    db_session.add(suite)
    db_session.flush()
    db_session.add(TestCase(suite_id=suite.id, title="Bí mật", position=1))
    hidden = TestBaseline(
        business_project_id=other.id, suite_id=suite.id, suite_name="Hidden", name="H"
    )
    db_session.add(hidden)
    db_session.flush()
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    assert client.get(f"{API}/testcases/baselines/{hidden.id}", headers=h).status_code == 404
    assert client.delete(f"{API}/testcases/baselines/{hidden.id}", headers=h).status_code == 404
    assert (
        client.get(
            f"{API}/testcases/baselines", params={"business_project_id": other.id}, headers=h
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API}/testcases/baselines", json={"suite_id": suite.id, "name": "x"}, headers=h
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"{API}/testcases/baselines/compare", params={"a": hidden.id, "b": "current"}, headers=h
        ).status_code
        == 404
    )
    # A run of the manager's dự án cannot take the other dự án's baseline.
    got = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "baseline_id": hidden.id, "selection": {}},
        headers=h,
    )
    assert got.status_code == 404, got.text


def test_compare_added_removed_changed(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    t = _tree(db_session, bp)
    h = as_user(world.manager)
    a = _baseline(client, h, t["suite"].id, name="1.0")

    # After 1.0: login changes (title, priority), card goes obsolete, a new case appears.
    t["login"].title = "Đăng nhập bằng email"
    t["login"].priority = "critical"
    t["card"].status = "obsolete"
    new = TestCase(suite_id=t["suite"].id, section_id=t["pay"].id, title="Ví", position=3)
    db_session.add(new)
    db_session.flush()

    now = client.get(
        f"{API}/testcases/baselines/compare", params={"a": a["id"], "b": "current"}, headers=h
    )
    assert now.status_code == 200, now.text
    diff = now.json()
    assert diff["b"] is None and diff["a"]["id"] == a["id"]
    assert [x["key"] for x in diff["added"]] == [f"C{new.id}"]
    assert [x["key"] for x in diff["removed"]] == [f"C{t['card'].id}"]
    [changed] = diff["changed"]
    assert changed["key"] == f"C{t['login'].id}" and changed["case_id"] == t["login"].id
    assert {f["field"]: (f["before"], f["after"]) for f in changed["fields"]} == {
        "title": ("Đăng nhập", "Đăng nhập bằng email"),
        "priority": ("high", "critical"),
    }
    assert diff["same"] == 2

    b = _baseline(client, h, t["suite"].id, name="1.1")
    two = client.get(
        f"{API}/testcases/baselines/compare", params={"a": a["id"], "b": str(b["id"])}, headers=h
    ).json()
    assert two["b"]["id"] == b["id"]
    assert len(two["added"]) == 1 and len(two["removed"]) == 1 and len(two["changed"]) == 1
    # Same baseline twice: nothing differs.
    same = client.get(
        f"{API}/testcases/baselines/compare", params={"a": b["id"], "b": "current"}, headers=h
    ).json()
    assert same["added"] == same["removed"] == same["changed"] == [] and same["same"] == 4
    # A baseline of some folders compares with those folders only.
    part = _baseline(client, h, t["suite"].id, name="Auth", section_ids=[t["auth"].id])
    scoped = client.get(
        f"{API}/testcases/baselines/compare", params={"a": part["id"], "b": "current"}, headers=h
    ).json()
    assert scoped["added"] == [] and scoped["same"] == 3
    bad = client.get(
        f"{API}/testcases/baselines/compare", params={"a": a["id"], "b": "x"}, headers=h
    )
    assert bad.status_code == 422


def test_compare_pages_and_cuts_values(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """§15.16: the lists come a page at a time with their totals; a long value is cut."""
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    t = _tree(db_session, bp)
    h = as_user(world.manager)
    a = _baseline(client, h, t["suite"].id, name="1.0")
    for c in (t["login"], t["logout"], t["google"]):
        c.expected = "x" * 5_000
    t["card"].status = "obsolete"
    extra = [
        TestCase(suite_id=t["suite"].id, section_id=t["pay"].id, title=f"N{i}", position=10 + i)
        for i in range(3)
    ]
    db_session.add_all(extra)
    db_session.flush()

    def page(**params: Any) -> Any:
        got = client.get(
            f"{API}/testcases/baselines/compare",
            params={"a": a["id"], "b": "current", **params},
            headers=h,
        )
        assert got.status_code == 200, got.text
        return got.json()

    first = page(limit=2)
    assert (first["total_changed"], first["total_added"], first["total_removed"]) == (3, 3, 1)
    assert first["offset"] == 0 and first["limit"] == 2 and first["same"] == 0
    assert [c["key"] for c in first["changed"]] == [f"C{t['login'].id}", f"C{t['logout'].id}"]
    assert [c["title"] for c in first["added"]] == ["N0", "N1"]
    assert len(first["removed"]) == 1
    [field] = first["changed"][0]["fields"]
    assert field["field"] == "expected"
    assert field["after"] == "x" * 2_000 + "..."
    second = page(limit=2, offset=2)
    assert [c["key"] for c in second["changed"]] == [f"C{t['google'].id}"]
    assert [c["title"] for c in second["added"]] == ["N2"] and second["removed"] == []
    assert page()["limit"] == 200
    too_big = client.get(
        f"{API}/testcases/baselines/compare",
        params={"a": a["id"], "b": "current", "limit": 501},
        headers=h,
    )
    assert too_big.status_code == 422


def test_run_from_baseline_keeps_baseline_content(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """30a G9 AC: editing a case after the baseline leaves the run from the baseline as it was."""
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    t = _tree(db_session, bp)
    h = as_user(world.manager)
    base = _baseline(client, h, t["suite"].id, name="1.0")

    got = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "baseline_id": base["id"], "selection": {}},
        headers=h,
    )
    assert got.status_code == 201, got.text
    run = got.json()
    assert run["source"] == "baseline" and run["baseline_id"] == base["id"]
    assert run["baseline_name"] == "1.0" and run["counts"]["total"] == 4
    assert run["name"].startswith("1.0 ")

    # Edit the case (and its shared content) after the baseline; delete another one.
    t["login"].title = "Đăng nhập mới"
    t["login"].steps_list = [{"content": "Khác", "expected": "Khác"}]
    t["logout"].expected = "Sửa sau baseline"
    db_session.flush()
    tests = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"]
    login = next(x for x in tests if x["case_id"] == t["login"].id)
    assert login["title"] == "Đăng nhập" and login["param_row"] is None
    detail = client.get(f"{API}/testcases/tests/{login['id']}", headers=h).json()
    assert detail["content"]["title"] == "Đăng nhập"
    assert detail["content"]["steps_list"] == [{"content": "Mở trang", "expected": "Có form"}]
    logout = next(x for x in tests if x["case_id"] == t["logout"].id)
    shown = client.get(f"{API}/testcases/tests/{logout['id']}", headers=h).json()
    assert shown["content"]["expected"] == "Về trang đăng nhập"

    # Deleting a case keeps the baseline's content (not the edited one) in the open run.
    assert client.delete(f"{API}/testcases/{t['logout'].id}", headers=h).status_code in (200, 204)
    gone = client.get(f"{API}/testcases/tests/{logout['id']}", headers=h).json()
    assert gone["content"]["expected"] == "Về trang đăng nhập"

    # Deleting the baseline: the run keeps its content.
    assert client.delete(f"{API}/testcases/baselines/{base['id']}", headers=h).status_code == 204
    db_session.expire_all()
    stored = db_session.get(TestRun, run["id"])
    assert stored is not None and stored.baseline_id is None and stored.source == "baseline"
    again = client.get(f"{API}/testcases/tests/{login['id']}", headers=h).json()
    assert again["content"]["title"] == "Đăng nhập"
    # Adding cases to it is refused now (its baseline is gone).
    refused = client.patch(
        f"{API}/testcases/runs/{run['id']}", json={"add": {"all": True}}, headers=h
    )
    assert refused.status_code == 409


def test_run_from_baseline_selection(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    t = _tree(db_session, bp)
    h = as_user(world.manager)
    base = _baseline(client, h, t["suite"].id)

    def run_of(selection: dict[str, Any]) -> Any:
        return client.post(
            f"{API}/testcases/runs",
            json={"business_project_id": bp, "baseline_id": base["id"], "selection": selection},
            headers=h,
        )

    def titles(run: Any) -> list[str]:
        items = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"]
        return [x["title"] for x in items]

    folder = run_of({"section_ids": [t["auth"].id]})
    assert folder.status_code == 201, folder.text
    assert titles(folder.json()) == ["Đăng nhập", "Đăng xuất", "Google"]
    assert titles(run_of({"case_ids": [t["card"].id]}).json()) == ["Thẻ"]
    assert titles(run_of({"all": True, "priority": ["high", "low"]}).json()) == [
        "Đăng nhập",
        "Google",
    ]
    assert titles(run_of({"all": True, "tags": ["smoke"]}).json()) == ["Đăng nhập"]
    assert titles(run_of({"all": True, "type": ["security"]}).json()) == ["Thẻ"]
    assert run_of({"case_ids": [t["old"].id]}).status_code == 422  # obsolete: not in it
    assert run_of({"suite_id": t["suite"].id + 1}).status_code == 422

    # Adding to a baseline run takes from the baseline, once per case.
    first = run_of({"case_ids": [t["card"].id]}).json()
    t["google"].title = "Google mới"
    db_session.flush()
    patched = client.patch(
        f"{API}/testcases/runs/{first['id']}",
        json={"add": {"section_ids": [t["social"].id, t["pay"].id]}},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    assert titles(first) == ["Thẻ", "Google"]


def test_baseline_of_5000_cases_is_quick(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    suite = TestSuite(business_project_id=bp, name="Big", position=1)
    db_session.add(suite)
    db_session.flush()
    section = TestSection(suite_id=suite.id, name="All", position=1)
    db_session.add(section)
    db_session.flush()
    db_session.execute(
        insert(TestCase),
        [
            {
                "suite_id": suite.id,
                "section_id": section.id,
                "title": f"Case {i}",
                "position": i,
                "template": "steps",
                "steps_list": [{"content": f"Bước {i}", "expected": "OK"}],
                "params": {"columns": ["phone"], "rows": [["0901"], ["0902"]]},
            }
            for i in range(5_000)
        ],
    )
    h = as_user(world.manager)
    started = time.perf_counter()
    made = _baseline(client, h, suite.id, name="Big 1.0")
    spent = time.perf_counter() - started
    assert made["case_count"] == 5_000 and len(made["cases"]) == 5_000
    assert spent < 5, f"baseline of 5 000 cases took {spent:.2f}s"
    stored = db_session.scalar(
        select(TestBaselineCase.snapshot)
        .where(TestBaselineCase.baseline_id == made["id"])
        .order_by(TestBaselineCase.position)
        .limit(1)
    )
    assert stored is not None
    assert stored["params"] == {"columns": ["phone"], "rows": [["0901"], ["0902"]]}


def test_run_from_shared_baseline_keeps_its_own_milestone(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """A baseline of a suite shared from another dự án: the run takes none of its milestone
    (another dự án's); once the suite is no longer shared, adding cases from it is a 404."""
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    h = as_user(world.manager)
    home = BusinessProject(name="Home")
    db_session.add(home)
    db_session.flush()
    suite = TestSuite(business_project_id=home.id, name="Shared", position=1)
    db_session.add(suite)
    db_session.flush()
    db_session.add_all(
        [
            TestCase(suite_id=suite.id, title="A", position=1),
            TestCase(suite_id=suite.id, title="B", position=2),
            TestSuiteLink(suite_id=suite.id, business_project_id=bp),
        ]
    )
    milestone = TestMilestone(business_project_id=home.id, name="Home 1.0")
    db_session.add(milestone)
    db_session.flush()
    base = bl.create(
        db_session,
        suite,
        name="Home 1.0",
        description=None,
        milestone_id=milestone.id,
        section_ids=None,
        user_id=None,
    )
    db_session.flush()
    first = db_session.scalar(
        select(TestCase.id).where(TestCase.suite_id == suite.id, TestCase.title == "A")
    )
    got = client.post(
        f"{API}/testcases/runs",
        json={
            "business_project_id": bp,
            "baseline_id": base.id,
            "selection": {"case_ids": [first]},
        },
        headers=h,
    )
    assert got.status_code == 201, got.text
    run = got.json()
    assert run["milestone_id"] is None and run["counts"]["total"] == 1

    db_session.query(TestSuiteLink).filter(TestSuiteLink.suite_id == suite.id).delete()
    db_session.flush()
    more = client.patch(f"{API}/testcases/runs/{run['id']}", json={"add": {}}, headers=h)
    assert more.status_code == 404, more.text
