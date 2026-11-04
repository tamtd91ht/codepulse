"""AI suggestion of the lead's points (docs/specs/25 §13)."""

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.dpi import service
from app.dpi.suggest import suggest
from app.models import Account, MergeRequest
from app.review.providers.base import ReviewPrompt
from app.review.providers.fake import FakeLLMProvider
from tests.api.conftest import FakeQueue, World

pytestmark = pytest.mark.integration
MONTH = date(2026, 10, 1)


def answer(prompt: ReviewPrompt) -> str:
    return json.dumps(
        {
            "summary": "Tháng ổn định.",
            "metrics": [
                {
                    "key": "A3",
                    "score": 7.3,
                    "confidence": "high",
                    "reason": "MR lớn",
                    "evidence": ["mr:1", "mr:999999"],
                },
                {"key": "B5", "score": 3.24, "confidence": "weird", "reason": "ok", "evidence": []},
                {
                    "key": "D3",
                    "score": None,
                    "confidence": "low",
                    "reason": "thiếu dữ liệu",
                    "evidence": [],
                },
                {
                    "key": "ZZ",
                    "score": 5,
                    "confidence": "high",
                    "reason": "không có",
                    "evidence": [],
                },
            ],
        }
    )


def test_the_suggestion_stays_within_the_rubric_and_cites_only_given_evidence(
    world: World, db_session: Session, settings: Settings
) -> None:
    account = Account(kind="gitlab_user", external_key="77", user_id=world.an.id)
    db_session.add(account)
    db_session.flush()
    mr = MergeRequest(
        project_id=world.project.id,
        iid=30,
        title="Thanh toán mới",
        author_account_id=account.id,
        state="merged",
        merged_at=datetime(2026, 10, 9, tzinfo=UTC),
        additions=400,
        deletions=20,
    )
    db_session.add(mr)
    db_session.flush()
    calc = service.Calculator(db_session, MONTH, settings)
    review = service.draft_review(db_session, world.an.id, MONTH)
    rubric = service.review_rubric(db_session, review)
    provider = FakeLLMProvider(responder=answer, cost_usd=0.03)
    result, cost = suggest(
        db_session, provider, calc.loader, world.an.id, MONTH, calc.card(review), rubric
    )
    assert cost == pytest.approx(0.03) and result is not None
    prompt = provider.prompts[0].user
    assert "Thanh toán mới" in prompt and f"mr:{mr.id}" in prompt
    metrics = result["metrics"]
    assert metrics["A3"]["score"] == 5.0  # clamped to the metric's max
    assert metrics["B5"]["score"] == 3.0 and metrics["B5"]["confidence"] == "low"
    assert metrics["D3"]["score"] is None
    assert "ZZ" not in metrics  # not a manager metric of the rubric
    # Evidence the model invented is dropped (mr:1 / mr:999999 were not given unless they exist).
    assert all(e["ref"] == f"mr:{mr.id}" for e in metrics["A3"]["evidence"])


def test_repos_without_external_llm_are_not_sent(
    world: World, db_session: Session, settings: Settings
) -> None:
    account = Account(kind="gitlab_user", external_key="78", user_id=world.an.id)
    db_session.add(account)
    world.project.allow_external_llm = False
    db_session.flush()
    db_session.add(
        MergeRequest(
            project_id=world.project.id,
            iid=31,
            title="Bí mật",
            author_account_id=account.id,
            state="merged",
            merged_at=datetime(2026, 10, 9, tzinfo=UTC),
        )
    )
    db_session.flush()
    calc = service.Calculator(db_session, MONTH, settings)
    review = service.draft_review(db_session, world.an.id, MONTH)
    provider = FakeLLMProvider(responder=answer)
    suggest(
        db_session,
        provider,
        calc.loader,
        world.an.id,
        MONTH,
        calc.card(review),
        service.review_rubric(db_session, review),
    )
    assert "Bí mật" not in provider.prompts[0].user


def test_only_who_assesses_may_ask_and_it_is_queued(
    client: TestClient, world: World, as_user: Any, queue: FakeQueue
) -> None:
    url = f"/api/v1/dpi/people/{world.an.id}/months/2026-10/suggest"
    assert client.post(url, headers=as_user(world.an)).status_code == 403
    assert client.post(url, headers=as_user(world.binh)).status_code == 403
    asked = client.post(url, headers=as_user(world.lead))
    assert asked.status_code == 202
    assert [j[0] for j in queue.jobs] == ["app.dpi.jobs.dpi_suggest_job"]
    assert client.post(url, headers=as_user(world.lead)).status_code == 409  # already running
    view = client.get(
        f"/api/v1/dpi/people/{world.an.id}?month=2026-10", headers=as_user(world.lead)
    )
    assert view.json()["ai_suggestion"]["status"] == "queued"
    mine = client.get(f"/api/v1/dpi/people/{world.an.id}?month=2026-10", headers=as_user(world.an))
    assert mine.json()["ai_suggestion"] is None  # the person does not see the lead's helper
