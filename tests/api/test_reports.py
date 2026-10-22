from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import MetricsUserPeriod
from app.reporting.jobs import send_team_reports
from app.reporting.notify import Delivery, DeliveryError, _retry
from app.reporting.report import build_team_report, render
from tests.api.conftest import WEEK, World

pytestmark = pytest.mark.integration
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture
def flagged(world: World, db_session: Session) -> World:
    """An had a critical this week: they should appear under "Cần hỗ trợ" for the lead."""
    row = db_session.scalars(select(MetricsUserPeriod).filter_by(user_id=world.an.id)).one()
    row.critical = 1
    db_session.flush()
    return world


def test_lead_report_has_people_sections(flagged: World, db_session: Session) -> None:
    report = build_team_report(db_session, flagged.backend.id, "week", WEEK, TZ, "lead")
    md = render(report, "md")
    assert report.title == "Weekly Code Quality – Team Backend – Tuần 41/2026"
    assert "| Quality Index | 75.0 | — |  |" in md
    assert "- [critical] SQL injection – app/a.py:3 – @An –" in md
    assert "## Cần hỗ trợ\n- An: 1 critical, QI 75.0" in md
    assert '100% finding thuộc "bảo mật" → buổi chia sẻ OWASP Top 10' in md
    html = render(report, "html")
    assert html.startswith("<!doctype html>") and "SQL injection" in html


@pytest.mark.parametrize("audience", ["manager", "channel"])
def test_other_audiences_never_see_per_person_scores(
    flagged: World, db_session: Session, audience: Any
) -> None:
    md = render(build_team_report(db_session, flagged.backend.id, "week", WEEK, TZ, audience))
    assert "Cần hỗ trợ" not in md and "Ghi nhận" not in md
    assert "QI 75.0" not in md


class RecordingSender:
    def __init__(self, fail_slack: bool = False) -> None:
        self.calls: list[tuple[str, Any, str]] = []
        self.fail_slack = fail_slack

    def email(self, to: list[str], subject: str, html: str, text: str) -> None:
        self.calls.append(("email", tuple(to), text))

    def slack(self, webhook: str, text: str) -> None:
        if self.fail_slack:
            raise DeliveryError("slack: ConnectError")
        self.calls.append(("slack", webhook, text))

    def teams(self, webhook: str, title: str, text: str) -> None:
        self.calls.append(("teams", webhook, text))


def test_each_audience_gets_its_version(flagged: World, db_session: Session) -> None:
    settings = Settings(
        report_tz="Asia/Ho_Chi_Minh",
        report_channels={"Backend": {"teams": "https://teams/hook", "slack": "https://slack/hook"}},
    )
    sender = RecordingSender()
    result = send_team_reports(db_session, "week", WEEK, settings, sender)
    by_target = {(kind, target): text for kind, target, text in sender.calls}
    lead_text = by_target[("email", ("lead@x.vn",))]
    manager_text = by_target[("email", ("manager@x.vn",))]
    assert "Cần hỗ trợ" in lead_text
    assert "Cần hỗ trợ" not in manager_text
    assert "Cần hỗ trợ" not in by_target[("slack", "https://slack/hook")]
    assert ("teams", "https://teams/hook") in by_target
    assert result.failed == []


def test_a_failing_channel_does_not_stop_the_others(flagged: World, db_session: Session) -> None:
    settings = Settings(slack_webhook_url=SecretStr("https://slack/default"))
    result = send_team_reports(db_session, "week", WEEK, settings, RecordingSender(fail_slack=True))
    assert result.failed == ["slack:Backend"]
    assert "email:lead:Backend" in result.sent


def test_report_endpoint_versions(client: TestClient, flagged: World, as_user: Any) -> None:
    url = f"/api/v1/reports/week/{WEEK}?team_id={flagged.backend.id}"
    lead = client.get(url, headers=as_user(flagged.lead))
    assert lead.headers["content-type"].startswith("text/markdown")
    assert "Cần hỗ trợ" in lead.text
    assert "Cần hỗ trợ" not in client.get(url, headers=as_user(flagged.manager)).text
    html = client.get(f"{url}&format=html", headers=as_user(flagged.lead))
    assert html.headers["content-type"].startswith("text/html")
    assert client.get(url, headers=as_user(flagged.an)).status_code == 403


def test_webhook_delivery_retries_then_succeeds() -> None:
    with respx.mock() as router:
        route = router.post("https://slack/hook")
        route.side_effect = [httpx.Response(500), httpx.Response(200)]
        Delivery("", "x@y", http=httpx.Client()).slack("https://slack/hook", "hi")
        assert route.call_count == 2


def test_retry_gives_up_with_a_delivery_error() -> None:
    def boom() -> None:
        raise OSError("connection refused")

    with pytest.raises(DeliveryError, match="email: OSError"):
        _retry(boom, "email", sleep=lambda s: None)
