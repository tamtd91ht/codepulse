"""Source-independent collector interface; GitLab today, GitHub later (docs/specs/14, task 6.4)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Project


@dataclass
class CollectResult:
    window_start: datetime | None
    started_at: datetime
    counts: dict[str, int] = field(default_factory=dict)


class Collector(Protocol):
    def collect(self, session: Session, project: Project, since: datetime | None) -> CollectResult:
        """Fetch everything changed since `since` (None = backfill) into the raw layer."""
        ...
