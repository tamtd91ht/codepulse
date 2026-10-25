"""The live view of a deep review: what Claude is doing right now (outside the spec, requested by
the user; see PROGRESS.md).

The CLI streams its events (stream-json); each one becomes at most a few short entries: the
tool it called and on what (a file path relative to the checkout, a search pattern), a turn
counter, the final answer. Never the text of a file, a tool result or the model's prose: those
may quote source code or secrets, and the live view is shown to people who did not write the
code. Patterns and paths are redacted and cut short, and the dashboard shows them as plain text.
"""

import time
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from app.review.prepare import redact

MAX_TARGET = 160
SEARCH_TOOLS = ("Grep", "Glob")
# The tool the CLI uses to hand back the --json-schema answer.
ANSWER_TOOL = "StructuredOutput"


def _relative(path: str, workdir: str) -> str:
    """A path inside the checkout, relative to it; anything else is shown by its name only."""
    for flavour in (PurePosixPath, PureWindowsPath):
        try:
            return str(flavour(path).relative_to(flavour(workdir)).as_posix())
        except ValueError:
            continue
    return PurePosixPath(path.replace("\\", "/")).name


def _short(text: str) -> str:
    text = redact(" ".join(str(text).split()))
    return text if len(text) <= MAX_TARGET else text[: MAX_TARGET - 1] + "…"


def _tool(name: str, data: dict[str, Any], workdir: str) -> dict[str, Any]:
    if name == ANSWER_TOOL:
        return {"kind": "answer"}
    entry: dict[str, Any] = {"kind": "tool", "tool": name[:40]}
    if name == "Read" and isinstance(data.get("file_path"), str):
        entry["target"] = _short(_relative(data["file_path"], workdir))
        if isinstance(data.get("offset"), int):
            entry["lines"] = data["offset"]
    elif name in SEARCH_TOOLS and isinstance(data.get("pattern"), str):
        entry["target"] = _short(data["pattern"])
        if isinstance(data.get("path"), str):
            entry["path"] = _short(_relative(data["path"], workdir))
        if isinstance(data.get("glob"), str):
            entry["glob"] = _short(data["glob"])
    return entry


class LiveTracker:
    """Turns stream-json events into entries; counts turns and the files the agent opened."""

    def __init__(self, workdir: str, clock: Any = time.time) -> None:
        self._workdir = workdir
        self._clock = clock
        self.turns = 0
        self.reads: set[str] = set()

    def entries(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        out: list[dict[str, Any]] = []
        if kind == "system" and event.get("subtype") == "init":
            out.append({"kind": "start", "model": str(event.get("model") or "")[:60]})
        elif kind == "assistant":
            self.turns += 1
            content = (event.get("message") or {}).get("content") or []
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    raw = item.get("input")
                    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
                    entry = _tool(str(item.get("name") or "?"), data, self._workdir)
                    if entry.get("tool") == "Read" and entry.get("target"):
                        self.reads.add(entry["target"])
                    out.append(entry)
                elif item.get("type") in ("text", "thinking") and not out:
                    out.append({"kind": "thinking"})  # what it wrote is not shown
        elif kind == "result":
            out.append(
                {
                    "kind": "end",
                    "ok": not event.get("is_error") and event.get("subtype") == "success",
                    "turns": event.get("num_turns"),
                }
            )
        stamp = round(self._clock(), 1)
        return [{**entry, "turn": self.turns, "at": stamp} for entry in out]


def describe(entry: dict[str, Any]) -> str:
    """One line for the stage detail of the running review."""
    kind = str(entry["kind"])
    if kind == "tool":
        return f"{entry['tool']} {entry.get('target', '')}".strip()
    labels = {
        "start": "bắt đầu",
        "thinking": "đang suy nghĩ",
        "answer": "đang trả kết quả",
        "end": "xong",
    }
    return labels.get(kind, kind)
