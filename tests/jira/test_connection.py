"""The Jira connection set on the web (docs/specs/27 §2, §3): precedence over JIRA_*, the token
encrypted at rest, the checks, and the scheduler's tick."""

import logging
import re
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import SecretBox
from app.jira.client import JiraClient
from app.jira.settings import (
    TOKEN_SECRET,
    check_ca_pem,
    clear_connection,
    connection,
    jira_configured,
    normalize_base_url,
    parse_connection,
    save_connection,
    save_token,
    task_source,
)
from app.jira.sync import DUE_KEY, JOB_ID, JOB_TIMEOUT, sync_all, tick
from app.models import AppSetting, Issue, JiraProject, StoredSecret
from tests.jira.test_sync import FakeJira, jira_issue

pytestmark = pytest.mark.integration

BOX = SecretBox(Fernet.generate_key().decode())
WEB_TOKEN = "pat-" + "web-" + "secret"
ENV_TOKEN = "pat-" + "env-" + "secret"


def settings(**extra: Any) -> Settings:
    values: dict[str, Any] = {
        "jira_base_url": "https://env.jira.vn/",
        "jira_token": SecretStr(ENV_TOKEN),
        "jira_sync_interval_min": 15,
        "allowed_email_domains": ["x.vn"],
        **extra,
    }
    return Settings(**values)


def ca_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA nội bộ")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_env_only_and_nothing(db_session: Session) -> None:
    conn = connection(db_session, settings(), BOX)
    assert conn.base_url == "https://env.jira.vn" and conn.token == ENV_TOKEN
    assert (conn.base_url_source, conn.token_source, conn.ca_source) == ("env", "env", None)
    assert conn.interval_min == 15 and conn.enabled and conn.configured
    assert conn.sources == {
        "interval_min": "env",
        "backfill_days": "env",
        "timeout_s": "env",
        "enabled": "env",
    }
    assert conn.verify() is True
    none = connection(db_session, settings(jira_base_url="", jira_token=SecretStr("")), BOX)
    assert not none.configured and none.base_url_source is None and none.token_source is None
    assert none.as_json()["env"] == {
        "base_url": False,
        "token": False,
        "ca_bundle": False,
        "auth_type": False,
        "username": False,
    }


def test_web_wins_over_env(db_session: Session) -> None:
    pem = ca_pem()
    values = parse_connection(
        {
            "base_url": "jira.congty.vn/jira/",
            "ca_pem": pem,
            "interval_min": 5,
            "backfill_days": 30,
            "timeout_s": 7,
            "enabled": True,
        }
    )
    save_connection(db_session, values, None)
    save_token(db_session, BOX, WEB_TOKEN, None, "https://jira.congty.vn/jira")
    conn = connection(db_session, settings(jira_ca_bundle="/etc/ca.pem"), BOX)
    assert conn.base_url == "https://jira.congty.vn/jira" and conn.token == WEB_TOKEN
    assert (conn.base_url_source, conn.token_source, conn.ca_source) == ("web", "web", "web")
    assert (conn.interval_min, conn.backfill_days, conn.timeout_s) == (5, 30, 7.0)
    assert set(conn.sources.values()) == {"web"}
    assert conn.token_updated_at is not None
    assert isinstance(conn.verify(), ssl.SSLContext)
    assert conn.as_json()["env"] == {
        "base_url": True,
        "token": True,
        "ca_bundle": True,
        "auth_type": False,
        "username": False,
    }
    # Neither the token nor the PEM leaves: not in the JSON, not in the repr.
    for text in (str(conn.as_json()), repr(conn)):
        assert WEB_TOKEN not in text and "BEGIN CERTIFICATE" not in text
    # A number cleared: its variable applies again; the integration switched off.
    save_connection(db_session, parse_connection({"interval_min": None, "enabled": False}), None)
    off = connection(db_session, settings(), BOX)
    assert off.interval_min == 15 and off.sources["interval_min"] == "env"
    assert not off.enabled and not off.configured and off.reachable
    assert not jira_configured(db_session, settings(), BOX)
    assert task_source(db_session, settings()) == "gitlab"  # off: GitLab by default
    # Back to the environment.
    assert clear_connection(db_session)
    back = connection(db_session, settings(), BOX)
    assert back.base_url == "https://env.jira.vn" and back.token == ENV_TOKEN and back.enabled
    assert not clear_connection(db_session)


def test_token_encrypted_at_rest(db_session: Session, caplog: pytest.LogCaptureFixture) -> None:
    save_token(db_session, BOX, "  " + WEB_TOKEN + "\n", None, "https://env.jira.vn")
    row = db_session.get(StoredSecret, TOKEN_SECRET)
    assert row is not None and row.source == "paste"
    assert WEB_TOKEN not in row.value_encrypted
    assert BOX.decrypt(row.value_encrypted) == WEB_TOKEN
    # Another key cannot read it: as if none was saved (JIRA_TOKEN applies to its own server),
    # and the Admin screen says so; the warning is logged once per process, not every tick.
    import app.jira.settings as jira_settings

    jira_settings._warned_unreadable = False
    other = SecretBox(Fernet.generate_key().decode())
    with caplog.at_level(logging.WARNING, logger="app.jira.settings"):
        for _ in range(3):
            conn = connection(db_session, settings(), other)
    assert conn.token == ENV_TOKEN and conn.token_source == "env"
    assert conn.token_unreadable and conn.as_json()["token_unreadable"] is True
    assert caplog.text.count("cannot be decrypted") == 1
    assert WEB_TOKEN not in caplog.text
    jira_settings._warned_unreadable = False


def test_web_only_configures_jira(db_session: Session) -> None:
    bare = settings(jira_base_url="", jira_token=SecretStr(""))
    assert not jira_configured(db_session, bare, BOX)
    save_connection(db_session, parse_connection({"base_url": "https://jira.congty.vn"}), None)
    assert not jira_configured(db_session, bare, BOX)  # no token yet
    save_token(db_session, BOX, WEB_TOKEN, None, "https://jira.congty.vn")
    assert jira_configured(db_session, bare, BOX)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"base_url": "ftp://jira.vn"}, "http(s)"),
        ({"base_url": "https://user:pw@jira.vn"}, "tài khoản"),
        ({"base_url": "https://jira.vn/?a=1"}, "?"),
        ({"base_url": "https://jira.vn:abc"}, "cổng"),
        ({"base_url": "https://jira.vn/" + "a" * 300}, "300"),
        ({"base_url": "http://localhost:8080"}, "localhost"),
        ({"base_url": "https://jira.localhost"}, "localhost"),
        ({"base_url": "http://127.0.0.5"}, "loopback"),
        ({"base_url": "http://[::1]:8080"}, "loopback"),
        ({"base_url": "http://[::ffff:127.0.0.1]"}, "loopback"),
        ({"base_url": "http://169.254.169.254/latest"}, "link-local"),
        ({"base_url": "http://[fe80::1]"}, "link-local"),
        ({"ca_pem": "not a pem"}, "PEM"),
        ({"ca_pem": "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----"}, "PEM"),
        ({"ca_pem": "-----BEGIN CERTIFICATE-----" + "a" * 100_001}, "100 KB"),
        ({"interval_min": 0}, "1 đến 1440"),
        ({"interval_min": 1441}, "1 đến 1440"),
        ({"backfill_days": 3651}, "1 đến 3650"),
        ({"timeout_s": 301}, "1 đến 300"),
        ({"timeout_s": 1.5}, "số nguyên"),
        ({"enabled": "yes"}, "true"),
    ],
)
def test_checks(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        parse_connection(raw)


def test_normalize_and_ca() -> None:
    assert normalize_base_url(" jira.congty.vn:8443/ ") == "https://jira.congty.vn:8443"
    assert normalize_base_url("http://10.0.0.5/jira") == "http://10.0.0.5/jira"
    pem = ca_pem()
    assert check_ca_pem(pem + pem).count("BEGIN CERTIFICATE") == 2
    assert parse_connection({"base_url": "", "ca_pem": "  "}) == {"base_url": None, "ca_pem": None}


def test_client_from_connection_uses_the_saved_ca(db_session: Session) -> None:
    save_connection(db_session, parse_connection({"ca_pem": ca_pem(), "timeout_s": 9}), None)
    conn = connection(db_session, settings(), BOX)
    with JiraClient.from_connection(conn) as client:
        assert client._http.timeout.read == 9.0
        assert str(client._http.base_url) == "https://env.jira.vn/rest/api/2/"
        assert client._http.headers["Authorization"] == f"Bearer {ENV_TOKEN}"


def test_sync_uses_the_connection_in_effect(db_session: Session) -> None:
    """Nothing in JIRA_*: the sync runs with what is saved on the web, and the issue links
    point at the saved URL."""
    bare = settings(jira_base_url="", jira_token=SecretStr(""))
    fake = FakeJira({"ABC": [jira_issue(1, "ABC-1")]})
    db_session.add(JiraProject(key="ABC", name="Abc", enabled=True))
    db_session.commit()
    assert sync_all(db_session, bare, fake) == {"skipped": "not_configured"}
    save_connection(
        db_session,
        parse_connection({"base_url": "https://web.jira.vn", "backfill_days": 3}),
        None,
    )
    # The default box (APP_ENCRYPTION_KEY of the test environment) reads it back.
    from app.crypto import secret_box

    save_token(db_session, secret_box(), WEB_TOKEN, None, "https://web.jira.vn")
    db_session.commit()
    out = sync_all(db_session, bare, fake)
    assert out["projects"] == {"ABC": {"issues": 1}}
    assert "updated >= -4320m" in fake.queries[0]  # 3 days back, the saved backfill
    from app.jira.settings import JIRA_ID_OFFSET

    issue = db_session.get(Issue, JIRA_ID_OFFSET + 1)
    assert issue is not None and issue.web_url == "https://web.jira.vn/browse/ABC-1"


class FakeRedis:
    def __init__(self) -> None:
        self.ttls: dict[str, int] = {}

    def set(self, name: str, value: Any, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and name in self.ttls:
            return False
        self.ttls[name] = ex or -1
        return True

    def ttl(self, name: str) -> int:
        return self.ttls.get(name, -2)

    def expire(self, name: str, time: int) -> bool:
        self.ttls[name] = time
        return True

    def delete(self, *names: str) -> int:
        return sum(self.ttls.pop(n, None) is not None for n in names)

    def pass_time(self, seconds: int) -> None:
        self.ttls = {k: t - seconds for k, t in self.ttls.items() if t - seconds > 0}


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((f, kwargs))


def test_tick_queues_once_per_interval(db_session: Session) -> None:
    redis, queue = FakeRedis(), FakeQueue()
    bare = settings(jira_base_url="", jira_token=SecretStr(""))
    assert tick(db_session, bare, redis, queue) == "not configured"
    assert queue.jobs == [] and redis.ttls == {}
    env = settings()  # every 15 minutes
    assert tick(db_session, env, redis, queue) == "queued"
    assert queue.jobs == [
        ("app.jira.sync.sync_all_job", {"job_id": JOB_ID, "job_timeout": JOB_TIMEOUT})
    ]
    assert redis.ttls[DUE_KEY] == 15 * 60
    for _ in range(14):
        redis.pass_time(60)
        assert tick(db_session, env, redis, queue) == "not due"
    redis.pass_time(60)
    assert tick(db_session, env, redis, queue) == "queued"
    assert len(queue.jobs) == 2
    # A shorter interval saved on the web: the current wait is cut down to it.
    save_connection(db_session, parse_connection({"interval_min": 2}), None)
    assert tick(db_session, env, redis, queue) == "not due"
    assert redis.ttls[DUE_KEY] == 2 * 60
    redis.pass_time(120)
    assert tick(db_session, env, redis, queue) == "queued"
    # Switched off on the web: nothing is queued any more.
    save_connection(db_session, parse_connection({"enabled": False}), None)
    redis.pass_time(600)
    assert tick(db_session, env, redis, queue) == "not configured"
    assert len(queue.jobs) == 3


@pytest.mark.parametrize(
    "token",
    ["pat with space", "pat-" + "\u0111", "pat-\x7f", "a" * 1001, ""],
)
def test_token_must_be_printable_ascii(token: str) -> None:
    from app.jira.settings import check_token

    with pytest.raises(ValueError, match="ASCII"):
        check_token(token)


def test_token_only_goes_to_its_own_server(db_session: Session) -> None:
    """§2: the web token to the origin it was saved for, JIRA_TOKEN to JIRA_BASE_URL's."""
    # JIRA_TOKEN without JIRA_BASE_URL: never used with a URL saved on the web.
    env_token_only = settings(jira_base_url="")
    save_connection(db_session, parse_connection({"base_url": "https://web.jira.vn"}), None)
    conn = connection(db_session, env_token_only, BOX)
    assert conn.token == "" and conn.token_other_server and not conn.configured
    # A web URL of another server than JIRA_BASE_URL: JIRA_TOKEN is not sent there.
    conn = connection(db_session, settings(), BOX)
    assert conn.base_url == "https://web.jira.vn" and conn.token == ""
    assert conn.token_other_server and conn.as_json()["token_other_server"] is True
    # Same server as JIRA_BASE_URL (another path, default port spelled out): it is.
    save_connection(
        db_session, parse_connection({"base_url": "https://ENV.jira.vn:443/jira"}), None
    )
    assert connection(db_session, settings(), BOX).token == ENV_TOKEN
    # The web token belongs to the server it was saved for.
    save_token(db_session, BOX, WEB_TOKEN, None, "https://env.jira.vn/jira")
    assert connection(db_session, settings(), BOX).token == WEB_TOKEN
    save_connection(db_session, parse_connection({"base_url": "https://evil.vn"}), None)
    moved = connection(db_session, settings(), BOX)
    assert moved.token == "" and moved.token_source is None and moved.token_other_server
    # A URL tested without saving it gets the same rule.
    assert connection(db_session, settings(), BOX, base_url="https://env.jira.vn").token == (
        WEB_TOKEN
    )
    assert connection(db_session, settings(), BOX, base_url="http://env.jira.vn").token == ""
    # A token cannot be saved before there is a URL to bind it to.
    with pytest.raises(ValueError, match="URL"):
        save_token(db_session, BOX, WEB_TOKEN, None, "")


def test_sync_records_a_client_that_cannot_be_built(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add_all([JiraProject(key="AAA", enabled=True), JiraProject(key="BBB", enabled=True)])
    db_session.commit()

    def broken(conn: Any) -> Any:
        raise ssl.SSLError("bad CA")

    monkeypatch.setattr(JiraClient, "from_connection", staticmethod(broken))
    out = sync_all(db_session, settings())
    assert set(out["projects"]) == {"AAA", "BBB"}
    assert "SSLError" in out["projects"]["AAA"]["error"]
    db_session.expire_all()
    for project in db_session.scalars(select(JiraProject)):
        assert project.last_error and "SSLError" in project.last_error
        assert project.synced_until is None


class FailingQueue:
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis down")


def test_tick_releases_the_slot_when_enqueue_fails(db_session: Session) -> None:
    redis = FakeRedis()
    with pytest.raises(ConnectionError):
        tick(db_session, settings(), redis, FailingQueue())
    assert DUE_KEY not in redis.ttls  # the next tick tries again
    assert tick(db_session, settings(), redis, FakeQueue()) == "queued"


# -- basic auth: a service account's username and password (Jira older than 8.14) -------------

PASSWORD = "mật " + "khẩu-" + "web"  # fake, built by concatenation


def test_basic_auth_from_env_then_web(db_session: Session) -> None:
    env = settings(jira_auth_type="basic", jira_username=" env-bot ")
    conn = connection(db_session, env, BOX)
    assert (conn.auth_type, conn.username) == ("basic", "env-bot")
    assert (conn.auth_type_source, conn.username_source) == ("env", "env")
    assert conn.configured and conn.token == ENV_TOKEN
    body = conn.as_json()
    assert body["auth_type"] == "basic" and body["username"] == "env-bot"
    assert body["env"]["auth_type"] is True and body["env"]["username"] is True
    # Basic auth without a username: not configured (a password alone logs in as no one).
    assert not jira_configured(db_session, settings(jira_auth_type="basic"), BOX)
    # A username with ':' in JIRA_USERNAME is not used.
    assert connection(db_session, settings(jira_username="a:b"), BOX).username == ""
    # Saved on the web: wins over the variables.
    save_connection(
        db_session, parse_connection({"auth_type": "basic", "username": "codepulse-bot"}), None
    )
    web = connection(db_session, settings(), BOX)
    assert (web.auth_type, web.username) == ("basic", "codepulse-bot")
    assert (web.auth_type_source, web.username_source) == ("web", "web")
    save_connection(db_session, parse_connection({"auth_type": None, "username": ""}), None)
    back = connection(db_session, env, BOX)
    assert (back.auth_type, back.username, back.username_source) == ("basic", "env-bot", "env")
    with JiraClient.from_connection(back) as client:
        assert client._http.headers["Authorization"].startswith("Basic ")


def test_the_password_only_goes_to_its_own_server(db_session: Session) -> None:
    """The password is bound to its server exactly as a PAT (§2)."""
    values = {"base_url": "https://jira.congty.vn", "auth_type": "basic", "username": "bot"}
    save_connection(db_session, parse_connection(values), None)
    save_token(db_session, BOX, PASSWORD, None, "https://jira.congty.vn", "basic")
    row = db_session.get(StoredSecret, TOKEN_SECRET)
    assert row is not None and PASSWORD not in row.value_encrypted
    conn = connection(db_session, settings(), BOX)
    assert conn.token == PASSWORD and conn.configured  # kept as typed: the space stays
    assert PASSWORD not in repr(conn) and PASSWORD not in str(conn.as_json())
    # Same server, another username or auth type: the password stays.
    save_connection(db_session, parse_connection({"username": "bot2"}), None)
    assert connection(db_session, settings(), BOX).token == PASSWORD
    # Another server: never sent there.
    save_connection(db_session, parse_connection({"base_url": "https://evil.vn"}), None)
    moved = connection(db_session, settings(), BOX)
    assert moved.token == "" and moved.token_other_server and not moved.configured
    # A password is checked as one, not as a PAT.
    with pytest.raises(ValueError, match="mật khẩu"):
        save_token(db_session, BOX, "a\nb", None, "https://evil.vn", "basic")
    with pytest.raises(ValueError, match="ASCII"):
        save_token(db_session, BOX, PASSWORD, None, "https://evil.vn", "pat")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"auth_type": "oauth"}, "pat hoặc basic"),
        ({"username": "a:b"}, "username"),
        ({"username": "a" * 201}, "200"),
        ({"username": "bot\x00"}, "username"),
    ],
)
def test_auth_checks(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        parse_connection(raw)


def test_password_and_username_checks() -> None:
    from app.jira.settings import check_password, check_secret, check_username

    assert check_password(" mật khẩu ") == " mật khẩu "  # not stripped
    assert check_secret(PASSWORD, "basic") == PASSWORD
    assert check_username("  codepulse-bot ") == "codepulse-bot"
    for bad in ("", "a" * 1001, "tab\there", "nul\x00", "del\x7f"):
        with pytest.raises(ValueError, match="mật khẩu"):
            check_password(bad)


@pytest.mark.parametrize(
    ("version", "numbers", "expected"),
    [
        ("8.0.2", [8, 0, 2], False),
        ("8.13.9", None, False),
        ("8.14.0", None, True),
        ("9.12.1", None, True),
        (None, [10, 3, 0], True),
        ("unknown", None, None),
        (None, None, None),
    ],
)
def test_pat_supported(version: str | None, numbers: Any, expected: bool | None) -> None:
    from app.jira.settings import pat_supported

    assert pat_supported(version, numbers) is expected


class RefusingJira(FakeJira):
    def __init__(self) -> None:
        super().__init__({"AAA": [jira_issue(1, "AAA-1")], "BBB": [jira_issue(2, "BBB-1")]})
        self.refuse = True
        self.down = False
        self.calls = 0

    def fields(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.down:
            from app.jira.client import JiraError

            raise JiraError(503, "GET /field -> HTTP 503")
        if self.refuse:
            from app.jira.client import JiraAuthError

            raise JiraAuthError(401, "GET /field -> HTTP 401: Jira từ chối đăng nhập: CAPTCHA")
        return super().fields()


def failure_of(session: Session, env: Settings) -> dict[str, Any] | None:
    from app.crypto import secret_box
    from app.jira.settings import auth_failure

    return auth_failure(session, connection(session, env), secret_box())


def test_a_refused_login_stops_the_sync_and_pauses_the_schedule(db_session: Session) -> None:
    db_session.add_all([JiraProject(key="AAA", enabled=True), JiraProject(key="BBB", enabled=True)])
    db_session.commit()
    fake = RefusingJira()
    out = sync_all(db_session, settings(), fake)
    # One login tried, not one per project and no retry: every project keeps the error.
    assert fake.calls == 1 and fake.queries == []
    assert out["auth_failed"] is True and set(out["projects"]) == {"AAA", "BBB"}
    db_session.expire_all()
    for row in db_session.scalars(select(JiraProject)):
        assert row.last_error and "401" in row.last_error and row.synced_until is None
    failure = failure_of(db_session, settings())
    assert failure is not None and "CAPTCHA" in failure["error"]
    # The schedule waits: a wrong password tried every interval would lock the account. A
    # scheduled run already queued checks again and does not call Jira.
    redis, queue = FakeRedis(), FakeQueue()
    assert tick(db_session, settings(), redis, queue) == "paused: login refused"
    assert queue.jobs == []
    assert sync_all(db_session, settings(), fake) == {"skipped": "paused"}
    assert fake.calls == 1
    # "Đồng bộ ngay" (manual) while Jira still cannot be reached: nothing proves the login, the
    # pause stays.
    fake.refuse = False
    fake.down = True
    sync_all(db_session, settings(), fake, manual=True)
    assert failure_of(db_session, settings()) is not None
    # After the CAPTCHA is solved: it goes through and the schedule resumes.
    fake.down = False
    out = sync_all(db_session, settings(), fake, manual=True)
    assert out["projects"] == {"AAA": {"issues": 1}, "BBB": {"issues": 1}}
    assert failure_of(db_session, settings()) is None
    assert tick(db_session, settings(), redis, queue) == "queued"
    # Clearing the connection on the web clears the pause as well.
    from app.crypto import secret_box
    from app.jira.settings import AUTH_FAILURE_KEY, record_auth_failure

    record_auth_failure(db_session, "x", connection(db_session, settings()), secret_box())
    clear_connection(db_session)
    assert db_session.get(AppSetting, AUTH_FAILURE_KEY) is None


def test_the_pause_ends_when_the_credentials_change(db_session: Session) -> None:
    """JIRA_TOKEN fixed and the scheduler restarted: another fingerprint, the tick goes on."""
    from app.crypto import secret_box
    from app.jira.settings import AUTH_FAILURE_KEY, record_auth_failure

    record_auth_failure(db_session, "HTTP 401", connection(db_session, settings()), secret_box())
    row = db_session.get(AppSetting, AUTH_FAILURE_KEY)
    assert row is not None and ENV_TOKEN not in str(row.value)  # an HMAC, not the secret
    redis, queue = FakeRedis(), FakeQueue()
    assert tick(db_session, settings(), redis, queue) == "paused: login refused"
    fixed = settings(jira_token=SecretStr("pat-" + "fixed"))
    assert tick(db_session, fixed, redis, queue) == "queued"
    assert db_session.get(AppSetting, AUTH_FAILURE_KEY) is None


def test_an_error_other_than_jiras_keeps_only_its_class(db_session: Session) -> None:
    """A UnicodeEncodeError can quote the header it failed on (the secret)."""

    class Broken(FakeJira):
        def fields(self) -> list[dict[str, Any]]:
            raise UnicodeEncodeError("ascii", PASSWORD, 1, 2, "ordinal not in range")

    db_session.add(JiraProject(key="AAA", enabled=True))
    db_session.commit()
    out = sync_all(db_session, settings(), Broken({}))
    assert out["projects"]["AAA"] == {"error": "UnicodeEncodeError"}
    db_session.expire_all()
    row = db_session.scalars(select(JiraProject)).one()
    assert row.last_error == "UnicodeEncodeError"


def test_saved_secret_keeps_its_auth_type_and_env_password_is_not_stripped(
    db_session: Session,
) -> None:
    from app.jira.settings import CONNECTION_KEY, TOKEN_AUTH_TYPE, clear_token

    save_token(db_session, BOX, PASSWORD, None, "https://env.jira.vn", "basic")
    row = db_session.get(AppSetting, CONNECTION_KEY)
    assert row is not None and row.value[TOKEN_AUTH_TYPE] == "basic"
    clear_token(db_session)
    assert TOKEN_AUTH_TYPE not in row.value
    padded = " " + PASSWORD + " "
    basic = settings(jira_auth_type="basic", jira_username="bot", jira_token=SecretStr(padded))
    assert connection(db_session, basic, BOX).token == padded
    pat = settings(jira_token=SecretStr(" " + ENV_TOKEN + "\n"))
    assert connection(db_session, pat, BOX).token == ENV_TOKEN
