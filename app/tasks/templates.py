"""Project templates and board modes (docs/specs/31 §19.6, §20.4).

A template gives a dự án's Task setup: the workflow (statuses, which of them the git rules use,
WIP / age hints, transitions), the task types, priorities, rules (git flags, Definition of Done,
automation), custom fields and the board mode. Built-in templates live in code here; a manager
may also save a dự án's setup as a template (`task_space_templates`) and apply it elsewhere.

Every template is handled as one JSON *payload* that names statuses instead of holding ids, so it
travels between dự án. `service.enable_space` seeds a new space from a built-in one; `apply`
applies any template to a space that is already on: everything while it has no task, only the
parts that cannot lose data once it has tasks.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tasks import (
    BOARD_MODES,
    CATEGORIES,
    FIELD_KINDS,
    PRIORITIES,
    PRIORITY_COLORS,
    TASK_TYPES,
    Task,
    TaskField,
    TaskSpace,
    TaskSpaceTemplate,
)

DEFAULT_TEMPLATE = "scrum"
PAYLOAD_VERSION = 1


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    # (name, category) in board order.
    statuses: tuple[tuple[str, str], ...]
    # Status names the git rules move tasks to; None: the rule has no status.
    start: str | None
    review: str | None
    done: str
    types: tuple[str, ...]
    board_mode: str
    # Overrides of the default rule flags.
    flags: dict[str, bool] = field(default_factory=dict)
    # Custom fields (TaskField definitions): name, kind, options, required.
    fields: tuple[dict[str, Any], ...] = ()


_SCRUM_STATUSES = (
    ("Cần làm", "todo"),
    ("Đang làm", "doing"),
    ("Đang review", "doing"),
    ("Xong", "done"),
)

TEMPLATES: dict[str, Template] = {
    "scrum": Template(
        key="scrum",
        name="Scrum",
        description="Làm theo sprint: Backlog, sprint, Board của sprint đang chạy.",
        statuses=_SCRUM_STATUSES,
        start="Đang làm",
        review="Đang review",
        done="Xong",
        types=TASK_TYPES,
        board_mode="scrum",
    ),
    "kanban": Template(
        key="kanban",
        name="Kanban",
        description="Luồng liên tục, không sprint: Board hiện mọi task đang mở.",
        statuses=(("Cần làm", "todo"), ("Đang làm", "doing"), ("Xong", "done")),
        start="Đang làm",
        review=None,
        done="Xong",
        types=("task", "bug", "subtask"),
        board_mode="kanban",
        # No review status: opening an MR leaves the task in Đang làm.
        flags={"review_on_mr": False},
    ),
    "qa": Template(
        key="qa",
        name="QA / xử lý lỗi",
        description="Theo dõi bug: xác minh, sửa, tester đóng sau khi kiểm lại.",
        statuses=(
            ("Mới", "todo"),
            ("Đang xác minh", "doing"),
            ("Đã sửa", "doing"),
            ("Đã đóng", "done"),
        ),
        start="Đang xác minh",
        review="Đã sửa",
        done="Đã đóng",
        types=("bug", "task", "subtask"),
        board_mode="scrum",
        # An MR moves the bug to Đã sửa; the tester closes it after retesting, not the merge.
        flags={"review_on_mr": True, "done_on_merge": False},
    ),
    "mobile": Template(
        key="mobile",
        name="Mobile",
        description="Scrum cho ứng dụng di động: thêm trường Nền tảng (iOS / Android / Cả hai).",
        statuses=_SCRUM_STATUSES,
        start="Đang làm",
        review="Đang review",
        done="Xong",
        types=TASK_TYPES,
        board_mode="scrum",
        fields=(
            {
                "name": "Nền tảng",
                "kind": "select",
                "options": ["iOS", "Android", "Cả hai"],
                "required": False,
            },
        ),
    ),
}


def template_or_422(key: str | None) -> Template:
    template = TEMPLATES.get(key or DEFAULT_TEMPLATE)
    if template is None:
        raise HTTPException(422, f"mẫu không biết: {key}")
    return template


def check_board_mode(mode: str) -> str:
    if mode not in BOARD_MODES:
        raise HTTPException(422, f"chế độ bảng không hợp lệ: {mode}")
    return mode


# -- payloads ------------------------------------------------------------------------------------


def builtin_payload(t: Template) -> dict[str, Any]:
    from app.tasks.service import DEFAULT_FLAGS, DEFAULT_PRIORITIES, DEFAULT_PRIORITY

    return {
        "version": PAYLOAD_VERSION,
        "types": list(t.types),
        "board_mode": t.board_mode,
        "statuses": [
            {
                "name": name,
                "category": category,
                "wip_limit": None,
                "age_warning_hours": None,
                "transitions": None,
            }
            for name, category in t.statuses
        ],
        "priorities": [
            {"name": name, "level": level, "color": color, "default": key == DEFAULT_PRIORITY}
            for key, name, level, color in DEFAULT_PRIORITIES
        ],
        "rules": {
            **DEFAULT_FLAGS,
            **t.flags,
            "start": t.start,
            "review": t.review,
            "done": t.done,
        },
        "fields": [dict(f) for f in t.fields],
    }


def snapshot(session: Session, space: TaskSpace) -> dict[str, Any]:
    """The payload of a dự án's current setup (statuses named, archived fields left out)."""
    from app.tasks import service as ts

    statuses = ts.statuses_of(session, space.business_project_id)
    names = {s.id: s.name for s in statuses}
    rules = ts.rules_of(space)
    out_rules: dict[str, Any] = {name: bool(rules.get(name)) for name in ts.RULE_FLAGS}
    for name in ("start", "review", "done"):
        target = rules.get(name)
        out_rules[name] = names.get(int(target)) if target is not None else None
    if isinstance(rules.get("dod"), dict):
        out_rules["dod"] = rules["dod"]
    if isinstance(rules.get("automation"), dict):
        out_rules["automation"] = rules["automation"]
    fields = session.scalars(
        select(TaskField)
        .where(
            TaskField.business_project_id == space.business_project_id,
            TaskField.archived.is_(False),
        )
        .order_by(TaskField.position, TaskField.id)
    )
    return {
        "version": PAYLOAD_VERSION,
        "types": list(space.types),
        "board_mode": space.board_mode,
        "statuses": [
            {
                "name": s.name,
                "category": s.category,
                "wip_limit": s.wip_limit,
                "age_warning_hours": s.age_warning_hours,
                "transitions": None
                if s.transitions is None
                else [names[i] for i in s.transitions if i in names],
            }
            for s in statuses
        ],
        "priorities": [
            {"name": x.name, "level": x.level, "color": x.color, "default": x.is_default}
            for x in ts.priorities_of(session, space.business_project_id)
        ],
        "rules": out_rules,
        "fields": [
            {
                "name": f.name,
                "kind": f.kind,
                "options": list(f.options or []),
                "required": f.required,
            }
            for f in fields
        ],
    }


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """A payload made safe to apply (a saved one may be from an older version): unknown types,
    categories, levels, colours and field kinds dropped; 422 when the workflow is unusable."""
    types = [t for t in payload.get("types") or [] if t in TASK_TYPES] or list(TASK_TYPES)
    mode = payload.get("board_mode") if payload.get("board_mode") in BOARD_MODES else "scrum"
    statuses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in payload.get("statuses") or []:
        name = str(s.get("name") or "").strip()[:60]
        if not name or name.lower() in seen or s.get("category") not in CATEGORIES:
            continue
        seen.add(name.lower())
        statuses.append(
            {
                "name": name,
                "category": s["category"],
                "wip_limit": s.get("wip_limit") if isinstance(s.get("wip_limit"), int) else None,
                "age_warning_hours": s.get("age_warning_hours")
                if isinstance(s.get("age_warning_hours"), int)
                else None,
                "transitions": None
                if s.get("transitions") is None
                else [str(x) for x in s["transitions"]],
            }
        )
    cats = {s["category"] for s in statuses}
    if "todo" not in cats or "done" not in cats:
        raise HTTPException(422, "mẫu không có trạng thái nhóm Cần làm và nhóm Xong")
    priorities: list[dict[str, Any]] = []
    pseen: set[str] = set()
    for p in payload.get("priorities") or []:
        name = str(p.get("name") or "").strip()[:40]
        if (
            not name
            or name.lower() in pseen
            or p.get("level") not in PRIORITIES
            or p.get("color") not in PRIORITY_COLORS
        ):
            continue
        pseen.add(name.lower())
        priorities.append(
            {
                "name": name,
                "level": p["level"],
                "color": p["color"],
                "default": bool(p.get("default")),
            }
        )
    fields: list[dict[str, Any]] = []
    fseen: set[str] = set()
    for f in payload.get("fields") or []:
        name = str(f.get("name") or "").strip()[:80]
        if not name or name.lower() in fseen or f.get("kind") not in FIELD_KINDS:
            continue
        fseen.add(name.lower())
        fields.append(
            {
                "name": name,
                "kind": f["kind"],
                "options": [str(o).strip()[:80] for o in f.get("options") or [] if str(o).strip()][
                    :50
                ],
                "required": bool(f.get("required")),
            }
        )
    return {
        "types": types,
        "board_mode": mode,
        "statuses": statuses,
        "priorities": priorities,
        "rules": dict(payload.get("rules") or {}),
        "fields": fields,
    }


# -- listing and lookup ----------------------------------------------------------------------------


def summary(
    key: str,
    name: str,
    description: str | None,
    payload: dict[str, Any],
    *,
    row: TaskSpaceTemplate | None = None,
    can_delete: bool = False,
) -> dict[str, Any]:
    return {
        "id": row.id if row else None,
        "key": key,
        "name": name,
        "description": description,
        "builtin": row is None or row.builtin,
        "types": list(payload.get("types") or []),
        "board_mode": payload.get("board_mode") or "scrum",
        "statuses": [
            {"name": s.get("name"), "category": s.get("category")}
            for s in payload.get("statuses") or []
        ],
        "priorities": [p.get("name") for p in payload.get("priorities") or []],
        "fields": [f.get("name") for f in payload.get("fields") or []],
        "has_dod": bool((payload.get("rules") or {}).get("dod")),
        "has_automation": bool((payload.get("rules") or {}).get("automation")),
        "created_by": row.created_by if row else None,
        "created_at": row.created_at.isoformat() if row and row.created_at else None,
        "can_delete": can_delete,
    }


def listing(session: Session, user_id: int, is_admin: bool) -> list[dict[str, Any]]:
    out = [summary(t.key, t.name, t.description, builtin_payload(t)) for t in TEMPLATES.values()]
    for row in session.scalars(select(TaskSpaceTemplate).order_by(TaskSpaceTemplate.name)):
        out.append(
            summary(
                row.key,
                row.name,
                row.description,
                row.payload,
                row=row,
                can_delete=not row.builtin and (is_admin or row.created_by == user_id),
            )
        )
    return out


def resolve(session: Session, key: str) -> tuple[str, dict[str, Any]]:
    """(name, payload) of a built-in or saved template; 422 when unknown."""
    if key in TEMPLATES:
        t = TEMPLATES[key]
        return t.name, builtin_payload(t)
    row = session.scalars(select(TaskSpaceTemplate).where(TaskSpaceTemplate.key == key)).first()
    if row is None:
        raise HTTPException(422, f"mẫu không biết: {key}")
    return row.name, dict(row.payload)


def name_of(session: Session, key: str | None) -> str | None:
    if key is None:
        return None
    if key in TEMPLATES:
        return TEMPLATES[key].name
    return session.scalar(select(TaskSpaceTemplate.name).where(TaskSpaceTemplate.key == key))


def _slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    ascii_name = folded.encode("ascii", "ignore").decode()
    return (re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "mau")[:32]


def save(
    session: Session, space: TaskSpace, name: str, description: str | None, user_id: int
) -> TaskSpaceTemplate:
    name = name.strip()[:80]
    if not name:
        raise HTTPException(422, "tên mẫu trống")
    clash = session.scalar(
        select(TaskSpaceTemplate.id).where(func.lower(TaskSpaceTemplate.name) == name.lower())
    )
    if clash is not None or name.lower() in {t.name.lower() for t in TEMPLATES.values()}:
        raise HTTPException(409, f"đã có mẫu tên {name}")
    base = _slug(name)
    taken = set(TEMPLATES) | set(
        session.scalars(select(TaskSpaceTemplate.key).where(TaskSpaceTemplate.key.like(f"{base}%")))
    )
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    row = TaskSpaceTemplate(
        key=key,
        name=name,
        description=(description or "").strip()[:500] or None,
        payload=snapshot(session, space),
        builtin=False,
        created_by=user_id,
    )
    session.add(row)
    session.flush()
    return row


# -- applying ------------------------------------------------------------------------------------


def apply(
    session: Session,
    space: TaskSpace,
    key: str,
    *,
    types: list[str] | None = None,
    board_mode: str | None = None,
) -> dict[str, Any]:
    """Apply template `key` to an existing space. No task yet: everything (workflow replaced).
    With tasks: only what loses nothing (add priorities, types, fields; rules, DoD, automation,
    board mode); the workflow is kept. Returns what was applied and what was skipped."""
    _, raw = resolve(session, key)
    payload = _clean(raw)
    if types is not None:
        if not types or any(t not in TASK_TYPES for t in types):
            raise HTTPException(422, "loại task không hợp lệ")
        payload["types"] = list(types)
    if board_mode is not None:
        payload["board_mode"] = check_board_mode(board_mode)
    bp_id = space.business_project_id
    # The space row lock: a task created meanwhile would land on a status being replaced.
    session.get(TaskSpace, bp_id, with_for_update=True)
    used = int(
        session.scalar(
            select(func.count()).select_from(Task).where(Task.business_project_id == bp_id)
        )
        or 0
    )
    applied: list[str] = []
    skipped: list[str] = []
    if used == 0:
        _apply_full(session, space, payload, applied)
    else:
        _apply_safe(session, space, payload, used, applied, skipped)
    space.template_key = key
    session.flush()
    return {"mode": "full" if used == 0 else "partial", "applied": applied, "skipped": skipped}


def _statuses_by_name(session: Session, bp_id: int) -> dict[str, int]:
    from app.tasks import service as ts

    return {s.name.lower(): s.id for s in ts.statuses_of(session, bp_id)}


def _rules(
    session: Session,
    space: TaskSpace,
    payload: dict[str, Any],
    applied: list[str],
    skipped: list[str] | None,
    full: bool,
) -> None:
    from app.tasks import automation, quality
    from app.tasks import service as ts

    wanted = payload["rules"]
    ids = _statuses_by_name(session, space.business_project_id)
    current = dict(space.rules or {})
    rules: dict[str, Any] = {} if full else dict(current)
    if full:
        rules.update(ts.DEFAULT_FLAGS)
    for flag in ts.RULE_FLAGS:
        if flag in wanted:
            rules[flag] = bool(wanted[flag])
    for target in ("start", "review", "done"):
        name = wanted.get(target)
        if name is None:
            if full or target in wanted:
                rules[target] = None
            continue
        found = ids.get(str(name).lower())
        if found is not None:
            rules[target] = found
        elif skipped is not None:
            skipped.append(f"Luật git “{target}”: dự án không có trạng thái {name}, giữ như cũ")
        else:
            rules[target] = None
    if full and rules.get("done") is None:
        rules["done"] = next(
            (
                s.id
                for s in ts.statuses_of(session, space.business_project_id)
                if s.category == "done"
            ),
            None,
        )
    applied.append("Luật đồng bộ git")
    if isinstance(wanted.get("dod"), dict):
        rules["dod"] = quality.clean_dod(wanted["dod"])
        applied.append("Definition of Done")
    # Full: `rules` started empty, so no DoD / automation of the old setup survives.
    space.rules = rules
    if isinstance(wanted.get("automation"), dict):
        clean = {
            k: v
            for k, v in wanted["automation"].items()
            if k in automation.BY_KEY and isinstance(v, dict)
        }
        try:
            automation.set_config(space, clean)
            applied.append("Quy tắc tự động")
        except HTTPException:
            if skipped is not None:
                skipped.append("Quy tắc tự động: cấu hình trong mẫu không hợp lệ")


def _apply_full(
    session: Session, space: TaskSpace, payload: dict[str, Any], applied: list[str]
) -> None:
    from app.tasks import service as ts

    bp_id = space.business_project_id
    space.types = list(payload["types"])
    space.board_mode = payload["board_mode"]
    applied += ["Loại task", "Chế độ bảng"]
    # Workflow: same-named statuses keep their row (and id), the rest are replaced.
    old = _statuses_by_name(session, bp_id)
    wanted = [
        {
            "id": old.get(s["name"].lower()),
            "name": s["name"],
            "category": s["category"],
            "transitions": None,
            "wip_limit": s["wip_limit"],
            "age_warning_hours": s["age_warning_hours"],
        }
        for s in payload["statuses"]
    ]
    out = ts.set_statuses(session, bp_id, wanted, {})
    ids = {s.name.lower(): s.id for s in out}
    for status, item in zip(out, payload["statuses"], strict=True):
        allowed = item["transitions"]
        status.transitions = (
            None if allowed is None else [ids[n.lower()] for n in allowed if n.lower() in ids]
        )
    session.flush()
    applied.append("Trạng thái và luồng chuyển")
    if payload["priorities"]:
        current = {x.name.lower(): x.id for x in ts.priorities_of(session, bp_id)}
        ts.set_priorities(
            session,
            bp_id,
            [
                {
                    "id": current.get(p["name"].lower()),
                    "name": p["name"],
                    "level": p["level"],
                    "color": p["color"],
                    "is_default": p["default"],
                }
                for p in payload["priorities"][: ts.MAX_PRIORITIES]
            ],
            {},
        )
        applied.append("Mức ưu tiên")
    _rules(session, space, payload, applied, None, full=True)
    fields = list(session.scalars(select(TaskField).where(TaskField.business_project_id == bp_id)))
    by_name = {f.name.lower(): f for f in fields}
    kept: set[int] = set()
    for i, item in enumerate(payload["fields"]):
        row = by_name.get(item["name"].lower())
        if row is None:
            row = TaskField(business_project_id=bp_id, name=item["name"], kind=item["kind"])
            session.add(row)
        row.kind, row.options, row.required = item["kind"], item["options"], item["required"]
        row.archived, row.position = False, i
        session.flush()
        kept.add(row.id)
    for f in fields:
        if f.id not in kept:
            f.archived = True
    if payload["fields"]:
        applied.append("Trường tuỳ chỉnh")


def _apply_safe(
    session: Session,
    space: TaskSpace,
    payload: dict[str, Any],
    used: int,
    applied: list[str],
    skipped: list[str],
) -> None:
    from app.tasks import service as ts

    bp_id = space.business_project_id
    skipped.append(f"Trạng thái và luồng chuyển giữ nguyên: dự án đã có {used} task")
    missing_types = [t for t in payload["types"] if t not in space.types]
    if missing_types:
        space.types = [t for t in TASK_TYPES if t in space.types or t in missing_types]
        applied.append(f"Thêm loại task: {', '.join(missing_types)}")
    if space.board_mode != payload["board_mode"]:
        space.board_mode = payload["board_mode"]
        applied.append("Chế độ bảng")
    current = ts.priorities_of(session, bp_id)
    have = {x.name.lower() for x in current}
    new = [p for p in payload["priorities"] if p["name"].lower() not in have]
    if new:
        room = max(ts.MAX_PRIORITIES - len(current), 0)
        if len(new) > room:
            skipped.append(
                f"Mức ưu tiên {', '.join(p['name'] for p in new[room:])}: "
                f"đã đủ {ts.MAX_PRIORITIES} mức"
            )
            new = new[:room]
    if new:
        ts.set_priorities(
            session,
            bp_id,
            [
                {
                    "id": x.id,
                    "name": x.name,
                    "level": x.level,
                    "color": x.color,
                    "is_default": x.is_default,
                }
                for x in current
            ]
            + [
                {"name": p["name"], "level": p["level"], "color": p["color"], "is_default": False}
                for p in new
            ],
            {},
        )
        applied.append(f"Thêm mức ưu tiên: {', '.join(p['name'] for p in new)}")
    _rules(session, space, payload, applied, skipped, full=False)
    fields = list(session.scalars(select(TaskField).where(TaskField.business_project_id == bp_id)))
    by_name = {f.name.lower(): f for f in fields}
    added: list[str] = []
    position = max((f.position for f in fields), default=-1) + 1
    for item in payload["fields"]:
        row = by_name.get(item["name"].lower())
        if row is not None:
            if row.archived and row.kind == item["kind"]:
                row.archived = False
                added.append(item["name"])
            elif row.archived or row.kind != item["kind"]:
                skipped.append(f"Trường {item['name']}: dự án đã có trường cùng tên khác loại")
            continue
        session.add(
            TaskField(
                business_project_id=bp_id,
                name=item["name"],
                kind=item["kind"],
                options=item["options"],
                required=item["required"],
                position=position,
            )
        )
        position += 1
        added.append(item["name"])
    if added:
        applied.append(f"Thêm trường: {', '.join(added)}")
