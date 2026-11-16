"""Tasks follow git (docs/specs/31 §5-§6): branches, commits and MRs that name a task key are
linked to it, smart commit commands run once per commit, and the dự án's rules move the task
(In Progress on the first commit, In Review when an MR opens, Done when a fixing MR merges).

Called from the GitLab push webhook (every branch) and from the collector's convert step (MRs,
default-branch commits). Every change is idempotent: a rule runs only when a link is new or an
MR's state changed."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Commit,
    MergeRequest,
    Project,
    Task,
    TaskComment,
    TaskGitLink,
    TaskSpace,
    TaskStatus,
    TaskWorklog,
    User,
)
from app.tasks import keys as tk
from app.tasks import service

log = logging.getLogger(__name__)


@dataclass
class Linked:
    link: TaskGitLink
    created: bool
    old_state: str | None


def _space(session: Session, project: Project) -> TaskSpace | None:
    if project.business_project_id is None:
        return None
    return session.get(TaskSpace, project.business_project_id)


def _tasks(session: Session, space: TaskSpace, keys: Iterable[str]) -> dict[str, Task]:
    """The tasks of this dự án among `keys` (another dự án's key never links here)."""
    wanted = {k for k in keys if k.split("-", 1)[0] == space.key}
    if not wanted:
        return {}
    return {
        t.key: t
        for t in session.scalars(
            select(Task).where(
                Task.business_project_id == space.business_project_id, Task.key.in_(wanted)
            )
        )
    }


def link(
    session: Session,
    task: Task,
    kind: str,
    project_id: int | None,
    *,
    sha: str | None = None,
    mr_iid: int | None = None,
    branch: str | None = None,
    title: str | None = None,
    relation: str = "refs",
    state: str | None = None,
    manual: bool = False,
    linked_by: int | None = None,
) -> Linked:
    """Create or refresh one link. An automatic link never weakens fixes → refs, and never
    overrides what a person chose by hand."""
    query = select(TaskGitLink).where(TaskGitLink.task_id == task.id, TaskGitLink.kind == kind)
    if kind == "commit":
        query = query.where(TaskGitLink.sha == sha)
    elif kind == "mr":
        query = query.where(TaskGitLink.project_id == project_id, TaskGitLink.mr_iid == mr_iid)
    else:
        query = query.where(TaskGitLink.project_id == project_id, TaskGitLink.branch == branch)
    row = session.scalars(query).first()
    if row is None:
        row = TaskGitLink(
            task_id=task.id,
            kind=kind,
            project_id=project_id,
            sha=sha,
            mr_iid=mr_iid,
            branch=branch,
            title=(title or "")[:500] or None,
            relation=relation,
            state=state,
            manual=manual,
            linked_by=linked_by,
        )
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:  # made meanwhile by the other path (webhook / sync)
            existing = session.scalars(query).first()
            if existing is None:
                raise
            return Linked(existing, False, existing.state)
        what = {"commit": (sha or "")[:12], "mr": f"!{mr_iid}", "branch": branch}[kind]
        service.log(session, task, linked_by, kind, None, what, "web" if manual else "git")
        return Linked(row, True, None)
    old_state = row.state
    if manual:
        row.relation, row.manual, row.linked_by = relation, True, linked_by
    elif not row.manual and relation == "fixes":
        row.relation = "fixes"
    if title:
        row.title = title[:500]
    if state is not None:
        row.state = state
    session.flush()
    return Linked(row, False, old_state)


def _status(session: Session, space: TaskSpace, rule: str) -> TaskStatus | None:
    ident = service.rules_of(space).get(rule)
    status = session.get(TaskStatus, ident) if ident else None
    return (
        status
        if status is not None and status.business_project_id == space.business_project_id
        else None
    )


def apply_rule(session: Session, space: TaskSpace, task: Task, rule: str) -> bool:
    """Move `task` as the dự án's git rule says; never pulls a finished task back (except
    reopening one that sat In Review when its MR closed)."""
    rules = service.rules_of(space)
    flag = {
        "start": "start_on_commit",
        "review": "review_on_mr",
        "done": "done_on_merge",
        "reopen": "reopen_on_mr_close",
    }[rule]
    if not rules.get(flag):
        return False
    current = session.get(TaskStatus, task.status_id)
    if current is None:
        return False
    if rule == "start":
        target = _status(session, space, "start")
        if current.category != "todo":
            return False
    elif rule == "review":
        target = _status(session, space, "review")
        if current.category == "done":
            return False
    elif rule == "done":
        target = _status(session, space, "done")
        if current.category == "done":
            return False
    else:
        target = _status(session, space, "start")
        if current.id != rules.get("review"):
            return False
    if target is None or target.id == current.id:
        return False
    return service.set_status(session, task, target, None, "git", check_transition=False)


def user_of_email(session: Session, email: str | None) -> int | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    user_id = session.scalar(
        select(Account.user_id).where(
            Account.kind == "git_email", func.lower(Account.external_key) == email
        )
    )
    if user_id is not None:
        return int(user_id)
    found = session.scalar(select(User.id).where(func.lower(User.primary_email) == email))
    return int(found) if found is not None else None


def smart_commit(
    session: Session, space: TaskSpace, task: Task, message: str | None, author_id: int | None
) -> None:
    """#time / #comment / #<status> of a commit (docs/specs/31 §5): only for an author mapped to
    a person, each command as far as that person holds its right."""
    commands = tk.smart_commands(message)
    if author_id is None or not (commands.minutes or commands.comment or commands.statuses):
        return
    from app.api.security import load_principal
    from app.config import get_settings

    try:
        principal = load_principal(session, author_id, get_settings())
    except Exception:  # inactive or gone: the commit still links, the commands are ignored
        return
    # Each command needs its own right (docs/specs/31 §4.1): #time tasks.worklog, #comment
    # tasks.comment, #<status> tasks.transition.
    if commands.minutes and service.may(principal, "worklog", task):
        session.add(
            TaskWorklog(
                task_id=task.id,
                user_id=author_id,
                minutes=commands.minutes,
                day=datetime.now(UTC).date(),
                note="smart commit",
                via="smart_commit",
            )
        )
        task.spent_min = (task.spent_min or 0) + commands.minutes
        service.log(
            session, task, author_id, "worklog", None, f"{commands.minutes} phút", "smart_commit"
        )
    if commands.comment and service.may(principal, "comment", task):
        session.add(TaskComment(task_id=task.id, author_id=author_id, body=commands.comment))
        service.log(session, task, author_id, "comment", None, "(smart commit)", "smart_commit")
    may_move = service.may(principal, "transition", task)
    for word in commands.statuses if may_move else ():
        status = service.status_by_word(session, space, word)
        if status is not None:
            try:
                service.set_status(session, task, status, author_id, "smart_commit")
            except Exception:  # a transition the workflow refuses: skipped, the rest stays
                log.info("smart commit status refused", extra={"task": task.key, "status": word})
            break
    session.flush()


def _commit(
    session: Session,
    space: TaskSpace,
    project: Project,
    sha: str,
    message: str | None,
    author_email: str | None,
    *,
    on_default: bool,
    when: datetime | None = None,
    pusher_id: int | None = None,
) -> int:
    """Link one commit to the tasks it names; returns the links made.

    History older than the dự án's Task space is not this task's (a key reused from another
    tracker). Smart commands run once, only on a push whose pusher is the commit's author (an
    author email alone can be forged). A `fixes` commit reaching the default branch finishes
    the task even when it was linked earlier from its feature branch."""
    title = (message or "").splitlines()[0] if message else ""
    if title.lower().startswith(("merge branch", "merge remote-tracking", "merge pull request")):
        return 0
    if _before_space(space, when):
        return 0
    refs = tk.commit_refs(message)
    if not refs:
        return 0
    tasks = _tasks(session, space, refs)
    made = 0
    author_id = user_of_email(session, author_email)
    for key, task in tasks.items():
        relation = refs[key]
        got = link(session, task, "commit", project.id, sha=sha, title=title, relation=relation)
        if got.created:
            made += 1
            if pusher_id is not None and pusher_id == author_id:
                smart_commit(session, space, task, message, author_id)
            apply_rule(session, space, task, "start")
        if on_default and got.link.relation == "fixes":
            apply_rule(session, space, task, "done")
    return made


def _before_space(space: TaskSpace, when: datetime | None) -> bool:
    started = space.created_at
    if when is None or started is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when < started - timedelta(days=1)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def on_push(
    session: Session,
    project: Project,
    branch: str,
    default_branch: str | None,
    commits: list[dict[str, Any]],
    *,
    deleted: bool = False,
    pusher_email: str | None = None,
) -> int:
    """A GitLab push (webhook): its branch and commits, on any branch."""
    space = _space(session, project)
    if space is None or not branch:
        return 0
    made = 0
    if not deleted:
        for task in _tasks(session, space, tk.branch_keys(branch)).values():
            if link(session, task, "branch", project.id, branch=branch).created:
                made += 1
                apply_rule(session, space, task, "start")
    on_default = bool(default_branch) and branch == default_branch
    pusher_id = user_of_email(session, pusher_email)
    for commit in commits:
        if not isinstance(commit, dict) or not commit.get("id"):
            continue
        author = commit.get("author") or {}
        made += _commit(
            session,
            space,
            project,
            str(commit["id"]),
            commit.get("message"),
            author.get("email") if isinstance(author, dict) else None,
            on_default=on_default,
            when=_when(commit.get("timestamp")),
            pusher_id=pusher_id,
        )
    return made


def on_sync(session: Session, project: Project, data: Any) -> int:
    """After the collector converted a repo's GitLab data (`app.collector.gitlab.extract.
    Extracted`): its MRs (state changes drive the rules) and default-branch commits."""
    space = _space(session, project)
    if space is None:
        return 0
    default = data.default_branch or project.default_branch
    made = 0
    for mr in data.merge_requests.values():
        refs = tk.mr_refs(mr.title, getattr(mr, "description", None), mr.source_branch)
        state = "merged" if mr.state == "merged" else "closed" if mr.state == "closed" else "opened"
        if _before_space(space, getattr(mr, "created_at", None)):
            continue
        for key, task in _tasks(session, space, refs).items():
            relation = refs[key]
            got = link(
                session,
                task,
                "mr",
                project.id,
                mr_iid=mr.iid,
                title=mr.title,
                relation=relation,
                state=state,
            )
            made += int(got.created)
            if got.created and service.rules_of(space).get("mr_comment"):
                _comment_on_mr(session, project, mr.iid, task)
            if not got.created and got.old_state == state:
                continue
            fixes = got.link.relation == "fixes"
            if state == "opened" and fixes:
                apply_rule(session, space, task, "review")
            elif state == "merged" and fixes and (not default or mr.target_branch == default):
                apply_rule(session, space, task, "done")
            elif state == "closed" and fixes and not _open_fixing_mr(session, task):
                apply_rule(session, space, task, "reopen")
            if mr.source_branch and state == "opened":
                link(session, task, "branch", project.id, branch=mr.source_branch)
        for commit in getattr(mr, "commits", []) or []:
            made += _commit(
                session,
                space,
                project,
                commit.sha,
                commit.message,
                commit.author_email,
                on_default=False,
                when=commit.authored_at,
            )
    # Commits of every branch (the collector lists them all): linked, never finished here.
    for commit in data.commits.values():
        if commit.is_merge:
            continue
        made += _commit(
            session,
            space,
            project,
            commit.sha,
            commit.message,
            commit.author_email,
            on_default=False,
            when=commit.authored_at,
        )
    return made


def _open_fixing_mr(session: Session, task: Task) -> bool:
    return (
        session.scalars(
            select(TaskGitLink.id).where(
                TaskGitLink.task_id == task.id,
                TaskGitLink.kind == "mr",
                TaskGitLink.relation == "fixes",
                TaskGitLink.state == "opened",
            )
        ).first()
        is not None
    )


def _comment_on_mr(session: Session, project: Project, iid: int, task: Task) -> None:
    """rules.mr_comment: say on the MR which task it is (best effort, never fails the sync)."""
    from app.collector.sync import client_factory
    from app.config import get_settings
    from app.crypto import secret_box
    from app.models import Connection

    connection = session.get(Connection, project.connection_id) if project.connection_id else None
    if connection is None:
        return
    base = get_settings().app_base_url
    body = f"Task [{task.key}]({base.rstrip('/')}{service.link_of(task.key)}): {task.title}"
    try:
        with client_factory(secret_box())(connection) as client:
            client.create_mr_note(project.external_id, iid, body)
    except Exception:
        log.warning(
            "task MR comment failed", extra={"task": task.key, "mr_iid": iid}, exc_info=True
        )


# -- by hand -------------------------------------------------------------------------------------


def repos_of(session: Session, bp_id: int) -> list[Project]:
    return list(
        session.scalars(
            select(Project)
            .where(Project.business_project_id == bp_id)
            .order_by(Project.path_with_namespace)
        )
    )


def link_commit_by_hand(
    session: Session, task: Task, sha: str, relation: str, user_id: int
) -> TaskGitLink:
    from fastapi import HTTPException

    repos = {p.id: p for p in repos_of(session, task.business_project_id)}
    found = list(
        session.scalars(
            select(Commit)
            .where(
                Commit.sha.startswith(sha.lower(), autoescape=True),
                Commit.project_id.in_(list(repos)),
            )
            .limit(2)
        )
    )
    if not found:
        raise HTTPException(404, "không thấy commit trong các repo của dự án")
    if len(found) > 1:
        raise HTTPException(409, "mã commit khớp nhiều commit: nhập dài hơn")
    commit = found[0]
    title = (commit.message or "").splitlines()[0] if commit.message else None
    got = link(
        session,
        task,
        "commit",
        commit.project_id,
        sha=commit.sha,
        title=title,
        relation=relation,
        manual=True,
        linked_by=user_id,
    )
    return got.link


def link_mr_by_hand(
    session: Session, task: Task, project_id: int, iid: int, relation: str, user_id: int
) -> TaskGitLink:
    from fastapi import HTTPException

    project = session.get(Project, project_id)
    if project is None or project.business_project_id != task.business_project_id:
        raise HTTPException(422, "repo không thuộc dự án của task")
    mr = session.scalars(
        select(MergeRequest).where(MergeRequest.project_id == project_id, MergeRequest.iid == iid)
    ).first()
    if mr is None:
        raise HTTPException(404, "không thấy MR (đã sync chưa?)")
    state = "merged" if mr.state == "merged" else "closed" if mr.state == "closed" else "opened"
    got = link(
        session,
        task,
        "mr",
        project_id,
        mr_iid=iid,
        title=mr.title,
        relation=relation,
        state=state,
        manual=True,
        linked_by=user_id,
    )
    return got.link


def candidates(
    session: Session, task: Task, q: str | None, limit: int = 30
) -> list[dict[str, Any]]:
    """Commits of the dự án's repos a person may link to the task, newest first."""
    repos = {p.id: p for p in repos_of(session, task.business_project_id)}
    if not repos:
        return []
    query = select(Commit).where(Commit.project_id.in_(list(repos)), Commit.is_merge.is_not(True))
    text = (q or "").strip()
    if text:
        hexish = len(text) >= 4 and all(c in "0123456789abcdefABCDEF" for c in text)
        if hexish:
            query = query.where(
                Commit.sha.startswith(text.lower(), autoescape=True)
                | Commit.message.ilike(f"%{text}%")
            )
        else:
            query = query.where(Commit.message.ilike(f"%{text}%"))
    linked = set(
        session.scalars(
            select(TaskGitLink.sha).where(
                TaskGitLink.task_id == task.id, TaskGitLink.kind == "commit"
            )
        )
    )
    out = []
    for c in session.scalars(
        query.order_by(Commit.authored_at.desc().nulls_last()).limit(limit * 2)
    ):
        if c.sha in linked:
            continue
        repo = repos.get(c.project_id or -1)
        out.append(
            {
                "sha": c.sha,
                "title": (c.message or "").splitlines()[0] if c.message else None,
                "repo": repo.path_with_namespace if repo else None,
                "project_id": c.project_id,
                "authored_at": c.authored_at.isoformat() if c.authored_at else None,
            }
        )
        if len(out) >= limit:
            break
    return out


def create_branch(
    session: Session, task: Task, project: Project, from_ref: str | None, user_id: int
) -> TaskGitLink:
    """Make `feature|bugfix/<KEY>-<slug>` on GitLab from the default branch and link it."""
    from fastapi import HTTPException

    from app.collector.gitlab.client import GitLabError
    from app.collector.sync import client_factory
    from app.crypto import secret_box
    from app.models import Connection

    if project.business_project_id != task.business_project_id:
        raise HTTPException(422, "repo không thuộc dự án của task")
    connection = session.get(Connection, project.connection_id) if project.connection_id else None
    if connection is None:
        raise HTTPException(409, "repo không có kết nối GitLab")
    name = tk.branch_name(task.key, task.title, task.type)
    ref = from_ref or project.default_branch or "main"
    try:
        with client_factory(secret_box())(connection) as client:
            if not client.branch_exists(project.external_id, name):
                client.post(
                    f"/projects/{project.external_id}/repository/branches",
                    {"branch": name, "ref": ref},
                )
    except GitLabError as exc:
        raise HTTPException(502, f"GitLab không tạo được nhánh: {exc}") from None
    got = link(session, task, "branch", project.id, branch=name, manual=True, linked_by=user_id)
    space = session.get(TaskSpace, task.business_project_id)
    if space is not None:
        apply_rule(session, space, task, "start")
    return got.link


def today() -> date:
    return datetime.now(UTC).date()
