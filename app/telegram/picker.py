"""Choosing what to review with buttons instead of GitLab tabs (docs/specs/15 §Chọn bằng nút).

`/review` with nothing to point at (no id, no person) lists the recent commits of a branch or
the open merge requests, numbered, with an inline keyboard: tap numbers to tick them, "Review
đã chọn" to queue. The list lives in Redis for PICKER_TTL_S, belongs to the person who asked,
and is rendered again on every tap. Pure functions here: rendering, keyboards, callback data,
and the two stores (Redis for production, memory for tests).
"""

import html
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from redis import Redis


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


PICKER_TTL_S = 30 * 60
PAGE_SIZE = 8
MAX_ITEMS = 40
Kind = Literal["commit", "mr"]
STATE_ICON = {"open": "⬜", "done": "✅", "running": "⏳", "failed": "↻", "blocked": "🚫"}


@dataclass
class Item:
    """One commit or MR the person may tick."""

    kind: Kind
    project_id: int
    ref: str  # iid (mr) or sha (commit)
    sha: str
    label: str  # short, for the queued-request record ("omicrm/api !12")
    title: str  # what the person reads in the list
    author: str  # name shown in the list
    author_user_id: int | None
    when: str = ""  # "2 giờ trước"
    state: str = "open"  # open | done | running | failed | blocked (not allowed for this person)
    result: str = ""  # "APPROVE 82" when done

    @property
    def selectable(self) -> bool:
        return self.state in ("open", "failed")

    def tickable(self, again: bool) -> bool:
        """`again`: a finished item may be ticked too (it becomes a new review round)."""
        return self.selectable or (again and self.state == "done")


@dataclass
class PickerState:
    token: str
    owner_from_id: int  # Telegram user who may tap
    user_id: int  # CodePulse user behind the owner
    chat_id: int
    message_id: int | None
    kind: Kind
    scope_label: str  # "omicrm/api · main · 7 ngày"
    items: list[Item]
    project_id: int = 0  # the repo the list came from (for the people filter)
    person_id: int | None = None  # list filtered to this CodePulse user, None = everyone
    again: bool = False  # finished items may be ticked (each becomes a new review round)
    may_force: bool = False  # lead/admin: a member cannot re-roll the score of their own work
    selected: list[int] = field(default_factory=list)
    page: int = 0
    raw: str = ""  # the command, for the audit log
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "PickerState":
        data = json.loads(text)
        data["items"] = [Item(**i) for i in data.get("items", [])]
        return cls(**data)

    @property
    def pages(self) -> int:
        return max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)

    def page_items(self) -> list[tuple[int, Item]]:
        start = self.page * PAGE_SIZE
        return [(i, it) for i, it in enumerate(self.items)][start : start + PAGE_SIZE]


class PickerStore(Protocol):
    def save(self, state: PickerState) -> None: ...
    def load(self, token: str) -> PickerState | None: ...
    def delete(self, token: str) -> None: ...


class RedisPickerStore:
    def __init__(self, redis: Redis, ttl_s: int = PICKER_TTL_S) -> None:
        self._redis, self._ttl = redis, ttl_s

    def save(self, state: PickerState) -> None:
        self._redis.set(f"tg:pick:{state.token}", state.to_json(), ex=self._ttl)

    def load(self, token: str) -> PickerState | None:
        raw = self._redis.get(f"tg:pick:{token}")
        if not raw:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return PickerState.from_json(text)

    def delete(self, token: str) -> None:
        self._redis.delete(f"tg:pick:{token}")


class MemoryPickerStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: int = PICKER_TTL_S):
        self._items: dict[str, tuple[str, float]] = {}
        self._clock, self._ttl = clock, ttl_s

    def save(self, state: PickerState) -> None:
        self._items[state.token] = (state.to_json(), self._clock() + self._ttl)

    def load(self, token: str) -> PickerState | None:
        entry = self._items.get(token)
        if entry is None or entry[1] < self._clock():
            self._items.pop(token, None)
            return None
        return PickerState.from_json(entry[0])

    def delete(self, token: str) -> None:
        self._items.pop(token, None)


def new_token() -> str:
    return secrets.token_urlsafe(6)  # 8 chars: callback_data must stay under 64 bytes


# -- callback data --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    kind: str  # pick | menu | repo
    token: str = ""
    op: str = ""  # pick: t (toggle) | all | go | pg | x ; menu: commit | mr | x ; repo: commit|mr
    arg: str = ""


def encode(kind: str, token: str = "", op: str = "", arg: str | int = "") -> str:
    parts = [kind, token, op, str(arg)]
    data = "|".join(parts).rstrip("|")
    if len(data.encode()) > 64:  # Telegram's limit; never happens with our short tokens
        raise ValueError("callback data too long")
    return data


KINDS = {"pk": "pick", "mn": "menu", "rp": "repo", "us": "person"}


def decode(data: str) -> Action | None:
    """pk|token|op|arg · mn||commit · rp||kind|project · us||kind|project:user (0 = everyone)"""
    parts = (data or "").split("|")
    if not parts or parts[0] not in KINDS:
        return None
    parts += [""] * (4 - len(parts))
    return Action(KINDS[parts[0]], parts[1], parts[2], parts[3])


# -- rendering ------------------------------------------------------------------------------------


def render_text(state: PickerState) -> str:
    what = "Commit gần đây" if state.kind == "commit" else "MR đang mở"
    lines = [f"<b>{what}</b> · {esc(state.scope_label)} · {len(state.items)} mục"]
    if state.pages > 1:
        lines[0] += f" · trang {state.page + 1}/{state.pages}"
    for i, it in state.page_items():
        icon = STATE_ICON.get(it.state, "⬜")
        mark = "☑" if i in state.selected else icon
        tail = ""
        if it.state == "done":
            tail = f" — đã review{': ' + esc(it.result) if it.result else ''}"
        elif it.state == "running":
            tail = " — đang review"
        elif it.state == "failed":
            tail = " — lần trước lỗi, review lại được"
        elif it.state == "blocked":
            tail = " — ngoài quyền review của bạn"
        who = f" · {esc(it.author)}" if it.author else ""
        when = f" · {esc(it.when)}" if it.when else ""
        lines.append(f"{mark} <b>{i + 1}.</b> {esc(it.title)}{who}{when}{tail}")
    selectable = [i for i, it in enumerate(state.items) if it.tickable(state.again)]
    if state.again:
        lines.append("Đang cho chọn cả mục đã review: mỗi mục sẽ được review lại thành lần mới.")
    if not selectable:
        lines.append(
            "Không có mục nào review được trong danh sách này"
            + (
                " (bấm ↻ để review lại mục đã xong)."
                if any(it.state == "done" for it in state.items)
                else "."
            )
        )
    else:
        lines.append(
            f"Bấm số để chọn (đã chọn {len(state.selected)}), rồi bấm <b>Review đã chọn</b>."
        )
    return "\n".join(lines)


def keyboard(state: PickerState) -> dict[str, Any]:
    """Inline keyboard: a toggle per selectable item on this page, then the controls."""
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for i, it in state.page_items():
        if not it.tickable(state.again):
            continue
        mark = "☑" if i in state.selected else "☐"
        row.append({"text": f"{mark} {i + 1}", "callback_data": encode("pk", state.token, "t", i)})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    controls = [
        {"text": "☑ Chọn hết", "callback_data": encode("pk", state.token, "all")},
        {
            "text": f"▶ Review đã chọn ({len(state.selected)})",
            "callback_data": encode("pk", state.token, "go"),
        },
    ]
    rows.append(controls)
    extras: list[dict[str, str]] = []
    if state.project_id:
        extras.append({"text": "👤 Người", "callback_data": encode("pk", state.token, "who")})
    if state.may_force and any(it.state == "done" for it in state.items):
        extras.append(
            {
                "text": "↩ Chỉ mục mới" if state.again else "↻ Cho chọn mục đã review",
                "callback_data": encode("pk", state.token, "again"),
            }
        )
    if extras:
        rows.append(extras)
    nav: list[dict[str, str]] = []
    if state.page > 0:
        nav.append(
            {"text": "◀ Trước", "callback_data": encode("pk", state.token, "pg", state.page - 1)}
        )
    if state.page < state.pages - 1:
        nav.append(
            {"text": "Sau ▶", "callback_data": encode("pk", state.token, "pg", state.page + 1)}
        )
    nav.append({"text": "✖ Huỷ", "callback_data": encode("pk", state.token, "x")})
    rows.append(nav)
    return {"inline_keyboard": rows}


def menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "📝 Commit gần đây", "callback_data": encode("mn", op="commit")},
                {"text": "🔀 MR đang mở", "callback_data": encode("mn", op="mr")},
            ],
            [{"text": "✖ Huỷ", "callback_data": encode("mn", op="x")}],
        ]
    }


def people_keyboard(
    kind: Kind, project_id: int, people: list[tuple[int, str]], more: int = 0
) -> dict[str, Any]:
    """Everyone first, then the people the caller may review (two per row), then cancel."""
    rows: list[list[dict[str, str]]] = [
        [{"text": "👥 Mọi người", "callback_data": encode("us", op=kind, arg=f"{project_id}:0")}]
    ]
    row: list[dict[str, str]] = []
    for user_id, name in people:
        row.append(
            {
                "text": name[:28] or f"#{user_id}",
                "callback_data": encode("us", op=kind, arg=f"{project_id}:{user_id}"),
            }
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if more:
        rows.append(
            [{"text": f"… còn {more} người: gõ user=tên", "callback_data": encode("mn", op="noop")}]
        )
    rows.append([{"text": "✖ Huỷ", "callback_data": encode("mn", op="x")}])
    return {"inline_keyboard": rows}


REPO_PAGE_SIZE = 8


def repo_pages(repos: list[tuple[int, str]]) -> int:
    return max(1, (len(repos) + REPO_PAGE_SIZE - 1) // REPO_PAGE_SIZE)


def repo_keyboard(kind: Kind, repos: list[tuple[int, str]], page: int = 0) -> dict[str, Any]:
    """One button per repo (short name), REPO_PAGE_SIZE per page, then paging and cancel.
    Every repo of the scope is reachable: the pages carry the rest."""
    page = min(max(page, 0), repo_pages(repos) - 1)
    leaves = [path.split("/")[-1] for _, path in repos]
    rows: list[list[dict[str, str]]] = []
    for project_id, path in repos[page * REPO_PAGE_SIZE : (page + 1) * REPO_PAGE_SIZE]:
        leaf = path.split("/")[-1]
        # Two repos with the same name in different groups: show the group too.
        label = "/".join(path.split("/")[-2:]) if leaves.count(leaf) > 1 else leaf
        rows.append([{"text": label[:40], "callback_data": encode("rp", op=kind, arg=project_id)}])
    nav: list[dict[str, str]] = []
    if page > 0:
        nav.append({"text": "◀ Trước", "callback_data": encode("mn", op=kind, arg=page - 1)})
    if page < repo_pages(repos) - 1:
        nav.append({"text": "Sau ▶", "callback_data": encode("mn", op=kind, arg=page + 1)})
    nav.append({"text": "✖ Huỷ", "callback_data": encode("mn", op="x")})
    rows.append(nav)
    return {"inline_keyboard": rows}
