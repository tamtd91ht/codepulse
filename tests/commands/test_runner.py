import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.commands import runner
from app.commands.jobs import command_file


def _streamer(events: list[dict[str, Any]], seen: dict[str, Any]) -> Any:
    def stream(
        cmd: Sequence[str],
        *,
        stdin: str,
        cwd: str,
        env: dict[str, str],
        timeout_s: float,
        on_line: Callable[[str], None],
        new_session: bool = False,
    ) -> tuple[int, str]:
        seen.update(
            cmd=list(cmd),
            stdin=stdin,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
            new_session=new_session,
        )
        for event in events:
            on_line(json.dumps(event) + "\n")
        return 0, ""

    return stream


def _run(
    tmp_path: Path, events: list[dict[str, Any]], **extra: Any
) -> tuple[Any, dict[str, Any], list[Any]]:
    seen: dict[str, Any] = {}
    logged: list[Any] = []
    outcome = runner.run(
        binary="claude",
        workdir=tmp_path / "work",
        out_dir=tmp_path / "out",
        command="report:weekly",
        arguments="backend 2026-W43",
        model="claude-opus-5-5",
        max_turns=50,
        max_budget_usd=5,
        timeout_s=600,
        auth_token="sk-ant-oat-abc",
        on_entries=logged.extend,
        cancelled=extra.pop("cancelled", lambda: False),
        streamer=extra.pop("streamer", None) or _streamer(events, seen),
    )
    return outcome, seen, logged


def test_the_command_is_the_prompt_and_claude_code_expands_it(tmp_path: Path) -> None:
    outcome, seen, _ = _run(tmp_path, [])
    assert seen["stdin"] == "/report:weekly backend 2026-W43"
    assert seen["cwd"] == str(tmp_path / "work")
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert json.loads(cmd[cmd.index("--settings") + 1]) == {"disableAllHooks": True}
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "50"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    # --add-dir takes several values: it must be last so it swallows nothing else.
    assert cmd[-2:] == ["--add-dir", str(tmp_path / "out")]
    assert seen["new_session"] is True  # the Bash tool's children die with the CLI
    assert seen["env"]["CODEPULSE_OUTPUT_DIR"] == str(tmp_path / "out")
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-abc"
    assert "DATABASE_URL" not in seen["env"]
    assert outcome.ok is False and "without a result" in outcome.error


def test_events_become_log_entries_and_an_outcome(tmp_path: Path) -> None:
    work = str(tmp_path / "work")
    events: list[dict[str, Any]] = [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-opus-5-5",
            "slash_commands": ["report:weekly", "compact"],
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading the log"},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "git log --oneline -5"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": f"{work}/docs/report.md", "content": "..."},
                    },
                ]
            },
        },
        {"type": "system", "subtype": "permission_denied", "tool_name": "WebFetch"},
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "is_error": True, "content": "denied"}]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 4,
            "total_cost_usd": 0.42,
            "result": "Done: report.md",
        },
    ]
    outcome, _, logged = _run(tmp_path, events)
    kinds = [(e["kind"], e.get("tool"), e.get("text")) for e in logged]
    assert kinds == [
        ("start", None, None),
        ("text", None, "Reading the log"),
        ("tool", "Bash", "git log --oneline -5"),
        ("tool", "Write", "docs/report.md"),
        ("denied", "WebFetch", None),
        ("tool_error", None, "denied"),
        ("end", None, None),
    ]
    assert outcome.ok and outcome.recognized is True
    assert outcome.turns == 4 and outcome.cost_usd == 0.42 and outcome.result == "Done: report.md"
    assert outcome.denied == ["WebFetch"]


def test_an_unknown_command_is_flagged(tmp_path: Path) -> None:
    events = [{"type": "system", "subtype": "init", "slash_commands": ["compact"]}]
    outcome, _, logged = _run(tmp_path, events)
    assert outcome.recognized is False
    assert any(e["kind"] == "warn" for e in logged)


def test_a_failed_result_carries_its_reason(tmp_path: Path) -> None:
    events = [{"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 50}]
    outcome, _, _ = _run(tmp_path, events)
    assert not outcome.ok and outcome.error.startswith("error_max_turns")


def test_secrets_in_the_text_are_redacted(tmp_path: Path) -> None:
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "token sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA found"}
                ]
            },
        }
    ]
    _, _, logged = _run(tmp_path, events)
    assert "AAAAAAAA" not in logged[0]["text"]


def test_cancel_stops_the_stream(tmp_path: Path) -> None:
    events = [{"type": "system", "subtype": "init"}]
    with pytest.raises(runner.Cancelled):
        _run(tmp_path, events, cancelled=lambda: True)


def test_a_timeout_is_an_outcome_not_an_exception(tmp_path: Path) -> None:
    def slow(cmd: Sequence[str], **_: Any) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(list(cmd), 600)

    outcome, _, _ = _run(tmp_path, [], streamer=slow)
    assert not outcome.ok and "quá thời gian" in outcome.error


def test_the_command_file_is_where_claude_code_looks(tmp_path: Path) -> None:
    assert command_file(tmp_path, "report:weekly") == (
        tmp_path / ".claude" / "commands" / "report" / "weekly.md"
    )
    assert command_file(tmp_path, "v1.2") == tmp_path / ".claude" / "commands" / "v1.2.md"
