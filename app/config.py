"""Application settings, read from environment variables (see docs/specs/13_DEPLOYMENT_OPS.md)."""

import os
from functools import lru_cache
from typing import Annotated, Literal

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
    claude_code_bin: str = "claude"
    monthly_llm_budget_usd: float = Field(default=200, ge=0)
    max_review_loc: int = Field(default=1500, gt=0)
    max_tokens_per_chunk: int = Field(default=12000, gt=0)
    min_confidence: float = Field(default=0.5, ge=0, le=1)

    backfill_days: int = Field(default=90, ge=0)
    sync_interval_min: int = Field(default=30, gt=0)
    ignore_paths: CommaList = ["*.lock", "dist/**", "vendor/**", "*.min.js", "**/generated/**"]
    sensitive_paths: CommaList = ["**/secrets/**", ".env*", "*.pem", "*.key"]
    allowed_email_domains: CommaList = []

    report_tz: str = "Asia/Ho_Chi_Minh"
    smtp_url: str = ""
    slack_webhook_url: SecretStr = SecretStr("")
    retention_days: int = Field(default=365, gt=0)

    @field_validator("ignore_paths", "sensitive_paths", "allowed_email_domains", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
