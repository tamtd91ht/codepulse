"""A read-only copy of the repository at one sha, for the deep review (docs/specs/18).

The copy comes from GitLab's archive API through the existing token (no git binary, no token
in a URL). Extraction is defensive: no path traversal, no symlinks, no `.git`, no file above
MAX_FILE_BYTES, and nothing matching SENSITIVE_PATHS (a committed `.env` must not end up in
front of the model). Everything is deleted when the context manager exits.
"""

import logging
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.diffstat import is_ignored

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024  # 1 GiB extracted: a gzip bomb must not fill the disk
MAX_FILES = 50_000
SKIP_DIRS = frozenset({".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__"})


class CheckoutError(Exception):
    """The repository copy could not be made (too large, bad archive)."""


@dataclass
class ExtractStats:
    files: int = 0
    bytes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def safe_relative(name: str) -> PurePosixPath | None:
    """GitLab archives wrap everything in `<project>-<sha>/`; strip it and refuse odd paths."""
    path = PurePosixPath(name)
    if path.is_absolute():
        return None
    parts = path.parts
    if len(parts) < 2:
        return None  # the top-level directory itself
    rel = PurePosixPath(*parts[1:])
    if rel.is_absolute() or any(p in ("", ".", "..") for p in rel.parts):
        return None
    if rel.parts[0] in SKIP_DIRS or any(p in SKIP_DIRS for p in rel.parts[:-1]):
        return None
    return rel


def is_sensitive(rel: PurePosixPath, patterns: list[str]) -> bool:
    """SENSITIVE_PATHS against the full path, every tail of it and the basename, case-folded:
    `.env*` must catch `deploy/.env.production`, `*.pem` must catch `certs/Server.PEM`."""
    lowered = [p.casefold() for p in patterns]
    parts = [p.casefold() for p in rel.parts]
    tails = ["/".join(parts[i:]) for i in range(len(parts))]
    return any(is_ignored(tail, lowered) for tail in tails)


def extract(
    archive: Path,
    root: Path,
    sensitive_paths: list[str],
    *,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_files: int = MAX_FILES,
) -> ExtractStats:
    stats = ExtractStats()
    root_resolved = root.resolve()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _extract_members(
                tar, root, root_resolved, sensitive_paths, stats, max_total_bytes, max_files
            )
    except (tarfile.TarError, OSError) as exc:
        raise CheckoutError(f"archive unreadable: {exc.__class__.__name__}") from None
    return stats


def _extract_members(
    tar: tarfile.TarFile,
    root: Path,
    root_resolved: Path,
    sensitive_paths: list[str],
    stats: ExtractStats,
    max_total_bytes: int,
    max_files: int,
) -> None:
    for member in tar:
        rel = safe_relative(member.name)
        if rel is None:
            stats.skip("path")
            continue
        if not member.isfile():
            stats.skip("not_file")  # directories are created on demand; symlinks never
            continue
        if member.size > MAX_FILE_BYTES:
            stats.skip("too_large")
            continue
        if is_sensitive(rel, sensitive_paths):
            stats.skip("sensitive")
            continue
        if stats.files + 1 > max_files or stats.bytes + member.size > max_total_bytes:
            raise CheckoutError("repository copy exceeds the extraction limits")
        dest = (root / Path(*rel.parts)).resolve()
        if root_resolved not in dest.parents:
            stats.skip("path")
            continue
        source = tar.extractfile(member)
        if source is None:
            stats.skip("not_file")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with source, open(dest, "wb") as out:
                shutil.copyfileobj(source, out)
        except OSError:  # reserved names on Windows (con, nul), odd encodings: skip the file
            stats.skip("unwritable")
            continue
        stats.files += 1
        stats.bytes += member.size


@contextmanager
def checkout(
    client: GitLabClient,
    project_external_id: int,
    sha: str,
    *,
    max_archive_bytes: int,
    sensitive_paths: list[str],
) -> Iterator[tuple[Path, ExtractStats]]:
    """Yield (root, stats) of a temporary read-only copy; removed afterwards whatever happens."""
    tmp = Path(tempfile.mkdtemp(prefix="codepulse-deep-"))
    try:
        archive = tmp / "repo.tar.gz"
        client.archive(project_external_id, sha, archive, max_archive_bytes)
        root = tmp / "src"
        root.mkdir()
        stats = extract(archive, root, sensitive_paths)
        archive.unlink(missing_ok=True)
        log.info(
            "deep review checkout ready",
            extra={"files": stats.files, "bytes": stats.bytes, "skipped": stats.skipped},
        )
        yield root, stats
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
