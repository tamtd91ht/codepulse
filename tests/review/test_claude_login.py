import os
import sys
from pathlib import Path

import pytest

from app.review.claude_login import (
    LOGIN_TTL_S,
    LoginError,
    LoginSessions,
    PtySetupToken,
    clean,
    find_error,
    find_token,
    find_url,
)

# What `claude setup-token` draws (Ink): colours, an OSC 8 hyperlink, carriage returns.
SCREEN = (
    "\x1b[2J\x1b[1mWelcome to Claude Code\x1b[22m\r\n"
    "Browser didn't open? Use the url below to sign in:\r\n\r\n"
    "\x1b]8;;https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz\x07"
    "https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz\x1b]8;;\x07\r\n"
    "Paste code here if prompted > "
)
DONE = (
    "\x1b[32m✓ Long-lived authentication token created successfully!\x1b[39m\r\n"
    "Your OAuth token (valid for 1 year):\r\n"
    "\x1b[33msk-ant-oat01-AbC_def-123456789012345678\x1b[39m\r\nStore this token securely."
)


def test_clean_drops_escapes() -> None:
    assert "\x1b" not in clean(SCREEN) and "\r" not in clean(SCREEN)


def test_finds_the_login_url_and_the_token() -> None:
    assert find_url(SCREEN) == "https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz"
    assert find_token(DONE) == "sk-ant-oat01-AbC_def-123456789012345678"
    assert find_url("starting…") is None
    assert find_token(SCREEN) is None


def test_finds_a_rejected_code() -> None:
    # As setup-token 2.1.308 drew it for a wrong code (the code itself is masked).
    screen = "*****#state\nOAuth error: Request failed with status code 400Press Enter to retry. "
    assert find_error(screen) == "Request failed with status code 400"
    assert find_error(DONE) is None


class FakeLogin:
    def __init__(self) -> None:
        self.closed = False

    def wait_for_url(self, timeout_s: float) -> str:
        return "https://claude.ai/oauth/authorize?x=1"

    def submit(self, code: str, timeout_s: float) -> str:
        return "sk-ant-oat01-token"

    def close(self) -> None:
        self.closed = True


def test_sessions_belong_to_the_admin_who_started_them() -> None:
    sessions = LoginSessions()
    login = FakeLogin()
    login_id, _ = sessions.start(lambda: login, user_id=1)
    with pytest.raises(KeyError):
        sessions.finish(login_id, 2, "code")
    assert sessions.finish(login_id, 1, "code") == "sk-ant-oat01-token"
    assert login.closed


def test_sessions_expire() -> None:
    now = [0.0]
    sessions = LoginSessions(clock=lambda: now[0])
    login = FakeLogin()
    login_id, _ = sessions.start(lambda: login, user_id=1)
    now[0] = LOGIN_TTL_S + 1
    with pytest.raises(KeyError):
        sessions.finish(login_id, 1, "code")
    assert login.closed


def test_a_login_that_shows_no_url_is_closed() -> None:
    class NoUrl(FakeLogin):
        def wait_for_url(self, timeout_s: float) -> str:
            raise LoginError("no URL")

    login = NoUrl()
    with pytest.raises(LoginError):
        LoginSessions().start(lambda: login, user_id=1)
    assert login.closed


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_pty_flow_against_a_fake_cli(tmp_path: Path) -> None:
    """A stand-in for `claude setup-token`: prints the screen, reads the code, prints a token."""
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        "[ -t 0 ] || { echo 'not a terminal'; exit 3; }\n"
        "printf 'Use the url below to sign in:\\nhttps://claude.ai/oauth/authorize?state=s\\n'\n"
        "printf 'Paste code here if prompted > '\n"
        "read code\n"
        '[ "$code" = "good#s" ] || { echo "OAuth error: invalid code"; exit 1; }\n'
        "printf 'Your OAuth token:\\nsk-ant-oat01-faketokenfaketokenfaketoken\\n'\n"
    )
    script.chmod(0o700)
    env = {"PATH": os.environ.get("PATH", "")}

    login = PtySetupToken(str(script), env)
    try:
        assert login.wait_for_url(5) == "https://claude.ai/oauth/authorize?state=s"
        assert login.submit("good#s", 5) == "sk-ant-oat01-faketokenfaketokenfaketoken"
    finally:
        login.close()

    bad = PtySetupToken(str(script), env)
    try:
        bad.wait_for_url(5)
        with pytest.raises(LoginError, match="invalid code"):
            bad.submit("bad#s", 5)
    finally:
        bad.close()
