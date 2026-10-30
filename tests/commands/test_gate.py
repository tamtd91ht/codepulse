import json
from collections.abc import Iterator
from typing import Any

import pytest

from app.commands.gate import (
    LABEL,
    PROBE_SCRIPT,
    Gate,
    Limits,
    clean_env,
    container_config,
    preflight,
)

CID = "a" * 64
DONE = b"PROBE DONE"
OTHER = "b" * 64


class FakeDocker:
    """Docker answering for one labelled container (CID) and one that is not the gate's."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], Iterator[bytes]]:
        if isinstance(body, Iterator):
            body = b"".join(body)
        self.calls.append((method, path, body))
        data: Any
        if path == f"/containers/{CID}/json":
            data = {"Config": {"Labels": {LABEL: "7"}}}
        elif path == f"/containers/{OTHER}/json":
            data = {"Config": {"Labels": {}}}
        elif path.startswith(f"/containers/{CID}/json?size=1"):
            data = {"SizeRw": 1234}
        elif path == "/containers/create":
            data = {"Id": CID}
        elif path.startswith("/containers/json"):
            data = [{"Id": CID, "Labels": {LABEL: "7"}, "State": "exited", "Created": 1}]
        else:
            data = {}
        return 200, {"Content-Type": "application/json"}, iter([json.dumps(data).encode()])


def _gate() -> tuple[Gate, FakeDocker]:
    docker = FakeDocker()
    return Gate(docker, Limits(image="img:1", network="codepulse-commands")), docker


def _body(reply: Any) -> Any:
    raw = reply.body if isinstance(reply.body, bytes) else b"".join(reply.body)
    return json.loads(raw)


def test_the_gate_builds_the_container_itself() -> None:
    config = container_config(7, {"CLAUDE_CODE_OAUTH_TOKEN": "t"}, Limits(image="img:1"))
    host = config["HostConfig"]
    assert config["Image"] == "img:1" and config["Cmd"] == ["python", "-m", "app.commands.inside"]
    assert config["User"] == "codepulse" and config["Labels"] == {LABEL: "7"}
    assert host["Privileged"] is False and host["CapDrop"] == ["ALL"]
    assert host["Binds"] == [] and host["Mounts"] == [] and host["Devices"] == []
    assert host["SecurityOpt"] == ["no-new-privileges:true"] and host["PidsLimit"] > 0
    assert "CLAUDE_CODE_OAUTH_TOKEN=t" in config["Env"]


@pytest.mark.parametrize(
    "env",
    [{"DATABASE_URL": "x"}, {"LD_PRELOAD": "/x.so"}, {"CLAUDE_CODE_OAUTH_TOKEN": 5}, []],
)
def test_only_allowed_environment_passes(env: Any) -> None:
    with pytest.raises(ValueError):
        clean_env(env)


def test_create_ignores_anything_but_run_id_and_env() -> None:
    gate, docker = _gate()
    body = json.dumps(
        {
            "run_id": 7,
            "env": {"CLAUDE_CODE_OAUTH_TOKEN": "t"},
            "HostConfig": {"Privileged": True, "Binds": ["/:/host"]},
        }
    ).encode()
    reply = gate.handle("POST", "/runs", iter([body]))
    assert reply.status == 200 and _body(reply) == {"Id": CID}
    sent = json.loads(docker.calls[-1][2])
    assert sent["HostConfig"]["Privileged"] is False and sent["HostConfig"]["Binds"] == []
    assert sent["Image"] == "img:1" and sent["HostConfig"]["NetworkMode"] == "codepulse-commands"


def test_a_bad_env_is_refused_before_docker_is_called() -> None:
    gate, docker = _gate()
    body = json.dumps({"run_id": 7, "env": {"DATABASE_URL": "x"}}).encode()
    assert gate.handle("POST", "/runs", iter([body])).status == 422
    assert docker.calls == []


@pytest.mark.parametrize("cid", [OTHER, "../../images", "abc"])
def test_only_the_gates_own_containers_can_be_touched(cid: str) -> None:
    gate, docker = _gate()
    assert gate.handle("POST", f"/runs/{cid}/start", None).status == 404
    assert not any(path.endswith("/start") for _, path, _ in docker.calls)


def test_operations_map_to_fixed_docker_calls() -> None:
    gate, docker = _gate()
    assert gate.handle("POST", f"/runs/{CID}/start", None).status == 200
    assert docker.calls[-1][:2] == ("POST", f"/containers/{CID}/start")
    gate.handle("PUT", f"/runs/{CID}/workspace", iter([b"tar"]))
    assert docker.calls[-1] == (
        "PUT",
        f"/containers/{CID}/archive?path=/home/codepulse",
        b"tar",
    )
    assert _body(gate.handle("GET", f"/runs/{CID}/size", None)) == {"size": 1234}
    gate.handle("GET", f"/runs/{CID}/result?name=report.md", None)
    assert docker.calls[-1][1] == (
        f"/containers/{CID}/archive?path=/home/codepulse/run/result/report.md"
    )
    gate.handle("DELETE", f"/runs/{CID}", None)
    assert docker.calls[-1][1] == f"/containers/{CID}?force=1&v=1"
    # Anything else Docker offers is not reachable through the gate.
    assert gate.handle("POST", f"/runs/{CID}/exec", None).status == 404
    assert gate.handle("GET", "/containers/json", None).status == 404


@pytest.mark.parametrize("name", ["../../etc/passwd", ".env", "a/b", ""])
def test_the_result_name_is_a_plain_file(name: str) -> None:
    gate, _ = _gate()
    assert gate.handle("GET", f"/runs/{CID}/result?name={name}", None).status == 422


def test_the_list_is_the_gates_containers() -> None:
    gate, docker = _gate()
    rows = _body(gate.handle("GET", "/runs", None))
    assert rows == [{"id": CID, "run_id": "7", "state": "exited", "created": 1}]
    assert "label" in docker.calls[-1][1]


def _frame(data: bytes) -> bytes:
    return bytes([1, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


class PreflightDocker:
    """Docker for the start-up checks: whether StorageOpt is accepted, what the probe sees."""

    def __init__(self, quota: bool, probe_out: bytes) -> None:
        self.quota, self.probe_out = quota, probe_out
        self.created: list[dict[str, Any]] = []
        self.removed = 0

    def call(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], Iterator[bytes]]:
        data: Any = {}
        status = 200
        if path == "/containers/create":
            config = json.loads(body)
            if "StorageOpt" in config["HostConfig"] and not self.quota:
                return 500, {}, iter([b'{"message": "--storage-opt is supported only for xfs"}'])
            self.created.append(config)
            data = {"Id": CID}
        elif path.startswith("/networks/"):
            data = {"IPAM": {"Config": [{"Subnet": "172.30.0.0/16", "Gateway": "172.30.0.1"}]}}
        elif "/logs" in path:
            return 200, {}, iter([_frame(self.probe_out)])
        elif method == "DELETE":
            self.removed += 1
        return status, {}, iter([json.dumps(data).encode()])


def test_preflight_passes_with_a_quota_and_a_closed_host() -> None:
    docker = PreflightDocker(quota=True, probe_out=DONE)
    limits, blocked = preflight(docker, Limits(disk_mb=1000))
    assert blocked is None and limits.quota
    probe = docker.created[-1]
    assert probe["Cmd"][-8:] == ["172.30.0.1", "22", "80", "443", "3000", "5432", "6379", "8000"]
    assert probe["HostConfig"]["StorageOpt"] == {"size": "1000M"}
    assert docker.removed == 2  # both test containers are gone


def test_no_disk_quota_refuses_runs_unless_explicitly_allowed() -> None:
    _, blocked = preflight(PreflightDocker(quota=False, probe_out=DONE), Limits())
    assert blocked is not None and "XFS" in blocked
    limits, blocked = preflight(
        PreflightDocker(quota=False, probe_out=DONE), Limits(allow_no_quota=True)
    )
    assert blocked is None and limits.quota is False
    assert "StorageOpt" not in container_config(1, {}, limits)["HostConfig"]


def test_a_reachable_host_refuses_runs() -> None:
    docker = PreflightDocker(quota=True, probe_out=b"OPEN 6379\nOPEN 5432\n" + DONE)
    _, blocked = preflight(docker, Limits())
    assert blocked is not None and "6379, 5432" in blocked and "iptables" in blocked
    _, blocked = preflight(
        PreflightDocker(quota=True, probe_out=b"OPEN 6379\n"), Limits(allow_open_network=True)
    )
    assert blocked is None


def test_a_probe_that_learns_nothing_refuses_runs() -> None:
    # It did not run to the end (crashed, killed): fail closed.
    _, blocked = preflight(PreflightDocker(quota=True, probe_out=b""), Limits())
    assert blocked is not None and "không chạy hết" in blocked


def test_a_refused_connection_counts_as_open() -> None:
    assert "ConnectionRefusedError:\n        print('OPEN', port)" in PROBE_SCRIPT


def test_a_blocked_gate_creates_nothing_but_still_cleans_up() -> None:
    docker = FakeDocker()
    gate = Gate(docker, Limits(), blocked="no quota")
    body = json.dumps({"run_id": 7, "env": {}}).encode()
    reply = gate.handle("POST", "/runs", iter([body]))
    assert reply.status == 503 and _body(reply) == {"error": "no quota"}
    assert gate.handle("DELETE", f"/runs/{CID}", None).status == 200
