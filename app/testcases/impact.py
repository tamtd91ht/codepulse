"""Which test cases a merge request calls for (docs/specs/30 §8.1), without the AI: the same Jira
key, a changed file inside a case's code_paths globs, or the case's automated test file itself
changed. Also the release list: every case the MRs merged in a period call for (§8.3).

The changed files are the collector's `commit_files` of the MR's commits (paths and line counts
only, never content).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CommitFile, MergeRequest, MrCommit, Project, TestAiRun, TestCase, TestSuite
from app.testcases import service as tc

MAX_PATHS = 400  # of one MR, matched and sent to the AI
MAX_CASES = 2_000  # of the dự án, matched


# Tokens of a glob: a literal character, "?" (one character but "/"), "*" (any run without "/"),
# "**" (any run), "**/" (nothing, or any run ending with "/").
Token = tuple[str, str]


@lru_cache(maxsize=1024)
def _tokens(pattern: str) -> tuple[Token, ...] | None:
    """`**` any depth, `*` / `?` inside one path part; a pattern without "/" matches a file name
    anywhere; a trailing "/" means everything below. None: a pattern that matches nothing."""
    p = tc.glob_clean(pattern)
    if not p:
        return None
    if p.endswith("/"):
        p += "**"
    out: list[Token] = [] if "/" in p else [("dir", "")]
    i = 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append(("dir", ""))
            i += 3
        elif p.startswith("**", i):
            out.append(("any", ""))
            i += 2
        elif p[i] == "*":
            out.append(("part", ""))
            i += 1
        elif p[i] == "?":
            out.append(("one", ""))
            i += 1
        else:
            out.append(("lit", p[i]))
            i += 1
    return tuple(out)


def glob_match(pattern: str, path: str) -> bool:
    """Whether `path` matches the glob: one pass per token over the path (no backtracking, so a
    pattern full of wildcards costs no more than a plain one)."""
    tokens = _tokens(pattern)
    if tokens is None:
        return False
    s = path.removeprefix("/")
    n = len(s)
    reach = [True] + [False] * n  # reach[j]: the tokens so far match s[:j]
    for kind, char in tokens:
        nxt = [False] * (n + 1)
        if kind == "lit":
            for j in range(n):
                if reach[j] and s[j] == char:
                    nxt[j + 1] = True
        elif kind == "one":
            for j in range(n):
                if reach[j] and s[j] != "/":
                    nxt[j + 1] = True
        elif kind == "part":
            for j in range(n + 1):
                nxt[j] = reach[j] or (j > 0 and nxt[j - 1] and s[j - 1] != "/")
        elif kind == "any":
            for j in range(n + 1):
                nxt[j] = reach[j] or (j > 0 and nxt[j - 1])
        else:  # "dir": nothing, or a run ending with "/"
            seen = False
            for j in range(n + 1):
                nxt[j] = reach[j] or (seen and s[j - 1] == "/")
                seen = seen or reach[j]
        reach = nxt
        if not any(reach):
            return False
    return reach[n]


@dataclass
class Changed:
    path: str
    additions: int = 0
    deletions: int = 0


def mr_paths(session: Session, mr_ids: list[int]) -> dict[int, list[Changed]]:
    """The files each MR's commits changed (summed per path), most changed first."""
    out: dict[int, list[Changed]] = {i: [] for i in mr_ids}
    if not mr_ids:
        return out
    rows = session.execute(
        select(
            MrCommit.mr_id,
            CommitFile.path,
            func.sum(CommitFile.additions),
            func.sum(CommitFile.deletions),
        )
        .join(CommitFile, CommitFile.sha == MrCommit.sha)
        .where(MrCommit.mr_id.in_(mr_ids))
        .group_by(MrCommit.mr_id, CommitFile.path)
    )
    for mr_id, path, adds, dels in rows:
        out[mr_id].append(Changed(path, int(adds or 0), int(dels or 0)))
    for items in out.values():
        items.sort(key=lambda c: (-(c.additions + c.deletions), c.path))
        del items[MAX_PATHS:]
    return out


def project_cases(session: Session, bp_ids: list[int]) -> list[TestCase]:
    """The cases (not obsolete) of the live suites of these dự án, home or shared."""
    return list(
        session.scalars(
            select(TestCase)
            .join(TestSuite, TestSuite.id == TestCase.suite_id)
            .where(
                tc.suite_in(bp_ids),
                TestSuite.archived_at.is_(None),
                TestCase.status != "obsolete",
            )
            .order_by(TestCase.suite_id, TestCase.position, TestCase.id)
            .limit(MAX_CASES)
        )
    )


def match(cases: list[TestCase], jira: list[str], paths: list[Changed]) -> dict[int, list[str]]:
    """case id -> why it is called for (Vietnamese, shown as is)."""
    out: dict[int, list[str]] = {}
    keys = {k.upper() for k in jira}
    names = [c.path for c in paths]
    for case in cases:
        why: list[str] = []
        for key in case.jira_keys or []:
            if key in keys:
                why.append(f"Jira {key}")
        for pattern in case.code_paths or []:
            hit = next((p for p in names if glob_match(pattern, p)), None)
            if hit:
                why.append(f"{hit} khớp {pattern}")
                break
        if case.automation:
            target = case.automation.split("::", 1)[0].strip().removeprefix("./")
            if target and any(p == target or p.endswith("/" + target) for p in names):
                why.append(f"đổi file test {target}")
        if why:
            out[case.id] = why
    return out


def latest_ai(session: Session, mr_ids: list[int]) -> dict[int, TestAiRun]:
    """The newest finished AI analysis of each MR."""
    if not mr_ids:
        return {}
    out: dict[int, TestAiRun] = {}
    for run in session.scalars(
        select(TestAiRun)
        .where(
            TestAiRun.kind == "impact",
            TestAiRun.merge_request_id.in_(mr_ids),
            TestAiRun.status.in_(("ready", "applied")),
        )
        .order_by(TestAiRun.created_at.desc())
    ):
        if run.merge_request_id is not None:
            out.setdefault(run.merge_request_id, run)
    return out


def ai_cases(run: TestAiRun | None) -> dict[int, dict[str, Any]]:
    if run is None or not run.result:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for item in run.result.get("cases") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            out[item["id"]] = item
    return out


@dataclass
class MrImpact:
    mr: MergeRequest
    repo: str
    paths: list[Changed]
    matched: dict[int, list[str]]
    ai: TestAiRun | None
    ai_cases: dict[int, dict[str, Any]] = field(default_factory=dict)


def for_mrs(session: Session, mrs: list[MergeRequest], bp_ids: list[int]) -> list[MrImpact]:
    cases = project_cases(session, bp_ids)
    live = {c.id for c in cases}
    paths = mr_paths(session, [m.id for m in mrs])
    ai = latest_ai(session, [m.id for m in mrs])
    repos = dict(
        session.execute(
            select(Project.id, Project.path_with_namespace).where(
                Project.id.in_({m.project_id for m in mrs if m.project_id is not None} or {0})
            )
        ).all()
    )
    out = []
    for mr in mrs:
        found = ai_cases(ai.get(mr.id))
        out.append(
            MrImpact(
                mr=mr,
                repo=repos.get(mr.project_id or 0, ""),
                paths=paths.get(mr.id, []),
                matched=match(cases, list(mr.jira_keys or []), paths.get(mr.id, [])),
                ai=ai.get(mr.id),
                ai_cases={k: v for k, v in found.items() if k in live},
            )
        )
    return out


def merged_mrs(
    session: Session,
    bp_id: int,
    since: datetime,
    until: datetime,
    target_branch: str | None = None,
    limit: int = 200,
) -> list[MergeRequest]:
    query = (
        select(MergeRequest)
        .join(Project, Project.id == MergeRequest.project_id)
        .where(
            Project.business_project_id == bp_id,
            MergeRequest.merged_at >= since,
            MergeRequest.merged_at < until,
        )
        .order_by(MergeRequest.merged_at.desc())
        .limit(limit)
    )
    if target_branch:
        query = query.where(MergeRequest.target_branch == target_branch)
    return list(session.scalars(query))


def regression(impacts: list[MrImpact]) -> dict[int, list[str]]:
    """case id -> the MRs calling for it (and why), over all of them."""
    out: dict[int, list[str]] = defaultdict(list)
    for item in impacts:
        label = f"!{item.mr.iid}"
        for case_id, why in item.matched.items():
            out[case_id].append(f"{label}: {'; '.join(why)}")
        for case_id, ai in item.ai_cases.items():
            if case_id not in item.matched:
                out[case_id].append(f"{label}: AI — {str(ai.get('reason') or '')[:160]}")
    return dict(out)
