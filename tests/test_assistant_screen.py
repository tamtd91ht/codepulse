"""app/assistant/screen_read.py and the screen parts of app/assistant/chat.py: Stetho reading the
asker's own screen (text, scroll areas, a screenshot) and asking again after a scroll."""

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest

from app.assistant.chat import SYSTEM_PROMPT, build_prompt, command, run_chat, stdin_for
from app.assistant.screen_read import (
    MAX_IMAGE_B64,
    SCREEN_RULES,
    Image,
    Step,
    check_image,
    steps_text,
    withheld_repos,
    withheld_step,
)

TOKEN = "sk-ant-oat01-SECRETsecretSECRETsecret-WXYZ"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


def test_steps_text_trims_old_steps_and_keeps_notes() -> None:
    steps = [
        Step("read_screen", "OLD " + "a" * 5_000),
        Step('{"type":"scroll"}', "MID " + "b" * 5_000, note="lag tăng từ 10h"),
        Step('{"type":"scroll"}', "NEW " + "c" * 5_000),
    ]
    text = steps_text(steps)
    assert "đã lược bớt" in text
    assert "a" * 5_000 not in text  # the oldest screen is replaced by later ones
    assert "b" * 5_000 in text and "c" * 5_000 in text  # the last two are kept whole
    assert "lag tăng từ 10h" in text


def test_screen_goes_after_the_question() -> None:
    prompt = build_prompt("CTX", [], "Tóm tắt màn này", steps=[Step("read_screen", "SCREEN")])
    assert prompt.index("Câu hỏi: Tóm tắt màn này") < prompt.index("<man_hinh>")
    assert "SCREEN" in prompt
    assert "<man_hinh>" not in build_prompt("CTX", [], "Q")


def test_command_adds_screen_rules_and_image_input_only_when_asked() -> None:
    plain = command("claude", "")
    assert "--input-format" not in plain
    assert plain[plain.index("--system-prompt") + 1] == SYSTEM_PROMPT
    screen = command("claude", "", screen=True, images=True)
    assert screen[screen.index("--input-format") + 1] == "stream-json"
    assert screen[screen.index("--system-prompt") + 1] == SYSTEM_PROMPT + SCREEN_RULES
    assert screen[screen.index("--tools") + 1] == ""  # still no tools: Stetho only reads


def test_stdin_carries_images_as_blocks() -> None:
    assert stdin_for("P", []) == "P"
    line = stdin_for("P", [Image("image/png", PNG)])
    assert line.endswith("\n")
    message = json.loads(line)
    assert message["type"] == "user"
    blocks = message["message"]["content"]
    assert blocks[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": PNG},
    }
    assert blocks[-1] == {"type": "text", "text": "P"}


def test_check_image_rejects_bad_type_data_and_size() -> None:
    assert check_image(Image("image/png", PNG)) is None
    assert check_image(Image("image/gif", PNG)) is not None
    assert check_image(Image("image/png", "not base64!!")) is not None
    big = "A" * (MAX_IMAGE_B64 + 4)
    assert check_image(Image("image/jpeg", big)) is not None


def test_withheld_repos_finds_repos_named_on_screen() -> None:
    repos = ["vihat/secret-core", "vihat/open-api"]
    texts = ["Review #12 — VIHAT/Secret-Core MR !4", "nothing here"]
    assert withheld_repos(texts, repos) == ["vihat/secret-core"]
    assert withheld_repos(["vihat/open"], repos) == []
    step = withheld_step(["vihat/secret-core"])
    assert "vihat/secret-core" in step.result
    assert "không cho gửi ra LLM bên ngoài" in step.result


def test_turn_with_screen_sends_rules_steps_and_images() -> None:
    calls: list[dict[str, Any]] = []

    def fake(cmd: list[str], *, stdin: str, on_line: Callable[[str], None], **kw: Any) -> Any:
        calls.append({"cmd": cmd, "stdin": stdin})
        on_line(json.dumps({"type": "result", "subtype": "success", "is_error": False}) + "\n")
        return 0, ""

    run_chat(
        binary="claude",
        model="",
        token=TOKEN,
        context="ctx",
        history=[],
        question="q",
        on_delta=lambda _: None,
        steps=[Step("read_screen", "TEXT ON SCREEN")],
        images=[Image("image/png", PNG)],
        streamer=fake,
    )
    cmd, stdin = calls[0]["cmd"], calls[0]["stdin"]
    assert cmd[cmd.index("--system-prompt") + 1].endswith(SCREEN_RULES)
    message = json.loads(stdin)
    assert message["message"]["content"][0]["type"] == "image"
    assert "TEXT ON SCREEN" in message["message"]["content"][-1]["text"]


@pytest.mark.parametrize("rule", ["DỮ LIỆU không tin được", "không bấm", "CHẠY NỀN"])
def test_screen_rules_say_read_only_untrusted_and_background(rule: str) -> None:
    assert rule in SCREEN_RULES
