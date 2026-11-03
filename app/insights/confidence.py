"""How much a block of insights can be trusted (docs/specs/20 §7, P5)."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

Level = Literal["ok", "low", "insufficient"]
_RANK: dict[Level, int] = {"ok": 0, "low": 1, "insufficient": 2}
# Above this share of MRs whose author is not mapped to a person, per-person blocks are "low".
UNMAPPED_WARN = 20.0


@dataclass
class Confidence:
    level: Level = "ok"
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def add(self, level: Level, code: str, message: str, **detail: Any) -> None:
        if _RANK[level] > _RANK[self.level]:
            self.level = level
        self.reasons.append({"code": code, "level": level, "message": message, **detail})

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "reasons": self.reasons}


def sample(c: Confidence, n: int, min_sample: int, what: str = "MR") -> None:
    if n < min_sample:
        c.add(
            "insufficient",
            "small_sample",
            f"Ít hơn {min_sample} {what} trong kỳ ({n}), không tính trung vị",
            n=n,
            min_sample=min_sample,
        )


def freshness(
    c: Confidence,
    repos: Iterable[tuple[str, datetime | None]],
    now: datetime,
    stale_hours: int,
) -> datetime | None:
    """Flags repos synced longer than `stale_hours` ago (or never); returns the oldest sync, the
    `data_as_of` of the block."""
    oldest: datetime | None = None
    stale: list[str] = []
    for path, synced in repos:
        if synced is None or now - synced > timedelta(hours=stale_hours):
            stale.append(path)
        if synced is not None and (oldest is None or synced < oldest):
            oldest = synced
    if stale:
        c.add(
            "low",
            "stale_sync",
            f"{len(stale)} repo chưa đồng bộ trong {stale_hours} giờ qua",
            repos=sorted(stale)[:10],
        )
    return oldest


def unmapped(c: Confidence, unmapped_mrs: int, total: int) -> None:
    if total and 100.0 * unmapped_mrs / total > UNMAPPED_WARN:
        c.add(
            "low",
            "unmapped_authors",
            f"{unmapped_mrs}/{total} MR có tác giả chưa gắn người",
            unmapped=unmapped_mrs,
            total=total,
        )
