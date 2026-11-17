"""Find anything (docs/specs/31 §21.4): a condition tree over tasks, the full-text search that
ignores Vietnamese accents, the visibility of saved filters and their digests.

A tree is `{all: [node, ...]}` or `{any: [...]}`; a node is a condition `{field, op, value}` or
another group. The List puts the chips of one field in an `any` group (OR) and the fields in
`all` (AND). Fields are what `GET /tasks` filters on, plus reporter, watcher, has_mr,
has_critical, dod_met, test_failed, blocked, age_in_column_hours, points, created, resolved and
custom fields (`custom:<id>`, by the field's kind).

The full text lives in `tasks.search_text` (key, title, description and comments folded: lower
case, accents dropped), kept by the session hook below, and is matched with ILIKE on the folded
words: `unaccent` is an extension a managed database may not allow."""

import logging
import math
import re
import typing
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from itertools import chain
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import (
    Integer,
    Numeric,
    Select,
    Table,
    and_,
    any_,
    bindparam,
    case,
    cast,
    event,
    exists,
    false,
    func,
    inspect,
    literal,
    not_,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session, aliased

from app.models import (
    Sprint,
    Task,
    TaskComment,
    TaskField,
    TaskFilter,
    TaskGitLink,
    TaskLink,
    TaskPriority,
    TaskSpace,
    TaskStatus,
    TaskTestLink,
    TaskWatcher,
    TeamMember,
)

if TYPE_CHECKING:
    from app.api.security import Principal

log = logging.getLogger(__name__)

MAX_TEXT = 200_000
MAX_LEAVES = 60
MAX_DEPTH = 4
MAX_VALUES = 100
# DoD is worked out task by task (its code, its tests): only for this many open tasks.
DOD_CAP = 1000

OPS = ("is", "is_not", "empty", "not_empty", "before", "after", "within_days", "gte", "lte")
# kind -> the operators it takes.
KIND_OPS: dict[str, tuple[str, ...]] = {
    "text": ("is", "is_not", "empty", "not_empty"),
    "ref": ("is", "is_not", "empty", "not_empty"),
    "enum": ("is", "is_not", "empty", "not_empty"),
    "person": ("is", "is_not", "empty", "not_empty"),
    "array": ("is", "is_not", "empty", "not_empty"),
    "sprint": ("is", "is_not", "empty", "not_empty"),
    "parent": ("is", "is_not", "empty", "not_empty"),
    "date": ("is", "before", "after", "within_days", "empty", "not_empty"),
    "number": ("is", "gte", "lte", "empty", "not_empty"),
    "hours": ("gte", "lte", "empty", "not_empty"),
    "bool": ("is",),
    "dod": ("is",),
}
FIELDS: dict[str, str] = {
    "text": "text",
    "space": "ref",
    "status": "ref",
    "version": "ref",
    "category": "enum",
    "type": "enum",
    "priority": "enum",
    "resolution": "enum",
    "assignee": "person",
    "reporter": "person",
    "watcher": "person",
    "label": "array",
    "component": "array",
    "sprint": "sprint",
    "parent": "parent",
    "due": "date",
    "created": "date",
    "updated": "date",
    "resolved": "date",
    "points": "number",
    "age_in_column_hours": "hours",
    "has_mr": "bool",
    "has_critical": "bool",
    "test_failed": "bool",
    "blocked": "bool",
    "dod_met": "dod",
}
CUSTOM_KINDS = {
    "text": "text",
    "number": "number",
    "date": "date",
    "select": "enum",
    "user": "person",
}
DOD_VALUES = ("met", "unmet", "blocking")
CUSTOM_RE = re.compile(r"^custom:(\d{1,9})$")
NUMBER_RE = r"^-?[0-9]+(\.[0-9]+)?$"


def fold(text: str | None) -> str:
    """Lower case, Vietnamese accents dropped, spaces collapsed ("Đăng  nhập" → "dang nhap")."""
    plain = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    out = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    return " ".join(out.split())[:MAX_TEXT]


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def text_match(q: str) -> Any:
    """The words of `q`, each somewhere in the folded text; or the task's key."""
    words = fold(q).split()[:10]
    if not words:
        return true()
    return or_(
        Task.key == q.strip().upper(),
        and_(*[Task.search_text.ilike(_like(w), escape="\\") for w in words]),
    )


def _id_in(ids: list[int]) -> Any:
    """`tasks.id = ANY(:ids)`: one array parameter however many ids (an IN list binds one each)."""
    if not ids:
        return false()
    return Task.id == any_(literal(ids, type_=ARRAY(Integer)))


# -- the condition tree ----------------------------------------------------------------------------


def _bad(message: str) -> HTTPException:
    return HTTPException(422, f"bộ lọc không hợp lệ: {message}")


def kind_of(field: str, custom: dict[int, TaskField] | None = None) -> str:
    if field in FIELDS:
        return FIELDS[field]
    m = CUSTOM_RE.match(field)
    if m:
        if custom is None:
            return "custom"
        f = custom.get(int(m.group(1)))
        if f is None:
            raise _bad(f"không thấy trường tuỳ biến {m.group(1)}")
        return CUSTOM_KINDS[f.kind]
    raise _bad(f"trường không biết: {field}")


def _values(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    out = []
    for v in raw:
        if v is None or isinstance(v, dict | list):
            continue
        text = str(v).strip()[:200]
        if text and text not in out:
            out.append(text)
    if len(out) > MAX_VALUES:
        raise _bad(f"tối đa {MAX_VALUES} giá trị mỗi điều kiện")
    return out


def clean_tree(tree: Any) -> dict[str, Any]:
    """The tree checked and normalised: `{all: [...]}` at the top, each condition with a known
    field, an operator its kind takes and the value it needs. 422 otherwise."""
    if tree is None or tree == {}:
        return {"all": []}
    count = 0

    def node(n: Any, depth: int) -> dict[str, Any]:
        nonlocal count
        if not isinstance(n, dict):
            raise _bad("mỗi nút là {field, op, value}, {all: [...]} hoặc {any: [...]}")
        group = "all" if "all" in n else "any" if "any" in n else None
        if group is not None:
            if depth >= MAX_DEPTH:
                raise _bad(f"lồng tối đa {MAX_DEPTH} cấp")
            items = n[group]
            if not isinstance(items, list):
                raise _bad(f"{group} phải là danh sách")
            count += 1
            if count > MAX_LEAVES:
                raise _bad(f"tối đa {MAX_LEAVES} điều kiện và nhóm")
            return {group: [node(x, depth + 1) for x in items]}
        count += 1
        if count > MAX_LEAVES:
            raise _bad(f"tối đa {MAX_LEAVES} điều kiện và nhóm")
        field = str(n.get("field") or "")
        op = str(n.get("op") or "is")
        kind = kind_of(field)
        if op not in OPS:
            raise _bad(f"toán tử không biết: {op}")
        if kind != "custom" and op not in KIND_OPS[kind]:
            raise _bad(f"{field} không dùng toán tử {op}")
        out: dict[str, Any] = {"field": field, "op": op}
        if op in ("empty", "not_empty"):
            return out
        values = _values(n.get("value"))
        if not values:
            raise _bad(f"{field} cần giá trị")
        if op in ("before", "after"):
            out["value"] = _day(values[0]).isoformat()
        elif op == "within_days":
            out["value"] = _int(values[0], 0, 3650)
        elif op in ("gte", "lte"):
            out["value"] = _int(values[0], 0, 87600) if kind == "hours" else _number(values[0])
        elif kind == "bool":
            out["value"] = _bool(values[0])
        elif kind == "dod":
            if values[0] not in DOD_VALUES:
                raise _bad(f"dod_met là {', '.join(DOD_VALUES)}")
            out["value"] = values[0]
        elif kind in ("text",):
            out["value"] = values[0]
        elif kind == "date":
            v = values[0]
            out["value"] = v if field == "due" and v in ("overdue", "week") else _day(v).isoformat()
        elif kind == "number":
            out["value"] = _number(values[0])
        else:
            out["value"] = values
        return out

    root = node(tree, 0)
    return root if "all" in root else {"all": [root]}


def _day(v: str) -> date:
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise _bad(f"ngày không hợp lệ: {v}") from None


def _int(v: str, lo: int, hi: int) -> int:
    try:
        n = int(float(v))
    except ValueError:
        raise _bad(f"cần số: {v}") from None
    if not lo <= n <= hi:
        raise _bad(f"số từ {lo} đến {hi}")
    return n


def _number(v: Any) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise _bad(f"cần số: {v}") from None
    if not math.isfinite(n) or abs(n) > 1e12:
        raise _bad(f"số không hợp lệ: {v}")
    return n


def _bool(v: str) -> bool:
    low = v.lower()
    if low in ("true", "1", "yes", "co", "có"):
        return True
    if low in ("false", "0", "no", "khong", "không"):
        return False
    raise _bad(f"cần true / false: {v}")


def leaves(tree: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for group in ("all", "any"):
        for n in tree.get(group, []):
            if "field" in n:
                yield n
            else:
                yield from leaves(n)


def from_query(query: dict[str, Any]) -> dict[str, Any]:
    """The GET /tasks params of an older saved filter as a tree."""
    out: list[dict[str, Any]] = []
    for key, raw in (query or {}).items():
        values = _values(raw)
        if not values:
            continue
        if key == "q":
            out.append({"field": "text", "op": "is", "value": values[0]})
        elif key == "due":
            out.append({"field": "due", "op": "is", "value": values[0]})
        elif key == "updated_from":
            day = _day(values[0]) - timedelta(days=1)
            out.append({"field": "updated", "op": "after", "value": day.isoformat()})
        elif key in FIELDS:
            out.append({"field": key, "op": "is", "value": values})
    return clean_tree({"all": out})


class Builder:
    """Turns a clean tree into a WHERE clause over `Task`, for one person."""

    def __init__(
        self,
        session: Session,
        principal: "Principal",
        scope: Select[Any],
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.principal = principal
        self.scope = scope  # select(Task.id) of every task the person may find here
        self.now = now or datetime.now(UTC)
        self.today = self.now.date()
        self._custom: dict[int, TaskField] | None = None
        self._computed: dict[str, Any] = {}

    def where(self, tree: dict[str, Any]) -> Any:
        return self._node(tree)

    def _node(self, n: dict[str, Any]) -> Any:
        if "all" in n:
            parts = [self._node(x) for x in n["all"]]
            return and_(true(), *parts) if parts else true()
        if "any" in n:
            parts = [self._node(x) for x in n["any"]]
            return or_(false(), *parts) if parts else true()
        return self._leaf(n)

    # -- helpers -----------------------------------------------------------------------------------

    def _ids(self, values: list[str]) -> list[int]:
        return [int(v) for v in values if v.isdigit()]

    def _people(self, values: list[str]) -> tuple[list[int], bool]:
        ids = self._ids(values)
        if "me" in values:
            ids.append(self.principal.user_id)
        return ids, "none" in values

    def _start(self, d: date) -> datetime:
        return datetime.combine(d, datetime.min.time(), UTC)

    def custom_fields(self) -> dict[int, TaskField]:
        if self._custom is None:
            self._custom = {f.id: f for f in self.session.scalars(select(TaskField))}
        return self._custom

    # -- one condition -----------------------------------------------------------------------------

    def _leaf(self, n: dict[str, Any]) -> Any:
        field, op, value = n["field"], n["op"], n.get("value")
        m = CUSTOM_RE.match(field)
        if m:
            return self._custom_leaf(int(m.group(1)), op, value)
        kind = FIELDS[field]
        if kind == "bool":
            return self._bool(field, bool(value))
        if kind == "dod":
            return _id_in(self._dod()[str(value)])
        if kind == "text":
            return self._text(Task.search_text, op, value, folded=True)
        if kind == "date":
            return self._date(field, op, value)
        if kind == "number":
            return self._number(Task.story_points, op, value)
        if kind == "hours":
            column = Task.status_changed_at
            if op == "empty":
                return column.is_(None)
            if op == "not_empty":
                return column.is_not(None)
            edge = self.now - timedelta(hours=float(str(value)))
            return column <= edge if op == "gte" else column >= edge
        positive, column = self._positive(field, kind, value or [])
        if op == "is":
            return positive
        if op == "is_not":
            return not_(positive)
        return self._emptiness(field, kind, column, op == "empty")

    def _positive(self, field: str, kind: str, values: list[str]) -> tuple[Any, Any]:
        """A null-safe "is one of `values`" (never NULL, so NOT works), and the column."""
        column: Any
        parts: list[Any] = []
        if kind == "ref":
            column = {
                "space": Task.business_project_id,
                "status": Task.status_id,
                "version": Task.version_id,
            }[field]
            ids = self._ids(values)
            return and_(column.is_not(None), column.in_(ids)) if ids else false(), column
        if kind == "enum":
            if field == "category":
                return (
                    Task.status_id.in_(
                        select(TaskStatus.id).where(TaskStatus.category.in_(values))
                    ),
                    None,
                )
            column = {"type": Task.type, "priority": Task.priority, "resolution": Task.resolution}[
                field
            ]
            return and_(column.is_not(None), column.in_(values)), column
        if kind == "person":
            ids, none = self._people(values)
            if field == "watcher":
                if ids:
                    parts.append(
                        exists().where(TaskWatcher.task_id == Task.id, TaskWatcher.user_id.in_(ids))
                    )
                if none:
                    parts.append(~exists().where(TaskWatcher.task_id == Task.id))
                return or_(false(), *parts), None
            column = Task.assignee_id if field == "assignee" else Task.reporter_id
            parts = [and_(column.is_not(None), column.in_(ids))] if ids else []
            if none:
                parts.append(column.is_(None))
            return or_(false(), *parts), column
        if kind == "array":
            column = Task.labels if field == "label" else Task.components
            return or_(false(), *[column.contains([v]) for v in values]), column
        if kind == "sprint":
            ids = self._ids(values)
            if ids:
                parts.append(and_(Task.sprint_id.is_not(None), Task.sprint_id.in_(ids)))
            if "active" in values:
                active = select(Sprint.id).where(Sprint.state == "active")
                parts.append(and_(Task.sprint_id.is_not(None), Task.sprint_id.in_(active)))
            if "backlog" in values or "none" in values:
                parts.append(Task.sprint_id.is_(None))
            return or_(false(), *parts), Task.sprint_id
        # parent
        parent = aliased(Task)
        keys = [v.upper() for v in values]
        ids_q = select(parent.id).where(parent.key.in_(keys))
        return and_(Task.parent_id.is_not(None), Task.parent_id.in_(ids_q)), Task.parent_id

    def _emptiness(self, field: str, kind: str, column: Any, empty: bool) -> Any:
        if kind == "array":
            n = func.jsonb_array_length(column)
            return n == 0 if empty else n > 0
        if field == "watcher":
            has = exists().where(TaskWatcher.task_id == Task.id)
            return ~has if empty else has
        if column is None:  # category: never empty
            return false() if empty else true()
        return column.is_(None) if empty else column.is_not(None)

    def _text(self, column: Any, op: str, value: Any, folded: bool = False) -> Any:
        if op in ("empty", "not_empty"):
            blank = func.coalesce(column, "") == ""
            return blank if op == "empty" else not_(blank)
        if folded:
            match = text_match(str(value))
        else:
            words = str(value).lower().split()[:10] or [""]
            match = and_(
                column.is_not(None),
                *[func.lower(column).ilike(_like(w), escape="\\") for w in words],
            )
        return match if op == "is" else not_(match)

    def _number(self, column: Any, op: str, value: Any) -> Any:
        if op == "empty":
            return column.is_(None)
        if op == "not_empty":
            return column.is_not(None)
        n = float(value)
        if op == "gte":
            return and_(column.is_not(None), column >= n)
        if op == "lte":
            return and_(column.is_not(None), column <= n)
        return and_(column.is_not(None), column == n)

    def _date(self, field: str, op: str, value: Any) -> Any:
        column = {
            "due": Task.due_date,
            "created": Task.created_at,
            "updated": Task.updated_at,
            "resolved": Task.resolved_at,
        }[field]
        is_day = field == "due"
        if op == "empty":
            return column.is_(None)
        if op == "not_empty":
            return column.is_not(None)
        if op == "within_days":
            n = int(value)
            if is_day:  # the next N days
                return and_(column >= self.today, column <= self.today + timedelta(days=n))
            return column >= self.now - timedelta(days=n)  # the last N days
        if value == "overdue":
            not_done = Task.status_id.in_(
                select(TaskStatus.id).where(TaskStatus.category != "done")
            )
            return and_(column < self.today, not_done)
        if value == "week":
            return and_(column >= self.today, column <= self.today + timedelta(days=7))
        d = date.fromisoformat(str(value))
        if is_day:
            if op == "before":
                return column < d
            if op == "after":
                return column > d
            return column == d
        if op == "before":
            return column < self._start(d)
        if op == "after":
            return column >= self._start(d + timedelta(days=1))
        return and_(column >= self._start(d), column < self._start(d + timedelta(days=1)))

    def _custom_leaf(self, field_id: int, op: str, value: Any) -> Any:
        f = self.custom_fields().get(field_id)
        if f is None:
            raise _bad(f"không thấy trường tuỳ biến {field_id}")
        kind = CUSTOM_KINDS[f.kind]
        if op not in KIND_OPS[kind]:
            raise _bad(f"trường {f.name} không dùng toán tử {op}")
        text = Task.custom[str(field_id)].astext
        on_space = Task.business_project_id == f.business_project_id
        blank = func.coalesce(text, "") == ""
        if op == "empty":
            return and_(on_space, blank)
        if op == "not_empty":
            return and_(on_space, not_(blank))
        if isinstance(value, list) and kind in ("text", "number", "date"):
            value = value[0] if value else ""
        if kind == "number" or op in ("gte", "lte"):
            value = _number(value)
        elif kind == "date" and op != "within_days":
            value = _day(str(value)).isoformat()
        if kind == "text":
            return and_(on_space, self._text(text, op, value))
        if kind == "number":
            number = case((text.op("~")(NUMBER_RE), cast(text, Numeric)), else_=None)
            return and_(on_space, self._number(number, op, value))
        if kind == "date":
            if op == "within_days":
                end = (self.today + timedelta(days=int(value))).isoformat()
                return and_(on_space, text >= self.today.isoformat(), text <= end)
            d = str(value)[:10]
            day = func.substr(text, 1, 10)
            if op == "before":
                return and_(on_space, not_(blank), day < d)
            if op == "after":
                return and_(on_space, not_(blank), day > d)
            return and_(on_space, day == d)
        values = list(value or [])
        if kind == "person":
            ids, none = self._people(values)
            parts = [and_(not_(blank), text.in_([str(i) for i in ids]))] if ids else []
            if none:
                parts.append(blank)
            positive = or_(false(), *parts)
        else:
            positive = and_(not_(blank), text.in_(values))
        return and_(on_space, positive if op == "is" else not_(positive))

    # -- facts worked out of code and tests --------------------------------------------------------

    def _bool(self, field: str, value: bool) -> Any:
        if field == "has_mr":
            has: Any = exists().where(TaskGitLink.task_id == Task.id, TaskGitLink.kind == "mr")
        elif field == "blocked":
            blocker = aliased(Task)
            has = exists().where(
                TaskLink.to_task_id == Task.id,
                TaskLink.kind == "blocks",
                TaskLink.from_task_id == blocker.id,
                blocker.status_id.in_(select(TaskStatus.id).where(TaskStatus.category != "done")),
            )
        else:
            has = _id_in(self._critical() if field == "has_critical" else self._test_failed())
        return has if value else not_(has)

    def _critical(self) -> list[int]:
        if "critical" not in self._computed:
            from app.tasks.service import git_summaries

            ids = list(
                self.session.scalars(
                    select(TaskGitLink.task_id)
                    .where(
                        TaskGitLink.task_id.in_(self.scope),
                        TaskGitLink.kind.in_(("commit", "mr")),
                    )
                    .distinct()
                )
            )
            summary = git_summaries(self.session, ids)
            self._computed["critical"] = sorted(i for i, s in summary.items() if s["open_critical"])
        return list(self._computed["critical"])

    def _test_failed(self) -> list[int]:
        if "failed" not in self._computed:
            from app.tasks.quality import covering, latest_tests

            ids = list(
                self.session.scalars(
                    select(TaskTestLink.task_id)
                    .where(TaskTestLink.task_id.in_(self.scope), TaskTestLink.kind == "covers")
                    .distinct()
                )
            )
            cover = covering(self.session, ids)
            tests = latest_tests(self.session, sorted({c for cs in cover.values() for c in cs}))
            self._computed["failed"] = sorted(
                t
                for t, cases in cover.items()
                if any(c in tests and tests[c].status == "failed" for c in cases)
            )
        return list(self._computed["failed"])

    def _dod(self) -> dict[str, list[int]]:
        """Open tasks of dự án with DoD checks: met, unmet, and unmet where DoD is enforced
        (the task cannot be closed: "blocking")."""
        if "dod" not in self._computed:
            from app.tasks.quality import dod, dod_settings

            out: dict[str, list[int]] = {v: [] for v in DOD_VALUES}
            spaces = {
                s.business_project_id: s
                for s in self.session.scalars(
                    select(TaskSpace).where(
                        TaskSpace.business_project_id.in_(
                            select(Task.business_project_id).where(Task.id.in_(self.scope))
                        )
                    )
                )
                if dod_settings(s)["checks"]
            }
            if spaces:
                tasks = self.session.scalars(
                    select(Task)
                    .where(
                        Task.id.in_(self.scope),
                        Task.business_project_id.in_(list(spaces)),
                        Task.status_id.in_(
                            select(TaskStatus.id).where(TaskStatus.category != "done")
                        ),
                    )
                    .order_by(Task.updated_at.desc())
                    .limit(DOD_CAP)
                )
                for task in tasks:
                    space = spaces[task.business_project_id]
                    ok = all(c["ok"] for c in dod(self.session, task, space))
                    if ok:
                        out["met"].append(task.id)
                    else:
                        out["unmet"].append(task.id)
                        if dod_settings(space)["enforce"]:
                            out["blocking"].append(task.id)
            self._computed["dod"] = out
        return dict(self._computed["dod"])


# -- running a search ------------------------------------------------------------------------------

SORTS: dict[str, Any] = {
    "rank": Task.rank,
    "updated": Task.updated_at,
    "created": Task.created_at,
    "due": Task.due_date,
    "key": Task.number,
    "priority": Task.priority,
    "title": Task.title,
}


def scope(principal: "Principal", spaces: list[int]) -> Select[Any]:
    """select(Task.id): the tasks the person may find (tasks.view is the caller's check), as
    the List: dự án with Task on that the person sees, narrowed to `spaces`."""
    query = select(Task.id).where(
        Task.business_project_id.in_(select(TaskSpace.business_project_id))
    )
    visible = principal.visible_projects()
    if visible is not None:
        query = query.where(Task.business_project_id.in_(visible))
    if spaces:
        query = query.where(Task.business_project_id.in_(spaces))
    return query


def search(
    session: Session,
    principal: "Principal",
    tree: dict[str, Any],
    *,
    spaces: list[int],
    sort: str = "rank",
    order: str = "asc",
    limit: int = 100,
    offset: int = 0,
    now: datetime | None = None,
) -> tuple[list[Task], int]:
    """The tasks matching a clean tree, a page of them, and how many match."""
    base = scope(principal, spaces)
    where = Builder(session, principal, base, now).where(tree)
    query = select(Task).where(Task.id.in_(base), where)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    if sort == "priority":  # the dự án's own order: first = most urgent
        column: Any = (
            select(TaskPriority.position)
            .where(
                TaskPriority.business_project_id == Task.business_project_id,
                TaskPriority.key == Task.priority,
            )
            .correlate(Task)
            .scalar_subquery()
        )
    else:
        column = SORTS.get(sort, Task.rank)
    ordered = column.desc().nulls_last() if order == "desc" else column.asc().nulls_last()
    tasks = list(session.scalars(query.order_by(ordered, Task.id).limit(limit).offset(offset)))
    return tasks, int(total)


def quick(
    session: Session, principal: "Principal", q: str, spaces: list[int], limit: int = 20
) -> list[dict[str, Any]]:
    """The command palette's search: by key, by title, then anywhere in the text (description,
    comments), accents optional; each hit says where it matched."""
    text = q.strip()
    words = fold(text).split()[:10]
    if not words:
        return []
    base = scope(principal, spaces)
    key = text.upper()
    found = list(
        session.scalars(
            select(Task)
            .where(
                Task.id.in_(base),
                or_(Task.key.ilike(_like(key)[1:], escape="\\"), text_match(text)),
            )
            .order_by(Task.updated_at.desc())
            .limit(300)
        )
    )
    statuses = {
        s.id: s
        for s in session.scalars(
            select(TaskStatus).where(TaskStatus.id.in_({t.status_id for t in found}))
        )
    }

    def where(t: Task) -> tuple[int, str]:
        if t.key == key:
            return 0, "key"
        if t.key.startswith(key):
            return 1, "key"
        title = fold(t.title)
        if all(w in title for w in words):
            return 2, "title"
        return 3, "text"

    ranked = sorted(((where(t), i, t) for i, t in enumerate(found)), key=lambda x: (x[0][0], x[1]))
    out = []
    for (_rank, match), _i, t in ranked:
        status = statuses.get(t.status_id)
        out.append(
            {
                "key": t.key,
                "title": t.title,
                "type": t.type,
                "space": t.business_project_id,
                "status": (
                    {"id": status.id, "name": status.name, "category": status.category}
                    if status
                    else None
                ),
                "match": match,
                "snippet": _snippet(t, words) if match == "text" else None,
            }
        )
        if len(out) >= limit:
            break
    return out


def _snippet(task: Task, words: list[str], width: int = 60) -> str | None:
    """Where the first word sits in the folded text, a few words either side."""
    text = task.search_text or ""
    at = text.find(words[0])
    if at < 0:
        return None
    start = max(0, at - width)
    return ("…" if start else "") + text[start : at + len(words[0]) + width].strip() + "…"


# -- keeping search_text ---------------------------------------------------------------------------

_WATCHED = ("key", "title", "description")


def _touched(session: Session) -> set[int]:
    ids: set[int] = set()
    for obj in chain(session.new, session.dirty, session.deleted):
        if isinstance(obj, Task):
            if obj in session.deleted or obj.id is None:
                continue
            state = inspect(obj)
            if obj in session.new or any(state.attrs[a].history.has_changes() for a in _WATCHED):
                ids.add(obj.id)
        elif isinstance(obj, TaskComment) and obj.task_id is not None:
            if obj in session.dirty and not inspect(obj).attrs.body.history.has_changes():
                continue
            ids.add(obj.task_id)
    return ids


def refresh(session: Session, task_ids: Iterable[int]) -> None:
    """Rebuild the folded text of these tasks from what the database holds now; `updated_at`
    stays as it is (a comment is not an edit of the task)."""
    ids = sorted(set(task_ids))
    if not ids:
        return
    conn = session.connection()
    tasks = typing.cast(Table, Task.__table__)
    comments = typing.cast(Table, TaskComment.__table__)
    bodies: dict[int, list[str]] = defaultdict(list)
    for task_id, body in conn.execute(
        select(comments.c.task_id, comments.c.body)
        .where(comments.c.task_id.in_(ids))
        .order_by(comments.c.id)
    ).all():
        bodies[int(task_id)].append(body or "")
    rows = conn.execute(
        select(tasks.c.id, tasks.c.key, tasks.c.title, tasks.c.description).where(
            tasks.c.id.in_(ids)
        )
    ).all()
    if not rows:
        return
    conn.execute(
        update(tasks)
        .where(tasks.c.id == bindparam("tid"))
        .values(search_text=bindparam("text"), updated_at=tasks.c.updated_at),
        [
            {
                "tid": r.id,
                "text": fold(
                    " ".join([r.key or "", r.title or "", r.description or "", *bodies[r.id]])
                ),
            }
            for r in rows
        ],
    )


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _context: Any) -> None:
    """Bulk `update(Task)` statements bypass this; none of them change key, title or text."""
    try:
        ids = _touched(session)
    except Exception:  # an odd object in the session: not worth failing the write over
        log.exception("task search text: could not tell what changed")
        return
    if ids:
        refresh(session, ids)  # a failing UPDATE fails the write, as any other statement


# -- saved filters ---------------------------------------------------------------------------------


def _teams_of(session: Session, user_ids: Iterable[int], day: date) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    ids = sorted(set(user_ids))
    if not ids:
        return out
    for m in session.scalars(select(TeamMember).where(TeamMember.user_id.in_(ids))):
        if m.joined_at <= day and (m.left_at is None or m.left_at > day):
            out[m.user_id].add(m.team_id)
    return out


def visible_filters(
    session: Session, principal: "Principal", space: int | None = None
) -> list[TaskFilter]:
    """Saved filters this person sees: their own; "team" ones of someone they share a team
    with (or lead / manage); "project" ones on a dự án they see (none: everyone)."""
    query = select(TaskFilter).order_by(TaskFilter.name, TaskFilter.id)
    if space is not None:
        query = query.where(
            or_(TaskFilter.business_project_id.is_(None), TaskFilter.business_project_id == space)
        )
    rows = list(
        session.scalars(
            query.where(
                or_(
                    TaskFilter.user_id == principal.user_id,
                    TaskFilter.shared_scope.in_(("team", "project")),
                    TaskFilter.shared.is_(True),
                )
            )
        )
    )
    mine = principal.own_teams | principal.lead_teams | principal.managed_teams
    owners = _teams_of(
        session,
        [f.user_id for f in rows if f.shared_scope == "team" and f.user_id != principal.user_id],
        datetime.now(UTC).date(),
    )
    out = []
    for f in rows:
        if f.business_project_id is not None and not principal.can_view_project(
            f.business_project_id
        ):
            continue
        team = f.shared_scope == "team" and (
            principal.is_admin or bool(owners.get(f.user_id, set()) & mine)
        )
        if f.user_id == principal.user_id or scope_of(f) == "project" or team:
            out.append(f)
    return out


def scope_of(f: TaskFilter) -> str:
    """The old `POST /tasks/filters` sets `shared` only: shared is "project"."""
    return "project" if f.shared and f.shared_scope == "me" else f.shared_scope


def tree_of(f: TaskFilter) -> dict[str, Any]:
    if f.tree:
        return clean_tree(f.tree)
    return from_query(f.query or {})


# -- digests of subscribed filters -----------------------------------------------------------------

DIGEST_KIND = "filter_digest"  # not "task.*": the person asked for it, no mute / hold applies
DIGEST_HOUR = 8
DIGEST_KEYS = 10


def _local_tz() -> ZoneInfo:
    try:
        from app.config import get_settings

        return ZoneInfo(get_settings().report_tz)
    except Exception:
        return ZoneInfo("Asia/Ho_Chi_Minh")


def digest_due(sub: dict[str, Any], local: datetime) -> bool:
    if local.hour < DIGEST_HOUR or sub.get("last_sent") == local.date().isoformat():
        return False
    if sub.get("cadence") == "weekly":
        return local.weekday() == 0
    return sub.get("cadence") == "daily"


def filter_link(f: TaskFilter) -> str:
    space = f"space={f.business_project_id}&" if f.business_project_id else ""
    return f"/tasks?{space}tab=list&filter={f.id}"


def send_filter_digests(session: Session, now: datetime | None = None) -> int:
    """Each subscribed filter whose time has come: the matching tasks to its owner, in the inbox
    and on Telegram when the owner linked it (the inbox alone otherwise)."""
    now = now or datetime.now(UTC)
    local = now.astimezone(_local_tz())
    sent = 0
    for f in list(session.scalars(select(TaskFilter).where(TaskFilter.subscription.is_not(None)))):
        sub = dict(f.subscription or {})
        if not digest_due(sub, local):
            continue
        try:
            with session.begin_nested():
                sent += _digest(session, f, sub, now, local)
        except Exception:
            log.exception("filter digest failed", extra={"filter_id": f.id})
    session.flush()
    return sent


def _digest(
    session: Session, f: TaskFilter, sub: dict[str, Any], now: datetime, local: datetime
) -> int:
    from app.api.security import load_principal
    from app.config import get_settings
    from app.notifications.inbox import notify
    from app.tasks.automation import queue_telegram, telegram_linked

    f.subscription = {**sub, "last_sent": local.date().isoformat()}
    session.flush()
    try:
        principal = load_principal(session, f.user_id, get_settings())
    except HTTPException:
        return 0  # the owner left
    if not principal.can("tasks.view"):
        return 0
    if f.business_project_id is not None and not principal.can_view_project(f.business_project_id):
        return 0
    spaces = [f.business_project_id] if f.business_project_id else []
    try:
        tree = tree_of(f)
        tasks, total = search(
            session,
            principal,
            tree,
            spaces=spaces,
            sort="updated",
            order="desc",
            limit=DIGEST_KEYS,
            now=now,
        )
    except HTTPException:  # no longer a valid tree (a custom field deleted): skip, marked sent
        log.warning("filter digest skipped: invalid tree", extra={"filter_id": f.id})
        return 0
    if not total:
        return 0
    keys = ", ".join(t.key for t in tasks) + (" …" if total > len(tasks) else "")
    when = "tuần" if sub.get("cadence") == "weekly" else "ngày"
    rows = notify(
        session,
        [f.user_id],
        kind=DIGEST_KIND,
        title=f"Bộ lọc “{f.name}” ({when} {local:%d/%m}): {total} task",
        body=keys,
        link=filter_link(f),
        ref=f"filter:{f.id}:{local.date().isoformat()}",
    )
    if rows and telegram_linked(session, f.user_id):
        queue_telegram(session, rows)
    return 1
