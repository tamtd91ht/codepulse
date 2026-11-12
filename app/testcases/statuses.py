"""Result statuses of a dự án (docs/specs/30 §15 G6): the built-in ones (TestRail's Passed /
Failed / Blocked / Retest, plus Skipped and Untested) and the ones a dự án adds.

A status has a key (what tests and results store), a name, a colour token, and whether it
counts as done (a done test needs no more running: it is left out of the remaining estimate).
Custom statuses are ordered after the built-in ones; archiving one keeps it readable on old
results but stops new results from using it. Since the database no longer checks the status
columns, every write goes through `check`.

Also here: the "chỉ người được giao" rule (who may record when the dự án turns it on).
"""

import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import TestAttachment, TestProjectSettings, TestStatus
from app.models.testcases import RESULT_STATUSES, TEST_STATUSES
from app.testcases import content
from app.testcases import service as tc

COLORS = ("good", "bad", "warn", "info", "muted", "primary")
CUSTOM_MAX = 20
ATTACH_PER_RESULT = 20
KEY_MAX = 30
NAME_MAX = 60
# Keys a custom status may never take: the built-in ones, the count fields, and the derived
# "needs_retest" flag (§15 G3) a test list filters on.
RESERVED = frozenset({*TEST_STATUSES, "total", "done", "remaining", "needs_retest"})
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
UNKNOWN = "trạng thái kết quả không hợp lệ"
# An uploaded file no result points at after this long is dropped (`gc_attachments`).
ATTACH_GC_AGE = timedelta(hours=24)
ATTACH_GC_BATCH = 1000

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StatusDef:
    key: str
    name: str
    color: str
    done: bool
    builtin: bool
    position: int
    id: int | None = None
    archived: bool = False

    def out(self) -> dict[str, Any]:
        return asdict(self)


# Built-in statuses in the order they are shown; Untested last (the rest of a progress bar).
BUILTIN: tuple[StatusDef, ...] = (
    StatusDef("passed", "Passed", "good", True, True, 1),
    StatusDef("failed", "Failed", "bad", True, True, 2),
    StatusDef("blocked", "Blocked", "primary", True, True, 3),
    # Retest asks for another run: not done.
    StatusDef("retest", "Retest", "warn", False, True, 4),
    StatusDef("skipped", "Skipped", "muted", True, True, 5),
)
UNTESTED = StatusDef("untested", "Chưa chạy", "muted", False, True, 1_000_000)
BUILTIN_KEYS = frozenset(s.key for s in BUILTIN)


def slug(name: str) -> str:
    """ "Cần xác nhận" -> "can_xac_nhan": lower case, no accents, words joined by "_"."""
    folded = unicodedata.normalize("NFD", name.casefold().replace("đ", "d"))
    plain = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    out = "_".join(re.findall(r"[a-z0-9]+", plain))[:KEY_MAX].strip("_")
    if out and not out[0].isalpha():
        out = f"s_{out}"[:KEY_MAX]
    return out


def custom_rows(session: Session, bp_id: int) -> list[TestStatus]:
    return list(
        session.scalars(
            select(TestStatus)
            .where(TestStatus.business_project_id == bp_id)
            .order_by(TestStatus.position, TestStatus.id)
        )
    )


def as_def(row: TestStatus) -> StatusDef:
    return StatusDef(
        key=row.key,
        name=row.name,
        color=row.color,
        done=row.done,
        builtin=False,
        position=100 + row.position,
        id=row.id,
        archived=row.archived_at is not None,
    )


def statuses(session: Session, bp_id: int, *, untested: bool = True) -> list[StatusDef]:
    """Every status of the dự án (archived custom ones included), in the order shown."""
    out = [*BUILTIN, *(as_def(r) for r in custom_rows(session, bp_id))]
    return [*out, UNTESTED] if untested else out


def by_key(session: Session, bp_id: int) -> dict[str, StatusDef]:
    return {s.key: s for s in statuses(session, bp_id)}


def labels(session: Session, bp_id: int) -> dict[str, str]:
    return {s.key: s.name for s in statuses(session, bp_id)}


def not_done(session: Session, bp_ids: Iterable[int]) -> dict[int, set[str]]:
    """Per dự án: the statuses whose tests still need running (Untested, Retest, custom ones
    marked not done). One query whatever the number of dự án."""
    wanted = sorted(set(bp_ids))
    base = {s.key for s in (*BUILTIN, UNTESTED) if not s.done}
    out = {bp: set(base) for bp in wanted}
    if wanted:
        for bp, key in session.execute(
            select(TestStatus.business_project_id, TestStatus.key).where(
                TestStatus.business_project_id.in_(wanted), TestStatus.done.is_(False)
            )
        ):
            out[bp].add(key)
    return out


def check(session: Session, bp_id: int, status: str | None) -> str | None:
    """The status a result is recorded with: a built-in result status or a live custom status
    of the dự án (422 otherwise). None stays None (a comment or a reassignment)."""
    if status is None:
        return None
    key = status.strip().lower()
    if key in RESULT_STATUSES:
        return key
    if key in RESERVED or not KEY_RE.match(key):
        raise HTTPException(422, f"{UNKNOWN}: {status[:40]}")
    row = session.scalar(
        select(TestStatus).where(TestStatus.business_project_id == bp_id, TestStatus.key == key)
    )
    if row is None:
        raise HTTPException(422, f"{UNKNOWN}: {status[:40]}")
    if row.archived_at is not None:
        raise HTTPException(422, f"trạng thái “{row.name}” đã lưu trữ, không dùng được nữa")
    return key


# -- step results -> the result --------------------------------------------------------------------


def from_steps(status: str | None, steps: list[dict[str, Any]]) -> tuple[str | None, int | None]:
    """The status a result takes from its steps, and the first failed step (1-based).

    A failed step fails the result, a blocked one (and none failed) blocks it, unless the tester
    picked another non-passing status; every step passed and nothing picked: passed. The first
    failed (else blocked) step is kept as `failed_step`."""
    marks = [str(s.get("status") or "") for s in steps]
    failed = next((i + 1 for i, m in enumerate(marks) if m == "failed"), None)
    blocked = next((i + 1 for i, m in enumerate(marks) if m == "blocked"), None)
    if failed is not None:
        return (status if status not in (None, "passed") else "failed"), failed
    if blocked is not None:
        return (status if status not in (None, "passed") else "blocked"), blocked
    if status is None and marks and all(m in ("passed", "skipped") for m in marks):
        return ("passed" if "passed" in marks else "skipped"), None
    return status, None


# -- attachments of a result -----------------------------------------------------------------------


def attachment_refs(session: Session, bp_id: int, ids: list[int]) -> list[dict[str, Any]]:
    """What a result keeps of the files it attaches: [{id, name, mime, size}] in the order
    given, each a file of the dự án (422 otherwise)."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    rows = {
        r.id: r
        for r in session.scalars(
            select(TestAttachment).where(
                TestAttachment.id.in_(wanted), TestAttachment.business_project_id == bp_id
            )
        )
    }
    if len(rows) != len(wanted):
        raise HTTPException(422, "tệp đính kèm không tồn tại")
    return [
        {"id": r.id, "name": r.name, "mime": r.mime, "size": r.size}
        for r in (rows[i] for i in wanted)
    ]


# Every attachment id some result points at (one scan of test_results; ids kept as numeric so a
# CI attachment carrying another kind of "id" never breaks the cast).
_REFERENCED = text(
    "SELECT (e->>'id')::numeric FROM test_results r "
    "CROSS JOIN LATERAL jsonb_array_elements("
    "CASE WHEN jsonb_typeof(r.attachments) = 'array' THEN r.attachments ELSE '[]'::jsonb END"
    ") e WHERE jsonb_typeof(e->'id') = 'number'"
)


def gc_attachments(session: Session, now: datetime | None = None) -> int:
    """Drop uploaded files older than ATTACH_GC_AGE that no result attaches (uploaded, then the
    result was never saved). At most ATTACH_GC_BATCH per call; the caller commits."""
    cutoff = (now or datetime.now(UTC)) - ATTACH_GC_AGE
    ids = list(
        session.scalars(
            select(TestAttachment.id)
            .where(
                TestAttachment.created_at < cutoff,
                text(f"test_attachments.id::numeric NOT IN ({_REFERENCED.text})"),
            )
            .order_by(TestAttachment.id)
            .limit(ATTACH_GC_BATCH)
        )
    )
    if ids:
        session.execute(delete(TestAttachment).where(TestAttachment.id.in_(ids)))
        log.info("unused result attachments dropped", extra={"count": len(ids)})
    return len(ids)


# -- who may record --------------------------------------------------------------------------------


def assigned_only(session: Session, bp_id: int) -> bool:
    """The dự án's "chỉ người được giao" flag (G6; set on the settings page)."""
    return bool(
        session.scalar(
            select(TestProjectSettings.assigned_only).where(
                TestProjectSettings.business_project_id == bp_id
            )
        )
    )


def may_record_any(principal: Principal, bp_id: int, only_assigned: bool) -> bool:
    """Record a result of any test of the dự án, whoever it is assigned to."""
    if only_assigned:
        return tc.may_manage(principal, bp_id)
    return tc.may_run(principal, bp_id) or tc.may_manage(principal, bp_id)


def may_record(
    session: Session,
    principal: Principal,
    bp_id: int,
    assignees: Iterable[int | None],
    *,
    only_assigned: bool | None = None,
) -> bool:
    """`content.may_record`, narrowed when the dự án records by assignees only: then the person
    the test (or its run) is assigned to, or a manager (`tests.manage`)."""
    flag = assigned_only(session, bp_id) if only_assigned is None else only_assigned
    if not flag:
        return content.may_record(principal, bp_id, assignees)
    if tc.may_manage(principal, bp_id):
        return True
    return tc.may_view(principal, bp_id) and principal.user_id in {a for a in assignees if a}
