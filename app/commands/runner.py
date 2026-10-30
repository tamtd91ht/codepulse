"""`claude -p "/<command> <arguments>"` in the run's clone, streamed into a live log.

Claude Code expands the command itself (it is the prompt, sent on stdin), so the run behaves as
on a laptop: `$ARGUMENTS`, the frontmatter's `allowed-tools` and `model`, `!` lines, the repo's
CLAUDE.md, rules and skills (`--setting-sources project`). What differs, on purpose:

- permission mode `acceptEdits`: files inside the clone and the output folder may be written;
  anything else (Bash, web) only when the command's `allowed-tools` or the repo's
  `.claude/settings.json` allows it, as print mode answers "no" to every prompt;
- hooks are off and no MCP server starts (they reach outside the worker), and the repo's
  settings keep only `permissions` and `model` (app/commands/workspace.sanitize_settings);
- turns, spend and time are capped (Admin > Chạy command);
- the environment is the review CLI's minimal one plus CODEPULSE_OUTPUT_DIR.
"""

import json
import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from app.review.prepare import redact
from app.review.providers.claude_code import cli_env, scrub_secrets, stream_lines

log = logging.getLogger(__name__)

OUTPUT_NOTE = (
    "You are running non-interactively from the CodePulse dashboard; nobody can answer "
    "questions, so make reasonable choices and finish the task. Save every file meant for the "
    "user (reports, exports, generated documents) into {out} (also in the environment as "
    "CODEPULSE_OUTPUT_DIR); it is what the user downloads. Do not commit or push anything."
)
MAX_TEXT = 2_000
MAX_TARGET = 300
MAX_RESULT = 20_000

Streamer = Callable[..., tuple[int, str]]
Entry = dict[str, Any]


class Cancelled(Exception):
    """An admin pressed "Huỷ"; raised from inside the stream so the CLI is killed."""


def prompt(command: str, arguments: str) -> str:
    return f"/{command} {arguments}".rstrip()


def argv(
    binary: str, out_dir: Path, *, model: str, max_turns: int, max_budget_usd: float
) -> list[str]:
    cmd = [
        binary,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--setting-sources",
        "project",
        "--permission-mode",
        "acceptEdits",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--settings",
        json.dumps({"disableAllHooks": True}),
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
        "--append-system-prompt",
        OUTPUT_NOTE.format(out=out_dir),
    ]
    if model:
        cmd += ["--model", model]
    # Variadic: last, so it never swallows another argument.
    return [*cmd, "--add-dir", str(out_dir)]


def _relative(path: str, workdir: str) -> str:
    for flavour in (PurePosixPath, PureWindowsPath):
        try:
            return str(flavour(path).relative_to(flavour(workdir)).as_posix())
        except ValueError:
            continue
    return path


def _short(text: str, limit: int = MAX_TARGET) -> str:
    text = redact(" ".join(str(text).split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _target(name: str, data: dict[str, Any], workdir: str) -> str:
    for key in ("file_path", "notebook_path", "path"):
        if isinstance(data.get(key), str):
            return _short(_relative(data[key], workdir))
    for key in ("command", "pattern", "url", "query", "description", "prompt", "skill"):
        if isinstance(data.get(key), str):
            return _short(data[key])
    return ""


@dataclass
class Outcome:
    ok: bool = False
    result: str = ""
    error: str = ""
    cost_usd: float = 0.0
    turns: int | None = None
    model: str = ""
    recognized: bool | None = None  # the command was in the CLI's list of slash commands
    denied: list[str] = field(default_factory=list)  # tools print mode refused


class Tracker:
    """stream-json events -> log entries for the dashboard (the run's admin sees the model's
    text and the tools it calls, redacted and cut short)."""

    def __init__(self, workdir: str, command: str, clock: Callable[[], float] = time.time):
        self.workdir = workdir
        self.command = command
        self.clock = clock
        self.outcome = Outcome()

    def entries(self, event: dict[str, Any]) -> list[Entry]:
        kind, subtype = event.get("type"), event.get("subtype")
        out: list[Entry] = []
        if kind == "system" and subtype == "init":
            self.outcome.model = str(event.get("model") or "")[:60]
            commands = event.get("slash_commands")
            if isinstance(commands, list):
                self.outcome.recognized = self.command in commands
            out.append({"kind": "start", "model": self.outcome.model})
            if self.outcome.recognized is False:
                out.append({"kind": "warn", "text": f"Claude Code không thấy /{self.command}"})
        elif kind == "system" and subtype == "permission_denied":
            tool = str(event.get("tool_name") or event.get("tool") or "?")[:40]
            self.outcome.denied.append(tool)
            out.append({"kind": "denied", "tool": tool})
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    raw = item.get("input")
                    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
                    name = str(item.get("name") or "?")[:40]
                    out.append(
                        {"kind": "tool", "tool": name, "text": _target(name, data, self.workdir)}
                    )
                elif item.get("type") == "text" and str(item.get("text") or "").strip():
                    out.append({"kind": "text", "text": _short(item["text"], MAX_TEXT)})
        elif kind == "user":
            content = (event.get("message") or {}).get("content") or []
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("is_error"):
                    out.append({"kind": "tool_error", "text": _short(str(item.get("content")))})
        elif kind == "result":
            self.outcome.ok = not event.get("is_error") and subtype == "success"
            self.outcome.turns = event.get("num_turns")
            self.outcome.cost_usd = float(event.get("total_cost_usd") or 0.0)
            self.outcome.result = redact(str(event.get("result") or ""))[:MAX_RESULT]
            if not self.outcome.ok:
                self.outcome.error = _short(f"{subtype}: {event.get('result') or ''}", 500)
            out.append(
                {
                    "kind": "end",
                    "ok": self.outcome.ok,
                    "turns": self.outcome.turns,
                    "cost_usd": self.outcome.cost_usd,
                }
            )
        stamp = round(self.clock(), 1)
        return [{**entry, "at": stamp} for entry in out]


def run(
    *,
    binary: str,
    workdir: Path,
    out_dir: Path,
    command: str,
    arguments: str,
    model: str,
    max_turns: int,
    max_budget_usd: float,
    timeout_s: float,
    auth_token: str,
    on_entries: Callable[[list[Entry]], None],
    cancelled: Callable[[], bool],
    streamer: Streamer = stream_lines,
) -> Outcome:
    """Run the command; never raises for the CLI's own failures (they are in the outcome).
    Raises Cancelled when `cancelled()` turns true while it streams."""
    tracker = Tracker(str(workdir), command)
    cmd: Sequence[str] = argv(
        binary, out_dir, model=model, max_turns=max_turns, max_budget_usd=max_budget_usd
    )
    env = {**cli_env(auth_token), "CODEPULSE_OUTPUT_DIR": str(out_dir)}
    last_check = 0.0

    def on_line(line: str) -> None:
        nonlocal last_check  # an empty line is a tick from the sandbox dispatcher
        now = time.monotonic()
        if now - last_check > 2:
            last_check = now
            if cancelled():
                raise Cancelled
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        entries = tracker.entries(event)
        if entries:
            try:
                on_entries(entries)
            except Exception:  # the live log is a bonus: never break the run over it
                log.warning("command log entries not stored", exc_info=True)

    try:
        code, stderr = streamer(
            cmd,
            stdin=prompt(command, arguments),
            cwd=str(workdir),
            env=env,
            timeout_s=timeout_s,
            on_line=on_line,
            new_session=True,
        )
    except subprocess.TimeoutExpired:
        tracker.outcome.ok = False
        tracker.outcome.error = f"quá thời gian ({int(timeout_s // 60)} phút)"
        return tracker.outcome
    except OSError as exc:
        tracker.outcome.ok = False
        tracker.outcome.error = f"không chạy được claude: {exc}"[:300]
        return tracker.outcome
    outcome = tracker.outcome
    if outcome.turns is None and not outcome.error:  # no result event at all
        tail = scrub_secrets(stderr, auth_token)[-400:]
        outcome.error = f"claude exited {code} without a result: {tail}"
    return outcome
