"""docs/specs/20 §4.7, §4.8, §7, and the §3.2 wall between insights and scoring."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.insights.confidence import Confidence, freshness, sample, unmapped
from app.insights.flow import Responder, flow
from app.insights.pipelines import PipelineFact, summarise
from app.insights.review_load import review_load
from tests.insights.test_flow import AUTHOR, OTHER, REVIEWER, REVIEWER_USER, mr, note

ROOT = Path(__file__).resolve().parents[2]


def test_reviewer_counted_once_per_mr_and_author_never() -> None:
    flows = [
        flow(mr(note(2), note(3), note(4, AUTHOR, "fixed"))),
        flow(mr(note(5))),
        flow(mr(note(5, OTHER, "nit", user=None))),
        flow(mr()),
    ]
    load = review_load(flows)
    assert load.merged == 4 and load.reviewed == 3
    assert load.reviewers == [(Responder(REVIEWER_USER), 2), (Responder(None), 1)]
    assert load.top_reviewer_share == 50.0
    assert not load.concentrated
    reviewer, author, n = load.pairs[0]
    assert (reviewer.user_id, author.user_id, n) == (REVIEWER_USER, 10, 2)


def test_one_reviewer_on_most_mrs_is_concentrated() -> None:
    flows = [flow(mr(note(2))) for _ in range(4)] + [flow(mr(note(2, OTHER, "x", user=None)))]
    load = review_load(flows)
    assert load.top_reviewer_share == 80.0 and load.concentrated


def test_review_load_of_nothing() -> None:
    load = review_load([])
    assert load.top_reviewer_share is None and not load.concentrated
    assert load.as_dict()["reviewers_count"] == 0
    assert REVIEWER  # keep the import meaningful for readers


def test_pipelines_ignore_unfinished_and_split_branches() -> None:
    facts = [
        PipelineFact("success", 600, True),
        PipelineFact("failed", 300, True),
        PipelineFact("success", 120, False),
        PipelineFact("canceled", 5, False),
        PipelineFact("running", None, True),
    ]
    s = summarise(facts, min_sample=2)
    assert (s["total"], s["finished"], s["success"], s["failed"]) == (5, 3, 2, 1)
    assert s["success_rate"] == 66.7
    assert s["duration_min"] == {"n": 3, "median": 5.0, "p75": 7.5}
    assert s["default_branch"]["success_rate"] == 50.0
    assert s["other_branches"]["success_rate"] == 100.0
    assert s["other_branches"]["duration_min"]["median"] is None  # 1 < min_sample


def test_confidence_takes_the_worst_level() -> None:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    c = Confidence()
    oldest = freshness(
        c,
        [("g/a", now - timedelta(hours=1)), ("g/b", now - timedelta(hours=72)), ("g/c", None)],
        now,
        48,
    )
    assert oldest == now - timedelta(hours=72)
    assert c.level == "low"
    assert c.reasons[0]["repos"] == ["g/b", "g/c"]
    sample(c, 3, 5)
    assert c.as_dict()["level"] == "insufficient"
    unmapped(c, 1, 10)  # 10 % is fine
    assert len(c.reasons) == 2
    unmapped(c, 3, 10)
    assert c.reasons[-1]["code"] == "unmapped_authors"
    assert c.as_dict()["level"] == "insufficient"


def test_confidence_ok_by_default() -> None:
    c = Confidence()
    sample(c, 5, 5)
    freshness(c, [], datetime(2026, 11, 3, tzinfo=UTC), 48)
    assert c.as_dict() == {"level": "ok", "reasons": []}


def _imports(package: str) -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "app" / package).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
    return found


def test_insights_and_scoring_never_import_each_other() -> None:
    assert not any(m.startswith("app.scoring") for m in _imports("insights"))
    assert not any(m.startswith("app.insights") for m in _imports("scoring"))
