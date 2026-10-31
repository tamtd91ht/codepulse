"""A request to close a critical/high finding, checked against the commits that fix it (outside
the spec, requested by the user 2026-10-31; app/review/resolve.py, app/api/routers/closures.py).

The author asked with the fixing commits; a manager of their team or an admin pressed "Chạy
review" (the request is `checking`). The worker fetches the commits from GitLab, refuses what
cannot be a fix (another repo, a commit older than the change that brought the finding, a diff
over MAX_REVIEW_LOC), then asks the same no-tool check the reviews use. "fixed" with evidence in
the diff closes the finding; anything else leaves it open and the request says why ("chưa đủ
điều kiện đóng"). The call's cost counts toward the month's LLM budget (`closures_usd`).

No transaction stays open while GitLab or the model work (docs/specs/19 §4.6).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.admin_settings import monthly_budget_usd, with_overrides
from app.api.audit import audit
from app.collector.gitlab.client import GitLabError
from app.collector.sync import ClientFactory, client_factory
from app.config import Settings, get_settings
from app.crypto import secret_box
from app.db import get_sessionmaker
from app.models import (
    Commit,
    Connection,
    FindingClosure,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
)
from app.models.review import CHANGE_TIME_UNKNOWN
from app.review.prepare import prepare
from app.review.providers import LLMProvider, build_provider
from app.review.resolve import check, mark_fixed

log = logging.getLogger(__name__)

# The audit action of one paid check: the month's budget sums these (`closures_usd`).
CHECK_COST = "finding.close_request.cost"


def _repo(session: Session, review: Review) -> Project | None:
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        return session.get(Project, mr.project_id) if mr and mr.project_id else None
    commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
    return session.get(Project, commit.project_id) if commit and commit.project_id else None


def _committed(info: Any) -> datetime | None:
    """When the commit entered the history (`committed_date`: rewritten on rebase, unlike the
    author date anyone can set); the author date only when GitLab gives nothing else."""
    if not isinstance(info, dict):
        return None
    raw = info.get("committed_date") or info.get("created_at") or info.get("authored_date")
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


@dataclass
class _Target:
    """What the check needs, copied out so no transaction is open during GitLab calls."""

    shas: list[str]
    external_id: int
    path: str
    since: datetime | None
    connection: Connection


def _still_mine(closure_id: int, token: str) -> tuple[Any, ...]:
    """The row of this run: still being checked, with this run's token."""
    return (
        FindingClosure.id == closure_id,
        FindingClosure.status == "checking",
        FindingClosure.run_token == token,
    )


def run_close_request(
    session: Session,
    closure_id: int,
    token: str,
    make_client: ClientFactory,
    provider: LLMProvider,
    settings: Settings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Decide one request being checked by the run `token`; returns its new status.

    Every write is conditional on "still checking, with this token": a request cancelled,
    refused or run again meanwhile is left alone ("superseded"), and a duplicate job of the same
    run never pays (it cannot claim the start)."""
    claimed = session.execute(
        update(FindingClosure)
        .where(*_still_mine(closure_id, token), FindingClosure.started_at.is_(None))
        .values(started_at=clock())
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if not getattr(claimed, "rowcount", 0):
        return "not mine"
    closure = session.get(FindingClosure, closure_id)
    if closure is None:
        return "not mine"
    session.refresh(closure)

    def decide(status: str, reason: str, *, commit: bool = True, **extra: Any) -> str:
        done = session.execute(
            update(FindingClosure)
            .where(*_still_mine(closure_id, token))
            .values(status=status, reason=reason[:1000], decided_at=clock(), **extra)
            .execution_options(synchronize_session=False)
        )
        if not getattr(done, "rowcount", 0):
            session.rollback()
            return "superseded"
        audit(
            session,
            closure.decided_by,
            "finding.close_request.checked",
            f"finding:{closure.finding_id}",
            closure_id=closure.id,
            status=status,
        )
        if commit:
            session.commit()
        return status

    finding = session.get(ReviewFinding, closure.finding_id)
    review = session.get(Review, finding.review_id) if finding and finding.review_id else None
    project = _repo(session, review) if review else None
    if finding is None or review is None or project is None:
        return decide("failed", "Không tìm thấy finding, review hoặc repo của nó.")
    if finding.status != "open":
        return decide("rejected", f"Finding không còn mở (trạng thái {finding.status}).")
    if project.allow_external_llm is False:
        return decide("rejected", "Repo này không cho dùng LLM bên ngoài nên không kiểm được.")
    budget = monthly_budget_usd(session, settings)
    if budget > 0:
        from app.workers.knowledge_jobs import month_spend_usd  # lazy: heavy module

        if month_spend_usd(session, clock()) >= budget:
            return decide("failed", "Đã dùng hết ngân sách LLM tháng này; chạy lại sau.")
    connection = session.get(Connection, project.connection_id)
    if connection is None:
        return decide("failed", "Repo chưa có kết nối GitLab.")
    target = _Target(
        shas=list(closure.shas),
        external_id=project.external_id,
        path=project.path_with_namespace,
        since=review.changed_at if review.changed_at > CHANGE_TIME_UNKNOWN else None,
        connection=connection,
    )
    session.commit()  # nothing open while GitLab answers

    entries: list[Any] = []
    refused: str | None = None
    try:
        with make_client(target.connection) as client:
            for sha in target.shas:
                try:
                    info = client.commit(target.external_id, sha)
                except GitLabError:
                    refused = f"Không tìm thấy commit {sha[:12]} trong repo {target.path}."
                    break
                when = _committed(info)
                if target.since is not None and when is not None and when < target.since:
                    refused = (
                        f"Commit {sha[:12]} có trước thay đổi gây ra finding nên không thể là "
                        "bản sửa."
                    )
                    break
                entries += client.commit_diff(target.external_id, sha)
    except GitLabError as exc:
        return decide("failed", f"GitLab: {str(exc)[:200]}")
    if refused is not None:
        return decide("rejected", refused)
    prepared = prepare(entries, settings.ignore_paths, settings.sensitive_paths)
    if not prepared.files:
        return decide("rejected", "Các commit không có thay đổi nào xem được (file bị bỏ qua).")
    if prepared.loc > settings.max_review_loc:
        return decide(
            "rejected",
            f"Các commit thay đổi {prepared.loc} dòng, quá MAX_REVIEW_LOC "
            f"({settings.max_review_loc}); chọn đúng commit sửa lỗi.",
        )
    change = "commit " + ", ".join(sha[:12] for sha in target.shas)

    checked = check(provider, target.path, change, prepared.files, [finding])
    if checked.call is not None:
        # Paid whatever comes next: recorded and committed before anything else can fail. The
        # request keeps the sum of its checks; the budget counts each call when it was made.
        cost = round(checked.call.cost_usd, 4)
        session.execute(
            update(FindingClosure)
            .where(FindingClosure.id == closure_id)
            .values(cost_usd=func.coalesce(FindingClosure.cost_usd, 0) + Decimal(str(cost)))
            .execution_options(synchronize_session=False)
        )
        audit(
            session,
            closure.decided_by,
            CHECK_COST,
            f"finding:{closure.finding_id}",
            closure_id=closure.id,
            cost_usd=cost,
        )
        session.commit()
    outcome = checked.outcomes[0] if checked.outcomes else None
    if outcome is None:
        return decide(
            "failed",
            "Không kiểm được (lỗi khi hỏi model); chạy lại sau."
            + (f" Chi tiết: {checked.error[:200]}" if checked.error else ""),
        )
    if outcome.closes:
        # One transaction: the request is closed only if this run still owns it, and the
        # finding only if it is still open; otherwise neither changes.
        if decide("closed", outcome.reason, commit=False, evidence=outcome.evidence) != "closed":
            return "superseded"
        if mark_fixed(session, finding):
            session.commit()
            return "closed"
        session.rollback()
        session.refresh(finding)
        return decide("rejected", f"Finding đã đổi trạng thái ({finding.status}) trong lúc kiểm.")
    why = {
        "fixed": (
            "Model cho là đã sửa nhưng chưa đủ chắc chắn hoặc không chỉ ra được chỗ sửa trong diff"
        ),
        "partial": "Mới sửa một phần",
        "not_fixed": "Các commit này chưa sửa lỗi",
    }[outcome.verdict]
    return decide(
        "rejected",
        f"Chưa đủ điều kiện đóng — {why}: {outcome.reason}",
        evidence=outcome.evidence,
    )


def close_request_job(closure_id: int, token: str) -> str:
    settings = get_settings()
    with get_sessionmaker()() as session:
        tuned = with_overrides(session, settings)
        session.commit()
        try:
            return run_close_request(
                session,
                closure_id,
                token,
                client_factory(secret_box()),
                build_provider(tuned),
                tuned,
            )
        except Exception:
            log.exception("close request failed", extra={"closure_id": closure_id})
            session.rollback()
            session.execute(
                update(FindingClosure)
                .where(*_still_mine(closure_id, token))
                .values(
                    status="failed",
                    reason="Lỗi khi kiểm; chạy lại sau.",
                    decided_at=datetime.now(UTC),
                )
            )
            session.commit()
            raise
