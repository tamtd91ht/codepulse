"""Signed download links of an MR review's analysis (app/telegram/mr_report.py)."""

from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from app.config import Settings
from app.telegram.mr_report import link_token, link_url, read_token

NOW = datetime(2026, 11, 2, 9, tzinfo=UTC)


def settings(secret: str = "s" * 48, hours: int = 24) -> Settings:
    return Settings(
        session_secret=SecretStr(secret),
        dashboard_url="https://dash.example.com/",
        telegram_report_link_hours=hours,
    )


def test_a_link_names_one_review_until_it_expires() -> None:
    s = settings()
    url = link_url(s, 42, NOW)
    assert url is not None and url.startswith("https://dash.example.com/api/v1/review-reports/42.")
    token = url.rsplit("/", 1)[1]
    review_id, expires, why = read_token(s, token, NOW + timedelta(hours=23))
    assert (review_id, why) == (42, "") and expires == NOW + timedelta(hours=24)
    assert read_token(s, token, NOW + timedelta(hours=25)) == (None, None, "expired")
    assert len(token) < 50  # short enough for a chat message


def test_a_changed_or_foreign_token_is_refused() -> None:
    s = settings()
    token = link_token(s, 42, NOW)
    assert token is not None
    rid, exp, sig = token.split(".")
    # Another review, a later expiry, another secret: the signature no longer matches.
    assert read_token(s, f"43.{exp}.{sig}", NOW)[2] == "invalid"
    assert read_token(s, f"{rid}.{int(exp) + 3600}.{sig}", NOW)[2] == "invalid"
    assert read_token(settings("t" * 48), token, NOW)[2] == "invalid"
    for junk in (
        "",
        "42",
        "a.b.c",
        "42.1.",
        "42.x.y",
        f"{rid}.{exp}.\u00e9",  # non-ASCII signature: refused, not a 500
        f"\u00b2.{exp}.{sig}",  # a Unicode digit
    ):
        assert read_token(s, junk, NOW)[2] == "invalid"


def test_no_secret_no_link() -> None:
    assert link_url(settings(secret="short"), 42, NOW) is None
