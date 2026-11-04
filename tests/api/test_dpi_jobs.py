"""DPI jobs and readiness (docs/specs/25 §4.5, §10)."""

import json
import re
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dpi.jobs import classify_month
from app.models import Account, AuditLog, MergeRequest, MrNote, ReviewComment
from app.review.providers.base import ReviewPrompt
from app.review.providers.fake import FakeLLMProvider
from tests.api.conftest import World

pytestmark = pytest.mark.integration


def labeller(prompt: ReviewPrompt) -> str:
    rows = json.loads(re.search(r"<comments>\n(.*)\n</comments>", prompt.user, re.S).group(1))  # type: ignore[union-attr]
    return json.dumps({"labels": [{"id": r["id"], "label": "correctness"} for r in rows]})


def test_comments_are_labelled_by_the_model_and_pushes_mark_them_acted_on(
    world: World, db_session: Session, settings: Settings
) -> None:
    author = Account(kind="gitlab_user", external_key="1", display_name="an", user_id=world.an.id)
    reviewer = Account(
        kind="gitlab_user", external_key="2", display_name="binh", user_id=world.binh.id
    )
    db_session.add_all([author, reviewer])
    db_session.flush()
    mr = db_session.get(MergeRequest, world.mr.id)
    assert mr is not None
    mr.author_account_id = author.id
    at = datetime(2026, 10, 8, 9, tzinfo=UTC)
    db_session.add_all(
        [
            MrNote(
                id=1,
                mr_id=mr.id,
                author_account_id=reviewer.id,
                is_system=False,
                body="Chỗ này có thể null khi khách chưa đăng nhập",
                created_at_src=at,
            ),
            MrNote(
                id=2,
                mr_id=mr.id,
                author_account_id=reviewer.id,
                is_system=False,
                body="ok",
                created_at_src=at,
            ),
            MrNote(
                id=3,
                mr_id=mr.id,
                author_account_id=author.id,
                is_system=False,
                body="Cảm ơn, mình sửa ngay",
                created_at_src=at,
            ),
        ]
    )
    db_session.flush()
    provider = FakeLLMProvider(responder=labeller, cost_usd=0.01)
    result = classify_month(db_session, date(2026, 10, 1), settings, provider)
    assert result == {"comments": 2, "llm": 1, "calls": 1}  # the author's own note is not one
    labels = {c.note_id: (c.label, c.by) for c in db_session.scalars(select(ReviewComment))}
    assert labels == {1: ("correctness", "llm"), 2: ("other", "rule")}
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "dpi.classify.cost")).one()
    # A second run labels nothing again.
    assert classify_month(db_session, date(2026, 10, 1), settings, provider)["comments"] == 0


def test_readiness_lists_repos_for_admins(client: TestClient, world: World, as_user: Any) -> None:
    body = client.get("/api/v1/dpi/readiness", headers=as_user(world.admin)).json()
    assert [r["project"]["path"] for r in body["repos"]] == ["demo/p"]
    assert body["acceptance"]["targets"]["through_mr_pct"] == 80
    assert client.get("/api/v1/dpi/readiness", headers=as_user(world.lead)).status_code == 403


def test_rebuild_is_queued_by_admins_only(
    client: TestClient, world: World, as_user: Any, queue: Any
) -> None:
    url = "/api/v1/dpi/rebuild"
    assert client.post(url, headers=as_user(world.lead), json={}).status_code == 403
    started = client.post(url, headers=as_user(world.admin), json={"months": 3, "resync": True})
    assert started.status_code == 202
    (job,) = [j for j in queue.jobs if j[0] == "app.dpi.jobs.dpi_rebuild_job"]
    assert job[1] == (3, True) and job[2]["job_id"] == "dpi-rebuild"
    assert client.get(url, headers=as_user(world.admin)).json() == {"status": "none"}
    assert client.post(url, headers=as_user(world.admin), json={"months": 9}).status_code == 422
