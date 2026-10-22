"""Process metrics from the domain layer (docs/specs/08 §2, task 4.2), like DevLake's
PR Review Time, PR Size and Build Success Rate."""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from app.models import MergeRequest, Pipeline

# Pipelines that ran to a verdict; canceled/skipped/manual say nothing about build health.
FINISHED_PIPELINE = {"success", "failed"}


@dataclass(frozen=True)
class ProcessMetrics:
    mr_count: int
    avg_review_time_h: float | None
    avg_mr_size: int | None
    avg_review_rounds: float | None
    build_success_rate: float | None  # percent 0-100


def review_time_hours(mr: MergeRequest) -> float | None:
    if mr.first_review_at is None or mr.created_at_src is None:
        return None
    return max(0.0, (mr.first_review_at - mr.created_at_src).total_seconds() / 3600)


def build_success_rate(pipelines: Sequence[Pipeline]) -> float | None:
    finished = [p for p in pipelines if p.status in FINISHED_PIPELINE]
    if not finished:
        return None
    return round(100 * sum(p.status == "success" for p in finished) / len(finished), 2)


def process_metrics(mrs: Sequence[MergeRequest], pipelines: Sequence[Pipeline]) -> ProcessMetrics:
    """`mrs`: MRs merged in the period; `pipelines`: pipelines that ran on those MRs' branches."""
    times = [t for t in map(review_time_hours, mrs) if t is not None]
    sizes = [
        (mr.additions or 0) + (mr.deletions or 0)
        for mr in mrs
        if mr.additions is not None or mr.deletions is not None
    ]
    rounds = [mr.review_rounds for mr in mrs if mr.review_rounds is not None]
    return ProcessMetrics(
        mr_count=len(mrs),
        avg_review_time_h=round(fmean(times), 2) if times else None,
        avg_mr_size=round(fmean(sizes)) if sizes else None,
        avg_review_rounds=round(fmean(rounds), 2) if rounds else None,
        build_success_rate=build_success_rate(pipelines),
    )
