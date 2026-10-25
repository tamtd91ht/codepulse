// Mirrors app/api/schemas.py. Decimals arrive as strings.

export type Num = string | null;

export interface Me {
  id: number;
  full_name: string | null;
  email: string | null;
  is_admin: boolean;
  lead_teams: number[];
  managed_teams: number[];
  teams: number[];
}

export interface Team {
  id: number;
  name: string;
  parent_id: number | null;
}

export interface Member {
  user_id: number;
  full_name: string | null;
  primary_email: string | null;
  role: string | null;
  joined_at: string;
  left_at: string | null;
}

export interface Person {
  id: number;
  full_name: string | null;
  primary_email: string | null;
  active: boolean | null;
}

export interface Metrics {
  period_type: "week" | "month";
  period_start: string;
  reviews_count: number | null;
  avg_score: Num;
  loc_changed: number | null;
  critical: number | null;
  high: number | null;
  medium: number | null;
  low: number | null;
  info: number | null;
  defect_density: Num;
  mr_count: number | null;
  avg_review_time_h: Num;
  avg_mr_size: number | null;
  avg_review_rounds: Num;
  build_success_rate: Num;
  fix_rate: Num;
  quality_index: Num;
}

export interface MetricsSeries {
  owner: string;
  series: Metrics[];
  delta: Record<string, number | null>;
  team_quality_index: number | null;
}

export interface LeaderboardRow extends Metrics {
  user: Person;
  trend: Num[];
}

export interface Leaderboard {
  team: Team;
  period_type: string;
  period_start: string;
  team_metrics: Metrics | null;
  members: LeaderboardRow[] | null;
}

export interface Hotspots {
  categories: { category: string; count: number }[];
  files: { file_path: string; count: number }[];
}

export interface CategoryWeek {
  week: string;
  category: string;
  count: number;
}

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export type Verdict = "approve" | "fix_required" | "block";

/** Design & intent notes from the reviewer (docs/specs/17): advisory, never scored. */
export interface Intent {
  summary: string;
  matches_intent: boolean | null;
  altitude: "over" | "right" | "under" | null;
  concerns: string[];
}

export interface Finding {
  id: number;
  review_id: number | null;
  severity: Severity;
  category: string;
  rule_id: string | null;
  file_path: string | null;
  line_start: number | null;
  line_end: number | null;
  title: string | null;
  message: string | null;
  suggestion: string | null;
  code_excerpt: string | null;
  confidence: Num;
  status: string | null;
}

export interface BoardFinding extends Finding {
  project: string;
  author: string | null;
  author_user_id: number | null;
  target: string;
  web_url: string | null;
  age_days: number;
}

export interface Review {
  id: number;
  target_type: "mr" | "commit";
  reviewed_sha: string;
  author_user_id: number | null;
  status: string;
  score: Num;
  loc_reviewed: number | null;
  summary: string | null;
  model: string | null;
  rubric_version: string | null;
  cost_usd: Num;
  error: string | null;
  finished_at: string | null;
  verdict: Verdict | null;
  risk_score: number | null;
  intent: Intent | null;
  trigger: string;
  requested_by_user_id: number | null;
  mode: "fast" | "deep";
  deep: DeepReport | null;
  round: number;
  superseded_by: number | null;
  basis: ReviewBasis | null;
}

/** One review of the same MR / commit (docs/specs/17 §Lịch sử). */
export interface ReviewRound {
  id: number;
  round: number;
  reviewed_sha: string;
  status: string;
  score: Num;
  verdict: Verdict | null;
  mode: "fast" | "deep";
  trigger: string;
  finished_at: string | null;
  basis: ReviewBasis | null;
}

/** One team-rule source a review used (docs/specs/17 §Căn cứ review). */
export interface BasisRule {
  kind: "business_project" | "project" | "file" | string;
  name: string; // the heading a finding cites in rule_id, or the file path
  status: "used" | "missing" | "changed_in_diff" | "cut" | string;
  chars?: number;
  hash?: string;
}

/** What a review was checked against: names and fingerprints only. */
export interface ReviewBasis {
  rubric?: { version: string; lens: string[] };
  rules?: BasisRule[];
  rules_ref?: string;
  /** source/status since the shared .claude repo; older reviews have path and kind only. */
  instructions?: {
    path: string;
    kind: string;
    source?: "repo" | "shared";
    status?: string;
    /** One of the project's review commands (/review-mr…), quoted as the review procedure. */
    procedure?: boolean;
    /** Why it was chosen for this change: "lệnh review", "khớp: tenant, invoice"… */
    why?: string[];
  }[];
  /**
   * reason "catalog": listed in the index the agent loads from on demand (`read`: it did);
   * "name" | "budget" on reviews before the per-commit selection.
   */
  instructions_not_loaded?: {
    path: string;
    source: "repo" | "shared";
    reason: string;
    kind?: string;
    read?: boolean;
    why?: string[];
  }[];
  claude_repo?: {
    repo: string;
    ref: string | null;
    sha?: string;
    status: "used" | "error" | "other_connection";
    detail?: string;
  } | null;
  profile?: { sha: string | null; built_at: string | null; confidence: number | null } | null;
}

/** What the deep pass did with the first pass's findings (docs/specs/18). */
export interface DeepNote {
  title: string;
  file_path: string;
  line_start: number;
  severity: Severity;
  reason: string;
  evidence: string;
  new_severity?: Severity;
}

export interface DeepReport {
  kept?: number;
  added?: number;
  downgraded?: DeepNote[];
  dropped?: DeepNote[];
  ignored_verifications?: number;
  project_fit?: string[];
  files_seen?: number;
  turns?: number | null;
  cost_usd?: number;
  instruction_sources?: string[];
  error?: string;
  detail?: string;
  skipped?: string;
}

/** The project profile built from the repository's own documents (docs/specs/18). */
export interface RepoProfile {
  id: number;
  path_with_namespace: string;
  business_project_id: number | null;
  deep_review: boolean;
  profile: {
    kind?: string;
    summary?: string;
    domain?: string;
    features?: string[];
    stack?: Partial<{ languages: string[]; frameworks: string[]; datastores: string[]; messaging: string[]; infra: string[] }>;
    security?: Partial<{
      authentication: string;
      authorization: string;
      tenancy: string;
      secrets_management: string;
      sensitive_data: string;
      notes: string[];
    }>;
    risk_areas?: string[];
    conventions?: string[];
    review_focus?: string[];
    confidence?: number;
    instruction_sources?: string[];
  } | null;
  profile_at: string | null;
  profile_sha: string | null;
}

export interface ReviewListItem extends Review {
  target: string;
  project: string | null;
  web_url: string | null;
}

export interface ReviewDetail extends Review {
  findings: Finding[];
  project: string | null;
  web_url: string | null;
  history: ReviewRound[];
  can_rerun: boolean;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Dispute {
  id: number;
  finding_id: number | null;
  raised_by: number | null;
  reason: string | null;
  decision: string | null;
  decided_at: string | null;
  finding_title: string | null;
  finding_severity: Severity | null;
  review_id: number | null;
  raised_by_name: string | null;
}

export interface Connection {
  id: number;
  name: string;
  kind: string;
  base_url: string;
  repos: number; // repos registered through it; a connection with repos cannot be deleted
}

/** Outbound notification webhook (docs/specs/16): finished reviews POSTed to an endpoint. */
export type WebhookEvent = "review.done" | "review.failed" | "review.severe";
export type WebhookFormat = "json" | "slack" | "teams";

export interface NotificationWebhook {
  id: number;
  name: string;
  url: string;
  format: WebhookFormat;
  has_secret: boolean;
  events: WebhookEvent[];
  business_project_id: number | null;
  project_id: number | null;
  enabled: boolean;
  last_delivery_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  failures: number;
  deliveries: number;
  created_at: string;
}

export interface WebhookTestResult {
  ok: boolean;
  status_code: number | null;
  error: string | null;
  attempts: number;
  enabled: boolean;
}

export interface GroupImport {
  added: string[];
  skipped: number;
  not_queued: string[];
}

export interface BusinessProject {
  id: number;
  name: string;
  description: string | null;
  repos: number;
  review_rules: string | null;
  /** Optional shared .claude repo read by the deep review of every repo of the dự án. */
  claude_repo_connection_id: number | null;
  claude_repo_path: string | null;
  claude_repo_ref: string | null;
  claude_repo_include: string | null;
}

/** One file of a shared .claude repo, as the preview lists it (path relative to .claude). */
export interface ClaudeRepoFile {
  path: string;
  kind: string;
  name: string;
  description: string;
  chars: number;
  clipped: boolean;
  procedure: boolean;
  why: string[];
}

/** POST /business-projects/{id}/claude-repo/preview: what a deep review would load from it. */
export interface ClaudeRepoPreview {
  info: {
    repo: string;
    ref: string | null;
    sha?: string;
    files?: number;
    status: "used" | "error" | "other_connection";
    detail?: string;
  };
  /** Quoted in every deep review: review commands (procedure), CLAUDE.md, pinned files. */
  loaded: ClaudeRepoFile[];
  /** Chosen per commit (reason "by_commit"), or only when a changed file matches its `paths:`. */
  not_loaded: (ClaudeRepoFile & { reason: string })[];
}

/** A GitLab project, i.e. one git repo. */
export interface Project {
  id: number;
  connection_id: number | null;
  external_id: number;
  path_with_namespace: string;
  default_branch: string | null;
  /** null: GitLab default branch; "*": every branch (docs/specs/17 §Nhánh review). */
  review_branch: string | null;
  review_enabled: boolean | null;
  post_mr_comment: boolean | null;
  allow_external_llm: boolean | null;
  last_synced_at: string | null;
  business_project_id: number | null;
  tracked: boolean;
  untracked_at: string | null;
  review_rules: string | null;
  review_rules_paths: string | null;
  deep_review: boolean;
  profile_at: string | null;
}

/* ---- review on request (docs/specs/17) ---- */

export interface Repo {
  id: number;
  path_with_namespace: string;
  default_branch: string | null;
  review_branch: string | null;
  business_project_id: number | null;
  business_project: string | null;
  review_enabled: boolean;
}

export interface Branch {
  name: string;
  default: boolean;
  last_commit_at: string | null;
}

export interface CommitRow {
  sha: string;
  short_sha: string;
  title: string;
  authored_at: string | null;
  author_name: string | null;
  user_id: number | null;
  user_name: string | null;
  is_merge: boolean;
  additions: number | null;
  deletions: number | null;
  review_id: number | null;
  review_status: string | null;
  score: number | null;
  verdict: Verdict | null;
  reviewable: boolean;
  reason: string | null;
}

export interface CommitList {
  ref: string;
  commits: CommitRow[];
  hidden: number;
  truncated: boolean;
  batch_max: number; // how many commits one request may queue
}

export interface ReviewCommitsResult {
  queued: string[];
  skipped: { sha: string; reason: string }[];
}

export interface Account {
  id: number;
  kind: string;
  external_key: string;
  display_name: string | null;
  user_id: number | null;
}

/** GET /metrics/projects: anonymous aggregates per dự án for one period. */
export interface ProjectAgg {
  reviews: number;
  avg_score: number | null;
  loc: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  severe: number; // critical + high found in the period
  open_severe: number; // open critical/high right now
  approve: number;
  fix_required: number;
  block: number;
  pass_rate: number | null; // % of judged reviews that were APPROVE
  delta: { reviews: number; avg_score: number | null; severe: number };
}

export interface ProjectEntry extends ProjectAgg {
  id: number | null; // null = repos without a dự án
  name: string;
  description: string | null;
  repos: number;
  trend: (number | null)[]; // avg score of the last 8 periods, oldest first
}

export interface ProjectsOverview {
  period_type: "week" | "month";
  period_start: string;
  trend_starts: string[];
  totals: ProjectAgg & { projects: number; repos: number };
  projects: ProjectEntry[];
}

export interface ProjectRepoRow extends Omit<ProjectAgg, "delta"> {
  id: number;
  path_with_namespace: string;
  tracked: boolean;
  last_review_at: string | null;
}

export interface ProjectDetail {
  project: { id: number; name: string; description: string | null };
  period_type: "week" | "month";
  period_start: string;
  totals: ProjectAgg & { repos: number };
  repos: ProjectRepoRow[];
  series: (Omit<ProjectAgg, "delta" | "open_severe"> & { period_start: string })[];
  categories: { category: string; count: number }[];
  recent: ReviewListItem[];
  next_period_start: string;
}

/** A Telegram review bot of one dự án (optionally pinned to one repo). The token is never returned. */
export interface TelegramBot {
  id: number;
  name: string;
  bot_username: string | null;
  business_project_id: number;
  project_id: number | null;
  enabled: boolean;
  allowed_chat_ids: number[] | null;
  last_update_id: number | null;
  last_polled_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface TelegramRequest {
  id: number;
  chat_id: number;
  user_id: number | null;
  user_name: string | null;
  command: string;
  targets: { kind: string; project_id: number; ref: string; sha: string; label: string }[];
  status: string;
  created_at: string;
  notified_at: string | null;
}

export interface TelegramAccount {
  id: number;
  external_key: string;
  display_name: string | null;
}

export interface TelegramLinkCode {
  code: string;
  expires_in_s: number;
}

export interface LlmCosts {
  budget_usd: number;
  months: { month: string; cost_usd: number; reviews: number }[];
}

/** POST /admin/llm-check: configuration, account and one live call; never contains the token. */
export interface LlmCheck {
  provider: string;
  model: string;
  cli_binary: string | null;
  cli_version: string | null;
  auth: { method: string; token_hint: string | null; cli_method: string | null; logged_in: boolean | null };
  account: {
    email: string | null;
    organization: string | null;
    subscription_type: string | null;
    api_provider: string | null;
  } | null;
  account_note: string | null;
  status: {
    ok: boolean;
    latency_ms: number | null;
    model: string | null;
    cost_usd: number | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    error: string | null;
  };
  usage: { reviews: number; cost_usd: number; budget_usd: number; budget_pct: number | null };
  checked_at: string;
}

/** GET /activity/reviews/{id}/live: the deep pass's actions (tool, path, pattern; no contents). */
export interface LiveEvent {
  kind: "start" | "thinking" | "tool" | "answer" | "end" | string;
  turn: number;
  at: number | null;
  tool: string | null;
  target: string | null;
  path: string | null;
  glob: string | null;
  lines: number | null;
  model: string | null;
  ok: boolean | null;
  turns: number | null;
}

export interface LiveReview {
  review_id: number;
  running: boolean;
  events: LiveEvent[];
}

/** GET/PUT /admin/review-settings: a review setting over its .env default. */
export interface ReviewSetting {
  key: string;
  env: string;
  value: number;
  default: number;
  overridden: boolean;
  minimum: number;
  maximum: number;
}

/** GET /admin/llm-token: which credential reviews use; never contains the token. */
export interface LlmToken {
  active: "stored" | "env_oauth" | "env_api_key" | "none";
  token_hint: string | null;
  stored: boolean;
  stored_source: "login" | "paste" | null;
  stored_by: string | null;
  stored_at: string | null;
  login_supported: boolean;
}

/** POST /admin/llm-login: a `claude setup-token` run waiting for the code from claude.ai. */
export interface LlmLogin {
  login_id: string;
  url: string;
  expires_in_s: number;
}

/* ---- reviews in progress (docs/specs/17 §Tiến trình) ---- */

export interface ActivityItem {
  state: "running" | "queued" | "ended";
  review_id: number | null;
  kind: "mr" | "commit";
  project_id: number;
  project: string;
  ref: string;
  sha: string;
  title: string;
  author: string | null;
  author_user_id: number | null;
  requested_by: string | null;
  trigger: string;
  round: number;
  started_at: string | null;
  enqueued_at: string | null;
  stage: string | null;
  detail: string;
  stage_at: string | null;
  loc: number | null;
  files: number | null;
  web_url: string | null;
  /** ended: done | failed | skipped; null when the job crashed (RQ may retry it). */
  status: string | null;
  ended_at: string | null;
}

export interface ActivityOut {
  running: ActivityItem[];
  queued: ActivityItem[];
  queued_total: number;
  /** Ended in the last 15 minutes, newest first. */
  recent: ActivityItem[];
  hidden: number;
}
