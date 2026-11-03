"""docs/specs/20 §6.4 (9.11): the AI draft of a lead's monthly note."""

import json
from typing import Any

from app.evidence.draft import build_prompt, postprocess, references, run_draft

SNAP: dict[str, Any] = {
    "period": "2026-10",
    "merged": {
        "count": 2,
        "items": [
            {
                "id": 11,
                "iid": 101,
                "title": "Payment fix",
                "path_with_namespace": "demo/p",
                "size_bucket": "S",
                "reviewed": True,
                "time_to_merge": 30.0,
            },
            {
                "id": 12,
                "iid": 102,
                "title": "Hotfix",
                "path_with_namespace": "demo/p",
                "size_bucket": "XS",
                "reviewed": False,
                "time_to_merge": 2.0,
            },
        ],
    },
    "notable": [{"id": 11, "why": "Lớn nhất trong tháng"}],
    "reviews_given": {
        "count": 1,
        "authors": 1,
        "items": [{"id": 21, "iid": 201, "title": "Cache", "path_with_namespace": "demo/q"}],
    },
    "quality": {
        "confirmed_severe": 1,
        "fixed": 1,
        "disputes_raised": 0,
        "disputes_accepted": 0,
        "items": [
            {
                "id": 31,
                "title": "SQL injection",
                "severity": "critical",
                "category": "security",
                "status": "fixed",
            }
        ],
    },
    "waiting": [{"id": 11, "pickup": 30.0, "repo_p75": 8.0}],
    "direct_commits": {"count": 1, "items": [{"sha": "abcdef1234567890", "title": "typo"}]},
}
ITEMS = [{"id": 41, "kind": "mentoring", "title": "Kèm bạn mới", "note": None}]


def test_references_cover_everything_the_draft_may_cite() -> None:
    known = references(SNAP, ITEMS)
    assert known == {
        "mr:11": "MR !101 demo/p",
        "mr:12": "MR !102 demo/p",
        "mr:21": "MR !201 demo/q",
        "finding:31": "finding: SQL injection",
        "commit:abcdef12": "commit abcdef12",
        "item:41": "bằng chứng: Kèm bạn mới",
    }


def test_the_prompt_carries_data_with_refs_and_no_code() -> None:
    prompt = build_prompt(SNAP, ITEMS, "Làm thêm phần thiết kế")
    body = json.loads(prompt.split("<du_lieu>\n")[1].split("\n</du_lieu>")[0])
    assert body["mr_da_merge"]["ds"][1]["merged_without_review"] is True
    assert body["mr_cho_review_lau"][0]["ref"] == "mr:11"
    assert body["tu_nhan_xet"] == "Làm thêm phần thiết kế"
    assert "code_excerpt" not in prompt and "diff" not in prompt


def test_postprocess_keeps_only_cited_bullets() -> None:
    raw = "\n".join(
        [
            "### Đã làm",
            "- Sửa lỗi thanh toán (ref: mr:11).",
            "- Một việc bịa ra không có nguồn.",
            "- Trích dẫn sai (ref: mr:999)",
            "Đoạn văn tự do không phải gạch đầu dòng.",
            "### Điểm tốt",
            "- Sửa lỗi bảo mật kịp thời (ref: finding:31) (ref: mr:11)",
            "### Cần chú ý",
            "- Bịa (ref: nothing:1)",
        ]
    )
    text, dropped = postprocess(raw, references(SNAP, ITEMS))
    assert text.splitlines() == [
        "### Đã làm",
        "- Sửa lỗi thanh toán [MR !101 demo/p]",
        "### Điểm tốt",
        "- Sửa lỗi bảo mật kịp thời [finding: SQL injection; MR !101 demo/p]",
    ]  # the empty "Cần chú ý" heading goes too
    assert dropped == 4


def fake_streamer(lines: list[dict[str, Any]], code: int = 0) -> Any:
    seen: dict[str, Any] = {}

    def run(
        cmd: list[str], *, stdin: str, cwd: str, env: Any, timeout_s: float, on_line: Any
    ) -> tuple[int, str]:
        seen.update(cmd=cmd, stdin=stdin)
        for line in lines:
            on_line(json.dumps(line))
        return code, ""

    run.seen = seen  # type: ignore[attr-defined]
    return run


def result(text: str, ok: bool = True) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success" if ok else "error",
        "is_error": not ok,
        "result": text,
        "total_cost_usd": 0.012,
        "modelUsage": {"claude-sonnet": {}},
    }


def test_run_draft_end_to_end_with_a_fake_cli() -> None:
    answer = "### Đã làm\n- Sửa lỗi thanh toán (ref: mr:11)\n- bịa"
    streamer = fake_streamer(
        [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}},
            result(answer),
        ]
    )
    out = run_draft(
        binary="claude",
        model="sonnet",
        token="tok",
        prompt="P",
        known=references(SNAP, ITEMS),
        streamer=streamer,
    )
    assert out.ok and out.cost_usd == 0.012 and out.model == "claude-sonnet"
    assert out.text == "### Đã làm\n- Sửa lỗi thanh toán [MR !101 demo/p]" and out.dropped == 1
    cmd = streamer.seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd


def test_run_draft_failures_never_raise() -> None:
    out = run_draft(
        binary="claude",
        model="",
        token="tok",
        prompt="P",
        known={},
        streamer=fake_streamer([result("Failed to authenticate", ok=False)], code=1),
    )
    assert not out.ok and "authenticate" in (out.error or "")
    uncited = run_draft(
        binary="claude",
        model="",
        token="tok",
        prompt="P",
        known={},
        streamer=fake_streamer([result("- không dẫn nguồn")]),
    )
    assert not uncited.ok and uncited.error and "dẫn" in uncited.error
