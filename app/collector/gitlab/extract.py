"""Extract step: raw_gitlab rows -> typed GitLab records (the in-memory tool layer).

docs/specs/03 defines no gl_* tables, so the tool layer is these dataclasses; raw rows stay the
replayable source of truth.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.collector.gitlab import collect as ep
from app.models import RawGitlab

PUSH_NOTE = re.compile(r"^added \d+ (new )?commits?\b")


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class GlUser:
    id: int
    username: str
    name: str | None


@dataclass
class GlFileStat:
    path: str
    additions: int
    deletions: int


@dataclass
class GlCommit:
    sha: str
    author_email: str
    author_name: str | None
    authored_at: datetime | None
    message: str | None
    parent_ids: list[str]
    additions: int | None
    deletions: int | None
    files: list[GlFileStat] | None = None  # None until a commit_diff row is seen

    @property
    def is_merge(self) -> bool:
        return len(self.parent_ids) > 1


@dataclass
class GlNote:
    id: int
    author: GlUser
    body: str
    system: bool
    created_at: datetime | None

    @property
    def is_push(self) -> bool:
        return self.system and bool(PUSH_NOTE.match(self.body))


@dataclass
class GlMergeRequest:
    iid: int
    title: str | None
    author: GlUser
    state: str | None
    source_branch: str | None
    target_branch: str | None
    head_sha: str | None
    created_at: datetime | None
    merged_at: datetime | None
    closed_at: datetime | None
    commits: list[GlCommit] = field(default_factory=list)
    notes: list[GlNote] = field(default_factory=list)
    files: list[GlFileStat] | None = None


@dataclass
class GlPipeline:
    id: int
    sha: str | None
    ref: str | None
    status: str | None
    duration_sec: int | None
    created_at: datetime | None
    finished_at: datetime | None


@dataclass
class Extracted:
    default_branch: str | None = None
    commits: dict[str, GlCommit] = field(default_factory=dict)
    merge_requests: dict[int, GlMergeRequest] = field(default_factory=dict)
    pipelines: dict[int, GlPipeline] = field(default_factory=dict)


def _user(raw: dict[str, Any]) -> GlUser:
    return GlUser(id=int(raw["id"]), username=raw["username"], name=raw.get("name"))


def _commit(raw: dict[str, Any]) -> GlCommit:
    stats = raw.get("stats") or {}
    return GlCommit(
        sha=raw["id"],
        author_email=(raw.get("author_email") or "").strip().lower(),
        author_name=raw.get("author_name"),
        authored_at=parse_ts(raw.get("authored_date") or raw.get("created_at")),
        message=raw.get("message") or raw.get("title"),
        parent_ids=list(raw.get("parent_ids") or []),
        additions=stats.get("additions"),
        deletions=stats.get("deletions"),
    )


def _files(payload: list[dict[str, Any]]) -> list[GlFileStat]:
    return [
        GlFileStat(
            path=f.get("new_path") or f.get("old_path") or "",
            additions=int(f.get("additions") or 0),
            deletions=int(f.get("deletions") or 0),
        )
        for f in payload
    ]


def _note(raw: dict[str, Any]) -> GlNote:
    return GlNote(
        id=int(raw["id"]),
        author=_user(raw["author"]),
        body=raw.get("body") or "",
        system=bool(raw.get("system")),
        created_at=parse_ts(raw.get("created_at")),
    )


def extract(rows: Iterable[RawGitlab]) -> Extracted:
    """Fold raw rows (in insertion order) into records; a later row for the same key wins."""
    out = Extracted()
    commit_files: dict[str, list[GlFileStat]] = {}
    mr_parts: dict[int, dict[str, Any]] = {}

    for row in rows:
        params = row.params or {}
        payload = row.payload
        match row.endpoint:
            case ep.EP_PROJECT:
                out.default_branch = payload.get("default_branch")
            case ep.EP_COMMIT:
                out.commits[payload["id"]] = _commit(payload)
            case ep.EP_COMMIT_DIFF:
                commit_files[params["sha"]] = _files(payload)
            case ep.EP_MR:
                out.merge_requests[payload["iid"]] = GlMergeRequest(
                    iid=int(payload["iid"]),
                    title=payload.get("title"),
                    author=_user(payload["author"]),
                    state=payload.get("state"),
                    source_branch=payload.get("source_branch"),
                    target_branch=payload.get("target_branch"),
                    head_sha=payload.get("sha"),
                    created_at=parse_ts(payload.get("created_at")),
                    merged_at=parse_ts(payload.get("merged_at")),
                    closed_at=parse_ts(payload.get("closed_at")),
                )
            case ep.EP_MR_COMMITS | ep.EP_MR_NOTES | ep.EP_MR_DIFFS:
                mr_parts.setdefault(int(params["iid"]), {})[row.endpoint] = payload
            case ep.EP_PIPELINE:
                out.pipelines[int(payload["id"])] = GlPipeline(
                    id=int(payload["id"]),
                    sha=payload.get("sha"),
                    ref=payload.get("ref"),
                    status=payload.get("status"),
                    duration_sec=payload.get("duration"),
                    created_at=parse_ts(payload.get("created_at")),
                    finished_at=parse_ts(payload.get("finished_at")),
                )

    for sha, files in commit_files.items():
        if sha in out.commits:
            out.commits[sha].files = files
    for iid, parts in mr_parts.items():
        mr = out.merge_requests.get(iid)
        if mr is None:
            continue
        mr.commits = [_commit(c) for c in parts.get(ep.EP_MR_COMMITS, [])]
        notes = [_note(n) for n in parts.get(ep.EP_MR_NOTES, [])]
        mr.notes = sorted(notes, key=lambda n: (n.created_at is None, n.created_at, n.id))
        if ep.EP_MR_DIFFS in parts:
            mr.files = _files(parts[ep.EP_MR_DIFFS])
    return out


def first_review_at(mr: GlMergeRequest) -> datetime | None:
    """First non-system note written by someone other than the MR author (docs/specs/04)."""
    for note in mr.notes:
        if not note.system and note.author.id != mr.author.id:
            return note.created_at
    return None


def review_rounds(mr: GlMergeRequest) -> int:
    """Number of pushes that follow at least one review note since the previous push."""
    rounds, reviewed = 0, False
    for note in mr.notes:
        if note.is_push:
            if reviewed:
                rounds += 1
            reviewed = False
        elif not note.system and note.author.id != mr.author.id:
            reviewed = True
    return rounds
