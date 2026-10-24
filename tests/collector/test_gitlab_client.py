import httpx
import pytest
import respx

from app.collector.gitlab.client import GitLabClient, GitLabError
from app.ratelimit import RateLimiter

BASE = "https://gitlab.example.com"
API = f"{BASE}/api/v4"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps: list[float]) -> GitLabClient:
    return GitLabClient(
        BASE, "glpat-secret", max_retries=3, max_per_second=1000, sleep=sleeps.append
    )


@respx.mock
def test_sends_private_token(client: GitLabClient) -> None:
    route = respx.get(f"{API}/projects/7").respond(json={"id": 7})
    assert client.project(7) == {"id": 7}
    assert route.calls.last.request.headers["PRIVATE-TOKEN"] == "glpat-secret"


@respx.mock
def test_paginates_with_x_next_page(client: GitLabClient) -> None:
    route = respx.get(f"{API}/projects/7/pipelines")
    route.side_effect = [
        httpx.Response(200, json=[{"id": 1}, {"id": 2}], headers={"X-Next-Page": "2"}),
        httpx.Response(200, json=[{"id": 3}], headers={"X-Next-Page": ""}),
    ]
    assert [p["id"] for p in client.pipelines(7, None)] == [1, 2, 3]
    assert route.calls[1].request.url.params["page"] == "2"
    assert route.calls[1].request.url.params["per_page"] == "100"


@respx.mock
def test_paginates_with_keyset_link(client: GitLabClient) -> None:
    next_url = f"{API}/projects/7/repository/commits?cursor=abc&per_page=100"
    respx.get(f"{API}/projects/7/repository/commits", params={"cursor": "abc"}).respond(
        json=[{"id": "b"}]
    )
    respx.get(f"{API}/projects/7/repository/commits").respond(
        json=[{"id": "a"}], headers={"Link": f'<{next_url}>; rel="next"'}
    )
    assert [c["id"] for c in client.commits(7, None)] == ["a", "b"]


@respx.mock
def test_retries_429_honouring_retry_after(client: GitLabClient, sleeps: list[float]) -> None:
    route = respx.get(f"{API}/projects/7")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"id": 7}),
    ]
    assert client.project(7) == {"id": 7}
    assert 7.0 in sleeps


@respx.mock
def test_retries_5xx_with_exponential_backoff_then_fails(
    client: GitLabClient, sleeps: list[float]
) -> None:
    route = respx.get(f"{API}/projects/7").respond(502)
    with pytest.raises(GitLabError) as excinfo:
        client.project(7)
    assert route.call_count == 4  # first try + 3 retries
    assert excinfo.value.status_code == 502
    backoffs = [s for s in sleeps if s >= 1]
    assert backoffs == sorted(backoffs) and len(backoffs) == 3


@respx.mock
def test_client_errors_are_not_retried_and_hide_token(client: GitLabClient) -> None:
    route = respx.get(f"{API}/projects/7").respond(401)
    with pytest.raises(GitLabError) as excinfo:
        client.project(7)
    assert route.call_count == 1
    assert "glpat-secret" not in str(excinfo.value)


@respx.mock
def test_transport_errors_are_retried(client: GitLabClient) -> None:
    route = respx.get(f"{API}/projects/7")
    route.side_effect = [httpx.ConnectTimeout("slow"), httpx.Response(200, json={"id": 7})]
    assert client.project(7) == {"id": 7}


@respx.mock
def test_mr_diffs_falls_back_to_changes_on_old_gitlab(client: GitLabClient) -> None:
    respx.get(f"{API}/projects/7/merge_requests/3/diffs").respond(404)
    respx.get(f"{API}/projects/7/merge_requests/3/changes").respond(
        json={"changes": [{"new_path": "a.py", "diff": "@@"}]}
    )
    assert client.mr_diffs(7, 3) == [{"new_path": "a.py", "diff": "@@"}]


def test_rate_limiter_spaces_requests() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(max_per_second=2, sleep=sleep, clock=lambda: now[0])
    for _ in range(3):
        limiter.acquire()
    assert slept == [0.5, 0.5]


def test_rate_limiter_waits_for_reset_when_nearly_exhausted() -> None:
    slept: list[float] = []
    limiter = RateLimiter(max_per_second=10, sleep=slept.append)
    limiter.observe(
        httpx.Headers({"RateLimit-Remaining": "2", "RateLimit-Reset": "1030"}),
        wall_clock=lambda: 1000.0,
    )
    limiter.observe(
        httpx.Headers({"RateLimit-Remaining": "500", "RateLimit-Reset": "1030"}),
        wall_clock=lambda: 1000.0,
    )
    assert slept == [30.0]


@respx.mock
def test_commits_on_ref_lists_one_branch_or_every_branch(client: GitLabClient) -> None:
    route = respx.get(f"{API}/projects/7/repository/commits").respond(json=[{"id": "a"}])
    assert client.commits_on_ref(7, "develop", None, None, 10) == [{"id": "a"}]
    params = route.calls.last.request.url.params
    assert params["ref_name"] == "develop" and "all" not in params
    client.commits_on_ref(7, "*", None, None, 10)
    params = route.calls.last.request.url.params
    assert params["all"] == "true" and "ref_name" not in params
