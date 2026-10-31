""" "Đăng nhập Claude" on the Admin screen: drives `claude setup-token` (outside the spec,
requested by the user; see PROGRESS.md).

setup-token is the CLI's own login for a Claude subscription: it prints an OAuth URL, the admin
signs in on claude.ai and gets a code, the code goes back to the CLI, which prints a long-lived
token (sk-ant-oat…). The CLI only runs that flow on a terminal, so it gets a pseudo-terminal here
(POSIX only: the api container; on Windows the admin pastes a token instead).

The api runs one uvicorn process, so a login in progress is kept in memory between the "start"
and the "code" request. The token is returned to the caller only, never logged; every error is
scrubbed of anything shaped like a key.
"""

import contextlib
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from app.review.providers.claude_code import ENV_PASSTHROUGH, scrub_secrets

log = logging.getLogger(__name__)

URL_TIMEOUT_S = 20.0
TOKEN_TIMEOUT_S = 25.0
ENTER_DELAY_S = 0.5
LOGIN_TTL_S = 600.0  # an abandoned login is killed after this

# CSI (colours, cursor moves), OSC (titles, hyperlinks) and other two-byte escapes that Ink emits.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_URL_RE = re.compile(r"https://[^\s\"'<>]+/oauth/authorize\?[^\s\"'<>]+")
_TOKEN_RE = re.compile(r"sk-ant-oat\d*-[A-Za-z0-9_\-]{20,}")
# A rejected code does not end the CLI: it shows "OAuth error: … Press Enter to retry."
_ERROR_RE = re.compile(r"OAuth error:?\s*(.*?)\s*(?:Press Enter to retry|\n|$)")


class LoginError(Exception):
    pass


class LoginUnavailable(LoginError):
    """This host cannot run the flow (no pseudo-terminal support)."""


def clean(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r", "")


def find_url(text: str) -> str | None:
    """The login URL once something follows it (it may arrive in pieces too, see find_token)."""
    cleaned = clean(text)
    for match in _URL_RE.finditer(cleaned):
        if match.end() < len(cleaned):
            return match.group(0)
    return None


def find_token(text: str, complete: bool = True) -> str | None:
    """The token, only once something follows it. The CLI's output arrives in pieces, and a piece
    that ends inside the token would otherwise hand over its first part: stored, it fails every
    call with "401 OAuth access token is invalid". `complete=False` once the CLI has exited."""
    cleaned = clean(text)
    for match in _TOKEN_RE.finditer(cleaned):
        # Greedy: a match that ends before the text does is followed by a non-token character.
        if not complete or match.end() < len(cleaned):
            return match.group(0)
    return None


def find_error(text: str) -> str | None:
    match = _ERROR_RE.search(clean(text))
    return scrub_secrets(match.group(1).strip() or "rejected")[:200] if match else None


class SetupToken(Protocol):
    """One running `claude setup-token`."""

    def wait_for_url(self, timeout_s: float) -> str: ...

    def submit(self, code: str, timeout_s: float) -> str: ...

    def close(self) -> None: ...


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_PASSTHROUGH}
    env["TERM"] = "xterm-256color"
    # No browser in a container; the URL is shown on the Admin screen instead.
    env["BROWSER"] = "true"
    return env


class PtySetupToken:
    """`claude setup-token` on a pseudo-terminal wide enough that the URL and token never wrap."""

    COLUMNS = 2000

    # Declared here: on Windows mypy treats the body of __init__ after the platform check as
    # unreachable and would not see them.
    _proc: subprocess.Popen[bytes]
    _master: int
    _cwd: tempfile.TemporaryDirectory[str]
    _buffer: str
    _changed: threading.Condition
    _reader: threading.Thread

    def __init__(self, binary: str, env: Mapping[str, str] | None = None) -> None:
        if sys.platform == "win32":
            raise LoginUnavailable("login from the dashboard needs a POSIX host (api container)")
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, self.COLUMNS, 0, 0))
        self._cwd = tempfile.TemporaryDirectory(prefix="codepulse-login-")
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - the configured CLI, fixed args
                [binary, "setup-token"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self._cwd.name,
                env=dict(env) if env is not None else _env(),
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            os.close(master)
            os.close(slave)
            self._cwd.cleanup()
            raise LoginError(f"could not start the CLI ({exc.__class__.__name__})") from None
        os.close(slave)
        self._master = master
        self._buffer = ""
        self._changed = threading.Condition()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while True:
            try:
                chunk = os.read(self._master, 4096)
            except OSError:  # EIO once the CLI exits and the slave side closes
                chunk = b""
            with self._changed:
                if not chunk:
                    self._changed.notify_all()
                    return
                self._buffer += chunk.decode("utf-8", errors="replace")
                self._changed.notify_all()

    def _wait(
        self,
        find: Callable[[str], str | None],
        timeout_s: float,
        what: str,
        fail: Callable[[str], str | None] = lambda _: None,
        last: Callable[[str], str | None] | None = None,
    ) -> str:
        """`find` in the output as it grows; once the CLI has exited, `last` (if given) gets a
        final look at everything it printed."""
        deadline = time.monotonic() + timeout_s
        with self._changed:
            while True:
                if found := find(self._buffer):
                    return found
                if error := fail(self._buffer):
                    raise LoginError(f"claude.ai rejected the code ({error}); start again")
                remaining = deadline - time.monotonic()
                if self._proc.poll() is not None and not self._reader.is_alive():
                    break
                if remaining <= 0:
                    tail = scrub_secrets(clean(self._buffer).strip())[-300:]
                    raise LoginError(f"the CLI showed no {what} within {timeout_s:g}s: {tail}")
                self._changed.wait(min(remaining, 0.5))
            if last is not None and (found := last(self._buffer)):
                return found
        tail = scrub_secrets(clean(self._buffer).strip())[-300:]
        raise LoginError(f"the CLI exited {self._proc.returncode} without a {what}: {tail}")

    def wait_for_url(self, timeout_s: float) -> str:
        return self._wait(find_url, timeout_s, "login URL")

    def submit(self, code: str, timeout_s: float) -> str:
        with self._changed:
            self._buffer = ""  # the code is echoed back; the token comes after it
        # Ink reads a multi-byte chunk as a paste, so "code\r" in one write never presses Enter:
        # the CLI would sit on the prompt until the timeout. Enter goes on its own, after a pause.
        os.write(self._master, code.encode())
        time.sleep(ENTER_DELAY_S)
        os.write(self._master, b"\r")
        return self._wait(
            find_token,
            timeout_s,
            "token",
            fail=find_error,
            last=lambda text: find_token(text, complete=False),
        )

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=5)
        with contextlib.suppress(OSError):
            os.close(self._master)
        self._cwd.cleanup()


SetupTokenFactory = Callable[[], SetupToken]


@dataclass
class _Pending:
    login: SetupToken
    user_id: int
    started: float = field(default_factory=time.monotonic)


class LoginSessions:
    """At most one login in progress; a new start replaces it, an old one expires."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    def start(self, factory: SetupTokenFactory, user_id: int) -> tuple[str, str]:
        """(login id, URL to open)."""
        self.cancel_all()
        login = factory()
        try:
            url = login.wait_for_url(URL_TIMEOUT_S)
        except BaseException:
            login.close()
            raise
        login_id = secrets.token_urlsafe(16)
        with self._lock:
            self._pending[login_id] = _Pending(login, user_id, self._clock())
        return login_id, url

    def finish(self, login_id: str, user_id: int, code: str) -> str:
        """The token for this code; the login is over either way."""
        with self._lock:
            self._expire()
            pending = self._pending.get(login_id)
            if pending is None or pending.user_id != user_id:
                raise KeyError(login_id)
            del self._pending[login_id]
        try:
            return pending.login.submit(code, TOKEN_TIMEOUT_S)
        finally:
            pending.login.close()

    def cancel(self, login_id: str) -> bool:
        with self._lock:
            pending = self._pending.pop(login_id, None)
        if pending is not None:
            pending.login.close()
        return pending is not None

    def cancel_all(self) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.login.close()

    def _expire(self) -> None:
        now = self._clock()
        for login_id, item in list(self._pending.items()):
            if now - item.started > LOGIN_TTL_S:
                del self._pending[login_id]
                item.login.close()


SESSIONS = LoginSessions()
