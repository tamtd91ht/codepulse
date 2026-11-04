"""The result of an MR review asked for in Telegram: levels, counts and a download link."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditLog, ReviewFinding, TelegramBot, TelegramRequest
from app.telegram.mr_report import link_url
from app.telegram.service import finished_requests
from tests.api.conftest import World

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 9, tzinfo=UTC)


def request_for(
    session: Session, bot: TelegramBot, world: World, *targets: dict[str, object]
) -> None:
    session.add(
        TelegramRequest(
            bot_id=bot.id,
            chat_id=-100,
            message_id=5,
            user_id=world.an.id,
            command="/review",
            targets=list(targets),
            created_at=NOW,
        )
    )
    session.flush()


MR_TARGET = {"kind": "mr", "project_id": 0, "ref": "7", "sha": "a" * 40, "label": "demo/p !7"}


def test_an_mr_result_spells_out_levels_counts_and_the_download_link(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings
) -> None:
    world.an_review.verdict = "block"
    db_session.add_all(
        [
            ReviewFinding(review_id=world.an_review.id, severity="low", category="maintainability"),
            ReviewFinding(  # refuted: not counted
                review_id=world.an_review.id,
                severity="high",
                category="bug",
                verified="rejected",
                status="rejected",
            ),
            ReviewFinding(  # undecided: listed apart
                review_id=world.an_review.id, severity="high", category="bug", verified="unverified"
            ),
        ]
    )
    request_for(
        db_session,
        bot,
        world,
        {**MR_TARGET, "project_id": world.project.id},
        {
            "kind": "commit",
            "project_id": world.project.id,
            "ref": "b" * 40,
            "sha": "b" * 40,
            "label": "demo/p bbbbbbbb",
        },
    )
    ((_, text),) = finished_requests(
        db_session,
        bot,
        settings.dashboard_url,
        NOW,
        timedelta(hours=3),
        report_link=lambda rid: link_url(settings, rid, NOW),
        link_hours=24,
    )
    mr_part, commit_part = text.split("• demo/p bbbbbbbb")
    assert "Mức cao nhất: 🔴 <b>Critical</b>" in mr_part
    assert "🔴 1 critical · 🟠 0 high · 🟡 0 medium · 🔵 1 low" in mr_part
    assert "(1 finding chưa kiểm chứng, không tính điểm)" in mr_part
    assert (
        f'<a href="https://dash.example.com/api/v1/review-reports/{world.an_review.id}.' in mr_part
    )
    assert "link hết hạn sau 24 giờ" in mr_part
    # A commit keeps the short line, and the legend comes once.
    assert "Tải kết quả" not in commit_part and "Mức cao nhất" not in commit_part
    assert text.count("<i>Các mức:</i>") == 1 and "🔵 <b>Low</b>" in text


def test_the_link_downloads_the_analysis_until_it_expires(
    client: TestClient, db_session: Session, world: World, settings: Settings
) -> None:
    world.an_finding.message = "<script>alert(1)</script> nối chuỗi vào SQL"
    world.an_finding.suggestion = "Dùng tham số"
    world.an_finding.code_excerpt = '+ q = f"SELECT * FROM t WHERE id={x}"'
    db_session.flush()
    now = datetime.now(UTC)
    url = link_url(settings, world.an_review.id, now)
    assert url is not None
    path = url.split("https://dash.example.com", 1)[1]
    res = client.get(path)  # no session: the token is the permission
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert (
        res.headers["content-disposition"] == 'attachment; filename="codepulse-review-p-mr7.html"'
    )
    assert res.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    body = res.text
    assert "Kết quả review MR !7 · demo/p" in body and "SQL injection" in body
    assert "&lt;script&gt;" in body and "<script>" not in body  # everything escaped
    assert "Các mức nghĩa là gì" in body and "Phải sửa trước khi merge." in body
    assert "app/a.py:3" in body and "Dùng tham số" in body
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.action == "telegram.report_download"))
        is not None
    )

    rid, exp, _sig = path.rsplit("/", 1)[1].split(".")
    assert client.get(path.replace(f"{rid}.{exp}", f"{rid}.{int(exp) + 60}")).status_code == 404
    old = link_url(settings, world.an_review.id, now - timedelta(hours=25))
    assert old is not None
    expired = client.get(old.split("https://dash.example.com", 1)[1])
    assert expired.status_code == 410 and "hết hạn" in expired.text
    # Only MR reviews: a commit review's link is refused even when correctly signed.
    commit_link = link_url(settings, world.binh_review.id, now)
    assert commit_link is not None
    assert client.get(commit_link.split("https://dash.example.com", 1)[1]).status_code == 404


def test_results_carry_the_criteria_scores(
    client: TestClient,
    db_session: Session,
    bot: TelegramBot,
    world: World,
    settings: Settings,
    as_user: Any,
) -> None:
    from app.notifications.payload import build, text
    from app.telegram.mr_report import render
    from app.workers.criteria_jobs import recompute_criteria
    from tests.api.test_criteria_api import assign, bp, make

    admin = as_user(world.admin)
    made = make(client, admin, name="Chuẩn OmiCRM", apply_caps=False)
    assign(client, admin, bp(world), [{"set_id": made["id"], "is_primary": True}])
    world.an_finding.criteria = {str(made["id"]): "correctness"}
    world.an_review.loc_reviewed = 100
    db_session.flush()
    assert recompute_criteria(db_session, bp(world), made["id"]) >= 1

    # Telegram: the MR result names the set, its score and each component's score.
    request_for(db_session, bot, world, {**MR_TARGET, "project_id": world.project.id})
    ((_, message),) = finished_requests(
        db_session, bot, settings.dashboard_url, NOW, timedelta(hours=3)
    )
    assert "📐 Chuẩn OmiCRM v1: <b>85</b>/100" in message
    assert "Đúng chức năng 50" in message
    # The analysis file has the table.
    _, body = render(db_session, world.an_review, settings, NOW, NOW + timedelta(hours=1))
    assert "Điểm theo tiêu chí · Chuẩn OmiCRM v1 (bộ chính): 85/100" in body
    # Notification webhooks: JSON field and chat text.
    payload = build(db_session, world.an_review, settings.dashboard_url, NOW)
    (scored,) = payload["review"]["criteria_scores"]
    assert scored["set_name"] == "Chuẩn OmiCRM" and scored["score"] == 85.0
    assert any(c["key"] == "correctness" for c in scored["components"])
    assert "📐 Chuẩn OmiCRM v1: *85*/100" in text("review.done", payload)
