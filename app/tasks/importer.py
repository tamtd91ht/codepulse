"""Import Jira issues into a task space (docs/specs/31 §19.2, roadmap P1 "thoát Jira").

Reads the issues CodePulse already synced from Jira (`issues`, source "jira") — no Jira call, so
it works while the connection still exists and after. Each issue becomes one task, once
(`task_imports` remembers its key; importing again only adds the new ones):

- type: bug / incident → Bug, feature → Story, a Jira sub-task → Sub-task of its imported parent,
  anything else → Task (or Task when the dự án does not use that type);
- state: closed → the dự án's "done" status, resolution "done", closed time kept; open → the
  first "Cần làm" status;
- title, labels, components, story points, due date, created time; the assignee when that person
  belongs to the dự án; a line in the description linking the Jira issue;
- the MRs and commits linked to the issue become the task's git links;
- optionally, defects of test results that name the Jira key are rewritten to the task's key.

The old key keeps resolving to the task (commits still saying ABC-123 link to it).
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Text, and_, exists, func, or_, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Commit,
    Issue,
    IssueCommitLink,
    IssueMrLink,
    JiraProjectScope,
    MergeRequest,
    Task,
    TaskImport,
    TaskSpace,
    TestResult,
    TestRun,
    TestRunTest,
)
from app.tasks import service as ts

SOURCE = "jira"
PREVIEW_MAX = 20
TYPE_OF = {"bug": "bug", "incident": "bug", "feature": "story"}


def _prefix(key: str | None) -> str:
    return (key or "").split("-", 1)[0]


def _counts_for(bp_id: int) -> Any:
    """A synced Jira issue counts for the dự án (docs/specs/27 §15): routed there, or routed
    nowhere in particular and its Jira project has a scope there. Written here on the models so
    the task code does not import the Jira integration (it is going away)."""
    return or_(
        Issue.business_project_ids.overlap([bp_id]),
        and_(
            Issue.business_project_ids.is_(None),
            exists().where(
                JiraProjectScope.jira_project_id == Issue.jira_project_id,
                JiraProjectScope.business_project_id == bp_id,
            ),
        ),
    )


def _issues(session: Session, bp_id: int, project_key: str | None = None) -> list[Issue]:
    query = select(Issue).where(Issue.source == SOURCE, Issue.key.is_not(None), _counts_for(bp_id))
    if project_key:
        query = query.where(Issue.key.like(f"{project_key}-%"))
    return list(session.scalars(query.order_by(Issue.created_at_src, Issue.id)))


def sources(session: Session, bp_id: int) -> list[dict[str, Any]]:
    """The Jira projects with synced issues that count for this dự án, and how many of them are
    imported already."""
    done = set(session.scalars(select(TaskImport.external_key).where(TaskImport.source == SOURCE)))
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for issue in _issues(session, bp_id):
        row = counts[_prefix(issue.key)]
        row[0] += 1
        row[1] += int(issue.key in done)
    return [
        {"project_key": k, "issues": total, "imported": imported}
        for k, (total, imported) in sorted(counts.items())
    ]


def _assignee(session: Session, issue: Issue, allowed: set[int]) -> int | None:
    ids = [int(a) for a in issue.assignee_account_ids or []]
    if not ids:
        return None
    for user_id in session.scalars(select(Account.user_id).where(Account.id.in_(ids))):
        if user_id is not None and user_id in allowed:
            return int(user_id)
    return None


def run(
    session: Session,
    space: TaskSpace,
    project_key: str,
    user_id: int,
    *,
    dry_run: bool,
    rewrite_defects: bool,
) -> dict[str, Any]:
    bp_id = space.business_project_id
    project_key = project_key.strip().upper()
    # A Jira key equal to a CodePulse key would name two things (ABC-5 the task and ABC-5 the
    # old issue): refused, so old keys always resolve to what they were.
    if session.scalar(select(TaskSpace.business_project_id).where(TaskSpace.key == project_key)):
        raise HTTPException(
            409,
            f"mã {project_key} đang là mã Task của một dự án: mã Jira cũ sẽ trùng mã task. "
            "Đổi mã Task của dự án đó (khi chưa có task) rồi nhập lại.",
        )
    issues = _issues(session, bp_id, project_key)
    if not issues:
        raise HTTPException(404, f"không có issue Jira {project_key} nào đã đồng bộ cho dự án")
    if not dry_run:
        # One import of this dự án at a time: a second click waits, then finds them imported.
        session.get(TaskSpace, bp_id, with_for_update=True, populate_existing=True)
    known = dict(
        session.execute(
            select(TaskImport.external_key, TaskImport.task_id).where(
                TaskImport.source == SOURCE,
                TaskImport.external_key.in_([i.key for i in issues if i.key]),
            )
        ).all()
    )
    todo = [i for i in issues if i.key not in known]
    roles = _roles(issues)
    # Parents before their children (epics, then their stories, then sub-tasks).
    depth = {"epic": 0, "top": 0, "child": 1, "subtask": 2, "loose": 1}
    todo.sort(
        key=lambda i: (
            depth[roles[str(i.key)][0]],
            i.created_at_src or datetime.min.replace(tzinfo=UTC),
        )
    )
    if dry_run:
        return {
            "project_key": project_key,
            "to_create": len(todo),
            "skipped": len(known),
            "preview": [
                {
                    "jira_key": i.key,
                    "title": i.title,
                    "type": _type_for(space, i, roles[str(i.key)][0]),
                    "state": i.state or "opened",
                }
                for i in todo[:PREVIEW_MAX]
            ],
        }
    statuses = ts.statuses_of(session, bp_id)
    rules = ts.rules_of(space)
    todo_status = next((s for s in statuses if s.category == "todo"), statuses[0])
    done_status = next(
        (s for s in statuses if s.id == rules.get("done")),
        next((s for s in statuses if s.category == "done"), None),
    )
    allowed = {u.id for u in ts.people(session, bp_id)}
    made: dict[str, Task] = {}
    keys_by_id = {int(t_id): str(k) for k, t_id in known.items()}
    existing = {t.id: t for t in session.scalars(select(Task).where(Task.id.in_(list(keys_by_id))))}
    by_ext: dict[str, Task] = {keys_by_id[i]: t for i, t in existing.items()}
    for issue in todo:
        role, parent_ext = roles[str(issue.key)]
        parent = (by_ext.get(parent_ext) or made.get(parent_ext)) if parent_ext else None
        task_type = _type_for(space, issue, role)
        if parent is not None and (parent.type == "subtask" or (task_type == "epic")):
            parent = None  # never a sub-task (or an epic) under it
        url = issue.web_url if (issue.web_url or "").startswith(("http://", "https://")) else None
        note = f"Nhập từ Jira: [{issue.key}]({url})" if url else f"Nhập từ Jira: {issue.key}"
        task = ts.create_task(
            session,
            space,
            {
                "type": task_type,
                "title": (issue.title or issue.key or "Không tiêu đề")[:300],
                "description": note,
                "parent_key": parent.key if parent is not None else None,
                "labels": list(issue.labels or []),
                "components": list(issue.components or []),
                "story_points": issue.story_points,
                "due_date": issue.due_date,
                "status_id": todo_status.id,
            },
            user_id,
        )
        # Set directly: no "giao cho bạn" notification storm for history being moved over.
        task.assignee_id = _assignee(session, issue, allowed)
        if issue.created_at_src is not None:
            task.created_at = issue.created_at_src
        if (issue.state or "") == "closed" and done_status is not None:
            task.status_id = done_status.id
            task.resolution = "done"
            task.resolved_at = issue.closed_at_src or datetime.now(UTC)
            task.status_changed_at = task.resolved_at
        session.add(
            TaskImport(source=SOURCE, external_key=issue.key, task_id=task.id, imported_by=user_id)
        )
        made[str(issue.key)] = task
        _copy_git(session, issue, task, user_id)
    session.flush()
    rewritten = (
        _rewrite_defects(session, bp_id, {k: t.key for k, t in made.items()})
        if rewrite_defects
        else 0
    )
    return {
        "project_key": project_key,
        "created": len(made),
        "skipped": len(known),
        "defects_rewritten": rewritten,
        "mapping": [
            {"jira_key": k, "task_key": t.key} for k, t in list(made.items())[:PREVIEW_MAX]
        ],
    }


def _roles(issues: list[Issue]) -> dict[str, tuple[str, str | None]]:
    """Each issue's place (Jira's `parent` names an epic for a story but a story for a
    sub-task, and the synced row does not say which): a top issue with grandchildren, or whose
    children carry story points while it has none, is an **epic**; its children keep their own
    type (`child`); a child of any other top issue, or a grandchild, is a **subtask**; anything
    deeper loses its parent (`loose`). Returns key -> (role, parent key or None)."""
    by_key = {str(i.key): i for i in issues}
    kids: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        if i.parent_key and i.parent_key in by_key:
            kids[i.parent_key].append(i)

    def top_role(i: Issue) -> str:
        own = kids.get(str(i.key), [])
        if any(kids.get(str(c.key)) for c in own):
            return "epic"
        if own and not i.story_points and any(c.story_points for c in own):
            return "epic"
        return "top"

    out: dict[str, tuple[str, str | None]] = {}
    for i in issues:
        key = str(i.key)
        parent = by_key.get(i.parent_key or "")
        if parent is None:
            out[key] = (top_role(i), None)
            continue
        grand = by_key.get(parent.parent_key or "")
        if grand is None:
            if top_role(parent) == "epic":
                out[key] = ("child", str(parent.key))
            else:
                out[key] = ("subtask", str(parent.key))
        elif by_key.get(grand.parent_key or "") is None and top_role(grand) == "epic":
            out[key] = ("subtask", str(parent.key))
        else:
            out[key] = ("loose", None)
    return out


def _type_for(space: TaskSpace, issue: Issue, role: str) -> str:
    if role == "epic" and "epic" in space.types:
        return "epic"
    if role == "subtask" and "subtask" in space.types:
        return "subtask"
    wanted = TYPE_OF.get(issue.type or "", "task")
    return (
        wanted if wanted in space.types else ("task" if "task" in space.types else space.types[0])
    )


def _copy_git(session: Session, issue: Issue, task: Task, user_id: int) -> None:
    from app.tasks import git as task_git

    for mr in session.scalars(
        select(MergeRequest)
        .join(IssueMrLink, IssueMrLink.mr_id == MergeRequest.id)
        .where(IssueMrLink.issue_id == issue.id)
    ):
        state = "merged" if mr.state == "merged" else "closed" if mr.state == "closed" else "opened"
        task_git.link(
            session,
            task,
            "mr",
            mr.project_id,
            mr_iid=mr.iid,
            title=mr.title,
            relation="refs",
            state=state,
            linked_by=user_id,
        )
    for link, commit in session.execute(
        select(IssueCommitLink, Commit)
        .join(Commit, Commit.sha == IssueCommitLink.sha)
        .where(IssueCommitLink.issue_id == issue.id)
    ).tuples():
        if commit.project_id is None:
            continue
        task_git.link(
            session,
            task,
            "commit",
            commit.project_id,
            sha=commit.sha,
            title=(commit.title if hasattr(commit, "title") else None),
            relation="fixes" if link.kind == "fixes" else "refs",
            linked_by=user_id,
        )


def _rewrite_defects(session: Session, bp_id: int, mapping: dict[str, str]) -> int:
    """Defects of the dự án's test results naming an imported Jira key now name its task."""
    if not mapping:
        return 0
    rows = session.scalars(
        select(TestResult)
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(
            TestRun.business_project_id == bp_id,
            TestResult.defects.has_any(postgresql.array(sorted(mapping), type_=Text)),
        )
    )
    changed = 0
    for result in rows:
        before = list(result.defects or [])
        after = list(dict.fromkeys(mapping.get(k, k) for k in before))
        if after != before:
            result.defects = after
            changed += 1
    return changed


def imported_count(session: Session, bp_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(TaskImport)
            .join(Task, Task.id == TaskImport.task_id)
            .where(Task.business_project_id == bp_id)
        )
        or 0
    )
