"""Collect step: GitLab API -> raw_gitlab, unchanged except that diff text is reduced to stats."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.base import CollectResult
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.gitlab.diffstat import file_stat
from app.models import MergeRequest, Pipeline, Project, RawGitlab

log = logging.getLogger(__name__)

# raw_gitlab.endpoint values; extract reads them back.
EP_PROJECT = "project"
EP_COMMIT = "commit"  # one row per commit
EP_COMMIT_DIFF = "commit_diff"  # params.sha, payload: list of file stats
EP_MR = "merge_request"  # one row per MR
EP_MR_COMMITS = "mr_commits"  # params.iid, payload: list of commits
EP_MR_NOTES = "mr_notes"  # params.iid, payload: list of notes
EP_MR_DIFFS = "mr_diffs"  # params.iid, payload: list of file stats
EP_PIPELINE = "pipeline"  # one row per pipeline

SYNC_OVERLAP = timedelta(hours=1)
# Per-item detail calls (one MR's diffs, notes...) retry once: a transient blip passes, a
# diff GitLab can never render (HTTP 500 on huge MRs) is skipped in ~1 s instead of ~30 s.
ITEM_RETRIES = 1


def skippable(exc: GitLabError) -> bool:
    """One item GitLab cannot serve (gone, or a 5xx on a huge/broken diff) must not fail the
    whole sync; auth and rate-limit errors still do (they affect every call)."""
    status = exc.status_code or 0
    return status == 404 or status >= 500


def window_start(project: Project, now: datetime, backfill_days: int) -> datetime:
    """Incremental from last_synced_at with a 1h overlap; first run backfills N days."""
    if project.last_synced_at is None:
        return now - timedelta(days=backfill_days)
    return project.last_synced_at - SYNC_OVERLAP


def _stored_mr(session: Session, project_id: int, iid: int) -> bool:
    return (
        session.scalar(
            select(MergeRequest.id).where(
                MergeRequest.project_id == project_id, MergeRequest.iid == iid
            )
        )
        is not None
    )


class GitLabCollector:
    def __init__(
        self, client: GitLabClient, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._client = client
        self._clock = clock

    def collect(self, session: Session, project: Project, since: datetime | None) -> CollectResult:
        result = CollectResult(window_start=since, started_at=self._clock())
        pid = project.external_id

        def store(endpoint: str, payload: Any, **params: Any) -> None:
            session.add(
                RawGitlab(
                    project_id=project.id,
                    endpoint=endpoint,
                    params=params or None,
                    payload=payload,
                    fetched_at=result.started_at,
                )
            )
            result.counts[endpoint] = result.counts.get(endpoint, 0) + 1

        def detail(endpoint: str, fetch: Callable[[], Any], **params: Any) -> Any | None:
            """A per-item call; an item GitLab cannot serve is logged and skipped."""
            try:
                with self._client.retries(ITEM_RETRIES):
                    return fetch()
            except GitLabError as exc:
                if not skippable(exc):
                    raise
                result.counts[f"skipped.{endpoint}"] = (
                    result.counts.get(f"skipped.{endpoint}", 0) + 1
                )
                log.warning(
                    "gitlab item skipped",
                    extra={
                        "project_id": project.id,
                        "endpoint": endpoint,
                        "status": exc.status_code,
                        **params,
                    },
                )
                return None

        store(EP_PROJECT, self._client.project(pid))

        for commit in self._client.commits(pid, since):
            store(EP_COMMIT, commit, sha=commit["id"])
            if len(commit.get("parent_ids") or []) <= 1:  # merge commits carry no own LOC
                sha = commit["id"]
                diff = detail(EP_COMMIT_DIFF, partial(self._client.commit_diff, pid, sha), sha=sha)
                if diff is not None:
                    store(EP_COMMIT_DIFF, [file_stat(e) for e in diff], sha=sha)
            session.flush()

        for mr in self._client.merge_requests(pid, since):
            iid = mr["iid"]
            parts: dict[str, Any] = {}
            for endpoint, fetch in (
                (EP_MR_COMMITS, lambda iid=iid: list(self._client.mr_commits(pid, iid))),
                (EP_MR_NOTES, lambda iid=iid: list(self._client.mr_notes(pid, iid))),
                (
                    EP_MR_DIFFS,
                    lambda iid=iid: [file_stat(e) for e in self._client.mr_diffs(pid, iid)],
                ),
            ):
                payload = detail(endpoint, fetch, iid=iid)
                if payload is not None:
                    parts[endpoint] = payload
            if len(parts) < 3 and _stored_mr(session, project.id, iid):
                # A partial MR would overwrite stored notes/LOC with empty values: keep the
                # stored row as it is until GitLab serves every part again.
                result.counts["skipped.merge_request"] = (
                    result.counts.get("skipped.merge_request", 0) + 1
                )
                continue
            store(EP_MR, mr, iid=iid)
            for endpoint, payload in parts.items():
                store(endpoint, payload, iid=iid)
            session.flush()

        for pipeline in self._client.pipelines(pid, since):
            # The list omits duration/finished_at; the detail endpoint has them.
            pipeline_id = pipeline["id"]
            extra = detail(
                EP_PIPELINE, partial(self._client.pipeline, pid, pipeline_id), id=pipeline_id
            )
            if extra is None and session.get(Pipeline, pipeline_id) is not None:
                continue  # keep the stored duration / finished_at
            store(EP_PIPELINE, {**pipeline, **(extra or {})}, id=pipeline_id)
        session.flush()

        log.info(
            "collected gitlab project",
            extra={"project_id": project.id, "since": since, "counts": result.counts},
        )
        return result

    def collect_commit(self, session: Session, project: Project, sha: str) -> list[RawGitlab]:
        """One commit (and its diff stats) into raw_gitlab, for a review of a commit the last
        sync has not seen (another branch, or newer than the sync). Returns the new rows."""
        pid = project.external_id
        fetched = self._clock()
        commit = self._client.commit(pid, sha)
        rows = [
            RawGitlab(
                project_id=project.id,
                endpoint=EP_COMMIT,
                params={"sha": commit["id"]},
                payload=commit,
                fetched_at=fetched,
            )
        ]
        if len(commit.get("parent_ids") or []) <= 1:
            try:
                diff = self._client.commit_diff(pid, commit["id"])
            except GitLabError as exc:
                if not skippable(exc):
                    raise
                diff = None
            if diff is not None:
                rows.append(
                    RawGitlab(
                        project_id=project.id,
                        endpoint=EP_COMMIT_DIFF,
                        params={"sha": commit["id"]},
                        payload=[file_stat(e) for e in diff],
                        fetched_at=fetched,
                    )
                )
        session.add_all(rows)
        session.flush()
        return rows
