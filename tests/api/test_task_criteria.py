"""docs/specs/31 §20.3 (round 2, stage 2.3 QA): a bug from one failed step, the covered task's
assignee as the bug's default assignee, "Cần chạy lại test" in Việc của tôi, acceptance criteria
with stable ids that testcases link to."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TaskTestLink, TestRun, TestRunTest
from app.testcases import bugs
from app.testcases import runs as rs
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable
from tests.api.test_testcase_bugs import _setup

pytestmark = pytest.mark.integration


def _done(space: dict[str, Any]) -> int:
    return next(s["id"] for s in space["statuses"] if s["category"] == "done")


def _fail_again(client: TestClient, h: dict[str, str], test_id: int) -> None:
    got = client.post(
        f"{API}/testcases/tests/{test_id}/results",
        json={
            "status": "failed",
            "step_results": [{"status": "failed", "actual": "Không mở"}, {"status": "untested"}],
        },
        headers=h,
    )
    assert got.status_code == 201, got.text


def test_bug_from_a_failed_step(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bugs.clear_cache()
    enable(client, world, as_user)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp_of(world))  # step 2 ("Bấm Lưu") failed
    url = f"{API}/testcases/tests/{s['test']['id']}/bug"
    # Step 1 passed: no bug from it; a step that does not exist neither.
    assert client.post(url, headers=admin, json={"step": 0}).status_code == 422
    assert client.post(url, headers=admin, json={"step": 7}).status_code == 422
    got = client.post(url, headers=admin, json={"step": 1})
    assert got.status_code == 201, got.text
    task = client.get(f"{API}/tasks/{got.json()['key']}", headers=admin).json()
    assert task["title"] == f"[C{s['case'].id}] Bấm Lưu {bugs.EN_DASH} Failed"
    assert "Bấm Lưu **(failed)**" in task["description"] and "Lỗi 500" in task["description"]
    # Still one bug per failed result.
    assert client.post(url, headers=admin, json={"step": 1}).status_code == 409
    # The steps after the failed one are left out of the description.
    test = db_session.get(TestRunTest, s["test"]["id"])
    run = db_session.get(TestRun, s["run"]["id"])
    assert test is not None and run is not None
    shown = rs.views(db_session, run, [test])[test.id]
    shown = {**shown, "steps_list": [*shown["steps_list"], {"content": "Đóng form"}]}
    text = bugs.description_md(shown, None, run, test, dashboard_url="https://x", upto=1)
    assert "Bấm Lưu" in text and "Đóng form" not in text
    assert "Đóng form" in bugs.description_md(shown, None, run, test, dashboard_url="https://x")


def test_bug_goes_to_the_covered_task_assignee(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bugs.clear_cache()
    enable(client, world, as_user)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp_of(world))
    test_id = s["test"]["id"]
    defaults = f"{API}/testcases/tests/{test_id}/bug/defaults"
    assert client.get(defaults, headers=admin).json() == {"assignee_id": None, "task_key": None}
    story = create(client, admin, world, type="story", assignee_id=world.an.id)
    res = client.post(
        f"{API}/tasks/{story['key']}/tests", headers=admin, json={"case": f"C{s['case'].id}"}
    )
    assert res.status_code == 201, res.text
    assert client.get(defaults, headers=admin).json() == {
        "assignee_id": world.an.id,
        "task_key": story["key"],
    }
    url = f"{API}/testcases/tests/{test_id}/bug"
    made = client.post(url, headers=admin, json={})
    assert made.status_code == 201, made.text
    bug = client.get(f"{API}/tasks/{made.json()['key']}", headers=admin).json()
    assert bug["assignee"]["id"] == world.an.id
    # An explicit null keeps the bug unassigned.
    _fail_again(client, admin, test_id)
    made = client.post(url, headers=admin, json={"assignee_id": None})
    assert made.status_code == 201, made.text
    bug = client.get(f"{API}/tasks/{made.json()['key']}", headers=admin).json()
    assert bug["assignee"] is None


def test_retest_shows_in_my_work(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bugs.clear_cache()
    space = enable(client, world, as_user)
    admin, an = as_user(world.admin), as_user(world.an)
    s = _setup(client, db_session, admin, bp_of(world))  # recorded Failed by the admin
    made = client.post(
        f"{API}/testcases/tests/{s['test']['id']}/bug",
        headers=admin,
        json={"assignee_id": world.an.id},
    ).json()

    def retests(h: dict[str, str]) -> list[dict[str, Any]]:
        work = client.get(f"{API}/tasks/my-work", headers=h).json()
        return [a for a in work["attention"] if a["kind"] == "retest"]

    assert retests(admin) == []  # the bug is still open
    res = client.post(
        f"{API}/tasks/{made['key']}/transition", headers=an, json={"status_id": _done(space)}
    )
    assert res.status_code == 200, res.text
    bugs.clear_cache()
    [item] = retests(admin)
    test_id, run_id = s["test"]["id"], s["run"]["id"]
    assert item["text"] == f"Cần chạy lại test T{test_id} (bug {made['key']} đã xong)"
    assert item["link"] == (
        f"/testcases?project={bp_of(world)}&tab=runs&run={run_id}&test={test_id}"
    )
    # Neither recorded by An nor assigned to An.
    assert retests(an) == []


def test_acceptance_criteria_with_linked_testcases(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    enable(client, world, as_user)
    admin, binh = as_user(world.admin), as_user(world.binh)
    s = _setup(client, db_session, admin, bp_of(world))  # its test is Failed
    story = create(client, admin, world, type="story", assignee_id=world.an.id)
    path = f"{API}/tasks/{story['key']}/criteria"
    assert client.get(path, headers=admin).json() == []
    for text in ("Lưu được khách hàng", "Báo lỗi khi trùng SĐT", "Có nhật ký"):
        res = client.post(path, headers=admin, json={"text": text})
        assert res.status_code == 201, res.text
    crit = res.json()
    assert [c["text"] for c in crit] == [
        "Lưu được khách hàng",
        "Báo lỗi khi trùng SĐT",
        "Có nhật ký",
    ]
    assert [c["status"] for c in crit] == ["none", "none", "none"]
    first, second, third = (c["id"] for c in crit)
    # Only who may edit the task changes its criteria.
    assert client.post(path, headers=binh, json={"text": "x"}).status_code == 403
    assert client.patch(f"{path}/{first}", headers=binh, json={"done": True}).status_code == 403
    assert client.post(path, headers=admin, json={"text": " "}).status_code == 422
    # Edit, tick, move the last one to the top.
    res = client.patch(f"{path}/{second}", headers=admin, json={"text": "Báo lỗi trùng SĐT"})
    assert res.status_code == 200, res.text
    client.patch(f"{path}/{first}", headers=admin, json={"done": True})
    crit = client.patch(f"{path}/{third}", headers=admin, json={"position": 0}).json()
    assert [c["id"] for c in crit] == [third, first, second]
    assert [c["position"] for c in crit] == [0, 1, 2]
    assert crit[1]["done"] is True and crit[2]["text"] == "Báo lỗi trùng SĐT"
    # A testcase linked to a criterion; a criterion of another task is refused.
    tests = f"{API}/tasks/{story['key']}/tests"
    other = create(client, admin, world, type="story")
    other_crit = client.post(
        f"{API}/tasks/{other['key']}/criteria", headers=admin, json={"text": "Khác"}
    ).json()[0]["id"]
    bad = client.post(
        tests, headers=admin, json={"case": f"C{s['case'].id}", "criterion_id": other_crit}
    )
    assert bad.status_code == 422
    res = client.post(
        tests, headers=admin, json={"case": f"C{s['case'].id}", "criterion_id": first}
    )
    assert res.status_code == 201, res.text
    cov = res.json()
    by_id = {c["id"]: c for c in cov["criteria"]}
    assert by_id[first]["status"] == "failed"
    assert [c["key"] for c in by_id[first]["cases"]] == [f"C{s['case'].id}"]
    assert by_id[second]["status"] == "none" and by_id[second]["cases"] == []
    assert cov["cases"][0]["criterion_id"] == first
    # Moving the link to another criterion, then deleting that criterion: the case still covers.
    client.post(tests, headers=admin, json={"case": f"C{s['case'].id}", "criterion_id": second})
    crit = client.get(path, headers=admin).json()
    assert [c["status"] for c in crit] == ["none", "none", "failed"]
    assert client.delete(f"{path}/{second}", headers=admin).status_code == 204
    crit = client.get(path, headers=admin).json()
    assert [c["id"] for c in crit] == [third, first] and [c["position"] for c in crit] == [0, 1]
    link = db_session.scalars(select(TaskTestLink).where(TaskTestLink.kind == "covers")).one()
    db_session.refresh(link)
    assert link.criterion_id is None
    cov = client.get(tests, headers=admin).json()
    assert cov["total"] == 1 and all(not c["cases"] for c in cov["criteria"])
    assert client.delete(f"{path}/999999", headers=admin).status_code == 404
