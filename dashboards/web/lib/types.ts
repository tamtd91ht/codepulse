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
  /** [from, to) instants of the latest period (team series), for links to its findings. */
  period_range?: [string, string] | null;
}

export interface LeaderboardRow extends Metrics {
  user: Person;
  trend: Num[];
  /** false: left the team since; the viewer can no longer list their findings. */
  viewable?: boolean;
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

export type Verdict = "approve" | "needs_human" | "fix_required" | "block";

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
  /** docs/specs/19 §4.3: confirmed | downgraded | rejected | unverified | skipped (low/info). */
  verified?: Verified | null;
  verification?: { verdict?: string; reason?: string; evidence?: string | null; confidence?: number } | null;
}

export type Verified = "confirmed" | "downgraded" | "rejected" | "unverified" | "skipped";

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
  mode: "fast" | "deep" | "agent";
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
  mode: "fast" | "deep" | "agent";
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
    /** Characters of the file the prompt held. */
    quoted?: number;
    /** status partial | cut: whether the agent opened the file (it was told the quote is incomplete). */
    read?: boolean;
  }[];
  /**
   * reason "catalog": listed in the index the agent loads from on demand (`read`: it did);
   * "duplicate": same content as `same_as`, quoted once;
   * "name" | "budget" on reviews before the per-commit selection.
   */
  instructions_not_loaded?: {
    path: string;
    source: "repo" | "shared";
    reason: string;
    kind?: string;
    read?: boolean;
    why?: string[];
    /** reason "duplicate": the file with the same content that was used instead. */
    same_as?: string;
  }[];
  claude_repo?: {
    repo: string;
    ref: string | null;
    sha?: string;
    /** Copied into the checkout for the agent to Read; mount_error says why not. */
    mounted?: boolean;
    mount_error?: string;
    status: "used" | "error" | "other_connection";
    detail?: string;
  } | null;
  profile?: { sha: string | null; built_at: string | null; confidence: number | null } | null;
  /** Review knowledge the review ran against (docs/specs/19 §3.4): version and what was loaded. */
  knowledge?: BasisKnowledge | null;
}

export interface BasisKnowledge {
  version: number;
  stale: boolean;
  approved: boolean;
  /** How many invariants were loaded. */
  invariants: number;
  /** Names of the checks whose triggers matched the diff. */
  checks_loaded: string[];
  /** Severity rules that applied; a rule id or a short record, whichever the worker recorded. */
  applied_rules?: (string | { id?: string; rule_id?: string; effect?: string; reason?: string })[];
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
  /** Flow v2 (docs/specs/19 §4): the agentic pass fell back to the no-tool pass. */
  fallback?: { from: string; error?: string };
  /** docs/specs/19 §4.3: the independent verification report. */
  verification?: {
    selected?: number;
    calls?: number;
    failed_calls?: number;
    cost_usd?: number;
    ignored_evidence?: number;
    confirmed?: number;
    downgraded?: number;
    rejected?: number;
    unverified?: number;
    skipped?: string;
    error?: string;
  };
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
  /** Whose change was reviewed (absent on older API responses). */
  author?: Person | null;
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
  /** Rebuild the review knowledge when a push to the default branch touches .claude/ (docs/specs/19 §3.4). */
  knowledge_auto_refresh: boolean;
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
  /** Not every listing carries it; source links on the knowledge page need it. */
  web_url?: string | null;
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
  /** Admins only: to link an author nobody is recognised as. */
  author_email?: string | null;
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
  open_critical: number;
  open_high: number;
  approve: number;
  fix_required: number;
  block: number;
  pass_rate: number | null; // % of judged reviews that were APPROVE
  delta: { reviews: number; avg_score: number | null; severe: number; critical: number; high: number };
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
  /** [from, to) instants of the period, for links to the findings found in it. */
  period_range: [string, string];
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
  period_range: [string, string];
  totals: ProjectAgg & { repos: number };
  repos: ProjectRepoRow[];
  series: (Omit<ProjectAgg, "delta" | "open_severe" | "open_critical" | "open_high"> & { period_start: string })[];
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
  /** `claude auth status --json` as the CLI printed it (local paths dropped); null if no answer. */
  cli_auth_status: Record<string, unknown> | null;
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
  key:
    | "max_review_loc"
    | "llm_model"
    | "public_url"
    | "claude_file_chars"
    | "fast_guidance_chars"
    | "deep_instructions_chars";
  env: string;
  kind: "int" | "model" | "url";
  value: number | string;
  default: number | string;
  overridden: boolean;
  minimum: number | null;
  maximum: number | null;
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
  kind: "mr" | "commit" | "knowledge";
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
  /** Ended: why it failed or was skipped, one line. */
  error?: string | null;
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

/** GET/POST /admin/purge: wipe this server's data (ADMIN_EMAILS only). */
export type PurgeScope = "reviews" | "collected" | "all" | "people";
export interface PurgeInfo {
  scopes: { scope: PurgeScope; confirm: string; tables: string[] }[];
  counts: Record<string, number>;
}
export interface PurgeResult {
  scope: PurgeScope;
  rows: Record<string, number>;
  recompute_queued: boolean;
}

/** GET /projects/{id}/gitlab-members, /business-projects/{id}/gitlab-members. */
export interface GitLabMember {
  gitlab_id: number;
  username: string;
  name: string | null;
  state: string;
  access: string;
  email: string | null;
  repos: string[];
  /** Sent back on import: the GitLab the member is read back from. */
  connection_id: number;
  /** linked: already someone's; link: firm match; review: weak hint (same name), not preselected; create: no match; skip: bot or blocked. */
  action: "linked" | "link" | "review" | "create" | "skip";
  user_id: number | null;
  /** same_email | username_domain | commit_noreply | commit_email | same_name | bot | a GitLab state. */
  reason: string | null;
}

export interface GitLabMembers {
  repos: number;
  /** More than 200 repos or 1000 members: the list is not complete. */
  truncated: boolean;
  members: GitLabMember[];
}

export interface GitLabMembersImported {
  created: number;
  linked: number;
  skipped: number;
  emails_linked: number;
}

/* ---- review knowledge (docs/specs/19) ---- */

export type KnowledgeAuthority = "repo" | "shared";

export interface KnowledgeSource {
  path: string;
  lines: string | null;
}

export interface KnowledgeInvariant {
  id: string;
  statement: string;
  severity: Severity;
  signals: string[];
  source: KnowledgeSource;
  confidence: number;
  authority: KnowledgeAuthority;
}

export interface KnowledgeDomain {
  summary: string;
  actors: string[];
  tenancy: string;
  money_flows: string[];
  critical_modules: { path: string; why: string }[];
  glossary: { term: string; meaning: string }[];
  stack: string[];
}

export interface KnowledgeSeverityRule {
  id: string;
  when: { globs: string[]; signals: string[] };
  effect: { raise_to?: Severity; lower_to?: Severity; min_confidence?: number };
  reason: string;
  source: KnowledgeSource;
}

export interface KnowledgeProcedure {
  steps: string[];
  severity_map: Record<string, string>;
  source: KnowledgeSource;
}

export interface KnowledgeCheck {
  id: string;
  name: string;
  triggers: { globs: string[]; signals: string[]; keywords: string[] };
  items: string[];
  source: KnowledgeSource;
  confidence: number;
}

export interface KnowledgeIndexEntry {
  path: string;
  source: "repo" | "shared" | "code";
  kind: string;
  purpose: string;
  used_for: string[];
}

export interface KnowledgeConvention {
  statement: string;
  evidence: string;
  confidence: number;
}

export interface KnowledgeMeta {
  sources: Record<string, string | null>;
  built_at: string;
  model: string;
  cost_usd: number;
  turns: number;
  files_read: number;
  confidence: number;
  code_probe?: unknown;
}

/** `review_knowledge.data` (docs/specs/19 §3.1). Arrays may be missing on a thin build. */
export interface Knowledge {
  invariants?: KnowledgeInvariant[];
  domain?: KnowledgeDomain | null;
  severity_rules?: KnowledgeSeverityRule[];
  procedure?: KnowledgeProcedure | null;
  checks?: KnowledgeCheck[];
  index?: KnowledgeIndexEntry[];
  omitted?: { path: string; reason: string }[];
  conflicts?: { a: string; b: string; chosen: string; why: string }[];
  code_conventions?: KnowledgeConvention[];
  meta?: KnowledgeMeta;
}

export interface KnowledgeCounts {
  invariants: number;
  checks: number;
  severity_rules: number;
  code_conventions: number;
  omitted: number;
  conflicts: number;
}

export type KnowledgeBuiltBy = "manual" | "auto" | "first_review";

/** One compiled version of a repo's review knowledge. */
export interface KnowledgeStaleReason {
  source: "claude" | "shared";
  built: string | null;
  current: string;
}

export interface KnowledgeVersion {
  id: number;
  version: number;
  schema_version: number;
  status: "active" | "superseded" | string;
  approved_by: { id: number; full_name: string | null } | null;
  approved_at: string | null;
  built_by: KnowledgeBuiltBy;
  created_at: string;
  sources: { claude_sha?: string | null; shared_sha?: string | null; default_branch_sha?: string | null };
  data: Knowledge;
  /** The .claude tree or the shared repo moved since this was built. */
  stale: boolean;
  /** One entry per moved source, with the short shas at build time and now. */
  stale_reasons?: KnowledgeStaleReason[];
  counts: KnowledgeCounts;
}

export interface KnowledgeVersionRow {
  id: number;
  version: number;
  created_at: string;
  built_by: KnowledgeBuiltBy;
  approved_at: string | null;
  counts: KnowledgeCounts;
}

export type KnowledgeEditAction = "disable" | "set_severity" | "reword" | "add_invariant";

/**
 * A manual change kept across versions (docs/specs/19 §3.4). Payload by action:
 * disable {}, set_severity {severity}, reword {statement}, add_invariant {statement, severity, signals}.
 */
export interface KnowledgeEdit {
  id: number;
  item_id: string;
  action: KnowledgeEditAction;
  payload: { severity?: Severity; statement?: string; signals?: string[] };
  user: { id: number; full_name: string | null };
  created_at: string;
}

/** One repo's knowledge at a glance (GET /knowledge/summary): no staleness, that needs GitLab. */
export interface KnowledgeSummary {
  project_id: number;
  version: number | null;
  created_at: string | null;
  approved: boolean;
  counts: KnowledgeCounts | null;
  building: boolean;
  /** The newest build failed (newer than the active one, or there is none). */
  failed: boolean;
}

export interface KnowledgeResponse {
  project_id: number;
  active: KnowledgeVersion | null;
  edits: KnowledgeEdit[];
  /** Reserved; the per-rule numbers come from GET …/knowledge/stats (KnowledgeStats). */
  stats: Record<string, unknown> | null;
  building: boolean;
  /** The newest failed build when it is newer than the active one: why it failed. */
  last_failure: { id: number; created_at: string; built_by: string; error: string | null; cost_usd: number | null } | null;
}

/** One rule of the knowledge over a window (docs/specs/19 §5). */
export interface KnowledgeRuleStat {
  rule_id: string;
  /** Findings citing the rule in done reviews of the window. */
  fired: number;
  confirmed: number;
  downgraded: number;
  rejected: number;
  unverified: number;
  /** Findings whose dispute was accepted. */
  disputes_accepted: number;
  /** Reviews where the agent said the rule applies but reported nothing for it. */
  applied_not_fired: number;
  /** dead: applied but never fired (meaningful over 90 days); noisy: rejected + disputes >= 50% of fired. */
  label: "dead" | "noisy" | null;
}

export interface KnowledgeStats {
  project_id: number;
  days: number;
  since: string;
  rules: KnowledgeRuleStat[];
  /** Done reviews in the window. */
  reviews: number;
}

/* ---- per-member evaluation (app/scoring/evaluation.py) ---- */

export interface EvaluationBucket {
  reviews: number;
  mrs: number;
  commits: number;
  loc: number;
  repos: number;
  /** LOC-weighted average of the reviews' scores. */
  avg_score: number | null;
  min_score: number | null;
  last_review_at: string | null;
  verdicts: Record<Verdict, number>;
  /** % approve among judged reviews. */
  pass_rate: number | null;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  findings: number;
  findings_per_100_loc: number | null;
  /** Findings a lead accepted as false positive. */
  disputed: number;
  /** Findings the verification refuted. */
  rejected: number;
  /** Any critical, or an average below 65. */
  needs_attention: boolean;
}

export interface MemberEvaluationRow extends EvaluationBucket {
  user_id: number;
  user: Person;
  teams: string[];
  /** Average per week of `weeks`, oldest first. */
  trend: (number | null)[];
}

export interface MembersEvaluation {
  from: string;
  to: string;
  /** [lo, hi) instants of finished_at (REPORT_TZ), for Critical Board links. */
  range: [string, string];
  weeks: string[];
  totals: EvaluationBucket & { members: number };
  members: MemberEvaluationRow[];
}

export interface RepoEvaluationRow extends EvaluationBucket {
  project_id: number | null;
  path: string | null;
}

export interface MemberEvaluationDetail {
  user: Person;
  from: string;
  to: string;
  range: [string, string];
  summary: EvaluationBucket;
  by_repo: RepoEvaluationRow[];
  categories: { category: string; count: number }[];
  weeks: string[];
  trend: (number | null)[];
  trend_reviews: number[];
}
