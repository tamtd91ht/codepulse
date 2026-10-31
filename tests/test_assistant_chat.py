"""app/assistant/chat.py: prompt, stream parsing and one turn against a fake CLI."""

import json
import subprocess
from collections.abc import Callable
from typing import Any

from app.assistant.chat import (
    MAX_HISTORY_CHARS,
    Turn,
    build_prompt,
    history_text,
    parse_stream_line,
    run_chat,
)

TOKEN = "sk-ant-oat01-SECRETsecretSECRETsecret-WXYZ"


def lines(*events: dict[str, Any]) -> list[str]:
    return [json.dumps(e) + "\n" for e in events]


def delta(text: str) -> dict[str, Any]:
    return {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    }


RESULT = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01}


def streamer(
    out: list[str], code: int = 0, raise_: Exception | None = None
) -> Callable[..., tuple[int, str]]:
    def run(cmd: list[str], *, on_line: Callable[[str], None], **kw: Any) -> tuple[int, str]:
        for line in out:
            on_line(line)
        if raise_ is not None:
            raise raise_
        return code, "stderr text"

    return run


def turn(out: list[str], **kw: Any) -> tuple[Any, list[str]]:
    got: list[str] = []
    result = run_chat(
        binary="claude",
        model="",
        token=TOKEN,
        context="ctx",
        history=[],
        question="q",
        on_delta=got.append,
        streamer=kw.pop("streamer", streamer(out)),
        **kw,
    )
    return result, got


def test_history_keeps_the_newest_turns_within_the_cap() -> None:
    history = [Turn("user", "a" * 7_000) for _ in range(10)]
    text = history_text(history)
    assert len(text) <= MAX_HISTORY_CHARS
    assert text.count("Người dùng:") == 5


def test_prompt_wraps_context_history_and_question() -> None:
    prompt = build_prompt("CTX", [Turn("assistant", "A")], "Q?")
    assert prompt.startswith("<ngu_canh>\nCTX\n</ngu_canh>")
    assert "<hoi_thoai_truoc>\nTrợ lý: A\n</hoi_thoai_truoc>" in prompt
    assert prompt.endswith("Câu hỏi: Q?")
    assert "hoi_thoai_truoc" not in build_prompt("C", [], "Q")


def test_parse_ignores_noise() -> None:
    assert parse_stream_line("not json") is None
    assert parse_stream_line('{"type": "stream_event", "event": "x"}') is None
    assert parse_stream_line(json.dumps({"type": "user"})) is None
    event = parse_stream_line(json.dumps(delta("hi")))
    assert event is not None and event.text == "hi"


def test_turn_streams_and_scrubs_the_token() -> None:
    result, got = turn(lines(delta("key "), delta(TOKEN), RESULT))
    assert result.ok
    assert got == ["key ", "***"]
    assert result.text == "key ***"
    assert result.cost_usd == 0.01


def test_turn_without_partial_messages_uses_the_message() -> None:
    message = {"type": "assistant", "message": {"content": [{"type": "text", "text": "whole"}]}}
    result, got = turn(lines(message, RESULT))
    assert got == ["whole"]
    assert result.ok


def test_failed_result_is_an_error_with_its_cost() -> None:
    failed = {**RESULT, "subtype": "error_max_turns", "is_error": True, "result": "too long"}
    result, _ = turn(lines(failed), streamer=streamer(lines(failed), code=1))
    assert not result.ok
    assert result.error == "too long"
    assert result.cost_usd == 0.01


def test_timeout_and_missing_cli_are_errors() -> None:
    timeout = subprocess.TimeoutExpired(["claude"], 1)
    result, _ = turn([], streamer=streamer([], raise_=timeout))
    assert result.error is not None and "hết" in result.error
    result, _ = turn([], streamer=streamer([], raise_=FileNotFoundError()))
    assert result.error is not None and "không chạy được" in result.error


def test_cancel_stops_on_the_next_line() -> None:
    result, got = turn(lines(delta("a"), delta("b"), RESULT), cancelled=lambda: True)
    assert result.cancelled
    assert not result.ok
    assert got == []


def test_a_turn_killed_after_it_started_is_still_priced() -> None:
    init = {"type": "system", "subtype": "init", "model": "claude-x"}
    seen: list[str] = []

    def cancelled() -> bool:
        return len(seen) >= 2  # init and one delta went through

    def run(cmd: list[str], *, on_line: Callable[[str], None], **kw: Any) -> tuple[int, str]:
        for line in lines(init, delta("a" * 300), delta("b"), RESULT):
            on_line(line)
            seen.append(line)
        return 0, ""

    result, got = turn([], streamer=run, cancelled=cancelled)
    assert result.cancelled
    assert got == ["a" * 300]
    assert result.cost_estimated
    assert result.cost_usd is not None and result.cost_usd > 0


def test_an_invalid_token_says_who_fixes_it_and_where() -> None:
    failed = {
        **RESULT,
        "subtype": "success",
        "is_error": True,
        "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
    }
    result, _ = turn(lines(failed), streamer=streamer(lines(failed), code=1))
    assert result.error is not None
    assert result.error.startswith("Token Claude của CodePulse không còn hợp lệ")
    assert "Đăng nhập Claude" in result.error
    assert "401" in result.error
