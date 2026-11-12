"""Jira bugs from test results (docs/specs/30a G3, docs/specs/30 §15): create a bug from a failed
result (steps, expected vs actual, link back, the key in the result's defects), link an existing
issue, bug states (synced first, then Jira, cached), "Cần retest" when every bug is closed, the
JIRA_WRITE_ENABLED switch and the dự án's test settings. Jira is mocked with respx."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import security
from app.api.routers import test_bugs
from app.config import Settings
from app.jira.settings import save_connection
from app.models import (
    AuditLog,
    Issue,
    JiraProject,
    PermissionSet,
    PermissionSetItem,
    TestCase,
    TestProjectSettings,
    TestResult,
    TestRun,
    TestRunTest,
    TestSuite,
    UserPermissionSet,
)
from app.ratelimit import WindowCounter
from app.testcases import bugs
from tests.api.conftest import World, grant

pytestmark = pytest.mark.integration
API = "/api/v1"
JIRA = "https://jira.example.vn"
REST = f"{JIRA}/rest/api/2"
TOKEN = "pat-" + "fake-" + "secret"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        session_secret=SecretStr("s" * 48),
        admin_emails=["admin@x.vn"],
        gitlab_oauth_url="https://gitlab.example.com",
        gitlab_oauth_client_id="cid",
        gitlab_oauth_client_secret=SecretStr("csecret"),
        app_base_url="https://codepulse.example.com",
        dashboard_url="https://dash.example.com",
        jira_base_url=JIRA,
        jira_token=SecretStr(TOKEN),
        jira_write_enabled=True,
    )


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    bugs.clear_cache()
    saved = security.token_rate
    security.token_rate = WindowCounter(None)  # in memory: no Redis in tests
    yield
    security.token_rate = saved
    bugs.clear_cache()


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _setup(client: TestClient, db: Session, h: dict[str, str], bp: int) -> dict[str, Any]:
    """A steps case in an open run, recorded Failed at step 2."""
    suite = TestSuite(business_project_id=bp, name="CSKH", position=1)
    db.add(suite)
    db.flush()
    case = TestCase(
        suite_id=suite.id,
        title="Thêm khách hàng",
        template="steps",
        preconditions="Đã đăng nhập",
        steps_list=[
            {"content": "Mở form", "expected": "Có form"},
            {"content": "Bấm Lưu", "expected": "Lưu thành công"},
        ],
        position=1,
    )
    db.add(case)
    db.flush()
    run = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "name": "Sprint 9", "selection": {"case_ids": [case.id]}},
        headers=h,
    ).json()
    [test] = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"]
    got = client.post(
        f"{API}/testcases/tests/{test['id']}/results",
        json={
            "status": "failed",
            "comment": "Báo lỗi 500",
            "version": "1.4.2",
            "step_results": [
                {"status": "passed", "actual": "Có form"},
                {"status": "failed", "actual": "Lỗi 500"},
            ],
        },
        headers=h,
    )
    assert got.status_code == 201, got.text
    return {"case": case, "run": run, "test": test}


def _project(client: TestClient, h: dict[str, str], bp: int, **body: Any) -> Any:
    got = client.put(
        f"{API}/testcases/project-settings", json={"business_project_id": bp, **body}, headers=h
    )
    assert got.status_code == 200, got.text
    return got.json()


def _give(db: Session, user_id: int, items: dict[str, str]) -> None:
    row = PermissionSet(name=f"bugs {user_id} {'-'.join(sorted(items))}", builtin=False)
    db.add(row)
    db.flush()
    db.add_all(
        [PermissionSetItem(set_id=row.id, permission=k, scope=v) for k, v in items.items()]
        + [UserPermissionSet(user_id=user_id, set_id=row.id)]
    )
    db.flush()


def _defects(db: Session, test_id: int, keys: list[str]) -> None:
    result = db.scalar(select(TestResult).where(TestResult.test_id == test_id))
    assert result is not None
    result.defects = keys
    db.flush()


def _status(client: TestClient, h: dict[str, str], bp: int, keys: str) -> Any:
    got = client.get(
        f"{API}/testcases/bugs/status",
        params={"keys": keys, "business_project_id": bp},
        headers=h,
    )
    assert got.status_code == 200, got.text
    return got.json()


def _issue(db: Session, key: str, state: str, bps: list[int] | None = None) -> None:
    project = db.scalar(select(JiraProject).where(JiraProject.key == key.split("-")[0]))
    if project is None:
        project = JiraProject(key=key.split("-")[0], name="X")
        db.add(project)
        db.flush()
    number = int(key.split("-")[1])
    db.add(
        Issue(
            id=10**15 + project.id * 10_000 + number,
            source="jira",
            jira_project_id=project.id,
            iid=number,
            key=key,
            title=f"Bug {key}",
            state=state,
            web_url=f"{JIRA}/browse/{key}",
            business_project_ids=bps,
        )
    )
    db.flush()


def test_project_settings(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    grant(db_session, world.manager, bp, "manage")
    viewer, manager = as_user(world.an), as_user(world.manager)
    params = {"business_project_id": bp}
    got = client.get(f"{API}/testcases/project-settings", params=params, headers=viewer).json()
    assert got["jira_project_key"] is None and got["jira_issue_type"] == "Bug"
    assert got["assigned_only"] is False and got["can_edit"] is False
    assert got["jira_write_enabled"] is True and got["jira_configured"] is True
    body = {"business_project_id": bp, "assigned_only": True}
    assert (
        client.put(f"{API}/testcases/project-settings", json=body, headers=viewer).status_code
        == 403
    )
    bad = client.put(
        f"{API}/testcases/project-settings",
        json={"business_project_id": bp, "jira_project_key": "not a key"},
        headers=manager,
    )
    assert bad.status_code == 422
    out = _project(
        client,
        manager,
        bp,
        assigned_only=True,
        require_approval=True,
        jira_project_key="omx",
        jira_issue_type="Lỗi",
        jira_components=["Backend", "Backend", " "],
        jira_labels=["from codepulse", "qa"],
    )
    assert out["jira_project_key"] == "OMX" and out["assigned_only"] is True
    assert out["require_approval"] is True and out["jira_issue_type"] == "Lỗi"
    assert out["jira_components"] == ["Backend"]
    assert out["jira_labels"] == ["from-codepulse", "qa"]
    row = db_session.get(TestProjectSettings, bp)
    assert row is not None and row.jira_project_key == "OMX"
    # A partial PUT keeps the rest.
    again = _project(client, manager, bp, assigned_only=False)
    assert again["jira_project_key"] == "OMX" and again["assigned_only"] is False


def test_create_bug_from_failed_result(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp)
    _project(client, admin, bp, jira_project_key="OMX", jira_components=["CRM"], jira_labels=["qa"])
    test_id = s["test"]["id"]
    with respx.mock(assert_all_called=False) as jira:
        route = jira.post(f"{REST}/issue").mock(
            return_value=httpx.Response(201, json={"id": "100", "key": "OMX-77"})
        )
        got = client.post(f"{API}/testcases/tests/{test_id}/bug", json={}, headers=admin)
    assert got.status_code == 201, got.text
    out = got.json()
    assert out["key"] == "OMX-77" and out["web_url"] == f"{JIRA}/browse/OMX-77"
    sent = json.loads(route.calls.last.request.content)["fields"]
    assert sent["project"] == {"key": "OMX"} and sent["issuetype"] == {"name": "Bug"}
    case_key = f"C{s['case'].id}"
    assert sent["summary"] == f"[{case_key}] Thêm khách hàng {bugs.EN_DASH} Failed"
    assert sent["components"] == [{"name": "CRM"}] and sent["labels"] == ["qa"]
    text = sent["description"]
    assert "|2|Bấm Lưu|Lưu thành công|*failed*|Lỗi 500|" in text  # expected vs actual
    assert "Đã đăng nhập" in text and "Báo lỗi 500" in text and "1.4.2" in text
    assert f"https://dash.example.com/testcases?project={bp}&tab=runs&run={s['run']['id']}" in text
    # The key is stored on the failed result and shown on the test.
    assert out["test"]["defects"] == ["OMX-77"]
    assert out["test"]["results"][0]["defects"] == ["OMX-77"]
    result = db_session.scalar(select(TestResult).where(TestResult.test_id == test_id))
    assert result is not None and result.defects == ["OMX-77"]
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "testresult.bug"))
    # The new bug is open (known without a Jira call).
    assert _status(client, admin, bp, "OMX-77")[0]["state"] == "opened"
    # One bug per failed result: a second click is refused, Jira is not called again.
    with respx.mock(assert_all_called=False) as jira:
        again_route = jira.post(f"{REST}/issue").mock(
            return_value=httpx.Response(201, json={"id": "101", "key": "OMX-78"})
        )
        again = client.post(f"{API}/testcases/tests/{test_id}/bug", json={}, headers=admin)
    assert again.status_code == 409 and "OMX-77" in again.json()["detail"]
    assert again_route.call_count == 0
    # A result chosen by id must be Failed.
    passed = client.post(
        f"{API}/testcases/tests/{test_id}/results", json={"status": "passed"}, headers=admin
    )
    assert passed.status_code == 201, passed.text
    passed_id = db_session.scalar(
        select(TestResult.id).where(TestResult.test_id == test_id, TestResult.status == "passed")
    )
    with respx.mock(assert_all_called=False) as jira:
        jira.post(f"{REST}/issue").mock(
            return_value=httpx.Response(201, json={"id": "102", "key": "OMX-79"})
        )
        refused = client.post(
            f"{API}/testcases/tests/{test_id}/bug", json={"result_id": passed_id}, headers=admin
        )
    assert refused.status_code == 422 and "Failed" in refused.json()["detail"]


def test_create_bug_refusals(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin, viewer, binh = as_user(world.admin), as_user(world.an), as_user(world.binh)
    s = _setup(client, db_session, admin, bp)
    url = f"{API}/testcases/tests/{s['test']['id']}/bug"
    # No Jira project in the settings: 422.
    got = client.post(url, json={}, headers=admin)
    assert got.status_code == 422 and "project Jira" in got.json()["detail"]
    _project(client, admin, bp, jira_project_key="OMX")
    # A viewer may not.
    assert client.post(url, json={}, headers=viewer).status_code == 403
    # Jira refuses (400): 502 with Jira's message.
    with respx.mock(assert_all_called=False) as jira:
        jira.post(f"{REST}/issue").mock(
            return_value=httpx.Response(
                400, json={"errorMessages": [], "errors": {"components": "Component CRM không có"}}
            )
        )
        got = client.post(url, json={"components": ["CRM"]}, headers=admin)
    assert got.status_code == 502 and "Component CRM không có" in got.json()["detail"]
    assert (
        db_session.scalar(select(TestResult.defects).where(TestResult.test_id == s["test"]["id"]))
        == []
    )
    # The assignee may create one.
    client.post(
        f"{API}/testcases/tests/{s['test']['id']}/results",
        json={"assigned_to": world.binh.id},
        headers=admin,
    )
    with respx.mock(assert_all_called=False) as jira:
        jira.post(f"{REST}/issue").mock(
            return_value=httpx.Response(201, json={"id": "1", "key": "OMX-5"})
        )
        assert client.post(url, json={}, headers=binh).status_code == 201
    # Writes switched off on the web: 409.
    save_connection(db_session, {"write_enabled": False}, None)
    got = client.post(url, json={}, headers=admin)
    assert got.status_code == 409 and "JIRA_WRITE_ENABLED" in got.json()["detail"]
    settings_out = client.get(
        f"{API}/testcases/project-settings", params={"business_project_id": bp}, headers=admin
    ).json()
    assert settings_out["jira_write_enabled"] is False
    # Admin > Jira > Kết nối switches it back on.
    put = client.put(f"{API}/dpi/jira/connection", json={"write_enabled": True}, headers=admin)
    assert put.status_code == 200, put.text
    assert put.json()["connection"]["write_enabled"] is True
    assert put.json()["connection"]["sources"]["write_enabled"] == "web"


def test_link_existing_bug(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp)
    url = f"{API}/testcases/tests/{s['test']['id']}/bug/link"
    assert client.post(url, json={"key": "not-a-key"}, headers=admin).status_code == 422
    with respx.mock(assert_all_called=False) as jira:
        jira.get(f"{REST}/issue/OMX-404").mock(return_value=httpx.Response(404, json={}))
        jira.get(f"{REST}/issue/OMX-9").mock(
            return_value=httpx.Response(
                200,
                json={
                    "key": "OMX-9",
                    "fields": {
                        "summary": "Lỗi lưu",
                        "status": {"name": "Open", "statusCategory": {"key": "new"}},
                    },
                },
            )
        )
        missing = client.post(url, json={"key": "OMX-404"}, headers=admin)
        assert missing.status_code == 422 and "OMX-404" in missing.json()["detail"]
        got = client.post(url, json={"key": "omx-9"}, headers=admin)
    assert got.status_code == 200, got.text
    assert got.json()["verified"] is True and got.json()["test"]["defects"] == ["OMX-9"]
    # Linking again does not duplicate.
    _issue(db_session, "OMX-9", "opened")
    again = client.post(url, json={"key": "OMX-9"}, headers=admin)
    assert again.json()["test"]["defects"] == ["OMX-9"]


def test_retest_when_bugs_closed(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp)
    test_id, run_id = s["test"]["id"], s["run"]["id"]
    _issue(db_session, "OMX-1", "opened")
    _issue(db_session, "OMX-2", "closed")
    client.post(f"{API}/testcases/tests/{test_id}/bug/link", json={"key": "OMX-1"}, headers=admin)
    client.post(f"{API}/testcases/tests/{test_id}/bug/link", json={"key": "OMX-2"}, headers=admin)

    def flag() -> bool:
        [t] = client.get(f"{API}/testcases/runs/{run_id}/tests", headers=admin).json()["items"]
        detail = client.get(f"{API}/testcases/tests/{test_id}", headers=admin).json()
        assert t["needs_retest"] == detail["needs_retest"]
        return bool(t["needs_retest"])

    assert flag() is False  # OMX-1 still open
    issue = db_session.scalar(select(Issue).where(Issue.key == "OMX-1"))
    assert issue is not None
    issue.state = "closed"
    db_session.flush()
    assert flag() is True
    only = client.get(
        f"{API}/testcases/runs/{run_id}/tests", params={"status": "needs_retest"}, headers=admin
    ).json()
    assert [t["id"] for t in only["items"]] == [test_id]
    # Closed run: no hint.
    client.post(f"{API}/testcases/runs/{run_id}/close", headers=admin)
    assert flag() is False


def test_bug_status_cache(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    admin, viewer = as_user(world.admin), as_user(world.an)
    s = _setup(client, db_session, admin, bp)
    _defects(db_session, s["test"]["id"], ["ABC-3", "ABC-4"])
    _issue(db_session, "OMX-1", "closed", [bp])
    _issue(db_session, "OMX-2", "opened", [bp + 1000])  # another dự án's issue
    with respx.mock(assert_all_called=False) as jira:
        live = jira.get(f"{REST}/issue/ABC-3").mock(
            return_value=httpx.Response(
                200,
                json={
                    "key": "ABC-3",
                    "fields": {
                        "summary": "Treo",
                        "status": {"name": "Done", "statusCategory": {"key": "done"}},
                    },
                },
            )
        )
        gone = jira.get(f"{REST}/issue/ABC-4").mock(return_value=httpx.Response(404, json={}))
        other = jira.get(f"{REST}/issue/OMX-2").mock(return_value=httpx.Response(404, json={}))
        stray = jira.get(f"{REST}/issue/XYZ-5").mock(return_value=httpx.Response(404, json={}))
        keys = "OMX-1,OMX-2,abc-3,ABC-4,XYZ-5,bad key"
        first = _status(client, viewer, bp, keys)
        second = _status(client, viewer, bp, keys)
    by_key = {b["key"]: b for b in first}
    assert list(by_key) == ["OMX-1", "OMX-2", "ABC-3", "ABC-4", "XYZ-5"]
    assert by_key["OMX-1"]["state"] == "closed" and by_key["OMX-1"]["source"] == "sync"
    assert by_key["ABC-3"]["state"] == "closed" and by_key["ABC-3"]["status"] == "Done"
    assert by_key["ABC-3"]["web_url"] == f"{JIRA}/browse/ABC-3"
    assert by_key["ABC-4"]["state"] == "unknown"
    # Neither another dự án's synced issue nor a key outside the dự án's defects is read.
    assert by_key["OMX-2"]["state"] == "unknown" and by_key["OMX-2"]["title"] is None
    assert by_key["XYZ-5"]["state"] == "unknown"
    assert other.call_count == 0 and stray.call_count == 0
    assert second == first
    assert live.call_count == 1 and gone.call_count == 1  # cached
    # The dự án is required, and must be one the caller sees.
    url = f"{API}/testcases/bugs/status"
    assert client.get(url, params={"keys": "OMX-1"}, headers=viewer).status_code == 422
    missing = client.get(
        url, params={"keys": "OMX-1", "business_project_id": 999_999}, headers=viewer
    )
    assert missing.status_code == 404


def test_bug_status_live_reads_capped(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bp = _bp(world)
    admin = as_user(world.admin)
    s = _setup(client, db_session, admin, bp)
    keys = [f"CAP-{i}" for i in range(1, 13)]
    _defects(db_session, s["test"]["id"], keys)
    with respx.mock(assert_all_called=False) as jira:
        route = jira.get(url__regex=rf"{REST}/issue/CAP-\d+").mock(
            return_value=httpx.Response(404, json={})
        )
        out = _status(client, admin, bp, ",".join(keys))
        assert len(out) == 12 and route.call_count == test_bugs.LIVE_READS_MAX
        # Past the per-person rate, the rest stay unknown without calling Jira.
        monkeypatch.setattr(test_bugs, "LIVE_CALLS_PER_MINUTE", 1)
        again = _status(client, admin, bp, ",".join(keys))
        assert route.call_count == test_bugs.LIVE_READS_MAX
    assert {b["state"] for b in again} == {"unknown"}


def test_assigned_only_bugs(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """With "chỉ người được giao" on, a tester (tests.run) who is not the assignee may neither
    create nor link a bug; the assignee may."""
    bp = _bp(world)
    _give(db_session, world.binh.id, {"tests.run": "all"})
    admin, an, binh = as_user(world.admin), as_user(world.an), as_user(world.binh)
    s = _setup(client, db_session, admin, bp)
    _project(client, admin, bp, jira_project_key="OMX", assigned_only=True)
    test_id = s["test"]["id"]
    _issue(db_session, "OMX-3", "opened", [bp])
    link = f"{API}/testcases/tests/{test_id}/bug/link"
    with respx.mock(assert_all_called=False) as jira:
        made = jira.post(f"{REST}/issue").mock(
            return_value=httpx.Response(201, json={"id": "1", "key": "OMX-5"})
        )
        create = client.post(f"{API}/testcases/tests/{test_id}/bug", json={}, headers=binh)
    assert create.status_code == 403 and made.call_count == 0
    assert client.post(link, json={"key": "OMX-3"}, headers=binh).status_code == 403
    client.post(
        f"{API}/testcases/tests/{test_id}/results",
        json={"assigned_to": world.an.id},
        headers=admin,
    )
    got = client.post(link, json={"key": "OMX-3"}, headers=an)
    assert got.status_code == 200, got.text


def test_description_escapes_wiki_markup() -> None:
    run = TestRun(id=7, name="Sprint [x|http://evil]", business_project_id=3)
    test = TestRunTest(id=9, run_id=7)
    result = TestResult(
        id=11,
        test_id=9,
        status="failed",
        comment=r"*bold* _it_ -del- ^sup^ ~sub~ +ins+ ?? # | \[x|http://e\]",
        log="line\n{noformat}\n[evil|http://x]\n{NOFORMAT}\nend",
        attachments=[{"id": 5, "name": "shot_1.png"}, {"url": "https://x|y", "name": "ci"}],
        step_results=[],
        ci={},
    )
    text = bugs.description(
        {"template": "text", "steps": "h1. Big\n  bq. quote\n!img.png!"},
        result,
        run,
        test,
        dashboard_url="https://dash.example.com/",
        extra="h2. Head\n[click|http://evil]{color:red}x{color}",
    )
    lines = text.splitlines()
    assert not any(line.lstrip().startswith(("h1.", "h2.", "bq.")) for line in lines)
    assert r"h2\. Head" in text and r"h1\. Big" in text and r"bq\. quote" in text
    assert "[click|http://evil]" not in text
    assert r"\[click\|http://evil\]" in text
    assert r"\{color:red\}" in text and r"\!img.png\!" in text
    assert r"Sprint \[x\|http://evil\]" in text
    assert r"\*bold\* \_it\_ \-del\- \^sup\^ \~sub\~ \+ins\+ \?\? \# \|" in text
    # A typed backslash never pairs with an escape (Jira reads a double backslash as a break).
    assert "\\\\" not in text
    # The log cannot close its {noformat} block early.
    assert text.lower().count("{noformat}") == 2
    assert "{ noformat}" in text and "{ NOFORMAT}" in text
    # A CodePulse upload links to the API; a URL that would break the link is plain text.
    assert r"* [shot\_1.png|https://dash.example.com/api/v1/testcases/attachments/5]" in text
    assert "[ci|" not in text
