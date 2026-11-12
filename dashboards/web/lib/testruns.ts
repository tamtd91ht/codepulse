/* Test runs, plans, milestones, configurations, shared steps (docs/specs/30 §13, API §14.2–§14.4). */

import { ApiError } from "./api";
import type { CustomValues, Person } from "./testcases";

export type { Person };

export type BuiltinStatus = "untested" | "passed" | "failed" | "blocked" | "retest" | "skipped";
/** §15 G6: a built-in status or the key of a custom status of the dự án. */
export type TestStatus = BuiltinStatus | (string & {});
export type ResultStatus = Exclude<TestStatus, "untested">;

export const TEST_STATUSES: BuiltinStatus[] = ["passed", "failed", "blocked", "retest", "skipped", "untested"];
export const RESULT_STATUSES: BuiltinStatus[] = ["passed", "failed", "blocked", "retest", "skipped"];
export const TEST_STATUS_LABEL: Record<string, string> = {
  untested: "Chưa test",
  passed: "Passed",
  failed: "Failed",
  blocked: "Blocked",
  retest: "Retest",
  skipped: "Skipped",
};

/** Tests by status: the built-in keys, plus one per custom status in use (§15 G6). */
export interface Counts {
  untested: number;
  passed: number;
  failed: number;
  blocked: number;
  retest: number;
  skipped?: number;
  total: number;
  [status: string]: number | undefined;
}

/* ---- Result statuses of a dự án (§15 G6): GET /testcases/statuses ---- */

export type StatusColor = "good" | "bad" | "warn" | "info" | "muted" | "primary";
export const STATUS_COLORS: { value: StatusColor; label: string }[] = [
  { value: "good", label: "Xanh lá" },
  { value: "bad", label: "Đỏ" },
  { value: "warn", label: "Cam" },
  { value: "info", label: "Xanh dương" },
  { value: "primary", label: "Đậm" },
  { value: "muted", label: "Xám" },
];

export interface StatusDef {
  id: number | null;
  key: string;
  name: string;
  color: StatusColor | string;
  done: boolean;
  builtin: boolean;
  position: number;
  archived: boolean;
}

export interface StatusList {
  items: StatusDef[];
  can_edit: boolean;
}

/** The built-in statuses, while the dự án's list loads (or where no dự án is known). */
export const BUILTIN_STATUSES: StatusDef[] = [
  { id: null, key: "passed", name: "Passed", color: "good", done: true, builtin: true, position: 1, archived: false },
  { id: null, key: "failed", name: "Failed", color: "bad", done: true, builtin: true, position: 2, archived: false },
  { id: null, key: "blocked", name: "Blocked", color: "primary", done: true, builtin: true, position: 3, archived: false },
  { id: null, key: "retest", name: "Retest", color: "warn", done: false, builtin: true, position: 4, archived: false },
  { id: null, key: "skipped", name: "Skipped", color: "muted", done: true, builtin: true, position: 5, archived: false },
  { id: null, key: "untested", name: "Chưa test", color: "muted", done: false, builtin: true, position: 1_000_000, archived: false },
];

/** The CSS class of a status: built-in ones have their own colours, custom ones their token. */
export function statusClass(key: string, defs?: StatusDef[] | null): string {
  if (key in TEST_STATUS_LABEL) return `st-${key}`;
  const def = defs?.find((d) => d.key === key);
  return `st-c-${def?.color ?? "muted"}`;
}

export function statusName(key: string, defs?: StatusDef[] | null): string {
  if (key === "untested") return TEST_STATUS_LABEL.untested;
  return defs?.find((d) => d.key === key)?.name ?? TEST_STATUS_LABEL[key] ?? key;
}

/** Shortcut keys of the built-in result statuses in the test drawer. */
export const STATUS_KEYS: Record<string, string> = { passed: "P", failed: "F", blocked: "B", retest: "R", skipped: "S" };

/** A file attached to a result: uploaded (`id`, §15 G6) or named by a CI report (`path`, G1). */
export interface Attachment {
  id?: number;
  name?: string;
  mime?: string;
  size?: number;
  path?: string;
  content_type?: string;
  url?: string;
}

export const attachmentUrl = (id: number) => `/api/v1/testcases/attachments/${id}`;

export interface Selection {
  suite_id?: number;
  section_ids?: number[];
  case_ids?: number[];
  all?: boolean;
  priority?: string[];
  type?: string[];
  tags?: string[];
  regression?: { days: number; target_branch?: string };
}

export interface RunOut {
  id: number;
  key: string;
  business_project_id: number;
  plan_id: number | null;
  plan_name: string | null;
  milestone_id: number | null;
  milestone_name: string | null;
  name: string;
  description: string | null;
  config: string | null;
  config_ids: number[];
  assigned_to: Person | null;
  refs: string[] | string | null;
  source: "manual" | "regression" | "plan" | string;
  source_meta: Record<string, unknown> | null;
  completed_at: string | null;
  created_at: string | null;
  created_by: Person | null;
  counts: Counts;
  can_edit: boolean;
  /** §15 G6: time spent (sum of the results' elapsed) and left (estimates of tests not done). */
  elapsed_total_s?: number;
  remaining_estimate_s?: number;
  /** §15 G6: only assignees (and managers) record; whether the viewer records any test. */
  assigned_only?: boolean;
  can_record_all?: boolean;
  /** §15.13 G9: the run's tests come from this baseline (frozen content). */
  baseline_id?: number | null;
  baseline_name?: string | null;
}

/* ---- Exploratory session (§15.12 G8) ---- */

export type SessionNoteKind = "note" | "bug" | "question" | "idea";
export const SESSION_NOTE_KINDS: SessionNoteKind[] = ["note", "bug", "question", "idea"];
export const SESSION_NOTE_LABEL: Record<SessionNoteKind, string> = { note: "Ghi chú", bug: "Lỗi", question: "Câu hỏi", idea: "Ý tưởng" };

export interface SessionNote {
  at: string | null;
  offset_s: number | null;
  kind: SessionNoteKind | (string & {});
  text: string;
  user_id: number | null;
  user_name?: string | null;
}

/**
 * `elapsed_s`: time banked by the earlier (paused) parts; while `running`, the current part began
 * at `started_at` and counts on top.
 */
export interface Session {
  started_at: string | null;
  elapsed_s: number;
  running: boolean;
  notes: SessionNote[];
}

/** Seconds the session has run at `now` (ms). */
export function sessionElapsed(s: Session | null | undefined, now: number): number {
  if (!s) return 0;
  const base = Math.max(0, s.elapsed_s || 0);
  if (!s.running || !s.started_at) return base;
  const start = Date.parse(s.started_at);
  return Number.isFinite(start) ? base + Math.max(0, Math.floor((now - start) / 1000)) : base;
}

/** "01:05:09" / "05:09". */
export function clockText(s: number): string {
  const v = Math.max(0, Math.floor(s));
  const h = Math.floor(v / 3600);
  const m = Math.floor((v % 3600) / 60);
  const sec = v % 60;
  const two = (x: number) => String(x).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(sec)}` : `${two(m)}:${two(sec)}`;
}

export interface TestOut {
  id: number;
  key: string;
  run_id: number;
  case_id: number | null;
  case_key: string | null;
  title: string;
  section: string | null;
  priority: string | null;
  type: string | null;
  status: TestStatus;
  assigned_to: Person | null;
  tested_at: string | null;
  estimate_s: number | null;
  defects: string[];
  latest_comment: string | null;
  /** §15 G1: the case's CI results disagree. */
  flaky?: boolean;
  /** §15 G6: of the latest result with a status. */
  elapsed_s?: number | null;
  failed_step?: number | null;
  /** The case's suite when the server sends it: narrows the result fields that apply (§15.8). */
  suite_id?: number | null;
  /** §15.15 G13: the parameter row (1-based) this test runs, and its values. */
  param_row?: number | null;
  params?: Record<string, unknown> | unknown[] | null;
  /** §15.12 G8: the exploratory session. */
  session?: Session | null;
}

export interface TestList {
  items: TestOut[];
  total: number;
}

export interface StepView {
  content?: string | null;
  expected?: string | null;
  shared?: number;
  shared_id?: number | null;
  shared_title?: string | null;
}

export interface TestContent {
  case_key: string | null;
  title: string;
  section: string | null;
  template: "text" | "steps" | "bdd" | "exploratory" | (string & {});
  preconditions: string | null;
  steps: string | null;
  expected: string | null;
  steps_list: StepView[] | null;
  steps_view?: StepView[] | null;
  test_data: string | null;
  notes: string | null;
  priority: string | null;
  type: string | null;
  estimate_s: number | null;
  refs: string[] | string | null;
  automation: string | null;
  /** §15.12 G8: Gherkin of a "bdd" case, mission of an "exploratory" one (values filled in). */
  bdd?: string | null;
  charter?: string | null;
  /** §15.15: the case's table, when the server copies it into the content. */
  params?: { columns: string[]; rows: string[][] } | null;
}

export interface StepResult {
  status: TestStatus;
  actual: string | null;
}

export interface ResultOut {
  id: number;
  status: ResultStatus | null;
  comment: string | null;
  version: string | null;
  elapsed_s: number | null;
  elapsed: string | null;
  defects: string[];
  step_results: StepResult[] | null;
  assigned_to: Person | null;
  created_by: Person | null;
  created_at: string | null;
  /** §15 G1: "ci" results carry the failure log, stack trace, attachments and CI facts. */
  source?: "manual" | "ci";
  log?: string | null;
  stack?: string | null;
  attachments?: Attachment[];
  /** §15 G6: the first failed (else blocked) step, 1-based. */
  failed_step?: number | null;
  ci?: { commit_sha?: string; branch?: string; mr_iid?: number; test_name?: string; file?: string; import_id?: number; attempts?: string[] };
  /** §15.8 G5: values of the result's custom fields. */
  custom?: CustomValues;
}

export interface TestDetail extends TestOut {
  content: TestContent;
  results: ResultOut[];
  run: { id: number; key: string; name: string; completed: boolean; business_project_id?: number | null };
  can_record: boolean;
  can_edit: boolean;
}

export interface PlanOut {
  id: number;
  key: string;
  name: string;
  description: string | null;
  milestone_id: number | null;
  milestone_name: string | null;
  completed_at: string | null;
  created_at: string | null;
  counts: Counts;
  /** A count in the list; the runs themselves in PlanDetail. */
  runs: number | RunOut[];
  can_edit: boolean;
}

export interface PlanDetail extends Omit<PlanOut, "runs"> {
  runs: RunOut[];
}

export interface PlanEntry {
  name?: string;
  selection: Selection;
  config_ids?: number[];
  assigned_to?: number | null;
}

export interface Milestone {
  id: number;
  key: string;
  name: string;
  description: string | null;
  start_on: string | null;
  due_on: string | null;
  completed_at: string | null;
  runs: number;
  counts: Counts;
  can_edit: boolean;
}

export interface ConfigGroup {
  id: number;
  name: string;
  configs: { id: number; name: string }[];
}

export interface SharedStep {
  id: number;
  key: string;
  title: string;
  steps: { content: string; expected: string | null }[];
  used_by: number;
  updated_at: string | null;
  can_edit: boolean;
}

/** GET /testcases/sections?suite_id= (§14.1), in the reading order of the tree. */
export interface SectionNode {
  id: number;
  suite_id: number;
  parent_id: number | null;
  name: string;
  description: string | null;
  position: number;
  depth: number;
  path: string;
  cases: number;
  total: number;
}

export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function emptyCounts(): Counts {
  return { untested: 0, passed: 0, failed: 0, blocked: 0, retest: 0, skipped: 0, total: 0 };
}

/** "73%": share of the tests whose latest result is Passed. */
export function passedPct(c: Counts | null | undefined): number {
  if (!c || !c.total) return 0;
  return Math.round((c.passed / c.total) * 100);
}

/** Tests with any result (not untested). */
export function doneCount(c: Counts): number {
  return c.total - c.untested;
}

export function refsText(refs: string[] | string | null | undefined): string {
  if (!refs) return "";
  return Array.isArray(refs) ? refs.join(", ") : refs;
}

/** "1h 30m" for a number of seconds. */
export function durationText(s: number | null | undefined): string {
  if (!s) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return [h ? `${h}h` : "", m ? `${m}m` : "", sec ? `${sec}s` : ""].filter(Boolean).join(" ");
}

/** "Hôm nay" as YYYY-MM-DD in the viewer's zone. */
export function today(): string {
  const d = new Date();
  const two = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

export const dayText = (d: string | null | undefined) => (d ? d.split("-").reverse().join("/") : "");

export const SOURCE_LABEL: Record<string, string> = { manual: "Thủ công", regression: "Cần test lại", plan: "Từ plan", ci: "Từ CI" };

/* ---- Jira bugs from results and the dự án's test settings (docs/specs/30a G3, §15) ---- */

export type BugStateName = "opened" | "closed" | "unknown";

export interface BugState {
  key: string;
  state: BugStateName;
  /** Jira's status name (live reads only). */
  status: string | null;
  title: string | null;
  web_url: string | null;
  source: "sync" | "jira" | null;
}

export interface BugIn {
  summary?: string;
  description?: string;
  issue_type?: string;
  components?: string[];
  labels?: string[];
  assignee?: string;
  result_id?: number;
}

export interface BugOut {
  key: string;
  web_url: string | null;
  verified: boolean;
  test: TestDetail;
}

export interface TestProjectSettings {
  business_project_id: number;
  assigned_only: boolean;
  require_approval: boolean;
  jira_project_key: string | null;
  jira_issue_type: string;
  jira_components: string[];
  jira_labels: string[];
  jira_configured: boolean;
  jira_write_enabled: boolean;
  jira_base_url: string | null;
  can_edit: boolean;
}

/** The value of the run view's status filter that keeps only tests to run again. */
export const NEEDS_RETEST = "needs_retest";

export const BUG_STATE_LABEL: Record<BugStateName, string> = { opened: "Đang mở", closed: "Đã đóng", unknown: "Chưa rõ" };

/** "A, B" or a list -> trimmed names, once each. */
export function splitNames(text: string): string[] {
  return [...new Set(text.split(",").map((s) => s.trim()).filter(Boolean))];
}
