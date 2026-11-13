"""Several Claude accounts: the default first, backups only while it fails (user 2026-11-09)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import LlmAccount, User, UserNotification
from app.ratelimit import RateLimiter
from app.review import llm_accounts as accts
from app.review.llm_recovery import is_llm_outage
from app.review.providers import Account, ClaudeCodeProvider, FailoverProvider, LLMError
from app.review.providers.account_errors import classify_account_error, reset_time
from tests.api.conftest import BOX
from tests.review.test_providers import PROMPT, SUCCESS, FakeRunner

NOW = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
MODEL = "claude-opus-5-5"
AUTH_401 = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 401,
        "result": "Failed to authenticate. API Error: 401 OAuth token has expired",
    }
)
LIMIT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "result": "Claude AI usage limit reached|1794214800",  # 2026-11-09 09:00 UTC
    }
)
OVERLOADED = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 529,
        "result": "Overloaded",
    }
)


# -- which errors belong to the account ----------------------------------------------------------


E = "claude reported an error: success"


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (f"{E} (HTTP 401): Failed to authenticate", "auth"),
        (f"{E}: OAuth token has expired. Please run /login", "auth"),
        (f"{E}: Claude AI usage limit reached|1762678800", "quota"),
        (f"{E}: You've hit your limit · resets 3pm", "quota"),
        (f"{E}: Your credit balance is too low", "quota"),
        (f"{E} (HTTP 429): rate_limit_error", "rate"),  # a burst: retried, short pause
        # The plan's usage limit also answers 429: its wording decides.
        (f"{E} (HTTP 429): 5-hour limit reached ∙ resets 3pm (Asia/Ho_Chi_Minh)", "quota"),
        (f"{E} (HTTP 429): rate_limit_error: Weekly limit reached", "quota"),
        (f"{E} (HTTP 429): You've reached your Opus limit", "quota"),
        (f"{E} (HTTP 429): Claude AI usage limit reached|1762678800", "quota"),
        (f"{E} (HTTP 429): rate_limit_error: exceeded your per-minute rate limit", "rate"),
        (f"{E} (HTTP 429): rate limit, resets in 30 seconds", "rate"),
        (f"{E} (HTTP 429): This request would exceed your rate limit", "rate"),
        (f"{E}: model: claude-opus-5-5 is not available on your plan", "model"),
        (f"{E} (HTTP 404): not_found_error: model: claude-x", "model"),
        (f"{E} (HTTP 529): Overloaded", None),
        # The causes of a wrapped error are read too.
        (f"claude failed after 1 attempts | {E} (HTTP 401): bad token", "auth"),
        # A crashed CLI's stderr may echo the diff: never read as the account's fault.
        ("claude exited 1: def shared_limiter(rate_limit): ... (HTTP 429)", None),
        ("claude exited 1: raise ValueError('model invalid') model not found", None),
        ("claude exited 137: Killed; quota of memory", None),
        ("claude exited 1 without a result: usage limit reached in the diff", None),
        ("timed out after 120s", None),
        ("claude returned non-JSON output", None),
    ],
)
def test_only_the_accounts_own_failures_move_a_call(message: str, kind: str | None) -> None:
    assert classify_account_error(message) == kind


def test_a_reset_time_in_the_message_is_read_and_sanity_checked() -> None:
    assert reset_time("usage limit reached|1794214800", NOW) == datetime(
        2026, 11, 9, 9, 0, tzinfo=UTC
    )
    assert reset_time("limit; resets at 2026-11-09T12:30:00Z", NOW) == datetime(
        2026, 11, 9, 12, 30, tzinfo=UTC
    )
    # "resets 3pm (Asia/Ho_Chi_Minh)": 15:00 in Hà Nội is 08:00 UTC, already past at NOW
    # (08:00 UTC): the next day.
    assert reset_time("5-hour limit reached ∙ resets 3pm (Asia/Ho_Chi_Minh)", NOW) == datetime(
        2026, 11, 10, 8, 0, tzinfo=UTC
    )
    assert reset_time("limit · resets 4:30pm (Asia/Ho_Chi_Minh)", NOW) == datetime(
        2026, 11, 9, 9, 30, tzinfo=UTC
    )
    assert reset_time("resets 3pm", NOW) is None  # no zone: the hour is ambiguous
    assert reset_time("usage limit reached|1000000000", NOW) is None  # in the past
    assert reset_time("rate limit", NOW) is None


def test_when_a_failed_account_is_tried_again() -> None:
    # Usage limit with a reset time: just after it, not earlier.
    at = accts.next_retry("quota", 1, 15, NOW, "usage limit reached|1794214800")
    assert at == datetime(2026, 11, 9, 9, 1, tzinfo=UTC)
    # Without one: the base, doubling, at most 8x.
    assert [accts.next_retry("quota", n, 15, NOW, "429") for n in (1, 2, 3, 4, 5)] == [
        NOW + timedelta(minutes=m) for m in (15, 30, 60, 120, 120)
    ]
    # A 429 that does not say which limit: one minute; back right after, it is the usage
    # limit (which answers 429 too) and backs off like one.
    assert [accts.next_retry("rate", n, 15, NOW, "429") for n in (1, 2, 3)] == [
        NOW + timedelta(minutes=m) for m in (1, 15, 30)
    ]
    # A failed login does not heal by itself: 8x the base.
    assert accts.next_retry("auth", 1, 15, NOW, "401") == NOW + timedelta(minutes=120)
    # A model the plan lacks: not by itself.
    assert accts.next_retry("model", 1, 15, NOW, "model not available") is None


def test_an_account_is_usable_when_ok_or_due() -> None:
    ok = Account(id=1, label="a", token="t")
    waiting = Account(
        id=2, label="b", token="t", state="quota", retry_at=NOW + timedelta(minutes=5)
    )
    due = Account(id=3, label="c", token="t", state="auth", retry_at=NOW - timedelta(seconds=1))
    blocked = Account(id=4, label="d", token="t", state="model", blocked_model=MODEL)
    assert ok.usable(NOW, MODEL) and due.usable(NOW, MODEL)
    assert not waiting.usable(NOW, MODEL) and not blocked.usable(NOW, MODEL)
    assert blocked.usable(NOW, "claude-sonnet-5-5")  # another model may be in the plan


# -- the failover provider ------------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.failures: list[tuple[int | None, str]] = []
        self.successes: list[int | None] = []

    def failed(self, account: Account, kind: str, text: str) -> None:
        self.failures.append((account.id, kind))

    def ok(self, account: Account, started: datetime) -> None:
        self.successes.append(account.id)


def failover(
    runner: FakeRunner, found: list[Account], rec: Recorder, now: datetime = NOW
) -> FailoverProvider:
    def make(token: str) -> ClaudeCodeProvider:
        return ClaudeCodeProvider(
            runner=runner, sleep=lambda s: None, max_per_minute=1e6, model=MODEL, auth_token=token
        )

    return FailoverProvider(
        accounts=lambda: found,
        make=make,
        model=MODEL,
        limiter=RateLimiter(1e6),
        on_failure=rec.failed,
        on_success=rec.ok,
        clock=lambda: now,
    )


DEFAULT = Account(id=1, label="Chính", token="sk-ant-oat01-default", is_default=True)
BACKUP = Account(id=2, label="Dự phòng", token="sk-ant-oat01-backup")


def tokens(runner: FakeRunner) -> list[str]:
    return [c["env"]["CLAUDE_CODE_OAUTH_TOKEN"] for c in runner.calls]


def test_the_default_answers_while_it_works() -> None:
    runner, rec = FakeRunner((0, json.dumps(SUCCESS), "")), Recorder()
    result = failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert tokens(runner) == ["sk-ant-oat01-default"]
    assert result.raw["llm_account"] == "Chính" and rec.successes == [1]
    assert "--model" in runner.calls[0]["cmd"] and MODEL in runner.calls[0]["cmd"]


@pytest.mark.parametrize(("failure", "kind"), [(AUTH_401, "auth"), (LIMIT, "quota")])
def test_an_account_failure_moves_to_the_backup_at_once(failure: str, kind: str) -> None:
    runner = FakeRunner((1, failure, ""), (0, json.dumps(SUCCESS), ""))
    rec = Recorder()
    result = failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    # One call on the default (no retries with backoff on a dead login or a used-up limit).
    assert tokens(runner) == ["sk-ant-oat01-default", "sk-ant-oat01-backup"]
    assert rec.failures == [(1, kind)] and rec.successes == [2]
    assert result.raw["llm_account"] == "Dự phòng" and result.raw["llm_account_id"] == 2


def test_an_error_that_is_not_the_accounts_never_switches() -> None:
    runner, rec = FakeRunner(*[(1, OVERLOADED, "")] * 4), Recorder()
    with pytest.raises(LLMError):
        failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert set(tokens(runner)) == {"sk-ant-oat01-default"}  # retried there, never on the backup
    assert rec.failures == []


def test_a_waiting_default_is_skipped_until_its_time_then_tried_first_again() -> None:
    waiting = Account(
        id=1,
        label="Chính",
        token="sk-ant-oat01-default",
        is_default=True,
        state="quota",
        retry_at=NOW + timedelta(minutes=10),
    )
    runner, rec = FakeRunner((0, json.dumps(SUCCESS), "")), Recorder()
    failover(runner, [waiting, BACKUP], rec).review(PROMPT)
    assert tokens(runner) == ["sk-ant-oat01-backup"]
    # Its time has come: the next call tries the default first, and it is back.
    later = NOW + timedelta(minutes=11)
    runner, rec = FakeRunner((0, json.dumps(SUCCESS), "")), Recorder()
    failover(runner, [waiting, BACKUP], rec, later).review(PROMPT)
    assert tokens(runner) == ["sk-ant-oat01-default"] and rec.successes == [1]


def test_with_every_account_down_the_review_waits_for_the_llm() -> None:
    runner = FakeRunner((1, AUTH_401, ""), (1, LIMIT, ""))
    rec = Recorder()
    with pytest.raises(LLMError) as failed:
        failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert rec.failures == [(1, "auth"), (2, "quota")]
    assert is_llm_outage(str(failed.value))  # "Tự review lại khi LLM hoạt động lại" takes it
    waiting = [
        Account(id=1, label="a", token="x", state="auth", retry_at=NOW + timedelta(hours=1)),
        Account(id=2, label="b", token="y", state="model", blocked_model=MODEL),
    ]
    with pytest.raises(LLMError) as none:
        failover(FakeRunner(), waiting, Recorder()).review(PROMPT)
    assert is_llm_outage(str(none.value)) and "until 09:00 UTC" in str(none.value)


def test_a_deep_review_moves_to_the_backup_too(tmp_path: Any) -> None:
    calls: list[str] = []

    def streamer(cmd: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"])
        payload = AUTH_401 if len(calls) == 1 else json.dumps(SUCCESS)
        kwargs["on_line"](payload)
        return 0, ""

    def make(token: str) -> ClaudeCodeProvider:
        return ClaudeCodeProvider(streamer=streamer, model=MODEL, auth_token=token)

    rec = Recorder()
    provider = FailoverProvider(
        accounts=lambda: [DEFAULT, BACKUP],
        make=make,
        model=MODEL,
        limiter=RateLimiter(1e6),
        on_failure=rec.failed,
        on_success=rec.ok,
        clock=lambda: NOW,
    )
    result = provider.review_in_repo(
        PROMPT, str(tmp_path), tools=["Read"], max_turns=3, timeout_s=5, on_event=lambda e: None
    )
    assert calls == ["sk-ant-oat01-default", "sk-ant-oat01-backup"]
    assert result.raw["llm_account"] == "Dự phòng" and rec.failures == [(1, "auth")]


def test_the_cli_is_not_retried_on_a_failed_login() -> None:
    runner = FakeRunner((1, AUTH_401, ""))
    provider = ClaudeCodeProvider(runner=runner, sleep=lambda s: None, max_per_minute=1e6)
    with pytest.raises(LLMError):
        provider.review(PROMPT)
    assert len(runner.calls) == 1


def test_the_environment_token_is_the_last_backup_and_the_cli_login_the_fallback() -> None:
    settings = Settings(
        claude_code_oauth_token=SecretStr("sk-ant-oat01-env"), llm_api_key=SecretStr("")
    )
    assert [a.label for a in accts.env_accounts(settings)] == [accts.ENV_LABEL]
    empty = Settings(claude_code_oauth_token=SecretStr(""), llm_api_key=SecretStr(""))
    assert accts.env_accounts(empty) == []


# -- stored accounts (database) -------------------------------------------------------------------


@pytest.fixture
def same_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def factory() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(accts, "get_sessionmaker", lambda: factory)
    monkeypatch.setattr(accts, "secret_box", lambda: BOX)
    return db_session


def add(
    session: Session,
    label: str,
    fill: str,
    *,
    source: str = "paste",
    user_id: int | None = None,
    family: str = "oat",
) -> LlmAccount:
    token = f"sk-ant-{family}01-" + fill * 30
    return accts.add_account(session, BOX, label=label, token=token, source=source, user_id=user_id)


def _admin(session: Session) -> User:
    user = User(full_name="Admin", primary_email="admin-llm@example.test")
    session.add(user)
    session.flush()
    return user


def test_the_first_account_is_the_default_and_the_next_ones_are_backups(
    db_session: Session,
) -> None:
    a = add(db_session, "A", "a", source="paste", user_id=None, family="oat")
    b = add(db_session, "B", "b", source="paste", user_id=None, family="oat")
    c = add(db_session, "C", "c", source="paste", user_id=None, family="api")
    assert [r.label for r in accts.account_rows(db_session)] == ["A", "B", "C"]
    assert a.is_default and not b.is_default and not c.is_default

    accts.reorder(db_session, [c.id, b.id])
    assert [r.label for r in accts.account_rows(db_session)] == ["A", "C", "B"]
    accts.set_default(db_session, b)
    assert [r.label for r in accts.account_rows(db_session)] == ["B", "A", "C"]
    accts.remove_account(db_session, b)
    assert [(r.label, r.is_default) for r in accts.account_rows(db_session)] == [
        ("A", True),
        ("C", False),
    ]
    with pytest.raises(ValueError):
        accts.add_account(
            db_session, BOX, label="x", token="not-a-token", source="paste", user_id=None
        )


def test_a_failure_is_recorded_once_told_and_cleared_by_the_next_success(
    same_session: Session,
) -> None:
    session = same_session
    admin = _admin(session)
    row = add(session, "Chính", "a", source="login", user_id=admin.id, family="oat")
    session.flush()
    account = accts.to_account(row, BOX)
    settings = Settings(llm_account_retry_min=15)

    accts.record_failure(account, "quota", "HTTP 429 rate limit", settings, MODEL)
    session.refresh(row)
    assert row.state == "quota" and row.failures == 1 and row.retry_at is not None
    first_retry = row.retry_at
    # Another worker that loaded the list before: the same failure, recorded once.
    accts.record_failure(account, "quota", "HTTP 429 rate limit", settings, MODEL)
    session.refresh(row)
    assert row.failures == 1 and row.retry_at == first_retry
    # Its time came, it was tried and failed again: backs off.
    row.retry_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    accts.record_failure(accts.to_account(row, BOX), "quota", "HTTP 429", settings, MODEL)
    session.refresh(row)
    assert row.failures == 2 and row.retry_at is not None
    assert row.retry_at > datetime.now(UTC) + timedelta(minutes=29)
    told = session.scalars(select(UserNotification).where(UserNotification.kind == "llm_account"))
    assert len(told.all()) == 1  # only when it went from working to failing

    # A long call that started before the failure does not clear it.
    accts.record_success(accts.to_account(row, BOX), datetime.now(UTC) - timedelta(hours=1))
    session.refresh(row)
    assert row.state == "quota"
    accts.record_success(accts.to_account(row, BOX))
    session.refresh(row)
    assert row.state == "ok" and row.failures == 0 and row.retry_at is None
    assert row.last_ok_at is not None


def test_the_order_calls_try_skips_disabled_accounts_and_ends_with_the_environment(
    same_session: Session,
) -> None:
    session = same_session
    a = add(session, "A", "a", source="paste", user_id=None, family="oat")
    b = add(session, "B", "b", source="paste", user_id=None, family="oat")
    b.enabled = False
    session.flush()
    settings = Settings(claude_code_oauth_token=SecretStr("sk-ant-oat01-env"))
    found = accts.load_accounts(settings)
    assert [x.label for x in found] == ["A", accts.ENV_LABEL]
    a.state, a.retry_at = "auth", datetime.now(UTC) + timedelta(hours=1)
    session.flush()
    assert accts.current_token(session, BOX, settings) == "sk-ant-oat01-env"
    assert session.scalar(select(LlmAccount.id).where(LlmAccount.is_default)) == a.id


def test_a_burst_rate_limit_is_retried_before_another_account_takes_over() -> None:
    burst = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 429,
            "result": "rate_limit_error",
        }
    )
    runner = FakeRunner((1, burst, ""), (0, json.dumps(SUCCESS), ""))
    rec = Recorder()
    result = failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert tokens(runner) == ["sk-ant-oat01-default"] * 2  # retried on the default
    assert rec.failures == [] and result.raw["llm_account"] == "Chính"


def test_a_crash_whose_stderr_mentions_limits_never_parks_an_account() -> None:
    crash = (1, "", "Traceback: shared_limiter(rate_limit) usage limit reached (HTTP 429)")
    runner, rec = FakeRunner(*[crash] * 4), Recorder()
    with pytest.raises(LLMError):
        failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert rec.failures == [] and set(tokens(runner)) == {"sk-ant-oat01-default"}


def test_a_429_that_does_not_say_which_limit_gets_one_retry_then_the_backup() -> None:
    unclear = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 429,
            "result": "This request would exceed your rate limit",
        }
    )
    runner = FakeRunner((1, unclear, ""), (1, unclear, ""), (0, json.dumps(SUCCESS), ""))
    rec = Recorder()
    result = failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert tokens(runner) == ["sk-ant-oat01-default"] * 2 + ["sk-ant-oat01-backup"]
    assert rec.failures == [(1, "rate")] and result.raw["llm_account"] == "Dự phòng"


def test_a_429_with_the_plans_limit_wording_moves_on_at_once() -> None:
    limit = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 429,
            "result": "5-hour limit reached ∙ resets 3pm (Asia/Ho_Chi_Minh)",
        }
    )
    runner = FakeRunner((1, limit, ""), (0, json.dumps(SUCCESS), ""))
    rec = Recorder()
    failover(runner, [DEFAULT, BACKUP], rec).review(PROMPT)
    assert tokens(runner) == ["sk-ant-oat01-default", "sk-ant-oat01-backup"]
    assert rec.failures == [(1, "quota")]


def test_a_429_back_right_after_its_pause_counts_as_the_usage_limit(
    same_session: Session,
) -> None:
    session = same_session
    admin = _admin(session)
    row = add(session, "Chính", "a", source="login", user_id=admin.id)
    settings = Settings(llm_account_retry_min=15)
    accts.record_failure(accts.to_account(row, BOX), "rate", "(HTTP 429)", settings, MODEL)
    session.refresh(row)
    assert row.failures == 1 and row.retry_at is not None
    assert row.retry_at < datetime.now(UTC) + timedelta(minutes=2)  # a short pause
    told = select(UserNotification).where(UserNotification.kind == "llm_account")
    assert session.scalars(told).all() == []  # maybe a burst: nobody told yet
    row.retry_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    accts.record_failure(accts.to_account(row, BOX), "rate", "(HTTP 429)", settings, MODEL)
    session.refresh(row)
    assert row.failures == 2 and row.state == "quota"
    assert row.retry_at is not None and row.retry_at > datetime.now(UTC) + timedelta(minutes=14)
    assert len(session.scalars(told).all()) == 1


def test_only_a_members_requests_go_to_the_queue_served_first() -> None:
    """User 2026-11-09: what a member asked for runs before automatic reviews; the scanning
    "Yêu cầu review" page (trigger "dashboard") is not a member's own request."""
    from redis import Redis
    from rq import Queue

    from app.workers.queues import ASKED_TRIGGERS, review_queue, worker_queues

    # Every lane of the asked tier before any lane of the rest (lanes: user 2026-11-13).
    served = worker_queues(["reviews"])
    tiers = [name.split(":")[0] for name in served]
    assert tiers == sorted(tiers, key=lambda t: t != "reviews_asked")
    assert served[0] == "reviews_asked:0" and served[-1] == "reviews"
    assert worker_queues(["default", "reviews"])[0] == "default"
    assert worker_queues(["commands"]) == ["commands"]
    base = Queue("default", connection=Redis())  # never connects: names only
    assert getattr(review_queue(base, asked=True), "name", "").startswith("reviews_asked:")
    assert getattr(review_queue(base), "name", "").startswith("reviews:")
    assert {"telegram", "rerun", "mcp"} == ASKED_TRIGGERS and "dashboard" not in ASKED_TRIGGERS
