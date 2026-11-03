"""docs/specs/20 §4.2-4.6 and §15: one case per rule of the flow definitions."""

from datetime import UTC, datetime, timedelta

import pytest

from app.insights.flow import (
    BOT_MARKER,
    MrFacts,
    Note,
    flow,
    is_approval_note,
    is_draft_title,
    is_ready_note,
    size_bucket,
    summarise,
    waiting,
)
from app.insights.stats import quantile, spread

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
AUTHOR, REVIEWER, OTHER = 1, 2, 3  # account ids
AUTHOR_USER, REVIEWER_USER = 10, 20


def h(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def note(
    at: float,
    account: int | None = REVIEWER,
    body: str = "LGTM",
    system: bool = False,
    user: int | None = None,
) -> Note:
    if user is None:
        user = {AUTHOR: AUTHOR_USER, REVIEWER: REVIEWER_USER}.get(account or -1)
    return Note(account, user, body, system, h(at))


def mr(
    *notes: Note,
    first_commit: float | None = -10,
    opened: float | None = 0,
    merged: float | None = 30,
    state: str = "merged",
    title: str = "Add login",
    additions: int | None = 120,
    deletions: int | None = 30,
    rounds: int | None = 1,
) -> MrFacts:
    return MrFacts(
        id=1,
        project_id=1,
        iid=7,
        title=title,
        author_account_id=AUTHOR,
        author_user_id=AUTHOR_USER,
        state=state,
        target_branch="main",
        opened_at=h(opened) if opened is not None else None,
        merged_at=h(merged) if merged is not None else None,
        additions=additions,
        deletions=deletions,
        review_rounds=rounds,
        first_commit_at=h(first_commit) if first_commit is not None else None,
        notes=notes,
    )


def test_four_stages_of_a_plain_mr() -> None:
    f = flow(mr(note(6)))
    assert (f.coding_h, f.pickup_h, f.review_h, f.time_to_merge_h) == (10, 6, 24, 30)
    assert f.reviewed


def test_draft_then_ready_starts_pickup_at_ready() -> None:
    f = flow(mr(note(4, AUTHOR, "marked this merge request as **ready**", True), note(6)))
    assert f.ready_at == h(4)
    assert f.pickup_h == 2


def test_legacy_wip_note_counts_as_ready() -> None:
    assert is_ready_note("unmarked as a **Work In Progress**")
    assert is_ready_note("Marked this merge request as **ready**.")
    assert not is_ready_note("marked this merge request as **draft**")


def test_approval_without_comment_is_a_response() -> None:
    f = flow(mr(note(5, REVIEWER, "approved this merge request", True)))
    assert f.first_response_at == h(5)
    assert is_approval_note("approved this merge request")


def test_other_system_notes_are_not_responses() -> None:
    f = flow(mr(note(5, REVIEWER, "added 1 commit", True)))
    assert not f.reviewed


def test_authors_own_notes_are_not_responses() -> None:
    f = flow(mr(note(2, AUTHOR, "pls review"), note(8)))
    assert f.first_response_at == h(8)


def test_author_from_another_account_of_same_person_is_not_a_response() -> None:
    f = flow(mr(note(2, OTHER, "self note", user=AUTHOR_USER)))
    assert not f.reviewed


def test_rebased_commit_after_open_counts_coding_as_zero() -> None:
    f = flow(mr(note(6), first_commit=3))
    assert f.coding_h == 0


def test_response_before_ready_counts_pickup_as_zero() -> None:
    f = flow(mr(note(1), note(5, AUTHOR, "marked this merge request as ready", True)))
    assert f.pickup_h == 0


def test_mr_merged_without_review() -> None:
    f = flow(mr())
    assert not f.reviewed
    assert f.pickup_h is None and f.review_h is None
    assert f.time_to_merge_h == 30


def test_comment_after_merge_reviews_nothing() -> None:
    f = flow(mr(note(40)))
    assert not f.reviewed


def test_missing_marks_give_none() -> None:
    f = flow(mr(note(6), merged=None, first_commit=None, state="opened"))
    assert f.coding_h is None and f.review_h is None and f.time_to_merge_h is None
    assert f.pickup_h == 6


def test_responders_are_people_once() -> None:
    f = flow(mr(note(5), note(6), note(7, OTHER, "nit", user=None)))
    assert {r.user_id for r in f.responders} == {REVIEWER_USER, None}


@pytest.mark.parametrize(
    ("size", "bucket"),
    [
        (0, "XS"),
        (49, "XS"),
        (50, "S"),
        (199, "S"),
        (499, "M"),
        (999, "L"),
        (1000, "XL"),
        (None, None),
    ],
)
def test_size_buckets(size: int | None, bucket: str | None) -> None:
    assert size_bucket(size) == bucket


def test_size_needs_a_diff() -> None:
    assert mr(additions=None, deletions=None).size is None
    assert mr(additions=3, deletions=None).size == 3


@pytest.mark.parametrize(
    ("title", "draft"),
    [
        ("Draft: x", True),
        ("[Draft] x", True),
        ("WIP: x", True),
        ("(WIP) x", True),
        ("Drafting", False),
        (None, False),
    ],
)
def test_draft_titles(title: str | None, draft: bool) -> None:
    assert is_draft_title(title) is draft


def test_quantiles_even_and_odd() -> None:
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([1, 2, 3], 0.5) == 2
    assert quantile([1, 2, 3, 4, 5], 0.75) == 4
    assert quantile([None, 7], 0.5) == 7
    assert quantile([], 0.5) is None


def test_spread_hides_median_below_min_sample() -> None:
    assert spread([1.0, 2.0], 3) == {"n": 2, "median": None, "p75": None}
    assert spread([1.0, 2.0, None, 3.0], 3) == {"n": 3, "median": 2.0, "p75": 2.5}


def test_summarise() -> None:
    flows = [
        flow(mr(note(6))),
        flow(mr(note(2), additions=900, deletions=200)),
        flow(mr()),
        flow(mr(note(10), additions=None, deletions=None, rounds=3)),
    ]
    s = summarise(flows, min_sample=2)
    assert s["merged"] == 4
    assert s["merged_without_review"] == 1
    assert s["merged_without_review_rate"] == 25.0
    assert s["stages"]["pickup"] == {"n": 3, "median": 6.0, "p75": 8.0}
    assert s["stages"]["time_to_merge"]["n"] == 4
    assert s["size"]["buckets"] == {"XS": 0, "S": 2, "M": 0, "L": 0, "XL": 1}
    assert s["size"]["xl_share"] == 33.3
    assert s["review_rounds"]["median"] == 1.0


def test_summarise_empty() -> None:
    s = summarise([], min_sample=5)
    assert s["merged"] == 0 and s["merged_without_review_rate"] is None
    assert s["size"]["xl_share"] is None


def test_waiting_lists_open_unanswered_non_draft_longest_first() -> None:
    now = h(100)
    young = mr(opened=90, merged=None, state="opened")
    old = mr(opened=10, merged=None, state="opened")
    answered = mr(note(20), opened=10, merged=None, state="opened")
    draft = mr(opened=0, merged=None, state="opened", title="Draft: wip")
    done = mr()
    ready_late = mr(
        note(80, AUTHOR, "marked this merge request as ready", True),
        opened=0,
        merged=None,
        state="opened",
    )
    got = waiting([young, old, answered, draft, done, ready_late], now)
    assert [(m.opened_at, age) for m, age in got] == [(h(10), 90.0), (h(0), 20.0), (h(90), 10.0)]


def test_codepulse_review_comment_is_not_a_response() -> None:
    from app.review.comment import MARKER

    assert MARKER == BOT_MARKER  # the copy must follow the poster
    bot = note(1, OTHER, BOT_MARKER + "\n## CodePulse review", user=None)
    f = flow(mr(bot, note(6)))
    assert f.first_response_at == h(6)
    assert not flow(mr(bot)).reviewed


def test_waiting_counts_from_the_latest_ready_mark() -> None:
    back_to_ready = mr(
        note(1, AUTHOR, "marked this merge request as ready", True),
        note(2, AUTHOR, "marked this merge request as draft", True),
        note(50, AUTHOR, "marked this merge request as ready", True),
        opened=0,
        merged=None,
        state="opened",
    )
    [(_, age)] = waiting([back_to_ready], h(60))
    assert age == 10.0
