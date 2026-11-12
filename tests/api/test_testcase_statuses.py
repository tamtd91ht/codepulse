"""§15 G6 (docs/specs/30): result statuses (built-in + Skipped + custom), a result from its
steps, "chỉ người được giao", elapsed vs estimate and the run's remaining estimate, files
attached to results."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import security
from app.api.routers import test_status
from app.models import (
    BusinessProject,
    PermissionSet,
    PermissionSetItem,
    TestAttachment,
    TestCase,
    TestProjectSettings,
    TestSuite,
    UserPermissionSet,
)
from app.ratelimit import WindowCounter
from app.testcases import statuses as tst
from tests.api.conftest import World, grant

API = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
STEPS3 = [
    {"content": "Mở trang", "expected": "Có form"},
    {"content": "Nhập", "expected": "Nhận"},
    {"content": "Lưu", "expected": "Đã lưu"},
]


@pytest.fixture(autouse=True)
def _local_rate() -> Iterator[None]:
    saved = security.token_rate
    security.token_rate = WindowCounter(None)  # in memory: no Redis in tests
    yield
    security.token_rate = saved


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _give(db: Session, user_id: int, items: dict[str, str]) -> None:
    row = PermissionSet(name=f"g6 {user_id} {'-'.join(sorted(items))}", builtin=False)
    db.add(row)
    db.flush()
    db.add_all(
        [PermissionSetItem(set_id=row.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user_id, set_id=row.id)]
    )
    db.flush()


def _cases(db: Session, bp: int) -> dict[str, TestCase]:
    suite = TestSuite(business_project_id=bp, name="G6", position=1)
    db.add(suite)
    db.flush()
    out = {
        "steps": TestCase(
            suite_id=suite.id,
            title="Ba bước",
            position=1,
            template="steps",
            steps_list=STEPS3,
            estimate_s=300,
        ),
        "short": TestCase(suite_id=suite.id, title="Ngắn", position=2, estimate_s=120),
        "free": TestCase(suite_id=suite.id, title="Không ước tính", position=3),
    }
    db.add_all(out.values())
    db.flush()
    return out


def _run(client: TestClient, h: dict[str, str], bp: int, case_ids: list[int]) -> Any:
    got = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"case_ids": case_ids}},
        headers=h,
    )
    assert got.status_code == 201, got.text
    return got.json()


def _tests(client: TestClient, h: dict[str, str], run_id: int) -> dict[str, Any]:
    items = client.get(f"{API}/testcases/runs/{run_id}/tests", headers=h).json()["items"]
    return {t["title"]: t for t in items}


def test_steps_decide_the_result(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    c = _cases(db_session, bp)
    admin = as_user(world.admin)
    run = _run(client, admin, bp, [c["steps"].id])
    test = _tests(client, admin, run["id"])["Ba bước"]
    url = f"{API}/testcases/tests/{test['id']}/results"

    # AC1: failed at step 3, no status picked -> the result is Failed, at step 3.
    steps = [{"status": "passed"}, {"status": "passed"}, {"status": "failed", "actual": "Lỗi"}]
    got = client.post(url, json={"step_results": steps}, headers=admin)
    assert got.status_code == 201, got.text
    detail = got.json()
    assert detail["status"] == "failed" and detail["failed_step"] == 3
    assert detail["results"][0]["failed_step"] == 3
    assert _tests(client, admin, run["id"])["Ba bước"]["failed_step"] == 3
    # "Passed" picked with a failed step is still Failed.
    forced = client.post(url, json={"status": "passed", "step_results": steps}, headers=admin)
    assert forced.json()["status"] == "failed"
    # Blocked at step 2 (nothing failed).
    blocked = [{"status": "passed"}, {"status": "blocked"}, {"status": "untested"}]
    got = client.post(url, json={"step_results": blocked}, headers=admin).json()
    assert got["status"] == "blocked" and got["results"][0]["failed_step"] == 2
    # Every step passed, nothing picked: Passed.
    got = client.post(url, json={"step_results": [{"status": "passed"}] * 3}, headers=admin)
    assert got.json()["status"] == "passed" and got.json()["failed_step"] is None
    # Another non-passing status picked is kept.
    got = client.post(url, json={"status": "retest", "step_results": steps}, headers=admin)
    assert got.json()["status"] == "retest" and got.json()["results"][0]["failed_step"] == 3


def test_from_steps_unit() -> None:
    assert tst.from_steps(None, []) == (None, None)
    assert tst.from_steps(None, [{"status": "skipped"}]) == ("skipped", None)
    assert tst.from_steps("blocked", [{"status": "failed"}]) == ("blocked", 1)
    assert tst.slug("Cần xác nhận lại") == "can_xac_nhan_lai"
    assert tst.slug("2 lần") == "s_2_lan"


def test_assigned_only(client: TestClient, world: World, as_user: Any, db_session: Session) -> None:
    bp = _bp(world)
    c = _cases(db_session, bp)
    grant(db_session, world.manager, bp, "manage")
    _give(db_session, world.binh.id, {"tests.run": "all"})
    admin, an, binh = as_user(world.admin), as_user(world.an), as_user(world.binh)
    manager = as_user(world.manager)
    run = _run(client, admin, bp, [c["short"].id, c["free"].id])
    tests = _tests(client, admin, run["id"])
    short, free = tests["Ngắn"], tests["Không ước tính"]
    url = f"{API}/testcases/tests/{short['id']}/results"
    assigned = client.post(url, json={"assigned_to": world.an.id}, headers=admin)
    assert assigned.status_code == 201, assigned.text

    # Off: a tester (tests.run) records any test.
    assert client.post(url, json={"status": "passed"}, headers=binh).status_code == 201
    assert client.get(f"{API}/testcases/runs/{run['id']}", headers=binh).json()["can_record_all"]

    db_session.add(TestProjectSettings(business_project_id=bp, assigned_only=True))
    db_session.flush()
    # On: the tester who is not the assignee may not; the assignee and a manager may.
    assert client.post(url, json={"status": "failed"}, headers=binh).status_code == 403
    seen = client.get(f"{API}/testcases/tests/{short['id']}", headers=binh).json()
    assert seen["can_record"] is False
    out = client.get(f"{API}/testcases/runs/{run['id']}", headers=binh).json()
    assert out["assigned_only"] is True and out["can_record_all"] is False
    assert client.post(url, json={"status": "failed"}, headers=an).status_code == 201
    assert client.post(url, json={"status": "passed"}, headers=manager).status_code == 201
    bulk = client.post(
        f"{API}/testcases/runs/{run['id']}/results",
        json={"test_ids": [short["id"], free["id"]], "status": "passed"},
        headers=binh,
    )
    assert bulk.status_code == 403
    assert client.get(f"{API}/testcases/runs/{run['id']}", headers=manager).json()["can_record_all"]


def test_custom_statuses(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    c = _cases(db_session, bp)
    admin, an = as_user(world.admin), as_user(world.an)
    url = f"{API}/testcases/statuses"

    listed = client.get(url, params={"business_project_id": bp}, headers=an).json()
    keys = [s["key"] for s in listed["items"]]
    assert keys == ["passed", "failed", "blocked", "retest", "skipped", "untested"]
    assert listed["can_edit"] is False
    skipped = next(s for s in listed["items"] if s["key"] == "skipped")
    assert skipped["done"] is True and skipped["color"] == "muted"

    body = {"business_project_id": bp, "name": "Cần xác nhận", "color": "info", "done": False}
    assert client.post(url, json=body, headers=an).status_code == 403
    made = client.post(url, json=body, headers=admin)
    assert made.status_code == 201, made.text
    custom = made.json()["items"][-2]
    assert custom["key"] == "can_xac_nhan" and custom["builtin"] is False
    assert client.post(url, json=body, headers=admin).status_code == 409  # same key
    reserved = {**body, "name": "x", "key": "passed"}
    assert client.post(url, json=reserved, headers=admin).status_code == 409
    bad = {**body, "name": "y", "color": "#ff0000"}
    assert client.post(url, json=bad, headers=admin).status_code == 422
    spare = client.post(url, json={**body, "name": "Tạm"}, headers=admin).json()["items"][-2]

    run = _run(client, admin, bp, [c["short"].id, c["free"].id, c["steps"].id])
    tests = _tests(client, admin, run["id"])
    short, free = tests["Ngắn"], tests["Không ước tính"]
    rec = f"{API}/testcases/tests/{short['id']}/results"
    assert client.post(rec, json={"status": "nope"}, headers=admin).status_code == 422
    assert client.post(rec, json={"status": "total"}, headers=admin).status_code == 422
    got = client.post(rec, json={"status": "can_xac_nhan"}, headers=admin)
    assert got.status_code == 201 and got.json()["status"] == "can_xac_nhan"
    bulk = client.post(
        f"{API}/testcases/runs/{run['id']}/results",
        json={"test_ids": [free["id"]], "status": "skipped"},
        headers=admin,
    )
    assert bulk.status_code == 200, bulk.text
    counts = client.get(f"{API}/testcases/runs/{run['id']}", headers=admin).json()["counts"]
    assert counts["can_xac_nhan"] == 1 and counts["skipped"] == 1
    assert counts["untested"] == 1 and counts["total"] == 3 and counts["retest"] == 0
    bp_counts = client.get(f"{API}/testcases/runs", params={"business_project_id": bp}, headers=an)
    assert bp_counts.json()[0]["counts"]["can_xac_nhan"] == 1
    xlsx = client.get(f"{API}/testcases/runs/{run['id']}/export", headers=admin)
    assert xlsx.status_code == 200

    # Used: not deleted, archived; archived: no new results; unused: deleted.
    one = f"{url}/{custom['id']}"
    assert client.delete(one, headers=admin).status_code == 409
    assert client.patch(one, json={"archived": True}, headers=an).status_code == 403
    archived = client.patch(one, json={"archived": True, "name": "Chờ xác nhận"}, headers=admin)
    assert archived.status_code == 200
    item = next(s for s in archived.json()["items"] if s["key"] == "can_xac_nhan")
    assert item["archived"] is True and item["name"] == "Chờ xác nhận"
    assert client.post(rec, json={"status": "can_xac_nhan"}, headers=admin).status_code == 422
    assert client.delete(f"{url}/{spare['id']}", headers=admin).status_code == 204


def test_times_and_remaining_estimate(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    c = _cases(db_session, bp)
    admin = as_user(world.admin)
    run = _run(client, admin, bp, [c["steps"].id, c["short"].id, c["free"].id])
    assert run["remaining_estimate_s"] == 420 and run["elapsed_total_s"] == 0
    tests = _tests(client, admin, run["id"])
    # Skipped counts as done: the 120 s estimate is no longer left.
    client.post(
        f"{API}/testcases/tests/{tests['Ngắn']['id']}/results",
        json={"status": "skipped", "elapsed": "1m"},
        headers=admin,
    )
    out = client.get(f"{API}/testcases/runs/{run['id']}", headers=admin).json()
    # 300 s of the steps case + the average elapsed (60 s) for the case without an estimate.
    assert out["elapsed_total_s"] == 60 and out["remaining_estimate_s"] == 360
    assert _tests(client, admin, run["id"])["Ngắn"]["elapsed_s"] == 60
    # Retest still needs running.
    client.post(
        f"{API}/testcases/tests/{tests['Ba bước']['id']}/results",
        json={"status": "retest", "elapsed": "2m"},
        headers=admin,
    )
    out = client.get(f"{API}/testcases/runs/{run['id']}", headers=admin).json()
    assert out["elapsed_total_s"] == 180 and out["remaining_estimate_s"] == 300 + 90


def test_attachments(client: TestClient, world: World, as_user: Any, db_session: Session) -> None:
    bp = _bp(world)
    c = _cases(db_session, bp)
    admin, an, outsider = as_user(world.admin), as_user(world.an), as_user(world.outsider)
    url = f"{API}/testcases/attachments"

    def up(h: dict[str, str], data: bytes, name: str, project: int = bp) -> Any:
        return client.post(
            url,
            params={"business_project_id": project, "filename": name},
            content=data,
            headers={**h, "Content-Type": "application/octet-stream"},
        )

    first = up(admin, PNG, "ảnh chụp.png")
    assert first.status_code == 201, first.text
    img = first.json()
    assert img["mime"] == "image/png" and img["name"] == "ảnh chụp.png"
    again = up(admin, PNG, "khác.png").json()
    assert again["id"] == img["id"]  # the same bytes are kept once
    log = up(admin, b"line 1\nline 2\n", "run.log").json()
    assert log["mime"] == "text/plain"
    pdf = up(admin, b"%PDF-1.7\n...", "report.pdf")
    assert pdf.status_code == 201 and pdf.json()["mime"] == "application/pdf"
    assert up(admin, b"MZ\x90\x00", "virus.exe").status_code == 422
    assert up(admin, b"<html>", "fake.pdf").status_code == 422
    assert up(admin, b"\x00\x01", "bin.txt").status_code == 422
    assert up(admin, b"", "empty.txt").status_code == 422
    # A viewer who records nothing in the dự án may not upload.
    assert up(an, PNG + b"x", "a.png").status_code == 403
    assert up(outsider, PNG + b"y", "a.png").status_code == 403
    # A file of another dự án is not served to someone who does not see that dự án.
    other = BusinessProject(name="Khác G6")
    db_session.add(other)
    db_session.flush()
    hidden = TestAttachment(
        business_project_id=other.id, name="x.png", mime="image/png", size=1, sha256="0", data=b"x"
    )
    db_session.add(hidden)
    db_session.flush()
    assert client.get(f"{url}/{hidden.id}", headers=an).status_code == 404

    served = client.get(f"{url}/{img['id']}", headers=an)
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in served.headers["content-security-policy"]
    assert served.headers["content-disposition"].startswith("inline")
    text = client.get(f"{url}/{log['id']}", headers=an)
    assert text.headers["content-disposition"].startswith("attachment")

    run = _run(client, admin, bp, [c["short"].id])
    test = _tests(client, admin, run["id"])["Ngắn"]
    rec = f"{API}/testcases/tests/{test['id']}/results"
    got = client.post(rec, json={"attachment_ids": [img["id"], log["id"]]}, headers=admin)
    assert got.status_code == 201, got.text
    files = got.json()["results"][0]["attachments"]
    assert [f["id"] for f in files] == [img["id"], log["id"]]
    assert files[0] == {"id": img["id"], "name": "ảnh chụp.png", "mime": "image/png", "size": 48}
    assert client.post(rec, json={"attachment_ids": [10**9]}, headers=admin).status_code == 422


def _upload(client: TestClient, h: dict[str, str], bp: int, data: bytes, name: str) -> Any:
    return client.post(
        f"{API}/testcases/attachments",
        params={"business_project_id": bp, "filename": name},
        content=data,
        headers={**h, "Content-Type": "application/octet-stream"},
    )


def test_needs_retest_is_reserved(client: TestClient, world: World, as_user: Any) -> None:
    assert "needs_retest" in tst.RESERVED
    got = client.post(
        f"{API}/testcases/statuses",
        json={"business_project_id": _bp(world), "name": "Needs retest", "color": "warn"},
        headers=as_user(world.admin),
    )
    assert got.status_code == 409, got.text


def test_attachment_quota_per_project(
    client: TestClient, world: World, as_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    monkeypatch.setattr(test_status, "ATTACH_PROJECT_MAX_BYTES", len(PNG) + 10)
    assert _upload(client, admin, bp, PNG, "a.png").status_code == 201
    # The same bytes again are reused, so they do not count twice.
    assert _upload(client, admin, bp, PNG, "b.png").status_code == 201
    full = _upload(client, admin, bp, b"line 1\nline 2\n", "run.log")
    assert full.status_code == 413 and "GB" in full.json()["detail"]


def test_attachment_upload_rate_per_person(
    client: TestClient, world: World, as_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    monkeypatch.setattr(test_status, "ATTACH_RATE_PER_MINUTE", 2)
    assert _upload(client, admin, bp, b"one\n", "1.log").status_code == 201
    assert _upload(client, admin, bp, b"two\n", "2.log").status_code == 201
    third = _upload(client, admin, bp, b"three\n", "3.log")
    assert third.status_code == 429 and third.headers["retry-after"]


def test_unused_attachments_are_dropped_after_a_day(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    c = _cases(db_session, bp)
    admin = as_user(world.admin)
    used = _upload(client, admin, bp, b"used\n", "used.log").json()["id"]
    unused = _upload(client, admin, bp, b"unused\n", "unused.log").json()["id"]
    fresh = _upload(client, admin, bp, b"fresh\n", "fresh.log").json()["id"]
    run = _run(client, admin, bp, [c["short"].id])
    test = _tests(client, admin, run["id"])["Ngắn"]
    got = client.post(
        f"{API}/testcases/tests/{test['id']}/results",
        json={"status": "passed", "attachment_ids": [used]},
        headers=admin,
    )
    assert got.status_code == 201, got.text
    old = datetime.now(UTC) - timedelta(days=2)
    for row in db_session.scalars(
        select(TestAttachment).where(TestAttachment.id.in_([used, unused]))
    ):
        row.created_at = old
    db_session.flush()

    assert tst.gc_attachments(db_session) == 1
    left = set(
        db_session.scalars(
            select(TestAttachment.id).where(TestAttachment.business_project_id == bp)
        )
    )
    assert used in left and fresh in left and unused not in left
    assert tst.gc_attachments(db_session) == 0
