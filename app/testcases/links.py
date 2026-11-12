"""Links between test cases, Jira and code (docs/specs/30 §15.2 G2): the repos of a dự án and a
compact view of their folders (for code_paths and the AI), the documents mentioning Jira keys,
and the traceability matrix (Jira issue -> cases -> latest results -> defects).

Folders come from the collector's `commit_files` paths (no checkout, no content) and the repo's
component map names when built.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.docs import service as docs
from app.jira.scopes import jira_issue_in
from app.models import (
    Commit,
    CommitFile,
    Issue,
    Project,
    ProjectDocument,
    ProjectDocumentVersion,
    RepoComponentMap,
    TestCase,
    TestResult,
    TestRun,
    TestRunTest,
    TestSuite,
)
from app.testcases import content
from app.testcases import service as tc

TREE_FILES = 4_000  # recent changed paths read per repo
TREE_ENTRIES = 40  # folders listed per repo
ISSUES_MAX = 300  # Jira issues sent to the AI
DOCS_MAX = 4  # documents auto-picked for one Jira key
TRACE_ISSUES = 500  # issues per page at most
TRACE_PAGE = 200  # issues per page by default
TRACE_EXPORT = 5_000  # issues in one CSV export
JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")


@dataclass
class Repo:
    id: int
    path: str  # path_with_namespace
    name: str  # its last part
    folders: list[str] = field(default_factory=list)  # "src/customer/ (120)"
    components: list[str] = field(default_factory=list)


def repos(
    session: Session, bp_ids: list[int], *, folders: bool = False, external_llm: bool = False
) -> list[Repo]:
    """The repos of these dự án; with `folders`, their main folders and component names. With
    `external_llm`, only the repos allowed to be sent to an external LLM (the AI prompt)."""
    query = (
        select(Project.id, Project.path_with_namespace)
        .where(Project.business_project_id.in_(sorted(set(bp_ids)) or [0]))
        .order_by(Project.path_with_namespace)
    )
    if external_llm:
        query = query.where(Project.allow_external_llm.is_not(False))
    rows = session.execute(query).all()
    out = [Repo(id=i, path=p, name=p.rsplit("/", 1)[-1]) for i, p in rows]
    if folders:
        for repo in out:
            repo.folders = folder_summary(_recent_paths(session, repo.id))
            repo.components = _components(session, repo.id)
    return out


def _recent_paths(session: Session, project_id: int) -> list[str]:
    return list(
        session.scalars(
            select(CommitFile.path)
            .join(Commit, Commit.sha == CommitFile.sha)
            .where(Commit.project_id == project_id)
            .order_by(CommitFile.created_at.desc())
            .limit(TREE_FILES)
        )
    )


def folder_summary(paths: list[str], limit: int = TREE_ENTRIES) -> list[str]:
    """The folders most files were changed in (first and second level), "dir/ (n)"; files at
    the root are listed by name."""
    counts: Counter[str] = Counter()
    for raw in set(paths):
        parts = raw.strip("/").split("/")
        if len(parts) == 1:
            counts[parts[0]] += 1
            continue
        counts[parts[0] + "/"] += 1
        if len(parts) > 2:
            counts[f"{parts[0]}/{parts[1]}/"] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [f"{name} ({n})" for name, n in sorted(ranked)]


def _components(session: Session, project_id: int) -> list[str]:
    row = session.get(RepoComponentMap, project_id)
    if row is None or not row.groups:
        return []
    return [str(g.get("name") or "")[:80] for g in row.groups if g.get("name")][:30]


def project_issues(session: Session, bp_id: int, limit: int = ISSUES_MAX) -> list[tuple[str, str]]:
    """The dự án's Jira issues (key, title), newest first, sub-tasks aside."""
    return [
        (key, title or "")
        for key, title in session.execute(
            select(Issue.key, Issue.title)
            .where(Issue.source == "jira", Issue.parent_key.is_(None), jira_issue_in([bp_id]))
            .order_by(Issue.created_at_src.desc().nulls_last())
            .limit(limit)
        )
        if key
    ]


def existing_keys(session: Session, bp_id: int, keys: set[str]) -> set[str]:
    """Those of `keys` that are Jira issues of the dự án."""
    if not keys:
        return set()
    return {
        k
        for k in session.scalars(
            select(Issue.key).where(
                Issue.source == "jira", Issue.key.in_(sorted(keys)), jira_issue_in([bp_id])
            )
        )
        if k
    }


def keys_in(text: str) -> list[str]:
    out: list[str] = []
    for key in JIRA_KEY.findall(text or ""):
        if key not in out:
            out.append(key)
    return out


@dataclass
class DocHit:
    id: int
    title: str
    keys: list[str]


def documents_mentioning(
    session: Session, bp_id: int, keys: list[str], limit: int = DOCS_MAX
) -> list[DocHit]:
    """The dự án's live documents whose current text names one of `keys` (a BRD of a story)."""
    if not keys:
        return []
    body = ProjectDocumentVersion.body
    rows = session.execute(
        select(ProjectDocument.id, ProjectDocument.title, body)
        .join(
            ProjectDocumentVersion, ProjectDocumentVersion.id == ProjectDocument.current_version_id
        )
        .where(
            docs.in_project(bp_id),
            ProjectDocument.archived_at.is_(None),
            or_(*(body.contains(k) for k in keys[:20])),
        )
        .order_by(ProjectDocument.pinned.desc(), ProjectDocument.updated_at.desc())
        .limit(limit)
    ).all()
    return [
        DocHit(id=i, title=t, keys=[k for k in keys if k in (text or "")]) for i, t, text in rows
    ]


# -- traceability ----------------------------------------------------------------------------------


@dataclass
class TraceCase:
    id: int
    key: str
    title: str
    status: str
    suite: str
    run_id: int | None = None
    run_key: str | None = None
    run_name: str | None = None
    test_status: str | None = None
    defects: list[str] = field(default_factory=list)


@dataclass
class TraceRow:
    key: str
    title: str
    type: str | None
    state: str | None
    sprint: str | None
    labels: list[str]
    url: str | None
    cases: list[TraceCase]


@dataclass
class Trace:
    rows: list[TraceRow]
    issues: int  # issues matching the filters
    covered: int  # of them, named by a live case
    without_requirement: int
    sprints: list[str]
    labels: list[str]
    total: int = 0  # issues the pages go through (`uncovered` applied)
    limit: int = 0
    offset: int = 0


@dataclass
class _CaseRow:
    id: int
    title: str
    status: str
    suite: str


def _issue_filters(
    bp_id: int, *, sprint: str | None, label: str | None, q: str | None, days: int | None
) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = [
        Issue.source == "jira",
        Issue.parent_key.is_(None),
        jira_issue_in([bp_id]),
    ]
    if sprint:
        where.append(Issue.milestone == sprint)
    if label:
        where.append(Issue.labels.contains([label]))
    if q:
        like = f"%{q.strip()}%"
        where.append(or_(Issue.key.ilike(like), Issue.title.ilike(like)))
    if days:
        where.append(Issue.created_at_src >= datetime.now(UTC) - timedelta(days=days))
    return where


def _live_cases(bp_id: int) -> list[ColumnElement[bool]]:
    """The live cases of the dự án (suites made there or shared with it, not archived)."""
    return [tc.suite_in([bp_id]), TestSuite.archived_at.is_(None), TestCase.status != "obsolete"]


def traceability(
    session: Session,
    bp_id: int,
    *,
    milestone_id: int | None = None,
    sprint: str | None = None,
    label: str | None = None,
    q: str | None = None,
    days: int | None = None,
    uncovered: bool = False,
    limit: int = TRACE_PAGE,
    offset: int = 0,
) -> Trace:
    """Rows per Jira issue of the dự án with the cases linked to it (jira_keys), each case's
    latest test (in the runs of `milestone_id` when given) and the defects recorded on it.

    One page of issues (`limit`, `offset`); `issues` / `covered` count every issue matching the
    filters, `total` those matching with `uncovered` too (what the pages go through)."""
    limit = max(1, min(limit, TRACE_ISSUES))
    offset = max(0, offset)
    where = _issue_filters(bp_id, sprint=sprint, label=label, q=q, days=days)
    # Every Jira key named by a live case of the dự án: a hash semi-join, one pass over the
    # cases' jira_keys column only (no cap: a capped list makes covered issues look uncovered).
    named = (
        select(func.jsonb_array_elements_text(TestCase.jira_keys))
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*_live_cases(bp_id))
    )
    is_covered = Issue.key.in_(named)
    issues_n, covered_n = session.execute(
        select(func.count(), func.count().filter(is_covered)).select_from(Issue).where(*where)
    ).one()
    total = issues_n - covered_n if uncovered else issues_n
    if uncovered:
        where = [*where, ~is_covered]
    issues = session.execute(
        select(
            Issue.key,
            Issue.title,
            Issue.type,
            Issue.state,
            Issue.milestone,
            Issue.labels,
            Issue.web_url,
        )
        .where(*where)
        .order_by(Issue.created_at_src.desc().nulls_last(), Issue.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    without = session.scalar(
        select(func.count())
        .select_from(TestCase)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(*_live_cases(bp_id), func.jsonb_array_length(TestCase.jira_keys) == 0)
    )
    wanted = sorted({i.key for i in issues if i.key})
    by_key: dict[str, list[_CaseRow]] = {}
    if wanted:
        # The cases naming one of the page's keys only, the columns shown only. The GIN index
        # on jira_keys is jsonb_path_ops, which serves `@>` (not `?|`): one containment test
        # per key, OR-ed (a BitmapOr over the index).
        found_cases = session.execute(
            select(
                TestCase.id,
                TestCase.title,
                TestCase.status,
                TestCase.jira_keys,
                TestSuite.name,
            )
            .join(TestSuite, TestSuite.id == TestCase.suite_id)
            .where(
                *_live_cases(bp_id),
                or_(*(TestCase.jira_keys.contains([k]) for k in wanted)),
            )
            .order_by(TestCase.id)
        ).all()
        keep = set(wanted)
        for case_id, title, status, keys, suite_name in found_cases:
            row = _CaseRow(case_id, title, status, suite_name or "")
            for key in keys or []:
                if key in keep:
                    by_key.setdefault(key, []).append(row)
    linked = {c.id for found in by_key.values() for c in found}
    latest = _latest_tests(session, bp_id, linked, milestone_id)
    defects = _defects(session, [t[0] for t in latest.values()])
    rows: list[TraceRow] = []
    for issue in issues:
        found = by_key.get(issue.key or "", [])
        traced = []
        for case in found:
            test = latest.get(case.id)
            traced.append(
                TraceCase(
                    id=case.id,
                    key=content.key("case", case.id) or "",
                    title=case.title,
                    status=case.status,
                    suite=case.suite,
                    run_id=test[1] if test else None,
                    run_key=content.key("run", test[1]) if test else None,
                    run_name=test[3] if test else None,
                    test_status=test[2] if test else None,
                    defects=defects.get(test[0], []) if test else [],
                )
            )
        rows.append(
            TraceRow(
                key=issue.key or "",
                title=issue.title or "",
                type=issue.type,
                state=issue.state,
                sprint=issue.milestone,
                labels=list(issue.labels or []),
                url=issue.web_url,
                cases=traced,
            )
        )
    sprints, labels = _facets(session, bp_id)
    return Trace(
        rows=rows,
        issues=int(issues_n),
        covered=int(covered_n),
        without_requirement=int(without or 0),
        sprints=sprints,
        labels=labels,
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _latest_tests(
    session: Session, bp_id: int, case_ids: set[int], milestone_id: int | None
) -> dict[int, tuple[int, int, str, str]]:
    """case id -> (test id, run id, status, run name) of its latest test in the dự án's runs:
    the last one tested, else (never tested) the one of the newest run. One row per case
    (DISTINCT ON), so the cost follows the page's cases, not their history."""
    if not case_ids:
        return {}
    query = (
        select(TestRunTest.case_id, TestRunTest.id, TestRun.id, TestRunTest.status, TestRun.name)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(TestRunTest.case_id.in_(sorted(case_ids)), TestRun.business_project_id == bp_id)
        .ext(distinct_on(TestRunTest.case_id))
        .order_by(
            TestRunTest.case_id,
            TestRunTest.tested_at.desc().nulls_last(),
            TestRun.created_at.desc(),
            TestRunTest.id.desc(),
        )
    )
    if milestone_id is not None:
        query = query.where(TestRun.milestone_id == milestone_id)
    out: dict[int, tuple[int, int, str, str]] = {}
    for case_id, test_id, run_id, status, name in session.execute(query):
        if case_id is not None and case_id not in out:
            out[case_id] = (test_id, run_id, status, name)
    return out


def _defects(session: Session, test_ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if not test_ids:
        return out
    for test_id, found in session.execute(
        select(TestResult.test_id, TestResult.defects)
        .where(TestResult.test_id.in_(test_ids), func.jsonb_array_length(TestResult.defects) > 0)
        .order_by(TestResult.id)
    ):
        kept = out.setdefault(test_id, [])
        for d in found or []:
            if d not in kept and len(kept) < 10:
                kept.append(d)
    return out


def _facets(session: Session, bp_id: int) -> tuple[list[str], list[str]]:
    """The sprints / versions and the labels seen on the dự án's issues, for the filters."""
    base = and_(Issue.source == "jira", jira_issue_in([bp_id]))
    sprints = [
        s
        for s in session.scalars(
            select(Issue.milestone)
            .where(base, Issue.milestone.is_not(None))
            .group_by(Issue.milestone)
            .order_by(func.max(Issue.created_at_src).desc().nulls_last())
            .limit(50)
        )
        if s
    ]
    label = func.jsonb_array_elements_text(Issue.labels).label("label")
    labels = [
        str(lbl)
        for lbl in session.scalars(
            select(cast(label, String))
            .where(base, Issue.labels.is_not(None))
            .group_by(label)
            .order_by(func.count().desc())
            .limit(50)
        )
        if lbl
    ]
    return sprints, sorted(labels)


def csv_rows(trace: Trace) -> list[list[str]]:
    rows = [
        [
            "Jira",
            "Tiêu đề issue",
            "Loại",
            "Trạng thái",
            "Sprint / version",
            "Testcase",
            "Tiêu đề testcase",
            "Bộ",
            "Trạng thái testcase",
            "Run",
            "Kết quả",
            "Bug",
        ]
    ]
    for row in trace.rows:
        head = [row.key, row.title, row.type or "", row.state or "", row.sprint or ""]
        if not row.cases:
            rows.append([*head, "", "", "", "", "", "", ""])
        for case in row.cases:
            rows.append(
                [
                    *head,
                    case.key,
                    case.title,
                    case.suite,
                    case.status,
                    case.run_key or "",
                    case.test_status or "",
                    ", ".join(case.defects),
                ]
            )
    return rows
