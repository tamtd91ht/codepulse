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
    env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-secret")
    assert "sk-secret" not in repr(Settings())
    assert "sk-ant-oat01-secret" not in repr(Settings())


def test_missing_database_url_fails(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")
    with pytest.raises(ValidationError):
        Settings()


def test_review_flow_v2_defaults_and_deep_review_aliases(env: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert (settings.review_agent, settings.review_agent_min_loc) == (True, 20)
    assert (settings.review_max_turns, settings.review_timeout_s) == (40, 480)
    assert settings.review_chunk_tokens == 60_000
    assert (settings.verify, settings.verify_max_findings, settings.verify_max_turns) == (
        True,
        12,
        15,
    )
    assert settings.verify_min_confidence_unverified == 0.7
    # The DEEP_REVIEW* names of docs/specs/18 still work (.env files in the field).
    env.setenv("DEEP_REVIEW", "false")
    env.setenv("DEEP_REVIEW_MIN_LOC", "5")
    env.setenv("DEEP_REVIEW_MAX_TURNS", "25")
    env.setenv("REVIEW_TIMEOUT_S", "300")
    old = Settings()
    assert (old.review_agent, old.review_agent_min_loc) == (False, 5)
    assert (old.review_max_turns, old.review_timeout_s) == (25, 300)


def test_unknown_provider_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValidationError):
        Settings()
