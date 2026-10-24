"""Response and request bodies of the REST API (docs/specs/09). OpenAPI is generated from these."""

import base64
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, StringConstraints


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
    repos: int = 0  # repos registered through it (tracked or not); a connection with repos stays


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
    business_project_id: int | None
    tracked: bool
    untracked_at: datetime | None


class BusinessProjectIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: str | None = Field(default=None, max_length=500)


class BusinessProjectOut(ORM):
    id: int
    name: str
    description: str | None
    repos: int = 0  # tracked repos assigned to it


class RepoAssignment(BaseModel):
    project_ids: list[int] = Field(min_length=1, max_length=1000)
    business_project_id: int | None  # null removes the repos from their dự án


class RepoRemoval(BaseModel):
    project_ids: list[int] = Field(min_length=1, max_length=1000)
    # Typed by the admin: the repo path for one repo, "xoa <n> repo" for several.
    confirm: str = Field(min_length=1, max_length=300)


class RepoRemovalOut(BaseModel):
    removed: list[str]
    reviews_deleted: int
    # False when the clean-up jobs could not be queued (Redis down); see not_queued.
    cleanup_queued: bool
    not_queued: list[str]


class ProjectIn(BaseModel):
    connection_id: int
    external_id: int = Field(gt=0)


class GroupImportIn(BaseModel):
    connection_id: int
    # Numeric id or full path, e.g. "vihat/backend".
    group: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    register_webhooks: bool = True
    business_project_id: int | None = None  # assign the newly added repos to this dự án


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


# -- notification webhooks (docs/specs/16) --------------------------------------------------------

WebhookEvent = Literal["review.done", "review.failed", "review.severe"]
WebhookFormat = Literal["json", "slack", "teams"]


def _no_userinfo(url: HttpUrl) -> HttpUrl:
    # user:password@host would be sent as Basic auth and stored/returned in clear; use a secret.
    if url.username or url.password:
        raise ValueError("credentials in the URL are not allowed; use the secret field")
    return url


WebhookUrl = Annotated[HttpUrl, AfterValidator(_no_userinfo)]  # http(s), no userinfo
WebhookSecret = Annotated[str, StringConstraints(min_length=16, max_length=200)]


class NotificationWebhookOut(BaseModel):
    id: int
    name: str
    url: str
    format: str
    has_secret: bool  # the secret itself is never returned
    events: list[str]
    business_project_id: int | None
    project_id: int | None
    enabled: bool
    last_delivery_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    failures: int
    deliveries: int
    created_at: datetime


class NotificationWebhookIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    url: WebhookUrl
    format: WebhookFormat = "json"
    secret: WebhookSecret | None = None
    events: list[WebhookEvent] = Field(min_length=1, max_length=3)
    business_project_id: int | None = None
    project_id: int | None = None  # pinned repo; with business_project_id it must belong to it
    enabled: bool = True


class NotificationWebhookPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    url: WebhookUrl | None = None
    format: WebhookFormat | None = None
    secret: WebhookSecret | Literal[""] | None = None  # "" (or null) removes it
    events: list[WebhookEvent] | None = Field(default=None, min_length=1, max_length=3)
    business_project_id: int | None = None
    project_id: int | None = None
    enabled: bool | None = None


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


class MemberOut(BaseModel):
    user_id: int
    full_name: str | None
    primary_email: str | None
    role: str | None
    joined_at: date
    left_at: date | None


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


# -- telegram -------------------------------------------------------------------------------------


class TelegramBotOut(ORM):
    id: int
    name: str
    bot_username: str | None
    business_project_id: int
    project_id: int | None
    enabled: bool
    allowed_chat_ids: list[int] | None
    last_update_id: int | None
    last_polled_at: datetime | None
    last_error: str | None  # the token is never returned
    created_at: datetime


class TelegramBotIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    token: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^\d+:[A-Za-z0-9_-]{20,}$")
    ]
    business_project_id: int
    project_id: int | None = None  # pin the bot to one repo of the dự án
    enabled: bool = True
    allowed_chat_ids: list[int] | None = Field(default=None, max_length=50)


class TelegramBotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    token: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, pattern=r"^\d+:[A-Za-z0-9_-]{20,}$")
        ]
        | None
    ) = None
    project_id: int | None = None
    enabled: bool | None = None
    allowed_chat_ids: list[int] | None = Field(default=None, max_length=50)


class TelegramRequestOut(BaseModel):
    id: int
    chat_id: int
    user_id: int | None
    user_name: str | None
    command: str
    targets: list[dict[str, object]]
    status: str
    created_at: datetime
    notified_at: datetime | None


class TelegramAccountOut(ORM):
    id: int
    external_key: str  # telegram user id
    display_name: str | None


class TelegramLinkCodeOut(BaseModel):
    code: str
    expires_in_s: int


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
