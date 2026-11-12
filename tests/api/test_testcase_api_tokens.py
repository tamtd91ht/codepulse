"""The public testcase API (docs/specs/30 §15, G11): personal access tokens with testcase scopes,
bound to one dự án or not, refused outside /api/v1/testcases, rate limited; its OpenAPI docs."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import security
from app.models import ApiToken, BusinessProject, TestCase, TestSuite, User
from app.ratelimit import WindowCounter
from tests.api.conftest import World, grant

pytestmark = pytest.mark.integration

API = "/api/v1"


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


def _other(db: Session, *users: User) -> int:
    other = BusinessProject(name="Khác")
    db.add(other)
    db.flush()
    for user in users:
        grant(db, user, other.id, "manage")
    return other.id


def _suite(db: Session, bp: int, name: str = "Bộ") -> TestSuite:
    suite = TestSuite(business_project_id=bp, name=name, position=1)
    db.add(suite)
    db.flush()
    case = TestCase(suite_id=suite.id, title="Đăng nhập", position=1)
    db.add(case)
    db.flush()
    return suite


def _token(
    client: TestClient, session_headers: dict[str, str], scopes: list[str], bp: int | None = None
) -> dict[str, Any]:
    made = client.post(
        f"{API}/me/tokens",
        headers=session_headers,
        json={"name": "ci", "scopes": scopes, "business_project_id": bp},
    )
    assert made.status_code == 201, made.text
    body: dict[str, Any] = made.json()
    return body


def _bearer(token: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['token']}"}


def test_scopes_are_granted_as_asked_and_listed(
    client: TestClient, world: World, as_user: Any
) -> None:
    h = as_user(world.admin)
    api_only = _token(client, h, ["results:write"], _bp(world))
    # A token for the testcase API only does not get findings:read (stays out of /mcp).
    assert api_only["scopes"] == ["results:write"]
    assert api_only["business_project_id"] == _bp(world)
    assert api_only["business_project_name"] == "Demo"
    mixed = _token(client, h, ["findings:close_request", "testcases:read"])
    assert mixed["scopes"] == ["findings:read", "findings:close_request", "testcases:read"]
    listed = client.get(f"{API}/me/tokens", headers=h).json()
    assert {t["id"]: t["business_project_id"] for t in listed} == {
        api_only["id"]: _bp(world),
        mixed["id"]: None,
    }
    bad = client.post(
        f"{API}/me/tokens", headers=h, json={"name": "x", "scopes": ["testcases:delete"]}
    )
    assert bad.status_code == 422


def test_binding_needs_a_dự_án_the_creator_sees(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    hidden = BusinessProject(name="Ẩn")
    db_session.add(hidden)
    db_session.flush()
    got = client.post(
        f"{API}/me/tokens",
        headers=as_user(world.an),
        json={"name": "ci", "scopes": ["testcases:read"], "business_project_id": hidden.id},
    )
    assert got.status_code == 404
    missing = client.post(
        f"{API}/me/tokens",
        headers=as_user(world.an),
        json={"name": "ci", "scopes": ["testcases:read"], "business_project_id": 999_999},
    )
    assert missing.status_code == 404


def test_results_write_only_cannot_create_cases(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """AC1: a token with results:write only gets 403 on POST /testcases."""
    suite = _suite(db_session, _bp(world))
    token = _token(client, as_user(world.admin), ["results:write"])
    got = client.post(
        f"{API}/testcases", headers=_bearer(token), json={"suite_id": suite.id, "title": "Mới"}
    )
    assert got.status_code == 403
    assert "testcases:write" in got.json()["detail"]
    # Nor read.
    listed = client.get(
        f"{API}/testcases", headers=_bearer(token), params={"business_project_id": _bp(world)}
    )
    assert listed.status_code == 403


def test_read_scope_reads_and_write_scope_writes(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    suite = _suite(db_session, bp)
    h = as_user(world.admin)
    reader = _bearer(_token(client, h, ["testcases:read"]))
    writer = _bearer(_token(client, h, ["testcases:write"]))
    params = {"business_project_id": bp}
    assert client.get(f"{API}/testcases", headers=reader, params=params).status_code == 200
    body = {"suite_id": suite.id, "title": "Từ API"}
    assert client.post(f"{API}/testcases", headers=reader, json=body).status_code == 403
    made = client.post(f"{API}/testcases", headers=writer, json=body)
    assert made.status_code == 201, made.text
    # testcases:write reads too.
    assert client.get(f"{API}/testcases", headers=writer, params=params).status_code == 200


def test_bound_token_sees_its_dự_án_only(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """AC2: a token bound to dự án A neither reads nor writes dự án B, even an admin's."""
    a = _bp(world)
    b = _other(db_session, world.admin)
    suite_a, suite_b = _suite(db_session, a, "A"), _suite(db_session, b, "B")
    token = _bearer(_token(client, as_user(world.admin), ["testcases:write"], a))
    ok = client.get(f"{API}/testcases/suites", headers=token, params={"business_project_id": a})
    assert ok.status_code == 200 and [s["id"] for s in ok.json()] == [suite_a.id]
    no = client.get(f"{API}/testcases/suites", headers=token, params={"business_project_id": b})
    assert no.status_code in (403, 404)
    write_b = client.post(
        f"{API}/testcases", headers=token, json={"suite_id": suite_b.id, "title": "x"}
    )
    assert write_b.status_code in (403, 404)
    write_a = client.post(
        f"{API}/testcases", headers=token, json={"suite_id": suite_a.id, "title": "x"}
    )
    assert write_a.status_code == 201
    projects = client.get(f"{API}/testcases/projects", headers=token).json()
    assert [p["id"] for p in projects] == [a]
    # The same person's session still sees both.
    both = client.get(f"{API}/testcases/projects", headers=as_user(world.admin)).json()
    assert {a, b} <= {p["id"] for p in both}


def test_revoked_expired_and_wrong_route_are_401(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """AC3: revoked -> 401; a token never works outside /api/v1/testcases."""
    h = as_user(world.admin)
    token = _token(client, h, ["testcases:read", "findings:close_request"])
    params = {"business_project_id": _bp(world)}
    assert client.get(f"{API}/testcases", headers=_bearer(token), params=params).status_code == 200
    for path in ("/me/tokens", "/webhooks", "/reviews", "/docs"):
        got = client.get(f"{API}{path}", headers=_bearer(token))
        assert got.status_code == 401, path
    assert "testcases" in client.get(f"{API}/me/tokens", headers=_bearer(token)).json()["detail"]
    assert client.post(f"{API}/me/tokens/{token['id']}/revoke", headers=h).status_code == 200
    got = client.get(f"{API}/testcases", headers=_bearer(token), params=params)
    assert got.status_code == 401 and "thu hồi" in got.json()["detail"]
    unknown = {"Authorization": "Bearer cpat_" + "x" * 43}
    assert client.get(f"{API}/testcases", headers=unknown, params=params).status_code == 401


def test_last_used_is_touched(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    token = _token(client, as_user(world.admin), ["testcases:read"])
    client.get(
        f"{API}/testcases", headers=_bearer(token), params={"business_project_id": _bp(world)}
    )
    row = db_session.get(ApiToken, token["id"])
    assert row is not None
    db_session.refresh(row)
    assert row.last_used_at is not None


def test_rate_limit_per_token(
    client: TestClient, world: World, as_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(security, "TOKEN_RATE_PER_MINUTE", 2)
    h = as_user(world.admin)
    first, second = _token(client, h, ["testcases:read"]), _token(client, h, ["testcases:read"])
    params = {"business_project_id": _bp(world)}
    for _ in range(2):
        assert client.get(f"{API}/testcases", headers=_bearer(first), params=params).is_success
    over = client.get(f"{API}/testcases", headers=_bearer(first), params=params)
    assert over.status_code == 429 and int(over.headers["Retry-After"]) >= 1
    # Per token: another one still goes; the session is not limited.
    assert client.get(f"{API}/testcases", headers=_bearer(second), params=params).is_success
    assert client.get(f"{API}/testcases", headers=h, params=params).is_success


def test_result_paths_take_results_write() -> None:
    need = security.needed_scopes
    assert need("GET", "/api/v1/testcases/runs/3") == ("testcases:read", "testcases:write")
    for path in (
        "/api/v1/testcases/tests/5/results",
        "/api/v1/testcases/runs/5/results",
        "/api/v1/testcases/runs/import-results",
        "/api/v1/testcases/attachments",
    ):
        assert need("POST", path) == ("results:write", "testcases:write")
    # Reading an attachment back is still a read call.
    assert "results:write" not in need("GET", "/api/v1/testcases/attachments/4")
    assert need("POST", "/api/v1/testcases/runs") == ("testcases:write",)
    # POST only: another verb on a result path is a testcases:write call.
    assert need("PATCH", "/api/v1/testcases/runs/5/results") == ("testcases:write",)
    assert need("DELETE", "/api/v1/testcases/tests/5/results") == ("testcases:write",)
    assert need("POST", "/api/v1/testcases/runs/5/close") == ("testcases:write",)
    # The CLI's --wait reads its import (and the dự án's list) with results:write.
    for path in ("/api/v1/testcases/ci-imports/9", "/api/v1/testcases/ci-imports"):
        assert "results:write" in need("GET", path)
    assert "results:write" not in need("GET", "/api/v1/testcases/runs/3")


def test_cli_scopes_upload_and_wait(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """The documented CLI token (results:write only): upload into a run, poll the import with
    --wait, list the dự án's imports; --close needs testcases:write."""
    from app.testcases import ci

    bp = _bp(world)
    suite = _suite(db_session, bp)
    h = as_user(world.admin)
    run = client.post(
        f"{API}/testcases/runs",
        headers=h,
        json={"business_project_id": bp, "selection": {"suite_id": suite.id}},
    ).json()
    case_id = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"][0][
        "case_id"
    ]
    token = _bearer(_token(client, h, ["results:write"], bp))
    report = f'<testsuites><testcase classname="x" name="C{case_id} ok"/></testsuites>'.encode()

    def upload(**form: str) -> Any:
        return client.post(
            f"{API}/testcases/runs/import-results",
            headers=token,
            data={"business_project_id": str(bp), "run_id": str(run["id"]), **form},
            files={"file": ("junit.xml", report, "application/xml")},
        )

    closing = upload(close_run="true")
    assert closing.status_code == 403 and "testcases:write" in closing.json()["detail"]
    got = upload()
    assert got.status_code == 202, got.text
    import_id = got.json()["import_id"]
    ci.process(db_session, import_id)
    polled = client.get(f"{API}/testcases/ci-imports/{import_id}", headers=token)
    assert polled.status_code == 200 and polled.json()["status"] == "done"
    listed = client.get(
        f"{API}/testcases/ci-imports", headers=token, params={"business_project_id": bp}
    )
    assert listed.status_code == 200 and [r["id"] for r in listed.json()] == [import_id]
    # Still no other read.
    assert client.get(f"{API}/testcases/runs/{run['id']}", headers=token).status_code == 403


def test_results_write_token_records_a_result(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = _bp(world)
    suite = _suite(db_session, bp)
    h = as_user(world.admin)
    run = client.post(
        f"{API}/testcases/runs",
        headers=h,
        json={"business_project_id": bp, "selection": {"suite_id": suite.id}},
    )
    assert run.status_code == 201, run.text
    tests = client.get(f"{API}/testcases/runs/{run.json()['id']}/tests", headers=h).json()
    test_id = tests["items"][0]["id"]
    ci = _bearer(_token(client, h, ["results:write"], bp))
    got = client.post(
        f"{API}/testcases/tests/{test_id}/results", headers=ci, json={"status": "passed"}
    )
    assert got.status_code == 201, got.text
    close = client.post(f"{API}/testcases/runs/{run.json()['id']}/close", headers=ci)
    assert close.status_code == 403


def test_results_write_token_uploads_an_attachment(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """A CI token (results:write only) uploads a file and attaches it to the result it records."""
    bp = _bp(world)
    suite = _suite(db_session, bp)
    h = as_user(world.admin)
    run = client.post(
        f"{API}/testcases/runs",
        headers=h,
        json={"business_project_id": bp, "selection": {"suite_id": suite.id}},
    ).json()
    test_id = client.get(f"{API}/testcases/runs/{run['id']}/tests", headers=h).json()["items"][0][
        "id"
    ]
    ci = _bearer(_token(client, h, ["results:write"], bp))
    up = client.post(
        f"{API}/testcases/attachments",
        params={"business_project_id": bp, "filename": "ci.log"},
        content=b"step 1 ok\nstep 2 failed\n",
        headers={**ci, "Content-Type": "application/octet-stream"},
    )
    assert up.status_code == 201, up.text
    got = client.post(
        f"{API}/testcases/tests/{test_id}/results",
        headers=ci,
        json={"status": "failed", "attachment_ids": [up.json()["id"]]},
    )
    assert got.status_code == 201, got.text
    assert got.json()["results"][0]["attachments"][0]["id"] == up.json()["id"]
    # Serving the file is a read: not for a results:write-only token.
    served = client.get(f"{API}/testcases/attachments/{up.json()['id']}", headers=ci)
    assert served.status_code == 403


def test_openapi_docs_need_sign_in_and_document_the_bearer(
    client: TestClient, world: World, as_user: Any
) -> None:
    url = f"{API}/testcases/api-docs/openapi.json"
    assert client.get(url).status_code == 401
    token = _token(client, as_user(world.admin), ["testcases:read"])
    spec = client.get(url, headers=_bearer(token))
    assert spec.status_code == 200
    body = spec.json()
    assert body["components"]["securitySchemes"]["cpatToken"]["scheme"] == "bearer"
    assert body["paths"] and all(p.startswith("/api/v1/testcases") for p in body["paths"])
    assert "/api/v1/testcases/runs/{run_id}/close" in body["paths"]
    ui = client.get(f"{API}/testcases/api-docs", headers=as_user(world.an))
    assert ui.status_code == 200 and "swagger" in ui.text.lower()
    results_only = _token(client, as_user(world.admin), ["results:write"])
    assert client.get(url, headers=_bearer(results_only)).status_code == 403
    # The default FastAPI schema is unchanged.
    assert client.get("/openapi.json").status_code == 200
