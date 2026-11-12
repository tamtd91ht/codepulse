"""Testcase (docs/specs/30): who may read and edit, suites shared by several dự án, the fields of a
case (cleaned the same way whether typed, imported or written by the AI), its history and search.

Permissions (docs/specs/30 §2): `tests.view` reads the cases of a dự án one sees, `tests.edit`
designs them, `tests.run` records results, `tests.manage` runs the runs (scopes `project`: the
dự án one manages; `all`: every one).
"""

import re
import textwrap
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, and_, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.assistant.knowledge import fold
from app.models import (
    BusinessProject,
    Project,
    TestCase,
    TestCaseRevision,
    TestField,
    TestMilestone,
    TestSection,
    TestSharedStep,
    TestSuite,
    TestSuiteLink,
)
from app.models.testcases import CASE_KINDS, CASE_STATUSES, CASE_TYPES, PRIORITIES, TEMPLATES
from app.testcases import content, gherkin
from app.testcases import params as tparams

NAME_MAX = 200
CODE_MAX = 40
TITLE_MAX = 500
SECTION_MAX = 200
SECTION_DESCRIPTION_MAX = 2_000
ESTIMATE_MAX = 40
TEXT_MAX = 10_000
BDD_MAX = 20_000  # §15.12 G8: a case's Gherkin scenario
CHARTER_MAX = 5_000  # §15.12 G8: an exploratory case's charter
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
    "section_id",
    "title",
    "template",
    "preconditions",
    "steps",
    "test_data",
    "expected",
    "steps_list",
    "notes",
    "priority",
    "type",
    "kind",
    "automation",
    "automation_id",
    "tags",
    "jira_keys",
    "code_paths",
    "status",
    "milestone_id",
    "estimate_s",
    # §15 G5: custom field values, {key: value}; checked by app/testcases/fields.py.
    "custom",
    # §15.12 G8: the Gherkin scenario (template "bdd"), the charter (template "exploratory").
    "bdd",
    "charter",
    # §15.15 G13: the parameter table {columns, rows}.
    "params",
)
# What a restore puts back: not the sheet's code (only an import sets it).
RESTORED = tuple(f for f in FIELDS if f != "code")
# Fields that are not the case's content: changing only these keeps an approved case approved.
NOT_CONTENT = ("status", "code")
TEXT_FIELDS = ("preconditions", "steps", "test_data", "expected", "notes")
PRIORITY_LABEL = {"critical": "Rất cao", "high": "Cao", "medium": "Trung bình", "low": "Thấp"}
KIND_LABEL = {"manual": "Thủ công", "automated": "Tự động"}
STATUS_LABEL = {
    "draft": "Nháp",
    "review": "Chờ duyệt",
    "approved": "Đã duyệt",
    "obsolete": "Lỗi thời",
}
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}


# -- permissions -----------------------------------------------------------------------------------


def may_view(principal: Principal, bp_id: int | None) -> bool:
    return principal.can("tests.view") and principal.can_view_project(bp_id)


def _scoped(principal: Principal, key: str, bp_id: int | None) -> bool:
    """A `project | all` permission over dự án `bp_id`: `project` covers the dự án one manages,
    `all` every dự án one sees."""
    scope = principal.scope(key)
    if scope is None or not principal.can_view_project(bp_id):
        return False
    return scope == "all" or principal.can_manage_project(bp_id)


def may_edit(principal: Principal, bp_id: int | None) -> bool:
    """Design the cases: add, change, import, delete; sections, shared steps, AI."""
    return may_view(principal, bp_id) and _scoped(principal, "tests.edit", bp_id)


def may_run(principal: Principal, bp_id: int | None) -> bool:
    """Record results of any test of the dự án (an assignee records theirs anyway)."""
    return may_view(principal, bp_id) and _scoped(principal, "tests.run", bp_id)


def may_manage(principal: Principal, bp_id: int | None) -> bool:
    """Runs, plans, milestones, configurations; AI analysis of merge requests."""
    return may_view(principal, bp_id) and _scoped(principal, "tests.manage", bp_id)


def viewable_projects(session: Session, principal: Principal) -> list[BusinessProject]:
    if not principal.can("tests.view"):
        return []
    query = select(BusinessProject).order_by(BusinessProject.name)
    seen = principal.visible_projects()
    if seen is not None:
        query = query.where(BusinessProject.id.in_(seen))
    return list(session.scalars(query))


def project_for(
    session: Session,
    principal: Principal,
    bp_id: int,
    edit: bool = False,
    *,
    manage: bool = False,
) -> BusinessProject:
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not may_view(principal, bp.id):
        raise HTTPException(404, "dự án not found")
    if edit and not may_edit(principal, bp.id):
        raise HTTPException(403, "bạn không có quyền sửa testcase của dự án này")
    if manage and not may_manage(principal, bp.id):
        raise HTTPException(403, "bạn không có quyền quản lý test run của dự án này")
    return bp


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


@dataclass(frozen=True)
class CodePath:
    """One code_paths entry (docs/specs/30 §15.2 G2): "[!][{repo}:]{glob}". No repo: any repo
    of the dự án. "!" (a case only): drop an entry its section or suite gives it."""

    repo: str | None
    glob: str
    negate: bool = False

    def text(self, *, negate: bool | None = None) -> str:
        bang = "!" if (self.negate if negate is None else negate) else ""
        return f"{bang}{self.repo + ':' if self.repo else ''}{self.glob}"


# "{repo}:" before a glob: a GitLab path (group/sub/name) or its last part, without wildcards.
_REPO_PREFIX = re.compile(r"^([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)\s*:\s*(.*)$")


def code_path(entry: str) -> CodePath:
    raw = entry.strip()
    negate = raw.startswith("!")
    if negate:
        raw = raw[1:].strip()
    found = _REPO_PREFIX.match(raw)
    if found:
        return CodePath(found.group(1), found.group(2).strip(), negate)
    return CodePath(None, raw, negate)


def repo_matches(ref: str, full: str) -> bool:
    """`ref` (a code path's repo) names the repo `full` (path_with_namespace): the whole path
    or its last part, letter case aside."""
    ref, full = ref.strip().lower(), full.strip().lower()
    return bool(ref) and (ref == full or ref == full.rsplit("/", 1)[-1])


def globs(values: Any) -> list[str]:
    out = []
    for raw in _list(values, GLOB_MAX):
        entry = code_path(raw)
        g = glob_clean(entry.glob)
        if not g and entry.glob.strip():
            raise FieldError(f"mẫu “{raw[:60]}” có quá nhiều * / ? (tối đa {GLOB_WILDCARDS})")
        if not g:
            continue
        text = CodePath(entry.repo, g, entry.negate).text()
        if text not in out:
            out.append(text)
    return out


def repos_of(session: Session, bp_ids: Iterable[int]) -> list[str]:
    """The GitLab paths of the repos of these dự án."""
    wanted = sorted(set(bp_ids)) or [0]
    return sorted(
        session.scalars(
            select(Project.path_with_namespace).where(Project.business_project_id.in_(wanted))
        )
    )


def area_paths(
    session: Session, suite: TestSuite, values: Any, current: list[str] | None
) -> list[str]:
    """The code_paths of a suite or a section ("Vùng code"), cleaned and checked: no "!"."""
    out = globs(values)
    check_code_paths(
        session, projects_of(session, suite), out, known=current or [], negate_ok=False
    )
    return out


def check_code_paths(
    session: Session,
    bp_ids: Iterable[int],
    entries: list[str],
    *,
    known: Iterable[str] = (),
    negate_ok: bool = True,
) -> None:
    """Every "{repo}:" must name one repo of the dự án (entries in `known`, already kept, are
    not checked again)."""
    kept = set(known)
    repos: list[str] | None = None
    for entry in entries:
        if entry in kept:
            continue
        path = code_path(entry)
        if path.negate and not negate_ok:
            raise FieldError("dấu ! (bỏ vùng kế thừa) chỉ dùng trong testcase")
        if not path.repo:
            continue
        if repos is None:
            repos = repos_of(session, bp_ids)
        hits = [r for r in repos if repo_matches(path.repo, r)]
        if not hits:
            listed = ", ".join(repos[:10]) if repos else "dự án chưa gắn repo nào"
            raise FieldError(f"repo “{path.repo[:80]}” không thuộc dự án ({listed})")
        if len(hits) > 1 and path.repo.lower() not in (h.lower() for h in hits):
            raise FieldError(
                f"tên “{path.repo[:80]}” trùng nhiều repo ({', '.join(hits[:5])}); "
                "ghi đủ đường dẫn repo"
            )


def _ref(value: Any, what: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FieldError(f"{what} không hợp lệ")
    return value


def _step_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str | int | float):
        raise FieldError("bước không hợp lệ")
    text = CONTROL.sub("", str(value).replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(text) > content.STEP_TEXT_MAX:
        raise FieldError(f"mỗi bước tối đa {content.STEP_TEXT_MAX} ký tự")
    return text


def steps_clean(values: Any) -> list[dict[str, Any]]:
    """Template "steps": [{"content", "expected"} | {"shared": id}], empty steps dropped."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise FieldError("danh sách bước không hợp lệ")
    out: list[dict[str, Any]] = []
    for step in values:
        if not isinstance(step, dict):
            raise FieldError("bước không hợp lệ")
        if step.get("shared") is not None:
            out.append({"shared": _ref(step.get("shared"), "bước dùng chung")})
            continue
        text, expected = _step_text(step.get("content")), _step_text(step.get("expected"))
        if text or expected:
            out.append({"content": text, "expected": expected})
    if len(out) > content.STEP_MAX:
        raise FieldError(f"tối đa {content.STEP_MAX} bước")
    return out


def estimate_of(value: Any) -> int | None:
    """Seconds of an estimate typed ("30s", "5m", "1h 30m"); empty: None; unreadable: refused."""
    if value is None or not str(value).strip():
        return None
    seconds = content.parse_estimate(str(value)[:ESTIMATE_MAX])
    if seconds is None:
        raise FieldError("thời gian ước tính không đọc được (ví dụ: 30s, 5m, 1h 30m)")
    return seconds


def clean(
    values: dict[str, Any], *, partial: bool = False, current: TestCase | None = None
) -> dict[str, Any]:
    """The case's fields as kept: trimmed, bounded, enums checked. With `partial`, only the
    fields given. `estimate` (text) is read into `estimate_s`. References (section, milestone,
    shared steps) are checked against the suite by `check_refs`. A whole case (not `partial`),
    or an edit of `current`, is also checked as a whole (`check_case`: BDD syntax, parameters)."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key == "estimate":
            out["estimate_s"] = estimate_of(value)
            continue
        if key not in FIELDS:
            continue
        if key == "title":
            out[key] = _line(value, TITLE_MAX)
            if not out[key]:
                raise FieldError("testcase cần tiêu đề")
        elif key == "code":
            out[key] = _line(value, CODE_MAX) or None
        elif key == "section_id":
            out[key] = _ref(value, "thư mục")
        elif key == "milestone_id":
            out[key] = _ref(value, "milestone")
        elif key == "estimate_s":
            if "estimate" in values:
                continue
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise FieldError("thời gian ước tính không hợp lệ")
            out[key] = min(value, 7 * 24 * 3600) if value else None
        elif key == "steps_list":
            out[key] = steps_clean(value)
        elif key in ("automation", "automation_id"):
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
        elif key == "type":
            if value not in CASE_TYPES:
                raise FieldError("type không hợp lệ")
            out[key] = value
        elif key == "template":
            if value not in TEMPLATES:
                raise FieldError("template không hợp lệ")
            out[key] = value
        elif key == "status":
            if value not in CASE_STATUSES:
                raise FieldError("trạng thái không hợp lệ")
            out[key] = value
        elif key == "custom":
            # Checked against the dự án's fields by app/testcases/fields.py (it needs a session).
            if value is not None and not isinstance(value, dict):
                raise FieldError("trường tuỳ biến không hợp lệ")
            out[key] = dict(value or {})
        elif key == "bdd":
            out[key] = bdd_clean(value)
        elif key == "charter":
            if value is not None and len(str(value).strip()) > CHARTER_MAX:
                raise FieldError(f"charter tối đa {CHARTER_MAX} ký tự")
            out[key] = _text(value)
        elif key == "params":
            try:
                out[key] = tparams.clean(value)
            except tparams.ParamError as exc:
                raise FieldError(str(exc)) from None
    if not partial and not out.get("title"):
        raise FieldError("testcase cần tiêu đề")
    if not partial or current is not None:
        check_case(out, current)
    return out


def bdd_clean(value: Any) -> str | None:
    """A Gherkin text as kept: newlines unified, control characters, trailing blanks and the
    common indent removed. Longer than BDD_MAX: refused (never cut, it would break the syntax)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FieldError("kịch bản BDD không hợp lệ")
    text = CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = textwrap.dedent(text).strip("\n")
    if len(text) > BDD_MAX:
        raise FieldError(f"kịch bản BDD tối đa {BDD_MAX} ký tự")
    return text or None


# What a parameter's variables may appear in (§15.15).
PARAM_TEXTS = ("title", "preconditions", "steps", "expected", "test_data", "bdd")
# Fields that, given, make `check_case` look at the whole case again.
_CASE_WIDE = ("template", "bdd", "params", "steps_list", *PARAM_TEXTS)


def check_case(values: dict[str, Any], current: TestCase | None = None) -> None:
    """What only the whole case can tell (§15.12, §15.15), on cleaned `values` over `current`
    (None: a new case): a BDD case's scenario must parse (one Scenario or Scenario Outline, a
    Background allowed); a Scenario Outline's Examples become the case's parameter table (they
    win over `params` sent with it); every {{variable}} (in a BDD case also <variable>) needs a
    column of the parameter table. Sets `values["params"]` when the Examples give it."""
    if current is not None and not any(k in values for k in _CASE_WIDE):
        return

    def now(name: str) -> Any:
        if name in values:
            return values[name]
        return getattr(current, name) if current is not None else None

    template = now("template") or "text"
    bdd = now("bdd")
    if template == "bdd" and bdd and ("bdd" in values or "template" in values or current is None):
        try:
            feature = gherkin.check_case(bdd)
        except gherkin.GherkinError as exc:
            raise FieldError(f"kịch bản BDD {exc}") from None
        scenario = feature.scenarios[0]
        if scenario.outline:
            try:
                values["params"] = tparams.clean(gherkin.outline_params(scenario.examples))
            except tparams.ParamError as exc:
                raise FieldError(f"Examples: {exc}") from None
    table = now("params") or {}
    if not table.get("columns"):
        return
    steps = [
        text
        for step in now("steps_list") or []
        if isinstance(step, dict)
        for text in (step.get("content"), step.get("expected"))
        if isinstance(text, str)
    ]
    try:
        tparams.check_variables(
            table, *(now(f) for f in PARAM_TEXTS), *steps, outline=template == "bdd"
        )
    except tparams.ParamError as exc:
        raise FieldError(str(exc)) from None


def check_refs(
    session: Session,
    suite: TestSuite,
    values: dict[str, Any],
    *,
    lenient: bool = False,
    current: TestCase | None = None,
) -> dict[str, Any]:
    """The section must be of the suite, the milestone and the shared steps of one of its dự
    án. `lenient` (a restore): a reference gone is dropped instead of refused. `current` (an
    edit): a milestone or shared step the case already refers to stays accepted, even when the
    suite is no longer shared with its dự án; only new references are checked."""
    projects = projects_of(session, suite)
    had_milestone = current.milestone_id if current is not None else None
    had_shared = set(content.shared_ids(current.steps_list)) if current is not None else set()
    section_id = values.get("section_id")
    if section_id is not None:
        section = session.get(TestSection, section_id)
        if section is None or section.suite_id != suite.id:
            if not lenient:
                raise FieldError("thư mục không thuộc bộ testcase này")
            values["section_id"] = None
    milestone_id = values.get("milestone_id")
    if milestone_id is not None and milestone_id != had_milestone:
        milestone = session.get(TestMilestone, milestone_id)
        if milestone is None or milestone.business_project_id not in projects:
            if not lenient:
                raise FieldError("milestone không thuộc dự án của bộ testcase")
            values["milestone_id"] = None
    if values.get("code_paths") and not lenient:
        # A "{repo}:" must be a repo of the suite's dự án; entries the case had stay.
        had_paths = (current.code_paths or []) if current is not None else []
        check_code_paths(session, projects, values["code_paths"], known=had_paths)
    wanted = content.shared_ids(values.get("steps_list"))
    if wanted:
        found = content.shared_map(session, wanted)
        ok = {i for i, s in found.items() if s.business_project_id in projects}
        ok |= had_shared & set(found)  # still there, and the case already had it
        if not lenient and set(wanted) - ok:
            raise FieldError("bước dùng chung không thuộc dự án của bộ testcase")
        values["steps_list"] = [
            s for s in values["steps_list"] if "shared" not in s or s["shared"] in ok
        ]
    automation_id = values.get("automation_id")
    if automation_id and automation_taken(
        session, suite.id, automation_id, current.id if current is not None else None
    ):
        if not lenient:
            raise FieldError(f"automation ID {automation_id} đã có ở testcase khác trong bộ")
        values.pop("automation_id")
    return values


def automation_taken(
    session: Session, suite_id: int, automation_id: str, case_id: int | None = None
) -> bool:
    """§15 G1: an automation ID names one case of a suite."""
    query = select(TestCase.id).where(
        TestCase.suite_id == suite_id, TestCase.automation_id == automation_id
    )
    if case_id is not None:
        query = query.where(TestCase.id != case_id)
    return session.scalar(query.limit(1)) is not None


def search_text(case: TestCase) -> str:
    steps = " ".join(
        f"{s.get('content') or ''} {s.get('expected') or ''}"
        for s in case.steps_list or []
        if isinstance(s, dict)
    )
    parts = [
        case.code or "",
        case.title,
        case.preconditions or "",
        case.steps or "",
        case.expected or "",
        steps,
        case.bdd or "",
        case.charter or "",
        " ".join(str(v) for row in (case.params or {}).get("rows") or [] for v in row),
        " ".join(case.tags or []),
        " ".join(case.jira_keys or []),
        custom_words(case.custom),
    ]
    return fold(" ".join(parts))[:20_000]


def custom_words(custom: dict[str, Any] | None) -> str:
    """The text of a case's custom values the search reads (§15.8): every text or list of texts
    (text, textarea, dropdown and multi-select values; a url or a date reads as text too)."""
    parts: list[str] = []
    for value in (custom or {}).values() if isinstance(custom, dict) else ():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(v for v in value if isinstance(v, str))
    return " ".join(parts)


def snapshot(case: TestCase) -> dict[str, Any]:
    return {f: getattr(case, f) for f in FIELDS}


def problems(case: TestCase, shared: dict[int, TestSharedStep] | None = None) -> list[str]:
    """What a tester would miss (docs/specs/30 §7, checked here, no AI). A shared step counts as
    a step with its expected result unless `shared` says otherwise."""
    out = []
    if case.template == "bdd":  # §15.12 G8
        if not gherkin.scenario_steps(case.bdd):
            return ["thiếu các bước"]
        if not gherkin.has_then(case.bdd):
            out.append("thiếu kết quả mong đợi")
        return out
    if case.template == "exploratory":
        return [] if (case.charter or "").strip() else ["thiếu charter"]
    if case.template == "steps":
        steps = [s for s in case.steps_list or [] if isinstance(s, dict)]
        if shared is not None:
            steps = content.expand_steps(steps, shared)
        if not steps:
            return ["thiếu các bước"]
        if not any("shared" in s or (s.get("expected") or "").strip() for s in steps):
            out.append("thiếu kết quả mong đợi")
        return out
    if not (case.steps or "").strip():
        out.append("thiếu các bước")
    if not (case.expected or "").strip():
        out.append("thiếu kết quả mong đợi")
    return out


# A step with its expected result, or a shared step (jsonpath over steps_list).
_STEP_DONE: ColumnElement[Any] = literal_column(
    """'$[*] ? (exists(@.shared) || @.expected != "")'::jsonpath"""
)


_THEN = r"(^|\n)[ \t]*Then[ \t]+[^ \t\n]"


def problem_clause() -> ColumnElement[bool]:
    """SQL twin of `problems`: the cases missing steps or an expected result."""
    blank = func.coalesce(func.trim(TestCase.steps), "") == ""
    no_expected = func.coalesce(func.trim(TestCase.expected), "") == ""
    bdd = func.coalesce(TestCase.bdd, "")
    return or_(
        and_(TestCase.template == "text", or_(blank, no_expected)),
        # §15.12 G8: a BDD case without a Then step (the parser's twin, close enough for a
        # filter), an exploratory case without a charter.
        and_(TestCase.template == "bdd", ~bdd.op("~")(_THEN)),
        and_(
            TestCase.template == "exploratory",
            func.coalesce(func.trim(TestCase.charter), "") == "",
        ),
        and_(
            TestCase.template == "steps",
            or_(
                func.jsonb_array_length(TestCase.steps_list) == 0,
                ~func.jsonb_path_exists(TestCase.steps_list, _STEP_DONE),
            ),
        ),
    )


def next_position(session: Session, suite_id: int) -> int:
    top = session.scalar(select(func.max(TestCase.position)).where(TestCase.suite_id == suite_id))
    return int(top or 0) + 1


def code_taken(session: Session, suite_id: int, code: str, case_id: int | None = None) -> bool:
    query = select(TestCase.id).where(TestCase.suite_id == suite_id, TestCase.code == code)
    if case_id is not None:
        query = query.where(TestCase.id != case_id)
    return session.scalar(query.limit(1)) is not None


def create(
    session: Session,
    suite: TestSuite,
    values: dict[str, Any],
    *,
    user_id: int | None,
    source: str,
    position: int | None = None,
    flush: bool = True,
    fields: Sequence[TestField] | None = None,
) -> TestCase:
    """A new case of `suite` from cleaned values. Its key is C<id>; `code` is only the sheet's
    code of an import (the caller keeps it unique in the suite). Custom fields it lacks take
    their defaults (§15.8; `fields`: the home dự án's case fields, read when not given)."""
    from app.testcases import fields as tf  # fields imports this module

    values = dict(values)
    values["custom"] = tf.with_defaults(
        session,
        suite.business_project_id,
        "case",
        values.get("custom"),
        suite_id=suite.id,
        template=values.get("template") or "text",
        fields=fields,
    )
    case = TestCase(
        suite_id=suite.id,
        **values,
        source=source,
        revision=1,
        position=position if position is not None else next_position(session, suite.id),
        created_by=user_id,
        updated_by=user_id,
    )
    case.tags = case.tags or []
    case.jira_keys = case.jira_keys or []
    case.code_paths = case.code_paths or []
    case.steps_list = case.steps_list or []
    case.template = case.template or "text"
    case.type = case.type or "functional"
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
    code = changed.get("code")
    if code and code_taken(session, case.suite_id, code, case.id):
        raise FieldError(f"mã {code} đã có trong bộ")
    record(session, case, user_id=user_id, via=via)
    for key, value in changed.items():
        setattr(case, key, value)
    case.search = search_text(case)
    return True


# -- sections --------------------------------------------------------------------------------------


@dataclass
class SectionRow:
    section: TestSection
    depth: int
    path: str
    cases: int  # directly inside, not obsolete
    total: int  # with every section below


def section_tree(session: Session, suite_id: int) -> list[SectionRow]:
    """The suite's sections in reading order, with their case counts."""
    sections = content.section_rows(session, [suite_id])
    paths = content.paths_of(sections)
    direct: dict[int, int] = {
        sid: int(n)
        for sid, n in session.execute(
            select(TestCase.section_id, func.count())
            .where(
                TestCase.suite_id == suite_id,
                TestCase.section_id.is_not(None),
                TestCase.status != "obsolete",
            )
            .group_by(TestCase.section_id)
        ).all()
        if sid is not None
    }
    out = []
    for section, depth in content.tree_order(sections):
        below = content.descendants(sections, section.id)
        out.append(
            SectionRow(
                section=section,
                depth=depth,
                path=paths.get(section.id, section.name),
                cases=direct.get(section.id, 0),
                total=sum(direct.get(i, 0) for i in below),
            )
        )
    return out


def _depth(sections: list[TestSection], section_id: int | None) -> int:
    """How deep a section is (a top one: 1; the suite's root: 0)."""
    by_id = {s.id: s for s in sections}
    depth = 0
    while section_id is not None and section_id in by_id and depth <= content.SECTION_DEPTH_MAX:
        depth += 1
        section_id = by_id[section_id].parent_id
    return depth


def _height(sections: list[TestSection], root: int) -> int:
    """Levels of the subtree of `root` (itself only: 1)."""
    children: dict[int | None, list[int]] = {}
    for s in sections:
        children.setdefault(s.parent_id, []).append(s.id)

    def walk(node: int, level: int) -> int:
        if level > content.SECTION_DEPTH_MAX:
            return level
        return max([level, *(walk(c, level + 1) for c in children.get(node, []))])

    return walk(root, 1)


def _parent_in(session: Session, suite_id: int, parent_id: int | None) -> None:
    if parent_id is None:
        return
    parent = session.get(TestSection, parent_id)
    if parent is None or parent.suite_id != suite_id:
        raise FieldError("thư mục cha không thuộc bộ testcase này")


def _sibling_end(session: Session, suite_id: int, parent_id: int | None) -> int:
    query = select(func.max(TestSection.position)).where(TestSection.suite_id == suite_id)
    query = query.where(
        TestSection.parent_id.is_(None) if parent_id is None else TestSection.parent_id == parent_id
    )
    return int(session.scalar(query) or 0) + 1


def section_name(value: Any) -> str:
    name = _line(value, SECTION_MAX)
    if not name:
        raise FieldError("thư mục cần tên")
    return name


def create_section(
    session: Session,
    suite: TestSuite,
    *,
    parent_id: int | None,
    name: str,
    description: str | None = None,
) -> TestSection:
    _parent_in(session, suite.id, parent_id)
    sections = content.section_rows(session, [suite.id])
    if _depth(sections, parent_id) + 1 > content.SECTION_DEPTH_MAX:
        raise FieldError(f"thư mục lồng tối đa {content.SECTION_DEPTH_MAX} cấp")
    about = _text(description)
    section = TestSection(
        suite_id=suite.id,
        parent_id=parent_id,
        name=section_name(name),
        description=about[:SECTION_DESCRIPTION_MAX] if about else None,
        position=_sibling_end(session, suite.id, parent_id),
    )
    session.add(section)
    session.flush()
    return section


def move_section(session: Session, section: TestSection, parent_id: int | None) -> None:
    """Under another section of the same suite (None: the suite's root); never into itself or
    one of its own sections."""
    if parent_id == section.parent_id:
        return
    _parent_in(session, section.suite_id, parent_id)
    sections = content.section_rows(session, [section.suite_id])
    if parent_id is not None and parent_id in content.descendants(sections, section.id):
        raise FieldError("không chuyển thư mục vào chính nó hoặc thư mục con của nó")
    if _depth(sections, parent_id) + _height(sections, section.id) > content.SECTION_DEPTH_MAX:
        raise FieldError(f"thư mục lồng tối đa {content.SECTION_DEPTH_MAX} cấp")
    section.parent_id = parent_id
    section.position = _sibling_end(session, section.suite_id, parent_id)


def delete_section(session: Session, section: TestSection) -> tuple[int, int]:
    """The section, every one below it and their cases (the database cascades); returns
    (sections, cases) removed."""
    from app.testcases.runs import refresh_open_snapshots  # runs imports this module

    sections = content.section_rows(session, [section.suite_id])
    ids = content.descendants(sections, section.id)
    case_ids = list(session.scalars(select(TestCase.id).where(TestCase.section_id.in_(ids))))
    # Open runs keep their own copy of each case: refreshed before the cases go.
    refresh_open_snapshots(session, case_ids)
    session.delete(section)
    session.flush()
    return len(ids), len(case_ids)


def section_scope(session: Session, section: TestSection) -> set[int]:
    """The section and every one below it."""
    return content.descendants(content.section_rows(session, [section.suite_id]), section.id)


@dataclass
class SuiteCounts:
    total: int = 0
    approved: int = 0
    draft: int = 0
    obsolete: int = 0
    review: int = 0


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
