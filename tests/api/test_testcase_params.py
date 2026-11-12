"""Parameterised cases, the case side (docs/specs/30 §15.15, §15.16, 30a G13): the table on a
case, its limits, variables without a column refused, a Scenario Outline's Examples as the table,
the content a test of one row shows."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import TestCase
from app.testcases import content, params
from tests.api.conftest import World

API = "/api/v1"
TABLE = {"columns": ["phone", "otp"], "rows": [["0901", "123"], ["0902", "456"]]}


def _bp(world: World) -> int:
    bp = world.project.business_project_id
    assert bp is not None
    return bp


def _suite(client: TestClient, h: dict[str, str], bp: int) -> Any:
    got = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": "OTP"}, headers=h
    )
    assert got.status_code == 201, got.text
    return got.json()


def _post(client: TestClient, h: dict[str, str], suite: int, **values: Any) -> Any:
    return client.post(
        f"{API}/testcases", json={"suite_id": suite, "title": "Đăng nhập OTP", **values}, headers=h
    )


def test_table_on_a_case_and_its_variables(client: TestClient, world: World, as_user: Any) -> None:
    admin = as_user(world.admin)
    suite = _suite(client, admin, _bp(world))
    got = _post(
        client,
        admin,
        suite["id"],
        title="Đăng nhập {{phone}}",
        steps="1. Nhập {{ phone }}\n2. Nhập {{otp}}",
        expected="Vào",
        params=TABLE,
    )
    assert got.status_code == 201, got.text
    case = got.json()
    assert case["params"] == TABLE
    missing = _post(client, admin, suite["id"], steps="Nhập {{email}}", params=TABLE)
    assert missing.status_code == 422 and "email" in missing.json()["detail"]
    # Steps template: the steps' texts count too.
    steps = _post(
        client,
        admin,
        suite["id"],
        template="steps",
        steps_list=[{"content": "Nhập {{pin}}", "expected": "OK"}],
        params=TABLE,
    )
    assert steps.status_code == 422 and "pin" in steps.json()["detail"]
    # No table: {{x}} is plain text.
    plain = _post(client, admin, suite["id"], steps="Nhập {{email}}")
    assert plain.status_code == 201 and plain.json()["params"] == {}

    # An edit dropping a column still used, or adding an unknown variable: refused.
    path = f"{API}/testcases/{case['id']}"
    drop = client.patch(
        path,
        json={"revision": case["revision"], "params": {"columns": ["phone"], "rows": [["1"]]}},
        headers=admin,
    )
    assert drop.status_code == 422 and "otp" in drop.json()["detail"]
    text = client.patch(
        path, json={"revision": case["revision"], "expected": "Thấy {{name}}"}, headers=admin
    )
    assert text.status_code == 422
    cleared = client.patch(path, json={"revision": case["revision"], "params": None}, headers=admin)
    assert cleared.status_code == 200 and cleared.json()["params"] == {}
    assert cleared.json()["revisions"][0]["snapshot"]["params"] == TABLE
    restored = client.post(f"{path}/restore/1", headers=admin)
    assert restored.status_code == 200 and restored.json()["params"] == TABLE
    found = client.get(
        f"{API}/testcases", params={"business_project_id": _bp(world), "q": "0902"}, headers=admin
    ).json()
    assert [c["id"] for c in found["items"]] == [case["id"]]


@pytest.mark.parametrize(
    "table",
    [
        {"columns": [f"c{i}" for i in range(21)], "rows": []},
        {"columns": ["a"], "rows": [["x"]] * 201},
        {"columns": ["a"], "rows": [["x" * 501]]},
        {"columns": ["1a"], "rows": [["x"]]},
        {"columns": ["a b"], "rows": [["x"]]},
        {"columns": ["a", "a"], "rows": [["x", "y"]]},
        {"columns": ["a", "b"], "rows": [["x"]]},
        {"columns": [], "rows": [["x"]]},
        {"rows": "x"},
    ],
)
def test_table_limits(
    client: TestClient, world: World, as_user: Any, table: dict[str, Any]
) -> None:
    admin = as_user(world.admin)
    suite = _suite(client, admin, _bp(world))
    got = _post(client, admin, suite["id"], params=table)
    assert got.status_code == 422, got.text
    assert "tham số" in got.json()["detail"]


def test_outline_examples_are_the_table(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    admin = as_user(world.admin)
    suite = _suite(client, admin, _bp(world))
    outline = (
        "Scenario Outline: Đăng nhập\n"
        "  When nhập <phone> và {{otp}}\n"
        "  Then vào trang chủ\n"
        "  Examples:\n"
        "    | phone | otp |\n"
        "    | 0901  | 1   |\n"
        "    | 0902  | 2   |"
    )
    got = _post(
        client,
        admin,
        suite["id"],
        template="bdd",
        bdd=outline,
        params={"columns": ["other"], "rows": [["x"]]},  # Examples win
    )
    assert got.status_code == 201, got.text
    case = got.json()
    assert case["params"] == {"columns": ["phone", "otp"], "rows": [["0901", "1"], ["0902", "2"]]}
    # Changing the Examples changes the table.
    edited = client.patch(
        f"{API}/testcases/{case['id']}",
        json={"revision": case["revision"], "bdd": outline.replace("0902", "0903")},
        headers=admin,
    )
    assert edited.status_code == 200
    assert edited.json()["params"]["rows"][1] == ["0903", "2"]
    # A plain scenario with a table of its own: <x> needs a column too.
    plain = _post(
        client,
        admin,
        suite["id"],
        template="bdd",
        bdd="Scenario: a\n  When nhập <email>\n  Then ok",
        params={"columns": ["phone"], "rows": [["1"]]},
    )
    assert plain.status_code == 422 and "email" in plain.json()["detail"]

    # What a test of row 2 shows: the steps of the scenario with row 2's values.
    row = db_session.get(TestCase, case["id"])
    assert row is not None
    shown = content.case_content(row, {}, {})
    assert shown["params"]["columns"] == ["phone", "otp"] and shown["bdd"]
    second = params.rows(row.params)[1]
    test = params.render(shown, second)
    assert test["steps_list"] == [
        {"content": "When nhập 0903 và 2", "expected": "Then vào trang chủ"}
    ]
    assert params.label(second) == "[phone=0903, otp=2]"
