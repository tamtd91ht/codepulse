import pytest
from pydantic import ValidationError

from app.config import Settings

REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/db",
    "REDIS_URL": "redis://localhost:6379/0",
    "APP_ENCRYPTION_KEY": "k",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_defaults_match_spec(env: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert settings.llm_provider == "claude_code"
    assert settings.max_review_loc == 1500
    assert settings.report_tz == "Asia/Ho_Chi_Minh"
    assert "*.pem" in settings.sensitive_paths


def test_comma_lists_are_split(env: pytest.MonkeyPatch) -> None:
    env.setenv("IGNORE_PATHS", "*.lock, dist/** ,")
    env.setenv("ALLOWED_EMAIL_DOMAINS", "company.vn,vihat.vn")
    settings = Settings()
    assert settings.ignore_paths == ["*.lock", "dist/**"]
    assert settings.allowed_email_domains == ["company.vn", "vihat.vn"]


def test_secrets_are_masked(env: pytest.MonkeyPatch) -> None:
    env.setenv("LLM_API_KEY", "sk-secret")
    assert "sk-secret" not in repr(Settings())


def test_missing_database_url_fails(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")
    with pytest.raises(ValidationError):
        Settings()


def test_unknown_provider_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValidationError):
        Settings()
