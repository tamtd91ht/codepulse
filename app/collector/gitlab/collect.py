"""Collect step: GitLab API -> raw_gitlab, unchanged except that diff text is reduced to stats."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.collector.base import CollectResult
from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.diffstat import file_stat
from app.models import Project, RawGitlab

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


def window_start(project: Project, now: datetime, backfill_days: int) -> datetime:
    """Incremental from last_synced_at with a 1h overlap; first run backfills N days."""
    if project.last_synced_at is None:
        return now - timedelta(days=backfill_days)
    return project.last_synced_at - SYNC_OVERLAP


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

        store(EP_PROJECT, self._client.project(pid))

        for commit in self._client.commits(pid, since):
            store(EP_COMMIT, commit, sha=commit["id"])
            if len(commit.get("parent_ids") or []) <= 1:  # merge commits carry no own LOC
                diff = self._client.commit_diff(pid, commit["id"])
                store(EP_COMMIT_DIFF, [file_stat(e) for e in diff], sha=commit["id"])
            session.flush()

        for mr in self._client.merge_requests(pid, since):
            iid = mr["iid"]
            store(EP_MR, mr, iid=iid)
            store(EP_MR_COMMITS, list(self._client.mr_commits(pid, iid)), iid=iid)
            store(EP_MR_NOTES, list(self._client.mr_notes(pid, iid)), iid=iid)
            store(EP_MR_DIFFS, [file_stat(e) for e in self._client.mr_diffs(pid, iid)], iid=iid)
            session.flush()

        for pipeline in self._client.pipelines(pid, since):
            # The list omits duration/finished_at; the detail endpoint has them.
            detail = self._client.pipeline(pid, pipeline["id"])
            store(EP_PIPELINE, {**pipeline, **detail}, id=pipeline["id"])
        session.flush()

        log.info(
            "collected gitlab project",
            extra={"project_id": project.id, "since": since, "counts": result.counts},
        )
        return result
