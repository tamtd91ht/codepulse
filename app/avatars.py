"""Avatars of people (Admin > Người & Team, user request 2026-11-03).

One file per person in AVATAR_DIR, named "<user id>.<ext>": a new upload replaces the old one,
whatever its format. The type comes from the file's own bytes, never from the client's
Content-Type or file name, and only PNG, JPEG and WebP are kept.
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path

MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


class AvatarError(ValueError):
    pass


def sniff(data: bytes) -> str | None:
    """The extension of a PNG, JPEG or WebP image from its first bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def folder(avatar_dir: str) -> Path:
    path = Path(avatar_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(avatar_dir: str, user_id: int, data: bytes, max_kb: int) -> str:
    """Write `data` as the person's avatar; returns its file name. Files of the other formats
    stay until the caller has committed the new name (then `remove(..., keep=name)`), so a
    failed commit never leaves the stored name pointing at a deleted file."""
    if not data:
        raise AvatarError("empty file")
    if len(data) > max_kb * 1024:
        raise AvatarError(f"the image is larger than {max_kb} KB")
    ext = sniff(data)
    if ext is None:
        raise AvatarError("only PNG, JPEG or WebP images")
    target = folder(avatar_dir)
    name = f"{user_id}.{ext}"
    # Write next to the target, then rename: a reader never sees half a file.
    fd, tmp = tempfile.mkstemp(dir=target, prefix=f".{user_id}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return name


def remove(avatar_dir: str, user_id: int, keep: str | None = None) -> None:
    """Delete the person's avatar files (every format), but `keep`."""
    target = Path(avatar_dir)
    for ext in MEDIA_TYPES:
        name = f"{user_id}.{ext}"
        if name != keep:
            (target / name).unlink(missing_ok=True)


def avatar_url(user_id: int, file_name: str | None, updated_at: datetime | None) -> str | None:
    """Where the dashboard loads a person's avatar; the version busts the browser cache."""
    if not file_name:
        return None
    version = (
        int(updated_at.timestamp() * 1000) if updated_at else 0
    )  # ms: two quick uploads differ
    return f"/api/v1/users/{user_id}/avatar?v={version}"


def path_of(avatar_dir: str, file_name: str) -> Path | None:
    """The stored file, if `file_name` is one of ours and still exists."""
    stem, _, ext = file_name.partition(".")
    if not stem.isdigit() or ext not in MEDIA_TYPES:
        return None  # never a path from elsewhere, whatever the column holds
    path = Path(avatar_dir) / f"{int(stem)}.{ext}"
    return path if path.is_file() else None
