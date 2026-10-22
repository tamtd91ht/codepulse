"""Send the periodic reports (docs/specs/10: weekly Monday 08:00, monthly on the 1st)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import MetricsTeamPeriod, Team, TeamMember, User
from app.reporting.notify import Delivery, DeliveryError, Sender
from app.reporting.report import Audience, build_team_report, render
from app.scoring.aggregate import PeriodType, period_start_of, previous_period

log = logging.getLogger(__name__)


@dataclass
class SendResult:
    sent: list[str] = field(default_factory=list)  # "email:lead:x@y", "slack:Backend", ...
    failed: list[str] = field(default_factory=list)


def _emails_by_role(session: Session, team: Team, day: date) -> dict[str, set[str]]:
    """Current leads of the team; managers of the team or any parent team."""
    parents, current = {team.id}, team
    while current.parent_id is not None and current.parent_id not in parents:
        parents.add(current.parent_id)
        parent = session.get(Team, current.parent_id)
        if parent is None:
            break
        current = parent
    rows = session.execute(
        select(TeamMember.team_id, TeamMember.role, User.primary_email)
        .join(User, User.id == TeamMember.user_id)
        .where(
            TeamMember.team_id.in_(parents),
            TeamMember.joined_at <= day,
            (TeamMember.left_at.is_(None)) | (TeamMember.left_at > day),
            User.active.is_not(False),
            User.primary_email.is_not(None),
        )
    ).tuples()
    out: dict[str, set[str]] = {"lead": set(), "manager": set()}
    for team_id, role, email in rows:
        role = (role or "").lower()
        if role == "lead" and team_id == team.id and email:
            out["lead"].add(email)
        elif role == "manager" and email:
            out["manager"].add(email)
    out["manager"] -= out["lead"]  # a lead who also manages gets the fuller report once
    return out


def _attempt(result: SendResult, label: str, action: Callable[[], None]) -> None:
    """One delivery; a failing channel is logged and never stops the others."""
    try:
        action()
        result.sent.append(label)
    except DeliveryError as exc:
        log.error("report delivery failed", extra={"target": label, "error": str(exc)})
        result.failed.append(label)


def _send_team(
    session: Session,
    team: Team,
    period_type: PeriodType,
    start: date,
    tz: ZoneInfo,
    day: date,
    settings: Settings,
    sender: Sender,
    result: SendResult,
) -> None:
    def rendered(audience: Audience) -> tuple[str, str, str]:
        report = build_team_report(session, team.id, period_type, start, tz, audience)
        return report.title, render(report, "md"), render(report, "html")

    recipients = _emails_by_role(session, team, day)
    people: tuple[Audience, ...] = ("lead", "manager")
    for audience in people:
        to = sorted(recipients[audience])
        if to:
            title, md, html = rendered(audience)
            _attempt(
                result, f"email:{audience}:{team.name}", partial(sender.email, to, title, html, md)
            )

    channels = settings.report_channels.get(team.name, {})
    slack = channels.get("slack") or settings.slack_webhook_url.get_secret_value()
    title, md, html = rendered("channel")
    if slack:
        _attempt(result, f"slack:{team.name}", partial(sender.slack, slack, md))
    if channels.get("teams"):
        _attempt(result, f"teams:{team.name}", partial(sender.teams, channels["teams"], title, md))
    if channels.get("emails"):
        _attempt(
            result,
            f"email:channel:{team.name}",
            partial(sender.email, list(channels["emails"]), title, html, md),
        )


def send_team_reports(
    session: Session,
    period_type: PeriodType,
    start: date,
    settings: Settings,
    sender: Sender,
    today: date | None = None,
) -> SendResult:
    tz = ZoneInfo(settings.report_tz)
    day = today or datetime.now(tz).date()
    result = SendResult()
    team_ids = set(
        session.scalars(
            select(MetricsTeamPeriod.team_id).where(
                MetricsTeamPeriod.period_type == period_type,
                MetricsTeamPeriod.period_start == start,
            )
        )
    )
    for team in session.scalars(select(Team).where(Team.id.in_(team_ids)).order_by(Team.name)):
        _send_team(session, team, period_type, start, tz, day, settings, sender, result)
    log.info(
        "reports sent",
        extra={
            "period": period_type,
            "start": str(start),
            "sent": len(result.sent),
            "failed": len(result.failed),
        },
    )
    return result


def send_reports_job(period_type: PeriodType) -> dict[str, int]:
    """Scheduler entry point: report on the last complete period."""
    from app.db import get_sessionmaker

    settings = get_settings()
    tz = ZoneInfo(settings.report_tz)
    start = previous_period(period_start_of(datetime.now(tz).date(), period_type), period_type)
    delivery = Delivery(settings.smtp_url.get_secret_value(), settings.smtp_from)
    with get_sessionmaker()() as session:
        result = send_team_reports(session, period_type, start, settings, delivery)
    return {"sent": len(result.sent), "failed": len(result.failed)}
