"""Remove repos and everything collected or reviewed for them (requested by the user).

This is the explicit exception to docs/specs/AGENTS.md "review history is never deleted": an
admin chose to wipe repos they do not care about. Untracking (projects.tracked) is the
history-keeping alternative. Metrics of the periods that contained the deleted MRs and reviews
are re-aggregated afterwards (recompute_job), so past numbers change.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabError
from app.collector.sync import WEBHOOK_PATH, client_factory
from app.models import (
    Commit,
    Connection,
    FindingDispute,
    MergeRequest,
    MrCommit,
    MrNote,
    Pipeline,
    Project,
    RawGitlab,
    Review,
    ReviewFinding,
)

log = logging.getLogger(__name__)
REMOVAL_JOB_TIMEOUT = 5 * 60


def confirmation_phrase(paths: list[str]) -> str:
    """What the admin must type: the repo's path for one repo, a count phrase for several."""
    return paths[0] if len(paths) == 1 else f"xoa {len(paths)} repo"


@dataclass
class RemovalResult:
    project_ids: list[int] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    reviews: int = 0
    rows: dict[str, int] = field(default_factory=dict)  # deleted rows per table, for the audit
    # For the webhook clean-up after the rows are gone: (connection_id, GitLab project id).
    hooks: list[tuple[int, int]] = field(default_factory=list)
    first_day: date | None = None  # day range of the deleted MRs/reviews, to re-aggregate
    last_day: date | None = None


def remove_projects(session: Session, project_ids: list[int], tz: ZoneInfo) -> RemovalResult:
    """Delete the repos and their data in the caller's transaction (children first).

    The project rows are locked first, so a sync of the same repo waits instead of inserting
    rows that would break the final deletes.
    """
    projects = list(
        session.scalars(
            select(Project)
            .where(Project.id.in_(project_ids))
            .order_by(Project.id)
            .with_for_update()
        )
    )
    result = RemovalResult(
        project_ids=[p.id for p in projects], paths=[p.path_with_namespace for p in projects]
    )
    if not projects:
        return result
    ids = result.project_ids
    result.hooks = [(p.connection_id, p.external_id) for p in projects if p.connection_id]

    mr_ids = select(MergeRequest.id).where(MergeRequest.project_id.in_(ids))
    # commits are keyed by sha alone: a sha that another repo's MR also contains (fork, mirror)
    # stays, with its review, so that repo keeps its history.
    shared = (
        select(MrCommit.sha)
        .join(MergeRequest, MergeRequest.id == MrCommit.mr_id)
        .where(MrCommit.sha == Commit.sha, MergeRequest.project_id.not_in(ids))
    )
    shas = select(Commit.sha).where(Commit.project_id.in_(ids), ~exists(shared))
    reviews = select(Review.id).where(or_(Review.mr_id.in_(mr_ids), Review.commit_sha.in_(shas)))

    days: list[datetime] = [
        moment
        for row in (
            session.execute(
                select(func.min(Review.finished_at), func.max(Review.finished_at)).where(
                    Review.id.in_(reviews)
                )
            ).one(),
            session.execute(
                select(func.min(MergeRequest.merged_at), func.max(MergeRequest.merged_at)).where(
                    MergeRequest.project_id.in_(ids)
                )
            ).one(),
        )
        for moment in row
        if moment is not None
    ]
    if days:
        result.first_day = min(days).astimezone(tz).date()
        result.last_day = max(days).astimezone(tz).date()

    findings = select(ReviewFinding.id).where(ReviewFinding.review_id.in_(reviews))
    steps: list[tuple[str, Any]] = [
        ("finding_disputes", delete(FindingDispute).where(FindingDispute.finding_id.in_(findings))),
        ("review_findings", delete(ReviewFinding).where(ReviewFinding.review_id.in_(reviews))),
        ("reviews", delete(Review).where(Review.id.in_(reviews))),
        ("mr_notes", delete(MrNote).where(MrNote.mr_id.in_(mr_ids))),
        (
            "mr_commits",
            delete(MrCommit).where(or_(MrCommit.mr_id.in_(mr_ids), MrCommit.sha.in_(shas))),
        ),
        ("pipelines", delete(Pipeline).where(Pipeline.project_id.in_(ids))),
        ("merge_requests", delete(MergeRequest).where(MergeRequest.project_id.in_(ids))),
        ("commits", delete(Commit).where(Commit.sha.in_(shas))),
        ("raw_gitlab", delete(RawGitlab).where(RawGitlab.project_id.in_(ids))),
    ]
    for table, statement in steps:
        result.rows[table] = session.execute(statement).rowcount  # type: ignore[attr-defined]
    result.reviews = result.rows["reviews"]
    # Shared commits stay, attributed to no repo, until another repo's sync claims them again.
    session.execute(update(Commit).where(Commit.project_id.in_(ids)).values(project_id=None))
    session.execute(delete(Project).where(Project.id.in_(ids)))
    return result


def remove_webhook_job(connection_id: int, external_id: int, base_url: str) -> dict[str, object]:
    """RQ entry point: delete the CodePulse hook of a removed repo on GitLab.

    Skipped when the repo was added again meanwhile (its new hook must stay). A repo that is
    gone on GitLab, or no longer visible to the bot, has nothing left to clean; other errors
    raise so RQ retries.
    """
    from app.crypto import secret_box
    from app.db import get_sessionmaker

    url = base_url.rstrip("/") + WEBHOOK_PATH
    removed = 0
    with get_sessionmaker()() as session:
        readded = session.scalar(
            select(Project.id).where(
                Project.connection_id == connection_id, Project.external_id == external_id
            )
        )
        if readded is not None:
            return {"removed": 0, "reason": "repo added again"}
        connection = session.get(Connection, connection_id)
        if connection is None:
            return {"removed": 0, "reason": "connection not found"}
        try:
            with client_factory(secret_box())(connection) as client:
                for hook in client.project_hooks(external_id):
                    if hook.get("url") == url:
                        client.delete_project_hook(external_id, int(hook["id"]))
                        removed += 1
        except GitLabError as exc:
            if exc.status_code not in (403, 404):
                raise
            log.warning(
                "webhook not removed",
                extra={"external_id": external_id, "removed": removed, "reason": str(exc)},
            )
            return {"removed": removed, "reason": str(exc)}
    log.info("webhook removed", extra={"external_id": external_id, "removed": removed})
    return {"removed": removed}
