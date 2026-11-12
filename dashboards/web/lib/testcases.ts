/* Testcase (docs/specs/30): the API's shapes and the labels shown. */

export type Priority = "critical" | "high" | "medium" | "low";
export type CaseKind = "manual" | "automated";
export type CaseStatus = "draft" | "approved" | "obsolete";

export const PRIORITY_LABEL: Record<Priority, string> = { critical: "Rất cao", high: "Cao", medium: "Trung bình", low: "Thấp" };
export const PRIORITY_TONE: Record<Priority, "bad" | "warn" | "info" | undefined> = { critical: "bad", high: "warn", medium: "info", low: undefined };
export const KIND_LABEL: Record<CaseKind, string> = { manual: "Thủ công", automated: "Tự động" };
export const STATUS_LABEL: Record<CaseStatus, string> = { draft: "Nháp", approved: "Đã duyệt", obsolete: "Lỗi thời" };
export const STATUS_TONE: Record<CaseStatus, "good" | "warn" | undefined> = { draft: "warn", approved: "good", obsolete: undefined };
export const PRIORITIES: Priority[] = ["critical", "high", "medium", "low"];
export const STATUSES: CaseStatus[] = ["draft", "approved", "obsolete"];

export interface TcProject {
  id: number;
  name: string;
  suites: number;
  cases: number;
  can_edit: boolean;
}

export interface Suite {
  id: number;
  business_project_id: number;
  home_name: string;
  name: string;
  description: string | null;
  source_url: string | null;
  source_sheet: string | null;
  projects: number[];
  shared: boolean;
  archived: boolean;
  total: number;
  approved: number;
  draft: number;
  obsolete: number;
  can_edit: boolean;
  can_delete: boolean;
  updated_at: string | null;
}

export interface CaseValues {
  code?: string | null;
  section?: string | null;
  title?: string | null;
  preconditions?: string | null;
  steps?: string | null;
  test_data?: string | null;
  expected?: string | null;
  notes?: string | null;
  priority?: Priority;
  kind?: CaseKind;
  automation?: string | null;
  tags?: string[];
  jira_keys?: string[];
  code_paths?: string[];
  status?: CaseStatus;
}

export interface TestCase {
  id: number;
  suite_id: number;
  code: string;
  section: string | null;
  title: string;
  preconditions: string | null;
  steps: string | null;
  test_data: string | null;
  expected: string | null;
  notes: string | null;
  priority: Priority;
  kind: CaseKind;
  automation: string | null;
  tags: string[];
  jira_keys: string[];
  code_paths: string[];
  status: CaseStatus;
  source: "manual" | "import" | "ai";
  revision: number;
  problems: string[];
  updated_at: string | null;
}

export interface Person {
  id: number;
  name: string;
}

export interface Revision {
  number: number;
  via: "editor" | "import" | "ai" | "restore" | "bulk";
  changed_by: Person | null;
  changed_at: string | null;
  snapshot: Record<string, unknown>;
}

export interface CaseDetail extends TestCase {
  can_edit: boolean;
  created_by: Person | null;
  updated_by: Person | null;
  created_at: string | null;
  revisions: Revision[];
}

export interface CaseList {
  items: TestCase[];
  total: number;
  sections: string[];
  tags: string[];
  can_edit: boolean;
}

export interface Stats {
  total: number;
  by_status: Record<CaseStatus, number>;
  by_priority: Record<Priority, number>;
  automated: number;
  problems: number;
  jira_issues: number;
  jira_covered: number;
  uncovered: { key: string; title: string | null; url: string | null }[];
}

export interface SheetRead {
  name: string;
  header_row: number;
  mapping: Record<string, number>;
  rows: string[][];
  total: number;
}

export interface ReadOut {
  title: string;
  source_url: string | null;
  sheets: SheetRead[];
  fields: Record<string, string>;
}

export interface ImportResult {
  suite_id: number;
  name: string;
  created: number;
  updated: number;
  unchanged: number;
  skipped: number;
  missing: number;
}

export type AiKind = "generate" | "audit" | "impact";
export type AiStatus = "queued" | "fetching" | "writing" | "ready" | "failed" | "applied" | "discarded";

export interface Proposal {
  section?: string | null;
  title: string;
  preconditions?: string | null;
  steps?: string | null;
  test_data?: string | null;
  expected?: string | null;
  priority: Priority;
  tags?: string[];
  jira_keys?: string[];
}

export interface AiIssue {
  codes: string[];
  kind: "duplicate" | "unclear" | "missing_expected" | "missing_steps" | "wrong_priority" | "other";
  message: string;
}

export interface AiRun {
  id: number;
  kind: AiKind;
  business_project_id: number;
  suite_id: number | null;
  suite_name: string | null;
  merge_request_id: number | null;
  mr_label: string | null;
  status: AiStatus;
  running: boolean;
  error: string | null;
  input: { instruction?: string | null; doc_ids?: number[]; jira_keys?: string[]; urls?: string[]; files?: { name: string; kind: string }[]; count?: number };
  sources: { url?: string | null; name?: string | null; title?: string | null; via: string; chars: number; error: string | null }[] | null;
  result: {
    summary?: string;
    cases?: Proposal[] | { id: number; reason: string; confidence: string }[];
    missing?: Proposal[];
    gaps?: string[];
    issues?: AiIssue[];
    warnings?: string[];
    applied?: number[];
  } | null;
  model: string | null;
  cost_usd: number | null;
  applied_count: number | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface RunList {
  runs: AiRun[];
  per_day: number;
  used_today: number;
  max_sources: number;
  max_count: number;
}

export const AI_KIND_LABEL: Record<AiKind, string> = { generate: "Sinh testcase", audit: "Rà soát bộ", impact: "Phân tích MR" };
export const AI_STATUS_LABEL: Record<AiStatus, string> = {
  queued: "Đang chờ",
  fetching: "Đang đọc nguồn",
  writing: "AI đang viết",
  ready: "Có kết quả",
  failed: "Lỗi",
  applied: "Đã thêm",
  discarded: "Đã bỏ",
};
export const ISSUE_LABEL: Record<AiIssue["kind"], string> = {
  duplicate: "Trùng",
  unclear: "Mơ hồ",
  missing_expected: "Thiếu kết quả",
  missing_steps: "Thiếu bước",
  wrong_priority: "Ưu tiên",
  other: "Khác",
};

export interface AiBrief {
  id: number;
  status: AiStatus;
  error: string | null;
  summary: string | null;
  missing: number;
  created_at: string | null;
  finished_at: string | null;
}

export interface ImpactCase {
  case: TestCase;
  suite: string;
  reasons: string[];
  ai_reason: string | null;
  confidence: string | null;
}

export interface MrImpact {
  mr_id: number;
  iid: number;
  title: string | null;
  repo: string;
  business_project_id: number | null;
  merged_at: string | null;
  target_branch: string | null;
  files: number;
  review_id: number | null;
  cases: ImpactCase[];
  ai: AiBrief | null;
  can_edit: boolean;
  ai_allowed: boolean;
}

export interface RegressionMr {
  mr_id: number;
  iid: number;
  title: string | null;
  repo: string;
  merged_at: string | null;
  target_branch: string | null;
  files: number;
  matched: number;
  review_id: number | null;
  ai: AiBrief | null;
}

export interface Regression {
  mrs: RegressionMr[];
  cases: ImpactCase[];
  branches: string[];
  since: string;
  until: string;
}

export const AI_ACTIVE: AiStatus[] = ["queued", "fetching", "writing"];

/** Lines of a list typed one per line or comma-separated. */
export function splitList(text: string): string[] {
  return text
    .split(/[\n,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
