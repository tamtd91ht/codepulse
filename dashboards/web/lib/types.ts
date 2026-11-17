// Mirrors app/api/schemas.py. Decimals arrive as strings.

export type Num = string | null;

export interface Me {
  id: number;
  full_name: string | null;
  email: string | null;
  is_admin: boolean;
  /** The uploaded avatar (Admin › Người & Team); null: initials. */
  avatar_url?: string | null;
  /** Manages every dự án without a grant (below admin); `projects` then lists every dự án as "manage". */
  manage_all?: boolean;
  lead_teams: number[];
  managed_teams: number[];
  teams: number[];
  /** dự án id -> role (docs/specs/09 "Phân quyền theo dự án"); empty for admins, who have every dự án. */
  projects: Record<string, ProjectRole>;
  /** Admin > Chạy command is on (COMMAND_RUNS_ENABLED) and this is an admin. */
  command_runs: boolean;
  /** Luồng làm việc: lead and above (admin, lead/manager of a team, manager of a dự án). */
  insights?: boolean;
  /** Thành viên (per-person evaluation of a team): lead and above too. */
  members_page?: boolean;
  /** Task Jira (docs/specs/27 §18): Jira is connected. */
  jira_tasks?: boolean;
  /** docs/specs/21: permission key -> scope ("own" | "team" | "project" | "all") or "flag"; read with can(). */
  permissions?: Record<string, string>;
  /** docs/specs/21 §10: dự án id -> this person's Lead / Manager role there. */
  project_admin?: Record<string, ProjectAdminRole>;
  /** docs/specs/21 §10: the dự án this person sets up (repos, .claude, rules); null = every one. */
  setup_projects?: number[] | null;
}

/** docs/specs/21 §10: the Lead or Manager of a dự án sets it up; the Manager also staffs it. */
export type ProjectAdminRole = "lead" | "manager";

/* ---- permission sets (docs/specs/21, Admin › Phân quyền) ---- */

export type PermissionScope = "own" | "team" | "project" | "all";

export interface CatalogPermission {
  key: string;
  label: string;
  /** Empty for a flag. */
  scopes: PermissionScope[];
  flag: boolean;
  sensitive: boolean;
}

export interface PermissionCatalog {
  groups: { name: string; permissions: CatalogPermission[] }[];
  scopes: { key: PermissionScope; label: string }[];
  roles: { key: string; label: string }[];
}

export interface PermissionItem {
  permission: string;
  /** A scope, or "flag". */
  scope: string;
}

export interface PermissionSetRow {
  id: number;
  name: string;
  description: string | null;
  builtin: boolean;
  /** Built-in sets: the role that holds it without being given it. */
  auto_role: string | null;
  items: PermissionItem[];
  member_count: number;
  /** Custom sets: the people given it. */
  members: { id: number; full_name: string | null; email: string | null }[];
  /** Custom sets: the teams given it, everyone in them holds it (docs/specs/21 §11). */
  teams?: { id: number; name: string }[];
}

export interface UserPermissions {
  user: { id: number; full_name: string | null; email: string | null };
  is_admin: boolean;
  active: boolean;
  roles: string[];
  sets: { id: number; name: string; auto_role: string | null; via: "role" | "given" | "personal" | "team" }[];
  permissions: {
    key: string;
    scope: string;
    sources: { set_id: number | null; name: string; scope: string }[];
    /** GET /me/permissions?explain=1 only (docs/specs/31 §21.5). */
    label?: string | null;
    meaning?: string | null;
  }[];
}

/** "May I, and why" for one permission (GET /me/permissions?check=…, docs/specs/31 §21.5). */
export interface PermissionCheck {
  key: string;
  label: string;
  scope: string | null;
  /** null: depends on the task (a task right asked of a whole dự án). */
  allowed: boolean | null;
  target: string | null;
  reason: string;
  lines: string[];
}

/** Quyền của tôi: GET /me/permissions (the caller's own effective permissions). */
export interface MyPermissions extends UserPermissions {
  check: PermissionCheck | null;
  /** The dự án the caller sees (to ask about one). */
  projects: { id: number; name: string }[];
}

export type ProjectRole = "view" | "manage";

/** One person's access to a dự án (admin: GET/PUT /business-projects/{id}/grants). */
export interface ProjectGrant {
  user_id: number;
  full_name: string | null;
  email: string | null;
  active: boolean;
  role: ProjectRole;
  admin_role?: ProjectAdminRole | null;
}

export interface Team {
  id: number;
  name: string;
  parent_id: number | null;
  /** docs/specs/31 §20.7: the GitLab group the team follows (direct members); null: by hand. */
  gitlab_group_path?: string | null;
  gitlab_sync_at?: string | null;
  gitlab_sync_error?: string | null;
}

/** POST /teams/{id}/gitlab/sync (docs/specs/31 §20.7). */
export interface TeamGitlabSync {
  team: Team;
  added: { user_id: number; full_name: string | null }[];
  ended: { user_id: number; full_name: string | null }[];
  unmapped: { gitlab_id: number; username: string; name: string | null }[];
  skipped: number;
  error: string | null;
}

export interface Member {
  user_id: number;
  full_name: string | null;
  primary_email: string | null;
  role: string | null;
  joined_at: string;
  left_at: string | null;
  /** "gitlab": added by the team's GitLab group sync; "manual": by hand. */
  source?: "manual" | "gitlab";
}

export interface Person {
  id: number;
  full_name: string | null;
  primary_email: string | null;
  active: boolean | null;
  /** Admin › Người & Team only: manages every dự án without a grant. */
  manage_all?: boolean;
  /** Admin › Người & Team only: in ADMIN_EMAILS (has every right already). */
  is_admin?: boolean;
  /** The uploaded avatar (Admin › Người & Team); null: initials. */
  avatar_url?: string | null;
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
  /** docs/specs/10, period=range (e.g. "Hôm nay"): the team over those days, computed now. */
  current?: Metrics | null;
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
  /** When the change was made: the commit's time (an MR: when it was opened). */
  committed_at?: string | null;
}

export interface Review {
  id: number;
  target_type: "mr" | "commit";
  /** The merge request reviewed (target_type "mr"). */
  mr_id?: number | null;
  reviewed_sha: string;
  author_user_id: number | null;
  status: string;
  score: Num;
  loc_reviewed: number | null;
  summary: string | null;
  model: string | null;
  /** The Claude account that answered (Admin › LLM); null before 2026-11-09. */
  llm_account?: string | null;
  rubric_version: string | null;
  cost_usd: Num;
  error: string | null;
  finished_at: string | null;
  /** Queued: since when it waits; running: since when it runs; else null. */
  waiting_since?: string | null;
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
  /** .claude excerpts put in the prompt when there is no Tiền đề (app/review/service.py _guidance). */
  fast_guidance?: { path: string; source?: string; kind?: string }[];
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
  /** Reviewed past MAX_REVIEW_LOC on a lead's rerun: the change's LOC and the limit then. */
  loc_limit?: { loc: number; max: number } | null;
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
  /** Flow v2: tool calls of the agentic pass by name, and the files it opened (paths only). */
  tool_calls?: Record<string, number>;
  files_read?: string[];
  files_read_total?: number;
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
  /** When the change was made: the commit's time, or when the MR was opened. The list sorts by it. */
  changed_at?: string | null;
  /** The viewer manages it and it may run again (as ReviewDetail.can_rerun). */
  can_rerun?: boolean;
  /** No person yet: the git / GitLab identity behind the change. */
  author_account?: ReviewAuthorAccount | null;
}

/** The author of a review with no person: a git email (commit) or a GitLab account (MR). */
export interface ReviewAuthorAccount {
  id: number;
  kind: "git_email" | "gitlab_user" | string;
  name: string | null;
  email: string | null;
  /** Linked after the review ran: a sync moves the review to them. */
  user: Person | null;
  /** Admin, or a manager of the review's dự án. */
  can_link: boolean;
}

/** GET /reviews/{id}/author: what the "Gắn" form offers. */
export interface ReviewAuthorOut {
  account: ReviewAuthorAccount;
  candidates: Person[];
  can_create: boolean;
  /** Reviews of this author with no person yet (what a sync moves). */
  reviews: number;
}

/** POST /reviews/rerun: which ones were queued, and why the others were not. */
export interface RerunManyOut {
  queued: number[];
  failed: { id: number; error: string }[];
}

/** GET /reviews/rerunnable: every review matching the list's filters that may run again. */
export interface RerunnableOut {
  ids: number[];
  /** How many of them are finished (each gets a new round). */
  done: number;
  /** More matched than one batch takes; only the newest are in `ids`. */
  truncated: boolean;
}

export interface ReviewDetail extends Review {
  /** Whose change it is: the person, or with none yet the git / GitLab identity. */
  author?: Person | null;
  author_account?: ReviewAuthorAccount | null;
  findings: Finding[];
  project: string | null;
  web_url: string | null;
  history: ReviewRound[];
  can_rerun: boolean;
  /** docs/specs/22 §6: one score per criteria set of the review's dự án (absent before 22.2). */
  criteria_scores?: ReviewCriteriaScore[];
  /** docs/specs/27 §17: the Jira issues an MR names; null for a commit review. */
  jira?: MrJira | null;
  /** docs/specs/07: Gitleaks / Semgrep on the added lines (hints, not scored). */
  static_findings?: StaticFinding[];
}

export interface StaticFinding {
  id: number;
  tool: "gitleaks" | "semgrep" | string;
  rule_id: string;
  severity: string;
  category: string;
  file_path: string;
  line_start: number;
  line_end: number;
  title: string;
  message: string | null;
  code_excerpt: string | null;
  confirmed_by_ai: boolean;
}

/** review.basis.static: what ran. */
export interface StaticBasis {
  files: number;
  found: number;
  tools: Record<string, { ran: boolean; found: number; seconds: number; error: string | null; version: string | null }>;
}

export interface JiraIssueRef {
  key: string;
  title: string | null;
  state: "opened" | "closed" | null;
  type: string | null;
  story_points: number | null;
  url: string | null;
  /** closes: key in the MR title or source branch; mention: only in the description. */
  link: "closes" | "mention";
}

export interface MrJira {
  issues: JiraIssueRef[];
  /** Linked issues of another dự án (or a repo without one): key only, no details. */
  other_keys?: string[];
  /** Keys of registered Jira projects the MR names whose issue is not synced (yet). */
  missing_keys: string[];
  /** The task source is Jira: an MR is expected to name its issue. */
  expected: boolean;
  /** "commit": the Jira side of a commit review (docs/specs/27 §19); "closes" there means the
   * message says it fixes the issue (`Fixes: KEY`). */
  target?: "commit";
}

/* ---- weighted criteria sets (docs/specs/22) ---- */

/** One part of a criteria set: its weight (%) and the finding categories that fall in it. */
export interface CriteriaComponent {
  key: string;
  name: string;
  description: string;
  weight: number;
  categories: string[];
}

/** An immutable version of a set (docs/specs/22 §1 "Phiên bản"). */
export interface CriteriaVersion {
  id: number;
  version: number;
  components: CriteriaComponent[];
  apply_caps: boolean;
  note: string | null;
  created_at: string;
  created_by?: number | null;
  created_by_name?: string | null;
}

/** A version as the set detail lists it (components optional). */
export interface CriteriaVersionRow {
  id: number;
  version: number;
  note: string | null;
  created_at: string;
  created_by?: number | null;
  created_by_name?: string | null;
}

export interface CriteriaSetProject {
  id: number;
  name: string;
  is_primary: boolean;
}

/** GET /criteria-sets (list) and /criteria-sets/{id} (with `versions`). */
export interface CriteriaSet {
  id: number;
  name: string;
  description: string | null;
  archived: boolean;
  current_version: CriteriaVersion | null;
  projects: CriteriaSetProject[];
  versions?: CriteriaVersionRow[];
  /** Has scores already: only an unused set can be deleted (the server has the last word). */
  used?: boolean;
}

export interface CriteriaTemplate {
  key: string;
  name: string;
  description: string;
  components: CriteriaComponent[];
}

/** GET /criteria/meta. */
export interface CriteriaMeta {
  categories: { key: string; label: string }[];
  limits: { min_components: number; max_components: number; name_max: number; description_max: number };
  templates: CriteriaTemplate[];
}

export interface CriteriaBreakdownComponent {
  key: string;
  name: string;
  weight: number;
  score: number;
  penalty: number;
  findings: { id: number; source: "ai" | "category" }[];
}

export interface CriteriaBreakdown {
  capped: boolean;
  components: CriteriaBreakdownComponent[];
}

/** GET /reviews/{id} criteria_scores[]. */
export interface ReviewCriteriaScore {
  set_id: number;
  set_name: string;
  version: number;
  is_primary: boolean;
  score: number | string;
  breakdown: CriteriaBreakdown;
}

/** A finding as the preview names it, so a breakdown can say what took points off. */
export interface CriteriaPreviewFinding {
  id: number;
  severity: Severity;
  category: string;
  title: string | null;
}

/** POST /criteria/preview: one recent review scored by the set being edited (nothing saved). */
export interface CriteriaPreviewRow {
  review_id: number;
  /** MR title, commit subject or repo path: whatever names the review. */
  title?: string | null;
  project?: string | null;
  finished_at: string | null;
  /** The review's own score (docs/specs/08). */
  score: number | string | null;
  /** The score by the draft set. */
  criteria_score: number | string;
  breakdown: CriteriaBreakdown;
  findings?: CriteriaPreviewFinding[];
}

/** GET/PUT /business-projects/{bp}/criteria-sets, in `position` order. */
export interface ProjectCriteriaSet {
  set_id: number;
  name: string;
  is_primary: boolean;
  position: number;
  archived?: boolean;
  version?: number | null;
  components?: CriteriaComponent[];
}

/** GET /criteria/summary: LOC-weighted averages over the reviews in scope, and the weekly trend. */
export interface CriteriaSummary {
  /** The sets the scope can pick from (primary first); the picker shows when there are several. */
  sets?: { set_id: number; name: string; is_primary: boolean }[];
  set_id: number | null;
  set_name?: string | null;
  reviews: number;
  score: number | string | null;
  components: { key: string; name: string; weight: number; score: number | string | null }[];
  weeks: { week_start: string; score: number | string | null; reviews: number }[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/**
 * One closing of a critical/high finding that a later change fixed, or a request to close one
 * (GET /reviews/{id}/closures, /close-requests). A closed finding keeps its severity and still
 * counts in the score.
 */
export interface FindingClosure {
  id: number;
  finding_id: number;
  /** The viewer may run the check, refuse, or confirm (lead/manager of the author, or admin). */
  can_decide?: boolean;
  /** The viewer sent it and it still waits. */
  can_cancel?: boolean;
  /** May lower a critical straight to low (a manager of the author's team, or an admin). */
  can_lower_far?: boolean;
  /** The finding's severity now. */
  finding_severity?: Severity | null;
  /** Closing by an argument or a note lowers the finding: before and after; the check's suggestion. */
  severity_before?: Severity | null;
  severity_after?: Severity | null;
  suggested_severity?: "medium" | "low" | null;
  /** auto: a later review's change fixed it; request: the author named the fixing commits; admin: by hand; reopen. */
  kind: "auto" | "request" | "admin" | "reopen";
  /** pending: waiting for a manager/admin; checking: the check runs; proposed: the argument held, waiting for a
   * manager/admin to confirm; then closed / rejected / failed; cancelled by the author. */
  status: "pending" | "checking" | "proposed" | "unmet" | "closed" | "rejected" | "failed" | "cancelled" | "reopened";
  shas: string[];
  /** auto: the review whose change closed it. */
  review_id: number | null;
  requested_by: number | null;
  /** Who ran the check or refused the request. */
  decided_by: number | null;
  note: string | null;
  /** The author's case that the finding does not hold (instead of, or with, the commits). */
  argument: string | null;
  /** What the finding became: fixed (a change fixed it) or accepted_risk (the argument held). */
  closed_as: "fixed" | "accepted_risk" | "false_positive" | null;
  /** The checker's reason, or why the request was refused. */
  reason: string | null;
  /** file:lines of the fix in the change. */
  evidence: string | null;
  cost_usd: number | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  /** Where the request came from: the web form, or Claude Code through the MCP server (docs/specs/26 §8). */
  via?: "web" | "mcp";
  /** What an MCP request carried besides the commits; null for the web form. */
  meta?: ClosureMeta | null;
}

/** One test run reported with an MCP close request. */
export interface ClosureTest {
  command: string;
  result: "passed" | "failed" | "not_run";
  summary?: string | null;
}

/** docs/specs/26 §8: files changed, tests, the token and the MCP client that sent the request. */
export interface ClosureMeta {
  files_changed?: string[];
  tests?: ClosureTest[];
  token_id?: number | null;
  token_name?: string | null;
  client?: string | null;
}

/* ---- personal access tokens for the MCP server (docs/specs/26 §2) ---- */

export type ApiTokenScope =
  | "findings:read"
  | "findings:close_request"
  | "reviews:request"
  // docs/specs/31 §10: move / comment / log work on tasks over MCP.
  | "tasks:write"
  // docs/specs/30 §15 (G11): the public testcase API.
  | "testcases:read"
  | "testcases:write"
  | "results:write";

export interface ApiToken {
  id: number;
  name: string;
  /** The first 8 characters of the token, shown as `cpat_xxxxxxxx…`. */
  prefix: string;
  scopes: ApiTokenScope[];
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  /** Bound to this dự án only (testcase API); null: every dự án of its owner. */
  business_project_id?: number | null;
  business_project_name?: string | null;
  /** Not revoked and not expired. */
  active: boolean;
}

/** POST /me/tokens: the token itself is returned this once. */
export interface ApiTokenCreated extends ApiToken {
  token: string;
}

/** A row of the "Đóng finding" page. */
export interface ClosureListItem extends FindingClosure {
  finding: {
    id: number;
    title: string | null;
    severity: Severity;
    category: string;
    file_path: string | null;
    line_start: number | null;
    message: string | null;
    status: string | null;
  };
  finding_review_id: number;
  /** What the row shows: "closed" when the finding is closed now (whatever became of this
   * request), "reopened" when this request closed it and it is open again, else `status`. */
  shown_status?: string;
  /** The finding's outcome when it is closed now (fixed, accepted_risk, false_positive). */
  shown_closed_as?: string | null;
  /** web_url: the repo on GitLab (commit links); null when its connection is unknown. */
  repo: { id: number; path: string; web_url?: string | null } | null;
  business_project: { id: number; name: string } | null;
  author: { id: number; name: string | null } | null;
  requested_by_name: string | null;
  decided_by_name: string | null;
  /** A manager of the author's team or an admin: may run the check or refuse. */
  can_decide: boolean;
  can_cancel: boolean;
  /** The finding's review page is visible to this person (a manager may decide without it). */
  can_open_review: boolean;
}

export interface ClosureFacets {
  authors: { id: number; name: string | null }[];
  repos: { id: number; path: string; business_project_id: number | null }[];
  business_projects: { id: number; name: string }[];
  /** Requests waiting for this person's decision. */
  todo: number;
  can_decide: boolean;
}

export interface Dispute {
  id: number;
  finding_id: number | null;
  raised_by: number | null;
  reason: string | null;
  decision: string | null;
  decided_at: string | null;
  created_at?: string | null;
  finding_title: string | null;
  finding_severity: Severity | null;
  finding_category?: string | null;
  finding_status?: string | null;
  finding_file_path?: string | null;
  finding_line_start?: number | null;
  review_id: number | null;
  raised_by_name: string | null;
  decided_by_name?: string | null;
  /** AI second look (POST /disputes/{id}/ai-check); only for who may decide. */
  ai_status?: DisputeAiStatus | null;
  ai_verdict?: DisputeAiVerdict | null;
  ai_confidence?: number | null;
  ai_reason?: string | null;
  ai_evidence?: string | null;
  ai_finished_at?: string | null;
  can_ai_check?: boolean;
}

export type DisputeAiStatus = "queued" | "running" | "done" | "failed";
export type DisputeAiVerdict = "finding_holds" | "dispute_holds" | "unclear";

export interface Connection {
  id: number;
  name: string;
  kind: string;
  base_url: string;
  repos: number; // repos registered through it; a connection with repos cannot be deleted
}

/** Outbound notification webhook (docs/specs/16): finished reviews POSTed to an endpoint. */
export type WebhookEvent =
  | "review.done"
  | "review.failed"
  | "review.severe"
  // docs/specs/30 §15 (G11): testcase events.
  | "run.closed"
  | "result.failed"
  | "case.status_changed"
  | "plan.closed";

/** GET /webhooks/{id}/deliveries: one event sent (or to send) to a webhook. */
export interface WebhookDelivery {
  id: number;
  webhook_id: number;
  event: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  last_status_code: number | null;
  last_error: string | null;
  created_at: string;
  delivered_at: string | null;
  payload: Record<string, unknown>;
}
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
  /** People granted the dự án. */
  grants: number;
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
  /** docs/specs/07: Gitleaks / Semgrep hints in its reviews. */
  static_analysis?: boolean;
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
  /** The commit on GitLab. */
  web_url?: string | null;
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
  /** Why it failed or was skipped ("MR quá lớn: … LOC"…). */
  review_error?: string | null;
  score: number | null;
  verdict: Verdict | null;
  reviewable: boolean;
  reason: string | null;
  /** Reviewed already but pickable (managers, admins): sending it runs a new round. */
  again?: boolean;
}

export interface CommitList {
  ref: string;
  commits: CommitRow[];
  hidden: number;
  truncated: boolean;
  batch_max: number; // how many commits one request may queue
  limit?: number; // commits listed per repo at most (Admin › Cấu hình review)
  reviewed_hidden?: number; // left out by "Bỏ commit đã review"
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

/** PUT /accounts/{id}/user, POST /identity/authors: the account and the reviews it brought. */
export interface AccountLinked extends Account {
  reviews_attributed: number;
  periods_queued: number;
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
  /** Latest finished review up to the period's end (by change time): where an empty period points. */
  last_review_at?: string | null;
}

export interface ProjectsOverview {
  /** "range": a custom range of days, period_start..period_end inclusive. */
  period_type: "week" | "month" | "range";
  period_start: string;
  period_end: string;
  /** Earliest day a custom range may start. */
  range_min: string;
  /** How the trend is bucketed (a range: by week, or by month when long). */
  trend_period: "week" | "month";
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
  /** "range": whole days period_start..period_end (docs/specs/10, e.g. "Hôm nay"). */
  period_type: "week" | "month" | "range";
  period_start: string;
  /** Last day, inclusive. */
  period_end?: string;
  /** What each `series` point is (a range trends by week, by month when long). */
  series_period?: "week" | "month";
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
  months: {
    month: string;
    /** Reviews' spend. */
    cost_usd: number;
    reviews: number;
    /** Review knowledge builds (every status: all were paid). */
    knowledge_usd: number;
    knowledge_builds: number;
    /** .claude command runs (Admin > Chạy command). */
    commands_usd: number;
    commands_runs: number;
    /** Checks of requests to close a finding (the automatic ones are in the reviews' cost). */
    closures_usd: number;
    closure_checks: number;
    /** Stetho, the AI assistant: spend, questions, and how many were priced by an estimate (stopped early). */
    assistant_usd: number;
    assistant_turns: number;
    assistant_estimated: number;
    /** Every source above: what the monthly budget compares. */
    total_usd: number;
  }[];
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
    | "review_agent_min_loc"
    | "llm_model"
    | "public_url"
    | "claude_file_chars"
    | "fast_guidance_chars"
    | "deep_instructions_chars"
    | "command_timeout_min"
    | "command_max_turns"
    | "command_max_budget_usd"
    | "command_clone_depth"
    | "command_max_output_mb"
    | "command_file_ttl_hours"
    | "monthly_llm_budget_usd"
    | "review_commits_list_max"
    | "auto_close_findings"
    | "auto_close_days"
    | "llm_auto_resume"
    | "llm_probe_interval_min"
    | "llm_account_retry_min"
    | "close_requests_per_day"
    | "mcp_reviews_per_day";
  env: string;
  kind: "int" | "model" | "url" | "bool";
  value: number | string | boolean;
  default: number | string | boolean;
  overridden: boolean;
  minimum: number | null;
  maximum: number | null;
}

/** A person's close-request limit over 24 hours (web and MCP), Admin › Hồ sơ thành viên. */
export interface CloseQuota {
  user_id: number;
  /** Their own number; null: the default of Admin › Cấu hình. 0: may send none. */
  own: number | null;
  default: number;
  limit: number;
  used_24h: number;
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

/** One Claude account of Admin › LLM (GET /admin/llm-accounts); the token is never sent. */
export interface LlmAccount {
  id: number;
  label: string;
  token_hint: string | null;
  source: "login" | "paste" | "migrated";
  set_by: number | null;
  set_by_name: string | null;
  is_default: boolean;
  position: number;
  enabled: boolean;
  /** ok · quota: usage limit · auth: login failed · model: the plan lacks the review model */
  state: "ok" | "quota" | "auth" | "model";
  /** A call now would try it. */
  usable: boolean;
  retry_at: string | null;
  failures: number;
  last_error: string | null;
  blocked_model: string | null;
  last_used_at: string | null;
  last_ok_at: string | null;
  last_failed_at: string | null;
  updated_at: string | null;
}

export interface LlmAccounts {
  accounts: LlmAccount[];
  /** The account the next call uses (null with the environment's token, the CLI login, or none). */
  active_id: number | null;
  active_label: string | null;
  model: string;
  retry_min: number;
  env_token_hint: string | null;
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
  /** The GitLab profile page. */
  web_url?: string | null;
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
  /** Repos GitLab did not list (the others are), with why. */
  failed?: { repo: string; reason: string; message: string }[];
  /** Public emails not all looked up (time budget): fewer suggestions by email. */
  emails_skipped?: boolean;
}

export interface GitLabMembersImported {
  created: number;
  linked: number;
  skipped: number;
  emails_linked: number;
  reviews_attributed: number;
  periods_queued: number;
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
  /** The approver's full name; null when unapproved or the user has no name. */
  approved_by: string | null;
  approved_by_user_id: number | null;
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
  /** Reviews with at least one critical / high finding that counts, and their share of the
   * reviews in % (docs/specs/23 §10): the count grows with the work, the share does not. */
  critical_reviews?: number;
  high_reviews?: number;
  critical_rate?: number | null;
  high_rate?: number | null;
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

/** GET /admin/commands: a command of the repo's .claude/commands or of the dự án's shared repo. */
export interface ClaudeCommand {
  /** What follows "/", e.g. "report:weekly". */
  name: string;
  source: "repo" | "shared";
  path: string;
  description: string;
  argument_hint: string;
  allowed_tools: string[];
  model: string;
  body: string;
  /** A shared command the repo's own command of the same name replaces. */
  overridden: boolean;
}

export interface ClaudeCommandList {
  ref: string | null;
  shared_repo: string | null;
  shared_ref: string | null;
  commands: ClaudeCommand[];
  repo_error: string | null;
  shared_error: string | null;
}

export type CommandRunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

/** /admin/command-runs: one run of a command; its files are kept until the first download. */
export interface CommandRun {
  id: number;
  status: CommandRunStatus;
  source: "repo" | "shared";
  command: string;
  arguments: string;
  project_id: number | null;
  repo: string | null;
  business_project_id: number | null;
  business_project: string | null;
  ref: string | null;
  repo_sha: string | null;
  shared_ref: string | null;
  shared_sha: string | null;
  error: string | null;
  cost_usd: number | null;
  num_turns: number | null;
  files: { name: string; size: number }[];
  /** The file name the download sends; null when nothing is left to download. */
  download: string | null;
  files_deleted: boolean;
  meta: { replaced?: string[]; recognized?: boolean | null; denied?: string[]; skipped?: string[]; model?: string; sensitive_removed?: number };
  started_by: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CommandLogEntry {
  kind: "stage" | "start" | "warn" | "text" | "tool" | "denied" | "tool_error" | "end";
  text?: string;
  tool?: string;
  model?: string;
  ok?: boolean;
  turns?: number;
  cost_usd?: number;
  at?: number;
}

export interface CommandRunDetail extends CommandRun {
  log: CommandLogEntry[];
  log_next: number;
  result: string | null;
}

/* ---- Engineering Insights (docs/specs/20) ---- */

export type InsightStage = "coding" | "pickup" | "review" | "time_to_merge";

export interface InsightConfidence {
  level: "ok" | "low" | "insufficient";
  reasons: { code: string; level: string; message: string; repos?: string[] }[];
}

export interface InsightSpread {
  n: number;
  median: number | null;
  p75: number | null;
}

export interface FlowSummary {
  merged: number;
  merged_without_review: number;
  merged_without_review_rate: number | null;
  stages: Record<InsightStage, InsightSpread>;
  size: InsightSpread & { buckets: Record<"XS" | "S" | "M" | "L" | "XL", number>; xl_share: number | null };
  review_rounds: InsightSpread;
}

export interface PipelineGroup {
  total: number;
  finished: number;
  success: number;
  failed: number;
  success_rate: number | null;
  duration_min: InsightSpread;
}

export interface PipelineSummary extends PipelineGroup {
  default_branch: PipelineGroup;
  other_branches: PipelineGroup;
}

export interface InsightPerson {
  user_id: number | null;
  name: string;
  hidden: boolean;
  mapped: boolean | null;
}

interface InsightWindow {
  insights_version: string;
  period_type: "week" | "month" | "range";
  period_start: string;
  period_end: string;
  period_range: [string, string];
  trend_period: "week" | "month";
  trend_starts: string[];
  data_as_of: string | null;
  confidence: InsightConfidence;
}

export interface InsightsFlow extends InsightWindow {
  totals: FlowSummary & {
    delta: {
      merged: number;
      time_to_merge: number | null;
      pickup: number | null;
      merged_without_review_rate: number | null;
      xl_share: number | null;
      pipeline_success_rate: number | null;
    };
  };
  trend: ({ period_start: string; merged: number; merged_without_review_rate: number | null } & Record<InsightStage, number | null>)[];
  repos: (FlowSummary & {
    id: number;
    path_with_namespace: string;
    business_project_id: number | null;
    last_synced_at: string | null;
    pipelines: PipelineSummary;
  })[];
  pipelines: PipelineSummary;
  open: {
    opened: number;
    waiting_first_response: number;
    oldest: {
      id: number;
      iid: number;
      title: string | null;
      project_id: number;
      path_with_namespace: string | null;
      web_url: string | null;
      waiting_h: number;
      author: InsightPerson;
    }[];
  };
}

export interface InsightMr extends Record<InsightStage, number | null> {
  id: number;
  iid: number;
  title: string | null;
  project_id: number;
  path_with_namespace: string;
  target_branch: string | null;
  web_url: string | null;
  opened_at: string | null;
  merged_at: string | null;
  size: number | null;
  size_bucket: "XS" | "S" | "M" | "L" | "XL" | null;
  review_rounds: number | null;
  reviewed: boolean;
  reviewers: number;
  author: InsightPerson;
}

export interface InsightsMrs extends InsightWindow {
  stage: InsightStage;
  only: "unreviewed" | "xl" | null;
  total: number;
  items: InsightMr[];
}

export interface InsightsReviewLoad extends InsightWindow {
  merged: number;
  reviewed: number;
  reviewers_count: number;
  top_reviewer_share: number | null;
  concentrated: boolean;
  reviewers: (InsightPerson & { reviewed_mrs: number; share: number | null })[];
  pairs: { reviewer: InsightPerson; author: InsightPerson; mrs: number }[];
}

export interface InsightModule {
  project_id: number;
  path_with_namespace: string;
  module: string;
  churn: number;
  commits: number;
  last_change_at: string | null;
  bus_factor: number;
  top_share: number | null;
  single_owner: boolean;
  orphaned: boolean;
  owners: (InsightPerson & { share: number })[];
  /** §4.9b: the component as shown, what it is, its kind, automatic or AI-grouped. */
  name: string;
  description: string;
  kind: ComponentKind;
  source: "auto" | "llm";
  /** The top holder is a git account not linked to a person. */
  unattributed: boolean;
  unlinked_share: number;
  /** §4.9c: the tree (level 0 = repo; parent = the parent node's `module` id). */
  level: number;
  parent: string | null;
  tier: "repo" | "module" | "component" | "part" | "sub";
  contributors: number;
  share_of_parent: number | null;
  /** Flagged for the same holder as its flagged parent: shown, not counted again. */
  inherited: boolean;
  /** One of the warnings, counted once at the highest level it holds. */
  counted: boolean;
  why: string | null;
}

export type ComponentKind = "code" | "config" | "docs" | "noise" | "domain" | "platform" | "integration";

export interface InsightComponents {
  project_id: number;
  path_with_namespace: string;
  source: "auto" | "llm";
  paths: number;
  layered_roots: string[];
  unmapped_share: number;
  ai: {
    status: "queued" | "running" | "done" | "failed";
    error: string | null;
    built_at: string | null;
    requested_at: string | null;
    confidence: number | null;
    model: string | null;
    stale: boolean;
  } | null;
  allow_external_llm: boolean;
  can_rebuild: boolean;
  components: {
    id: string;
    name: string;
    description: string;
    kind: ComponentKind;
    source: "auto" | "llm";
    files: number;
    tests: number;
    parts: string[];
    samples: string[];
  }[];
}

export interface InsightsKnowledge {
  insights_version: string;
  window: [string, string];
  window_days: number;
  module_depth: number;
  bus_threshold: number;
  min_commits: number;
  data_as_of: string | null;
  confidence: InsightConfidence;
  half_life_days?: number;
  bulk_files?: number;
  maps?: Record<string, { source: "auto" | "llm"; components: number; layered: boolean }>;
  totals: {
    modules: number;
    nodes?: number;
    warnings?: number;
    single_owner: number;
    unattributed?: number;
    orphaned: number;
    inherited?: number;
    shown: number;
  };
  orphan_after_days: number;
  modules: InsightModule[];
  nodes?: InsightModule[];
  /** The tree was cut (warnings and their ancestors are always kept). */
  nodes_truncated?: boolean;
}

/* ---- Evidence packs (docs/specs/20 §6) ---- */

export type EvidenceStatus = "draft" | "submitted" | "reviewed" | "shared";
export type EvidenceKind = "mr" | "commit" | "review_given" | "finding_fixed" | "incident" | "design" | "mentoring" | "other";

export interface EvidenceMr extends Record<InsightStage, number | null> {
  id: number;
  iid: number;
  title: string | null;
  project_id: number;
  path_with_namespace: string | null;
  web_url: string | null;
  merged_at: string | null;
  size: number | null;
  size_bucket: "XS" | "S" | "M" | "L" | "XL" | null;
  review_rounds: number | null;
  reviewed: boolean;
}

export interface EvidenceSnapshot {
  version: string;
  built_at: string;
  period: string;
  window: [string, string];
  merged: { count: number; items: EvidenceMr[] };
  notable: { id: number; why: string }[];
  reviews_given: { count: number; authors: number; items: EvidenceMr[] };
  quality: {
    confirmed_severe: number;
    fixed: number;
    items: {
      id: number;
      review_id: number;
      title: string | null;
      severity: Severity;
      category: string;
      status: string | null;
      file_path: string | null;
      line_start: number | null;
    }[];
    disputes_raised: number;
    disputes_accepted: number;
  };
  waiting: { id: number; pickup: number | null; repo_p75: number | null }[];
  stale_repos: string[];
  /** Commits of the month no MR carries (packs built before 9.10b have none). */
  direct_commits?: {
    count: number;
    items: { sha: string; project_id: number; path_with_namespace: string | null; web_url: string | null; title: string | null; authored_at: string | null }[];
  };
  /** Accounts tied to nobody that committed in the same repos this month. */
  unmapped_accounts?: string[];
}

export interface EvidenceItem {
  id: number;
  kind: EvidenceKind;
  ref: string | null;
  title: string;
  note: string | null;
  added_by: number | null;
  added_by_name: string | null;
  mine: boolean;
}

export interface EvidencePack {
  user: { id: number; full_name: string | null; primary_email: string | null };
  period: string;
  persisted: boolean;
  status: EvidenceStatus;
  snapshot: EvidenceSnapshot;
  self_notes: string | null;
  lead_notes: string | null;
  goals_next: string | null;
  /** The lead's AI draft (§6.4); null for the person themselves. */
  ai_draft: string | null;
  /** A draft job is writing it (poll until false). */
  ai_draft_running: boolean;
  submitted_at: string | null;
  reviewed_at: string | null;
  reviewed_by_name: string | null;
  shared_at: string | null;
  items: EvidenceItem[];
  role: "self" | "lead";
  can: {
    edit_self_notes: boolean;
    /** After the 1:1: an answer appended to the self notes, never replacing them. */
    reply: boolean;
    edit_lead_notes: boolean;
    edit_goals: boolean;
    add_item: boolean;
    refresh: boolean;
    ai_draft: boolean;
    submit: boolean;
    return: boolean;
    review: boolean;
    share: boolean;
  };
  insights_version: string;
}

export interface EvidenceList {
  user: { id: number; full_name: string | null };
  months_back: number;
  packs: { period: string; status: EvidenceStatus; updated_at: string | null }[];
}

/* ---- DORA (docs/specs/20 §4.10) ---- */

export interface DoraSummary {
  deployments: number;
  failed: number;
  rollbacks: number;
  per_week: number;
  deploy_days: number;
  change_failure_rate: number | null;
  recovery_h: InsightSpread;
  lead_time_h: InsightSpread;
  deploy_h: InsightSpread;
  shipped_mrs: number;
}

export interface InsightsDora {
  insights_version: string;
  period_type: "week" | "month" | "range";
  period_start: string;
  period_end: string;
  period_range: [string, string];
  trend_period: "week" | "month";
  trend_starts: string[];
  data_as_of: string | null;
  confidence: InsightConfidence;
  totals: DoraSummary & {
    delta: { deployments: number; change_failure_rate: number | null; lead_time_h: number | null; recovery_h: number | null };
  };
  incidents: {
    opened: number;
    closed: number;
    open_now: number;
    per_deployment: number | null;
    restore_h: InsightSpread;
    delta: { opened: number; restore_h: number | null };
    recent: {
      id: number;
      iid: number;
      /** Jira key (ABC-123) when the issue comes from Jira; shown instead of #iid. */
      key?: string | null;
      title: string | null;
      project_id: number;
      path_with_namespace: string | null;
      web_url: string | null;
      state: string | null;
      opened_at: string | null;
      closed_at: string | null;
      fix_mrs: number;
    }[];
  };
  trend: { period_start: string; deployments: number; change_failure_rate: number | null; lead_time_h: number | null }[];
  repos: (DoraSummary & {
    id: number;
    path_with_namespace: string;
    production_environment: string | null;
    /** Every environment the repo deployed to (the admin picks production among them). */
    environments: string[];
    /** Those counted as production in the window. */
    production_detected: string[];
  })[];
  failures: {
    id: number;
    project_id: number;
    path_with_namespace: string | null;
    environment: string;
    sha: string | null;
    kind: "failed" | "rolled_back";
    finished_at: string | null;
  }[];
}

/* ---- Insights backfill (docs/specs/20, 9.12) ---- */

export type BackfillStatus = "queued" | "reconverting" | "syncing" | "done" | "failed";

export interface BackfillProgress {
  total: number;
  active: number;
  queued: number;
  reconverting: number;
  syncing: number;
  done: number;
  failed: number;
  items: {
    project_id: number;
    path_with_namespace: string;
    status: BackfillStatus;
    runs: number | null;
    commits: number | null;
    error: string | null;
    queued_at: string | null;
    started_at: string | null;
    finished_at: string | null;
  }[];
}

/** An in-app notification (docs/specs/24): GET /inbox, and live on /inbox/stream. */
export type NotificationKind =
  | "review_done"
  | "review_failed"
  | "finding_closed"
  | "dispute_new"
  | "dispute_decided"
  // Task (docs/specs/31 §9, §19.4, §19.7)
  | "task.assigned"
  | "task.mentioned"
  | "task.commented"
  | "task.status"
  | "task.retest"
  | "task.remind"
  | "task.alert"
  | "task.digest"
  | "task.summary";
export interface InboxNote {
  id: number;
  user_id: number;
  kind: NotificationKind;
  title: string;
  body: string | null;
  link: string | null;
  tone: "info" | "good" | "warn" | "bad";
  ref: string | null;
  actor_id: number | null;
  read_at: string | null;
  created_at: string | null;
  /** docs/specs/31 §20.5: a grouped task update arrives again with the same id, newer stamp. */
  updated_at?: string | null;
  /** "Tạm ẩn 1 ngày": not unread until then. */
  snoozed_until?: string | null;
}

/** docs/specs/31 §20.5: a task notification channel, the daily summary, the rule log. */
export type TaskNotifyChannel = "inbox" | "telegram" | "off";
export interface TaskNotifySummary {
  on: boolean;
  time: string;
  channel: "inbox" | "telegram";
}
export interface TaskRuleRunRow {
  id: number;
  rule: string;
  rule_title: string;
  task_key: string | null;
  action: string;
  detail: Record<string, unknown>;
  dry_run: boolean;
  at: string | null;
}
export interface TaskRuleDryRun {
  rule: string;
  count: number;
  items: Array<{ task_key: string | null; action: string } & Record<string, unknown>>;
}
export interface InboxPage {
  items: InboxNote[];
  unread: number;
  next_before: number | null;
}

/** GET /admin/llm-health: is the LLM working (app/review/llm_recovery.py). */
export interface LlmHealth {
  status: "ok" | "down" | "recovering";
  down_since: string | null;
  last_error: string | null;
  last_probe_at: string | null;
  next_probe_at: string | null;
  probes: number;
  recovered_at: string | null;
  requeued_last: number | null;
  waiting_reviews: number;
}

/* ---- Task Jira (docs/specs/27 §18, GET /jira/tasks…) ---- */

export interface JiraPerson {
  user_id: number | null;
  name: string;
}

export interface JiraMrReview {
  id: number;
  score: number | null;
  verdict: string | null;
  finished_at: string | null;
  critical: number;
  high: number;
}

export interface JiraTaskMr {
  id: number;
  iid: number;
  title: string | null;
  state: string | null;
  repo: string | null;
  url: string | null;
  author: JiraPerson | null;
  created_at: string | null;
  merged_at: string | null;
  link?: "closes" | "mention";
  branch?: string | null;
  keys?: string[];
  review: JiraMrReview | null;
}

export interface JiraTask {
  key: string;
  title: string | null;
  url: string | null;
  state: "opened" | "closed" | null;
  type: string | null;
  story_points: number | null;
  jira_project: string | null;
  parent_key: string | null;
  assignees: JiraPerson[];
  created_at: string | null;
  closed_at: string | null;
  due_date: string | null;
  mrs: number;
  mrs_merged: number;
  /** Commits whose own message names the issue, and those saying they fix it (§19). */
  commit_count: number;
  fix_commits: number;
  reviews: number;
  score: number | null;
  critical: number;
  high: number;
}

export interface JiraTaskBrief {
  key: string;
  title: string | null;
  state: string | null;
  type: string | null;
}

export interface JiraTaskDetail extends JiraTask {
  labels: string[];
  components: string[];
  business_projects: { id: number; name: string }[];
  parent: JiraTaskBrief | null;
  children: JiraTaskBrief[];
  merge_requests: JiraTaskMr[];
  commits: JiraTaskCommit[];
  /** §20: may link reviewed commits by hand (others than one's own: managers, admins). */
  can_link: boolean;
  /** §20: may move the issue on Jira (managers of its dự án, jira.admin, admins). */
  can_move: boolean;
}

export interface JiraTransition {
  id: string;
  name: string | null;
  to: string | null;
  done: boolean;
}

/** A commit naming an issue in its message (docs/specs/27 §19). */
export interface JiraTaskCommit {
  sha: string;
  title: string;
  repo: string | null;
  url: string | null;
  author: JiraPerson | null;
  authored_at: string | null;
  /** fixes: `Fixes: KEY` (or a fix subject naming it); refs: names it otherwise. */
  kind?: "fixes" | "refs";
  /** Keys it has that match no synced issue (commits naming none). */
  keys?: string[];
  /** It is in an MR that names an issue (only the commit lacks the key). */
  in_linked_mr?: boolean;
  /** Linked by hand on Task Jira (§20), and by whom. */
  manual?: boolean;
  linked_by?: string | null;
  /** "Review lại" allowed (review.rerun on its dự án). */
  can_rerun?: boolean;
  review: JiraMrReview | null;
}

export interface JiraUnlinkedCommits {
  items: JiraTaskCommit[];
  total: number;
  commits_in_period: number;
  with_issue: number;
  page: number;
  page_size: number;
}

export interface JiraTaskList {
  items: JiraTask[];
  total: number;
  page: number;
  page_size: number;
  kpis: {
    issues: number;
    open: number;
    closed_in_period: number;
    with_mr: number;
    with_points: number;
    severe_open: number;
    bugs: number;
    bugs_fixed: number;
  };
}

export interface JiraUnlinkedMrs {
  items: JiraTaskMr[];
  total: number;
  page: number;
  page_size: number;
}

export interface JiraTaskOptions {
  jira_projects: { key: string; name: string | null }[];
  business_projects: { id: number; name: string }[];
  types: string[];
  configured: boolean;
  task_source: "jira" | "gitlab" | "codepulse";
  can_admin: boolean;
}

/* ---- Task (docs/specs/31) ---- */

export type TaskType = "epic" | "story" | "task" | "bug" | "subtask";
/** The five levels: a priority's arrow and meaning (docs/specs/31 §18). */
export type TaskPriorityLevel = "highest" | "high" | "medium" | "low" | "lowest";
export type TaskPriorityColor = "red" | "orange" | "amber" | "green" | "blue" | "purple" | "gray";
/** A priority key of the task's dự án (each dự án names its own, docs/specs/31 §18). */
export type TaskPriority = string;

export interface TaskPriorityDef {
  id: number;
  key: string;
  name: string;
  level: TaskPriorityLevel;
  color: TaskPriorityColor;
  position: number;
  is_default: boolean;
}
export type TaskCategory = "todo" | "doing" | "done";
export type TaskLinkKind = "blocks" | "relates" | "duplicates" | "clones";
export type TaskRelation = "fixes" | "refs";
export type TaskVia = "web" | "git" | "smart_commit" | "mcp" | "ai" | string;

/** §8 `Person`. */
export interface TaskPerson {
  id: number;
  full_name: string | null;
  primary_email: string | null;
  avatar_url?: string | null;
}

export interface TaskStatusRef {
  id: number;
  name: string;
  category: TaskCategory;
}

/** One status of a space; `transitions`: the ids it may move to (null: any). */
export interface TaskStatus extends TaskStatusRef {
  position: number;
  transitions: number[] | null;
  /** Board hints (docs/specs/31 §19.3): soft WIP cap, hours before a card is flagged. */
  wip_limit?: number | null;
  age_warning_hours?: number | null;
}

/** §2 `rules`. */
export interface TaskRules {
  start_on_commit: boolean;
  review_on_mr: boolean;
  done_on_merge: boolean;
  reopen_on_mr_close: boolean;
  block_done_on_critical: boolean;
  mr_comment: boolean;
  start: number | null;
  review: number | null;
  done: number | null;
  /** Definition of Done (docs/specs/31 §19.4). */
  dod?: { checks?: string[]; min_score?: number; enforce?: boolean } | null;
}

/** GET /tasks/spaces: a dự án and its Task setup (not enabled: shown to its managers only). */
export interface TaskSpace {
  business_project_id: number;
  name: string;
  enabled: boolean;
  key: string | null;
  types: TaskType[];
  rules: Partial<TaskRules> | null;
  statuses: TaskStatus[];
  /** The dự án's priorities, most urgent first (docs/specs/31 §18). */
  priorities?: TaskPriorityDef[];
  can_manage: boolean;
  can_create: boolean;
  /** tasks.sprint: plan, start, complete sprints. */
  can_sprint: boolean;
  /** tasks.assign: may name someone else as the assignee. */
  can_assign: boolean;
  /** tasks.attach: may attach files (also in the create form). */
  can_attach?: boolean;
  board_mode?: "scrum" | "kanban";
}

export interface TaskGitCounts {
  commits: number;
  mrs: number;
  open_critical: number;
  open_high: number;
}

/** §8 `TaskRow`. `space`: the dự án id (an object with it is read too, see components/tasks/taskParts). */
export interface TaskRow {
  id: number;
  key: string;
  space: number | { business_project_id?: number; id?: number; key?: string; name?: string };
  type: TaskType;
  title: string;
  status: TaskStatusRef;
  priority: TaskPriority;
  priority_name?: string;
  priority_level?: TaskPriorityLevel;
  priority_color?: TaskPriorityColor;
  /** Why a closed task was closed (docs/specs/31 §19.1); null while open. */
  resolution?: TaskResolution | null;
  started_at?: string | null;
  /** When it entered its current status (age in the Board column). */
  status_changed_at?: string | null;
  assignee: TaskPerson | null;
  reporter: TaskPerson | null;
  labels: string[];
  story_points: number | null;
  due_date: string | null;
  sprint_id: number | null;
  version_id: number | null;
  parent_key: string | null;
  subtasks: { done: number; total: number } | null;
  rank: number | null;
  git: TaskGitCounts | null;
  /** docs/specs/31 §20.1: an open task blocks it. */
  blocked?: boolean;
  /** §20.1: its linked MRs were all closed without merge. */
  mr_closed?: boolean;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface TaskPage {
  items: TaskRow[];
  total: number;
}

export interface TaskLinkItem {
  id: number;
  kind: TaskLinkKind;
  direction: "out" | "in";
  task: TaskRow;
}

export interface TaskComment {
  id: number;
  author: TaskPerson | null;
  body: string;
  created_at: string;
  edited_at: string | null;
  can_edit: boolean;
}

export interface TaskHistoryItem {
  user: TaskPerson | null;
  field: string;
  old: string | null;
  new: string | null;
  via: TaskVia;
  at: string;
}

export interface TaskAttachment {
  id: number;
  name: string;
  size: number;
  content_type: string | null;
  uploaded_by: TaskPerson | null;
  created_at: string;
  url: string;
  can_delete: boolean;
}

export interface TaskWorklog {
  id: number;
  user: TaskPerson | null;
  minutes: number;
  day: string | null;
  note: string | null;
  via: TaskVia;
}

/** The latest review of a linked commit / MR (§7). */
export interface TaskReviewRef {
  id: number;
  score: Num | number;
  verdict: Verdict | null;
  open_critical: number;
  open_high: number;
}

export interface TaskGitBranch {
  id: number;
  project_id: number;
  repo: string | null;
  branch: string;
  manual?: boolean;
  web_url?: string | null;
}

export interface TaskGitCommit {
  id: number;
  project_id: number;
  repo: string | null;
  sha: string;
  title: string | null;
  relation: TaskRelation;
  manual?: boolean;
  web_url?: string | null;
  review: TaskReviewRef | null;
}

export interface TaskGitMr {
  id: number;
  project_id: number;
  repo: string | null;
  mr_iid: number;
  title: string | null;
  state: "opened" | "merged" | "closed" | string | null;
  web_url: string | null;
  relation: TaskRelation;
  manual?: boolean;
  review: TaskReviewRef | null;
  pipeline: { status: string; web_url?: string | null } | null;
}

export interface TaskGitSummary {
  commits: number;
  mrs: number;
  open_critical: number;
  open_high: number;
  avg_score: Num | number;
  pipelines_failed: number;
  deployed: { environment: string; at: string } | null;
}

export interface TaskGit {
  branches: TaskGitBranch[];
  commits: TaskGitCommit[];
  mrs: TaskGitMr[];
  summary: TaskGitSummary;
}

/** GET /tasks/{key}. */
export interface TaskDetail extends Omit<TaskRow, "git" | "subtasks"> {
  description: string | null;
  components: string[];
  estimate_min: number | null;
  spent_min: number | null;
  custom: Record<string, unknown> | null;
  watchers: TaskPerson[];
  watching: boolean;
  links: TaskLinkItem[];
  subtasks: TaskRow[];
  comments: TaskComment[];
  history: TaskHistoryItem[];
  attachments: TaskAttachment[];
  worklogs: TaskWorklog[];
  git: TaskGit | null;
  transitions: TaskStatusRef[];
  /** docs/specs/31 §4.1: the task rights, split as Jira's permission scheme. */
  /** docs/specs/31 §19.4: the dự án's Definition of Done on this task. */
  dod?: { key: string; label: string; ok: boolean; detail: string }[];
  dod_enforced?: boolean;
  /** §20.1: open tasks that block this one. */
  blocked_by?: { key: string; title: string; status: string | null }[];
  can: {
    edit: boolean;
    assign: boolean;
    transition: boolean;
    delete: boolean;
    comment: boolean;
    attach: boolean;
    worklog: boolean;
    link: boolean;
    moderate: boolean;
    manage: boolean;
  };
}

export interface TaskCommitCandidate {
  sha: string;
  title: string | null;
  project_id: number;
  repo?: string | null;
  authored_at?: string | null;
  author?: string | null;
  linked?: boolean;
}

export type SprintState = "planned" | "active" | "closed";

export interface Sprint {
  id: number;
  business_project_id?: number;
  name: string;
  goal: string | null;
  state: SprintState;
  start_date: string | null;
  end_date: string | null;
  closed_at: string | null;
  committed_points: number | null;
  tasks?: TaskRow[];
  points?: number;
  done_points?: number;
  /** Mean finished points of the last 3 closed sprints (§19.3); null before that. */
  capacity?: number | null;
}

export interface TaskBurndown {
  days: { day: string; remaining_points: number; remaining_tasks: number; ideal_points: number }[];
  committed_points: number | null;
}

export interface TaskReports {
  velocity: { sprint_id: number; name: string; committed: number; completed: number }[];
  flow: { day: string; created: number; resolved: number }[];
  cycle_time: { median_h: number | null; p85_h: number | null; count: number; skipped?: number };
  by_status: { status: TaskStatusRef | string; count: number }[];
  /** docs/specs/31 §18.3: per priority, in the dự án's order. */
  /** §20.0: tasks closed in the range, per resolution. */
  by_resolution?: Partial<Record<TaskResolution, number>>;
  by_priority?: { priority: TaskPriorityDef; open: number; overdue: number; created: number; resolved: number; open_points: number }[];
  by_assignee: { user: TaskPerson | null; open: number; done: number; points: number }[];
  worklog: { user: TaskPerson | null; minutes: number }[];
}

/** GET /tasks/spaces/{bp}/epics (docs/specs/31 §19.1). */
export interface TaskEpic {
  key: string;
  title: string;
  closed: boolean;
  total: number;
  done: number;
  points: number;
  points_open: number;
}

export type TaskResolution = "done" | "wont_do" | "duplicate" | "cannot_reproduce" | "obsolete";

export interface TaskVersion {
  id: number;
  business_project_id?: number;
  name: string;
  description: string | null;
  release_date: string | null;
  released_at: string | null;
  /** Counts when the API gives them. */
  tasks?: number;
  done?: number;
}

/** A saved filter (GET /tasks/filters); `query` holds the GET /tasks parameters. */
export interface TaskFilter {
  id: number;
  name: string;
  space: number | null;
  query: Record<string, string>;
  shared: boolean;
  user_id?: number;
  owner?: TaskPerson | null;
  can_delete?: boolean;
}

export type TaskFieldKind = "text" | "number" | "date" | "select" | "user";

export interface TaskField {
  id?: number;
  name: string;
  kind: TaskFieldKind;
  options: string[] | null;
  required: boolean;
  position: number;
  archived: boolean;
}

export interface TaskAiItem {
  type: TaskType;
  title: string;
  description: string | null;
  story_points: number | null;
}

/** GET /tasks/ai/{run_id}. */
export interface TaskAiRun {
  status: "queued" | "running" | "done" | "failed" | string;
  output: { items?: TaskAiItem[]; markdown?: string } | null;
  error: string | null;
}

/** GET /tasks/{key}/timeline (docs/specs/31 §20.1): one item of the task's timeline. */
export type TaskTimelineType = "comment" | "history" | "worklog" | "git" | "review" | "test" | "deploy";

export interface TaskTimelineItem {
  id: string;
  at: string;
  type: TaskTimelineType;
  actor: TaskPerson | null;
  text: string;
  link: string | null;
  data: Record<string, unknown>;
}

/** A testcase covering a task, with its latest status (docs/specs/31 §19.4, §20.3). */
export interface TaskCoverCase {
  case_id: number;
  key: string;
  title: string;
  status: string;
  run_id: number | null;
  test_id: number | null;
  criterion_id?: number | null;
}

/** An acceptance criterion of a task with a stable id (docs/specs/31 §20.3): GET /tasks/{key}/criteria.
 * `status`: none (no testcase) | passed | failed | untested; null when testcases are hidden. */
export interface TaskCriterion {
  id: number;
  position: number;
  text: string;
  done: boolean;
  cases: TaskCoverCase[];
  status: "passed" | "failed" | "untested" | "none" | null;
}

/* -- Nhân rộng Task (docs/specs/31 §20.4) ---------------------------------------------------- */

/** GET /tasks/templates: a built-in template (id null) or one saved from a dự án. */
export interface TaskSpaceTemplate {
  id: number | null;
  key: string;
  name: string;
  description: string | null;
  builtin: boolean;
  types: TaskType[];
  board_mode: "scrum" | "kanban";
  statuses: { name: string; category: TaskCategory }[];
  priorities: string[];
  fields: string[];
  has_dod: boolean;
  has_automation: boolean;
  created_by: number | null;
  created_at: string | null;
  can_delete: boolean;
}

/** POST /tasks/spaces/{bp}/apply-template. */
export interface TaskTemplateApplied {
  mode: "full" | "partial";
  applied: string[];
  skipped: string[];
  space: TaskSpace & { template_key?: string | null };
}

/** GET /tasks/spaces/{bp}/members: the enable wizard's members step. */
export interface TaskSpaceMembers {
  members: { user_id: number; full_name: string | null; email: string | null; role: "view" | "manage"; admin_role: "lead" | "manager" | null }[];
  suggestions: { user_id: number; full_name: string | null; email: string | null; commits: number; last_commit_at: string | null }[];
  can_grant: boolean;
}

/** GET /tasks/adoption: Tình trạng bật Task. */
export interface TaskAdoptionRow {
  business_project_id: number;
  name: string;
  enabled: boolean;
  key: string | null;
  template_key: string | null;
  template_name: string | null;
  board_mode: "scrum" | "kanban" | null;
  tasks: number;
  open: number;
  last_activity: string | null;
  viewers: number;
  can_manage: boolean;
}

export interface TaskAdoption {
  rows: TaskAdoptionRow[];
  all_projects: boolean;
}

/* -- Sprint review (docs/specs/31 §20.6, round 2 stage 2.6) -------------------------------------- */

export interface SprintTaskGroup {
  /** null: a closed sprint's carry-over count is not known (no audit row). */
  count: number | null;
  /** null: a closed sprint's carried-over points are not known. */
  points: number | null;
  keys: string[];
  tasks: { key: string; title: string; type: string; points: number }[];
}

export interface SprintScopeChange {
  since: string | null;
  added_after_start: SprintTaskGroup;
  removed_after_start: SprintTaskGroup;
}

export interface SprintReview {
  sprint: {
    id: number;
    name: string;
    goal: string | null;
    state: "planned" | "active" | "closed";
    start_date: string | null;
    end_date: string | null;
    closed_at: string | null;
    business_project_id: number;
  };
  window: { from: string; to: string };
  committed_points: number | null;
  done: SprintTaskGroup;
  done_pct: number | null;
  closed_without_done: SprintTaskGroup;
  carried_over: SprintTaskGroup;
  added_after_start: SprintTaskGroup;
  removed_after_start: SprintTaskGroup;
  bugs_created: { count: number; highest: number; keys: string[] };
  findings: { critical: number; high: number };
  cycle_time_h: number | null;
  can_create_retro: boolean;
  markdown: string;
}

export interface KanbanFlow {
  days: number;
  throughput: number;
  throughput_points: number;
  per_week: number;
  created: number;
  cycle_time_h: number | null;
}

/** GET /tasks/spaces/{bp}/review/latest: what the Sức khoẻ tab shows with no sprint running. */
export interface LatestSprintReview {
  mode: string;
  review: SprintReview | null;
  flow: KanbanFlow | null;
}

export interface ForecastRange {
  low_points: number;
  high_points: number;
  low_pct: number;
  high_pct: number;
  basis: ("daily" | "velocity")[];
}

export type TrendDirection = "up" | "down" | "flat" | null;

export interface OverviewTrends {
  /** Oldest first, as the values. */
  sprints: string[];
  lead_time_h: { values: (number | null)[]; direction: TrendDirection };
  highest_bugs: { values: number[]; direction: TrendDirection };
  critical_findings: { values: number[]; direction: TrendDirection };
}

/* -- Một cái nhìn (docs/specs/31 §21.1) ------------------------------------------------------- */

/** GET /tasks/spaces/{bp}/dod-gaps: the enforced DoD checks each "doing" task still fails. */
export interface TaskDodGaps {
  enforced: boolean;
  gaps: Record<string, string[]>;
}
/* ---------- Task planning: timeline by epic, sprint capacity (docs/specs/31 §21.3) ---------- */

/** One bar of the timeline: an epic, its Story / Task / Bug child, or a task outside every epic. */
export interface TimelineBar {
  id: number;
  key: string;
  title: string;
  type: TaskType;
  status: string | null;
  category: TaskCategory | null;
  done: boolean;
  assignee: string | null;
  points: number | null;
  sprint_id: number | null;
  due_date: string | null;
  /** Day the bar starts: started_at, the sprint's start or the creation day. */
  start: string;
  /** Due date, the sprint's end, or (epic) the latest child end; null when none is known. */
  end: string | null;
  end_source: "due" | "sprint" | "children" | null;
  /** 0..1: done (child), done points over points (epic). */
  progress: number;
  overdue: boolean;
  overdue_from: string | null;
  /** A blocker of this task ends after this task's end. */
  blocked_late: boolean;
  blockers: string[];
  can_edit: boolean;
  /** Epics only. */
  children?: TimelineBar[];
  children_count?: number;
  done_count?: number;
}

export interface TimelineSprint {
  id: number;
  name: string;
  state: SprintState;
  start: string;
  end: string;
}

export interface TimelineVersion {
  id: number;
  name: string;
  date: string | null;
  released: boolean;
}

export interface TaskTimelineData {
  business_project_id: number;
  from: string;
  to: string;
  today: string;
  epics: TimelineBar[];
  loose: TimelineBar[];
  sprints: TimelineSprint[];
  versions: TimelineVersion[];
  links: { from: string; to: string; late: boolean }[];
  warnings: { kind: "blocked_late"; key: string; blocker: string; text: string }[];
  can_sprint: boolean;
}

export interface TaskTimelineMany {
  from: string;
  to: string;
  today: string;
  spaces: { business_project_id: number; name: string; key: string; epics: TimelineBar[]; versions: TimelineVersion[] }[];
}

export interface SprintPlanLoad {
  user: { id: number } | null;
  name: string;
  tasks: number;
  points: number;
  pct: number;
  over: boolean;
}

export interface SprintPlan {
  sprint_id: number;
  points: number;
  capacity: number | null;
  source: "auto" | "manual";
  /** The mean of the last 3 closed sprints; null without that history. */
  auto: number | null;
  over_capacity: boolean;
  load: SprintPlanLoad[];
  overload_pct: number;
  overloaded: string[];
  can_sprint: boolean;
}

export interface SprintPreflight {
  sprint_id: number;
  ok: boolean;
  warnings: {
    kind: "unestimated" | "unassigned" | "blocked_outside" | "over_capacity" | "overloaded";
    text: string;
    count: number;
    tasks: { key: string; title: string; blocker?: { key: string; title: string }; blocker_sprint?: number | null }[];
  }[];
}
/* docs/specs/31 §21.2 "Không bỏ sót": GET/PUT/DELETE /me/task-notify, GET/PUT /task-notify/defaults. */
export interface TaskNotifyPrefsData {
  mode: "immediate" | "digest";
  quiet_hours: [number, number] | null;
  /** Python weekday(): 5 Saturday, 6 Sunday (quiet all day). */
  quiet_days: number[];
  kinds: string[];
  kind_labels: Record<string, string>;
  telegram_kinds: string[];
  send_hour: number;
  channels: Record<string, TaskNotifyChannel>;
  urgent_kind: string;
  summary: TaskNotifySummary;
  telegram_linked: boolean;
  /** Personal only: false while following the organisation's defaults. */
  customised?: boolean;
  org_defaults?: boolean;
  /** Defaults only: an admin saved them. */
  set?: boolean;
}

/** GET /usage/summary (admins): anonymous usage per ISO week (Monday, UTC). */
export interface UsageSummary {
  weeks: string[];
  events: { event: string; surface: string; counts: number[]; total: number }[];
  roles: { role: string; counts: number[]; total: number }[];
  automation: { total: number; automated: number; share: number | null; target: number };
  keyboard: { total: number; keyboard: number; share: number | null };
  keep_days: number;
}
