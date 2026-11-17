"""A filter typed as a sentence (docs/specs/31 §19.8): "bug cao chưa giao sprint này" → the
params `GET /tasks` takes. Deterministic: Vietnamese / English words and the dự án's own status,
priority, sprint, version and people names; no LLM. Words it does not know become `q`.

One value per field (the List keeps one value per filter); a later word for the same field wins."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Sprint, TaskSpace, TaskVersion, User
from app.tasks import service as ts

MAX_TEXT = 300
TYPE_LABELS = {
    "epic": "Epic",
    "story": "Story",
    "task": "Task",
    "bug": "Bug",
    "subtask": "Sub-task",
}
CATEGORY_LABELS = {"todo": "Cần làm", "doing": "Đang làm", "done": "Xong"}
LEVEL_WORDS: dict[str, str] = {
    "khan": "highest",
    "khan cap": "highest",
    "gap": "highest",
    "urgent": "highest",
    "critical": "highest",
    "blocker": "highest",
    "highest": "highest",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "lowest": "lowest",
}
# Words that carry no filter on their own ("ưu tiên cao" = "cao").
FILLER = frozenset(
    [
        "cac",
        "nhung",
        "tat",
        "ca",
        "moi",
        "cua",
        "co",
        "la",
        "va",
        "voi",
        "o",
        "trong",
        "uu",
        "tien",
        "loai",
        "trang",
        "thai",
        "viec",
        "task",
        "tasks",
        "show",
        "me",
        "all",
        "the",
        "with",
        "in",
        "of",
        "and",
        "is",
        "are",
        "list",
        "danh",
        "sach",
        "hien",
        "loc",
        "nguoi",
        "lam",
        "giao",
        "cho",
    ]
)


def fold(text: str) -> str:
    """Lower case, accents dropped ("Đang làm" → "dang lam")."""
    plain = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()


def _words(text: str) -> tuple[str, ...]:
    return tuple(w for w in re.split(r"[^a-z0-9]+", fold(text)) if w)


@dataclass
class Vocab:
    """What the sentence can name in one dự án."""

    statuses: list[tuple[int, str]] = field(default_factory=list)  # id, name
    priorities: list[tuple[str, str, str]] = field(default_factory=list)  # key, name, level
    types: list[str] = field(default_factory=lambda: list(TYPE_LABELS))
    people: list[tuple[int, str, str]] = field(default_factory=list)  # id, full name, email
    sprints: list[tuple[int, str]] = field(default_factory=list)  # id, name (not closed first)
    versions: list[tuple[int, str]] = field(default_factory=list)  # id, name
    today: date = field(default_factory=lambda: datetime.now(UTC).date())


@dataclass
class Chip:
    field: str
    value: str
    label: str


Phrase = tuple[str, ...]


def _phrases(v: Vocab) -> dict[Phrase, Chip]:
    """Every phrase it knows, folded, → its chip. Later entries win, so the dự án's own names
    (added last) beat the built-in words: a status "Đang làm" is that status, not the group."""
    out: dict[Phrase, Chip] = {}

    def add(words: str | Phrase, chip: Chip) -> None:
        key = _words(words) if isinstance(words, str) else words
        if key:
            out[key] = chip

    # Types.
    type_words = {
        "epic": ["epic", "epics"],
        "story": ["story", "stories", "tinh nang", "user story"],
        "bug": ["bug", "bugs", "loi", "defect"],
        "subtask": ["subtask", "sub task", "subtasks", "viec con"],
        "task": ["loai task"],
    }
    for kind, words in type_words.items():
        if kind in v.types:
            for w in words:
                add(w, Chip("type", kind, f"Loại: {TYPE_LABELS[kind]}"))
    # Status groups.
    groups = {
        "todo": ["can lam", "chua lam", "todo", "to do", "chua bat dau"],
        "doing": ["dang lam", "doing", "in progress", "dang xu ly", "dang thuc hien"],
        "done": ["xong", "da xong", "done", "hoan thanh", "da hoan thanh", "closed", "da dong"],
    }
    for cat, words in groups.items():
        for w in words:
            add(w, Chip("category", cat, f"Nhóm: {CATEGORY_LABELS[cat]}"))
    # People.
    for w in ("cua toi", "toi lam", "giao cho toi", "my", "mine", "assigned to me", "viec toi"):
        add(w, Chip("assignee", "me", "Người làm: tôi"))
    for w in ("chua giao", "chua co nguoi", "chua co nguoi lam", "unassigned", "khong ai lam"):
        add(w, Chip("assignee", "none", "Người làm: chưa giao"))
    # Sprint.
    for w in (
        "sprint nay",
        "sprint hien tai",
        "sprint dang chay",
        "current sprint",
        "this sprint",
        "active sprint",
    ):
        add(w, Chip("sprint", "active", "Sprint: đang chạy"))
    for w in ("backlog", "chua vao sprint", "ngoai sprint", "khong sprint"):
        add(w, Chip("sprint", "backlog", "Sprint: backlog"))
    # Due.
    for w in ("qua han", "tre han", "overdue"):
        add(w, Chip("due", "overdue", "Hạn: quá hạn"))
    for w in (
        "sap den han",
        "sap het han",
        "den han",
        "han tuan nay",
        "den han tuan nay",
        "due this week",
        "due soon",
        "han 7 ngay",
    ):
        add(w, Chip("due", "week", "Hạn: trong 7 ngày"))
    # Updated since.
    monday = v.today - timedelta(days=v.today.weekday())
    for w in ("tuan nay", "this week", "cap nhat tuan nay", "updated this week"):
        add(w, Chip("updated_from", monday.isoformat(), f"Cập nhật từ {monday:%d/%m}"))
    for w in ("hom nay", "today", "cap nhat hom nay"):
        add(w, Chip("updated_from", v.today.isoformat(), "Cập nhật hôm nay"))
    week_ago = v.today - timedelta(days=7)
    for w in ("7 ngay qua", "tuan qua", "last week", "7 ngay"):
        add(w, Chip("updated_from", week_ago.isoformat(), f"Cập nhật từ {week_ago:%d/%m}"))
    # Priorities: level words first, then the dự án's names and keys.
    for word, level in LEVEL_WORDS.items():
        match = next((p for p in v.priorities if p[2] == level), None)
        if match:
            add(word, Chip("priority", match[0], f"Ưu tiên: {match[1]}"))
    for key, name, _level in v.priorities:
        chip = Chip("priority", key, f"Ưu tiên: {name}")
        add(name, chip)
        add(key, chip)
        add(f"uu tien {fold(name)}", chip)
    # Statuses, sprints, versions by name.
    for sid, name in v.statuses:
        add(name, Chip("status", str(sid), f"Trạng thái: {name}"))
    for spid, name in reversed(v.sprints):  # reversed: the first listed wins a shared name
        add(name, Chip("sprint", str(spid), f"Sprint: {name}"))
    for vid, name in reversed(v.versions):
        chip = Chip("version", str(vid), f"Phiên bản: {name}")
        add(name, chip)
        add(f"phien ban {fold(name)}", chip)
        add(f"version {fold(name)}", chip)
        add(f"ban {fold(name)}", chip)
    return out


def _person(v: Vocab, word: str) -> tuple[int, str] | None:
    """`@an` → the one person whose email name is it, else the one whose name has that word.
    Only a sure match (Vietnamese names share words); otherwise None and the word stays text."""
    w = fold(word)
    by_mail = [p for p in v.people if fold(p[2].split("@")[0]) == w]
    by_name = [p for p in v.people if w in _words(p[1])]
    one = by_mail if by_mail else by_name
    if len(one) == 1:
        return one[0][0], one[0][1] or one[0][2]
    return None


def parse(text: str, v: Vocab) -> tuple[list[Chip], dict[str, str]]:
    """The chips (one per field, in the order typed) and the query they make."""
    tokens = [t for t in text[:MAX_TEXT].split() if t]
    phrases = _phrases(v)
    longest = max((len(k) for k in phrases), default=1)
    chips: dict[str, Chip] = {}
    rest: list[str] = []

    def put(chip: Chip) -> None:
        chips.pop(chip.field, None)  # a later word wins; keep the order typed
        chips[chip.field] = chip

    i = 0
    while i < len(tokens):
        raw = tokens[i].strip(",.;:!?()\"'")
        # @person.
        if raw.startswith("@") and len(raw) > 1:
            who = _person(v, raw[1:])
            if who is not None:
                put(Chip("assignee", str(who[0]), f"Người làm: {who[1]}"))
                i += 1
                continue
        # nhãn X / label X / #X.
        # ("nhận" folds to "nhan" too: only the word as written with its accent, or unaccented.)
        if raw.lower() in ("nhãn", "nhan", "label", "tag") and i + 1 < len(tokens):
            name = tokens[i + 1].strip(",.;:!?()\"'").lstrip("#")
            if name:
                put(Chip("label", name, f"Nhãn: {name}"))
                i += 2
                continue
        if raw.startswith("#") and len(raw) > 1:
            # "#bug" is the type, as in the Board's quick create; any other "#x" a label.
            typed = phrases.get(_words(raw[1:]))
            put(
                typed
                if typed and typed.field == "type"
                else Chip("label", raw[1:], f"Nhãn: {raw[1:]}")
            )
            i += 1
            continue
        # The longest known phrase starting here.
        matched = False
        for n in range(min(longest, len(tokens) - i), 0, -1):
            key = tuple(w for t in tokens[i : i + n] for w in _words(t))
            if key and key in phrases:
                put(phrases[key])
                i += n
                matched = True
                break
        if matched:
            continue
        if raw and not (set(_words(raw)) <= FILLER):
            rest.append(raw)
        i += 1
    if rest:
        q = " ".join(rest)
        put(Chip("q", q, f"Tìm: “{q}”"))
    ordered = list(chips.values())
    return ordered, {c.field: c.value for c in ordered}


def vocab(session: Session, space: TaskSpace, today: date | None = None) -> Vocab:
    bp = space.business_project_id
    sprints = list(
        session.scalars(
            select(Sprint)
            .where(Sprint.business_project_id == bp)
            .order_by((Sprint.state == "closed").asc(), Sprint.id.desc())
            .limit(200)
        )
    )
    versions = list(
        session.scalars(
            select(TaskVersion)
            .where(TaskVersion.business_project_id == bp)
            .order_by(TaskVersion.released_at.is_not(None).asc(), TaskVersion.id.desc())
            .limit(200)
        )
    )
    people: list[User] = ts.people(session, bp)
    return Vocab(
        statuses=[(s.id, s.name) for s in ts.statuses_of(session, bp)],
        priorities=[(p.key, p.name, p.level) for p in ts.priorities_of(session, bp)],
        types=list(space.types or TYPE_LABELS),
        people=[(u.id, u.full_name or "", u.primary_email or "") for u in people],
        sprints=[(s.id, s.name) for s in sprints],
        versions=[(x.id, x.name) for x in versions],
        today=today or datetime.now(UTC).date(),
    )


def condition(chip: Chip) -> dict[str, Any]:
    """The chip as a condition of the search tree (docs/specs/31 §21.4): `{field, op, value}`,
    so the List shows it as an editable chip "Trường · toán tử · giá trị"."""
    if chip.field == "q":
        return {"field": "text", "op": "is", "value": chip.value}
    if chip.field == "due":
        return {"field": "due", "op": "is", "value": chip.value}
    if chip.field == "updated_from":
        # "từ ngày D" = after the day before D.
        day = date.fromisoformat(chip.value) - timedelta(days=1)
        return {"field": "updated", "op": "after", "value": day.isoformat()}
    return {"field": chip.field, "op": "is", "value": [chip.value]}


def chips_out(chips: list[Chip]) -> list[dict[str, Any]]:
    """Each chip with its GET /tasks param (`field`, `value`) and, since §21.4, its condition
    (`cond`, `op`) for the chip bar."""
    out = []
    for c in chips:
        cond = condition(c)
        out.append(
            {"field": c.field, "value": c.value, "label": c.label, "op": cond["op"], "cond": cond}
        )
    return out
