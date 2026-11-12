"""Testcase (docs/specs/30): who may read and edit, suites shared by several dự án, the fields of a
case (cleaned the same way whether typed, imported or written by the AI), its history and search.

Permissions are the project documents' (docs/specs/30 §2): `docs.view` reads the cases of a dự án
one sees, `docs.edit` changes them (scope `project`: the dự án one manages; `all`: every one).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.assistant.knowledge import fold
from app.docs import service as docs
from app.models import TestCase, TestCaseRevision, TestSuite, TestSuiteLink
from app.models.testcases import CASE_KINDS, CASE_STATUSES, PRIORITIES

NAME_MAX = 200
CODE_MAX = 40
TITLE_MAX = 500
SECTION_MAX = 200
TEXT_MAX = 10_000
AUTOMATION_MAX = 500
LIST_MAX = 20
TAG_MAX = 40
GLOB_MAX = 200
# Characters a spreadsheet or PostgreSQL refuses (NUL, the C0 controls but tab and newline).
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# A glob with more wildcards than this is refused (matching is linear: app/testcases/impact.py).
GLOB_WILDCARDS = 12
JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,7})\b")
# The fields a revision keeps and a restore puts back.
FIELDS = (
    "code",
    "section",
    "title",
    "preconditions",
    "steps",
    "test_data",
    "expected",
    "notes",
    "priority",
    "kind",
    "automation",
    "tags",
    "jira_keys",
    "code_paths",
    "status",
)
TEXT_FIELDS = ("preconditions", "steps", "test_data", "expected", "notes")
PRIORITY_LABEL = {"critical": "Rất cao", "high": "Cao", "medium": "Trung bình", "low": "Thấp"}
KIND_LABEL = {"manual": "Thủ công", "automated": "Tự động"}
STATUS_LABEL = {"draft": "Nháp", "approved": "Đã duyệt", "obsolete": "Lỗi thời"}
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}


# -- permissions -----------------------------------------------------------------------------------

may_view = docs.may_view
may_edit = docs.may_edit
project_for = docs.project_for


def linked_ids(session: Session, suite_id: int) -> list[int]:
    return list(
        session.scalars(
            select(TestSuiteLink.business_project_id)
            .where(TestSuiteLink.suite_id == suite_id)
            .order_by(TestSuiteLink.created_at)
        )
    )


def projects_of(session: Session, suite: TestSuite) -> list[int]:
    """Its home first, then the dự án it is shared with."""
    return [suite.business_project_id, *linked_ids(session, suite.id)]


def suite_in(bp_ids: Iterable[int]) -> ColumnElement[bool]:
    """Suites of one of these dự án: made there, or shared with it."""
    wanted = sorted(set(bp_ids)) or [0]
    return or_(
        TestSuite.business_project_id.in_(wanted),
        TestSuite.id.in_(
            select(TestSuiteLink.suite_id).where(TestSuiteLink.business_project_id.in_(wanted))
        ),
    )


def may_view_suite(session: Session, principal: Principal, suite: TestSuite) -> bool:
    return any(may_view(principal, bp) for bp in projects_of(session, suite))


def may_edit_suite(session: Session, principal: Principal, suite: TestSuite) -> bool:
    return any(may_edit(principal, bp) for bp in projects_of(session, suite))


def suite_for(
    session: Session, principal: Principal, suite_id: int, *, edit: bool = False
) -> TestSuite:
    suite = session.get(TestSuite, suite_id)
    if suite is None or not may_view_suite(session, principal, suite):
        raise HTTPException(404, "bộ testcase không tồn tại")
    if edit and not may_edit_suite(session, principal, suite):
        raise HTTPException(403, "bạn không có quyền sửa testcase của dự án này")
    return suite


def case_for(
    session: Session, principal: Principal, case_id: int, *, edit: bool = False
) -> tuple[TestCase, TestSuite]:
    case = session.get(TestCase, case_id)
    if case is None:
        raise HTTPException(404, "testcase không tồn tại")
    try:
        suite = suite_for(session, principal, case.suite_id, edit=edit)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, "testcase không tồn tại") from None
        raise
    return case, suite


# -- fields ----------------------------------------------------------------------------------------


class FieldError(ValueError):
    """A value that cannot be kept; the message is shown (Vietnamese)."""


def _line(value: Any, limit: int) -> str:
    return " ".join(CONTROL.sub("", str(value or "")).split())[:limit]


def _text(value: Any) -> str | None:
    text = CONTROL.sub("", str(value or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:TEXT_MAX] or None


def _list(values: Any, limit: int) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;\n]", values)
    out: list[str] = []
    for v in values or []:
        item = _line(v, limit)
        if item and item.lower() not in (o.lower() for o in out):
            out.append(item)
        if len(out) >= LIST_MAX:
            break
    return out


def jira_keys(values: Any) -> list[str]:
    """`ABC-123` keys in a list or a cell's text, upper-cased, once each."""
    raw = " ".join(values) if isinstance(values, list | tuple) else str(values or "")
    out: list[str] = []
    for key in JIRA_KEY.findall(raw.upper()):
        if key not in out:
            out.append(key)
    return out[:LIST_MAX]


def glob_clean(pattern: str) -> str:
    """A path glob as kept: "./" dropped, runs of "*" at most "**", at most GLOB_WILDCARDS
    wildcards (else "")."""
    g = pattern.strip().replace("\\", "/")
    while g.startswith("./"):
        g = g[2:]
    g = re.sub(r"\*{3,}", "**", g)
    return "" if len(re.findall(r"\*+|\?", g)) > GLOB_WILDCARDS else g


def globs(values: Any) -> list[str]:
    out = []
    for raw in _list(values, GLOB_MAX):
        g = glob_clean(raw)
        if not g and raw.strip():
            raise FieldError(f"mẫu “{raw[:60]}” có quá nhiều * / ? (tối đa {GLOB_WILDCARDS})")
        if g and g not in out:
            out.append(g)
    return out


def clean(values: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """The case's fields as kept: trimmed, bounded, enums checked. With `partial`, only the
    fields given."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in FIELDS:
            continue
        if key == "title":
            out[key] = _line(value, TITLE_MAX)
            if not out[key]:
                raise FieldError("testcase cần tiêu đề")
        elif key == "code":
            out[key] = _line(value, CODE_MAX)
        elif key == "section":
            out[key] = _line(value, SECTION_MAX) or None
        elif key == "automation":
            out[key] = _line(value, AUTOMATION_MAX) or None
        elif key in TEXT_FIELDS:
            out[key] = _text(value)
        elif key == "tags":
            out[key] = _list(value, TAG_MAX)
        elif key == "jira_keys":
            out[key] = jira_keys(value)
        elif key == "code_paths":
            out[key] = globs(value)
        elif key == "priority":
            if value not in PRIORITIES:
                raise FieldError("ưu tiên không hợp lệ")
            out[key] = value
        elif key == "kind":
            if value not in CASE_KINDS:
                raise FieldError("loại không hợp lệ")
            out[key] = value
        elif key == "status":
            if value not in CASE_STATUSES:
                raise FieldError("trạng thái không hợp lệ")
            out[key] = value
    if not partial and not out.get("title"):
        raise FieldError("testcase cần tiêu đề")
    return out


def search_text(case: TestCase) -> str:
    parts = [
        case.code,
        case.section or "",
        case.title,
        case.preconditions or "",
        case.steps or "",
        case.expected or "",
        " ".join(case.tags or []),
        " ".join(case.jira_keys or []),
    ]
    return fold(" ".join(parts))[:20_000]


def snapshot(case: TestCase) -> dict[str, Any]:
    return {f: getattr(case, f) for f in FIELDS}


def problems(case: TestCase) -> list[str]:
    """What a tester would miss (docs/specs/30 §7, checked here, no AI)."""
    out = []
    if not (case.steps or "").strip():
        out.append("thiếu các bước")
    if not (case.expected or "").strip():
        out.append("thiếu kết quả mong đợi")
    return out


class Codes:
    """The codes of one suite, read once: free codes handed out without a query each (an import
    of thousands of rows). Callers lock the suite row first so two requests cannot hand out the
    same code."""

    def __init__(self, session: Session, suite_id: int) -> None:
        self.taken = set(
            session.scalars(select(TestCase.code).where(TestCase.suite_id == suite_id))
        )
        self.top = max(
            (int(m.group(1)) for c in self.taken if (m := re.fullmatch(r"TC-(\d+)", c))),
            default=0,
        )

    def free(self, wanted: str = "") -> str:
        if wanted and wanted not in self.taken:
            self.taken.add(wanted)
            return wanted
        while True:
            self.top += 1
            code = f"TC-{self.top:04d}"
            if code not in self.taken:
                self.taken.add(code)
                return code


def next_codes(session: Session, suite_id: int, count: int) -> list[str]:
    """`count` free codes TC-0001… after the highest TC-number of the suite."""
    taken = set(session.scalars(select(TestCase.code).where(TestCase.suite_id == suite_id)))
    top = 0
    for code in taken:
        m = re.fullmatch(r"TC-(\d+)", code)
        if m:
            top = max(top, int(m.group(1)))
    out: list[str] = []
    n = top
    while len(out) < count:
        n += 1
        code = f"TC-{n:04d}"
        if code not in taken:
            out.append(code)
    return out


def next_position(session: Session, suite_id: int) -> int:
    top = session.scalar(select(func.max(TestCase.position)).where(TestCase.suite_id == suite_id))
    return int(top or 0) + 1


def create(
    session: Session,
    suite: TestSuite,
    values: dict[str, Any],
    *,
    user_id: int | None,
    source: str,
    position: int | None = None,
    codes: Codes | None = None,
    flush: bool = True,
) -> TestCase:
    """A new case of `suite` from cleaned values (a free code given when none or taken)."""
    code = (codes or Codes(session, suite.id)).free(values.get("code") or "")
    case = TestCase(
        suite_id=suite.id,
        **{k: v for k, v in values.items() if k != "code"},
        code=code,
        source=source,
        revision=1,
        position=position if position is not None else next_position(session, suite.id),
        created_by=user_id,
        updated_by=user_id,
    )
    case.tags = case.tags or []
    case.jira_keys = case.jira_keys or []
    case.code_paths = case.code_paths or []
    case.search = search_text(case)
    session.add(case)
    if flush:
        session.flush()
    return case


def record(session: Session, case: TestCase, *, user_id: int | None, via: str) -> None:
    """The case as it is now goes to its history; its revision moves on (the caller changes it
    next)."""
    session.add(
        TestCaseRevision(
            case_id=case.id,
            number=case.revision,
            snapshot=snapshot(case),
            via=via,
            changed_by=user_id,
            changed_at=datetime.now(UTC),
        )
    )
    case.revision += 1
    case.updated_by = user_id


def change(
    session: Session, case: TestCase, values: dict[str, Any], *, user_id: int | None, via: str
) -> bool:
    """Apply cleaned values; when anything differs, the case as it was goes to its history and
    its revision moves on. Returns whether it changed."""
    changed = {k: v for k, v in values.items() if getattr(case, k) != v}
    if not changed:
        return False
    if "code" in changed:
        code = changed["code"]
        if not code:
            raise FieldError("mã testcase không được trống")
        clash = session.scalar(
            select(TestCase.id).where(
                TestCase.suite_id == case.suite_id, TestCase.code == code, TestCase.id != case.id
            )
        )
        if clash:
            raise FieldError(f"mã {code} đã có trong bộ")
    record(session, case, user_id=user_id, via=via)
    for key, value in changed.items():
        setattr(case, key, value)
    case.search = search_text(case)
    return True


@dataclass
class SuiteCounts:
    total: int = 0
    approved: int = 0
    draft: int = 0
    obsolete: int = 0


def links_of(session: Session, suite_ids: list[int]) -> dict[int, list[int]]:
    """suite id -> the dự án it is shared with (one query for a list of suites)."""
    out: dict[int, list[int]] = {i: [] for i in suite_ids}
    if suite_ids:
        for suite_id, bp_id in session.execute(
            select(TestSuiteLink.suite_id, TestSuiteLink.business_project_id)
            .where(TestSuiteLink.suite_id.in_(suite_ids))
            .order_by(TestSuiteLink.created_at)
        ):
            out[suite_id].append(bp_id)
    return out


def counts(session: Session, suite_ids: list[int]) -> dict[int, SuiteCounts]:
    out: dict[int, SuiteCounts] = {i: SuiteCounts() for i in suite_ids}
    if not suite_ids:
        return out
    rows = session.execute(
        select(TestCase.suite_id, TestCase.status, func.count())
        .where(TestCase.suite_id.in_(suite_ids))
        .group_by(TestCase.suite_id, TestCase.status)
    )
    for suite_id, status, n in rows:
        c = out[suite_id]
        setattr(c, status, n)
        c.total += n
    return out
