import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.commands import container, gate, inside, workspace
from app.commands.runner import Cancelled


def _frame(kind: int, data: bytes) -> bytes:
    return bytes([kind, 0, 0, 0]) + struct.pack(">I", len(data)) + data


def test_docker_log_frames_are_demultiplexed_across_chunks() -> None:
    raw = _frame(1, b"hello\n") + _frame(2, b"oops") + _frame(1, b"world\n")
    chunks = [raw[:3], raw[3:11], raw[11:]]
    assert list(gate.frames(chunks)) == [(1, b"hello\n"), (2, b"oops"), (1, b"world\n")]


def _tar(members: list[tuple[str, bytes | None, bytes]]) -> bytes:
    """(name, data or None for a symlink, link target)"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, data, target in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, target.decode()
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _file(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "in.tar"
    path.write_bytes(data)
    return path


def test_only_the_announced_regular_file_is_taken(tmp_path: Path) -> None:
    data = _tar([("result/x.txt", b"other", b""), ("result/report.md", b"# hi", b"")])
    archive = _file(tmp_path, data)
    assert container.extract_result(archive, "report.md", tmp_path / "report.md", 100) == 4
    assert (tmp_path / "report.md").read_bytes() == b"# hi"
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.parametrize(
    "members",
    [
        [("result/report.md", None, b"/proc/1/environ")],  # a link to the worker's secrets
        [("result/report.md", b"x" * 200, b"")],  # over the cap
        [("result/other.md", b"x", b"")],  # not the announced file
    ],
)
def test_anything_else_is_refused(tmp_path: Path, members: Any) -> None:
    with pytest.raises(container.ContainerError):
        container.extract_result(
            _file(tmp_path, _tar(members)), "report.md", tmp_path / "report.md", 100
        )
    assert not (tmp_path / "report.md").exists()


def test_the_workspace_tar_holds_the_run_folder_and_no_links(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "app.py").write_text("x", encoding="utf-8")
    with contextlib.suppress(OSError):
        (work / "link").symlink_to("/etc/passwd")
    dest = tmp_path / "w.tar"
    container.workspace_tar(work, {"argv": ["claude"]}, dest)
    with tarfile.open(dest) as tar:
        names = tar.getnames()
        request = tar.extractfile("run/request.json")
        assert request is not None and json.loads(request.read()) == {"argv": ["claude"]}
        assert tar.getmember("run").isdir()
    assert "run/work/src/app.py" in names and "run/work/link" not in names


class FakeGate:
    def __init__(self, lines: list[bytes], size: int = 0) -> None:
        self.lines = lines
        self.calls: list[str] = []
        self.env: dict[str, str] = {}
        self.archive: bytes = b""
        self.disk = size

    def create(self, run_id: int, env: dict[str, str]) -> str:
        self.calls.append(f"create {run_id}")
        self.env = env
        return "c1"

    def put_workspace(self, cid: str, tar: Path) -> None:
        self.calls.append("put")

    def start(self, cid: str) -> None:
        self.calls.append("start")

    def logs(self, cid: str) -> Iterator[bytes]:
        for line in self.lines:
            yield _frame(1, line)

    def wait(self, cid: str) -> int:
        self.calls.append("wait")
        return 0

    def kill(self, cid: str) -> None:
        self.calls.append("kill")

    def size(self, cid: str) -> int:
        return self.disk

    def result(self, cid: str, name: str, max_bytes: int, dest: Path) -> None:
        self.calls.append(f"result {name}")
        dest.write_bytes(self.archive)

    def remove(self, cid: str) -> None:
        self.calls.append("remove")


def _boxed(tmp_path: Path, gate: FakeGate, **extra: Any) -> container.ContainerRun:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return container.ContainerRun(
        gate,  # type: ignore[arg-type]
        run_id=9,
        work=work,
        tar_path=tmp_path / "w.tar",
        max_output_bytes=1000,
        max_disk_bytes=extra.get("max_disk_bytes", 10_000),
        cancelled=extra.get("cancelled", lambda: False),
        kill_after=extra.get("kill_after", lambda _delay, _action: None),
        watch_every_s=extra.get("watch_every_s", 3600),
    )


def _call(boxed: container.ContainerRun, on_line: Callable[[str], None]) -> tuple[int, str]:
    return boxed(
        ["claude", "-p"],
        stdin="/report",
        cwd="/home/codepulse/run/work",
        env={
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x",
            "DATABASE_URL": "postgres://secret",
            "PATH": "/worker/bin",
            "CODEPULSE_OUTPUT_DIR": "/home/codepulse/run/out",
        },
        timeout_s=60,
        on_line=on_line,
    )


def test_only_the_token_and_output_dir_reach_the_container(tmp_path: Path) -> None:
    summary = {"type": inside.RESULT_TYPE, "name": "report.md", "files": []}
    gate = FakeGate([b'{"type": "system"}\n', json.dumps(summary).encode() + b"\n"])
    boxed = _boxed(tmp_path, gate)
    lines: list[str] = []
    assert _call(boxed, lines.append) == (0, "")
    assert gate.env == {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x",
        "CODEPULSE_OUTPUT_DIR": "/home/codepulse/run/out",
    }
    assert gate.calls[:3] == ["create 9", "put", "start"]
    # The result line is the worker's, not the live log's.
    assert lines == ['{"type": "system"}\n'] and boxed.summary["name"] == "report.md"
    assert not (tmp_path / "w.tar").exists()  # the clone left this machine's disk

    gate.archive = _tar([("report.md", b"# done", b"")])
    assert boxed.fetch(tmp_path / "runs" / "9") == ("report.md", 6)
    assert "result report.md" in gate.calls
    boxed.close()
    assert gate.calls[-1] == "remove"


def test_cancel_from_the_live_log_kills_the_container(tmp_path: Path) -> None:
    gate = FakeGate([b'{"type": "system"}\n'])

    def on_line(_: str) -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        _call(_boxed(tmp_path, gate), on_line)
    assert "kill" in gate.calls


def test_cancel_works_while_the_command_is_silent(tmp_path: Path) -> None:
    seen = threading.Event()

    class SlowLogs(FakeGate):
        def logs(self, cid: str) -> Iterator[bytes]:
            seen.wait(2)  # until the watcher killed it
            yield from ()

        def kill(self, cid: str) -> None:
            super().kill(cid)
            seen.set()

    slow = SlowLogs([])
    boxed = _boxed(tmp_path, slow, cancelled=lambda: True, watch_every_s=0.01)
    with pytest.raises(Cancelled):
        _call(boxed, lambda _: None)
    assert "kill" in slow.calls


def test_a_container_filling_the_disk_is_stopped(tmp_path: Path) -> None:
    seen = threading.Event()

    class Big(FakeGate):
        def logs(self, cid: str) -> Iterator[bytes]:
            seen.wait(2)
            yield from ()

        def kill(self, cid: str) -> None:
            super().kill(cid)
            seen.set()

    big = Big([], size=50_000)
    boxed = _boxed(tmp_path, big, max_disk_bytes=10_000, watch_every_s=0.01)
    with pytest.raises(container.DiskExceeded):
        _call(boxed, lambda _: None)


def test_the_deadline_kills_it_and_is_a_timeout(tmp_path: Path) -> None:
    gate = FakeGate([])
    boxed = _boxed(tmp_path, gate, kill_after=lambda _delay, action: action())
    with pytest.raises(subprocess.TimeoutExpired):
        _call(boxed, lambda _: None)
    assert "kill" in gate.calls


def test_inside_runs_the_cli_and_packs_one_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "run"
    (root / "work" / "docs").mkdir(parents=True)
    (root / "work" / "docs" / "old.md").write_text("old", encoding="utf-8")
    script = (
        "import json, os, pathlib, sys\n"
        "out = pathlib.Path(os.environ['CODEPULSE_OUTPUT_DIR'])\n"
        "out.mkdir(exist_ok=True)\n"
        "(out / 'report.md').write_text(sys.stdin.read())\n"
        "pathlib.Path('docs/new.md').write_text('new')\n"
        "print(json.dumps({'type': 'result'}), flush=True)\n"
    )
    request = {
        "argv": [sys.executable, "-c", script],
        "stdin": "/report W43",
        "timeout_s": 30,
        "max_output_bytes": 10_000,
    }
    (root / "request.json").write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setenv("CODEPULSE_OUTPUT_DIR", str(root / "out"))
    emitted: list[str] = []
    summary = inside.run(root, emit=emitted.append)
    assert not (root / "request.json").exists()
    assert json.loads(emitted[0]) == {"type": "result"}
    assert json.loads(emitted[-1])["type"] == inside.RESULT_TYPE
    assert summary["code"] == 0 and summary["name"] == workspace.ZIP_NAME
    assert sorted(f["name"] for f in summary["files"]) == ["report.md", "workspace/docs/new.md"]
    assert (root / inside.RESULT_DIR / workspace.ZIP_NAME).is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_history_is_one_commit_of_the_cleaned_tree(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("a", encoding="utf-8")
    workspace.reset_history("git", work, dict(os.environ))
    log = subprocess.run(  # noqa: S603 - a test repo
        ["git", "-C", str(work), "log", "--oneline"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1
