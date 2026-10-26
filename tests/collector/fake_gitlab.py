"""A respx-mocked GitLab serving tests/fixtures/gitlab/*.json."""

import json
import re
from pathlib import Path
from typing import Any

import httpx
import respx

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "gitlab"
BASE = "https://gitlab.example.com"
API = f"{BASE}/api/v4"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _serve(name: str) -> httpx.Response:
    path = FIXTURES / name
    if not path.exists():
        return httpx.Response(404, json={"message": "404 Not Found"})
    return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))


def mount(router: respx.MockRouter, project_id: int = 1001) -> respx.MockRouter:
    p = f"{API}/projects/{project_id}"
    router.get(f"{p}").mock(side_effect=lambda r: _serve("project.json"))
    router.get(f"{p}/repository/commits").mock(side_effect=lambda r: _serve("commits.json"))
    router.get(url__regex=rf"{p}/repository/commits/(?P<sha>\w+)/diff").mock(
        side_effect=lambda r, sha: _serve(f"commit_diff_{sha}.json")
    )
    router.get(f"{p}/merge_requests").mock(side_effect=lambda r: _serve("merge_requests.json"))
    router.get(url__regex=rf"{p}/merge_requests/(?P<iid>\d+)/(?P<kind>commits|notes|diffs)").mock(
        side_effect=lambda r, iid, kind: _serve(f"mr_{iid}_{kind}.json")
    )
    router.get(f"{p}/pipelines").mock(side_effect=lambda r: _serve("pipelines.json"))
    router.get(url__regex=rf"{p}/pipelines/(?P<pid>\d+)$").mock(
        side_effect=lambda r, pid: _serve(f"pipeline_{pid}.json")
    )
    # File contents (review context): none by default, so context is simply skipped.
    router.get(url__regex=rf"{p}/repository/files/.+/raw").mock(
        return_value=httpx.Response(404, json={"message": "404 File Not Found"})
    )
    # The repo's own .claude for the review's first pass: none by default.
    router.get(f"{p}/repository/archive.tar.gz", params={"path": ".claude"}).mock(
        return_value=httpx.Response(404, json={"message": "404 Not Found"})
    )
    router.get(url__regex=rf"{API}/users/(?P<uid>\d+)$").mock(
        side_effect=lambda r, uid: _serve(f"users_{uid}.json")
    )
    return router


def requested_paths(router: respx.MockRouter) -> list[str]:
    return [re.sub(r"\?.*", "", str(call.request.url)) for call in router.calls]
