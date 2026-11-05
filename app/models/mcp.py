"""One row per MCP tool call (docs/specs/26 §13.1): who called which tool, whether it worked and
what it touched, for the "Tích hợp MCP" report (app/api/routers/mcp_report.py).

Never the result, the free-text arguments (note, argument) or the token. Kept MCP_CALLS_KEEP_DAYS
(app/retention.py).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class McpCall(Base):
    __tablename__ = "mcp_calls"
    __table_args__ = (
        Index("ix_mcp_calls_created_at", "created_at"),
        Index("ix_mcp_calls_user_id_created_at", "user_id", "created_at"),
        Index("ix_mcp_calls_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    token_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("api_tokens.id", ondelete="SET NULL")
    )
    tool: Mapped[str]
    ok: Mapped[bool]
    # None when ok; else the tool error code (commit_not_pushed, rate_limited, forbidden, …).
    error_code: Mapped[str | None]
    duration_ms: Mapped[int]
    # What the call touched, when the tool knows it (no foreign keys: a log outlives its rows).
    project_id: Mapped[int | None]
    finding_id: Mapped[int | None] = mapped_column(BigInteger)
    closure_id: Mapped[int | None] = mapped_column(BigInteger)
    commit_sha: Mapped[str | None]
    # Read tools: how many findings (or close requests) the result held.
    result_count: Mapped[int | None]
    client: Mapped[str | None]  # "name version" of the MCP client, when it said
