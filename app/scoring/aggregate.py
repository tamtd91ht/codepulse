"""Weekly / monthly metrics per user and team (docs/specs/08 §2-5, task 4.3).

aggregate() is idempotent: it rewrites every row of the period, so re-running after a dispute or
a weight change gives the same result as a first run.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    MergeRequest,
    MetricsTeamPeriod,
    MetricsUserPeriod,
    Pipeline,
    Review,
    ReviewFinding,
    TeamMember,
    User,
)
from app.review.status import SEVERE
from app.scoring.formula import (
    MIN_CONFIDENCE_UNVERIFIED,
    SEVERITIES,
    ScoredFinding,
    counts_for_score,
    quality_index,
    review_score,
    rule_for,
    weights_for,
)
from app.scoring.process import process_metrics
from app.scoring.verdict import assess

log = logging.getLogger(__name__)

PeriodType = Literal["week", "month"]


# -- periods ------------------------------------------------------------------------------------


def period_start_of(day: date, period_type: PeriodType) -> date:
    return day - timedelta(days=day.weekday()) if period_type == "week" else day.replace(day=1)


def next_period(start: date, period_type: PeriodType) -> date:
    if period_type == "week":
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def previous_period(start: date, period_type: PeriodType) -> date:
    return period_start_of(start - timedelta(days=1), period_type)


def period_bounds(start: date, period_type: PeriodType, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) as aware datetimes in the report timezone (docs/specs/08: REPORT_TZ)."""
    end = next_period(start, period_type)
    return datetime.combine(start, time.min, tz), datetime.combine(end, time.min, tz)


# -- inputs -------------------------------------------------------------------------------------


@dataclass
class ReviewFacts:
    user_id: int
    finished_at: datetime
    score: float | None
    loc: int
    rubric_version: str | None
    # The period's moment (Review.counted_at: the change's time); finished_at when not given.
    counted_at: datetime | None = None
    # severity, confidence, status, verified (docs/specs/19 §4.3)
    findings: list[tuple[str, float, str, str]] = field(default_factory=list)
    mr_id: int | None = None
    # Critical/high fingerprints over every review of the MR, and those still in this one.
    severe_all: set[str] = field(default_factory=set)
    severe_latest: set[str] = field(default_factory=set)


@dataclass
class PeriodData:
    reviews: list[ReviewFacts]
    merged: list[tuple[int, MergeRequest]]  # (user_id, MR merged in the period)
    pipelines: dict[tuple[int | None, str | None], list[Pipeline]]  # (project, branch) -> runs


def load_period(session: Session, start: datetime, end: datetime) -> PeriodData:
    active = select(User.id).where(User.active.is_not(False))
    reviews: dict[int, ReviewFacts] = {}
    for review in session.scalars(
        select(Review).where(
            Review.status == "done",
            Review.counted_at >= start,
            Review.counted_at < end,
            Review.author_user_id.in_(active),
        )
    ):
        if review.author_user_id is None or review.finished_at is None:
            continue  # excluded by the query; keeps the types honest
        reviews[review.id] = ReviewFacts(
            user_id=review.author_user_id,
            finished_at=review.finished_at,
            score=float(review.score) if review.score is not None else None,
            loc=review.loc_reviewed or 0,
            rubric_version=review.rubric_version,
            mr_id=review.mr_id if review.target_type == "mr" else None,
            counted_at=review.counted_at,
        )
    if reviews:
        for finding in session.scalars(
            select(ReviewFinding).where(ReviewFinding.review_id.in_(reviews))
        ):
            if finding.review_id is None:
                continue
            reviews[finding.review_id].findings.append(
                (
                    finding.severity,
                    float(finding.confidence or 1),
                    finding.status or "open",
                    finding.verified or "skipped",
                )
            )

    _load_fixes(session, reviews)

    merged = [
        (user_id, mr)
        for mr, user_id in session.execute(
            select(MergeRequest, Account.user_id)
            .join(Account, Account.id == MergeRequest.author_account_id)
            .where(
                MergeRequest.merged_at >= start,
                MergeRequest.merged_at < end,
                Account.user_id.in_(active),
            )
        ).tuples()
        if user_id is not None
    ]
    pipelines: dict[tuple[int | None, str | None], list[Pipeline]] = defaultdict(list)
    branches = {(mr.project_id, mr.source_branch) for _, mr in merged}
    if branches:
        projects = {p for p, _ in branches}
        for pipeline in session.scalars(
            select(Pipeline).where(Pipeline.project_id.in_(projects), Pipeline.created_at_src < end)
        ):
            key = (pipeline.project_id, pipeline.ref)
            if key in branches:
                pipelines[key].append(pipeline)
    return PeriodData(list(reviews.values()), merged, pipelines)


def _load_fixes(session: Session, reviews: dict[int, ReviewFacts]) -> None:
    """fix_rate inputs (task 4.4): compare each MR's latest review with all its earlier ones."""
    latest_by_mr = {r.mr_id: rid for rid, r in reviews.items() if r.mr_id is not None}
    if not latest_by_mr:
        return
    rows_by_id = {
        review_id: (mr_id, sha)
        for review_id, mr_id, sha in session.execute(
            select(Review.id, Review.mr_id, Review.reviewed_sha).where(
                Review.mr_id.in_(latest_by_mr), Review.status.in_(("done", "superseded"))
            )
        ).tuples()
        if mr_id is not None
    }
    latest_sha = {
        mr_id: rows_by_id.get(rid, (None, None))[1] for mr_id, rid in latest_by_mr.items()
    }
    # Earlier rounds of the latest head (a forced re-judgment) are not "before the fix": a
    # finding the rerun stopped reporting was not fixed by anyone (docs/specs/17 §Lịch sử).
    mr_of_review: dict[int, int] = {
        review_id: mr_id
        for review_id, (mr_id, sha) in rows_by_id.items()
        if review_id == latest_by_mr[mr_id] or sha != latest_sha[mr_id]
    }
    rows = session.execute(
        select(ReviewFinding.review_id, ReviewFinding.fingerprint).where(
            ReviewFinding.review_id.in_(mr_of_review),
            ReviewFinding.severity.in_(SEVERE),
            ReviewFinding.fingerprint.is_not(None),
            ReviewFinding.status != "false_positive",
        )
    ).tuples()
    for review_id, fingerprint in rows:
        if review_id is None or fingerprint is None:
            continue
        mr_id = mr_of_review[review_id]
        latest = reviews[latest_by_mr[mr_id]]
        latest.severe_all.add(fingerprint)
        if review_id == latest_by_mr[mr_id]:
            latest.severe_latest.add(fingerprint)


# -- metrics ------------------------------------------------------------------------------------


def _dec(value: float | None, places: int = 2) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, places)))


def compute(
    reviews: list[ReviewFacts],
    merged: list[MergeRequest],
    pipelines: list[Pipeline],
    weights_override: Mapping[str, float] | None = None,
    min_sample: int = 3,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
) -> dict[str, Any]:
    """One metrics row (any owner) from the reviews and MRs attributed to that owner. A finding
    counts here exactly when it counted in the review's score (formula.counts_for_score): never
    a disputed or rejected one, and from rubric v4 no unverified one below the confidence
    threshold (docs/specs/19 §4.4)."""
    counts = dict.fromkeys(SEVERITIES, 0)
    penalty = 0.0
    weighted_score = weight_total = 0.0
    for review in reviews:
        weights = weights_for(review.rubric_version, weights_override)
        rule = rule_for(review.rubric_version)
        for severity, confidence, status, verified in review.findings:
            scored = ScoredFinding(severity, confidence, status, verified)
            if not counts_for_score(scored, rule, min_confidence_unverified):
                continue
            counts[severity] += 1
            penalty += weights.get(severity, 0) * confidence
        if review.score is not None:
            # LOC-weighted (docs/specs/08): a 5-line MR must not weigh as much as a 500-line one.
            w = max(review.loc, 1)
            weighted_score += review.score * w
            weight_total += w
    loc = sum(r.loc for r in reviews)
    process = process_metrics(merged, pipelines)
    severe_total = sum(len(r.severe_all) for r in reviews)
    severe_fixed = sum(len(r.severe_all - r.severe_latest) for r in reviews)
    avg_score = weighted_score / weight_total if weight_total else None
    density = penalty / (loc / 100) if loc else None
    fix_rate = severe_fixed / severe_total if severe_total else None
    # docs/specs/08: no index from too little data (MIN_SAMPLE reviews).
    index = (
        quality_index(avg_score, density, process.build_success_rate, fix_rate)
        if len(reviews) >= min_sample
        else None
    )
    return {
        "reviews_count": len(reviews),
        "avg_score": _dec(avg_score),
        "loc_changed": loc,
        **counts,
        "weighted_penalty": _dec(penalty),
        "defect_density": _dec(density, 3),
        "mr_count": process.mr_count,
        "avg_review_time_h": _dec(process.avg_review_time_h),
        "avg_mr_size": process.avg_mr_size,
        "avg_review_rounds": _dec(process.avg_review_rounds),
        "build_success_rate": _dec(process.build_success_rate),
        "fix_rate": _dec(fix_rate, 3),
        "quality_index": _dec(index),
    }


def _teams_at(memberships: list[TeamMember], user_id: int, day: date) -> set[int]:
    """Teams the user belonged to on that day (team history: docs/specs/05, 08 §4)."""
    return {
        m.team_id
        for m in memberships
        if m.user_id == user_id and m.joined_at <= day and (m.left_at is None or m.left_at > day)
    }


@dataclass
class AggregateResult:
    users: int
    teams: int


def recompute_job(first_day: str, last_day: str) -> dict[str, int]:
    """RQ entry point: re-aggregate every period in a day range after repos were removed (the
    remaining reviews keep their scores, so they are not re-scored)."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    settings = get_settings()
    with get_sessionmaker()() as session, session.begin():
        result = recompute(
            session,
            date.fromisoformat(first_day),
            date.fromisoformat(last_day),
            ZoneInfo(settings.report_tz),
            settings.scoring_weights,
            settings.min_sample,
            rescore=False,
            min_confidence_unverified=settings.verify_min_confidence_unverified,
        )
    return {"periods": len(result.periods)}


def aggregate_job(period_type: PeriodType, period_start: str | None = None) -> dict[str, int]:
    """Scheduler/RQ entry point: the given period, or the last complete one."""
    from app.config import get_settings
    from app.db import get_sessionmaker

    settings = get_settings()
    tz = ZoneInfo(settings.report_tz)
    if period_start:
        start = date.fromisoformat(period_start)
    else:
        start = previous_period(period_start_of(datetime.now(tz).date(), period_type), period_type)
    with get_sessionmaker()() as session, session.begin():
        result = aggregate(
            session,
            period_type,
            start,
            tz,
            settings.scoring_weights,
            settings.min_sample,
            min_confidence_unverified=settings.verify_min_confidence_unverified,
        )
        for earlier in late_periods(session, period_type, start, tz):
            aggregate(
                session,
                period_type,
                earlier,
                tz,
                settings.scoring_weights,
                settings.min_sample,
                min_confidence_unverified=settings.verify_min_confidence_unverified,
            )
    return {"users": result.users, "teams": result.teams}


def late_periods(
    session: Session, period_type: PeriodType, period_start: date, tz: ZoneInfo
) -> list[date]:
    """Earlier periods that gained a review since this one began: a review counts in the period
    of its change (Review.counted_at), so one that finished now for an older commit changes a
    period that was already aggregated. No upper bound: a review that finished after this period
    closed (before the job ran, or while a run was missed) is taken now, not a period later."""
    start, _ = period_bounds(period_start, period_type, tz)
    moments = session.scalars(
        select(Review.counted_at).where(
            Review.status == "done",
            Review.finished_at >= start,
            Review.counted_at < start,
        )
    )
    return sorted(
        {period_start_of(moment.astimezone(tz).date(), period_type) for moment in moments if moment}
    )


def aggregate(
    session: Session,
    period_type: PeriodType,
    period_start: date,
    tz: ZoneInfo,
    weights_override: Mapping[str, float] | None = None,
    min_sample: int = 3,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
) -> AggregateResult:
    period_start = period_start_of(period_start, period_type)
    start, end = period_bounds(period_start, period_type, tz)
    data = load_period(session, start, end)

    def pipelines_for(mrs: Iterable[MergeRequest]) -> list[Pipeline]:
        return [p for mr in mrs for p in data.pipelines.get((mr.project_id, mr.source_branch), [])]

    by_user_reviews: dict[int, list[ReviewFacts]] = defaultdict(list)
    by_user_mrs: dict[int, list[MergeRequest]] = defaultdict(list)
    by_team_reviews: dict[int, list[ReviewFacts]] = defaultdict(list)
    by_team_mrs: dict[int, list[MergeRequest]] = defaultdict(list)
    memberships = list(session.scalars(select(TeamMember)))
    for review in data.reviews:
        by_user_reviews[review.user_id].append(review)
        for team in _teams_at(
            memberships,
            review.user_id,
            (review.counted_at or review.finished_at).astimezone(tz).date(),
        ):
            by_team_reviews[team].append(review)
    for user_id, mr in data.merged:
        by_user_mrs[user_id].append(mr)
        if mr.merged_at is None:
            continue
        for team in _teams_at(memberships, user_id, mr.merged_at.astimezone(tz).date()):
            by_team_mrs[team].append(mr)

    key = {"period_type": period_type, "period_start": period_start}
    session.execute(delete(MetricsUserPeriod).filter_by(**key))
    session.execute(delete(MetricsTeamPeriod).filter_by(**key))
    users = set(by_user_reviews) | set(by_user_mrs)
    for user_id in users:
        mrs = by_user_mrs[user_id]
        row = compute(
            by_user_reviews[user_id],
            mrs,
            pipelines_for(mrs),
            weights_override,
            min_sample,
            min_confidence_unverified,
        )
        session.add(MetricsUserPeriod(user_id=user_id, **key, **row))
    teams = set(by_team_reviews) | set(by_team_mrs)
    for team_id in teams:
        mrs = by_team_mrs[team_id]
        row = compute(
            by_team_reviews[team_id],
            mrs,
            pipelines_for(mrs),
            weights_override,
            min_sample,
            min_confidence_unverified,
        )
        session.add(MetricsTeamPeriod(team_id=team_id, **key, **row))
    session.flush()
    log.info(
        "metrics aggregated",
        extra={
            "period": period_type,
            "start": str(period_start),
            "users": len(users),
            "teams": len(teams),
        },
    )
    return AggregateResult(users=len(users), teams=len(teams))


def recompute_scores(
    session: Session,
    start: datetime,
    end: datetime,
    weights_override: Mapping[str, float] | None = None,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
    review_ids: Iterable[int] | None = None,
) -> int:
    """Re-score finished reviews from their findings (after a dispute or a weight change), with
    the same VERIFY_MIN_CONFIDENCE_UNVERIFIED the review path uses. `review_ids` limits it to
    those (a dispute re-scores its own review, not every review of the same moment)."""
    changed = 0
    query = select(Review).where(
        Review.status.in_(("done", "superseded")),
        Review.counted_at >= start,
        Review.counted_at < end,
    )
    if review_ids is not None:
        query = query.where(Review.id.in_(list(review_ids)))
    for review in session.scalars(query):
        findings = [
            ScoredFinding(
                f.severity, float(f.confidence or 1), f.status or "open", f.verified or "skipped"
            )
            for f in session.scalars(
                select(ReviewFinding).where(ReviewFinding.review_id == review.id)
            )
        ]
        weights = weights_for(review.rubric_version, weights_override)
        score = Decimal(
            str(
                review_score(
                    findings,
                    review.loc_reviewed or 0,
                    weights,
                    review.rubric_version,
                    min_confidence_unverified,
                )
            )
        )
        verdict, risk = assess((f.severity, f.status, f.verified) for f in findings)
        if review.score != score or review.verdict != verdict or review.risk_score != risk:
            review.score, review.verdict, review.risk_score = score, verdict, risk
            changed += 1
    session.flush()
    return changed


@dataclass
class RecomputeResult:
    reviews_rescored: int
    periods: list[tuple[str, date]]


def recompute(
    session: Session,
    first_day: date,
    last_day: date,
    tz: ZoneInfo,
    weights_override: Mapping[str, float] | None = None,
    min_sample: int = 3,
    rescore: bool = True,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
) -> RecomputeResult:
    """docs/specs/08 §5 `codepulse metrics recompute --from --to`: scores, then every week and
    month that overlaps [first_day, last_day]. rescore=False only re-aggregates."""
    start = datetime.combine(first_day, time.min, tz)
    end = datetime.combine(last_day + timedelta(days=1), time.min, tz)
    rescored = (
        recompute_scores(session, start, end, weights_override, min_confidence_unverified)
        if rescore
        else 0
    )
    periods: list[tuple[str, date]] = []
    period_type: PeriodType
    for period_type in ("week", "month"):
        current = period_start_of(first_day, period_type)
        while current <= last_day:
            aggregate(
                session,
                period_type,
                current,
                tz,
                weights_override,
                min_sample,
                min_confidence_unverified,
            )
            periods.append((period_type, current))
            current = next_period(current, period_type)
    return RecomputeResult(rescored, periods)
