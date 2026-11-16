"""Task keys in git text (docs/specs/31 §5): commit messages, MR titles / descriptions, branch
names. Pure functions; which keys exist is decided by the caller (app/tasks/git.py)."""

import re
from dataclasses import dataclass, field

# CRM-12. Keys are upper case in messages; a branch is matched case-insensitively.
KEY = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]{1,9})-(\d{1,7})(?![0-9])")
KEY_ANY_CASE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,9})-(\d{1,7})(?![0-9])")
_FIX_TRAILER = re.compile(
    r"^\s*(?:fixes|closes|resolves|fix|close|resolve)\s*:\s*(.+)$", re.I | re.M
)
_FIX_PHRASE = re.compile(
    r"\b(?:fixes|fixed|closes|closed|resolves|resolved)\s+((?:[A-Z][A-Z0-9]{1,9}-\d+[\s,]*)+)", re.I
)
_FIX_TYPE = re.compile(r"^\s*(?:fix|hotfix|bugfix)(?:\([^)]*\))?!?:", re.I)
_REVERT = re.compile(r"^\s*revert\b", re.I)
# Smart commit commands after a key or anywhere: #time 1h30m, #comment text, #done.
_TIME = re.compile(r"#time\s+((?:\d+(?:\.\d+)?\s*[wdhm]\s*)+)", re.I)
_COMMENT = re.compile(r"#comment\s+(.+?)(?=\s#[a-z]|\Z)", re.I | re.S)
_STATUS = re.compile(r"(?<![\w&])#([a-z][a-z0-9-]{1,40})\b", re.I)
_UNITS = {"w": 5 * 8 * 60, "d": 8 * 60, "h": 60, "m": 1}


def keys_in(text: str | None) -> list[str]:
    """Upper-case keys in `text`, in order, without repeats."""
    seen: dict[str, None] = {}
    for m in KEY.finditer(text or ""):
        seen[f"{m.group(1)}-{int(m.group(2))}"] = None
    return list(seen)


def branch_keys(branch: str | None) -> list[str]:
    """Keys in a branch name (feature/crm-12-login), any case, upper-cased."""
    seen: dict[str, None] = {}
    for m in KEY_ANY_CASE.finditer(branch or ""):
        seen[f"{m.group(1).upper()}-{int(m.group(2))}"] = None
    return list(seen)


def fixed_keys(text: str | None) -> set[str]:
    """Keys a message says it fixes: trailers (Fixes: K1, K2) and phrases (fixes K1)."""
    out: set[str] = set()
    for m in _FIX_TRAILER.finditer(text or ""):
        out.update(keys_in(m.group(1)))
    for m in _FIX_PHRASE.finditer(text or ""):
        out.update(keys_in(m.group(1).upper()))
    return out


def commit_refs(message: str | None) -> dict[str, str]:
    """key -> "fixes" | "refs" for a commit message (docs/specs/31 §5)."""
    text = message or ""
    keys = keys_in(text)
    if not keys:
        return {}
    title = text.splitlines()[0] if text else ""
    if _REVERT.match(title):
        return dict.fromkeys(keys, "refs")
    fixes = fixed_keys(text)
    if _FIX_TYPE.match(title):
        fixes.update(keys_in(title))
    return {k: "fixes" if k in fixes else "refs" for k in keys}


def mr_refs(title: str | None, description: str | None, branch: str | None) -> dict[str, str]:
    """key -> relation for an MR: title or source branch → fixes; "Closes KEY" in the
    description → fixes; only mentioned in the description → refs."""
    out: dict[str, str] = {}
    for key in keys_in(description):
        out[key] = "refs"
    for key in fixed_keys(description):
        out[key] = "fixes"
    for key in [*keys_in(title), *branch_keys(branch)]:
        out[key] = "fixes"
    return out


def parse_minutes(spec: str) -> int:
    """ "1h30m" / "2d" / "1.5h" → minutes (a day is 8 h, a week 5 days)."""
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([wdhm])", spec, re.I):
        total += float(amount) * _UNITS[unit.lower()]
    return round(total)


@dataclass
class SmartCommands:
    minutes: int = 0
    comment: str | None = None
    statuses: list[str] = field(default_factory=list)  # slugs: done, in-progress, ...


def smart_commands(message: str | None) -> SmartCommands:
    """#time, #comment and #<status> of a commit message (docs/specs/31 §5)."""
    text = message or ""
    out = SmartCommands()
    for m in _TIME.finditer(text):
        out.minutes += parse_minutes(m.group(1))
    comment = _COMMENT.search(text)
    if comment:
        out.comment = comment.group(1).strip()[:2000] or None
    for m in _STATUS.finditer(text):
        word = m.group(1).lower()
        if word not in ("time", "comment") and not word.isdigit():
            out.statuses.append(word)
    return out


def slug(name: str) -> str:
    """A status name as a smart-commit word: "In Progress" → "in-progress", "Đang làm" →
    "dang-lam" (accents dropped)."""
    import unicodedata

    plain = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


# Smart-commit words that mean a rule's status whatever the dự án calls it (docs/specs/31
# §19.1): English words keep working after the default names became Vietnamese.
STATUS_ALIASES: dict[str, str] = {
    "start": "start",
    "in-progress": "start",
    "dang-lam": "start",
    "doing": "start",
    "review": "review",
    "in-review": "review",
    "dang-review": "review",
    "done": "done",
    "xong": "done",
}


def branch_name(key: str, title: str, task_type: str) -> str:
    """feature/CRM-12-dang-nhap-bang-otp (bug → bugfix/), ASCII, ≤ 60 characters."""
    import unicodedata

    plain = unicodedata.normalize("NFD", title.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    words = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    prefix = "bugfix" if task_type == "bug" else "feature"
    return f"{prefix}/{key}-{words}"[:60].rstrip("-")
