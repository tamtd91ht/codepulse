"""Periodic team reports (docs/specs/10 "Báo cáo định kỳ", task 5.4).

Three audiences, because of the Phase 0 rule that individual scores are for the person and
their lead only:
- "lead": the full report, including who to recognise and who may need support;
- "manager": team aggregates only;
- "channel": what a team chat channel may see: aggregates and the open critical/high list
  (names included so issues have an owner) but no per-person scores.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    MetricsTeamPeriod,
    MetricsUserPeriod,
    Review,
    ReviewFinding,
    Team,
    TeamMember,
    User,
)
from app.scoring.aggregate import PeriodType, next_period, period_bounds, previous_period
from app.scoring.compare import compare_team, compare_user

Audience = Literal["lead", "manager", "channel"]
Format = Literal["md", "html"]

TEMPLATES = Path(__file__).parent / "templates"
CATEGORY_VI = {
    "security": "bảo mật",
    "bug": "lỗi logic",
    "performance": "hiệu năng",
    "maintainability": "khả năng bảo trì",
    "testing": "kiểm thử",
    "style": "convention",
    "docs": "tài liệu",
}
# Category share that triggers a training suggestion (spec example: 40% testing -> workshop).
COACHING_SHARE = 0.3
TRAINING = {
    "security": "buổi chia sẻ OWASP Top 10 / secure coding",
    "bug": "review chéo luồng chính + checklist edge case",
    "performance": "workshop truy vấn DB (N+1, index) và profiling",
    "maintainability": "refactoring kata, quy ước độ dài hàm",
    "testing": "workshop viết unit test",
    "style": "thống nhất CONVENTIONS.md + linter",
    "docs": "quy ước docstring cho API public",
}

_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(enabled_extensions=("html.j2",)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt(value: Any, digits: int = 1) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def _signed(value: Any, digits: int = 1) -> str:
    if value is None:
        return ""
    return f"{'+' if value > 0 else ''}{float(value):.{digits}f}"


_env.filters["fmt"] = _fmt
_env.filters["signed"] = _signed


@dataclass
class TeamReport:
    team: str
    period_type: PeriodType
    start: date
    audience: Audience
    title: str
    rows: list[dict[str, Any]]  # metric, this period, previous, delta
    open_issues: list[dict[str, Any]]
    praise: list[str] = field(default_factory=list)
    support: list[str] = field(default_factory=list)
    coaching: list[str] = field(default_factory=list)
    trend: list[dict[str, Any]] = field(default_factory=list)  # monthly: weekly QI


def _members(session: Session, team_id: int, start: date, end: date) -> list[User]:
    return list(
        session.scalars(
            select(User)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.joined_at < end,
                (TeamMember.left_at.is_(None)) | (TeamMember.left_at > start),
                User.active.is_not(False),
            )
            .distinct()
        )
    )


def build_team_report(
    session: Session,
    team_id: int,
    period_type: PeriodType,
    start: date,
    tz: ZoneInfo,
    audience: Audience = "lead",
    now: datetime | None = None,
) -> TeamReport:
    team = session.get(Team, team_id)
    if team is None:
        raise ValueError(f"team {team_id} not found")
    end = next_period(start, period_type)
    comparison = compare_team(session, team_id, period_type, start)
    cur, prev = comparison.current, comparison.previous

    metrics = [
        ("Quality Index", "quality_index", 1),
        ("Điểm TB", "avg_score", 1),
        ("Critical", "critical", 0),
        ("High", "high", 0),
        ("Mật độ lỗi / 100 LOC", "defect_density", 2),
        ("Build success %", "build_success_rate", 0),
        ("Reviews", "reviews_count", 0),
    ]

    def change(attr: str) -> float | None:
        now, before = getattr(cur, attr, None), getattr(prev, attr, None)
        return None if now is None or before is None else float(now) - float(before)

    rows = [
        {
            "label": label,
            "now": getattr(cur, attr, None),
            "before": getattr(prev, attr, None),
            "delta": change(attr),
            "digits": digits,
        }
        for label, attr, digits in metrics
    ]

    members = _members(session, team_id, start, end)
    names = {u.id: u.full_name or u.primary_email or f"#{u.id}" for u in members}
    moment = now or datetime.now(UTC)
    open_rows = session.execute(
        select(ReviewFinding, Review)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(
            Review.status == "done",
            Review.author_user_id.in_(names),
            ReviewFinding.status == "open",
            ReviewFinding.severity.in_(("critical", "high")),
        )
        .order_by(Review.finished_at)
        .limit(20)
    ).tuples()
    open_issues = [
        {
            "severity": f.severity,
            "title": f.title,
            "where": f"{f.file_path}:{f.line_start}",
            "who": names.get(r.author_user_id or 0, "—"),
            "days": max(0, (moment - (r.finished_at or moment)).days),
        }
        for f, r in open_rows
    ]

    lo, hi = period_bounds(start, period_type, tz)
    categories = Counter(
        session.scalars(
            select(ReviewFinding.category)
            .join(Review, Review.id == ReviewFinding.review_id)
            .where(
                Review.status == "done",
                Review.author_user_id.in_(names),
                Review.finished_at >= lo,
                Review.finished_at < hi,
                ReviewFinding.status != "false_positive",
                ReviewFinding.severity != "info",
            )
        )
    )
    total = sum(categories.values())
    coaching = [
        f'{share:.0%} finding thuộc "{CATEGORY_VI.get(cat, cat)}" → '
        f"{TRAINING.get(cat, 'chủ đề training')}"
        for cat, n in categories.most_common(3)
        if total and (share := n / total) >= COACHING_SHARE
    ]

    praise: list[str] = []
    support: list[str] = []
    if audience == "lead":
        by_user = {
            m.user_id: m
            for m in session.scalars(
                select(MetricsUserPeriod).where(
                    MetricsUserPeriod.user_id.in_(names),
                    MetricsUserPeriod.period_type == period_type,
                    MetricsUserPeriod.period_start == start,
                )
            )
        }
        before = {
            m.user_id: m
            for m in session.scalars(
                select(MetricsUserPeriod).where(
                    MetricsUserPeriod.user_id.in_(names),
                    MetricsUserPeriod.period_type == period_type,
                    MetricsUserPeriod.period_start == previous_period(start, period_type),
                )
            )
        }
        for user_id, m in sorted(by_user.items(), key=lambda kv: names[kv[0]]):
            previous_qi = before[user_id].quality_index if user_id in before else None
            gain = (
                float(m.quality_index) - float(previous_qi)
                if m.quality_index is not None and previous_qi is not None
                else None
            )
            clean = m.avg_score is not None and m.avg_score >= 90 and not m.high and not m.critical
            if clean or (gain is not None and gain >= 5):
                note = f"avg score {_fmt(m.avg_score, 0)}, {m.high or 0} high"
                if gain is not None and gain >= 5:
                    note += f", QI {_signed(gain)}"
                praise.append(f"{names[user_id]}: {note}")
            if (m.critical or 0) >= 1 or (m.quality_index is not None and m.quality_index < 60):
                support.append(
                    f"{names[user_id]}: {m.critical or 0} critical, QI {_fmt(m.quality_index)}"
                )

    trend: list[dict[str, Any]] = []
    if period_type == "month":
        weekly = session.scalars(
            select(MetricsTeamPeriod)
            .where(
                MetricsTeamPeriod.team_id == team_id,
                MetricsTeamPeriod.period_type == "week",
                MetricsTeamPeriod.period_start >= start,
                MetricsTeamPeriod.period_start < end,
            )
            .order_by(MetricsTeamPeriod.period_start)
        )
        trend = [
            {"week": w.period_start, "qi": w.quality_index, "critical": w.critical or 0}
            for w in weekly
        ]

    if period_type == "week":
        iso = start.isocalendar()
        title = f"Weekly Code Quality – Team {team.name} – Tuần {iso.week}/{iso.year}"
    else:
        title = f"Monthly Code Quality – Team {team.name} – Tháng {start:%m/%Y}"
    return TeamReport(
        team=team.name,
        period_type=period_type,
        start=start,
        audience=audience,
        title=title,
        rows=rows,
        open_issues=open_issues,
        praise=praise,
        support=support,
        coaching=coaching,
        trend=trend,
    )


def render(report: TeamReport, fmt: Format = "md") -> str:
    return _env.get_template(f"team_report.{fmt}.j2").render(r=report)


@dataclass
class PersonalReport:
    name: str
    email: str
    period_type: PeriodType
    start: date
    title: str
    rows: list[dict[str, Any]]
    team_quality_index: float | None
    open_issues: list[dict[str, Any]]
    categories: list[tuple[str, int]]
    dashboard_url: str


def build_personal_report(
    session: Session,
    user: User,
    period_type: PeriodType,
    start: date,
    tz: ZoneInfo,
    dashboard_url: str,
    now: datetime | None = None,
) -> PersonalReport | None:
    """docs/specs/10 (task 5.5): one person's own data only. None when they had no activity."""
    comparison = compare_user(session, user.id, period_type, start)
    cur, prev = comparison.current, comparison.previous
    if cur is None or not cur.reviews_count:
        return None

    def change(attr: str) -> float | None:
        now_, before = getattr(cur, attr, None), getattr(prev, attr, None)
        return None if now_ is None or before is None else float(now_) - float(before)

    rows = [
        {
            "label": label,
            "now": getattr(cur, attr),
            "before": getattr(prev, attr, None),
            "delta": change(attr),
            "digits": digits,
        }
        for label, attr, digits in (
            ("Quality Index", "quality_index", 1),
            ("Điểm TB", "avg_score", 1),
            ("Critical", "critical", 0),
            ("High", "high", 0),
            ("Mật độ lỗi / 100 LOC", "defect_density", 2),
            ("Reviews", "reviews_count", 0),
        )
    ]
    moment = now or datetime.now(UTC)
    open_rows = session.execute(
        select(ReviewFinding, Review)
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(
            Review.status == "done",
            Review.author_user_id == user.id,
            ReviewFinding.status == "open",
            ReviewFinding.severity.in_(("critical", "high", "medium")),
        )
        # Alphabetical happens to be most severe first for these three.
        .order_by(ReviewFinding.severity, Review.finished_at)
        .limit(10)
    ).tuples()
    open_issues = [
        {
            "severity": f.severity,
            "title": f.title,
            "where": f"{f.file_path}:{f.line_start}",
            "days": max(0, (moment - (r.finished_at or moment)).days),
            "review_id": r.id,
        }
        for f, r in open_rows
    ]
    lo, hi = period_bounds(start, period_type, tz)
    categories = Counter(
        session.scalars(
            select(ReviewFinding.category)
            .join(Review, Review.id == ReviewFinding.review_id)
            .where(
                Review.status == "done",
                Review.author_user_id == user.id,
                Review.finished_at >= lo,
                Review.finished_at < hi,
                ReviewFinding.status != "false_positive",
            )
        )
    ).most_common(3)
    if period_type == "week":
        iso = start.isocalendar()
        title = f"Báo cáo cá nhân – Tuần {iso.week}/{iso.year}"
    else:
        title = f"Báo cáo cá nhân – Tháng {start:%m/%Y}"
    return PersonalReport(
        name=user.full_name or user.primary_email or "",
        email=user.primary_email or "",
        period_type=period_type,
        start=start,
        title=title,
        rows=rows,
        team_quality_index=comparison.team_quality_index,
        open_issues=open_issues,
        categories=[(CATEGORY_VI.get(c, c), n) for c, n in categories],
        dashboard_url=f"{dashboard_url.rstrip('/')}/members/{user.id}",
    )


def render_personal(report: PersonalReport, fmt: Format = "md") -> str:
    return _env.get_template(f"personal_report.{fmt}.j2").render(r=report)
