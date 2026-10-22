from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.api.deps import get_queue, get_secret_box, get_session
from app.api.main import app
from app.api.security import issue_token
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import (
    Commit,
    Connection,
    MergeRequest,
    MetricsTeamPeriod,
    MetricsUserPeriod,
    Project,
    Review,
    ReviewFinding,
    Team,
    TeamMember,
    User,
)

BOX = SecretBox(Fernet.generate_key().decode())
WEEK = date(2026, 10, 5)


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((f, args, kwargs))


@dataclass
class World:
    admin: User
    lead: User
    manager: User
    an: User
    binh: User
    outsider: User
    backend: Team
    engineering: Team
    mobile: Team
    project: Project
    mr: MergeRequest
    an_review: Review
    binh_review: Review
    an_finding: ReviewFinding


@pytest.fixture
def settings() -> Settings:
    return Settings(
        session_secret=SecretStr("s" * 48),
        admin_emails=["admin@x.vn"],
        gitlab_oauth_url="https://gitlab.example.com",
        gitlab_oauth_client_id="cid",
        gitlab_oauth_client_secret=SecretStr("csecret"),
        app_base_url="https://codepulse.example.com",
        dashboard_url="https://dash.example.com",
    )


@pytest.fixture
def world(db_session: Session) -> World:
    s = db_session
    users = {
        name: User(full_name=name.title(), primary_email=f"{name}@x.vn")
        for name in ("admin", "lead", "manager", "an", "binh", "outsider")
    }
    engineering = Team(name="Engineering")
    s.add_all([*users.values(), engineering])
    s.flush()
    backend = Team(name="Backend", parent_id=engineering.id)
    mobile = Team(name="Mobile")
    s.add_all([backend, mobile])
    s.flush()
    joined = date(2025, 1, 1)
    s.add_all(
        [
            TeamMember(team_id=backend.id, user_id=users["lead"].id, role="lead", joined_at=joined),
            TeamMember(team_id=backend.id, user_id=users["an"].id, role="member", joined_at=joined),
            TeamMember(
                team_id=backend.id, user_id=users["binh"].id, role="member", joined_at=joined
            ),
            TeamMember(
                team_id=engineering.id,
                user_id=users["manager"].id,
                role="manager",
                joined_at=joined,
            ),
            TeamMember(team_id=mobile.id, user_id=users["outsider"].id, joined_at=joined),
        ]
    )
    connection = Connection(
        name="gl", base_url="https://gitlab.example.com", token_encrypted=BOX.encrypt("t")
    )
    s.add(connection)
    s.flush()
    project = Project(connection_id=connection.id, external_id=1001, path_with_namespace="demo/p")
    s.add(project)
    s.flush()
    mr = MergeRequest(project_id=project.id, iid=7, head_sha="a" * 40)
    s.add_all([mr, Commit(sha="b" * 40, project_id=project.id)])
    s.flush()
    finished = datetime(2026, 10, 6, 9, tzinfo=UTC)
    an_review = Review(
        target_type="mr",
        mr_id=mr.id,
        reviewed_sha="a" * 40,
        status="done",
        author_user_id=users["an"].id,
        score=Decimal(50),
        finished_at=finished,
        rubric_version="v1",
    )
    binh_review = Review(
        target_type="commit",
        commit_sha="b" * 40,
        reviewed_sha="b" * 40,
        status="failed",
        author_user_id=users["binh"].id,
        finished_at=finished,
        rubric_version="v1",
    )
    s.add_all([an_review, binh_review])
    s.flush()
    an_finding = ReviewFinding(
        review_id=an_review.id,
        severity="critical",
        category="security",
        file_path="app/a.py",
        line_start=3,
        title="SQL injection",
        confidence=Decimal("0.9"),
    )
    s.add(an_finding)
    for user in (users["an"], users["binh"]):
        s.add(
            MetricsUserPeriod(
                user_id=user.id,
                period_type="week",
                period_start=WEEK,
                reviews_count=3,
                avg_score=Decimal(70),
                quality_index=Decimal(75),
            )
        )
    s.add(
        MetricsTeamPeriod(
            team_id=backend.id,
            period_type="week",
            period_start=WEEK,
            reviews_count=6,
            avg_score=Decimal(70),
            quality_index=Decimal(75),
        )
    )
    s.flush()
    return World(
        **users,
        backend=backend,
        engineering=engineering,
        mobile=mobile,
        project=project,
        mr=mr,
        an_review=an_review,
        binh_review=binh_review,
        an_finding=an_finding,
    )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def client(db_session: Session, settings: Settings, queue: FakeQueue) -> Iterator[TestClient]:
    app.dependency_overrides.update(
        {
            get_session: lambda: db_session,
            get_settings: lambda: settings,
            get_queue: lambda: queue,
            get_secret_box: lambda: BOX,
        }
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def as_user(settings: Settings) -> Any:
    def headers(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {issue_token(user.id, settings)}"}

    return headers
