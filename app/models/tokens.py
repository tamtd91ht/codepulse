"""Personal access tokens for the MCP server (docs/specs/26 §2; outside the original spec,
requested by the user 2026-11-05).

Only the SHA-256 of a token is stored; the token itself is shown once, when it is made.
A token works only on /mcp, with the scopes it was given, and never beyond its owner's
permissions (app/mcp/tokens.py).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ApiToken(TimestampMixin, Base):
    __tablename__ = "api_tokens"
    __table_args__ = (Index("ix_api_tokens_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str]
    # The first characters after "cpat_", shown in lists so a person can tell tokens apart.
    prefix: Mapped[str]
    token_hash: Mapped[str] = mapped_column(unique=True)
    # findings:read (always) and findings:close_request (app/mcp/tokens.py SCOPES).
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
