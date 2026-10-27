"""Response and request bodies of the REST API (docs/specs/09). OpenAPI is generated from these."""

import base64
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.source import ALL_BRANCHES

# Git ref characters GitLab accepts in practice; no spaces, no "..", no leading "-".
BRANCH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./+@-]{0,254}")


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


def clean_branch(value: str) -> str:
    """A git branch name as GitLab accepts it, "" (default branch) or "*" (every branch)."""
    value = value.strip()
    if value in ("", ALL_BRANCHES):
        return value
    parts = value.split("/")
    if (
        not BRANCH_RE.fullmatch(value)
        or ".." in value
        or "@{" in value
        or value == "@"
        or any(not p or p.startswith(".") or p.endswith(".lock") for p in parts)
        or value.endswith(".")
    ):
        raise ValueError("not a valid branch name")
    return value


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
    review_rules: str | None = None
    review_rules_paths: str | None = None
    deep_review: bool = True
    knowledge_auto_refresh: bool = False  # docs/specs/19 §3.4
    review_branch: str | None = None  # NULL: default branch; "*": every branch
    profile_at: datetime | None = None


RULES_MAX = 12_000  # markdown that goes into every review prompt of the scope (docs/specs/17)


class BusinessProjectIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    description: str | None = Field(default=None, max_length=500)
    # Omitted = keep the current rules; "" or null when present = clear them.
    review_rules: str | None = Field(default=None, max_length=RULES_MAX)


class BusinessProjectOut(ORM):
    id: int
    name: str
    description: str | None
    review_rules: str | None = None
    repos: int = 0  # tracked repos assigned to it
    # Optional shared .claude repo (see app/review/claude_repo.py).
    claude_repo_connection_id: int | None = None
    claude_repo_path: str | None = None
    claude_repo_ref: str | None = None
    claude_repo_include: str | None = None


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
    model_config = ConfigDict(extra="forbid")

    review_enabled: bool | None = None
    post_mr_comment: bool | None = None
    allow_external_llm: bool | None = None
    review_rules: str | None = Field(default=None, max_length=RULES_MAX)
    # Comma/newline separated paths inside the repo, read on the default branch (max 10).
    review_rules_paths: str | None = Field(default=None, max_length=2000)
    deep_review: bool | None = None  # docs/specs/18
    knowledge_auto_refresh: bool | None = None  # docs/specs/19 §3.4
    # docs/specs/17 §Nhánh review: "" clears (default branch), "*" every branch.
    review_branch: str | None = Field(default=None, max_length=255)

    @field_validator("review_branch")
    @classmethod
    def _branch_name(cls, value: str | None) -> str | None:
        return None if value is None else clean_branch(value)


class BranchBulkIn(BaseModel):
    """One review branch for many repos (docs/specs/17 §Nhánh review)."""

    model_config = ConfigDict(extra="forbid")

    project_ids: list[int] = Field(min_length=1, max_length=500)
    review_branch: str = Field(max_length=255)  # "" = default branch, "*" = every branch

    @field_validator("review_branch")
    @classmethod
    def _branch_name(cls, value: str) -> str:
        return clean_branch(value)


class BranchBulkOut(BaseModel):
    updated: int
    # Repos where GitLab has no branch of that name (checked for small batches only).
    missing: list[str] = []
    checked: bool = False


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
    rule_id: str | None = None
    file_path: str | None
    line_start: int | None
    line_end: int | None
    title: str | None
    message: str | None
    suggestion: str | None
    code_excerpt: str | None
    confidence: Decimal | None
    status: str | None
    # docs/specs/19 §4.3: the independent verification's verdict on this finding.
    verified: str | None = None
    verification: dict[str, Any] | None = None


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
    # docs/specs/17
    verdict: str | None = None
    risk_score: int | None = None
    intent: dict[str, Any] | None = None
    trigger: str = "webhook"
    requested_by_user_id: int | None = None
    mode: str = "fast"  # docs/specs/18: fast | deep
    deep: dict[str, Any] | None = None
    round: int = 1
    superseded_by: int | None = None
    basis: dict[str, Any] | None = None  # docs/specs/17 §Căn cứ review


class ReviewListItem(ReviewOut):
    target: str  # "MR !12" or "commit abc12345"
    project: str | None
    web_url: str | None


class ReviewRound(BaseModel):
    """One earlier/later review of the same target (docs/specs/17 §Lịch sử)."""

    id: int
    round: int
    reviewed_sha: str
    status: str
    score: Decimal | None
    verdict: str | None
    mode: str
    trigger: str
    finished_at: datetime | None
    basis: dict[str, Any] | None = None


class ReviewDetail(ReviewOut):
    findings: list[FindingOut]
    project: str | None
    web_url: str | None
    history: list[ReviewRound] = []  # every review of this MR / commit, oldest first
    can_rerun: bool = False  # a deliberate new round is possible (commit, or MR still open)


class RerunIn(BaseModel):
    # force: review a finished head again as a new round (the old one is kept, superseded)
    force: bool = False


# -- review on request (docs/specs/17) ------------------------------------------------------------


class RepoOut(BaseModel):
    id: int
    path_with_namespace: str
    default_branch: str | None
    review_branch: str | None = None
    business_project_id: int | None
    business_project: str | None
    review_enabled: bool


class BranchOut(BaseModel):
    name: str
    default: bool
    last_commit_at: str | None


class CommitRowOut(BaseModel):
    sha: str
    short_sha: str
    title: str
    authored_at: str | None
    author_name: str | None
    author_email: str | None = None  # admins only: to link an unmapped author
    user_id: int | None
    user_name: str | None
    is_merge: bool
    additions: int | None
    deletions: int | None
    review_id: int | None
    review_status: str | None
    score: float | None
    verdict: str | None
    reviewable: bool
    reason: str | None  # why not reviewable


class CommitListOut(BaseModel):
    ref: str
    commits: list[CommitRowOut]
    hidden: int  # commits by people outside the caller's scope
    truncated: bool  # more commits exist in the window than `limit`
    batch_max: int  # REVIEW_BATCH_MAX: how many may be queued in one request


class ReviewCommitsIn(BaseModel):
    ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    shas: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")]] = Field(
        min_length=1, max_length=200
    )


class ReviewCommitsOut(BaseModel):
    queued: list[str]
    skipped: list[dict[str, str]]  # {sha, reason}


class RepoProfileOut(BaseModel):
    id: int
    path_with_namespace: str
    business_project_id: int | None
    deep_review: bool
    profile: dict[str, Any] | None
    profile_at: datetime | None
    profile_sha: str | None


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
    # [from, to) instants of the latest period, for links to the findings it counts.
    period_range: tuple[datetime, datetime] | None = None


class LeaderboardRow(MetricsOut):
    user: UserOut
    trend: list[Decimal | None]  # quality index of the last 8 weeks, oldest first (sparkline)
    viewable: bool = True  # the viewer may list this person's findings (not someone who left)


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


# -- reviews in progress (docs/specs/17 §Tiến trình) ---------------------------------------------


class ActivityItem(BaseModel):
    state: Literal["running", "queued", "ended"]
    review_id: int | None = None  # running only; the page exists once the review ends
    # "knowledge": a review-knowledge build of the repo (docs/specs/19), shown in the same panel
    kind: Literal["mr", "commit", "knowledge"]
    project_id: int
    project: str
    ref: str  # MR iid, commit sha, or the branch the knowledge was built from
    sha: str
    title: str
    author: str | None = None
    author_user_id: int | None = None
    requested_by: str | None = None
    trigger: str
    round: int = 1
    started_at: datetime | None = None
    enqueued_at: datetime | None = None
    stage: str | None = (
        None  # start | diff | checkout | llm | agent | verify | save; knowledge: docs | compile | probe | save
    )
    detail: str = ""
    stage_at: datetime | None = None
    loc: int | None = None
    files: int | None = None
    web_url: str | None = None
    status: str | None = None  # ended: done | failed | skipped; None = the job crashed
    ended_at: datetime | None = None


class ActivityOut(BaseModel):
    running: list[ActivityItem]
    queued: list[ActivityItem]  # the first ones you may see
    queued_total: int  # all waiting review jobs you may see
    recent: list[ActivityItem] = []  # ended in the last 15 minutes, newest first
    hidden: int  # running or waiting reviews of people outside your scope (count only)


# -- Claude subscription token set from the Admin screen (outside the spec; see PROGRESS.md) ----


class LlmTokenIn(BaseModel):
    # Only a subscription token (`claude setup-token`); an API key stays in LLM_API_KEY.
    token: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^sk-ant-oat\d*-[A-Za-z0-9_-]{20,}$")
    ]


class LlmTokenOut(BaseModel):
    # What reviews use now: stored (dashboard) > env_oauth > env_api_key > none.
    active: Literal["stored", "env_oauth", "env_api_key", "none"]
    token_hint: str | None  # masked, never the token
    stored: bool
    stored_source: str | None = None  # login | paste
    stored_by: str | None = None  # the admin's name
    stored_at: datetime | None = None
    login_supported: bool  # whether "Đăng nhập Claude" can run on this host


class LlmLoginOut(BaseModel):
    login_id: str
    url: str
    expires_in_s: int


class LlmLoginCodeIn(BaseModel):
    # The code claude.ai shows after sign-in ("<code>#<state>").
    code: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_#.\-]{8,512}$")
    ]


# -- review settings edited on the Admin screen (outside the spec; see PROGRESS.md) --------------


class ReviewSettingOut(BaseModel):
    key: str
    env: str  # the environment variable it overrides
    kind: str  # int | model | url
    value: int | str  # what reviews use now
    default: int | str  # from the environment (or the built-in default)
    overridden: bool
    minimum: int | None = None  # int settings only
    maximum: int | None = None


class ReviewSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None = back to the environment default; app/admin_settings.clean checks the values.
    max_review_loc: Annotated[int, Field(ge=1, le=1_000_000, strict=True)] | None = None
    llm_model: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    public_url: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    claude_file_chars: Annotated[int, Field(strict=True)] | None = None
    fast_guidance_chars: Annotated[int, Field(strict=True)] | None = None
    deep_instructions_chars: Annotated[int, Field(strict=True)] | None = None


class LiveEvent(BaseModel):
    """One action of the deep pass (app/review/live.py): no file contents, no model prose."""

    kind: str  # start | thinking | tool | answer | end
    turn: int = 0
    at: float | None = None  # unix seconds
    tool: str | None = None
    target: str | None = None  # Read: path; Grep/Glob: pattern
    path: str | None = None
    glob: str | None = None
    lines: int | None = None
    model: str | None = None
    ok: bool | None = None
    turns: int | None = None


class LiveReviewOut(BaseModel):
    review_id: int
    running: bool
    events: list[LiveEvent]


# -- the dự án's shared .claude repo (outside the spec; see PROGRESS.md) --------------------------


class ClaudeRepoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: int
    # GitLab full path, e.g. "omicrm/claude-config"; a pasted repo link or clone URL is accepted
    # and reduced to the path.
    path: Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]
    ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None = None
    # Extra globs relative to the .claude folder (comma or newline separated).
    include: str | None = Field(default=None, max_length=2000)
    # From a pasted folder link (…/-/tree/dev/.claude): the words after /-/tree/, which hold
    # the branch and the folder; the router tells them apart by asking GitLab.
    tree: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def _tree_of_link(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("path"), str):
            return {**data, "tree": gitlab_tree_words(data["path"])}
        return data

    @field_validator("path")
    @classmethod
    def _repo_path(cls, value: str) -> str:
        return gitlab_repo_path(value)


GITLAB_PATH_RE = re.compile(r"[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)+")


def gitlab_repo_path(value: str) -> str:
    """ "group/sub/repo" from what an admin pastes: the path itself, the repo's web link (also a
    deeper one such as …/-/tree/main), or an https / ssh clone URL."""
    text = value.strip()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+", "", text, flags=re.IGNORECASE)  # https://host
    text = re.sub(r"^[^@/\s]+@[^:/\s]+:", "", text)  # git@host:
    text = text.split("/-/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.strip("/").removesuffix(".git")
    if not GITLAB_PATH_RE.fullmatch(text):
        raise ValueError("cần dạng nhóm/repo hoặc link repo GitLab, ví dụ omicrm/claude-config")
    return text


def gitlab_tree_words(value: str) -> list[str]:
    """["dev", ".claude"] from a link like …/-/tree/dev/.claude?ref_type=heads (also /-/blob/);
    [] for anything else. A branch may itself contain "/", so the split is left to GitLab."""
    match = re.search(r"/-/(?:tree|blob)/([^?#\s]+)", value)
    if not match:
        return []
    return [unquote(w) for w in match.group(1).strip("/").split("/") if w]


class ClaudeRepoPreviewOut(BaseModel):
    info: dict[str, Any]  # repo, ref, sha, files, status (used | error | other_connection)
    # path (relative to .claude), kind, name, description, chars, clipped, procedure, why
    loaded: list[dict[str, Any]]  # quoted in every deep review
    not_loaded: list[dict[str, Any]]  # same + reason (by_commit | paths): chosen per commit


# -- wiping this server's data from the Admin screen (outside the spec; see PROGRESS.md) --------


class PurgeScopeOut(BaseModel):
    scope: Literal["reviews", "collected", "all", "people"]
    confirm: str  # what the admin must type
    tables: list[str]  # deleted, in order


class PurgeInfoOut(BaseModel):
    scopes: list[PurgeScopeOut]
    counts: dict[str, int]  # rows per table now


class PurgeIn(BaseModel):
    scope: Literal["reviews", "collected", "all", "people"]
    confirm: str = Field(min_length=1, max_length=100)


class PurgeOut(BaseModel):
    scope: Literal["reviews", "collected", "all", "people"]
    rows: dict[str, int]  # deleted rows per table
    # False when the metrics re-aggregation could not be queued (Redis down).
    recompute_queued: bool


# -- GitLab members -> CodePulse people (outside the spec; see PROGRESS.md) ------------------------


class GitLabMemberOut(BaseModel):
    gitlab_id: int
    username: str
    name: str | None
    state: str
    access: str  # guest | reporter | developer | maintainer | owner
    email: str | None  # public (or visible to the token); used to suggest a match
    repos: list[str]
    connection_id: int  # sent back on import: the GitLab the user is read back from
    # linked: already a person's; link: firm match; review: weak hint (same name), not
    # preselected; create: no match; skip: bot, blocked or deactivated
    action: Literal["linked", "link", "review", "create", "skip"]
    user_id: int | None  # the linked or suggested person
    # same_email | username_domain | commit_noreply | commit_email | same_name | bot | <state>
    reason: str | None


class GitLabMembersOut(BaseModel):
    repos: int  # repos listed
    truncated: bool  # more than 200 repos or 1000 members: the list is not complete
    members: list[GitLabMemberOut]


class GitLabMemberChoice(BaseModel):
    """What the admin confirmed; the username and state are read back from GitLab."""

    gitlab_id: int = Field(gt=0)
    connection_id: int
    action: Literal["link", "create", "skip"]
    user_id: int | None = None  # link
    full_name: str | None = Field(default=None, max_length=255)  # create
    email: str | None = Field(default=None, pattern=r"^$|^[^@\s]+@[^@\s]+$", max_length=255)
    move: bool = False  # take the account from the person it is linked to


class GitLabMembersImport(BaseModel):
    members: list[GitLabMemberChoice] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _once_each(self) -> "GitLabMembersImport":
        ids = [m.gitlab_id for m in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("each GitLab member may appear once")
        return self


class GitLabMembersImported(BaseModel):
    created: int
    linked: int
    skipped: int
    emails_linked: int  # commit emails tied to the people afterwards


class AuthorLinkIn(BaseModel):
    """A commit author (git email) to an existing person, or to a new one."""

    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=255)
    name: str | None = Field(default=None, max_length=255)
    user_id: int | None = None
    create: bool = False  # True: a new person named `name`, with `email` as primary email
