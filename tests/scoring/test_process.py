from datetime import UTC, datetime, timedelta

from app.models import MergeRequest, Pipeline
from app.scoring.process import process_metrics

T0 = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)


def mr(
    review_after_h: float | None, adds: int | None, dels: int, rounds: int | None
) -> MergeRequest:
    return MergeRequest(
        iid=1,
        created_at_src=T0,
        first_review_at=T0 + timedelta(hours=review_after_h)
        if review_after_h is not None
        else None,
        additions=adds,
        deletions=dels if adds is not None else None,
        review_rounds=rounds,
    )


def test_process_metrics() -> None:
    mrs = [mr(2, 100, 20, 1), mr(5, 300, 80, 3), mr(None, None, 0, None)]
    pipelines = [
        Pipeline(id=1, status="success"),
        Pipeline(id=2, status="failed"),
        Pipeline(id=3, status="success"),
        Pipeline(id=4, status="canceled"),  # not a verdict on the build
    ]
    metrics = process_metrics(mrs, pipelines)
    assert metrics.mr_count == 3
    assert metrics.avg_review_time_h == 3.5  # MRs never reviewed are left out
    assert metrics.avg_mr_size == 250
    assert metrics.avg_review_rounds == 2.0
    assert metrics.build_success_rate == 66.67


def test_empty_period_has_no_averages() -> None:
    metrics = process_metrics([], [Pipeline(id=1, status="skipped")])
    assert metrics == process_metrics([], [])
    assert (metrics.mr_count, metrics.avg_review_time_h, metrics.build_success_rate) == (
        0,
        None,
        None,
    )
