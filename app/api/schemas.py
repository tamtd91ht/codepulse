"""Response and request bodies of the REST API (docs/specs/09). OpenAPI is generated from these."""

import base64
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(last_id: int) -> str:
    return base64.urlsafe_b64encode(str(last_id).encode()).decode()


def decode_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeDecodeError):
        return None


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# -- admin ----------------------------------------------------------------------------------------


class ConnectionOut(ORM):
    id: int
    name: str
    kind: str
    base_url: str  # tokens and webhook secrets are never returned


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: HttpUrl
    token: str = Field(min_length=8)
    webhook_secret: str | None = Field(default=None, min_length=16)


class ConnectionPatch(BaseModel):
    # base_url is fixed: projects, MRs and accounts belong to that GitLab instance, and the
    # stored token must never be sent to another host. Register a new connection instead.
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    token: str | None = Field(default=None, min_length=8)  # rotation: revoke the old one in GitLab


class ProjectOut(ORM):
    id: int
    connection_id: int | None
    external_id: int
    path_with_namespace: str
    default_branch: str | None
    review_enabled: bool | None
    post_mr_comment: bool | None
    allow_external_llm: bool | None
    last_synced_at: datetime | None


class ProjectIn(BaseModel):
    connection_id: int
    external_id: int = Field(gt=0)


class GroupImportIn(BaseModel):
    connection_id: int
    # Numeric id or full path, e.g. "vihat/backend".
    group: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    register_webhooks: bool = True


class GroupImportOut(BaseModel):
    added: list[str]  # path_with_namespace of each new project
    skipped: int  # already registered
    not_queued: list[str]  # webhook/sync jobs could not be queued (synced later by the scheduler)


class ProjectPatch(BaseModel):
    review_enabled: bool | None = None
    post_mr_comment: bool | None = None
    allow_external_llm: bool | None = None


class UserOut(ORM):
    id: int
    full_name: str | None
    primary_email: str | None
    active: bool | None


class UserIn(BaseModel):
    full_name: str = Field(min_length=1)
    primary_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    active: bool = True


class UserPatch(BaseModel):
    full_name: str | None = None
    active: bool | None = None


class TeamOut(ORM):
    id: int
    name: str
    parent_id: int | None


class TeamIn(BaseModel):
    name: str = Field(min_length=1)
    parent_id: int | None = None


class MemberIn(BaseModel):
    user_id: int
    role: Literal["member", "lead", "manager"] = "member"
    joined_at: date
    left_at: date | None = None


class AccountOut(ORM):
    id: int
    kind: str
    external_key: str
    display_name: str | None
    user_id: int | None


class AccountLink(BaseModel):
    user_id: int | None  # None unlinks


# -- reviews --------------------------------------------------------------------------------------


class FindingOut(ORM):
    id: int
    review_id: int | None
    severity: str
    category: str
    file_path: str | None
    line_start: int | None
    line_end: int | None
    title: str | None
    message: str | None
    suggestion: str | None
    code_excerpt: str | None
    confidence: Decimal | None
    status: str | None


class BoardFindingOut(FindingOut):
    project: str
    author: str | None
    author_user_id: int | None
    target: str  # "MR !12" or "commit abc12345"
    web_url: str | None
    age_days: int


class ReviewOut(ORM):
    id: int
    target_type: str
    mr_id: int | None
    commit_sha: str | None
    reviewed_sha: str
    author_user_id: int | None
    status: str
    score: Decimal | None
    loc_reviewed: int | None
    summary: str | None
    model: str | None
    rubric_version: str | None
    cost_usd: Decimal | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ReviewListItem(ReviewOut):
    target: str  # "MR !12" or "commit abc12345"
    project: str | None
    web_url: str | None


class ReviewDetail(ReviewOut):
    findings: list[FindingOut]
    project: str | None
    web_url: str | None


# -- metrics --------------------------------------------------------------------------------------


class MetricsOut(ORM):
    period_type: str
    period_start: date
    reviews_count: int | None
    avg_score: Decimal | None
    loc_changed: int | None
    critical: int | None
    high: int | None
    medium: int | None
    low: int | None
    info: int | None
    defect_density: Decimal | None
    mr_count: int | None
    avg_review_time_h: Decimal | None
    avg_mr_size: int | None
    avg_review_rounds: Decimal | None
    build_success_rate: Decimal | None
    fix_rate: Decimal | None
    quality_index: Decimal | None


class MetricsSeries(BaseModel):
    owner: str  # "user:3" / "team:2"
    series: list[MetricsOut]
    delta: dict[str, float | None]  # latest period vs the one before (docs/specs/08 §3)
    team_quality_index: float | None = None


class LeaderboardRow(MetricsOut):
    user: UserOut
    trend: list[Decimal | None]  # quality index of the last 8 weeks, oldest first (sparkline)


class Hotspots(BaseModel):
    categories: list[dict[str, str | int]]  # [{"category": "testing", "count": 12}]
    files: list[dict[str, str | int]]  # [{"file_path": "app/x.py", "count": 5}]


class CategoryWeek(BaseModel):
    week: date
    category: str
    count: int


class Leaderboard(BaseModel):
    team: TeamOut
    period_type: str
    period_start: date
    team_metrics: MetricsOut | None
    members: list[LeaderboardRow] | None  # None for managers: aggregates only (Phase 0)
