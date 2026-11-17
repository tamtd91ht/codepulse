"""One timeline of a task (docs/specs/31 §20.1): comments, field changes, worklogs, git events,
reviews of the linked code, test results of the linked cases and the production deploy, newest
first. Read-only; built from the rows the task already has."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.api.security import Principal
from app.models import (
    MergeRequest,
    Review,
    ReviewFinding,
    Task,
    TaskComment,
    TaskGitLink,
    TaskHistory,
    TaskTestLink,
    TaskWorklog,
    TestResult,
    TestRun,
    TestRunTest,
)
from app.tasks import history_text
from app.tasks import service as ts

TYPES = ("comment", "history", "worklog", "git", "review", "test", "deploy")
MAX_LIMIT = 200
MR_STATE_TEXT = {"merged": "đã merge", "closed": "bị đóng không merge", "opened": "đang mở"}
TEST_STATUS_TEXT = {
    "passed": "Passed",
    "failed": "Failed",
    "blocked": "Blocked",
    "retest": "Retest",
    "untested": "Chưa chạy",
}


def parse_types(raw: str | None) -> set[str]:
    """`comment,git` -> {"comment", "git"}; empty means all. Unknown words raise ValueError."""
    if not raw or not raw.strip():
        return set(TYPES)
    wanted = {t.strip().lower() for t in raw.split(",") if t.strip()}
    unknown = wanted - set(TYPES)
    if unknown:
        raise ValueError(", ".join(sorted(unknown)))
    return wanted


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _item(
    kind: str,
    ident: str,
    at: datetime | None,
    actor: int | None,
    text: str,
    data: dict[str, Any],
    link: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{kind}:{ident}",
        "at": at,
        "type": kind,
        "actor": actor,
        "text": text,
        "link": link,
        "data": data,
    }


def _before_ok(at: datetime | None, before: datetime | None) -> bool:
    if at is None:
        return False
    if before is None:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at <= before


def _comments(
    session: Session, principal: Principal, task: Task, before: datetime | None, limit: int
) -> list[dict[str, Any]]:
    q = select(TaskComment).where(TaskComment.task_id == task.id)
    if before is not None:
        q = q.where(TaskComment.created_at <= before)
    moderate = ts.rights(principal, task)["moderate"]
    return [
        _item(
            "comment",
            str(c.id),
            c.created_at,
            c.author_id,
            c.body,
            {
                "id": c.id,
                "body": c.body,
                "edited_at": _iso(c.edited_at),
                "can_edit": c.author_id == principal.user_id or moderate,
            },
        )
        for c in session.scalars(
            q.order_by(TaskComment.created_at.desc(), TaskComment.id.desc()).limit(limit)
        )
    ]


def _history(
    session: Session, task: Task, before: datetime | None, limit: int
) -> list[dict[str, Any]]:
    q = select(TaskHistory).where(TaskHistory.task_id == task.id)
    if before is not None:
        q = q.where(TaskHistory.at <= before)
    out = []
    for h in session.scalars(q.order_by(TaskHistory.at.desc(), TaskHistory.id.desc()).limit(limit)):
        # A sentence, never the raw field name (§21.0, F1).
        event, text = history_text.describe_row(h)
        out.append(
            _item(
                "history",
                str(h.id),
                h.at,
                h.user_id,
                text,
                {"field": h.field, "event": event, "old": h.old, "new": h.new, "via": h.via},
            )
        )
    return out


def _worklogs(
    session: Session, task: Task, before: datetime | None, limit: int
) -> list[dict[str, Any]]:
    q = select(TaskWorklog).where(TaskWorklog.task_id == task.id)
    if before is not None:
        q = q.where(TaskWorklog.created_at <= before)
    out = []
    for w in session.scalars(
        q.order_by(TaskWorklog.created_at.desc(), TaskWorklog.id.desc()).limit(limit)
    ):
        hours, minutes = divmod(w.minutes, 60)
        spent = (f"{hours}h" if hours else "") + (f"{minutes}m" if minutes or not hours else "")
        text = f"ghi {spent} cho ngày {w.day.isoformat()}" + (f": {w.note}" if w.note else "")
        out.append(
            _item(
                "worklog",
                str(w.id),
                w.created_at,
                w.user_id,
                text,
                {
                    "id": w.id,
                    "minutes": w.minutes,
                    "day": w.day.isoformat(),
                    "note": w.note,
                    "via": w.via,
                },
            )
        )
    return out


def _git(
    session: Session, links: list[TaskGitLink], git: dict[str, Any], before: datetime | None
) -> list[dict[str, Any]]:
    urls: dict[int, str | None] = {}
    repos: dict[int, str | None] = {}
    for part in ("branches", "commits", "mrs"):
        for x in git.get(part, []):
            urls[int(x["id"])] = x.get("web_url")
            repos[int(x["id"])] = x.get("repo")
    mr_keys = [(link.project_id, link.mr_iid) for link in links if link.kind == "mr"]
    mrs = (
        {
            (mr.project_id, mr.iid): mr
            for mr in session.scalars(
                select(MergeRequest).where(
                    tuple_(MergeRequest.project_id, MergeRequest.iid).in_(mr_keys)
                )
            )
        }
        if mr_keys
        else {}
    )
    out = []
    for link in links:
        url, repo = urls.get(link.id), repos.get(link.id)
        base = {"kind": link.kind, "repo": repo, "relation": link.relation, "manual": link.manual}
        if link.kind == "branch":
            item = _item(
                "git",
                f"{link.id}",
                link.created_at,
                link.linked_by,
                f"nhánh {link.branch} được nối",
                {**base, "branch": link.branch},
                url,
            )
            if _before_ok(item["at"], before):
                out.append(item)
            continue
        if link.kind == "commit":
            sha = (link.sha or "")[:8]
            item = _item(
                "git",
                f"{link.id}",
                link.created_at,
                link.linked_by,
                f"commit {sha}" + (f" {link.title}" if link.title else ""),
                {**base, "sha": link.sha, "title": link.title},
                url,
            )
            if _before_ok(item["at"], before):
                out.append(item)
            continue
        mr = mrs.get((link.project_id, link.mr_iid or 0))
        state = (mr.state if mr else None) or link.state
        title = (mr.title if mr else None) or link.title
        opened = _item(
            "git",
            f"{link.id}",
            link.created_at,
            link.linked_by,
            f"MR !{link.mr_iid} được nối" + (f": {title}" if title else ""),
            {**base, "mr_iid": link.mr_iid, "title": title, "state": state},
            url,
        )
        if _before_ok(opened["at"], before):
            out.append(opened)
        # Where it ended: merged or closed without merge, at the time GitLab says.
        ended = mr.merged_at if mr and state == "merged" else None
        if ended is None and mr and state == "closed":
            ended = mr.closed_at
        if ended is not None and state in ("merged", "closed"):
            item = _item(
                "git",
                f"{link.id}:{state}",
                ended,
                None,
                f"MR !{link.mr_iid} {MR_STATE_TEXT[state]}",
                {**base, "mr_iid": link.mr_iid, "title": title, "state": state},
                url,
            )
            if _before_ok(item["at"], before):
                out.append(item)
    return out


def _reviews(
    session: Session, links: list[TaskGitLink], before: datetime | None, limit: int
) -> list[dict[str, Any]]:
    shas = {link.sha for link in links if link.kind == "commit" and link.sha}
    mr_keys = [(link.project_id, link.mr_iid) for link in links if link.kind == "mr"]
    mr_of: dict[int, int | None] = {}
    if mr_keys:
        for mid, iid in session.execute(
            select(MergeRequest.id, MergeRequest.iid).where(
                tuple_(MergeRequest.project_id, MergeRequest.iid).in_(mr_keys)
            )
        ).all():
            mr_of[int(mid)] = int(iid) if iid is not None else None
    if not shas and not mr_of:
        return []
    conds = []
    if shas:
        conds.append(Review.commit_sha.in_(shas))
    if mr_of:
        conds.append(Review.mr_id.in_(list(mr_of)))
    when = func.coalesce(Review.finished_at, Review.started_at)
    q = select(Review).where(or_(*conds), Review.status == "done", when.is_not(None))
    if before is not None:
        q = q.where(when <= before)
    reviews = list(session.scalars(q.order_by(when.desc(), Review.id.desc()).limit(limit)))
    counts: dict[int, dict[str, int]] = defaultdict(dict)
    if reviews:
        for review_id, severity, n in session.execute(
            select(ReviewFinding.review_id, ReviewFinding.severity, func.count())
            .where(
                ReviewFinding.review_id.in_([r.id for r in reviews]),
                ReviewFinding.severity.in_(("critical", "high")),
            )
            .group_by(ReviewFinding.review_id, ReviewFinding.severity)
        ).all():
            if review_id is not None:
                counts[int(review_id)][str(severity)] = int(n)
    out = []
    for r in reviews:
        at = r.finished_at or r.started_at
        if not _before_ok(at, before):
            continue
        score = None if r.score is None else float(r.score)
        crit = counts[r.id].get("critical", 0)
        high = counts[r.id].get("high", 0)
        target = (
            f"MR !{mr_of.get(r.mr_id)}"
            if r.mr_id is not None
            else f"commit {(r.commit_sha or '')[:8]}"
        )
        text = f"review {target}" + (f": điểm {score:.1f}" if score is not None else "")
        text += f", {crit} critical" + (f", {high} high" if high else "")
        out.append(
            _item(
                "review",
                str(r.id),
                at,
                None,
                text,
                {
                    "review_id": r.id,
                    "score": score,
                    "verdict": r.verdict,
                    "critical": crit,
                    "high": high,
                    "target": target,
                },
                f"/reviews/{r.id}",
            )
        )
    return out


def _tests(
    session: Session, principal: Principal, task: Task, before: datetime | None, limit: int
) -> list[dict[str, Any]]:
    if not principal.can("tests.view"):
        return []
    from app.testcases import content

    kinds: dict[int, str] = {}
    for case_id, kind in session.execute(
        select(TaskTestLink.case_id, TaskTestLink.kind).where(TaskTestLink.task_id == task.id)
    ).all():
        kinds.setdefault(int(case_id), str(kind))
    if not kinds:
        return []
    # The latest result (with a status) of each test of those cases in each run.
    latest = (
        select(
            TestResult.id.label("rid"),
            func.row_number()
            .over(partition_by=TestResult.test_id, order_by=TestResult.id.desc())
            .label("n"),
        )
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .where(TestRunTest.case_id.in_(list(kinds)), TestResult.status.is_not(None))
        .subquery()
    )
    q = (
        select(TestResult, TestRunTest, TestRun)
        .join(latest, latest.c.rid == TestResult.id)
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(latest.c.n == 1)
    )
    visible = principal.visible_projects()
    if visible is not None:
        q = q.where(TestRun.business_project_id.in_(visible))
    if before is not None:
        q = q.where(TestResult.created_at <= before)
    out = []
    for result, test, run in session.execute(
        q.order_by(TestResult.created_at.desc(), TestResult.id.desc()).limit(limit)
    ).all():
        status = result.status or "untested"
        case_key = content.key("case", test.case_id)
        out.append(
            _item(
                "test",
                str(result.id),
                result.created_at,
                result.created_by,
                f"{case_key} {TEST_STATUS_TEXT.get(status, status)} trong run “{run.name}”",
                {
                    "case_id": test.case_id,
                    "case_key": case_key,
                    "title": test.title,
                    "status": status,
                    "kind": kinds.get(test.case_id or 0, "covers"),
                    "run_id": run.id,
                    "run": run.name,
                },
                f"/testcases?project={run.business_project_id}&tab=runs&run={run.id}",
            )
        )
    return out


def _deploy(git: dict[str, Any], before: datetime | None) -> list[dict[str, Any]]:
    deployed = git["summary"].get("deployed")
    if not deployed or not deployed.get("at"):
        return []
    at = datetime.fromisoformat(str(deployed["at"]))
    if not _before_ok(at, before):
        return []
    env = deployed.get("environment")
    return [
        _item(
            "deploy",
            "production",
            at,
            None,
            f"lên production ({env})" if env else "lên production",
            {"environment": env},
        )
    ]


def timeline(
    session: Session,
    principal: Principal,
    task: Task,
    types: set[str],
    before: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Up to `limit` items at or before `before`, newest first. The cursor is inclusive: the next
    page repeats the items sharing the last timestamp and the client drops those it has (by id)."""
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    limit = max(1, min(limit, MAX_LIMIT))
    items: list[dict[str, Any]] = []
    if "comment" in types:
        items += _comments(session, principal, task, before, limit)
    if "history" in types:
        items += _history(session, task, before, limit)
    if "worklog" in types:
        items += _worklogs(session, task, before, limit)
    if types & {"git", "review", "deploy"}:
        links = list(
            session.scalars(
                select(TaskGitLink)
                .where(TaskGitLink.task_id == task.id)
                .order_by(TaskGitLink.created_at, TaskGitLink.id)
            )
        )
        if types & {"git", "deploy"}:
            git = ts.git_detail(session, task)
            if "git" in types:
                items += _git(session, links, git, before)
            if "deploy" in types:
                items += _deploy(git, before)
        if "review" in types:
            items += _reviews(session, links, before, limit)
    if "test" in types:
        items += _tests(session, principal, task, before, limit)

    def key(it: dict[str, Any]) -> datetime:
        at: datetime = it["at"]
        return at if at.tzinfo else at.replace(tzinfo=UTC)

    items = [it for it in items if it["at"] is not None]

    def tie(it: dict[str, Any]) -> tuple[datetime, str, int, str]:
        head = str(it["id"]).split(":")[1]
        return (key(it), it["type"], int(head) if head.isdigit() else 0, str(it["id"]))

    items.sort(key=tie, reverse=True)
    items = items[:limit]
    people = ts.users_by_id(session, [it["actor"] for it in items])
    for it in items:
        it["at"] = _iso(it["at"])
        actor = it["actor"]
        it["actor"] = ts.person(people.get(actor)) if actor is not None else None
    return items
