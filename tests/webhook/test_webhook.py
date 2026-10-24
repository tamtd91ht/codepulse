from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_queue, get_secret_box, get_session
from app.api.main import app
from app.crypto import SecretBox
from app.db import get_redis
from app.models import Connection, MergeRequest, Project, Review
from app.webhook.jobs import handle_event

pytestmark = pytest.mark.integration

BOX = SecretBox(Fernet.generate_key().decode())
SECRET = "hook-secret"


class FakeRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((f, args, kwargs))


@pytest.fixture
def project(db_session: Session) -> Project:
    connection = Connection(
        name="gl",
        base_url="https://gitlab.example.com",
        token_encrypted=BOX.encrypt("t"),
        webhook_secret_encrypted=BOX.encrypt(SECRET),
    )
    db_session.add(connection)
    db_session.flush()
    project = Project(connection_id=connection.id, external_id=1001, path_with_namespace="d/p")
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def client(db_session: Session, queue: FakeQueue, project: Project) -> Iterator[TestClient]:
    redis = FakeRedis()
    app.dependency_overrides.update(
        {
            get_session: lambda: db_session,
            get_redis: lambda: redis,
            get_queue: lambda: queue,
            get_secret_box: lambda: BOX,
        }
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client: TestClient, event: str, payload: dict[str, Any], **headers: str) -> Any:
    return client.post(
        "/api/v1/webhooks/gitlab",
        json=payload,
        headers={"X-Gitlab-Token": SECRET, "X-Gitlab-Event": event, **headers},
    )


PUSH = {"object_kind": "push", "project_id": 1001, "project": {"id": 1001}, "after": "d" * 40}


def test_rejects_wrong_or_missing_token(client: TestClient, queue: FakeQueue) -> None:
    assert post(client, "Push Hook", PUSH, **{"X-Gitlab-Token": "nope"}).status_code == 401
    response = client.post(
        "/api/v1/webhooks/gitlab", json=PUSH, headers={"X-Gitlab-Event": "Push Hook"}
    )
    assert response.status_code == 401
    assert queue.jobs == []


def test_unknown_project_is_rejected(client: TestClient) -> None:
    payload = {**PUSH, "project_id": 999, "project": {"id": 999}}
    assert post(client, "Push Hook", payload).status_code == 401


def test_push_is_queued_once_per_event_uuid(client: TestClient, queue: FakeQueue) -> None:
    first = post(client, "Push Hook", PUSH, **{"X-Gitlab-Event-UUID": "u-1"})
    again = post(client, "Push Hook", PUSH, **{"X-Gitlab-Event-UUID": "u-1"})
    assert first.json() == {"status": "queued"}
    assert again.json() == {"status": "duplicate"}
    ((func, args, kwargs),) = queue.jobs
    assert func == "app.webhook.jobs.process_gitlab_event"
    assert args == ("Push Hook", PUSH)
    assert kwargs["job_id"] == "webhook-u-1"


@pytest.mark.parametrize(
    ("event", "action"),
    [("Issue Hook", None), ("Merge Request Hook", "approved"), ("Merge Request Hook", "close")],
)
def test_irrelevant_events_are_ignored(
    client: TestClient, queue: FakeQueue, event: str, action: str | None
) -> None:
    payload = {"project": {"id": 1001}, "object_attributes": {"action": action, "iid": 1}}
    assert post(client, event, payload).json() == {"status": "ignored"}
    assert queue.jobs == []


def test_mr_update_supersedes_old_reviews_and_syncs(
    db_session: Session, project: Project, queue: FakeQueue
) -> None:
    mr = MergeRequest(project_id=project.id, iid=1, head_sha="a" * 40)
    db_session.add(mr)
    db_session.flush()
    old, current, failed = (
        Review(target_type="mr", mr_id=mr.id, reviewed_sha="a" * 40, status="done"),
        Review(target_type="mr", mr_id=mr.id, reviewed_sha="b" * 40, status="queued"),
        Review(
            target_type="mr",
            mr_id=mr.id,
            reviewed_sha="c" * 40,
            status="failed",
            finished_at=datetime.now(UTC),
        ),
    )
    db_session.add_all([old, current, failed])
    db_session.flush()

    payload = {
        "project": {"id": 1001},
        "object_attributes": {
            "iid": 1,
            "action": "update",
            "oldrev": "a" * 40,
            "last_commit": {"id": "b" * 40},
        },
    }
    handle_event(db_session, queue, "Merge Request Hook", payload)
    for review in (old, current, failed):
        db_session.refresh(review)
    assert (old.status, current.status, failed.status) == ("superseded", "queued", "failed")
    assert queue.jobs[0][0] == "app.collector.sync.sync_project_job"
    assert queue.jobs[0][1] == (project.id,)
    func, args, kwargs = queue.jobs[1]
    assert (func, args) == ("app.workers.review_jobs.review_mr_job", (project.id, 1, "b" * 40))
    assert kwargs["job_id"] == f"review-mr-{project.id}-1-{'b' * 12}"
    assert kwargs["retry"].max == 3


def queued_functions(queue: FakeQueue) -> list[str]:
    return [func.rsplit(".", 1)[1] for func, _, _ in queue.jobs]


@pytest.mark.parametrize(
    ("attrs", "reviewed"),
    [
        ({"action": "open"}, True),
        ({"action": "reopen"}, True),
        ({"action": "update"}, False),  # title or label edit: no new commits
        ({"action": "merge"}, False),
    ],
)
def test_mr_events_that_trigger_a_review(
    db_session: Session, project: Project, queue: FakeQueue, attrs: dict[str, Any], reviewed: bool
) -> None:
    payload = {
        "project": {"id": 1001},
        "object_attributes": {"iid": 2, "last_commit": {"id": "e" * 40}, **attrs},
    }
    handle_event(db_session, queue, "Merge Request Hook", payload)
    assert ("review_mr_job" in queued_functions(queue)) is reviewed


def test_disabled_project_is_never_reviewed(
    db_session: Session, project: Project, queue: FakeQueue
) -> None:
    project.review_enabled = False
    payload = {
        "project": {"id": 1001},
        "object_attributes": {"iid": 2, "action": "open", "last_commit": {"id": "e" * 40}},
    }
    handle_event(db_session, queue, "Merge Request Hook", payload)
    assert queued_functions(queue) == ["sync_project_job"]


def push(ref: str, *shas: str, after: str = "f" * 40) -> dict[str, Any]:
    return {
        "project": {"id": 1001, "default_branch": "main"},
        "ref": ref,
        "after": after,
        "commits": [{"id": sha} for sha in shas],
    }


def test_direct_pushes_to_the_default_branch_are_reviewed_unless_in_an_mr(
    db_session: Session, project: Project, queue: FakeQueue
) -> None:
    in_mr = {"1" * 40}
    payload = push("refs/heads/main", "1" * 40, "2" * 40)
    handle_event(db_session, queue, "Push Hook", payload, commit_in_mr=lambda p, sha: sha in in_mr)
    reviews = [args for func, args, _ in queue.jobs if func.endswith("review_commit_job")]
    assert reviews == [(project.id, "2" * 40)]


def test_events_for_an_untracked_repo_are_ignored(
    db_session: Session, project: Project, queue: FakeQueue
) -> None:
    project.tracked = False
    db_session.flush()
    handle_event(db_session, queue, "Push Hook", push("refs/heads/main", "2" * 40))
    assert queue.jobs == []


@pytest.mark.parametrize(
    "payload",
    [push("refs/heads/feature", "2" * 40), push("refs/heads/main", after="0" * 40)],
)
def test_other_pushes_only_sync(
    db_session: Session, project: Project, queue: FakeQueue, payload: dict[str, Any]
) -> None:
    handle_event(db_session, queue, "Push Hook", payload, commit_in_mr=lambda p, sha: False)
    assert queued_functions(queue) == ["sync_project_job"]


def test_undecryptable_secret_is_a_mismatch_not_a_crash(
    db_session: Session, client: TestClient, project: Project
) -> None:
    other = Connection(
        name="rotated",
        base_url="https://gitlab2.example.com",
        token_encrypted="x",
        webhook_secret_encrypted=SecretBox(Fernet.generate_key().decode()).encrypt("s"),
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(Project(connection_id=other.id, external_id=1001, path_with_namespace="o/p"))
    db_session.flush()
    assert post(client, "Push Hook", PUSH).json() == {"status": "queued"}


@pytest.mark.parametrize(
    ("host", "allowed", "ok"),
    [
        ("10.0.0.7", ["10.0.0.0/24"], True),
        ("10.0.1.7", ["10.0.0.0/24"], False),
        ("2001:db8::1", ["2001:db8::/32"], True),
        (None, ["10.0.0.0/24"], False),
        ("not-an-ip", ["10.0.0.0/24"], False),
    ],
)
def test_source_allowlist(host: str | None, allowed: list[str], ok: bool) -> None:
    from app.webhook.router import source_allowed

    assert source_allowed(host, allowed) is ok


def test_webhook_rejects_sources_outside_the_allowlist(
    client: TestClient, queue: FakeQueue
) -> None:
    from app.config import Settings, get_settings

    app.dependency_overrides[get_settings] = lambda: Settings(webhook_allowed_ips=["10.9.9.0/24"])
    assert post(client, "Push Hook", PUSH).status_code == 403  # TestClient comes from "testclient"
    assert queue.jobs == []


def test_the_repo_review_branch_is_for_listing_pushes_to_it_only_sync(
    db_session: Session, project: Project, queue: FakeQueue
) -> None:
    # Reviewing direct pushes to another branch than the default is a decision left open.
    project.review_branch = "develop"
    db_session.flush()
    handle_event(
        db_session,
        queue,
        "Push Hook",
        push("refs/heads/develop", "2" * 40),
        commit_in_mr=lambda p, sha: False,
    )
    assert queued_functions(queue) == ["sync_project_job"]


def test_every_branch_is_for_listing_only_not_for_reviewing_each_push(
    db_session: Session, project: Project, queue: FakeQueue
) -> None:
    project.review_branch = "*"
    db_session.flush()
    payload = push("refs/heads/feature", "2" * 40)
    handle_event(db_session, queue, "Push Hook", payload, commit_in_mr=lambda p, sha: False)
    assert queued_functions(queue) == ["sync_project_job"]
