"""LLM connection check for admins (outside the spec, requested by the user; see PROGRESS.md)."""

import subprocess
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import AdminPrincipal
from app.config import Settings, get_settings
from app.models import Review
from app.review.llm_check import check_llm
from app.review.providers.claude_code import Runner

router = APIRouter(tags=["admin"])


def get_llm_runner() -> Runner:
    """The subprocess runner for the CLI; overridden in tests."""
    return subprocess.run


def month_usage(session: Session, budget_usd: float) -> dict[str, Any]:
    """This month's reviews and spend, counted like GET /admin/llm-costs."""
    month = func.date_trunc("month", Review.started_at)
    cost, reviews = session.execute(
        select(func.coalesce(func.sum(Review.cost_usd), 0), func.count())
        .where(Review.cost_usd.is_not(None))
        .where(month == func.date_trunc("month", func.now()))
    ).one()
    cost_usd = float(cost or 0)
    return {
        "reviews": int(reviews),
        "cost_usd": cost_usd,
        "budget_usd": budget_usd,
        "budget_pct": round(cost_usd / budget_usd * 100, 1) if budget_usd > 0 else None,
    }


@router.post("/admin/llm-check")
def llm_check(
    principal: AdminPrincipal,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    runner: Annotated[Runner, Depends(get_llm_runner)],
) -> dict[str, Any]:
    """Configured provider/model/CLI, auth method and account, one live call, month usage.

    POST because it makes a real (tiny) LLM call that is billed. The token is never returned.
    """
    report = check_llm(settings, runner=runner)
    audit(
        session,
        principal.user_id,
        "llm.check",
        f"llm:{report.provider}",
        ok=report.status.ok,
        latency_ms=report.status.latency_ms,
    )
    session.commit()
    return {**report.to_dict(), "usage": month_usage(session, settings.monthly_llm_budget_usd)}
