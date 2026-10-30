"""The command gate: the only holder of the Docker socket (service `command-gate`).

A Docker API caller can start a privileged container that mounts `/`: it is root on the host.
So the command worker never talks to Docker. It talks to this gate, over a unix socket in a
folder only the gate and the command worker mount, and the gate offers a few fixed operations:

    POST   /runs                 {"run_id", "env"} -> creates a container from a config the gate
                                 builds itself (image, command, no mounts, no capabilities,
                                 limits, the command network); env keys from ALLOWED_ENV only
    PUT    /runs/<id>/workspace  the tar of the prepared run, into /home/codepulse
    POST   /runs/<id>/start | /wait | /kill
    GET    /runs/<id>/logs       stdout/stderr, followed (Docker's multiplexed stream)
    GET    /runs/<id>/size       bytes the container wrote to its own layer
    GET    /runs/<id>/result?name=<file>   the tar of /home/codepulse/run/result/<file>
    DELETE /runs/<id>
    GET    /runs                 the containers it created (for the clean-up)

Every /runs/<id> call first checks that the container carries the gate's label, so the gate
cannot be used on any other container. Its own settings come from the environment
(COMMAND_IMAGE, COMMAND_NETWORK, COMMAND_MEMORY_MB, COMMAND_CPUS, COMMAND_GATE_SOCKET); it
loads no application settings and holds no secret of the app.

Run it with `python -m app.commands.gate`.
"""

import http.client
import json
import logging
import os
import re
import socket
import socketserver
import struct
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit

log = logging.getLogger(__name__)

LABEL = "codepulse.command_run"
HOME = "/home/codepulse"
RESULT_DIR = f"{HOME}/run/result"
ALLOWED_ENV = frozenset(
    {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "CODEPULSE_OUTPUT_DIR",
        "LANG",
        "TZ",
    }
)
CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9._ -]{1,120}$")
MAX_JSON = 64 * 1024
JSON_HEADERS = {"Content-Type": "application/json"}
CHUNK = 64 * 1024


@dataclass(frozen=True)
class Limits:
    image: str = "codepulse-app:latest"
    network: str = "codepulse-commands"
    memory_mb: int = 4096
    cpus: float = 2.0
    pids: int = 512
    # A real quota on the container's layer (overlay2 on XFS with pquota): without it one
    # command can fill the host's disk or inodes, PostgreSQL included.
    disk_mb: int = 5120
    quota: bool = True
    # Escape hatches, off by default: serve without a disk quota / without the network
    # isolation of RUNBOOK §15 (the gate checks both at start and refuses runs otherwise).
    allow_no_quota: bool = False
    allow_open_network: bool = False

    @classmethod
    def from_env(cls) -> "Limits":
        env = os.environ
        return cls(
            image=env.get("COMMAND_IMAGE", cls.image),
            network=env.get("COMMAND_NETWORK", cls.network),
            memory_mb=int(env.get("COMMAND_MEMORY_MB", cls.memory_mb)),
            cpus=float(env.get("COMMAND_CPUS", cls.cpus)),
            disk_mb=int(env.get("COMMAND_DISK_MB", cls.disk_mb)),
            allow_no_quota=env.get("COMMAND_ALLOW_NO_DISK_QUOTA") == "1",
            allow_open_network=env.get("COMMAND_ALLOW_OPEN_NETWORK") == "1",
        )


def container_config(run_id: int, env: dict[str, str], limits: Limits) -> dict[str, Any]:
    """The only container the gate creates. Nothing in it comes from the caller but the run id
    (a label) and the allowed environment variables."""
    memory = limits.memory_mb * 1024 * 1024
    return {
        "Image": limits.image,
        "Cmd": ["python", "-m", "app.commands.inside"],
        "Env": [f"{k}={v}" for k, v in sorted(env.items())] + [f"HOME={HOME}"],
        "WorkingDir": HOME,
        "User": "codepulse",
        "Tty": False,
        "OpenStdin": False,
        "Labels": {LABEL: str(run_id)},
        "HostConfig": {
            "NetworkMode": limits.network,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Binds": [],
            "Mounts": [],
            "Devices": [],
            "Memory": memory,
            "MemorySwap": memory,
            "NanoCpus": int(limits.cpus * 1e9),
            "PidsLimit": limits.pids,
            "ShmSize": 64 * 1024 * 1024,
            "IpcMode": "private",
            "AutoRemove": False,
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "20m"}},
            **({"StorageOpt": {"size": f"{limits.disk_mb}M"}} if limits.quota else {}),
        },
    }


def clean_env(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("env must be an object")
    env: dict[str, str] = {}
    for key, value in raw.items():
        if key not in ALLOWED_ENV or not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"env {str(key)[:40]} not allowed")
        env[key] = value
    return env


# -- Docker over its unix socket ---------------------------------------------------------------


class Backend(Protocol):
    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], Iterator[bytes]]: ...


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float | None) -> None:
        super().__init__("docker", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined,unused-ignore]
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class DockerSocket:
    def __init__(self, path: str = "/var/run/docker.sock", timeout: float | None = None) -> None:
        self.path, self.timeout = path, timeout

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], Iterator[bytes]]:
        conn = _UnixConnection(self.path, self.timeout)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()

        def chunks() -> Iterator[bytes]:
            try:
                while piece := response.read(CHUNK):
                    yield piece
            finally:
                conn.close()

        return response.status, dict(response.getheaders()), chunks()


# -- the gate's operations ---------------------------------------------------------------------


@dataclass
class Reply:
    status: int
    body: Iterator[bytes] | bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


def _json(status: int, data: Any) -> Reply:
    return Reply(status, json.dumps(data).encode(), {"Content-Type": "application/json"})


def _read(body: Iterator[bytes]) -> bytes:
    return b"".join(body)


def frames(stream: Iterable[bytes]) -> Iterator[tuple[int, bytes]]:
    """Demultiplex Docker's log stream: 8-byte headers (stream, 0, 0, 0, big-endian size)."""
    buffer = b""
    for chunk in stream:
        buffer += chunk
        while len(buffer) >= 8:
            kind, size = buffer[0], struct.unpack(">I", buffer[4:8])[0]
            if len(buffer) < 8 + size:
                break
            yield kind, buffer[8 : 8 + size]
            buffer = buffer[8 + size :]


# Ports of the host a command container must not reach (RUNBOOK §15): SSH, web, the dashboard
# and api, PostgreSQL, Redis.
PROBE_PORTS = (22, 80, 443, 3000, 5432, 6379, 8000)
# Only a timeout counts as closed: the RUNBOOK's DROP rule makes the host silent, while a
# refused connection means the host answered, i.e. the rule is missing. "PROBE DONE" proves the
# probe ran to the end (anything else fails closed).
PROBE_SCRIPT = (
    "import socket, sys\n"
    "for port in map(int, sys.argv[2:]):\n"
    "    try:\n"
    "        socket.create_connection((sys.argv[1], port), 3).close()\n"
    "        print('OPEN', port)\n"
    "    except ConnectionRefusedError:\n"
    "        print('OPEN', port)\n"
    "    except OSError:\n"
    "        pass\n"
    "print('PROBE DONE')\n"
)
PROBE_EVERY_S = 3600.0


def _run_once(docker: Backend, config: dict[str, Any]) -> tuple[int, str]:
    """Create, start, wait for and remove one container: (create status, its stdout)."""
    status, _, body = docker.call(
        "POST", "/containers/create", json.dumps(config).encode(), JSON_HEADERS
    )
    raw = _read(body)
    if status not in (200, 201):
        return status, raw.decode("utf-8", "replace")[:300]
    cid = str(json.loads(raw)["Id"])
    try:
        for method, path in (("POST", "start"), ("POST", "wait")):
            _read(docker.call(method, f"/containers/{cid}/{path}")[2])
        _, _, logs = docker.call("GET", f"/containers/{cid}/logs?stdout=1&stderr=1")
        out = b"".join(payload for kind, payload in frames(logs) if kind == 1)
    finally:
        _read(docker.call("DELETE", f"/containers/{cid}?force=1&v=1")[2])
    return status, out.decode("utf-8", "replace")


def preflight(docker: Backend, limits: Limits) -> tuple[Limits, str | None]:
    """At start: does Docker enforce the disk quota, and is the host closed to the command
    network? Returns the limits to use and why runs are refused (None: they are not)."""
    limits, reason = quota_check(docker, limits)
    return limits, reason or network_check(docker, limits)


def quota_check(docker: Backend, limits: Limits) -> tuple[Limits, str | None]:
    """Whether Docker accepts the disk quota; without it runs are refused unless allowed."""
    probe = container_config(0, {}, limits)
    probe["Cmd"] = ["true"]
    status, detail = _run_once(docker, probe)
    if status not in (200, 201):
        if not limits.allow_no_quota:
            return limits, (
                "Docker không hỗ trợ giới hạn đĩa cho container (cần overlay2 trên XFS với "
                f"pquota, RUNBOOK §15): {detail[:150]}"
            )
        log.warning("command containers run without a disk quota (COMMAND_ALLOW_NO_DISK_QUOTA)")
        limits = replace(limits, quota=False)
    return limits, None


def network_check(docker: Backend, limits: Limits) -> str | None:
    """Why runs are refused because the host is reachable from the command network, or None.
    Fails closed: no gateway, or a probe that did not run to the end, refuses runs too."""
    if limits.allow_open_network:
        return None
    status, _, body = docker.call("GET", f"/networks/{quote(limits.network)}")
    network = json.loads(_read(body) or b"{}") if status == 200 else {}
    gateways = [
        c.get("Gateway")
        for c in (network.get("IPAM") or {}).get("Config") or []
        if c.get("Gateway")
    ]
    if not gateways:
        return f"không kiểm tra được mạng {limits.network} (không thấy gateway)"
    probe = container_config(0, {}, limits)
    probe["Cmd"] = ["python", "-c", PROBE_SCRIPT, str(gateways[0]), *map(str, PROBE_PORTS)]
    try:
        _, out = _run_once(docker, probe)
    except (OSError, ValueError, KeyError) as exc:
        return f"không chạy được container kiểm tra mạng: {exc.__class__.__name__}"
    if "PROBE DONE" not in out:
        return "container kiểm tra mạng không chạy hết"
    open_ports = [line.split()[1] for line in out.splitlines() if line.startswith("OPEN ")]
    if open_ports:
        return (
            "container chạy command vẫn kết nối được tới máy chủ (cổng "
            f"{', '.join(open_ports)}): chưa áp dụng luật iptables của RUNBOOK §15"
        )
    return None


def watch_network(gate: "Gate", quota_blocked: str | None, every_s: float = PROBE_EVERY_S) -> None:
    """Re-check the network every hour: firewall rules lost after a reload stop new runs."""
    while True:
        time.sleep(every_s)
        try:
            reason = network_check(gate.docker, gate.limits)
        except Exception as exc:  # a check that cannot run refuses runs
            reason = f"không kiểm tra được mạng: {exc.__class__.__name__}"
        gate.blocked = quota_blocked or reason
        if reason:
            log.error("command runs refused: %s", reason)


class Gate:
    def __init__(self, docker: Backend, limits: Limits, blocked: str | None = None) -> None:
        self.docker, self.limits = docker, limits
        # Why runs are refused (preflight failed); every other call still works, so runs that
        # exist can still be inspected and removed.
        self.blocked = blocked

    def _owned(self, cid: str) -> bool:
        if not CONTAINER_ID.match(cid):
            return False
        status, _, body = self.docker.call("GET", f"/containers/{cid}/json")
        data = _read(body)
        if status != 200:
            return False
        try:
            labels = (json.loads(data).get("Config") or {}).get("Labels") or {}
        except ValueError:
            return False
        return LABEL in labels

    def handle(self, method: str, target: str, body: Iterator[bytes] | None) -> Reply:
        url = urlsplit(target)
        parts = [p for p in url.path.split("/") if p]
        query = parse_qs(url.query)
        if parts == ["runs"] and method == "POST":
            return self._create(_read(body or iter(())))
        if parts == ["runs"] and method == "GET":
            return self._list()
        if len(parts) < 2 or parts[0] != "runs":
            return _json(404, {"error": "not found"})
        cid = parts[1]
        if not self._owned(cid):
            return _json(404, {"error": "no such run container"})
        action = "/".join(parts[2:])
        return self._act(method, action, cid, query, body)

    def _create(self, raw: bytes) -> Reply:
        if self.blocked:
            return _json(503, {"error": self.blocked})
        if len(raw) > MAX_JSON:
            return _json(413, {"error": "too large"})
        try:
            data = json.loads(raw)
            run_id = int(data["run_id"])
            env = clean_env(data.get("env") or {})
        except (ValueError, KeyError, TypeError) as exc:
            return _json(422, {"error": str(exc)[:200]})
        config = json.dumps(container_config(run_id, env, self.limits)).encode()
        status, _, body = self.docker.call(
            "POST", "/containers/create", config, {"Content-Type": "application/json"}
        )
        return Reply(status, _read(body), {"Content-Type": "application/json"})

    def _list(self) -> Reply:
        filters = quote(json.dumps({"label": [LABEL]}))
        status, _, body = self.docker.call("GET", f"/containers/json?all=1&filters={filters}")
        if status != 200:
            return _json(502, {"error": "docker"})
        rows = [
            {
                "id": c.get("Id"),
                "run_id": (c.get("Labels") or {}).get(LABEL),
                "state": c.get("State"),
                "created": c.get("Created"),
            }
            for c in json.loads(_read(body))
        ]
        return _json(200, rows)

    def _act(
        self,
        method: str,
        action: str,
        cid: str,
        query: dict[str, list[str]],
        body: Iterator[bytes] | None,
    ) -> Reply:
        base = f"/containers/{cid}"
        route: tuple[str, str] | None = {
            ("PUT", "workspace"): ("PUT", f"{base}/archive?path={quote(HOME)}"),
            ("POST", "start"): ("POST", f"{base}/start"),
            ("POST", "wait"): ("POST", f"{base}/wait"),
            ("POST", "kill"): ("POST", f"{base}/kill"),
            ("GET", "logs"): ("GET", f"{base}/logs?follow=1&stdout=1&stderr=1"),
            ("GET", "size"): ("GET", f"{base}/json?size=1"),
            ("DELETE", ""): ("DELETE", f"{base}?force=1&v=1"),
        }.get((method, action))
        if method == "GET" and action == "result":
            name = (query.get("name") or [""])[0]
            if not FILE_NAME.match(name) or name.startswith("."):
                return _json(422, {"error": "bad name"})
            route = ("GET", f"{base}/archive?path={quote(f'{RESULT_DIR}/{name}')}")
        if route is None:
            return _json(404, {"error": "not found"})
        docker_method, docker_path = route
        headers = {"Content-Type": "application/x-tar"} if action == "workspace" else {}
        status, reply_headers, stream = self.docker.call(
            docker_method, docker_path, body if action == "workspace" else None, headers
        )
        if action == "size":
            data = json.loads(_read(stream) or b"{}") if status == 200 else {}
            return _json(status, {"size": int(data.get("SizeRw") or 0)})
        keep = {
            k: v
            for k, v in reply_headers.items()
            if k.lower() in ("content-type", "x-docker-container-path-stat")
        }
        return Reply(status, stream, keep)


# -- the HTTP server on the unix socket --------------------------------------------------------


def _request_body(handler: BaseHTTPRequestHandler) -> Iterator[bytes] | None:
    length = handler.headers.get("Content-Length")
    if length is not None:
        remaining = int(length)

        def fixed() -> Iterator[bytes]:
            nonlocal remaining
            while remaining > 0:
                piece = handler.rfile.read(min(CHUNK, remaining))
                if not piece:
                    return
                remaining -= len(piece)
                yield piece

        return fixed()
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":

        def chunked() -> Iterator[bytes]:
            while True:
                size = int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    handler.rfile.readline()
                    return
                yield handler.rfile.read(size)
                handler.rfile.readline()

        return chunked()
    return None


def make_handler(gate: Gate) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # the body ends when the connection closes

        def _serve(self) -> None:
            try:
                reply = gate.handle(self.command, self.path, _request_body(self))
            except Exception as exc:
                log.warning("gate call failed: %s", exc.__class__.__name__)
                reply = _json(502, {"error": exc.__class__.__name__})
            self.send_response(reply.status)
            for key, value in reply.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if isinstance(reply.body, bytes):
                self.wfile.write(reply.body)
            else:
                for piece in reply.body:
                    self.wfile.write(piece)
                    self.wfile.flush()

        do_GET = do_POST = do_PUT = do_DELETE = _serve

        def log_message(self, format: str, *args: Any) -> None:
            log.info("gate %s", format % args)

    return Handler


BRIDGE_NAME = "cp-commands"  # the host interface the RUNBOOK's iptables rules name


def ensure_network(docker: Backend, name: str) -> None:
    """The command network: a bridge of its own, containers on it cannot talk to each other
    (no inter-container traffic), and a fixed interface name so the host can firewall it."""
    status, _, body = docker.call("GET", f"/networks/{quote(name)}")
    _read(body)
    if status == 200:
        return
    config = {
        "Name": name,
        "Driver": "bridge",
        "Labels": {LABEL: "network"},
        "Options": {
            "com.docker.network.bridge.enable_icc": "false",
            "com.docker.network.bridge.name": BRIDGE_NAME,
        },
    }
    status, _, body = docker.call(
        "POST",
        "/networks/create",
        json.dumps(config).encode(),
        {"Content-Type": "application/json"},
    )
    _read(body)
    if status not in (200, 201, 409):
        raise RuntimeError(f"command network not created (HTTP {status})")


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    path = os.environ.get("COMMAND_GATE_SOCKET", "/run/codepulse-gate/gate.sock")
    if os.path.exists(path):
        os.unlink(path)
    server_class = socketserver.ThreadingUnixStreamServer  # type: ignore[attr-defined,unused-ignore]
    limits = Limits.from_env()
    docker = DockerSocket()
    ensure_network(docker, limits.network)
    limits, quota_blocked = quota_check(docker, limits)
    blocked = quota_blocked or network_check(docker, limits)
    if blocked:
        log.error("command runs refused: %s", blocked)
    gate = Gate(docker, limits, blocked)
    threading.Thread(target=watch_network, args=(gate, quota_blocked), daemon=True).start()
    with server_class(path, make_handler(gate)) as server:
        os.chmod(path, 0o666)  # noqa: S103 - the folder is mounted by the command worker only
        server.daemon_threads = True
        server.serve_forever()


if __name__ == "__main__":
    main()
