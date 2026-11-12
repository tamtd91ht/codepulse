"""The testcase audit trail (docs/specs/30 §15.14, G12): who changed what, when, before and after.

A SQLAlchemy Session listener writes one `audit_log` row per created, changed or deleted object
of the tracked models (suites, sections, cases, shared steps, runs, plans, milestones,
configurations, custom fields, statuses, a dự án's test settings, baselines, report schedules,
permission sets and their items, given sets, API tokens):

- `action` "<entity>.<create|update|delete>", `target` "<entity>:<id>";
- `details` `{entity, id, op, changes: [{field, before, after}]}` with the changed columns only
  (never `search`, `created_at`, `updated_at`; a value longer than 2 000 characters is cut; a
  secret column, any named like *token*, *secret*, *hash*, *password*, shows "***");
- `business_project_id`: the dự án of the object (its own column, its suite's, its configuration
  group's); null for the global ones (permission sets, given sets, API tokens);
- `actor_user_id`: the signed-in person, or a token's owner (`set_actor`, called where the
  principal is resolved); null for background jobs.

How: `after_flush` (the ids of new rows are known, attribute history is still there) collects the
rows from the attribute history only, then writes them with one Core INSERT on the flush's own
connection: same transaction, no new flush (so no recursion), cheap for bulk work. ORM bulk
DELETE statements on a tracked model (`session.execute(delete(Model).where(...))`) are caught in
`do_orm_execute`: the rows about to go are read first; when the same keys are added again later in
the transaction (a permission set's items rewritten) the pair is written as one update. A
SAVEPOINT rolled back takes back only what was captured inside it (a snapshot at its start);
the outermost rollback forgets everything.
Not tracked: test results (they keep their own history), Core / bulk UPDATE statements (the CI's
computed `flaky` flag) and bulk INSERT ... RETURNING (CI's auto-created cases), whose source is a
system job.
"""

import json
import re
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Table, delete, event, insert, inspect, select, update
from sqlalchemy.orm import (
    InstanceState,
    Mapper,
    ORMExecuteState,
    Session,
    SessionTransaction,
    UOWTransaction,
)
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import ClauseElement

from app.logging import request_id_var
from app.models import (
    ApiToken,
    AuditLog,
    BusinessProject,
    PermissionSet,
    PermissionSetItem,
    TestBaseline,
    TestCase,
    TestConfig,
    TestConfigGroup,
    TestField,
    TestMilestone,
    TestPlan,
    TestProjectSettings,
    TestReportSchedule,
    TestRun,
    TestSection,
    TestSharedStep,
    TestStatus,
    TestSuite,
    TestSuiteLink,
    UserPermissionSet,
)

VALUE_LIMIT = 2000
CUT = "..."
MASK = "***"
SKIPPED = frozenset({"search", "created_at", "updated_at"})
SECRET = re.compile(r"token|secret|hash|password", re.IGNORECASE)
ACTOR_KEY = "audit_actor"
PENDING_KEY = "audit_pending_deletes"
WRITTEN_KEY = "audit_written"
SAVED_KEY = "audit_savepoints"  # {SAVEPOINT transaction: (pending, written) when it began}
ENDED_KEY = "audit_savepoint_ended"  # (the SAVEPOINT just closed, its snapshot)

# The person behind the work in progress; `Session.info[ACTOR_KEY]` wins (a sync FastAPI
# dependency runs in a worker thread whose context does not flow back to the endpoint, while the
# request's session is the same object), the variable serves code without a request session.
actor_var: ContextVar[int | None] = ContextVar("audit_actor", default=None)


@dataclass(frozen=True)
class Tracked:
    entity: str
    # How the dự án is found: "direct" (its own business_project_id), "suite" (suite_id ->
    # test_suites), "group" (group_id -> test_config_groups), "none" (global).
    project: str
    # Columns not worth a row of their own (touched by every call, or by the scheduler).
    skip: frozenset[str] = frozenset()
    columns: tuple[str, ...] = field(default=())
    secrets: frozenset[str] = frozenset()


_SPECS: dict[type[Any], tuple[str, str, frozenset[str]]] = {
    TestSuite: ("suite", "direct", frozenset()),
    TestSuiteLink: ("suite_link", "suite", frozenset()),
    TestSection: ("section", "suite", frozenset()),
    TestCase: ("case", "suite", frozenset()),
    TestSharedStep: ("shared_step", "direct", frozenset()),
    TestRun: ("run", "direct", frozenset()),
    TestPlan: ("plan", "direct", frozenset()),
    TestMilestone: ("milestone", "direct", frozenset()),
    TestConfigGroup: ("config_group", "direct", frozenset()),
    TestConfig: ("config", "group", frozenset()),
    TestField: ("field", "direct", frozenset()),
    TestStatus: ("status", "direct", frozenset()),
    TestProjectSettings: ("project_settings", "direct", frozenset()),
    TestBaseline: ("baseline", "direct", frozenset()),
    TestReportSchedule: ("report_schedule", "direct", frozenset({"last_sent_at"})),
    PermissionSet: ("permission_set", "none", frozenset()),
    PermissionSetItem: ("permission_set_item", "none", frozenset()),
    UserPermissionSet: ("user_permission_set", "none", frozenset()),
    # A token is a person's own, not a dự án's (§15.14: admins see it); `last_used_at` moves on
    # every call it makes.
    ApiToken: ("api_token", "none", frozenset({"last_used_at"})),
}


def _tracked(cls: type[Any], entity: str, project: str, skip: frozenset[str]) -> Tracked:
    mapper = inspect(cls)
    keys = tuple(p.key for p in mapper.column_attrs if p.key not in SKIPPED | skip)
    return Tracked(
        entity=entity,
        project=project,
        skip=skip,
        columns=keys,
        secrets=frozenset(k for k in keys if SECRET.search(k)),
    )


TRACKED: dict[type[Any], Tracked] = {cls: _tracked(cls, *spec) for cls, spec in _SPECS.items()}
_TABLE = cast(Table, AuditLog.__table__)


# -- the actor ------------------------------------------------------------------------------------


def set_actor(session: Session, user_id: int | None) -> None:
    """Who the changes made through `session` (and this context) are attributed to."""
    session.info[ACTOR_KEY] = user_id
    actor_var.set(user_id)


def _actor(session: Session) -> int | None:
    if ACTOR_KEY in session.info:
        return cast(int | None, session.info[ACTOR_KEY])
    return actor_var.get()


# -- values ---------------------------------------------------------------------------------------


def _cut(text: str) -> str:
    return text if len(text) <= VALUE_LIMIT else text[:VALUE_LIMIT] + CUT


def plain(value: Any) -> Any:
    """A JSON value for the trail; long ones cut."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, str):
        return _cut(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, ClauseElement):
        return None  # a SQL expression: its value is the database's
    if isinstance(value, dict | list | tuple):
        text = json.dumps(value, default=str, ensure_ascii=False)
        return json.loads(text) if len(text) <= VALUE_LIMIT else text[:VALUE_LIMIT] + CUT
    return _cut(str(value))


def _shown(spec: Tracked, key: str, value: Any) -> Any:
    if key in spec.secrets:
        return None if value is None else MASK
    return plain(value)


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# -- rows -----------------------------------------------------------------------------------------


@dataclass
class _Change:
    spec: Tracked
    op: str
    ident: Any
    values: dict[str, Any]  # what resolves the dự án (business_project_id / suite_id / group_id)
    changes: list[dict[str, Any]]


def _ident(mapper: Mapper[Any], state: InstanceState[Any]) -> Any:
    cols = mapper.primary_key
    values = [state.dict.get(mapper.get_property_by_column(c).key) for c in cols]
    if any(v is None for v in values) and state.key is not None:
        values = list(state.key[1])
    if len(values) == 1:
        return values[0]
    return ":".join(str(v) for v in values)


def _row_ident(mapper: Mapper[Any], row: dict[str, Any]) -> Any:
    values = [row.get(mapper.get_property_by_column(c).key) for c in mapper.primary_key]
    return values[0] if len(values) == 1 else ":".join(str(v) for v in values)


_LOOKUP_KEYS = ("business_project_id", "suite_id", "group_id")


def _lookup(data: dict[str, Any]) -> dict[str, Any]:
    return {k: data[k] for k in _LOOKUP_KEYS if k in data}


def _created(spec: Tracked, mapper: Mapper[Any], state: InstanceState[Any]) -> _Change:
    data = state.dict
    changes = [
        {"field": k, "before": None, "after": _shown(spec, k, data[k])}
        for k in spec.columns
        if k in data and not _empty(data[k])
    ]
    return _Change(spec, "create", _ident(mapper, state), _lookup(data), changes)


def _updated(spec: Tracked, mapper: Mapper[Any], state: InstanceState[Any]) -> _Change | None:
    touched = state.committed_state
    if not touched:
        return None
    changes = []
    for key in spec.columns:
        if key not in touched:
            continue
        hist = state.attrs[key].history
        if not hist.added and not hist.deleted:
            continue
        before = hist.deleted[0] if hist.deleted else None
        after = hist.added[0] if hist.added else None
        if before == after or isinstance(after, ClauseElement):
            continue
        changes.append(
            {"field": key, "before": _shown(spec, key, before), "after": _shown(spec, key, after)}
        )
    if not changes:
        return None
    return _Change(spec, "update", _ident(mapper, state), _lookup(state.dict), changes)


def _deleted(spec: Tracked, ident: Any, data: dict[str, Any]) -> _Change:
    changes = [
        {"field": k, "before": _shown(spec, k, data[k]), "after": None}
        for k in spec.columns
        if k in data and not _empty(data[k])
    ]
    return _Change(spec, "delete", ident, _lookup(data), changes)


def _replaced(spec: Tracked, ident: Any, old: dict[str, Any], new: dict[str, Any]) -> _Change:
    """Deleted in bulk and added again under the same key: one update (or nothing)."""
    changes = [
        {"field": k, "before": _shown(spec, k, old.get(k)), "after": _shown(spec, k, new.get(k))}
        for k in spec.columns
        if k in new and plain(old.get(k)) != plain(new.get(k))
    ]
    return _Change(spec, "update", ident, _lookup(new), changes)


def _parents(
    session: Session, model: type[Any], ids: set[int], seen: Iterable[Any]
) -> dict[int, int | None]:
    """{id: business_project_id} of suites / config groups: those in this session first (a
    parent deleted in this same flush is gone from the database), then one query."""
    found: dict[int, int | None] = {}
    for obj in seen:
        if isinstance(obj, model):
            data = inspect(obj).dict
            if data.get("id") in ids:
                found[data["id"]] = data.get("business_project_id")
    mapper = inspect(model)
    for pid in ids - set(found):
        obj = session.identity_map.get(mapper.identity_key_from_primary_key((pid,)))
        if obj is not None and "business_project_id" in inspect(obj).dict:
            found[pid] = inspect(obj).dict["business_project_id"]
    missing = ids - set(found)
    if missing:
        table = mapper.local_table
        rows = session.connection().execute(
            select(table.c.id, table.c.business_project_id).where(table.c.id.in_(missing))
        )
        found.update({int(r[0]): r[1] for r in rows})
    return found


def _write(
    session: Session,
    changes: list[_Change],
    seen: Iterable[Any] = (),
    manual: dict[tuple[str, str], AuditLog] | None = None,
) -> None:
    changes = [c for c in changes if c.changes or c.op != "update"]
    if not changes:
        return
    seen = list(seen)
    suite_ids = {
        int(c.values["suite_id"])
        for c in changes
        if c.spec.project == "suite" and c.values.get("suite_id") is not None
    }
    group_ids = {
        int(c.values["group_id"])
        for c in changes
        if c.spec.project == "group" and c.values.get("group_id") is not None
    }
    suites = _parents(session, TestSuite, suite_ids, seen) if suite_ids else {}
    groups = _parents(session, TestConfigGroup, group_ids, seen) if group_ids else {}

    def project(c: _Change) -> int | None:
        if c.spec.project == "direct":
            return cast(int | None, c.values.get("business_project_id"))
        if c.spec.project == "suite":
            return suites.get(cast(int, c.values.get("suite_id")))
        if c.spec.project == "group":
            return groups.get(cast(int, c.values.get("group_id")))
        return None

    alive: set[int] = set()
    projects = {project(c) for c in changes} - {None}
    if projects:
        # A dự án deleted in this same flush: the row stays, unattached (the FK would refuse it).
        alive = set(
            session.connection().scalars(
                select(BusinessProject.id).where(BusinessProject.id.in_(projects))
            )
        )
    actor, request_id = _actor(session), request_id_var.get()
    rows: list[dict[str, Any]] = [
        {
            "actor_user_id": actor,
            "action": f"{c.spec.entity}.{c.op}",
            "target": f"{c.spec.entity}:{c.ident}",
            "details": {"entity": c.spec.entity, "id": c.ident, "op": c.op, "changes": c.changes},
            "request_id": request_id,
            "business_project_id": bp if (bp := project(c)) is not None and bp in alive else None,
        }
        for c in changes
    ]
    fresh = []
    for row in rows:
        same = manual.pop((row["action"], row["target"]), None) if manual else None
        if same is not None:
            _fold(session, same, row)
        else:
            fresh.append(row)
    if not fresh:
        return
    ids = session.connection().scalars(
        insert(_TABLE).returning(_TABLE.c.id, sort_by_parameter_order=True), fresh
    )
    written = _written(session)
    for row_id, row in zip(ids, fresh, strict=True):
        written[(row["action"], row["target"])] = (row_id, row)


def _written(session: Session) -> dict[tuple[str, str], tuple[int, dict[str, Any]]]:
    """The trail rows written in this transaction, by (action, target)."""
    return cast(
        dict[tuple[str, str], tuple[int, dict[str, Any]]],
        session.info.setdefault(WRITTEN_KEY, {}),
    )


def _fold(session: Session, entry: AuditLog, row: dict[str, Any]) -> None:
    """A hand-written audit row for the same action on the same object in this transaction
    (e.g. "permission_set.update" by its endpoint): one row, the endpoint's details plus the
    trail's before / after."""
    details = {**(entry.details or {}), **row["details"]}
    bp = entry.business_project_id or row["business_project_id"]
    session.connection().execute(
        update(_TABLE)
        .where(_TABLE.c.id == entry.id)
        .values(details=details, business_project_id=bp)
    )
    set_committed_value(entry, "details", details)
    set_committed_value(entry, "business_project_id", bp)


# -- listeners ------------------------------------------------------------------------------------


def _pending(session: Session) -> dict[tuple[str, Any], tuple[Tracked, dict[str, Any]]]:
    return cast(
        dict[tuple[str, Any], tuple[Tracked, dict[str, Any]]],
        session.info.setdefault(PENDING_KEY, {}),
    )


def _flush_pending(session: Session, pending: dict[tuple[str, Any], Any]) -> list[_Change]:
    out = [_deleted(spec, ident, data) for (_e, ident), (spec, data) in pending.items()]
    pending.clear()
    return out


def _manual(session: Session) -> dict[tuple[str, str], AuditLog]:
    """Hand-written audit rows of this flush; one matching a trail row written by an earlier
    flush of the transaction takes that row in (the trail row goes)."""
    out: dict[tuple[str, str], AuditLog] = {}
    written = session.info.get(WRITTEN_KEY)
    for obj in session.new:
        if not isinstance(obj, AuditLog) or obj.target is None:
            continue
        key = (obj.action, obj.target)
        earlier = written.pop(key, None) if written else None
        if earlier is not None:
            _fold(session, obj, earlier[1])
            session.connection().execute(delete(_TABLE).where(_TABLE.c.id == earlier[0]))
        else:
            out[key] = obj
    return out


def after_flush(session: Session, _ctx: UOWTransaction) -> None:
    pending = session.info.get(PENDING_KEY) or {}
    manual = _manual(session)
    changes: list[_Change] = []
    for obj in session.new:
        spec = TRACKED.get(type(obj))
        if spec is None:
            continue
        state = inspect(obj)
        mapper = state.mapper
        ident = _ident(mapper, state)
        old = pending.pop((spec.entity, ident), None) if pending else None
        if old is not None:
            changes.append(_replaced(spec, ident, old[1], state.dict))
        else:
            changes.append(_created(spec, mapper, state))
    for obj in session.dirty:
        spec = TRACKED.get(type(obj))
        if spec is None:
            continue
        state = inspect(obj)
        change = _updated(spec, state.mapper, state)
        if change is not None:
            changes.append(change)
    deleted = list(session.deleted)
    for obj in deleted:
        spec = TRACKED.get(type(obj))
        if spec is None:
            continue
        state = inspect(obj)
        changes.append(_deleted(spec, _ident(state.mapper, state), state.dict))
    if pending:
        changes += _flush_pending(session, pending)
    if changes:
        _write(session, changes, deleted, manual)


def do_orm_execute(orm_state: ORMExecuteState) -> None:
    """A bulk DELETE of a tracked model: read the rows it removes, kept until the next flush or
    the commit (a rewrite adds the same keys back)."""
    if not orm_state.is_delete:
        return
    mapper = orm_state.bind_mapper
    if mapper is None:
        return
    spec = TRACKED.get(mapper.class_)
    if spec is None:
        return
    session = orm_state.session
    if session.autoflush and (session.new or session.dirty or session.deleted):
        session.flush()
    table = mapper.local_table
    where = getattr(orm_state.statement, "whereclause", None)
    query = select(table)
    if where is not None:
        query = query.where(where)
    pending = _pending(session)
    for row in session.connection().execute(query).mappings():
        data = {mapper.get_property_by_column(c).key: row[c.key] for c in table.columns}
        pending[(spec.entity, _row_ident(mapper, data))] = (spec, data)


def before_commit(session: Session) -> None:
    pending = session.info.get(PENDING_KEY)
    if not pending:
        return
    if session.new or session.dirty or session.deleted:
        session.flush()  # after_flush pairs them with re-added rows and writes the rest
    if pending:
        _write(session, _flush_pending(session, pending))


def _forget(session: Session, *_args: Any) -> None:
    session.info.pop(PENDING_KEY, None)
    session.info.pop(WRITTEN_KEY, None)
    session.info.pop(SAVED_KEY, None)
    session.info.pop(ENDED_KEY, None)


def _saved(session: Session) -> dict[SessionTransaction, tuple[dict[Any, Any], dict[Any, Any]]]:
    return cast(
        dict[SessionTransaction, tuple[dict[Any, Any], dict[Any, Any]]],
        session.info.setdefault(SAVED_KEY, {}),
    )


def after_transaction_create(session: Session, transaction: SessionTransaction) -> None:
    """A SAVEPOINT begins: what the trail holds now is what its rollback goes back to."""
    if transaction.nested:
        pending = dict(session.info.get(PENDING_KEY) or {})
        written = dict(session.info.get(WRITTEN_KEY) or {})
        _saved(session)[transaction] = (pending, written)


def after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """A SAVEPOINT ends (released or rolled back): its snapshot is dropped, kept aside for the
    `after_soft_rollback` SQLAlchemy fires right after this one when it was a rollback."""
    saved = session.info.get(SAVED_KEY)
    snapshot = saved.pop(transaction, None) if saved else None
    if snapshot is not None:
        session.info[ENDED_KEY] = (transaction, snapshot)
    else:
        session.info.pop(ENDED_KEY, None)


def after_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    """The outermost rollback forgets everything; a SAVEPOINT's rollback only what was captured
    inside it (the rows read for a bulk DELETE, the trail rows it wrote are gone with it)."""
    ended = session.info.pop(ENDED_KEY, None)
    if not previous.nested:
        if previous.parent is None:
            _forget(session)
        return
    if ended is None or ended[0] is not previous:
        return
    snapshot = ended[1]
    pending, written = snapshot
    session.info[PENDING_KEY] = pending
    session.info[WRITTEN_KEY] = written


_LISTENERS: tuple[tuple[str, Callable[..., Any]], ...] = (
    ("after_flush", after_flush),
    ("do_orm_execute", do_orm_execute),
    ("before_commit", before_commit),
    ("after_transaction_create", after_transaction_create),
    ("after_transaction_end", after_transaction_end),
    ("after_soft_rollback", after_soft_rollback),
    ("after_commit", _forget),
)


def install() -> None:
    """On every Session (idempotent): the app's sessionmaker, the workers', the tests'."""
    for name, fn in _LISTENERS:
        if not event.contains(Session, name, fn):
            event.listen(Session, name, fn)


install()
