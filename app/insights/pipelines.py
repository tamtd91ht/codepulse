"""Pipeline health of a set of repos (docs/specs/20 §4.8)."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.insights.stats import share, spread

FINISHED = {"success", "failed"}  # canceled / skipped / manual / running say nothing of health


@dataclass(frozen=True)
class PipelineFact:
    status: str | None
    duration_sec: int | None
    default_branch: bool


def _group(items: list[PipelineFact], min_sample: int) -> dict[str, Any]:
    finished = [p for p in items if p.status in FINISHED]
    success = sum(1 for p in finished if p.status == "success")
    minutes = spread(
        (p.duration_sec / 60.0 if p.duration_sec is not None else None for p in finished),
        min_sample,
    )
    return {
        "total": len(items),
        "finished": len(finished),
        "success": success,
        "failed": len(finished) - success,
        "success_rate": share(success, len(finished)),
        "duration_min": minutes,
    }


def summarise(pipelines: Iterable[PipelineFact], min_sample: int) -> dict[str, Any]:
    items = list(pipelines)
    return {
        **_group(items, min_sample),
        "default_branch": _group([p for p in items if p.default_branch], min_sample),
        "other_branches": _group([p for p in items if not p.default_branch], min_sample),
    }
