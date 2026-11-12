"""Reading what an admin pastes to add a repo: a GitLab link, a clone URL, a path or a project ID.

1234                                       -> project ID
vihat/omicrm/api                           -> path, on the only (or the chosen) connection
https://gitlab.vihat.vn/vihat/omicrm/api   -> host + path; the host picks the connection
https://gitlab.vihat.vn/vihat/api/-/merge_requests/7, .../api.git, git@gitlab.vihat.vn:vihat/api.git
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from app.models import Connection

_SSH = re.compile(r"^(?:ssh://)?[\w.+-]+@(?P<host>[\w.-]+)(?::\d+)?[:/](?P<path>.+)$")


class RepoRefError(ValueError):
    """The text is not a repo reference, or does not say which connection it belongs to."""


@dataclass(frozen=True)
class RepoRef:
    host: str | None  # lower-case host name when the text was a link
    external_id: int | None
    path: str | None  # group/sub/repo
    url_path: str = ""  # the link's full path, so a GitLab under a sub-path can be matched
    ssh: bool = False  # git@host:group/repo carries no web sub-path


def _clean_path(path: str) -> str:
    path = unquote(path).split("/-/", 1)[0].strip("/")
    path = path.removesuffix(".git").strip("/")
    return path


def parse_repo_ref(text: str) -> RepoRef:
    raw = text.strip()
    if not raw:
        raise RepoRefError("nhập link GitLab, đường dẫn hoặc project ID của repo")
    if raw.isdigit():
        if int(raw) <= 0:
            raise RepoRefError("project ID phải lớn hơn 0")
        return RepoRef(None, int(raw), None)
    ssh = _SSH.match(raw)
    if ssh and "://" not in raw.removeprefix("ssh://"):
        path = _clean_path(ssh["path"])
        if "/" not in path:
            raise RepoRefError("clone URL phải trỏ tới một repo, dạng git@host:group/repo.git")
        return RepoRef(ssh["host"].lower(), None, path, "/" + path, ssh=True)
    if "://" in raw:
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise RepoRefError("link không hợp lệ: chỉ nhận link http(s) hoặc clone URL ssh")
        url_path = unquote(parts.path).split("/-/", 1)[0].rstrip("/").removesuffix(".git")
        path = _clean_path(parts.path)
        if "/" not in path:
            raise RepoRefError("link phải trỏ tới một repo, dạng https://gitlab…/group/repo")
        return RepoRef(parts.hostname.lower(), None, path, url_path)
    path = _clean_path(raw)
    if "/" not in path or " " in path:
        raise RepoRefError("nhập link GitLab, đường dẫn dạng group/repo hoặc project ID")
    # Maybe gitlab.vihat.vn/group/repo without a scheme: decided against the connections,
    # since a group name may hold dots too.
    return RepoRef(None, None, path)


def _base(connection: Connection) -> tuple[str, str]:
    parts = urlsplit(connection.base_url)
    return (parts.hostname or "").lower(), parts.path.rstrip("/")


def resolve_repo_ref(
    ref: RepoRef, connections: Sequence[Connection], chosen: Connection | None
) -> tuple[Connection, int | str]:
    """The connection the repo lives on and what to ask GitLab for (an ID or a path)."""
    if ref.host is None and ref.path and ref.path.count("/") >= 2:
        first = ref.path.split("/", 1)[0].lower()
        if any(_base(c)[0] == first for c in ([chosen] if chosen else connections)):
            ref = parse_repo_ref(f"https://{ref.path}")
    if ref.host is None:
        if chosen is not None:
            connection = chosen
        elif len(connections) == 1:
            connection = connections[0]
        elif not connections:
            raise RepoRefError("chưa có kết nối GitLab nào")
        else:
            raise RepoRefError(
                "có nhiều kết nối GitLab: chọn kết nối, hoặc dán link đầy đủ của repo"
            )
        return connection, ref.external_id if ref.external_id is not None else ref.path or ""

    candidates = [c for c in ([chosen] if chosen else connections) if _base(c)[0] == ref.host]
    if not candidates:
        if chosen is not None:
            raise RepoRefError(f"link thuộc {ref.host}, không phải kết nối {chosen.name}")
        raise RepoRefError(f"chưa có kết nối GitLab nào tới {ref.host}")
    if ref.ssh:
        if len(candidates) > 1 and chosen is None:
            raise RepoRefError(f"có {len(candidates)} kết nối tới {ref.host}: chọn một kết nối")
        return candidates[0], ref.path or ""
    # A GitLab served under a sub-path (https://host/gitlab): drop that prefix from the link.
    best = max(candidates, key=lambda c: len(_base(c)[1]) if _url_under(ref, c) else -1)
    if not _url_under(ref, best):
        raise RepoRefError(f"link không nằm dưới {best.base_url}")
    hosted = [c for c in candidates if _base(c)[1] == _base(best)[1]]
    if len(hosted) > 1 and chosen is None:
        raise RepoRefError(f"có {len(hosted)} kết nối tới {ref.host}: chọn một kết nối")
    prefix = _base(best)[1]
    path = _clean_path(ref.url_path[len(prefix) :])
    if "/" not in path:
        raise RepoRefError("link phải trỏ tới một repo, dạng https://gitlab…/group/repo")
    return best, path


def _url_under(ref: RepoRef, connection: Connection) -> bool:
    if ref.ssh:
        return True
    prefix = _base(connection)[1]
    return not prefix or ref.url_path == prefix or ref.url_path.startswith(prefix + "/")
