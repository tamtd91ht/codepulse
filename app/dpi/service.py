"""The DPI cycle (docs/specs/25 §5, §6): rubric and profiles in force, each person's month
(`DpiReview`), its scorecard computed from the facts and what the lead gave, the snapshots."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.dpi.facts import Loader, active_people
from app.dpi.metrics import compute, split_mrs
from app.dpi.rubric import (
    DEFAULT_PROFILE,
    DEFAULT_RUBRIC,
    MANAGER_METRICS,
    Rubric,
    as_rubric,
)
from app.dpi.score import Profile, inputs_hash, rolling, scorecard
from app.jira.settings import task_source
from app.models import (
    AppSetting,
    DpiAssessment,
    DpiDispute,
    DpiMetricOverride,
    DpiPeerFeedback,
    DpiProfile,
    DpiReview,
    DpiRubric,
    DpiSnapshot,
    DpiUserProfile,
    User,
)

SHADOW_KEY = "dpi_shadow"
OPEN = ("draft", "self_review", "lead_review", "calibration")
CLOSED = ("finalized", "locked")
# Deadlines after the month's end (§5): self review N+3, lead N+7, calibration N+10.
DEADLINES = {"self_review": 3, "lead_review": 7, "calibration": 10}
LOCK_AFTER_DAYS = 7
MIN_PEER = 3
MAX_ADJUSTMENT = 10.0


def month_start(day: date) -> date:
    return day.replace(day=1)


def previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def months_back(month: date, n: int) -> date:
    for _ in range(n):
        month = previous_month(month)
    return month


def next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def shadow(session: Session) -> bool:
    row = session.get(AppSetting, SHADOW_KEY)
    return True if row is None else bool(row.value)


def set_shadow(session: Session, on: bool, user_id: int | None) -> None:
    row = session.get(AppSetting, SHADOW_KEY)
    if row is None:
        session.add(AppSetting(key=SHADOW_KEY, value=on, set_by=user_id))
    else:
        row.value, row.set_by = on, user_id


def active_rubric(session: Session) -> Rubric:
    row = session.scalars(select(DpiRubric).where(DpiRubric.active.is_(True))).first()
    return as_rubric(row.version, row.metrics) if row else DEFAULT_RUBRIC


def rubric_version(session: Session, version: str) -> Rubric:
    row = session.get(DpiRubric, version)
    return as_rubric(row.version, row.metrics) if row else active_rubric(session)


def profiles(session: Session) -> dict[str, Profile]:
    rows = session.scalars(select(DpiProfile).order_by(DpiProfile.position)).all()
    return {
        p.key: Profile(p.key, p.label, {k: float(v) for k, v in p.weights.items()}, p.a1_target)
        for p in rows
    }


def profile_of(session: Session, user_id: int, month: date) -> tuple[str, bool]:
    """(profile key, assigned): the profile in force at the month's end; none assigned yet ->
    the default, flagged."""
    row = session.scalars(
        select(DpiUserProfile)
        .where(DpiUserProfile.user_id == user_id, DpiUserProfile.effective_from < next_month(month))
        .order_by(DpiUserProfile.effective_from.desc())
        .limit(1)
    ).first()
    return (row.profile, True) if row else (DEFAULT_PROFILE, False)


def find_review(session: Session, user_id: int, month: date) -> DpiReview | None:
    return session.scalars(
        select(DpiReview).where(DpiReview.user_id == user_id, DpiReview.month == month)
    ).first()


def draft_review(session: Session, user_id: int, month: date) -> DpiReview:
    """An unsaved draft: what a month nobody acted on yet looks like (never added)."""
    key, _assigned = profile_of(session, user_id, month)
    return DpiReview(
        user_id=user_id,
        month=month,
        status="draft",
        profile=key,
        leave_days=Decimal(0),
        adjustment=Decimal(0),
        rubric_version=active_rubric(session).version,
    )


def ensure_review(session: Session, user_id: int, month: date) -> DpiReview:
    """The person's review of the month, created once (two requests at once: one insert wins,
    INSERT … ON CONFLICT DO NOTHING); it pins the rubric in force."""
    review = find_review(session, user_id, month)
    if review is not None:
        return review
    key, _assigned = profile_of(session, user_id, month)
    session.execute(
        insert(DpiReview)
        .values(
            user_id=user_id,
            month=month,
            status="draft",
            profile=key,
            rubric_version=active_rubric(session).version,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "month"])
    )
    found = find_review(session, user_id, month)
    assert found is not None  # noqa: S101 - inserted above or by the concurrent request
    return found


def review_rubric(session: Session, review: DpiReview) -> Rubric:
    """The rubric the review was opened under (a new version applies from the next cycle)."""
    return (
        rubric_version(session, review.rubric_version)
        if review.rubric_version
        else active_rubric(session)
    )


def deadlines(month: date) -> dict[str, date]:
    end = next_month(month) - timedelta(days=1)
    return {step: end + timedelta(days=days) for step, days in DEADLINES.items()}


def overdue(review: DpiReview, today: date) -> bool:
    due = deadlines(review.month).get(review.status)
    return due is not None and today > due


def manager_scores(session: Session, review: DpiReview) -> dict[str, float]:
    return {
        a.metric: float(a.score)
        for a in session.scalars(select(DpiAssessment).where(DpiAssessment.review_id == review.id))
    }


def metric_overrides(session: Session, review: DpiReview) -> dict[str, float]:
    return {
        o.metric: float(o.value)
        for o in session.scalars(
            select(DpiMetricOverride).where(DpiMetricOverride.review_id == review.id)
        )
    }


class Calculator:
    """Scorecards of one month, sharing one facts loader (repo baselines are read once)."""

    def __init__(self, session: Session, month: date, settings: Settings) -> None:
        self.session = session
        self.month = month
        self.settings = settings
        self.tz = ZoneInfo(settings.report_tz)
        self.loader = Loader(
            session,
            month,
            self.tz,
            bot_pattern=settings.bot_account_pattern,
            module_depth=settings.knowledge_module_depth,
            source=task_source(session, settings),
        )
        self.profiles = profiles(session)

    def card(self, review: DpiReview, rubric: Rubric | None = None) -> dict[str, Any]:
        rubric = rubric or review_rubric(self.session, review)
        profile = self.profiles.get(review.profile) or self.profiles.get(DEFAULT_PROFILE)
        if profile is None:  # no profiles at all (a fresh database without the seed)
            profile = Profile(
                DEFAULT_PROFILE, "Middle", {"A": 25, "B": 30, "C": 15, "D": 15, "E": 15}, 28
            )
        facts = self.loader.facts(review.user_id, profile.a1_target, float(review.leave_days or 0))
        auto = compute(facts, [m.key for m in rubric.auto])
        manager = manager_scores(self.session, review)
        overrides = metric_overrides(self.session, review)
        card = scorecard(rubric, profile, auto, manager, overrides, float(review.adjustment or 0))
        card["flags"] = {"split_mrs": split_mrs(facts.mrs)}
        card["contribution"] = facts.contribution
        _profile, assigned = profile_of(self.session, review.user_id, self.month)
        card["profile_assigned"] = assigned
        card["inputs_hash"] = inputs_hash(
            {
                "rubric": rubric.version,
                "profile": profile.key,
                "weights": profile.weights,
                "target": profile.a1_target,
                # What each metric was computed from, not only its value: a change in the
                # evidence (another MR, a defect) makes a new snapshot even at the same score.
                "metrics": {
                    k: {
                        "value": v["value"],
                        "detail": v.get("detail"),
                        "evidence": sorted(
                            f"{e.get('kind')}:{e.get('id') or e.get('ref')}"
                            for e in v.get("evidence") or []
                        ),
                    }
                    for k, v in card["metrics"].items()
                },
                "adjustment": float(review.adjustment or 0),
            }
        )
        return card


def snapshot(
    session: Session, review: DpiReview, card: dict[str, Any], kind: str = "auto"
) -> DpiSnapshot:
    """Keep the card unless the latest snapshot of this kind has the same inputs (§6.1)."""
    latest = session.scalars(
        select(DpiSnapshot)
        .where(
            DpiSnapshot.user_id == review.user_id,
            DpiSnapshot.month == review.month,
            DpiSnapshot.kind == kind,
        )
        .order_by(DpiSnapshot.id.desc())
        .limit(1)
    ).first()
    if latest is not None and latest.inputs_hash == card["inputs_hash"]:
        return latest
    row = DpiSnapshot(
        user_id=review.user_id,
        month=review.month,
        kind=kind,
        rubric_version=card["rubric_version"],
        profile=card["profile"],
        inputs_hash=card["inputs_hash"],
        values=card,
        total=card["total"],
        rating=card["rating"],
        confidence=card["confidence"],
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def history(
    session: Session, user_id: int, month: date, months: int = 6, *, final_only: bool = False
) -> list[dict[str, Any]]:
    """R6: the latest snapshot of each of the last `months` months (final wins over auto);
    `final_only`: finalized months only (what HR may see)."""
    first = months_back(month, months - 1)
    query = select(DpiSnapshot).where(
        DpiSnapshot.user_id == user_id,
        DpiSnapshot.month >= first,
        DpiSnapshot.month <= month,
    )
    if final_only:
        query = query.where(DpiSnapshot.kind == "final")
    rows = session.scalars(query.order_by(DpiSnapshot.month, DpiSnapshot.id)).all()
    best: dict[date, DpiSnapshot] = {}
    for row in rows:
        current = best.get(row.month)
        if current is None or row.kind == "final" or current.kind != "final":
            best[row.month] = row
    out = []
    m = first
    while m <= month:
        found = best.get(m)
        out.append(
            {
                "month": m.isoformat(),
                "total": float(found.total) if found and found.total is not None else None,
                "rating": found.rating if found else None,
                "confidence": found.confidence if found else None,
                "kind": found.kind if found else None,
                "groups": {g: v.get("ratio") for g, v in (found.values.get("groups") or {}).items()}
                if found
                else {},
            }
        )
        m = next_month(m)
    return out


def rolling_of(trend: list[dict[str, Any]], current: float | None) -> dict[str, Any]:
    """Earlier months count when finalized only: a provisional total lacks the lead's 30."""
    previous = [t["total"] if t["kind"] == "final" else None for t in trend[:-1]][::-1]
    return rolling([current, *([*previous, None, None])[:2]])


def can_assess_complete(session: Session, review: DpiReview, rubric: Rubric) -> bool:
    wanted = {m.key for m in rubric.metrics if m.source == "manager"}
    return wanted <= set(manager_scores(session, review))


def b5_suggestions(calc: "Calculator", user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """§3 B5: the MRs of the month most worth judging the design of: most critical/high
    findings first, then the largest, then the lowest review score."""
    from app.models import MergeRequest, Review, ReviewFinding

    loader = calc.loader
    accounts = loader.accounts(user_id)
    if not accounts:
        return []
    mrs = list(
        calc.session.scalars(
            select(MergeRequest).where(
                MergeRequest.author_account_id.in_(accounts),
                MergeRequest.state == "merged",
                MergeRequest.merged_at >= loader.lo,
                MergeRequest.merged_at < loader.hi,
            )
        )
    )
    if not mrs:
        return []
    ids = [m.id for m in mrs]
    severe: dict[int, int] = defaultdict(int)
    score: dict[int, float] = {}
    for mr_id, review_score, severity in calc.session.execute(
        select(Review.mr_id, Review.score, ReviewFinding.severity)
        .outerjoin(ReviewFinding, ReviewFinding.review_id == Review.id)
        .where(Review.mr_id.in_(ids), Review.status == "done")
    ):
        if mr_id is None:
            continue
        if review_score is not None:
            score[int(mr_id)] = min(score.get(int(mr_id), 100.0), float(review_score))
        if severity in ("critical", "high"):
            severe[int(mr_id)] += 1

    def size(m: MergeRequest) -> int:
        return int(m.additions or 0) + int(m.deletions or 0)

    ranked = sorted(mrs, key=lambda m: (-severe[m.id], -size(m), score.get(m.id, 100.0)))
    out = []
    for m in ranked[:limit]:
        why = []
        if severe[m.id]:
            why.append(f"{severe[m.id]} finding critical/high")
        why.append(f"{size(m)} dòng")
        if m.id in score:
            why.append(f"điểm review {score[m.id]:.0f}")
        out.append(
            {
                "kind": "mr",
                "id": m.id,
                "ref": f"!{m.iid}",
                "label": f"{m.title or ''} · {', '.join(why)}".strip(" ·"),
                "url": loader.url(m.project_id, "merge_requests", m.iid),
            }
        )
    return out


def self_items(session: Session, user_id: int, month: date) -> list[dict[str, Any]]:
    """The self-reported items of the month (the evidence pack, spec 20 §5.4) and their approval."""
    from app.models import EvidenceItem, EvidencePack

    pack = session.scalars(
        select(EvidencePack).where(
            EvidencePack.user_id == user_id, EvidencePack.period_start == month
        )
    ).first()
    if pack is None:
        return []
    return [
        {
            "id": i.id,
            "kind": i.kind,
            "title": i.title,
            "note": i.note,
            "ref": i.ref,
            "approved": i.approved,
            "counted": i.kind not in ("mr", "commit"),
        }
        for i in session.scalars(
            select(EvidenceItem).where(EvidenceItem.pack_id == pack.id).order_by(EvidenceItem.id)
        )
    ]


AXES = {
    "A": "Kết quả công việc",
    "B": "Năng lực kỹ thuật (chất lượng code)",
    "C": "Độ chín trong giao việc",
    "D": "Hợp tác",
    "E": "Ownership & phát triển",
}
READY_ROLLING = 85.0
NEAR_ROLLING = 80.0
READY_FLOOR = 0.7


def growth(trend: list[dict[str, Any]], rolling_value: float | None) -> dict[str, Any]:
    """R6 Competency & Growth: each axis (a group's share achieved) averaged over the last
    three months with data, its trend over the window, and promotion readiness: rolling ≥ 85
    over finalized months with no axis under 70% -> ready; rolling ≥ 80 -> getting there."""
    axes: list[dict[str, Any]] = []
    for g, label in AXES.items():
        series = [t["groups"].get(g) for t in trend if t["groups"].get(g) is not None]
        recent = series[-3:]
        axes.append(
            {
                "group": g,
                "label": label,
                "level": round(sum(recent) / len(recent), 4) if recent else None,
                "trend": round(series[-1] - series[0], 4) if len(series) >= 2 else None,
            }
        )
    finals = [t for t in trend if t.get("kind") == "final" and t.get("total") is not None]
    floor_ok = all(a["level"] is None or a["level"] >= READY_FLOOR for a in axes)
    if rolling_value is None or len(finals) < 2:
        readiness, why = "unknown", "Cần ít nhất 2 tháng đã chốt"
    elif rolling_value >= READY_ROLLING and floor_ok:
        readiness, why = (
            "ready",
            f"Rolling {rolling_value:.0f} ≥ {READY_ROLLING:.0f}, không trục nào dưới 70%",
        )
    elif rolling_value >= NEAR_ROLLING:
        readiness, why = (
            "near",
            f"Rolling {rolling_value:.0f}; cần ≥ {READY_ROLLING:.0f} và mọi trục ≥ 70%",
        )
    else:
        readiness, why = "not_yet", f"Rolling {rolling_value:.0f} < {NEAR_ROLLING:.0f}"
    return {"axes": axes, "promotion_readiness": readiness, "why": why}


def peer_summary(session: Session, user_id: int, month: date, *, named: bool) -> dict[str, Any]:
    rows = session.scalars(
        select(DpiPeerFeedback).where(
            DpiPeerFeedback.to_user_id == user_id, DpiPeerFeedback.month == month
        )
    ).all()
    if not named and len(rows) < MIN_PEER:
        # Too few to stay anonymous: the person learns only that feedback exists.
        return {"count": len(rows), "average": None, "items": [], "hidden": bool(rows)}
    names = {}
    if named and rows:
        names = {
            u.id: u.full_name or u.primary_email
            for u in session.scalars(
                select(User).where(User.id.in_({r.from_user_id for r in rows}))
            )
        }
    return {
        "count": len(rows),
        "average": round(sum(r.score for r in rows) / len(rows), 2) if rows else None,
        "items": [
            {
                "score": r.score,
                "comment": r.comment,
                **(
                    {"from": names.get(r.from_user_id), "from_user_id": r.from_user_id}
                    if named
                    else {}
                ),
            }
            for r in rows
        ],
    }


def open_disputes(session: Session, review: DpiReview) -> list[DpiDispute]:
    return list(
        session.scalars(
            select(DpiDispute).where(DpiDispute.review_id == review.id).order_by(DpiDispute.id)
        )
    )


def run_month(session: Session, month: date, settings: Settings) -> dict[str, int]:
    """§5 draft: on the 1st, every person active in the month gets their review (moved to
    self_review) and an automatic snapshot; reviews already further along are only re-scored."""
    tz = ZoneInfo(settings.report_tz)
    people = [
        u
        for u in active_people(session, month, tz, task_source(session, settings))
        if session.get(User, u) is not None and session.get(User, u).active is not False  # type: ignore[union-attr]
    ]
    calc = Calculator(session, month, settings)
    created = scored = 0
    for user_id in people:
        existed = session.scalars(
            select(DpiReview.id).where(DpiReview.user_id == user_id, DpiReview.month == month)
        ).first()
        review = ensure_review(session, user_id, month)
        if existed is None:
            created += 1
        if review.status in CLOSED:
            continue
        if review.status == "draft":
            review.status = "self_review"
        snapshot(session, review, calc.card(review))
        scored += 1
    return {"people": len(people), "created": created, "scored": scored}


def lock_due(session: Session, today: date) -> int:
    """§5: a finalized review locks LOCK_AFTER_DAYS after it was finalized."""
    cutoff = datetime.combine(today - timedelta(days=LOCK_AFTER_DAYS), datetime.min.time(), UTC)
    rows = session.scalars(
        select(DpiReview).where(DpiReview.status == "finalized", DpiReview.finalized_at < cutoff)
    ).all()
    for review in rows:
        review.status = "locked"
        review.locked_at = datetime.now(UTC)
    return len(rows)


__all__ = [
    "CLOSED",
    "MANAGER_METRICS",
    "MAX_ADJUSTMENT",
    "OPEN",
    "Calculator",
    "active_rubric",
    "b5_suggestions",
    "can_assess_complete",
    "deadlines",
    "ensure_review",
    "history",
    "lock_due",
    "month_start",
    "overdue",
    "peer_summary",
    "previous_month",
    "profile_of",
    "profiles",
    "rolling_of",
    "run_month",
    "set_shadow",
    "shadow",
    "snapshot",
]
