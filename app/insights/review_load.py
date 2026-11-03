"""Who reviews whom (docs/specs/20 §4.7). Counts MRs a person responded to, never comments, and
never judges what the comments said (P8)."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.insights.flow import Flow, Responder
from app.insights.stats import share

TOP_PAIRS = 10
# Above this share of the merged MRs, one reviewer is a single point of failure.
CONCENTRATION_WARN = 60.0


@dataclass(frozen=True)
class Load:
    """Review load over a set of merged MRs; reviewers and authors are `Responder` keys (the
    person, or one "unmapped" key for every account not tied to a person)."""

    merged: int
    reviewed: int
    reviewers: list[tuple[Responder, int]]  # most MRs first
    pairs: list[tuple[Responder, Responder, int]]  # (reviewer, author, MRs), top TOP_PAIRS

    @property
    def top_reviewer_share(self) -> float | None:
        return share(self.reviewers[0][1], self.merged) if self.reviewers else None

    @property
    def concentrated(self) -> bool:
        top = self.top_reviewer_share
        return top is not None and top > CONCENTRATION_WARN

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged": self.merged,
            "reviewed": self.reviewed,
            "reviewers_count": len(self.reviewers),
            "top_reviewer_share": self.top_reviewer_share,
            "concentrated": self.concentrated,
        }


def author_of(f: Flow) -> Responder:
    return Responder(f.mr.author_user_id)


def review_load(flows: Sequence[Flow]) -> Load:
    per_reviewer: Counter[Responder] = Counter()
    pairs: Counter[tuple[Responder, Responder]] = Counter()
    for f in flows:
        author = author_of(f)
        for reviewer in f.responders:
            per_reviewer[reviewer] += 1
            pairs[(reviewer, author)] += 1

    def order(key: Responder) -> tuple[bool, int]:
        return (key.user_id is None, key.user_id or 0)

    reviewers = sorted(per_reviewer.items(), key=lambda kv: (-kv[1], order(kv[0])))
    top = sorted(pairs.items(), key=lambda kv: (-kv[1], order(kv[0][0]), order(kv[0][1])))
    return Load(
        merged=len(flows),
        reviewed=sum(1 for f in flows if f.reviewed),
        reviewers=reviewers,
        pairs=[(r, a, n) for (r, a), n in top[:TOP_PAIRS]],
    )
