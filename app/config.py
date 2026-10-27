"""Application settings, read from environment variables (see docs/specs/13_DEPLOYMENT_OPS.md)."""

import os
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, PostgresDsn, RedisDsn, SecretStr, field_validator
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
    # How much of the .claude configuration (rules, skills, agents, CLAUDE.md, review commands)
    # reaches the model: per file, and in total for the first pass and the deep pass. Also
    # editable on Admin > Cài đặt review (app/admin_settings.py).
    claude_file_chars: int = Field(default=60_000, ge=2_000, le=500_000)
    fast_guidance_chars: int = Field(default=120_000, ge=5_000, le=1_000_000)
    deep_instructions_chars: int = Field(default=300_000, ge=10_000, le=2_000_000)
    # 0.4 since rubric v3, which asks for honest confidence instead of hedged severity.
    min_confidence: float = Field(default=0.4, ge=0, le=1)
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

    # Review flow v2 (docs/specs/19 §4, §8): the main pass runs with read-only tools inside a
    # copy of the repository (REVIEW_AGENT); below REVIEW_AGENT_MIN_LOC only the no-tool pass
    # runs. The DEEP_REVIEW* names of docs/specs/18 still work as aliases.
    review_agent: bool = Field(
        default=True, validation_alias=AliasChoices("REVIEW_AGENT", "DEEP_REVIEW")
    )
    review_agent_min_loc: int = Field(
        default=20,
        ge=0,
        validation_alias=AliasChoices("REVIEW_AGENT_MIN_LOC", "DEEP_REVIEW_MIN_LOC"),
    )
    review_max_turns: int = Field(
        default=40,
        ge=5,
        le=200,
        validation_alias=AliasChoices("REVIEW_MAX_TURNS", "DEEP_REVIEW_MAX_TURNS"),
    )
    review_timeout_s: int = Field(
        default=480,
        ge=60,
        le=3600,
        validation_alias=AliasChoices("REVIEW_TIMEOUT_S", "DEEP_REVIEW_TIMEOUT_S"),
    )
    review_chunk_tokens: int = Field(default=60_000, ge=4_000)  # chunk only above this
    deep_review_max_archive_mb: int = Field(default=300, ge=10, le=5000)
    # [2] Independent verification of findings (docs/specs/19 §4.3).
    verify: bool = True
    verify_max_findings: int = Field(default=12, ge=0, le=100)
    verify_max_turns: int = Field(default=15, ge=1, le=100)
    # An unverified finding is scored only from this confidence (docs/specs/19 §4.4).
    verify_min_confidence_unverified: float = Field(default=0.7, ge=0, le=1)
    profile_ttl_days: int = Field(default=7, ge=1, le=90)

    # Review knowledge (docs/specs/19 §8): the code probe's turns and timeout, the cost cap of
    # one build, and how many tokens of the compiled premises one review prompt may hold.
    knowledge_max_turns: int = Field(default=40, ge=5, le=200)
    knowledge_timeout_s: int = Field(default=300, ge=30, le=3600)
    # One compile call reads up to 400k chars and writes a large JSON: far longer than a
    # review call. Retried once (a compile is paid work).
    knowledge_compile_timeout_s: int = Field(default=900, ge=60, le=3600)
    knowledge_max_cost_usd: float = Field(default=5.0, ge=0)
    knowledge_tokens: int = Field(default=12000, ge=1000, le=100_000)

    # Dashboard "Yêu cầu review": commits listed per request, and queued per request.
    review_commits_list_max: int = Field(default=200, ge=20, le=500)
    review_batch_max: int = Field(default=50, ge=1, le=200)

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
