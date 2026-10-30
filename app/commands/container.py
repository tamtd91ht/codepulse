"""One throwaway container per command run (the user's choice 2026-10-30: the system must be
safe when a repo's command runs shell commands).

The command worker holds every secret (DATABASE_URL, APP_ENCRYPTION_KEY, GitLab tokens), so the
CLI never runs there, and the worker never talks to Docker either: it asks the command gate
(app/commands/gate.py, the only holder of the Docker socket) over a unix socket only the two of
them mount. The gate builds the container itself: no environment but the Claude token and
CODEPULSE_OUTPUT_DIR, no mounts, no capabilities, `no-new-privileges`, limits on memory, CPU
and processes, on the command network.

Here the worker uploads the prepared clone as a tar (app/commands/inside.py runs it), follows
the logs as the live log, and watches the run: "Huỷ", the deadline or more than
COMMAND_DISK_MB written to the container's layer kill it. Then exactly one result file is
streamed out (a regular file under the size cap, nothing else) and the container is removed
with every process the command started.

Nothing the container writes reaches the worker's disk except that one file, written by the
worker under a name it chose. The CLI's reported cost comes from inside and could be forged by
the command itself; the run's own cap still bounds what it can spend.
"""

import io
import json
import logging
import os
import subprocess
import tarfile
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from app.commands import inside
from app.commands.gate import ALLOWED_ENV, frames
from app.commands.runner import Cancelled
from app.commands.workspace import safe_file_name

log = logging.getLogger(__name__)

STDOUT, STDERR = 1, 2
CONNECT_TIMEOUT_S = 10.0
CALL_TIMEOUT_S = 120.0
KILL_MARGIN_S = 60.0
WATCH_EVERY_S = 3.0
MAX_LINE = 256 * 1024
STDERR_TAIL = 2000
TAR_OVERHEAD = 64 * 1024
CONTAINER_HOME = "/home/codepulse"


class ContainerError(OSError):
    """The gate (or Docker behind it) refused or failed a call."""


class DiskExceeded(ContainerError):
    """The command wrote more than COMMAND_DISK_MB to its container."""


class Gate:
    """The command gate's API over its unix socket."""

    def __init__(self, socket_path: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url="http://gate",
            timeout=httpx.Timeout(CALL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            transport=transport or httpx.HTTPTransport(uds=socket_path),
        )

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, ok: Sequence[int] = (), **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ContainerError(
                f"command gate {method} {path}: {exc.__class__.__name__}"
            ) from None
        if response.is_error and response.status_code not in ok:
            try:  # the gate says why (e.g. it refuses runs: no disk quota, open network)
                why = str(response.json().get("error") or "")[:300]
            except ValueError:
                why = ""
            raise ContainerError(
                f"command gate {method} {path} -> HTTP {response.status_code}"
                + (f": {why}" if why else "")
            )
        return response

    def create(self, run_id: int, env: dict[str, str]) -> str:
        body = self._call("POST", "/runs", json={"run_id": run_id, "env": env}).json()
        return str(body["Id"])

    def put_workspace(self, cid: str, tar: Path) -> None:
        with open(tar, "rb") as body:
            self._call(
                "PUT",
                f"/runs/{cid}/workspace",
                content=_chunks(body),
                timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT_S),
            )

    def start(self, cid: str) -> None:
        self._call("POST", f"/runs/{cid}/start", ok=(304,))

    def logs(self, cid: str) -> Iterator[bytes]:
        """The raw multiplexed stdout/stderr stream until the container stops."""
        try:
            with self._http.stream(
                "GET",
                f"/runs/{cid}/logs",
                timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT_S),
            ) as response:
                if response.is_error:
                    raise ContainerError(f"command gate logs -> HTTP {response.status_code}")
                yield from response.iter_bytes()
        except httpx.HTTPError as exc:
            raise ContainerError(f"command gate logs: {exc.__class__.__name__}") from None

    def wait(self, cid: str) -> int:
        body = self._call(
            "POST", f"/runs/{cid}/wait", timeout=httpx.Timeout(None, connect=CONNECT_TIMEOUT_S)
        ).json()
        return int(body.get("StatusCode", -1))

    def kill(self, cid: str) -> None:
        self._call("POST", f"/runs/{cid}/kill", ok=(404, 409))

    def size(self, cid: str) -> int:
        return int(self._call("GET", f"/runs/{cid}/size").json().get("size") or 0)

    def result(self, cid: str, name: str, max_bytes: int, dest: Path) -> None:
        """The tar of the one result file, streamed into `dest` up to `max_bytes` (and refused
        beyond): a large result never sits in the worker's memory."""
        written = 0
        try:
            with (
                self._http.stream("GET", f"/runs/{cid}/result", params={"name": name}) as response,
                open(dest, "wb") as out,
            ):
                if response.is_error:
                    raise ContainerError(f"command gate result -> HTTP {response.status_code}")
                for piece in response.iter_bytes():
                    written += len(piece)
                    if written > max_bytes:
                        raise ContainerError("result larger than the size limit")
                    out.write(piece)
        except httpx.HTTPError as exc:
            raise ContainerError(f"command gate result: {exc.__class__.__name__}") from None

    def remove(self, cid: str) -> None:
        self._call("DELETE", f"/runs/{cid}", ok=(404,))

    def runs(self) -> list[dict[str, Any]]:
        rows = self._call("GET", "/runs").json()
        return rows if isinstance(rows, list) else []


def _chunks(body: io.BufferedReader, size: int = 1024 * 1024) -> Iterator[bytes]:
    while chunk := body.read(size):
        yield chunk


def workspace_tar(work: Path, request: dict[str, Any], dest: Path) -> None:
    """`run/request.json` and `run/work/...` as one tar, owned by the image's user (the worker
    runs as that same user). Symlinks were removed already; any left are skipped."""
    uid = getattr(os, "getuid", lambda: 0)()
    gid = getattr(os, "getgid", lambda: 0)()

    def owned(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if info.issym() or info.islnk():
            return None
        info.uid, info.gid, info.uname, info.gname = uid, gid, "", ""
        return info

    with tarfile.open(dest, "w") as tar:
        # Every folder is in the tar: one Docker creates on its own would belong to root.
        folder = owned(tarfile.TarInfo("run"))
        assert folder is not None  # noqa: S101 - a folder
        folder.type, folder.mode = tarfile.DIRTYPE, 0o700
        tar.addfile(folder)
        data = json.dumps(request).encode()
        info = owned(tarfile.TarInfo("run/request.json"))
        assert info is not None  # noqa: S101 - a regular file
        info.size, info.mode = len(data), 0o600
        tar.addfile(info, io.BytesIO(data))
        tar.add(work, arcname="run/work", filter=owned)


def extract_result(archive: Path, name: str, dest: Path, max_bytes: int) -> int:
    """Write the one result file from the gate's tar to `dest`: only a regular file named
    `name` (Docker names the entry after the file) within `max_bytes`. Returns its size."""
    with tarfile.open(archive) as tar:
        for member in tar:
            if PurePosixPath(member.name).name != name or len(PurePosixPath(member.name).parts) > 2:
                continue
            if not member.isreg() or member.size > max_bytes:
                raise ContainerError("result is not a regular file within the size limit")
            source = tar.extractfile(member)
            if source is None:
                break
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "xb") as target:
                remaining = max_bytes
                while chunk := source.read(1024 * 1024):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ContainerError("result larger than announced")
                    target.write(chunk)
            return member.size
    raise ContainerError("result file missing")


Timer = Callable[[float, Callable[[], None]], Any]


class ContainerRun:
    """A `stream_lines` replacement for app/commands/runner.run: the same CLI call, inside a
    throwaway container. After the call, `summary` holds what inside.py reported and
    `fetch()` copies the result file out; `close()` removes the container."""

    def __init__(
        self,
        gate: Gate,
        *,
        run_id: int,
        work: Path,
        tar_path: Path,
        max_output_bytes: int,
        max_disk_bytes: int,
        cancelled: Callable[[], bool] = lambda: False,
        kill_after: Timer | None = None,
        watch_every_s: float = WATCH_EVERY_S,
    ) -> None:
        self.gate = gate
        self.run_id = run_id
        self.work, self.tar_path = work, tar_path
        self.max_output_bytes, self.max_disk_bytes = max_output_bytes, max_disk_bytes
        self.cancelled = cancelled
        self.cid: str | None = None
        self.summary: dict[str, Any] = {}
        self.stopped_for = ""  # cancel | disk | timeout
        self._kill_after = kill_after or _timer
        self._watch_every_s = watch_every_s

    def _stop(self, why: str) -> None:
        if not self.stopped_for:
            self.stopped_for = why
        if self.cid:
            try:
                self.gate.kill(self.cid)
            except ContainerError:
                log.warning("command container not killed", extra={"container": self.cid[:12]})

    def _watch(self, done: threading.Event) -> None:
        """ "Huỷ" even while the command prints nothing, and the disk the container fills."""
        while not done.wait(self._watch_every_s):
            try:
                if self.cancelled():
                    self._stop("cancel")
                    return
                if self.cid and self.gate.size(self.cid) > self.max_disk_bytes:
                    self._stop("disk")
                    return
            except Exception:  # a watch that fails must not end the run
                log.warning("command container watch failed", exc_info=True)

    def __call__(
        self,
        cmd: Sequence[str],
        *,
        stdin: str,
        cwd: str,
        env: dict[str, str],
        timeout_s: float,
        on_line: Callable[[str], None],
        new_session: bool = True,
    ) -> tuple[int, str]:
        request = {
            "argv": list(cmd),
            "stdin": stdin,
            "timeout_s": timeout_s,
            "max_output_bytes": self.max_output_bytes,
        }
        workspace_tar(self.work, request, self.tar_path)
        inner = {k: v for k, v in env.items() if k in ALLOWED_ENV}  # all the gate accepts
        self.cid = self.gate.create(self.run_id, inner)
        self.gate.put_workspace(self.cid, self.tar_path)
        self.tar_path.unlink(missing_ok=True)
        self.gate.start(self.cid)
        cid = self.cid
        done = threading.Event()
        timer = self._kill_after(timeout_s + KILL_MARGIN_S, lambda: self._stop("timeout"))
        watcher = threading.Thread(target=self._watch, args=(done,), daemon=True)
        watcher.start()
        stderr = b""
        rest = b""
        try:
            for kind, payload in frames(self.gate.logs(cid)):
                if kind == STDERR:
                    stderr = (stderr + payload)[-STDERR_TAIL:]
                    continue
                *lines, rest = (rest + payload).split(b"\n")
                rest = rest[-MAX_LINE:]
                for raw in lines:
                    self._line(raw[:MAX_LINE].decode("utf-8", "replace"), on_line)
            if rest:
                self._line(rest.decode("utf-8", "replace"), on_line)
        except BaseException:  # "Huỷ" from on_line, or the worker is stopping
            self._stop("cancel")
            raise
        finally:
            done.set()
            if hasattr(timer, "cancel"):
                timer.cancel()
        code = self.gate.wait(cid)
        if self.stopped_for == "cancel":
            raise Cancelled
        if self.stopped_for == "disk":
            raise DiskExceeded(f"command ghi quá {self.max_disk_bytes // (1024 * 1024)} MB")
        if self.stopped_for == "timeout" or self.summary.get("timed_out"):
            raise subprocess.TimeoutExpired(list(cmd), timeout_s)
        return code, stderr.decode("utf-8", "replace")

    def _line(self, line: str, on_line: Callable[[str], None]) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict) and event.get("type") == inside.RESULT_TYPE:
            self.summary = event
            return
        on_line(line + "\n")

    def fetch(self, dest_dir: Path) -> tuple[str, int] | None:
        """Copy the result file into `dest_dir` (the run's folder on the shared volume); returns
        (stored name, size), or None when the command produced nothing."""
        raw = self.summary.get("name")
        if not self.cid or not isinstance(raw, str):
            return None
        name = safe_file_name(raw)
        archive = self.tar_path.with_name("result.tar")  # the worker's private folder
        try:
            self.gate.result(self.cid, name, self.max_output_bytes + TAR_OVERHEAD, archive)
            size = extract_result(archive, name, dest_dir / name, self.max_output_bytes)
        finally:
            archive.unlink(missing_ok=True)
        return name, size

    def close(self) -> None:
        if self.cid:
            try:
                self.gate.remove(self.cid)
            except ContainerError:
                log.warning("command container not removed", extra={"container": self.cid[:12]})


def _timer(delay: float, action: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, action)
    timer.daemon = True
    timer.start()
    return timer
