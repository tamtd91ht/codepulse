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
# docs/specs/25 §4.1: GitLab's default closing pattern, other #n mentions, a branch "123-…".
CLOSING = re.compile(
    r"\b(?:clos(?:e[sd]?|ing)|fix(?:e[sd]|ing)?|resolv(?:e[sd]?|ing)|implement(?:s|ed|ing)?)"
    r"\s*:?\s+((?:#\d+(?:\s*(?:,|and)\s*)?)+)",
    re.IGNORECASE,
)
MENTION = re.compile(r"(?<![\w/&])#(\d+)\b")
BRANCH_ISSUE = re.compile(r"^(?:[\w.-]+/)?(\d+)[-_]")
TASK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]", re.MULTILINE)


def issue_refs(text: str | None) -> tuple[list[int], list[int]]:
    """(issues the text closes, other issues it mentions) of the same repo."""
    if not text:
        return [], []
    closes: list[int] = []
    for match in CLOSING.finditer(text):
        closes += [int(n) for n in re.findall(r"#(\d+)", match.group(1))]
    mentions = [int(n) for n in MENTION.findall(text) if int(n) not in closes]
    return sorted(set(closes)), sorted(set(mentions))


def branch_issue(branch: str | None) -> int | None:
    match = BRANCH_ISSUE.match(branch or "")
    return int(match.group(1)) if match else None


def tasks(text: str | None) -> tuple[int, int]:
    """(checklist items, ticked ones) of a description."""
    marks = TASK.findall(text or "")
    return len(marks), sum(1 for m in marks if m in "xX")


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
    merge_commit_sha: str | None = None
    squash_commit_sha: str | None = None
    labels: list[str] = field(default_factory=list)
    tasks_total: int = 0
    tasks_done: int = 0
    closes_iids: list[int] = field(default_factory=list)
    mention_iids: list[int] = field(default_factory=list)
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
    source: str | None = None
    coverage: float | None = None
    tests: dict[str, int] | None = None  # None until a pipeline_tests row is seen


@dataclass
class GlDeployment:
    id: int
    environment: str
    status: str
    sha: str | None
    ref: str | None
    created_at: datetime | None
    finished_at: datetime | None
    mr_iids: list[int] | None = None  # None until a deployment_mrs row is seen


@dataclass
class GlIncident:
    id: int
    iid: int
    title: str | None
    state: str | None
    labels: list[str]
    web_url: str | None
    created_at: datetime | None
    closed_at: datetime | None
    fix_mr_iids: list[int] | None = None  # None until an incident_fixes row is seen


@dataclass
class GlIssue:
    id: int
    iid: int
    title: str | None
    state: str | None
    labels: list[str]
    assignees: list[GlUser]
    milestone: str | None
    due_date: str | None
    web_url: str | None
    created_at: datetime | None
    closed_at: datetime | None
    time_estimate_s: int | None
    time_spent_s: int | None
    closed_by_iids: list[int] | None = None  # None until an issue_closed_by row is seen


@dataclass
class Extracted:
    default_branch: str | None = None
    commits: dict[str, GlCommit] = field(default_factory=dict)
    merge_requests: dict[int, GlMergeRequest] = field(default_factory=dict)
    pipelines: dict[int, GlPipeline] = field(default_factory=dict)
    environment_tiers: dict[str, str | None] = field(default_factory=dict)
    deployments: dict[int, GlDeployment] = field(default_factory=dict)
    incidents: dict[int, GlIncident] = field(default_factory=dict)  # by iid
    incidents_gone: set[int] = field(default_factory=set)  # iids no longer incidents
    issues: dict[int, GlIssue] = field(default_factory=dict)  # by iid


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
    deployment_mrs: dict[int, list[int]] = {}
    incident_fixes: dict[int, list[int]] = {}
    issue_closers: dict[int, list[int]] = {}
    pipeline_tests: dict[int, dict[str, int]] = {}

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
                description = payload.get("description")
                closes, mentions = issue_refs(description)
                total, done = tasks(description)
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
                    merge_commit_sha=payload.get("merge_commit_sha"),
                    squash_commit_sha=payload.get("squash_commit_sha"),
                    labels=[str(label) for label in payload.get("labels") or []],
                    tasks_total=total,
                    tasks_done=done,
                    closes_iids=closes,
                    mention_iids=mentions,
                )
            case ep.EP_MR_COMMITS | ep.EP_MR_NOTES | ep.EP_MR_DIFFS:
                mr_parts.setdefault(int(params["iid"]), {})[row.endpoint] = payload
            case ep.EP_ENVIRONMENTS:
                for env in payload or []:
                    if env.get("name"):
                        out.environment_tiers[str(env["name"])] = env.get("tier")
            case ep.EP_DEPLOYMENT:
                status = str(payload.get("status") or "")
                finished = payload.get("finished_at")
                if finished is None and status in ("success", "failed", "canceled"):
                    finished = payload.get("updated_at")  # GitLab before 14 has no finished_at
                out.deployments[int(payload["id"])] = GlDeployment(
                    id=int(payload["id"]),
                    environment=str((payload.get("environment") or {}).get("name") or ""),
                    status=status,
                    sha=payload.get("sha"),
                    ref=payload.get("ref"),
                    created_at=parse_ts(payload.get("created_at")),
                    finished_at=parse_ts(finished),
                )
            case ep.EP_INCIDENT:
                out.incidents[int(payload["iid"])] = GlIncident(
                    id=int(payload["id"]),
                    iid=int(payload["iid"]),
                    title=payload.get("title"),
                    state=payload.get("state"),
                    labels=[str(label) for label in payload.get("labels") or []],
                    web_url=payload.get("web_url"),
                    created_at=parse_ts(payload.get("created_at")),
                    closed_at=parse_ts(payload.get("closed_at")),
                )
            case ep.EP_INCIDENT_GONE:
                out.incidents_gone.update(int(i) for i in payload or [])
            case ep.EP_INCIDENT_FIXES:
                incident_fixes[int(params["iid"])] = [int(i) for i in payload or []]
            case ep.EP_DEPLOYMENT_MRS:
                deployment_mrs[int(params["id"])] = [int(i) for i in payload or []]
            case ep.EP_PIPELINE:
                coverage = payload.get("coverage")
                out.pipelines[int(payload["id"])] = GlPipeline(
                    id=int(payload["id"]),
                    sha=payload.get("sha"),
                    ref=payload.get("ref"),
                    status=payload.get("status"),
                    duration_sec=payload.get("duration"),
                    created_at=parse_ts(payload.get("created_at")),
                    finished_at=parse_ts(payload.get("finished_at")),
                    source=payload.get("source"),
                    coverage=float(coverage) if coverage not in (None, "") else None,
                )
            case ep.EP_PIPELINE_TESTS:
                pipeline_tests[int(params["id"])] = {
                    "total": int(payload.get("count") or 0),
                    "failed": int(payload.get("failed") or 0) + int(payload.get("error") or 0),
                    "skipped": int(payload.get("skipped") or 0),
                }
            case ep.EP_ISSUE:
                milestone = payload.get("milestone") or {}
                time_stats = payload.get("time_stats") or {}
                out.issues[int(payload["iid"])] = GlIssue(
                    id=int(payload["id"]),
                    iid=int(payload["iid"]),
                    title=payload.get("title"),
                    state=payload.get("state"),
                    labels=[str(label) for label in payload.get("labels") or []],
                    assignees=[
                        _user(a) for a in payload.get("assignees") or [payload.get("assignee")] if a
                    ],
                    milestone=milestone.get("title"),
                    due_date=payload.get("due_date") or milestone.get("due_date"),
                    web_url=payload.get("web_url"),
                    created_at=parse_ts(payload.get("created_at")),
                    closed_at=parse_ts(payload.get("closed_at")),
                    time_estimate_s=time_stats.get("time_estimate"),
                    time_spent_s=time_stats.get("total_time_spent"),
                )
            case ep.EP_ISSUE_CLOSED_BY:
                issue_closers[int(params["iid"])] = [int(i) for i in payload or []]

    for iid, closers in issue_closers.items():
        if iid in out.issues:
            out.issues[iid].closed_by_iids = closers
    for pipeline_id, summary in pipeline_tests.items():
        if pipeline_id in out.pipelines:
            out.pipelines[pipeline_id].tests = summary
    for iid, fixes in incident_fixes.items():
        if iid in out.incidents:
            out.incidents[iid].fix_mr_iids = fixes
    for dep_id, iids in deployment_mrs.items():
        if dep_id in out.deployments:
            out.deployments[dep_id].mr_iids = iids
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
