"""Build a repo's AI component grouping (docs/specs/20 §4.9b, app/insights/components.py),
queued by an admin (POST /admin/insights/components/{project_id}). The model sees paths and the
automatic components only, never source. No transaction stays open while the model works; the
cost is audited as `insights.components.cost` and counts toward the month's LLM budget."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.admin_settings import monthly_budget_usd, with_overrides
from app.api.audit import audit
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.insights.components import auto_map, build_prompt, regroup
from app.insights.load import repo_paths
from app.models import Project, RepoComponentMap
from app.review.providers import LLMProvider, build_provider

log = logging.getLogger(__name__)

COST_ACTION = "insights.components.cost"
STALE = timedelta(minutes=20)
ACTIVE = ("queued", "running")


def running(row: RepoComponentMap | None, now: datetime | None = None) -> bool:
    at = row.requested_at if row is not None else None
    return (
        row is not None
        and row.status in ACTIVE
        and at is not None
        and (now or datetime.now(UTC)) - at < STALE
    )


def _finish(session: Session, project_id: int, token: datetime, status: str, **values: Any) -> str:
    """Only while this run is still the repo's current one."""
    session.rollback()
    moved = session.execute(
        update(RepoComponentMap)
        .where(
            RepoComponentMap.project_id == project_id,
            RepoComponentMap.requested_at == token,
            RepoComponentMap.status.in_(ACTIVE),
        )
        .values(status=status, **values)
    ).rowcount  # type: ignore[attr-defined]
    session.commit()
    return status if moved else "superseded"


def run_component_map(
    session: Session,
    project_id: int,
    provider: LLMProvider,
    settings: Settings,
    requested_at: datetime | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    row = session.get(RepoComponentMap, project_id)
    if row is None or row.requested_at is None:
        return "not mine"
    token = requested_at or row.requested_at
    claimed = session.execute(
        update(RepoComponentMap)
        .where(
            RepoComponentMap.project_id == project_id,
            RepoComponentMap.status == "queued",
            RepoComponentMap.requested_at == token,
        )
        .values(status="running")
    ).rowcount  # type: ignore[attr-defined]
    session.commit()
    if not claimed:
        return "not mine"
    session.refresh(row)
    requested_by = row.requested_by
    project = session.get(Project, project_id)
    if project is None:
        return _finish(session, project_id, token, "failed", error="Repo không còn.")
    if project.allow_external_llm is False:
        return _finish(
            session, project_id, token, "failed", error="Repo này không cho dùng LLM bên ngoài."
        )
    budget = monthly_budget_usd(session, settings)
    if budget > 0:
        from app.workers.knowledge_jobs import month_spend_usd  # lazy: heavy module

        if month_spend_usd(session, clock()) >= budget:
            return _finish(
                session, project_id, token, "failed", error="Đã dùng hết ngân sách LLM tháng này."
            )
    paths = repo_paths(session, project_id)
    if not paths:
        return _finish(
            session,
            project_id,
            token,
            "failed",
            error="Repo chưa có dữ liệu file (commit_files): chạy Đồng bộ lại cho Insights trước.",
        )
    auto = auto_map(paths, project.path_with_namespace.rsplit("/", 1)[-1])
    prompt = build_prompt(project.path_with_namespace, auto, project.profile)
    session.commit()  # nothing open while the model works

    outcome = regroup(provider, prompt, auto)
    cost = round(outcome.call.cost_usd, 4) if outcome.call is not None else None
    if outcome.call is not None:
        audit(
            session,
            requested_by,
            COST_ACTION,
            f"project:{project_id}",
            cost_usd=cost,
            model=outcome.call.model,
        )
        session.commit()
    if outcome.grouping is None:
        return _finish(
            session,
            project_id,
            token,
            "failed",
            error="Không phân tích được (lỗi khi hỏi model); thử lại sau."
            + (f" Chi tiết: {outcome.error[:200]}" if outcome.error else ""),
            cost_usd=cost,
        )
    return _finish(
        session,
        project_id,
        token,
        "done",
        groups=[g.model_dump() for g in outcome.grouping.groups],
        confidence=round(outcome.grouping.confidence, 2),
        paths_seen=len(paths),
        model=outcome.call.model if outcome.call else None,
        cost_usd=cost,
        error=None,
        built_at=datetime.now(UTC),
    )


def component_map_job(project_id: int, requested_at: str) -> str:
    settings = get_settings()
    token = datetime.fromisoformat(requested_at)
    with get_sessionmaker()() as session:
        tuned = with_overrides(session, settings)
        session.commit()
        try:
            return run_component_map(
                session, project_id, build_provider(tuned), tuned, requested_at=token
            )
        except Exception:
            log.exception("component map failed", extra={"project_id": project_id})
            _finish(session, project_id, token, "failed", error="Lỗi khi phân tích; thử lại sau.")
            raise
