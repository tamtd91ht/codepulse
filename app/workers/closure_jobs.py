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


def run_close_request(
    session: Session,
    closure_id: int,
    make_client: ClientFactory,
    provider: LLMProvider,
    settings: Settings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Decide one request being checked; returns its new status."""
    closure = session.get(FindingClosure, closure_id)
    if closure is None or closure.status != "checking":
        return "not checking"

    def decide(status: str, reason: str, **extra: Any) -> str:
        closure.status = status
        closure.reason = reason[:1000]
        closure.decided_at = clock()
        for key, value in extra.items():
            setattr(closure, key, value)
        audit(
            session,
            closure.decided_by,
            "finding.close_request.checked",
            f"finding:{closure.finding_id}",
            closure_id=closure.id,
            status=status,
        )
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
    # Paid whatever comes next: on the row before anything else can fail.
    closure.cost_usd = Decimal(str(round(checked.call.cost_usd, 4))) if checked.call else None
    outcome = checked.outcomes[0] if checked.outcomes else None
    if outcome is None:
        return decide(
            "failed",
            "Không kiểm được (lỗi khi hỏi model); chạy lại sau."
            + (f" Chi tiết: {checked.error[:200]}" if checked.error else ""),
        )
    if outcome.closes:
        if mark_fixed(session, finding):
            return decide("closed", outcome.reason, evidence=outcome.evidence)
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


def close_request_job(closure_id: int) -> str:
    settings = get_settings()
    with get_sessionmaker()() as session:
        tuned = with_overrides(session, settings)
        session.commit()
        try:
            return run_close_request(
                session, closure_id, client_factory(secret_box()), build_provider(tuned), tuned
            )
        except Exception:
            log.exception("close request failed", extra={"closure_id": closure_id})
            session.rollback()
            closure = session.get(FindingClosure, closure_id)
            if closure is not None and closure.status == "checking":
                closure.status = "failed"
                closure.reason = "Lỗi khi kiểm; chạy lại sau."
                closure.decided_at = datetime.now(UTC)
                session.commit()
            raise
