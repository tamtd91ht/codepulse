import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.notifications.deliver import body_for, deliver, sign
from app.notifications.payload import events_for, public_error, sample, text

URL = "https://hooks.example.com/codepulse"
PAYLOAD = sample("https://dash.example.com/")


def test_events_for_status_and_severity() -> None:
    assert events_for("failed", {}) == ["review.failed"]
    assert events_for("skipped", {}) == []
    assert events_for("done", {"critical": 0, "high": 0, "medium": 2}) == ["review.done"]
    assert events_for("done", {"critical": 0, "high": 1}) == ["review.done", "review.severe"]


def test_bodies_per_format_and_the_text_rendering() -> None:
    plain = body_for("json", "review.done", PAYLOAD)
    assert plain["event"] == "review.done" and plain["review"]["label"] == "demo/app !1"
    slack = body_for("slack", "review.severe", PAYLOAD)
    assert (
        slack["mrkdwn"] is True
        and "Review có critical/high: FIX REQUIRED · 88/100" in slack["text"]
    )
    teams = body_for("teams", "review.done", PAYLOAD)
    assert teams["@type"] == "MessageCard" and "demo/app !1" in teams["title"]
    rendered = text("review.done", PAYLOAD)
    assert "1 high" in rendered and "42 LOC" in rendered
    assert "[high] security: Ví dụ finding (app/example.py:1)" in rendered
    assert rendered.endswith("<https://dash.example.com/reviews/0|Xem review>")
    failed = text(
        "review.failed",
        {**PAYLOAD, "review": {**PAYLOAD["review"], "status": "failed", "error": "llm: boom"}},
    )
    assert failed.splitlines()[0].endswith("Review lỗi*") and "Lỗi: llm: boom" in failed


@respx.mock
def test_delivery_is_signed_and_retried_on_5xx() -> None:
    route = respx.post(URL)
    route.side_effect = [httpx.Response(503), httpx.Response(200, text="ok")]
    waits: list[float] = []
    result = deliver(
        URL,
        "json",
        "s3cret-s3cret-s3cret",
        "review.done",
        PAYLOAD,
        sleep=waits.append,
        now=lambda: 1761382800.7,
    )
    assert result.ok and result.status_code == 200 and result.attempts == 2
    assert waits == [2.0]
    request = route.calls[-1].request
    assert request.headers["X-CodePulse-Event"] == "review.done"
    assert request.headers["X-CodePulse-Delivery"] == result.delivery_id
    assert request.headers["X-CodePulse-Timestamp"] == "1761382800"
    signed = b"1761382800." + request.content
    expected = hmac.new(b"s3cret-s3cret-s3cret", signed, hashlib.sha256).hexdigest()
    assert request.headers["X-CodePulse-Signature"] == f"sha256={expected}"
    assert sign("s3cret-s3cret-s3cret", "1761382800", request.content) == f"sha256={expected}"
    assert json.loads(request.content)["event"] == "review.done"


@respx.mock
def test_a_4xx_is_final_and_transport_errors_use_every_attempt() -> None:
    gone = respx.post(URL).mock(return_value=httpx.Response(404))
    result = deliver(URL, "json", None, "review.done", PAYLOAD, sleep=lambda s: None)
    assert (result.ok, result.status_code, result.error, result.attempts) == (
        False,
        404,
        "HTTP 404",
        1,
    )
    assert "X-CodePulse-Signature" not in gone.calls[0].request.headers

    down = respx.post("https://down.example.com/").mock(side_effect=httpx.ConnectError("refused"))
    result = deliver(
        "https://down.example.com/", "slack", None, "ping", PAYLOAD, sleep=lambda s: None
    )
    assert not result.ok and result.status_code is None
    assert result.error == "ConnectError" and result.attempts == 3 and down.call_count == 3
    once = deliver("https://down.example.com/", "json", None, "ping", PAYLOAD, attempts=1)
    assert once.attempts == 1 and down.call_count == 4


def test_text_escapes_markup_and_hides_error_details() -> None:
    assert public_error("gitlab: GET https://gitlab.internal/api/v4/x: 404") == "gitlab"
    assert public_error("monthly LLM budget reached") == "monthly LLM budget reached"
    assert public_error(None) is None and public_error("") is None
    hostile = {
        **PAYLOAD,
        "business_project": {"id": 1, "name": "<!channel>"},
        "review": {
            **PAYLOAD["review"],
            "top_findings": [
                {
                    "severity": "high",
                    "category": "security",
                    "title": "<https://evil.example|click> & co",
                    "file_path": "a.py",
                    "line_start": 1,
                }
            ],
        },
    }
    rendered = text("review.done", hostile)
    assert "<!channel>" not in rendered and "&lt;!channel&gt;" in rendered
    assert "&lt;https://evil.example|click&gt; &amp; co" in rendered
    assert rendered.endswith("<https://dash.example.com/reviews/0|Xem review>")


@respx.mock
def test_a_caller_supplied_client_is_used_and_kept_open() -> None:
    respx.post(URL).mock(return_value=httpx.Response(204))
    with httpx.Client() as http:
        assert deliver(URL, "teams", None, "ping", PAYLOAD, http=http).ok
        assert not http.is_closed
    with pytest.raises(RuntimeError):
        http.post(URL)  # closed by the with-block, not by deliver()
