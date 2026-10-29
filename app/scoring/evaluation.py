"""Per-member evaluation over a date range (the Thành viên page, requested by the user
2026-10-27: "xem member đó đã được review bao nhiêu lần, kết quả, chấm điểm ra sao").

Unlike app/scoring/aggregate.py, which stores one row per finished ISO week / month, this
reads the reviews of any range (including the running week) and groups them live by person,
and by repo for one person. Findings count the way the score counts them (docs/specs/08 §2,
19 §4.4): never a disputed or refuted one; from rubric v4 only verified ones and the unverified
ones that are confident enough. Pure: the API router loads the facts, this module groups them.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.scoring.formula import (
    MIN_CONFIDENCE_UNVERIFIED,
    ScoredFinding,
    counts_for_score,
    rule_for,
)
from app.scoring.verdict import VERDICTS

SEVERITIES = ("critical", "high", "medium", "low", "info")
# Below this LOC-weighted average, or with any critical, a row is flagged "cần hỗ trợ".
ATTENTION_SCORE = 65.0


@dataclass
class FindingFacts:
    severity: str
    category: str
    confidence: float = 1.0
    status: str = "open"
    verified: str = "skipped"


@dataclass
class ReviewFacts:
    id: int
    user_id: int
    finished_at: datetime
    score: float | None
    loc: int
    target_type: str  # mr | commit
    verdict: str | None
    rubric_version: str | None
    project_id: int | None = None
    project_path: str | None = None
    # The week it counts in (Review.counted_at: the change's time); finished_at when not given.
    counted_at: datetime | None = None
    findings: list[FindingFacts] = field(default_factory=list)


@dataclass
class Bucket:
    """Reviews grouped by anything: a person, a repo of a person, a week."""

    reviews: int = 0
    mrs: int = 0
    commits: int = 0
    loc: int = 0
    weighted_score: float = 0.0
    weight: int = 0
    min_score: float | None = None
    last_review_at: datetime | None = None
    verdicts: Counter[str] = field(default_factory=Counter)
    severity: Counter[str] = field(default_factory=Counter)
    categories: Counter[str] = field(default_factory=Counter)
    disputed: int = 0  # findings a lead accepted as false positive
    rejected: int = 0  # findings the verification refuted
    repos: set[int] = field(default_factory=set)

    def add(self, review: ReviewFacts) -> None:
        self.reviews += 1
        if review.target_type == "mr":
            self.mrs += 1
        else:
            self.commits += 1
        self.loc += review.loc
        if review.score is not None:
            # LOC-weighted like docs/specs/08 §2: a 5-line commit must not weigh as much as a
            # 500-line MR.
            w = max(review.loc, 1)
            self.weighted_score += review.score * w
            self.weight += w
            self.min_score = (
                review.score if self.min_score is None else min(self.min_score, review.score)
            )
        if self.last_review_at is None or review.finished_at > self.last_review_at:
            self.last_review_at = review.finished_at
        if review.verdict in VERDICTS:
            self.verdicts[review.verdict] += 1
        if review.project_id is not None:
            self.repos.add(review.project_id)
        rule = rule_for(review.rubric_version)
        for f in review.findings:
            if f.status == "false_positive":
                self.disputed += 1
            elif f.verified == "rejected":
                self.rejected += 1
            scored = ScoredFinding(f.severity, f.confidence, f.status, f.verified)
            if counts_for_score(scored, rule, MIN_CONFIDENCE_UNVERIFIED):
                self.severity[f.severity] += 1
                self.categories[f.category] += 1

    @property
    def avg_score(self) -> float | None:
        return round(self.weighted_score / self.weight, 1) if self.weight else None

    @property
    def findings(self) -> int:
        return sum(self.severity[s] for s in SEVERITIES)

    @property
    def severe(self) -> int:
        return self.severity["critical"] + self.severity["high"]

    @property
    def findings_per_100_loc(self) -> float | None:
        return round(100.0 * self.findings / self.loc, 2) if self.loc else None

    @property
    def pass_rate(self) -> float | None:
        judged = sum(self.verdicts.values())
        return round(100.0 * self.verdicts["approve"] / judged, 1) if judged else None

    @property
    def needs_attention(self) -> bool:
        avg = self.avg_score
        return self.severity["critical"] > 0 or (avg is not None and avg < ATTENTION_SCORE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reviews": self.reviews,
            "mrs": self.mrs,
            "commits": self.commits,
            "loc": self.loc,
            "repos": len(self.repos),
            "avg_score": self.avg_score,
            "min_score": self.min_score,
            "last_review_at": self.last_review_at,
            "verdicts": {v: self.verdicts[v] for v in VERDICTS},
            "pass_rate": self.pass_rate,
            **{s: self.severity[s] for s in SEVERITIES},
            "findings": self.findings,
            "findings_per_100_loc": self.findings_per_100_loc,
            "disputed": self.disputed,
            "rejected": self.rejected,
            "needs_attention": self.needs_attention,
        }


def week_starts(start: date, end: date) -> list[date]:
    """The Mondays of the ISO weeks that touch [start, end]."""
    first = start - timedelta(days=start.weekday())
    weeks: list[date] = []
    while first <= end:
        weeks.append(first)
        first += timedelta(days=7)
    return weeks


def _week_of(review: ReviewFacts, tz: ZoneInfo) -> date:
    day = (review.counted_at or review.finished_at).astimezone(tz).date()
    return day - timedelta(days=day.weekday())


def evaluate_members(
    reviews: list[ReviewFacts], start: date, end: date, tz: ZoneInfo
) -> dict[str, Any]:
    """Totals, the weekly axis and one row per person (people without reviews are not rows:
    the router adds those it wants listed)."""
    weeks = week_starts(start, end)
    people: dict[int, Bucket] = defaultdict(Bucket)
    by_week: dict[int, dict[date, Bucket]] = defaultdict(lambda: defaultdict(Bucket))
    total = Bucket()
    for review in reviews:
        people[review.user_id].add(review)
        by_week[review.user_id][_week_of(review, tz)].add(review)
        total.add(review)
    members = [
        {
            "user_id": user_id,
            **bucket.as_dict(),
            "trend": [
                by_week[user_id][w].avg_score if w in by_week[user_id] else None for w in weeks
            ],
        }
        for user_id, bucket in people.items()
    ]
    members.sort(key=lambda m: (m["avg_score"] is None, m["avg_score"] or 0.0, -m["reviews"]))
    return {
        "totals": {**total.as_dict(), "members": len(people)},
        "weeks": weeks,
        "members": members,
    }


def evaluate_member(
    reviews: list[ReviewFacts], start: date, end: date, tz: ZoneInfo
) -> dict[str, Any]:
    """One person: the summary, per-repo rows, verdict / severity / category mixes, weekly trend."""
    weeks = week_starts(start, end)
    total = Bucket()
    repos: dict[int | None, Bucket] = defaultdict(Bucket)
    paths: dict[int | None, str | None] = {}
    by_week: dict[date, Bucket] = defaultdict(Bucket)
    for review in reviews:
        total.add(review)
        repos[review.project_id].add(review)
        paths.setdefault(review.project_id, review.project_path)
        by_week[_week_of(review, tz)].add(review)
    by_repo = [
        {"project_id": project_id, "path": paths[project_id], **bucket.as_dict()}
        for project_id, bucket in repos.items()
    ]
    by_repo.sort(key=lambda r: (-r["reviews"], r["path"] or ""))
    return {
        "summary": total.as_dict(),
        "by_repo": by_repo,
        "categories": [{"category": c, "count": n} for c, n in total.categories.most_common()],
        "weeks": weeks,
        "trend": [by_week[w].avg_score if w in by_week else None for w in weeks],
        "trend_reviews": [by_week[w].reviews if w in by_week else 0 for w in weeks],
    }
