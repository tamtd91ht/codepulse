import io
import tarfile
from pathlib import Path, PurePosixPath

import httpx
import pytest
import respx

from app.collector.gitlab.client import ArchiveTooLarge, GitLabClient, GitLabError
from app.review.checkout import MAX_FILE_BYTES, CheckoutError, checkout, extract, safe_relative

BASE = "https://gitlab.example.com"
API = f"{BASE}/api/v4"


def make_archive(entries: dict[str, bytes | None], top: str = "demo-p-abc123") -> bytes:
    """A GitLab-style tar.gz: everything under `<project>-<sha>/`; None = symlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(f"{top}/{name}" if not name.startswith("/") else name)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_safe_relative_strips_the_top_dir_and_refuses_odd_paths() -> None:
    assert safe_relative("demo-abc/app/main.py") == PurePosixPath("app/main.py")
    assert safe_relative("demo-abc") is None
    assert safe_relative("demo-abc/../x") is None
    assert safe_relative("demo-abc/.git/config") is None
    assert safe_relative("demo-abc/node_modules/x/index.js") is None


def test_extract_keeps_only_regular_safe_files(tmp_path: Path) -> None:
    archive = tmp_path / "a.tgz"
    archive.write_bytes(
        make_archive(
            {
                "README.md": b"# demo",
                "app/main.py": b"print(1)",
                ".env": b"SECRET=1",
                "deploy/.env.production": b"SECRET=2",
                "certs/Server.PEM": b"---",
                "config/secrets/key.pem": b"---",
                "link": None,
                "big.bin": b"x" * (MAX_FILE_BYTES + 1),
                "/abs/evil": b"no",
                "sub/../escape.txt": b"no",
            }
        )
    )
    root = tmp_path / "src"
    root.mkdir()
    stats = extract(archive, root, [".env*", "**/secrets/**", "*.pem"])
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == [
        "README.md",
        "app/main.py",
    ]
    assert stats.files == 2
    assert stats.skipped == {"sensitive": 4, "not_file": 1, "too_large": 1, "path": 2}


def test_extract_stops_at_the_total_limits(tmp_path: Path) -> None:
    archive = tmp_path / "many.tgz"
    archive.write_bytes(make_archive({f"f{i}.txt": b"x" * 10 for i in range(30)}))
    root = tmp_path / "src"
    root.mkdir()
    with pytest.raises(CheckoutError):
        extract(archive, root, [], max_files=10)
    with pytest.raises(CheckoutError):
        extract(archive, root, [], max_total_bytes=100)
    assert extract(archive, root, []).files == 30


def test_extract_rejects_a_broken_archive(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tgz"
    bad.write_bytes(b"not a tar")
    with pytest.raises(CheckoutError):
        extract(bad, tmp_path, [])


@respx.mock
def test_checkout_downloads_extracts_and_cleans_up() -> None:
    respx.get(f"{API}/projects/1001/repository/archive.tar.gz").mock(
        return_value=httpx.Response(200, content=make_archive({"a.py": b"x = 1"}))
    )
    with GitLabClient(BASE, "t") as client:
        with checkout(client, 1001, "abc", max_archive_bytes=10_000, sensitive_paths=[]) as (
            root,
            stats,
        ):
            assert (root / "a.py").read_text() == "x = 1" and stats.files == 1
            kept = root
        assert not kept.exists()


@respx.mock
def test_checkout_refuses_large_archives_and_reports_gitlab_errors() -> None:
    route = respx.get(f"{API}/projects/1001/repository/archive.tar.gz")
    route.mock(return_value=httpx.Response(200, content=b"x" * 2000))
    with GitLabClient(BASE, "t") as client:
        with (
            pytest.raises(ArchiveTooLarge),
            checkout(client, 1001, "abc", max_archive_bytes=1000, sensitive_paths=[]),
        ):
            pass
        route.mock(return_value=httpx.Response(404, json={"message": "404"}))
        with (
            pytest.raises(GitLabError),
            checkout(client, 1001, "abc", max_archive_bytes=1000, sensitive_paths=[]),
        ):
            pass
