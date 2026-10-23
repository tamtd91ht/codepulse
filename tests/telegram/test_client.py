import json

import httpx
import pytest
import respx

from app.telegram.client import TelegramClient, TelegramError, split_message

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"  # fake token for tests
BASE = f"https://api.telegram.org/bot{TOKEN}"


def client(**kw: object) -> TelegramClient:
    return TelegramClient(TOKEN, sleep=lambda s: None, **kw)  # type: ignore[arg-type]


@respx.mock
def test_get_me_and_send_message() -> None:
    respx.post(f"{BASE}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "CodePulseBot"}})
    )
    sent = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})
    )
    with client() as c:
        assert c.get_me()["username"] == "CodePulseBot"
        c.send_message(42, "<b>hi</b>", reply_to_message_id=7)
    body = json.loads(sent.calls[0].request.content)
    assert body["chat_id"] == 42 and body["parse_mode"] == "HTML"
    assert body["reply_to_message_id"] == 7 and body["text"] == "<b>hi</b>"


@respx.mock
def test_retries_on_429_with_retry_after_then_succeeds() -> None:
    route = respx.post(f"{BASE}/getUpdates")
    route.side_effect = [
        httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 1},
            },
        ),
        httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]}),
    ]
    with client() as c:
        assert c.get_updates(offset=None) == [{"update_id": 1}]
    assert route.call_count == 2


@respx.mock
def test_errors_never_leak_the_token() -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )
    with client() as c, pytest.raises(TelegramError) as info:
        c.send_message(1, "x")
    assert TOKEN not in str(info.value)
    assert "chat not found" in str(info.value)
    assert info.value.status_code == 400


@respx.mock
def test_transport_errors_are_retried_then_raised_without_url() -> None:
    respx.post(f"{BASE}/getMe").mock(side_effect=httpx.ConnectError("boom"))
    with client(max_retries=1) as c, pytest.raises(TelegramError) as info:
        c.get_me()
    assert "ConnectError" in str(info.value) and TOKEN not in str(info.value)


@respx.mock
def test_a_long_retry_after_is_not_waited_for_when_capped() -> None:
    route = respx.post(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 60},
            },
        )
    )
    with client(max_retry_after=5.0) as c, pytest.raises(TelegramError) as info:
        c.get_updates(offset=None)
    assert route.call_count == 1 and info.value.status_code == 429


def test_token_never_reaches_the_json_logs(capsys: pytest.CaptureFixture[str]) -> None:
    import logging

    from app.logging import configure_logging

    configure_logging()
    logging.getLogger("httpx").info(
        'HTTP Request: POST https://api.telegram.org/bot%s/getUpdates "HTTP/1.1 200 OK"', TOKEN
    )
    logging.getLogger("app.telegram").warning(
        "failed: https://api.telegram.org/bot%s/sendMessage", TOKEN
    )
    logging.getLogger("app.telegram").warning(f"inline https://api.telegram.org/bot{TOKEN}/x")
    out = capsys.readouterr().err
    assert TOKEN not in out
    assert "bot***" in out  # the warnings were written, redacted; httpx INFO was dropped
    assert "HTTP Request" not in out


def test_split_message_respects_the_limit() -> None:
    lines = [f"line {i} " + "x" * 90 for i in range(60)]
    parts = split_message("\n".join(lines), limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts).split("\n") == lines
    assert split_message("x" * 2500, limit=1000) == ["x" * 1000, "x" * 1000, "x" * 500]
