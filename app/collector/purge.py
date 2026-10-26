"""Wipe this server's data from the Admin screen (requested by the user, see PROGRESS.md).

Like app/collector/removal.py, this is an explicit exception to docs/specs/AGENTS.md "review
history is never deleted": an ADMIN_EMAILS admin chose to start clean. Only rows in this
server's database go; nothing is sent to GitLab (repo webhooks stay where they are). Users,
their GitLab accounts, teams, connections and the audit log are kept by every scope.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Delete, delete, func, select, text, update
from sqlalchemy.orm import Session

from app.models import (
    AppSetting,
    BusinessProject,
    Commit,
    FindingDispute,
    MergeRequest,
    MetricsTeamPeriod,
    MetricsUserPeriod,
    MrCommit,
    MrNote,
    NotificationWebhook,
    Pipeline,
    Project,
    RawGitlab,
    Review,
    ReviewFinding,
    StoredSecret,
    TelegramBot,
    TelegramRequest,
)

Scope = Literal["reviews", "collected", "all"]

LOCK_TIMEOUT = "5s"

# What the admin types to confirm each scope.
CONFIRMATION: dict[Scope, str] = {
    "reviews": "xoa review",
    "collected": "xoa du lieu",
    "all": "xoa toan bo",
}

# Children first. Each scope also deletes everything of the scopes before it.
_REVIEWS: list[tuple[str, Any]] = [
    ("telegram_requests", TelegramRequest),
    ("finding_disputes", FindingDispute),
    ("review_findings", ReviewFinding),
    ("reviews", Review),
    ("metrics_user_period", MetricsUserPeriod),
    ("metrics_team_period", MetricsTeamPeriod),
]
_COLLECTED: list[tuple[str, Any]] = [
    ("mr_notes", MrNote),
    ("mr_commits", MrCommit),
    ("pipelines", Pipeline),
    ("merge_requests", MergeRequest),
    ("commits", Commit),
    ("raw_gitlab", RawGitlab),
]
_CONFIG: list[tuple[str, Any]] = [
    ("telegram_bots", TelegramBot),
    ("notification_webhooks", NotificationWebhook),
    ("projects", Project),
    ("business_projects", BusinessProject),
    ("app_settings", AppSetting),
    ("stored_secrets", StoredSecret),
]


def tables(scope: Scope) -> list[tuple[str, Any]]:
    """(table, model) deleted by `scope`, in delete order."""
    out = list(_REVIEWS)
    if scope in ("collected", "all"):
        out += _COLLECTED
    if scope == "all":
        out += _CONFIG
    return out


def counts(session: Session) -> dict[str, int]:
    """Rows per table that some scope would delete."""
    return {
        table: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for table, model in tables("all")
    }


@dataclass
class PurgeResult:
    rows: dict[str, int] = field(default_factory=dict)
    # "reviews" keeps MRs: their periods are re-aggregated without the deleted reviews.
    first_day: date | None = None
    last_day: date | None = None


def purge(session: Session, scope: Scope, tz: ZoneInfo) -> PurgeResult:
    """Delete in the caller's transaction. The repo rows are locked first, so a sync running now
    waits instead of inserting rows that would break the deletes.

    A review job holds its rows for the whole review: rather than wait for it with every repo
    locked, give up after LOCK_TIMEOUT (OperationalError, the caller answers "try again")."""
    session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    session.execute(select(Project.id).with_for_update())
    result = PurgeResult()
    if scope == "reviews":
        first, last = session.execute(
            select(func.min(MergeRequest.merged_at), func.max(MergeRequest.merged_at))
        ).one()
        days: list[datetime] = [d for d in (first, last) if d is not None]
        if days:
            result.first_day = min(days).astimezone(tz).date()
            result.last_day = max(days).astimezone(tz).date()
    for table, model in tables(scope):
        statement: Delete = delete(model)
        result.rows[table] = session.execute(statement).rowcount  # type: ignore[attr-defined]
    if scope == "collected":
        # The next sync collects every repo again from the start of the backfill window.
        session.execute(update(Project).values(last_synced_at=None))
    return result
