"""Application settings, read from environment variables (see docs/specs/13_DEPLOYMENT_OPS.md)."""

import os
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CommaList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    # CODEPULSE_ENV_FILE="" disables the file (tests); environment variables always win.
    model_config = SettingsConfigDict(
        env_file=os.environ.get("CODEPULSE_ENV_FILE", ".env") or None, extra="ignore"
    )

    database_url: PostgresDsn
    redis_url: RedisDsn
    app_encryption_key: SecretStr
    app_base_url: str = "http://localhost:8000"

    llm_provider: Literal["claude_code", "anthropic", "openai", "ollama"] = "claude_code"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    # Claude subscription session for LLM_PROVIDER=claude_code (`claude setup-token`); wins over
    # LLM_API_KEY, which stays for an Anthropic API key.
    claude_code_oauth_token: SecretStr = SecretStr("")
    claude_code_bin: str = "claude"
    monthly_llm_budget_usd: float = Field(default=200, ge=0)
    max_review_loc: int = Field(default=1500, gt=0)
    max_tokens_per_chunk: int = Field(default=12000, gt=0)
    min_confidence: float = Field(default=0.5, ge=0, le=1)
    # JSON, e.g. {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}; empty = rubric's.
    scoring_weights: dict[str, float] | None = None
    min_sample: int = Field(default=3, ge=1)

    backfill_days: int = Field(default=90, ge=0)
    sync_interval_min: int = Field(default=30, gt=0)
    ignore_paths: CommaList = ["*.lock", "dist/**", "vendor/**", "*.min.js", "**/generated/**"]
    sensitive_paths: CommaList = ["**/secrets/**", ".env*", "*.pem", "*.key"]
    allowed_email_domains: CommaList = []

    report_tz: str = "Asia/Ho_Chi_Minh"

    # Dashboard sign-in: GitLab as OAuth/OIDC provider (Phase 0 decision).
    dashboard_url: str = "http://localhost:3000"
    gitlab_oauth_url: str = ""  # e.g. https://gitlab.company.vn
    gitlab_oauth_client_id: str = ""
    gitlab_oauth_client_secret: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")  # signs session tokens; required for the API
    session_ttl_hours: int = Field(default=12, gt=0)
    admin_emails: CommaList = []
    # Optional allowlist for the webhook source (GitLab's IPs / CIDRs); empty = any address.
    webhook_allowed_ips: CommaList = []
    # Bearer token Prometheus must send to /metrics; empty = open (keep it on the internal network).
    metrics_token: SecretStr = SecretStr("")
    smtp_url: SecretStr = SecretStr("")  # smtp[s]://user:password@host:port
    smtp_from: str = "codepulse@company.vn"
    slack_webhook_url: SecretStr = SecretStr("")  # default team channel when none is configured
    # Per team (by name): {"Backend": {"slack": "https://hooks...", "teams": "https://...",
    # "emails": ["x@company.vn"]}}. Leads and managers are emailed automatically.
    report_channels: dict[str, dict[str, Any]] = {}
    retention_days: int = Field(default=365, gt=0)

    # Telegram bots (configured per dự án in Admin; tokens live in the DB, encrypted).
    telegram_api_base: str = "https://api.telegram.org"
    telegram_poll_interval_s: int = Field(default=5, ge=2, le=120)
    # Review/sync commands one person may send per 10 minutes (a guard, not a quota).
    telegram_commands_per_10min: int = Field(default=20, ge=1)
    # Commits one "today" / "open" command may queue (LLM budget guard).
    telegram_max_targets: int = Field(default=20, ge=1, le=200)

    @field_validator(
        "ignore_paths",
        "sensitive_paths",
        "allowed_email_domains",
        "admin_emails",
        "webhook_allowed_ips",
        mode="before",
    )
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
