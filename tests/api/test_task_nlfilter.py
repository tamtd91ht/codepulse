"""docs/specs/31 §19.8: a filter typed as a sentence → the params of GET /tasks."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.tasks.nlfilter import Vocab, parse
from tests.api.conftest import World
from tests.api.test_tasks import API, bp_of, create, enable, status_id, ungrant

TODAY = date(2026, 11, 18)  # a Wednesday


def vocab() -> Vocab:
    return Vocab(
        statuses=[(1, "Cần làm"), (2, "Đang làm"), (3, "Đang review"), (4, "Xong")],
        priorities=[
            ("highest", "Cao nhất", "highest"),
            ("high", "Cao", "high"),
            ("medium", "Trung bình", "medium"),
            ("low", "Thấp", "low"),
        ],
        types=["epic", "story", "task", "bug", "subtask"],
        people=[
            (10, "Nguyễn Văn An", "an.nv@x.vn"),
            (11, "Trần Văn Bình", "binh@x.vn"),
        ],
        sprints=[(7, "Sprint 12"), (6, "Sprint 11")],
        versions=[(3, "v2.1")],
        today=TODAY,
    )


def query(text: str) -> dict[str, str]:
    return parse(text, vocab())[1]


def test_the_example_sentence() -> None:
    chips, q = parse("bug cao chưa giao sprint này", vocab())
    assert q == {"type": "bug", "priority": "high", "assignee": "none", "sprint": "active"}
    assert [c.label for c in chips] == [
        "Loại: Bug",
        "Ưu tiên: Cao",
        "Người làm: chưa giao",
        "Sprint: đang chạy",
    ]


def test_longest_phrase_and_space_names_win() -> None:
    assert query("cao nhất")["priority"] == "highest"
    assert query("ưu tiên cao")["priority"] == "high"
    assert query("khẩn cấp")["priority"] == "highest"
    # A status the dự án has beats the built-in group word; unknown status → the group.
    assert query("đang làm") == {"status": "2"}
    assert query("in progress") == {"category": "doing"}
    assert query("đã xong") == {"category": "done"}
    assert query("Xong") == {"status": "4"}
    # Accents are optional.
    assert query("dang review") == {"status": "3"}


def test_people_dates_labels_names() -> None:
    assert query("việc của tôi quá hạn") == {"assignee": "me", "due": "overdue"}
    assert query("@an") == {"assignee": "10"}
    assert query("@binh đến hạn tuần này") == {"assignee": "11", "due": "week"}
    # A word shared by both names is not a sure match: it stays text.
    assert query("@van") == {"q": "@van"}
    assert query("tuần này") == {"updated_from": "2026-11-16"}
    assert query("hôm nay") == {"updated_from": "2026-11-18"}
    assert query("nhãn backend #api") == {"label": "api"}  # one value per field: last wins
    assert query("nhãn backend") == {"label": "backend"}
    assert query("#bug") == {"type": "bug"}
    assert query("chưa nhận OTP") == {"q": "chưa nhận OTP"}
    assert query("sprint 12") == {"sprint": "7"}
    assert query("phiên bản v2.1 backlog") == {"version": "3", "sprint": "backlog"}


def test_unknown_words_become_search() -> None:
    chips, q = parse("story đăng nhập OTP của tôi", vocab())
    assert q == {"type": "story", "q": "đăng nhập OTP", "assignee": "me"}
    assert chips[-1].field == "q"
    # Filler alone does not search.
    assert query("tất cả task") == {}
    assert query("") == {}
    # A type the dự án does not use is plain text.
    v = vocab()
    v.types = ["task"]
    assert parse("bug", v)[1] == {"q": "bug"}


@pytest.mark.integration
def test_endpoint_reads_the_space(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    space = enable(client, world, as_user)
    an = as_user(world.an)
    create(client, an, world, title="Lỗi OTP", type="bug", priority="high")
    res = client.post(
        f"{API}/tasks/nl-filter",
        headers=an,
        json={"space": bp_of(world), "text": "bug cao đang làm OTP của tôi"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["used_ai"] is False
    assert body["query"] == {
        "type": "bug",
        "priority": "high",
        "status": str(status_id(space, "Đang làm")),
        "q": "OTP",
        "assignee": "me",
    }
    assert {c["field"] for c in body["chips"]} == set(body["query"])
    # The query works as GET /tasks params.
    listed = client.get(
        f"{API}/tasks", headers=an, params={"space": bp_of(world), "type": "bug", "q": "OTP"}
    )
    assert listed.json()["total"] == 1
    # Only one who sees the dự án's tasks.
    ungrant(db_session, world)
    res = client.post(
        f"{API}/tasks/nl-filter",
        headers=as_user(world.outsider),
        json={"space": bp_of(world), "text": "bug"},
    )
    assert res.status_code == 404
