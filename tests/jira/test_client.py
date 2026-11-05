"""Jira client: retry, 429, paging, timeout, no token in logs (docs/specs/27 §2, §14)."""

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.jira.client import JiraClient, JiraError

TOKEN = "pat-" + "fake-" + "0123456789"  # fake, built by concatenation


def make(
    handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float] | None = None
) -> JiraClient:
    record = sleeps if sleeps is not None else []
    return JiraClient(
        "https://jira.example.vn/",
        TOKEN,
        transport=httpx.MockTransport(handler),
        sleep=record.append,
    )


def test_sends_the_bearer_token_to_rest_api_2() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"version": "9.12.1", "serverTitle": "Jira"})

    assert make(handler).server_info()["version"] == "9.12.1"
    assert seen[0].url == "https://jira.example.vn/rest/api/2/serverInfo"
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


def test_retries_5xx_and_network_errors_then_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"key": "ABC", "name": "Alpha"}])

    sleeps: list[float] = []
    with caplog.at_level(logging.DEBUG):
        assert make(handler, sleeps).projects() == [{"key": "ABC", "name": "Alpha"}]
    assert calls["n"] == 3
    assert len([s for s in sleeps if s >= 1]) == 2  # two backoffs (the rest: pacing)
    assert TOKEN not in caplog.text
    assert all(TOKEN not in json.dumps(r.__dict__, default=str) for r in caplog.records)


def test_gives_up_after_three_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"errorMessages": ["oops"]})

    with pytest.raises(JiraError) as err:
        make(handler).fields()
    assert calls["n"] == 4  # one try and three retries
    assert err.value.status_code == 500 and "oops" in err.value.message
    assert TOKEN not in str(err.value)


def test_timeout_is_a_network_error_retried_then_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(JiraError) as err:
        make(handler).server_info()
    assert err.value.status_code is None and "ReadTimeout" in err.value.message


def test_429_honours_retry_after_capped_at_30_seconds() -> None:
    answers = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "120"}),
        httpx.Response(200, json={"version": "9"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    sleeps: list[float] = []
    make(handler, sleeps).server_info()
    assert 7.0 in sleeps and 30.0 in sleeps
    assert max(sleeps) == 30.0


def test_4xx_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    with pytest.raises(JiraError) as err:
        make(handler).server_info()
    assert calls["n"] == 1 and err.value.status_code == 401


def test_calls_are_paced_at_least_100_ms_apart() -> None:
    times = iter([0.0, 0.0, 0.03, 0.03])
    sleeps: list[float] = []
    client = JiraClient(
        "https://jira.example.vn",
        TOKEN,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        sleep=sleeps.append,
    )
    client._limiter._clock = lambda: next(times)
    client.server_info()
    client.server_info()
    assert sleeps and abs(sleeps[0] - 0.1) < 1e-9


def test_search_pages_through_every_issue() -> None:
    bodies: list[dict[str, object]] = []
    issues = [{"id": str(i), "key": f"ABC-{i}"} for i in range(1, 251)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        start, size = int(body["startAt"]), int(body["maxResults"])
        return httpx.Response(
            200,
            json={"startAt": start, "total": len(issues), "issues": issues[start : start + size]},
        )

    got = list(make(handler).search_all('project = "ABC"', ["summary"]))
    assert [i["key"] for i in got] == [i["key"] for i in issues]
    assert [b["startAt"] for b in bodies] == [0, 100, 200]
    assert all(b["maxResults"] == 100 and b["fields"] == ["summary"] for b in bodies)
    assert bodies[0]["jql"] == 'project = "ABC"'


def test_search_stops_on_an_empty_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"startAt": 0, "total": 500, "issues": []})

    assert list(make(handler).search_all("x", [])) == []
