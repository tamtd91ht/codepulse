from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpCall, RawGitlab, Review, ReviewFinding
from app.retention import MCP_CALLS_KEEP_DAYS, purge

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 22, tzinfo=UTC)


def test_purge_drops_old_excerpts_and_raw_but_keeps_history(db_session: Session) -> None:
    s = db_session
    old = Review(
        target_type="commit",
        reviewed_sha="old",
        status="done",
        finished_at=NOW - timedelta(days=400),
    )
    recent = Review(
        target_type="commit",
        reviewed_sha="new",
        status="done",
        finished_at=NOW - timedelta(days=10),
    )
    s.add_all([old, recent])
    s.flush()
    old_finding = ReviewFinding(
        review_id=old.id, severity="high", category="bug", code_excerpt="+x = 1"
    )
    new_finding = ReviewFinding(
        review_id=recent.id, severity="high", category="bug", code_excerpt="+y = 2"
    )
    s.add_all(
        [
            old_finding,
            new_finding,
            RawGitlab(endpoint="commit", payload={}, fetched_at=NOW - timedelta(days=400)),
            RawGitlab(endpoint="commit", payload={}, fetched_at=NOW - timedelta(days=1)),
        ]
    )
    s.flush()

    result = purge(s, retention_days=365, now=NOW)

    assert (result.excerpts_cleared, result.raw_rows_deleted) == (1, 1)
    s.refresh(old_finding)
    s.refresh(new_finding)
    assert (old_finding.code_excerpt, new_finding.code_excerpt) == (None, "+y = 2")
    assert old_finding.title is None and old_finding.severity == "high"  # the finding itself stays
    assert s.scalar(select(func.count()).select_from(Review)) == 2
    assert s.scalar(select(func.count()).select_from(RawGitlab)) == 1


def test_purge_drops_mcp_calls_after_180_days(db_session: Session) -> None:
    """docs/specs/26 §13.1: the MCP call log is kept MCP_CALLS_KEEP_DAYS (180) days."""
    s = db_session
    assert MCP_CALLS_KEEP_DAYS == 180

    def call(days: int) -> McpCall:
        return McpCall(
            created_at=NOW - timedelta(days=days), tool="list_findings", ok=True, duration_ms=3
        )

    s.add_all([call(181), call(179), call(1)])
    s.flush()

    result = purge(s, retention_days=365, now=NOW)

    assert result.mcp_calls_deleted == 1
    kept = sorted(s.scalars(select(McpCall.created_at)))
    assert kept == [NOW - timedelta(days=179), NOW - timedelta(days=1)]
