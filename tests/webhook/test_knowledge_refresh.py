"""Knowledge auto refresh by flag (docs/specs/19 §3.4): a default-branch push touching
`.claude/` or CLAUDE.md queues `build_knowledge_job` once an hour when the repo opted in."""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import Connection, Project
from app.webhook.jobs import (
    KNOWLEDGE_JOB_ID,
    KNOWLEDGE_THROTTLE_KEY,
    KNOWLEDGE_THROTTLE_TTL_S,
    handle_event,
    maybe_refresh_knowledge,
    touches_claude,
)


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, int | None] = {}

    def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys[key] = ex
        return True

    def delete(self, key: str) -> int:
        return 1 if self.keys.pop(key, None) is not None else 0


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((f, args, kwargs))


class FakeJob:
    def __init__(self, status: str) -> None:
        self.status = status

    def get_status(self, refresh: bool = False) -> str:
        return self.status


class BusyQueue(FakeQueue):
    """An RQ-like queue that already holds the repo's build."""

    def __init__(self, status: str) -> None:
        super().__init__()
        self.status = status

    def fetch_job(self, job_id: str) -> FakeJob:
        return FakeJob(self.status)


class BrokenQueue(FakeQueue):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("redis down")


def push(*paths: str, key: str = "modified", ref: str = "refs/heads/main") -> dict[str, Any]:
    commit: dict[str, Any] = {"id": "d" * 40, "added": [], "modified": [], "removed": []}
    commit[key] = list(paths)
    return {
        "object_kind": "push",
        "project_id": 1001,
        "project": {"id": 1001, "default_branch": "main"},
        "ref": ref,
        "after": "d" * 40,
        "commits": [commit],
    }


def knowledge_jobs(queue: FakeQueue) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    return [j for j in queue.jobs if j[0] == "app.workers.knowledge_jobs.build_knowledge_job"]


# -- pure ---------------------------------------------------------------------------------


def test_touches_claude_matches_claude_dir_and_root_claude_md() -> None:
    assert touches_claude(push(".claude/rules/tenant.md"))
    assert touches_claude(push("CLAUDE.md", key="added"))
    assert touches_claude(push("/.claude/CLAUDE.md", key="removed"))
    assert not touches_claude(push("app/main.py", "docs/CLAUDE.md"))
    assert not touches_claude(push("src/.claude/x.md"))
    assert not touches_claude({"commits": "junk"})
    assert not touches_claude({"commits": [{"id": "x", "modified": "not-a-list"}]})
    assert not touches_claude({})


def test_maybe_refresh_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    project = Project(external_id=1001, path_with_namespace="d/p", knowledge_auto_refresh=True)
    project.id = 42
    queue, redis = FakeQueue(), FakeRedis()
    assert maybe_refresh_knowledge(project, push(".claude/a.md"), queue, redis)
    ((func, args, kwargs),) = queue.jobs
    assert func == "app.workers.knowledge_jobs.build_knowledge_job"
    assert args == (42,) and kwargs["built_by"] == "auto"
    assert kwargs["job_id"] == KNOWLEDGE_JOB_ID.format(project_id=42) == "knowledge-42"
    assert redis.keys == {KNOWLEDGE_THROTTLE_KEY.format(project_id=42): KNOWLEDGE_THROTTLE_TTL_S}
    assert KNOWLEDGE_THROTTLE_TTL_S == 3600
    # the same hour: throttled
    assert not maybe_refresh_knowledge(project, push(".claude/b.md"), queue, redis)
    assert len(queue.jobs) == 1
    # no redis (tests, tooling): no throttle
    assert maybe_refresh_knowledge(project, push(".claude/b.md"), queue, None)
    # flag off: only a log line
    project.knowledge_auto_refresh = False
    assert not maybe_refresh_knowledge(project, push(".claude/c.md"), queue, FakeRedis())
    assert len(queue.jobs) == 2
    # other files: nothing
    project.knowledge_auto_refresh = True
    assert not maybe_refresh_knowledge(project, push("app/x.py"), queue, FakeRedis())
    assert len(queue.jobs) == 2


def test_active_build_is_not_enqueued_again() -> None:
    project = Project(external_id=1001, path_with_namespace="d/p", knowledge_auto_refresh=True)
    project.id = 7
    for status in ("queued", "started"):
        busy, redis = BusyQueue(status), FakeRedis()
        assert not maybe_refresh_knowledge(project, push(".claude/a.md"), busy, redis)
        assert busy.jobs == [] and redis.keys == {}  # the hour is not consumed either
    done = BusyQueue("finished")
    assert maybe_refresh_knowledge(project, push(".claude/a.md"), done, FakeRedis())
    assert len(done.jobs) == 1


def test_failed_enqueue_releases_the_throttle() -> None:
    project = Project(external_id=1001, path_with_namespace="d/p", knowledge_auto_refresh=True)
    project.id = 8
    redis = FakeRedis()
    with pytest.raises(RuntimeError):
        maybe_refresh_knowledge(project, push(".claude/a.md"), BrokenQueue(), redis)
    assert redis.keys == {}
    assert maybe_refresh_knowledge(project, push(".claude/a.md"), FakeQueue(), redis)


# -- with the database ---------------------------------------------------------------------

pytestmark_db = pytest.mark.integration


@pytest.fixture
def project(db_session: Session) -> Project:
    connection = Connection(name="gl", base_url="https://gitlab.example.com", token_encrypted="x")
    db_session.add(connection)
    db_session.flush()
    project = Project(
        connection_id=connection.id,
        external_id=1001,
        path_with_namespace="d/p",
        default_branch="main",
        knowledge_auto_refresh=True,
    )
    db_session.add(project)
    db_session.flush()
    return project


@pytestmark_db
def test_default_branch_push_touching_claude_queues_build(
    db_session: Session, project: Project
) -> None:
    queue, redis = FakeQueue(), FakeRedis()
    handle_event(db_session, queue, "Push Hook", push(".claude/rules/x.md"), redis=redis)
    (job,) = knowledge_jobs(queue)
    assert job[1] == (project.id,) and job[2]["built_by"] == "auto"
    assert job[2]["job_id"] == f"knowledge-{project.id}"
    # the commit review and the sync are still queued as before
    assert any(j[0] == "app.collector.sync.sync_project_job" for j in queue.jobs)
    assert any(j[0] == "app.workers.review_jobs.review_commit_job" for j in queue.jobs)

    handle_event(db_session, queue, "Push Hook", push(".claude/rules/y.md"), redis=redis)
    assert len(knowledge_jobs(queue)) == 1  # throttled for an hour


@pytestmark_db
def test_other_branches_flag_off_and_review_off(db_session: Session, project: Project) -> None:
    queue = FakeQueue()
    handle_event(db_session, queue, "Push Hook", push(".claude/a.md", ref="refs/heads/dev"))
    assert knowledge_jobs(queue) == []

    project.knowledge_auto_refresh = False
    handle_event(db_session, queue, "Push Hook", push(".claude/a.md"))
    assert knowledge_jobs(queue) == []

    # the knowledge refresh does not need reviews to be on
    project.knowledge_auto_refresh = True
    project.review_enabled = False
    handle_event(db_session, queue, "Push Hook", push("CLAUDE.md"))
    assert len(knowledge_jobs(queue)) == 1
    assert not any(j[0] == "app.workers.review_jobs.review_commit_job" for j in queue.jobs)
