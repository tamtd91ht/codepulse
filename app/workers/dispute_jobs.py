"""Run the AI second look at a dispute (app/review/dispute_check.py), queued by a lead from
the dispute (POST /disputes/{id}/ai-check). The file comes from GitLab at the reviewed commit;
no transaction stays open while GitLab or the model work. The call's cost is audited as
`dispute.ai_check.cost` and counts toward the month's LLM budget."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.admin_settings import monthly_budget_usd, with_overrides
from app.api.access import review_repo
from app.api.audit import audit
from app.collector.gitlab.client import GitLabError
from app.collector.gitlab.diffstat import is_ignored
from app.collector.sync import ClientFactory, client_factory
from app.config import Settings, get_settings
from app.crypto import secret_box
from app.db import get_sessionmaker
from app.models import Connection, FindingDispute, Review, ReviewFinding
from app.review.dispute_check import analyse, build_prompt, numbered_window
from app.review.masking import is_config, mask_code, mask_config
from app.review.providers import LLMProvider, build_provider

log = logging.getLogger(__name__)

COST_ACTION = "dispute.ai_check.cost"
# A check that has not finished in this long (its job died) no longer blocks a new one.
STALE = timedelta(minutes=15)
ACTIVE = ("queued", "running")


def running(dispute: FindingDispute, now: datetime | None = None) -> bool:
    at = dispute.ai_requested_at
    return (
        dispute.ai_status in ACTIVE and at is not None and (now or datetime.now(UTC)) - at < STALE
    )


# Larger files (generated, minified) are not read: the model sees the finding only.
MAX_FILE_BYTES = 200_000


def _finish(session: Session, dispute_id: int, token: datetime, status: str, **values: Any) -> str:
    """Write this run's result only while the run is still the dispute's current one (a lead
    may have asked again after it was given up as stale)."""
    session.rollback()
    moved = session.execute(
        update(FindingDispute)
        .where(
            FindingDispute.id == dispute_id,
            FindingDispute.ai_requested_at == token,
            FindingDispute.ai_status.in_(ACTIVE),
        )
        .values(ai_status=status, ai_finished_at=datetime.now(UTC), **values)
    ).rowcount  # type: ignore[attr-defined]
    session.commit()
    return status if moved else "superseded"


def run_dispute_check(
    session: Session,
    dispute_id: int,
    make_client: ClientFactory,
    provider: LLMProvider,
    settings: Settings,
    requested_at: datetime | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """`requested_at` is the run's token (the dispute's `ai_requested_at` when it was queued);
    None takes the current one."""
    dispute = session.get(FindingDispute, dispute_id)
    if dispute is None or dispute.ai_requested_at is None:
        return "not mine"
    token = requested_at or dispute.ai_requested_at
    claimed = session.execute(
        update(FindingDispute)
        .where(
            FindingDispute.id == dispute_id,
            FindingDispute.ai_status == "queued",
            FindingDispute.ai_requested_at == token,
        )
        .values(ai_status="running")
    ).rowcount  # type: ignore[attr-defined]
    session.commit()
    if not claimed:
        return "not mine"
    session.refresh(dispute)
    requested_by = dispute.ai_requested_by
    finding = session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None
    review = session.get(Review, finding.review_id) if finding and finding.review_id else None
    project = review_repo(session, review) if review else None
    if finding is None or review is None or project is None:
        return _finish(
            session,
            dispute_id,
            token,
            "failed",
            ai_reason="Không tìm thấy finding, review hoặc repo.",
        )
    if project.allow_external_llm is False:
        return _finish(
            session, dispute_id, token, "failed", ai_reason="Repo này không cho dùng LLM bên ngoài."
        )
    budget = monthly_budget_usd(session, settings)
    if budget > 0:
        from app.workers.knowledge_jobs import month_spend_usd  # lazy: heavy module

        if month_spend_usd(session, clock()) >= budget:
            return _finish(
                session,
                dispute_id,
                token,
                "failed",
                ai_reason="Đã dùng hết ngân sách LLM tháng này.",
            )
    connection = session.get(Connection, project.connection_id)
    path = finding.file_path
    facts = {
        "severity": finding.severity,
        "category": finding.category,
        "title": finding.title,
        "message": finding.message,
        "suggestion": finding.suggestion,
        "code_excerpt": finding.code_excerpt,
        "line_start": finding.line_start,
        "line_end": finding.line_end,
    }
    reason, project_path, external_id = (
        dispute.reason or "",
        project.path_with_namespace,
        project.external_id,
    )
    ref = review.reviewed_sha
    line_start, line_end = finding.line_start, finding.line_end
    sensitive_paths = list(settings.sensitive_paths)
    # A sensitive file (SENSITIVE_PATHS) is never read for this: the model sees the finding only.
    sensitive = path is not None and (
        is_ignored(path, sensitive_paths) or is_ignored(path.rsplit("/", 1)[-1], sensitive_paths)
    )
    session.commit()  # nothing open while GitLab and the model work

    source: str | None = None
    if connection is not None and path and not sensitive:
        try:
            with make_client(connection) as client:
                text = client.file_raw(external_id, path, ref)
            if len(text) <= MAX_FILE_BYTES and "\x00" not in text:
                text = mask_config(text) if is_config(path, sensitive_paths) else mask_code(text)
                source, _first = numbered_window(text, line_start, line_end)
        except GitLabError as exc:
            log.info(
                "dispute check: file not read",
                extra={"dispute_id": dispute_id, "status": exc.status_code},
            )
    outcome = analyse(
        provider,
        build_prompt(project=project_path, path=path, finding=facts, reason=reason, source=source),
    )
    cost = round(outcome.call.cost_usd, 4) if outcome.call is not None else None
    if outcome.call is not None:
        # Paid whatever happens next: it counts toward the month's budget.
        audit(
            session,
            requested_by,
            COST_ACTION,
            f"dispute:{dispute_id}",
            cost_usd=cost,
            model=outcome.call.model,
        )
        session.commit()
    paid = Decimal(str(cost)) if cost is not None else None
    if outcome.analysis is None:
        return _finish(
            session,
            dispute_id,
            token,
            "failed",
            ai_reason="Không phân tích được (lỗi khi hỏi model); thử lại sau."
            + (f" Chi tiết: {outcome.error[:200]}" if outcome.error else ""),
            ai_cost_usd=paid,
        )
    a = outcome.analysis
    return _finish(
        session,
        dispute_id,
        token,
        "done",
        ai_verdict=a.verdict,
        ai_confidence=Decimal(str(round(a.confidence, 2))),
        ai_reason=a.reason[:4000]
        + ("" if source else " (Không đọc được file ở commit đã review: chỉ dựa trên finding.)"),
        ai_evidence=f"{path}:{a.evidence_lines}" if path and a.evidence_lines else None,
        ai_model=outcome.call.model if outcome.call else None,
        ai_cost_usd=paid,
    )


def dispute_check_job(dispute_id: int, requested_at: str) -> str:
    settings = get_settings()
    token = datetime.fromisoformat(requested_at)
    with get_sessionmaker()() as session:
        tuned = with_overrides(session, settings)
        session.commit()
        try:
            return run_dispute_check(
                session,
                dispute_id,
                client_factory(secret_box()),
                build_provider(tuned),
                tuned,
                requested_at=token,
            )
        except Exception:
            log.exception("dispute check failed", extra={"dispute_id": dispute_id})
            _finish(
                session, dispute_id, token, "failed", ai_reason="Lỗi khi phân tích; thử lại sau."
            )
            raise
