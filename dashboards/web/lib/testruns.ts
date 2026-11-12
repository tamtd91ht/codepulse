/* Test runs, plans, milestones, configurations, shared steps (docs/specs/30 §13, API §14.2–§14.4). */

import { ApiError } from "./api";
import type { Person } from "./testcases";

export type { Person };

export type TestStatus = "untested" | "passed" | "failed" | "blocked" | "retest";
export type ResultStatus = Exclude<TestStatus, "untested">;

export const TEST_STATUSES: TestStatus[] = ["passed", "failed", "blocked", "retest", "untested"];
export const RESULT_STATUSES: ResultStatus[] = ["passed", "failed", "blocked", "retest"];
export const TEST_STATUS_LABEL: Record<TestStatus, string> = {
  untested: "Chưa test",
  passed: "Passed",
  failed: "Failed",
  blocked: "Blocked",
  retest: "Retest",
};

export interface Counts {
  untested: number;
  passed: number;
  failed: number;
  blocked: number;
  retest: number;
  total: number;
}

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
  template: "text" | "steps" | string;
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
  attachments?: { name?: string; path?: string; content_type?: string }[];
  ci?: { commit_sha?: string; branch?: string; mr_iid?: number; test_name?: string; file?: string; import_id?: number; attempts?: string[] };
}

export interface TestDetail extends TestOut {
  content: TestContent;
  results: ResultOut[];
  run: { id: number; key: string; name: string; completed: boolean };
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
  return { untested: 0, passed: 0, failed: 0, blocked: 0, retest: 0, total: 0 };
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
