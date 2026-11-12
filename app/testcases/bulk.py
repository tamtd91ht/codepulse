"""Bulk changes of test cases, find and replace, and undoing them (docs/specs/30 §15.10, G10).

Every bulk change that edits cases is one `TestBulkOp`: the revision each case had before (its
snapshot is the case as it was) and the cases a copy made. Undo puts a case back to that
snapshot only when nothing changed it since (its revision is still the one the op gave it), and
deletes the copies no one has recorded a result on. A bulk delete is not undoable.

Speed (§15.10: 200 cases in at most 5 s): the cases are read and locked by one query, the
revisions are written by one flush, and per-suite or per-dự án lookups are made once.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import regex
from fastapi import HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.security import Principal
from app.models import (
    TestBulkOp,
    TestCase,
    TestCaseRevision,
    TestMilestone,
    TestResult,
    TestRunTest,
    TestSuite,
)
from app.testcases import approval, content
from app.testcases import events as tc_events
from app.testcases import service as tc
from app.testcases.runs import refresh_open_snapshots

UNDO_WINDOW = timedelta(hours=24)
OPS_LISTED = 20
REPLACE_MAX = 500  # cases a find and replace shows, and changes at once
REGEX_MAX = 200
REGEX_BUDGET_S = 2.0  # matching every case of a suite, all together
PREVIEW_CONTEXT = 80  # characters shown either side of the change in a preview
PREVIEW_SPAN_MAX = 400  # the longest changed stretch a preview shows (cut beyond, with "...")
PREVIEW_CHANGES_MAX = 10  # changes shown per case
PREVIEW_CHARS_MAX = 1_000_000  # the preview's text in all; matches beyond are left out
SLOW = "biểu thức quá chậm; thu hẹp biểu thức hoặc thư mục"
REGEX_UNBOUNDED_MAX = 3  # `*`, `+`, `{n,}` in one pattern
REGEX_REPEAT_MAX = 1_000  # the largest `{n,m}`
REPLACE_FIELDS = ("title", "preconditions", "steps", "expected", "notes", "test_data")
# Fields of a case whose change is a change of its content: an approved case goes back to review
# when the dự án's approval is on (§15.9). Moving, folders, tags, links and the like are not.
CONTENT_ACTIONS = ("kind", "estimate", "custom", "replace")
EDIT_ACTIONS = (
    "status",
    "priority",
    "type",
    "section",
    "milestone",
    "tags",
    "kind",
    "estimate",
    "jira",
    "custom",
)
LABEL = {
    "status": "Đổi trạng thái",
    "priority": "Đổi ưu tiên",
    "type": "Đổi loại test",
    "section": "Chuyển thư mục",
    "milestone": "Đổi milestone",
    "tags": "Sửa tag",
    "kind": "Đổi kiểu chạy",
    "estimate": "Đổi thời gian ước tính",
    "jira": "Thêm mã Jira",
    "custom": "Đặt trường tuỳ biến",
    "move": "Chuyển bộ",
    "copy": "Sao chép",
    "replace": "Tìm và thay",
    "duplicates": "Đánh dấu trùng (AI)",
}


@dataclass
class Skip:
    case_id: int
    reason: str

    @property
    def key(self) -> str:
        return f"C{self.case_id}"


@dataclass
class Outcome:
    changed: int
    batch_id: int | None
    skipped: list[Skip] = field(default_factory=list)


# -- shared helpers --------------------------------------------------------------------------------


class Statuses:
    """`approval.guard_status` asked once per (dự án, status before, status wanted, content)."""

    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.seen: dict[tuple[int, str | None, str | None, bool], str | HTTPException | None] = {}

    def __call__(
        self, bp_id: int, *, before: str | None, wanted: str | None, content_changed: bool
    ) -> str | None:
        key = (bp_id, before, wanted, content_changed)
        if key not in self.seen:
            try:
                self.seen[key] = approval.guard_status(
                    self.session,
                    self.principal,
                    bp_id,
                    before=before,
                    wanted=wanted,
                    content_changed=content_changed,
                )
            except HTTPException as exc:
                self.seen[key] = exc
        got = self.seen[key]
        if isinstance(got, HTTPException):
            raise got
        return got


def _extra(case: TestCase) -> dict[str, Any]:
    """What a revision's snapshot needs besides `tc.FIELDS` to undo a bulk change: the suite (a
    move) and the custom field values."""
    return {"suite_id": case.suite_id, "custom": dict(case.custom or {})}


def _stamp(session: Session, extras: dict[int, dict[str, Any]], before: dict[int, int]) -> None:
    """Add `extras` to the snapshot of the revisions this op wrote (number = `before`)."""
    if not extras:
        return
    for obj in [*session.new, *session.identity_map.values()]:
        if not isinstance(obj, TestCaseRevision) or before.get(obj.case_id) != obj.number:
            continue
        extra = extras.get(obj.case_id) or {}
        missing = {k: v for k, v in extra.items() if k not in (obj.snapshot or {})}
        if missing:
            obj.snapshot = {**(obj.snapshot or {}), **missing}


def new_op(
    session: Session,
    *,
    bp_id: int | None,
    user_id: int | None,
    kind: str,
    summary: str,
    before: dict[int, int],
    created_ids: Iterable[int] = (),
) -> TestBulkOp | None:
    """The op to undo later; None when it changed nothing."""
    created = sorted(created_ids)
    if not before and not created:
        return None
    op = TestBulkOp(
        business_project_id=bp_id,
        user_id=user_id,
        kind=kind,
        summary=summary[:500],
        before={str(k): v for k, v in before.items()},
        created_ids=created,
        created_at=datetime.now(UTC),
    )
    session.add(op)
    session.flush()
    return op


def change_cases(
    session: Session,
    principal: Principal,
    cases: list[TestCase],
    suites: dict[int, TestSuite],
    values_of: Callable[[TestCase], dict[str, Any] | None],
    *,
    via: str,
    content_changed: bool,
    statuses: Statuses | None = None,
) -> dict[int, int]:
    """Apply `values_of(case)` (cleaned; None: leave it) to each case through `tc.change`,
    under the approval rule; returns {case id: the revision it had before} of those changed."""
    statuses = statuses or Statuses(session, principal)
    before: dict[int, int] = {}
    extras: dict[int, dict[str, Any]] = {}
    with session.no_autoflush:
        for case in cases:
            values = values_of(case)
            if values is None:
                continue
            if not any(getattr(case, k) != v for k, v in values.items()):
                continue
            bp_id = suites[case.suite_id].business_project_id
            status = statuses(
                bp_id,
                before=case.status,
                wanted=values.get("status"),
                content_changed=content_changed,
            )
            if status is not None:
                values = {**values, "status": status}
            old_status, revision, extra = case.status, case.revision, _extra(case)
            if tc.change(session, case, values, user_id=principal.user_id, via=via):
                before[case.id] = revision
                extras[case.id] = extra
                tc_events.case_status_changed(session, bp_id, case.id, old_status, case.status)
    _stamp(session, extras, before)
    return before


def _shared_fix(
    session: Session, cases: list[TestCase], target_projects: set[int]
) -> Callable[[list[dict[str, Any]] | None], list[dict[str, Any]]]:
    """Steps as they go to a suite of `target_projects`: a shared step of a dự án it is not in
    is copied in as plain steps."""
    found = content.shared_map(
        session, [i for c in cases for i in content.shared_ids(c.steps_list)]
    )
    ok = {i for i, s in found.items() if s.business_project_id in target_projects}

    def fix(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if all(i in ok for i in content.shared_ids(steps)):
            return list(steps or [])
        kept: list[dict[str, Any]] = []
        for step in steps or []:
            if isinstance(step, dict) and "shared" in step and step["shared"] not in ok:
                kept += [
                    {"content": s["content"], "expected": s["expected"]}
                    for s in content.expand_steps([step], found)
                ]
            else:
                kept.append(step)
        return kept[: content.STEP_MAX]

    return fix


def _milestone_ok(session: Session, milestone_id: int | None, projects: set[int]) -> int | None:
    if milestone_id is None:
        return None
    milestone = session.get(TestMilestone, milestone_id)
    if milestone is None or milestone.business_project_id not in projects:
        return None
    return milestone_id


# -- POST /testcases/bulk --------------------------------------------------------------------------


def _locked_cases(
    session: Session, principal: Principal, ids: list[int], *, delete: bool, section: bool
) -> tuple[list[TestCase], dict[int, TestSuite]]:
    found = dict(
        session.execute(select(TestCase.id, TestCase.suite_id).where(TestCase.id.in_(ids))).all()
    )
    if len(found) != len(set(ids)):
        raise HTTPException(404, "có testcase không còn")
    suite_ids = set(found.values())
    suites = {
        s.id: s for s in session.scalars(select(TestSuite).where(TestSuite.id.in_(suite_ids)))
    }
    for suite_id in sorted(suite_ids):
        suite = suites.get(suite_id)
        if suite is None or not tc.may_view_suite(session, principal, suite):
            raise HTTPException(404, "có testcase không còn")
        projects = tc.projects_of(session, suite)
        if delete:
            if not any(approval.may_delete(principal, bp) for bp in projects):
                approval.check_delete(principal, suite.business_project_id)
        elif not any(tc.may_edit(principal, bp) for bp in projects):
            raise HTTPException(403, "có testcase bạn không có quyền sửa")
    if section:
        # The section endpoints lock the suite for every tree change (and a section delete then
        # removes cases): locked first, the same order, so a section deleted meanwhile is seen
        # as gone (422) rather than a foreign key error.
        session.execute(
            select(TestSuite.id)
            .where(TestSuite.id.in_(suite_ids))
            .order_by(TestSuite.id)
            .with_for_update()
        )
    cases = list(
        session.scalars(
            select(TestCase)
            .where(TestCase.id.in_(ids), TestCase.suite_id.in_(suite_ids))
            .order_by(TestCase.id)
            .with_for_update()
        )
    )
    if len(cases) != len(found):
        raise HTTPException(409, "testcase vừa bị chuyển hoặc xoá; tải lại rồi thử lại")
    return cases, suites


def bulk(
    session: Session, principal: Principal, ids: list[int], action: str, params: dict[str, Any]
) -> Outcome:
    """One bulk action over the cases `ids` (docs/specs/30 §15.11); the caller commits."""
    ids = list(dict.fromkeys(ids))
    cases, suites = _locked_cases(
        session, principal, ids, delete=action == "delete", section=action == "section"
    )
    if action == "delete":
        refresh_open_snapshots(session, [c.id for c in cases])
        for case in cases:
            session.delete(case)
        audit(session, principal.user_id, "testcase.bulk_delete", None, ids=ids[:500])
        return Outcome(changed=len(cases), batch_id=None)
    if action == "move":
        return _move(session, principal, cases, params)
    if action == "copy":
        return _copy(session, principal, cases, params)
    if action not in EDIT_ACTIONS:
        raise HTTPException(422, "hành động không hợp lệ")
    skipped: list[Skip] = []
    values_of = _values_of(session, principal, action, params, suites, skipped)
    before = change_cases(
        session,
        principal,
        cases,
        suites,
        values_of,
        via="bulk",
        content_changed=action in CONTENT_ACTIONS,
    )
    first = suites[cases[0].suite_id] if cases else None
    op = new_op(
        session,
        bp_id=first.business_project_id if first else None,
        user_id=principal.user_id,
        kind="edit",
        summary=f"{LABEL[action]}: {len(before)} testcase",
        before=before,
    )
    if op is not None:
        audit(
            session,
            principal.user_id,
            "testcase.bulk",
            f"test_bulk_op:{op.id}",
            bulk_action=action,
            count=len(before),
        )
    return Outcome(changed=len(before), batch_id=op.id if op else None, skipped=skipped)


def _values_of(
    session: Session,
    principal: Principal,
    action: str,
    params: dict[str, Any],
    suites: dict[int, TestSuite],
    skipped: list[Skip],
) -> Callable[[TestCase], dict[str, Any] | None]:
    """What one case becomes under `action`, checked once where it does not depend on the
    case."""
    try:
        if action in ("section", "milestone"):
            key = f"{action}_id"
            per_suite = {
                s.id: tc.check_refs(session, s, {key: params.get(key)}) for s in suites.values()
            }
            return lambda case: per_suite[case.suite_id]
        if action in ("status", "priority", "type", "kind"):
            value = params.get(action)
            if value is None:
                raise HTTPException(422, "chọn giá trị")
            fixed = tc.clean({action: value}, partial=True)
            return lambda case: fixed
        if action == "estimate":
            fixed = tc.clean({"estimate": params.get("estimate")}, partial=True)
            return lambda case: fixed
    except tc.FieldError as exc:
        raise HTTPException(422, str(exc)) from None
    if action == "tags":
        add = list(params.get("tags_add") or [])
        drop = {str(t).strip().lower() for t in params.get("tags_remove") or []}
        if not add and not drop:
            raise HTTPException(422, "chọn tag để thêm hoặc bỏ")

        def tags(case: TestCase) -> dict[str, Any]:
            kept = [t for t in case.tags or [] if t.lower() not in drop]
            return tc.clean({"tags": [*kept, *add]}, partial=True)

        return tags
    if action == "jira":
        add = tc.jira_keys(params.get("jira_add") or [])
        if not add:
            raise HTTPException(422, "nhập mã Jira dạng ABC-123")
        return lambda case: tc.clean({"jira_keys": [*(case.jira_keys or []), *add]}, partial=True)
    return _custom_of(session, params, suites, skipped)


def _custom_of(
    session: Session, params: dict[str, Any], suites: dict[int, TestSuite], skipped: list[Skip]
) -> Callable[[TestCase], dict[str, Any] | None]:
    """Action `custom`: one custom field set on each case it applies to (its suite and
    template); `value` None clears it."""
    from app.testcases import fields  # §15.8 G5, written alongside

    key = str(params.get("field_key") or "").strip()
    if not key:
        raise HTTPException(422, "chọn trường tuỳ biến")
    value = params.get("value")
    every = {
        bp: fields.rows(session, bp, "case")
        for bp in {s.business_project_id for s in suites.values()}
    }
    if not any(f.key == key for found in every.values() for f in found):
        raise HTTPException(422, f"không có trường tuỳ biến {key}")
    # The value cleaned once per (suite, template): {key: value}, {} when it clears the field,
    # None when the field does not apply there.
    cleaned: dict[tuple[int, str], dict[str, Any] | None] = {}

    def custom(case: TestCase) -> dict[str, Any] | None:
        where = (case.suite_id, case.template)
        if where not in cleaned:
            bp = suites[case.suite_id].business_project_id
            live = fields.applicable(every[bp], case.suite_id, case.template)
            if not any(f.key == key for f in live):
                cleaned[where] = None
            else:
                try:
                    cleaned[where] = fields.clean_custom(
                        session,
                        bp,
                        "case",
                        {key: value},
                        suite_id=case.suite_id,
                        template=case.template,
                        partial=True,
                        enforce_required=False,
                        defaults=False,
                        fields=live,
                    )
                except tc.FieldError as exc:
                    raise HTTPException(422, str(exc)) from None
        got = cleaned[where]
        if got is None:
            skipped.append(Skip(case.id, "trường không áp dụng cho bộ / template này"))
            return None
        now = dict(case.custom or {})
        if key in got:
            now[key] = got[key]
        else:
            now.pop(key, None)
        return {"custom": now}

    return custom


def _target(
    session: Session, principal: Principal, params: dict[str, Any]
) -> tuple[TestSuite, set[int]]:
    suite_id = params.get("suite_id")
    if suite_id is None:
        raise HTTPException(422, "chọn bộ testcase đích")
    target = tc.suite_for(session, principal, int(suite_id), edit=True)
    session.refresh(target, with_for_update=True)
    return target, set(tc.projects_of(session, target))


def _move(
    session: Session, principal: Principal, cases: list[TestCase], params: dict[str, Any]
) -> Outcome:
    target, target_projects = _target(session, principal, params)
    taken = {
        c for c in session.scalars(select(TestCase.code).where(TestCase.suite_id == target.id)) if c
    }
    automation = set(
        session.scalars(
            select(TestCase.automation_id).where(
                TestCase.suite_id == target.id, TestCase.automation_id.is_not(None)
            )
        )
    )
    fix = _shared_fix(session, cases, target_projects)
    position = tc.next_position(session, target.id)
    before: dict[int, int] = {}
    extras: dict[int, dict[str, Any]] = {}
    skipped: list[Skip] = []
    for case in cases:
        if case.suite_id == target.id:
            skipped.append(Skip(case.id, "đã ở bộ đích"))
            continue
        before[case.id], extras[case.id] = case.revision, _extra(case)
        tc.record(session, case, user_id=principal.user_id, via="bulk")
        if case.automation_id and case.automation_id in automation:
            case.automation_id = None  # §15 G1: unique in a suite
        elif case.automation_id:
            automation.add(case.automation_id)
        case.suite_id = target.id
        case.section_id = None  # the sections are the old suite's
        if case.code in taken:
            case.code = None
        elif case.code:
            taken.add(case.code)
        case.milestone_id = _milestone_ok(session, case.milestone_id, target_projects)
        case.steps_list = fix(case.steps_list)
        case.position = position
        case.search = tc.search_text(case)
        position += 1
    _stamp(session, extras, before)
    op = new_op(
        session,
        bp_id=target.business_project_id,
        user_id=principal.user_id,
        kind="move",
        summary=f"{LABEL['move']} → {target.name}: {len(before)} testcase",
        before=before,
    )
    if op is not None:
        audit(
            session,
            principal.user_id,
            "testcase.bulk",
            f"test_bulk_op:{op.id}",
            bulk_action="move",
            suite=target.id,
            count=len(before),
        )
    return Outcome(changed=len(before), batch_id=op.id if op else None, skipped=skipped)


COPIED = (
    "title",
    "template",
    "preconditions",
    "steps",
    "test_data",
    "expected",
    "notes",
    "priority",
    "type",
    "kind",
    "automation",
    "tags",
    "jira_keys",
    "code_paths",
    "estimate_s",
)


def _fields() -> Any:
    from app.testcases import fields  # §15.8 G5; it imports the service this module uses

    return fields


def _custom_for(
    session: Session, target: TestSuite, case: TestCase, every: Sequence[Any]
) -> dict[str, Any]:
    """`case.custom` as it goes to `target`: the values the target dự án's fields (for the
    target suite and the case's template) accept; unknown keys and values they refuse are
    dropped. The defaults are filled in by `tc.create`."""
    fields = _fields()
    out: dict[str, Any] = {}
    for key, value in (case.custom or {}).items():
        try:
            out = fields.clean_custom(
                session,
                target.business_project_id,
                "case",
                {key: value},
                suite_id=target.id,
                template=case.template or "text",
                partial=True,
                current=out,
                enforce_required=False,
                defaults=False,
                fields=every,
            )
        except tc.FieldError:
            continue
    return out


def _copy(
    session: Session, principal: Principal, cases: list[TestCase], params: dict[str, Any]
) -> Outcome:
    """New cases in the target suite (and folder): status draft, the source of the original, a
    new key, no sheet code; an automation ID already used in the suite is cleared."""
    target, target_projects = _target(session, principal, params)
    section_id = params.get("section_id")
    if section_id is not None:
        try:
            tc.check_refs(session, target, {"section_id": section_id})
        except tc.FieldError as exc:
            raise HTTPException(422, str(exc)) from None
    automation = set(
        session.scalars(
            select(TestCase.automation_id).where(
                TestCase.suite_id == target.id, TestCase.automation_id.is_not(None)
            )
        )
    )
    fix = _shared_fix(session, cases, target_projects)
    position = tc.next_position(session, target.id)
    target_fields = _fields().rows(session, target.business_project_id, "case")
    made: list[TestCase] = []
    for case in cases:
        values: dict[str, Any] = {f: getattr(case, f) for f in COPIED}
        values["tags"] = list(case.tags or [])
        values["jira_keys"] = list(case.jira_keys or [])
        values["code_paths"] = list(case.code_paths or [])
        values["custom"] = _custom_for(session, target, case, target_fields)
        values["steps_list"] = fix(case.steps_list)
        values["status"] = "draft"
        values["milestone_id"] = _milestone_ok(session, case.milestone_id, target_projects)
        if section_id is not None:
            values["section_id"] = section_id
        elif case.suite_id == target.id:
            values["section_id"] = case.section_id  # a copy in the same suite stays beside it
        aid = case.automation_id
        if aid and aid not in automation:
            values["automation_id"] = aid
            automation.add(aid)
        made.append(
            tc.create(
                session,
                target,
                values,
                user_id=principal.user_id,
                source=case.source,
                position=position,
                flush=False,
                fields=target_fields,
            )
        )
        position += 1
    session.flush()
    op = new_op(
        session,
        bp_id=target.business_project_id,
        user_id=principal.user_id,
        kind="copy",
        summary=f"{LABEL['copy']} → {target.name}: {len(made)} testcase",
        before={},
        created_ids=[c.id for c in made],
    )
    if op is not None:
        audit(
            session,
            principal.user_id,
            "testcase.bulk",
            f"test_bulk_op:{op.id}",
            bulk_action="copy",
            suite=target.id,
            count=len(made),
        )
    return Outcome(changed=len(made), batch_id=op.id if op else None)


# -- find and replace ------------------------------------------------------------------------------


def regex_problem(pattern: str) -> str | None:
    """Why a regular expression typed by a person is refused, or None. A first line of defence
    (the match itself runs under a time limit, see `Replacer`): a repeated group that itself
    repeats or has alternatives ("(a+)+", "(a|aa)*"), the same thing repeated open-ended twice
    in a row ("a*a*", ".*.*"), back references, fuzzy matching ("{e<=2}"), more than
    REGEX_UNBOUNDED_MAX open-ended repeats, a bound over REGEX_REPEAT_MAX."""
    if len(pattern) > REGEX_MAX:
        return f"biểu thức tối đa {REGEX_MAX} ký tự"
    groups: list[list[bool]] = [[False, False]]  # per open group: has a repeat, has "|"
    closed: list[bool] | None = None  # the group just closed (a repeat applies to it)
    atom = False  # something a repeat may follow
    unbounded = 0
    i, n = 0, len(pattern)
    bound = re.compile(r"\{(\d*)(,?)(\d*)\}")
    fuzzy = re.compile(r"\{\s*[eisd]?\s*[<=>]|\{\s*[eisd]\s*[},]")
    start = 0  # where the atom a repeat would apply to begins
    starts: list[int] = []  # where each open group begins
    last: tuple[str, int] | None = None  # the last open-ended repeat: its atom, where it ends
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            nxt = pattern[i + 1 : i + 2]
            if nxt.isdigit() and nxt != "0":
                return "không hỗ trợ tham chiếu ngược (\\1)"
            start, i, closed, atom = i, i + 2, None, True
            continue
        if ch == "[":
            start = i
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            i, closed, atom = j + 1, None, True
            continue
        if ch == "(":
            if pattern.startswith("(?P=", i):
                return "không hỗ trợ tham chiếu ngược (?P=...)"
            groups.append([False, False])
            starts.append(i)
            i += 1
            if i < n and pattern[i] == "?":
                m = re.match(r"\?(?:P?<[A-Za-z_]\w*>|<[=!]|[:=!>]|[aiLmsux-]+[:)])", pattern[i:])
                if m is None:
                    return "nhóm không hỗ trợ"
                i += m.end()
                if m.group().endswith(")"):  # an inline flag group "(?i)"
                    groups.pop()
                    starts.pop()
                    closed, atom = None, False
                    continue
            closed, atom = None, False
            continue
        if ch == ")":
            if len(groups) == 1:
                return "thiếu dấu ("
            closed = groups.pop()
            start = starts.pop()
            groups[-1][0] |= closed[0]
            groups[-1][1] |= closed[1]
            i, atom = i + 1, True
            continue
        if ch == "|":
            groups[-1][1] = True
            i, closed, atom = i + 1, None, False
            continue
        repeat = ch in "*+?"
        open_ended = ch in "*+"
        size = 1
        if ch == "{":
            if fuzzy.match(pattern, i):
                return "không hỗ trợ so khớp gần đúng ({e<=1})"
            m = bound.match(pattern, i)
            if m is not None and (m.group(1) or m.group(3)):
                repeat = True
                size = m.end() - i
                low, comma, high = m.group(1), m.group(2), m.group(3)
                if (low and int(low) > REGEX_REPEAT_MAX) or (high and int(high) > REGEX_REPEAT_MAX):
                    return f"số lần lặp tối đa {REGEX_REPEAT_MAX}"
                open_ended = bool(comma) and not high
        if repeat and atom:
            if closed is not None and (closed[0] or closed[1]):
                return "không hỗ trợ lặp lồng nhau như (a+)+ hoặc (a|b)*"
            groups[-1][0] = True
            text = pattern[start:i]
            i += size
            if i < n and pattern[i] in "?+":  # lazy or possessive
                i += 1
            if open_ended:
                unbounded += 1
                if unbounded > REGEX_UNBOUNDED_MAX:
                    return f"tối đa {REGEX_UNBOUNDED_MAX} phép lặp không giới hạn (*, +)"
                if last is not None and last == (text, start):
                    return "không hỗ trợ lặp liền nhau cùng một mẫu như a*a* hoặc .*.*"
                last = (text, i)
            closed, atom = None, False
            continue
        start, i, closed, atom = i, i + 1, None, True
    if len(groups) != 1:
        return "thiếu dấu )"
    return None


class Replacer:
    """`text -> (text replaced, how many)`; a pattern refused or broken is a 422.

    A regular expression is compiled with the `regex` package (VERSION0: as Python's `re`) so a
    match can be stopped: each call gets what is left of REGEX_BUDGET_S, counted from the first
    call, and running out is a 422. Plain text is matched as an escaped literal."""

    def __init__(self, find: str, replace: str, *, is_regex: bool, case_sensitive: bool) -> None:
        if not find:
            raise HTTPException(422, "nhập nội dung cần tìm")
        self.replace = replace
        self.started: float | None = None
        self.pattern: regex.Pattern[str] | None = None
        self.literal: re.Pattern[str] | None = None
        if is_regex:
            problem = regex_problem(find)
            if problem:
                raise HTTPException(422, f"biểu thức chính quy không dùng được: {problem}")
            flags = regex.VERSION0 | (0 if case_sensitive else regex.IGNORECASE)
            try:
                self.pattern = regex.compile(find, flags)
            except (regex.error, ValueError, TypeError) as exc:
                raise HTTPException(422, f"biểu thức chính quy sai: {exc}") from None
        else:
            self.literal = re.compile(re.escape(find), 0 if case_sensitive else re.IGNORECASE)

    def remaining(self) -> float:
        """Seconds of REGEX_BUDGET_S left (the clock starts at the first call); 422 when none."""
        now = time.monotonic()
        if self.started is None:
            self.started = now
        left = REGEX_BUDGET_S - (now - self.started)
        if left <= 0:
            raise HTTPException(422, SLOW)
        return left

    def __call__(self, text: str) -> tuple[str, int]:
        replace = self.replace
        if self.pattern is None:
            assert self.literal is not None  # noqa: S101 - one of the two is set
            return self.literal.subn(lambda m: replace, text)

        def expand(m: regex.Match[str]) -> str:
            return m.expand(replace)

        try:
            return self.pattern.subn(expand, text, timeout=self.remaining())
        except TimeoutError:
            raise HTTPException(422, SLOW) from None
        except (regex.error, IndexError) as exc:
            raise HTTPException(422, f"chuỗi thay thế sai: {exc}") from None


def replacer(
    find: str, replace: str, *, regex: bool, case_sensitive: bool
) -> Callable[[str], tuple[str, int]]:
    """`text -> (text replaced, how many)` (see `Replacer`)."""
    return Replacer(find, replace, is_regex=regex, case_sensitive=case_sensitive)


@dataclass
class Match:
    case: TestCase
    changes: list[dict[str, Any]]
    values: dict[str, Any]


def find_matches(
    cases: Iterable[TestCase], fields: Sequence[str], sub: Callable[[str], tuple[str, int]]
) -> list[Match]:
    """The cases `sub` changes, each with its changes (field, before, after) and its new values.
    "steps" and "expected" also cover the content / expected result of each own step of a
    "steps" case (shared steps are left alone)."""
    started = time.monotonic()
    out: list[Match] = []
    for case in cases:
        if time.monotonic() - started > REGEX_BUDGET_S:
            raise HTTPException(422, SLOW)
        changes: list[dict[str, Any]] = []
        values: dict[str, Any] = {}
        for name in fields:
            text = getattr(case, name)
            if not text:
                continue
            after, hits = sub(text)
            if hits and after != text:
                changes.append({"field": name, "before": text, "after": after})
                values[name] = after
        part = {"steps": "content", "expected": "expected"}
        wanted = [part[f] for f in fields if f in part]
        if wanted and case.template == "steps" and case.steps_list:
            steps = [dict(s) if isinstance(s, dict) else s for s in case.steps_list]
            touched = False
            for n, step in enumerate(steps, start=1):
                if not isinstance(step, dict) or "shared" in step:
                    continue
                for name in wanted:
                    text = str(step.get(name) or "")
                    if not text:
                        continue
                    after, hits = sub(text)
                    if hits and after != text:
                        changes.append(
                            {"field": f"steps_list[{n}].{name}", "before": text, "after": after}
                        )
                        step[name] = after
                        touched = True
            if touched:
                values["steps_list"] = steps
        if changes:
            out.append(Match(case=case, changes=changes, values=values))
    return out


def snippet(before: str, after: str) -> tuple[str, str]:
    """The stretch of `before` and `after` that differs, with PREVIEW_CONTEXT characters either
    side; a changed stretch longer than PREVIEW_SPAN_MAX is cut. "..." marks a cut."""
    n = min(len(before), len(after))
    head = 0
    while head < n and before[head] == after[head]:
        head += 1
    tail = 0
    while tail < n - head and before[-1 - tail] == after[-1 - tail]:
        tail += 1
    start = max(0, head - PREVIEW_CONTEXT)

    def cut(text: str) -> str:
        end = len(text) - tail  # the changed stretch is text[head:end]
        if end - head > PREVIEW_SPAN_MAX:
            stop = head + PREVIEW_SPAN_MAX
        else:
            stop = min(len(text), end + PREVIEW_CONTEXT)
        lead = "..." if start > 0 else ""
        return lead + text[start:stop] + ("..." if stop < len(text) else "")

    return cut(before), cut(after)


@dataclass
class Preview:
    total: int
    matches: list[dict[str, Any]]
    truncated: bool


def preview(found: list[Match]) -> Preview:
    """What a preview shows: at most REPLACE_MAX cases, PREVIEW_CHANGES_MAX changes each, as
    snippets, PREVIEW_CHARS_MAX characters in all; `truncated` when anything is left out.
    Applying still replaces everything."""
    truncated = len(found) > REPLACE_MAX
    used = 0
    out: list[dict[str, Any]] = []
    for m in found[:REPLACE_MAX]:
        changes = []
        size = len(m.case.title or "")
        for c in m.changes[:PREVIEW_CHANGES_MAX]:
            before, after = snippet(str(c["before"] or ""), str(c["after"] or ""))
            changes.append({"field": c["field"], "before": before, "after": after})
            size += len(c["field"]) + len(before) + len(after)
        if used + size > PREVIEW_CHARS_MAX:
            truncated = True
            break
        if len(m.changes) > PREVIEW_CHANGES_MAX:
            truncated = True
        used += size
        out.append(
            {
                "case_id": m.case.id,
                "key": f"C{m.case.id}",
                "title": m.case.title,
                "changes": changes,
            }
        )
    return Preview(total=len(found), matches=out, truncated=truncated)


def replace_cases(
    session: Session, principal: Principal, suite: TestSuite, found: list[Match]
) -> Outcome:
    """Apply the matches (cases locked by the caller); one op "replace"."""
    cleaned: dict[int, dict[str, Any]] = {}
    for m in found:
        try:
            cleaned[m.case.id] = tc.clean(m.values, partial=True)
        except tc.FieldError as exc:
            raise HTTPException(422, f"{content.key('case', m.case.id)}: {exc}") from None
    before = change_cases(
        session,
        principal,
        [m.case for m in found],
        {suite.id: suite},
        lambda case: cleaned.get(case.id),
        via="bulk",
        content_changed=True,
    )
    op = new_op(
        session,
        bp_id=suite.business_project_id,
        user_id=principal.user_id,
        kind="replace",
        summary=f"{LABEL['replace']} ({suite.name}): {len(before)} testcase",
        before=before,
    )
    if op is not None:
        audit(
            session,
            principal.user_id,
            "testcase.bulk",
            f"test_bulk_op:{op.id}",
            bulk_action="replace",
            suite=suite.id,
            count=len(before),
        )
    return Outcome(changed=len(before), batch_id=op.id if op else None)


# -- AI duplicates ---------------------------------------------------------------------------------


def mark_obsolete(
    session: Session,
    principal: Principal,
    cases: list[TestCase],
    suites: dict[int, TestSuite],
    *,
    summary: str,
) -> Outcome:
    """§15.10: the duplicates an AI run found, chosen by a person, become obsolete (revision
    "ai"), as one op "duplicates" that can be undone."""
    before = change_cases(
        session,
        principal,
        cases,
        suites,
        lambda case: {"status": "obsolete"},
        via="ai",
        content_changed=False,
    )
    first = suites[cases[0].suite_id] if cases else None
    op = new_op(
        session,
        bp_id=first.business_project_id if first else None,
        user_id=principal.user_id,
        kind="duplicates",
        summary=summary,
        before=before,
    )
    return Outcome(changed=len(before), batch_id=op.id if op else None)


# -- undo ------------------------------------------------------------------------------------------


def can_undo(op: TestBulkOp, now: datetime | None = None) -> bool:
    at = now or datetime.now(UTC)
    return op.undone_at is None and at - op.created_at <= UNDO_WINDOW


@dataclass
class Undone:
    restored: int
    deleted: int
    skipped: list[Skip]


def undo(session: Session, principal: Principal, op_id: int) -> Undone:
    """Put back what op `op_id` changed: owner or tests.manage, within UNDO_WINDOW, once. The
    caller commits."""
    op = session.scalars(select(TestBulkOp).where(TestBulkOp.id == op_id).with_for_update()).first()
    if op is None or not tc.may_view(principal, op.business_project_id):
        raise HTTPException(404, "không tìm thấy lần sửa hàng loạt")
    if op.user_id != principal.user_id and not tc.may_manage(principal, op.business_project_id):
        raise HTTPException(403, "chỉ người làm hoặc người quản lý test mới hoàn tác được")
    if op.undone_at is not None:
        raise HTTPException(409, "lần sửa này đã được hoàn tác")
    if not can_undo(op):
        raise HTTPException(409, "chỉ hoàn tác được trong 24 giờ")
    before = {int(k): int(v) for k, v in (op.before or {}).items()}
    skipped: list[Skip] = []
    restored = _restore_all(session, principal, before, skipped)
    deleted = _delete_copies(
        session,
        principal,
        list(op.created_ids or []),
        skipped,
        own=op.user_id == principal.user_id,
    )
    op.undone_at = datetime.now(UTC)
    audit(
        session,
        principal.user_id,
        "testcase.bulk_undo",
        f"test_bulk_op:{op.id}",
        restored=restored,
        deleted=deleted,
        skipped=len(skipped),
    )
    return Undone(restored=restored, deleted=deleted, skipped=skipped)


def _restore_all(
    session: Session, principal: Principal, before: dict[int, int], skipped: list[Skip]
) -> int:
    if not before:
        return 0
    cases = {
        c.id: c
        for c in session.scalars(
            select(TestCase).where(TestCase.id.in_(before)).order_by(TestCase.id).with_for_update()
        )
    }
    snapshots = {
        r.case_id: dict(r.snapshot or {})
        for r in session.scalars(
            select(TestCaseRevision).where(
                tuple_(TestCaseRevision.case_id, TestCaseRevision.number).in_(
                    [(i, n) for i, n in before.items()]
                )
            )
        )
    }
    wanted_suites = {c.suite_id for c in cases.values()} | {
        int(s["suite_id"]) for s in snapshots.values() if isinstance(s.get("suite_id"), int)
    }
    suites = {
        s.id: s for s in session.scalars(select(TestSuite).where(TestSuite.id.in_(wanted_suites)))
    }
    editable = {i: tc.may_edit_suite(session, principal, s) for i, s in suites.items()}
    statuses = Statuses(session, principal)
    restored = 0
    for case_id in sorted(before):
        case = cases.get(case_id)
        if case is None:
            skipped.append(Skip(case_id, "testcase đã bị xoá"))
            continue
        if case.revision != before[case_id] + 1:
            skipped.append(Skip(case_id, "testcase đã được sửa sau lần này"))
            continue
        snap = snapshots.get(case_id)
        if snap is None:
            skipped.append(Skip(case_id, "không còn bản trước trong lịch sử"))
            continue
        reason = _restore(session, principal, case, snap, suites, editable, statuses)
        if reason:
            skipped.append(Skip(case_id, reason))
        else:
            restored += 1
    return restored


def _restore(
    session: Session,
    principal: Principal,
    case: TestCase,
    snap: dict[str, Any],
    suites: dict[int, TestSuite],
    editable: dict[int, bool],
    statuses: Statuses,
) -> str | None:
    """Put `case` back to `snap`; returns why not, or None."""
    raw = snap.get("suite_id")
    home = raw if isinstance(raw, int) else case.suite_id
    suite = suites.get(home)
    if suite is None:
        return "bộ testcase cũ không còn"
    if not editable.get(case.suite_id) or not editable.get(suite.id):
        return "bạn không có quyền sửa bộ testcase này"
    try:
        values = tc.clean({k: v for k, v in snap.items() if k in tc.RESTORED}, partial=True)
    except tc.FieldError as exc:
        return str(exc)
    if "custom" in snap and "custom" not in values:
        values["custom"] = dict(snap.get("custom") or {})
    moving = suite.id != case.suite_id
    values = tc.check_refs(session, suite, values, lenient=True, current=None if moving else case)
    try:
        status = statuses(
            suite.business_project_id,
            before=case.status,
            wanted=values.get("status"),
            content_changed=False,
        )
    except HTTPException:
        return "cần quyền duyệt (tests.approve) để đặt lại Đã duyệt"
    if status is not None:
        values["status"] = status
    code = snap.get("code") if moving else None
    if moving:
        if "automation_id" not in values:
            values["automation_id"] = None  # taken in the old suite meanwhile
        values["suite_id"] = suite.id
        values["position"] = tc.next_position(session, suite.id)
    old_status = case.status
    if not tc.change(session, case, values, user_id=principal.user_id, via="restore"):
        return None
    if moving and code and not tc.code_taken(session, suite.id, str(code), case.id):
        case.code = str(code)
    tc_events.case_status_changed(
        session, suite.business_project_id, case.id, old_status, case.status
    )
    return None


def _delete_copies(
    session: Session, principal: Principal, ids: list[int], skipped: list[Skip], *, own: bool
) -> int:
    """Delete the copies an op made, but not those with a result or changed since (revision
    over 1); someone else's copies (a manager undoing) also need tests.delete there."""
    if not ids:
        return 0
    cases = list(
        session.scalars(
            select(TestCase).where(TestCase.id.in_(ids)).order_by(TestCase.id).with_for_update()
        )
    )
    tested = set(
        session.scalars(
            select(TestRunTest.case_id)
            .join(TestResult, TestResult.test_id == TestRunTest.id)
            .where(TestRunTest.case_id.in_(ids))
        )
    )
    suites = {
        s.id: s
        for s in session.scalars(
            select(TestSuite).where(TestSuite.id.in_({c.suite_id for c in cases}))
        )
    }
    editable = {i: tc.may_edit_suite(session, principal, s) for i, s in suites.items()}
    deletable = {
        i: own or any(approval.may_delete(principal, bp) for bp in tc.projects_of(session, s))
        for i, s in suites.items()
    }
    gone: list[TestCase] = []
    for case in cases:
        if case.id in tested:
            skipped.append(Skip(case.id, "bản sao đã có kết quả test"))
        elif case.revision > 1:
            skipped.append(Skip(case.id, "đã sửa sau khi sao chép"))
        elif not editable.get(case.suite_id):
            skipped.append(Skip(case.id, "bạn không có quyền sửa bộ testcase này"))
        elif not deletable.get(case.suite_id):
            skipped.append(Skip(case.id, "bạn không có quyền xoá testcase (tests.delete)"))
        else:
            gone.append(case)
    refresh_open_snapshots(session, [c.id for c in gone])
    for case in gone:
        session.delete(case)
    return len(gone)
