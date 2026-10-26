"""GitLab REST v4 client: pagination, retry with backoff, rate limit, timeout (docs/specs/04)."""

import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.models.source import ALL_BRANCHES
from app.ratelimit import RateLimiter

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
PER_PAGE = 100


class ArchiveTooLarge(Exception):
    """The repository archive exceeds DEEP_REVIEW_MAX_ARCHIVE_MB."""


class GitLabError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _hook_body(url: str, token: str) -> dict[str, Any]:
    return {
        "url": url,
        "token": token,
        "push_events": True,
        "merge_requests_events": True,
        "pipeline_events": True,
        "enable_ssl_verification": True,
    }


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 5,
        max_per_second: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self._mr_diffs_broken = False  # set once /diffs failed and /changes answered
        self._limiter = RateLimiter(max_per_second, sleep=sleep)

    def close(self) -> None:
        self._http.close()

    @contextmanager
    def retries(self, max_retries: int) -> Iterator[None]:
        """Fewer retries for the calls inside: a per-item call whose 5xx is usually permanent
        (GitLab cannot render a huge diff) must not cost ~30 s of backoff each time."""
        saved = self._max_retries
        self._max_retries = min(saved, max_retries)
        try:
            yield
        finally:
            self._max_retries = saved

    def __enter__(self) -> "GitLabClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------------------------

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise GitLabError(f"{method} {url}: {exc.__class__.__name__}") from exc
                self._sleep(self._backoff(attempt, None))
                continue
            self._limiter.observe(response.headers)
            if response.status_code in RETRY_STATUS and attempt < self._max_retries:
                delay = self._backoff(attempt, response.headers.get("Retry-After"))
                log.warning(
                    "gitlab request retried",
                    extra={"status": response.status_code, "url": url, "delay_s": delay},
                )
                self._sleep(delay)
                continue
            if response.is_error:
                raise GitLabError(
                    f"{method} {url} -> HTTP {response.status_code}", response.status_code
                )
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 120.0)
            except ValueError:
                try:
                    delta = parsedate_to_datetime(retry_after) - datetime.now().astimezone()
                    return min(max(delta.total_seconds(), 0.0), 120.0)
                except (TypeError, ValueError):
                    pass
        return min(2.0**attempt, 60.0) + random.uniform(0, 0.5)  # noqa: S311 - jitter, not crypto

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params).json()

    def post(self, path: str, json: dict[str, Any]) -> Any:
        return self.request("POST", path, json=json).json()

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[Any]:
        """Yield items across pages: keyset `Link: rel="next"` first, then `X-Next-Page`."""
        query: dict[str, Any] | None = {"per_page": PER_PAGE, **(params or {})}
        url = path
        while True:
            response = self.request("GET", url, params=query)
            yield from response.json()
            next_link = response.links.get("next", {}).get("url")
            if next_link:
                url, query = next_link, None  # the link already carries every parameter
                continue
            next_page = response.headers.get("X-Next-Page")
            if not next_page:
                return
            query = {**(query or {}), "page": next_page}

    # -- endpoints (docs/specs/04) -----------------------------------------------------------

    def current_user(self) -> Any:
        return self.get("/user")

    def project(self, project_id: int) -> Any:
        return self.get(f"/projects/{project_id}")

    def project_by_path(self, path: str) -> Any:
        """A project by its full path (`group/sub/repo`)."""
        return self.get(f"/projects/{quote(path.strip('/'), safe='')}")

    def commits(self, project_id: int, since: datetime | None) -> Iterator[Any]:
        params: dict[str, Any] = {"with_stats": "true", "all": "true"}
        if since:
            params["since"] = since.isoformat()
        return self.paginate(f"/projects/{project_id}/repository/commits", params)

    def commit_diff(self, project_id: int, sha: str) -> list[Any]:
        return list(self.paginate(f"/projects/{project_id}/repository/commits/{sha}/diff"))

    def commit(self, project_id: int, sha: str) -> Any:
        return self.get(f"/projects/{project_id}/repository/commits/{sha}", {"stats": "true"})

    def branches(self, project_id: int, search: str | None = None, limit: int = 100) -> list[Any]:
        """Branches by name match (GitLab sorts by name); newest activity is not sortable here."""
        params: dict[str, Any] = {"per_page": min(limit, 100)}
        if search:
            params["search"] = search
        return list(self.get(f"/projects/{project_id}/repository/branches", params) or [])[:limit]

    def branch_exists(self, project_id: int, name: str) -> bool:
        """Exact lookup (the list endpoint's `search` is a substring match capped at a page)."""
        try:
            self.get(f"/projects/{project_id}/repository/branches/{quote(name, safe='')}")
        except GitLabError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    def commits_on_ref(
        self,
        project_id: int,
        ref: str,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[Any]:
        """Newest first, at most `limit`, with stats; stops paginating once it has enough.
        `ref` "*" lists every branch (GitLab `all=true`)."""
        params: dict[str, Any] = {"with_stats": "true"}
        if ref == ALL_BRANCHES:
            params["all"] = "true"
        else:
            params["ref_name"] = ref
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()
        out: list[Any] = []
        for item in self.paginate(f"/projects/{project_id}/repository/commits", params):
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def merge_requests(self, project_id: int, updated_after: datetime | None) -> Iterator[Any]:
        params: dict[str, Any] = {"state": "all", "order_by": "updated_at", "sort": "asc"}
        if updated_after:
            params["updated_after"] = updated_after.isoformat()
        return self.paginate(f"/projects/{project_id}/merge_requests", params)

    def open_merge_requests(self, project_id: int, limit: int = 40) -> list[Any]:
        """Open MRs, most recently updated first (for the Telegram picker)."""
        params: dict[str, Any] = {"state": "opened", "order_by": "updated_at", "sort": "desc"}
        out: list[Any] = []
        for item in self.paginate(f"/projects/{project_id}/merge_requests", params):
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def mr_diffs(self, project_id: int, iid: int) -> list[Any]:
        """File entries of an MR. GitLab < 15.7 has no /diffs (404) and some instances answer
        it with HTTP 500 for every MR; /changes returns the same entries under "changes".
        Once /diffs failed that way, this client goes straight to /changes."""
        if not self._mr_diffs_broken:
            try:
                # No retries: a failing /diffs has a fallback right below.
                with self.retries(0):
                    return list(self.paginate(f"/projects/{project_id}/merge_requests/{iid}/diffs"))
            except GitLabError as exc:
                status = exc.status_code or 0
                if status != 404 and status < 500:
                    raise
        changes = self.get(f"/projects/{project_id}/merge_requests/{iid}/changes")
        if not self._mr_diffs_broken:
            self._mr_diffs_broken = True
            log.warning("gitlab /diffs unusable, using /changes", extra={"project": project_id})
        return list(changes.get("changes", []))

    def mr_commits(self, project_id: int, iid: int) -> Iterator[Any]:
        return self.paginate(f"/projects/{project_id}/merge_requests/{iid}/commits")

    def mr_notes(self, project_id: int, iid: int) -> Iterator[Any]:
        return self.paginate(
            f"/projects/{project_id}/merge_requests/{iid}/notes",
            {"sort": "asc", "order_by": "created_at"},
        )

    def mr_approvals(self, project_id: int, iid: int) -> Any:
        return self.get(f"/projects/{project_id}/merge_requests/{iid}/approvals")

    def pipelines(self, project_id: int, updated_after: datetime | None) -> Iterator[Any]:
        params: dict[str, Any] = {}
        if updated_after:
            params["updated_after"] = updated_after.isoformat()
        return self.paginate(f"/projects/{project_id}/pipelines", params)

    def pipeline(self, project_id: int, pipeline_id: int) -> Any:
        return self.get(f"/projects/{project_id}/pipelines/{pipeline_id}")

    def user(self, user_id: int) -> Any:
        return self.get(f"/users/{user_id}")

    def merge_request(self, project_id: int, iid: int) -> Any:
        return self.get(f"/projects/{project_id}/merge_requests/{iid}")

    def commit_merge_requests(self, project_id: int, sha: str) -> list[Any]:
        return list(
            self.paginate(f"/projects/{project_id}/repository/commits/{sha}/merge_requests")
        )

    def archive(
        self, project_id: int, sha: str, dest: Path, max_bytes: int, path: str | None = None
    ) -> int:
        """Stream `archive.tar.gz?sha=` to `dest`; stops (ArchiveTooLarge) past max_bytes.
        `path`: only that folder of the repository.

        One attempt: a multi-megabyte download is not retried blindly, the caller falls back.
        """
        self._limiter.acquire()
        total = 0
        params = {"sha": sha, **({"path": path} if path else {})}
        try:
            with self._http.stream(
                "GET", f"/projects/{project_id}/repository/archive.tar.gz", params=params
            ) as response:
                if response.is_error:
                    raise GitLabError(
                        f"GET archive -> HTTP {response.status_code}", response.status_code
                    )
                with open(dest, "wb") as out:
                    for piece in response.iter_bytes():
                        total += len(piece)
                        if total > max_bytes:
                            raise ArchiveTooLarge(f"archive larger than {max_bytes} bytes")
                        out.write(piece)
        except httpx.TransportError as exc:
            raise GitLabError(f"GET archive: {exc.__class__.__name__}") from exc
        return total

    def file_raw(self, project_id: int, path: str, ref: str) -> str:
        encoded = quote(path, safe="")
        response = self.request(
            "GET", f"/projects/{project_id}/repository/files/{encoded}/raw", params={"ref": ref}
        )
        return response.text

    def create_mr_note(self, project_id: int, iid: int, body: str) -> Any:
        return self.post(f"/projects/{project_id}/merge_requests/{iid}/notes", {"body": body})

    def update_mr_note(self, project_id: int, iid: int, note_id: int, body: str) -> Any:
        path = f"/projects/{project_id}/merge_requests/{iid}/notes/{note_id}"
        return self.request("PUT", path, json={"body": body}).json()

    def group_projects(self, group: str) -> Iterator[Any]:
        """Active projects of a group (numeric id or full path), subgroups included."""
        params = {"include_subgroups": "true", "archived": "false", "with_shared": "false"}
        return self.paginate(f"/groups/{quote(group, safe='')}/projects", params)

    def project_hooks(self, project_id: int) -> list[Any]:
        return list(self.paginate(f"/projects/{project_id}/hooks"))

    def add_project_hook(self, project_id: int, url: str, token: str) -> Any:
        return self.post(f"/projects/{project_id}/hooks", _hook_body(url, token))

    def delete_project_hook(self, project_id: int, hook_id: int) -> None:
        """Idempotent: a hook that is already gone (e.g. a retried DELETE) counts as deleted."""
        try:
            self.request("DELETE", f"/projects/{project_id}/hooks/{hook_id}")
        except GitLabError as exc:
            if exc.status_code != 404:
                raise

    def update_project_hook(self, project_id: int, hook_id: int, url: str, token: str) -> Any:
        path = f"/projects/{project_id}/hooks/{hook_id}"
        return self.request("PUT", path, json=_hook_body(url, token)).json()
