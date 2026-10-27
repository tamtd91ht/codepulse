"""Per-rule statistics of a repo's review knowledge (docs/specs/19 §5): how often each `rk:`
rule fired, what the verification and the disputes said about it, and how often the agent
declared it applicable without reporting anything.

`aggregate` is pure (unit-tested without a database); `rule_stats` loads the rows of the
window and hands them over. A review counts once it finished: status `done`, or `superseded`
(a later push to the MR replaced it; its findings and disputes are history, never deleted).
Labels:

- `dead`: the agent said the rule applies at least once in the window, yet it never fired.
  The spec counts this over 90 days; with a shorter window the label is only indicative, so
  the caller should present 90 days for it (the dashboard offers 30/90).
- `noisy`: `(rejected + disputes_accepted) / fired >= 0.5` with at least two firings. A
  finding that is both rejected and successfully disputed is one noisy finding, not two.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import Commit, FindingDispute, MergeRequest, Review, ReviewFinding

RULE_PREFIX = "rk:"
DEAD_WINDOW_DAYS = 90
NOISY_RATIO = 0.5
NOISY_MIN_FIRED = 2
VERIFIED_STATES = ("confirmed", "downgraded", "rejected", "unverified")
FINISHED_STATUSES = ("done", "superseded")
MAX_DAYS = 365


@dataclass(frozen=True)
class FindingRow:
    """One finding that cites a rule: the review it belongs to, the rule, the verification's
    verdict and whether a dispute against it was accepted."""

    review_id: int
    rule_id: str
    verified: str
    dispute_accepted: bool = False


@dataclass(frozen=True)
class ReviewRow:
    """One done review: which rules the agent declared applicable (`applies = true`)."""

    review_id: int
    applied: frozenset[str] = field(default_factory=frozenset)


def normalize_rule_id(value: object) -> str | None:
    """`rk:inv-1` as the findings store it; `inv-1` (as the agent may answer) gets the prefix.
    Rubric and checklist ids are not knowledge rules and yield None."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith(RULE_PREFIX):
        return text if len(text) > len(RULE_PREFIX) else None
    if ":" in text:
        return None
    return RULE_PREFIX + text


def applied_rule_ids(basis: object) -> frozenset[str]:
    """Rules with `applies = true` in a review's basis: `basis.knowledge.applied_rules`
    (spec 19 §4.6) or the earlier flat `basis.applied_rules`; both shapes are plain records."""
    if not isinstance(basis, dict):
        return frozenset()
    knowledge = basis.get("knowledge")
    records = knowledge.get("applied_rules") if isinstance(knowledge, dict) else None
    if not isinstance(records, list):
        records = basis.get("applied_rules")
    if not isinstance(records, list):
        return frozenset()
    ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or not record.get("applies"):
            continue
        rule = normalize_rule_id(record.get("rule_id") or record.get("id"))
        if rule:
            ids.add(rule)
    return frozenset(ids)


def _label(stat: dict[str, Any], noise: int) -> str | None:
    fired = int(stat["fired"])
    if fired == 0:
        return "dead" if stat["applied_not_fired"] > 0 else None
    if fired >= NOISY_MIN_FIRED and noise / fired >= NOISY_RATIO:
        return "noisy"
    return None


def aggregate(reviews: Iterable[ReviewRow], findings: Iterable[FindingRow]) -> list[dict[str, Any]]:
    """Per-rule counters from the rows of one window, most fired first.

    `fired` counts findings; the verification states and `disputes_accepted` split them;
    `applied_not_fired` counts reviews where the rule applied without a finding citing it."""
    stats: dict[str, dict[str, Any]] = {}
    noise: dict[str, int] = {}  # findings rejected by the verification or disputed with success
    fired_in: dict[int, set[str]] = {}

    def stat(rule_id: str) -> dict[str, Any]:
        if rule_id not in stats:
            stats[rule_id] = {
                "rule_id": rule_id,
                "fired": 0,
                "confirmed": 0,
                "downgraded": 0,
                "rejected": 0,
                "unverified": 0,
                "disputes_accepted": 0,
                "applied_not_fired": 0,
                "label": None,
            }
        return stats[rule_id]

    for finding in findings:
        rule_id = normalize_rule_id(finding.rule_id)
        if rule_id is None:
            continue
        row = stat(rule_id)
        row["fired"] += 1
        if finding.verified in VERIFIED_STATES:
            row[finding.verified] += 1
        if finding.dispute_accepted:
            row["disputes_accepted"] += 1
        if finding.dispute_accepted or finding.verified == "rejected":
            noise[rule_id] = noise.get(rule_id, 0) + 1
        fired_in.setdefault(finding.review_id, set()).add(rule_id)

    for review in reviews:
        fired = fired_in.get(review.review_id, set())
        for rule_id in review.applied:
            if rule_id not in fired:
                stat(rule_id)["applied_not_fired"] += 1

    for row in stats.values():
        row["label"] = _label(row, noise.get(row["rule_id"], 0))
    return sorted(
        stats.values(), key=lambda r: (-r["fired"], -r["applied_not_fired"], r["rule_id"])
    )


def _project_reviews(project_id: int, since: datetime) -> Any:
    mr_ids = select(MergeRequest.id).where(MergeRequest.project_id == project_id)
    shas = select(Commit.sha).where(Commit.project_id == project_id)
    return (
        select(Review.id, Review.basis)
        .where(
            Review.status.in_(FINISHED_STATUSES),
            Review.finished_at >= since,
            (Review.mr_id.in_(mr_ids)) | (Review.commit_sha.in_(shas)),
        )
        .order_by(Review.id)
    )


def load_rows(
    session: Session, project_id: int, since: datetime
) -> tuple[list[ReviewRow], list[FindingRow]]:
    """The finished reviews of the repo since `since`, and their rule-citing findings
    with the dispute outcome (a finding's dispute counts when it was accepted)."""
    reviews: list[ReviewRow] = []
    review_ids: list[int] = []
    for review_id, basis in session.execute(_project_reviews(project_id, since)):
        reviews.append(ReviewRow(int(review_id), applied_rule_ids(basis)))
        review_ids.append(int(review_id))
    if not review_ids:
        return reviews, []
    accepted = exists().where(
        FindingDispute.finding_id == ReviewFinding.id, FindingDispute.decision == "accepted"
    )
    rows = session.execute(
        select(ReviewFinding.review_id, ReviewFinding.rule_id, ReviewFinding.verified, accepted)
        .where(
            ReviewFinding.review_id.in_(review_ids),
            ReviewFinding.rule_id.like(f"{RULE_PREFIX}%"),
        )
        .order_by(ReviewFinding.id)
    )
    findings = [
        FindingRow(int(review_id or 0), str(rule_id), str(verified or ""), bool(disputed))
        for review_id, rule_id, verified, disputed in rows
    ]
    return reviews, findings


def rule_stats(
    session: Session, project_id: int, days: int = 30, *, now: datetime | None = None
) -> dict[str, Any]:
    """`{"days", "since", "rules": [...], "reviews": n}` for the repo over the last `days`
    days (1..365). See the module docstring for the labels."""
    days = max(1, min(int(days), MAX_DAYS))
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    reviews, findings = load_rows(session, project_id, since)
    return {
        "days": days,
        "since": since.isoformat(),
        "rules": aggregate(reviews, findings),
        "reviews": len(reviews),
    }


__all__: Sequence[str] = (
    "FindingRow",
    "ReviewRow",
    "aggregate",
    "applied_rule_ids",
    "load_rows",
    "normalize_rule_id",
    "rule_stats",
)
