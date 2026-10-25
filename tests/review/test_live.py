import json
import subprocess
from typing import Any

import pytest

from app.review.live import LiveTracker, describe
from app.review.progress import LIVE_MAX, MemoryProgress
from app.review.providers import ClaudeCodeProvider, LLMError, ReviewPrompt

PROMPT = ReviewPrompt(system="sys", user="diff", json_schema={"type": "object"})
WORKDIR = "/srv/codepulse-deep-x/src"  # only a string: nothing is written there


def assistant(*content: dict[str, Any]) -> dict[str, Any]:
    return {"type": "assistant", "message": {"content": list(content)}}


# As 2.1.310 streams them (trimmed): init, a Read, the file content, prose, the answer, result.
EVENTS: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "tools": ["Read"]},
    assistant(
        {"type": "tool_use", "name": "Read", "input": {"file_path": f"{WORKDIR}/app/auth.py"}}
    ),
    {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "SECRET_KEY = 'hunter2'"}]},
    },
    assistant(
        {
            "type": "tool_use",
            "name": "Grep",
            "input": {"pattern": "tenant_id", "path": f"{WORKDIR}/app", "glob": "*.py"},
        }
    ),
    assistant({"type": "text", "text": "The check in auth.py: password = 'hunter2'"}),
    assistant({"type": "tool_use", "name": "Read", "input": {"file_path": "/etc/passwd"}}),
    assistant({"type": "tool_use", "name": "StructuredOutput", "input": {"summary": "x"}}),
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 5,
        "structured_output": {"summary": "ok"},
        "total_cost_usd": 0.5,
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "modelUsage": {"claude-sonnet-5-5": {}},
    },
]


def test_tracker_keeps_actions_and_drops_contents() -> None:
    tracker = LiveTracker(WORKDIR, clock=lambda: 100.0)
    entries = [e for event in EVENTS for e in tracker.entries(event)]
    assert [(e["kind"], e.get("tool"), e.get("target")) for e in entries] == [
        ("start", None, None),
        ("tool", "Read", "app/auth.py"),
        ("tool", "Grep", "tenant_id"),
        ("thinking", None, None),
        ("tool", "Read", "passwd"),  # outside the checkout: the name only
        ("answer", None, None),
        ("end", None, None),
    ]
    grep = entries[2]
    assert (grep["path"], grep["glob"], grep["turn"]) == ("app", "*.py", 2)
    assert "hunter2" not in json.dumps(entries)  # neither tool results nor prose
    assert tracker.turns == 5 and tracker.reads == {"app/auth.py", "passwd"}
    assert entries[-1]["ok"] is True and entries[-1]["turns"] == 5
    assert (
        describe(entries[1]) == "Read app/auth.py" and describe(entries[-2]) == "đang trả kết quả"
    )


def test_windows_paths_are_made_relative() -> None:
    tracker = LiveTracker(r"C:\Temp\deep\src", clock=lambda: 0.0)
    read = {"type": "tool_use", "name": "Read", "input": {"file_path": r"C:\Temp\deep\src\a\b.py"}}
    (entry,) = tracker.entries(assistant(read))
    assert entry["target"] == "a/b.py"


def test_memory_progress_keeps_the_newest_entries() -> None:
    progress = MemoryProgress()
    progress.event(1, [{"kind": "tool", "turn": n} for n in range(LIVE_MAX + 5)])
    kept = progress.live(1)
    assert len(kept) == LIVE_MAX and kept[0]["turn"] == 5


class FakeStreamer:
    def __init__(self, lines: list[str], code: int = 0, stderr: str = "") -> None:
        self.lines, self.code, self.stderr = lines, code, stderr
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd: list[str], **kw: Any) -> tuple[int, str]:
        self.calls.append({"cmd": cmd, **kw})
        for line in self.lines:
            kw["on_line"](line)
        return self.code, self.stderr


def test_streamed_deep_review_hands_over_events_and_returns_the_result() -> None:
    streamer = FakeStreamer([json.dumps(e) + "\n" for e in EVENTS] + ["not json\n"])
    seen: list[dict[str, Any]] = []
    provider = ClaudeCodeProvider(streamer=streamer, sleep=lambda s: None)
    result = provider.review_in_repo(
        PROMPT, WORKDIR, tools=("Read",), max_turns=10, timeout_s=30.0, on_event=seen.append
    )
    cmd = streamer.calls[0]["cmd"]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd
    assert streamer.calls[0]["cwd"] == WORKDIR and streamer.calls[0]["stdin"] == "diff"
    assert len(seen) == len(EVENTS)
    assert result.output == {"summary": "ok"} and result.cost_usd == 0.5


def test_a_broken_event_handler_does_not_break_the_review() -> None:
    def boom(event: dict[str, Any]) -> None:
        raise RuntimeError("redis down")

    provider = ClaudeCodeProvider(streamer=FakeStreamer([json.dumps(EVENTS[-1])]))
    result = provider.review_in_repo(
        PROMPT, WORKDIR, tools=("Read",), max_turns=10, timeout_s=30.0, on_event=boom
    )
    assert result.output == {"summary": "ok"}


@pytest.mark.parametrize(
    ("streamer", "message"),
    [
        (FakeStreamer([], code=1, stderr="auth failed sk-ant-oat01-abcdef"), "without a result"),
        (
            FakeStreamer([json.dumps({"type": "result", "subtype": "error_max_turns"})]),
            "error_max_turns",
        ),
    ],
)
def test_streamed_failures_are_llm_errors(streamer: FakeStreamer, message: str) -> None:
    provider = ClaudeCodeProvider(streamer=streamer)
    with pytest.raises(LLMError, match=message) as excinfo:
        provider.review_in_repo(
            PROMPT, WORKDIR, tools=("Read",), max_turns=10, timeout_s=30.0, on_event=lambda e: None
        )
    assert "abcdef" not in str(excinfo.value)


def test_stream_timeout_becomes_an_llm_error() -> None:
    def slow(cmd: list[str], **kw: Any) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(cmd, 1)

    with pytest.raises(LLMError, match="TimeoutExpired"):
        ClaudeCodeProvider(streamer=slow).review_in_repo(
            PROMPT, WORKDIR, tools=("Read",), max_turns=10, timeout_s=1, on_event=lambda e: None
        )
