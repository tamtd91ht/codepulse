"""Flow of one merge request: the four stages and who had the ball (docs/specs/20 §4.2-4.6).

Pure functions on plain facts, so every rule of §4 has a table test. The API loads the facts
(app/api/routers/insights.py) and never computes a stage itself.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.insights.stats import share, spread

Stage = Literal["coding", "pickup", "review", "time_to_merge"]
STAGES: tuple[Stage, ...] = ("coding", "pickup", "review", "time_to_merge")

# §4.4 size groups: upper bounds (exclusive) of XS, S, M, L; XL is everything above.
SIZE_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("XS", 50),
    ("S", 200),
    ("M", 500),
    ("L", 1000),
    ("XL", None),
)

# §4.2.1, compared on the note body lower-cased and without markdown bold.
_READY = {"marked this merge request as ready", "unmarked as a work in progress"}
_APPROVED = {"approved this merge request"}
# The summary CodePulse itself posts on an MR (app/review/comment.py MARKER): a bot's note is
# nobody's review (§4.2). Kept as a copy so insights stay apart from the review code.
BOT_MARKER = "<!-- codepulse-review -->"
_DRAFT_TITLE = re.compile(r"^\s*(\[?(draft|wip)\]?\s*:|\[(draft|wip)\]|\((draft|wip)\))", re.I)


def _plain(body: str) -> str:
    return body.replace("**", "").strip().rstrip(".").lower()


def is_ready_note(body: str) -> bool:
    return _plain(body) in _READY


def is_approval_note(body: str) -> bool:
    return _plain(body) in _APPROVED


def is_bot_note(body: str) -> bool:
    return BOT_MARKER in body


def is_draft_title(title: str | None) -> bool:
    """§4.6 v1 approximation of a draft MR: the title says so."""
    return bool(title and _DRAFT_TITLE.match(title))


@dataclass(frozen=True)
class Note:
    author_account_id: int | None
    author_user_id: int | None
    body: str
    system: bool
    created_at: datetime | None


@dataclass(frozen=True)
class MrFacts:
    """What §4 needs of one MR; times are timezone-aware."""

    id: int
    project_id: int
    iid: int
    title: str | None
    author_account_id: int | None
    author_user_id: int | None
    state: str | None
    target_branch: str | None
    opened_at: datetime | None
    merged_at: datetime | None
    additions: int | None
    deletions: int | None
    review_rounds: int | None
    first_commit_at: datetime | None
    notes: tuple[Note, ...] = ()

    @property
    def size(self) -> int | None:
        if self.additions is None and self.deletions is None:
            return None
        return (self.additions or 0) + (self.deletions or 0)


@dataclass(frozen=True)
class Responder:
    """A non-author who responded, or an MR's author: their person; None = not mapped to one
    (all unmapped accounts are one bucket, §4.7)."""

    user_id: int | None


@dataclass(frozen=True)
class Flow:
    mr: MrFacts
    ready_at: datetime | None
    first_response_at: datetime | None
    coding_h: float | None
    pickup_h: float | None
    review_h: float | None
    time_to_merge_h: float | None
    responders: frozenset[Responder] = field(default_factory=frozenset)

    @property
    def reviewed(self) -> bool:
        return self.first_response_at is not None

    def stage(self, name: Stage) -> float | None:
        value: float | None = getattr(self, f"{name}_h")
        return value


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    """end - start in hours, a negative gap counted as 0 (§4.3); None when a mark is missing."""
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _by_author(mr: MrFacts, note: Note) -> bool:
    if note.author_account_id is not None and note.author_account_id == mr.author_account_id:
        return True
    return note.author_user_id is not None and note.author_user_id == mr.author_user_id


def is_response(mr: MrFacts, note: Note) -> bool:
    """§4.2 first_response_at: a comment, or an approval, by someone else than the author;
    CodePulse's own review comment is not one."""
    if _by_author(mr, note) or (note.author_account_id is None and note.author_user_id is None):
        return False
    if is_bot_note(note.body):
        return False
    return not note.system or is_approval_note(note.body)


def flow(mr: MrFacts) -> Flow:
    """The marks and stages of one MR. For a merged MR only responses up to the merge count:
    a comment after the merge reviewed nothing."""
    ordered = sorted(
        ((n.created_at, n) for n in mr.notes if n.created_at is not None), key=lambda p: p[0]
    )
    ready_at = next((at for at, n in ordered if n.system and is_ready_note(n.body)), None)
    ready_at = ready_at or mr.opened_at
    responses = [
        (at, n)
        for at, n in ordered
        if is_response(mr, n) and (mr.merged_at is None or at <= mr.merged_at)
    ]
    first_response_at = responses[0][0] if responses else None
    return Flow(
        mr=mr,
        ready_at=ready_at,
        first_response_at=first_response_at,
        coding_h=_hours(mr.first_commit_at, mr.opened_at),
        pickup_h=_hours(ready_at, first_response_at),
        review_h=_hours(first_response_at, mr.merged_at),
        time_to_merge_h=_hours(mr.opened_at, mr.merged_at),
        # A mapped person is one responder whatever account they wrote from; every account not
        # mapped to a person is one "unmapped" responder (§4.7).
        responders=frozenset(Responder(n.author_user_id) for _at, n in responses),
    )


def size_bucket(size: int | None) -> str | None:
    if size is None:
        return None
    for name, upper in SIZE_BUCKETS:
        if upper is None or size < upper:
            return name
    return None  # pragma: no cover - the last bucket has no upper bound


def summarise(flows: Sequence[Flow], min_sample: int) -> dict[str, Any]:
    """§4.3-4.5 over merged MRs: counts, median/p75 of each stage, size groups, rounds."""
    merged = len(flows)
    unreviewed = sum(1 for f in flows if not f.reviewed)
    sizes = [f.mr.size for f in flows]
    buckets = {name: 0 for name, _ in SIZE_BUCKETS}
    for s in sizes:
        bucket = size_bucket(s)
        if bucket is not None:
            buckets[bucket] += 1
    sized = sum(buckets.values())
    return {
        "merged": merged,
        "merged_without_review": unreviewed,
        "merged_without_review_rate": share(unreviewed, merged),
        "stages": {name: spread((f.stage(name) for f in flows), min_sample) for name in STAGES},
        "size": {
            **spread((float(s) if s is not None else None for s in sizes), min_sample),
            "buckets": buckets,
            "xl_share": share(buckets["XL"], sized),
        },
        "review_rounds": spread(
            (float(f.mr.review_rounds) if f.mr.review_rounds is not None else None for f in flows),
            min_sample,
        ),
    }


def waiting(mrs: Iterable[MrFacts], now: datetime) -> list[tuple[MrFacts, float]]:
    """§4.6 open, non-draft MRs nobody else has answered yet, with their wait in hours since
    ready, longest first."""
    out: list[tuple[MrFacts, float]] = []
    for mr in mrs:
        if mr.state != "opened" or is_draft_title(mr.title):
            continue
        f = flow(mr)
        if f.reviewed:
            continue
        # From the latest ready mark: an MR set back to draft and ready again waits since then.
        readies = [
            n.created_at
            for n in mr.notes
            if n.system and n.created_at is not None and is_ready_note(n.body)
        ]
        age = _hours(max(readies) if readies else f.ready_at, now)
        if age is not None:
            out.append((mr, round(age, 1)))
    out.sort(key=lambda item: -item[1])
    return out
