/* Testcase (docs/specs/30): the API's shapes and the labels shown. */

export type Priority = "critical" | "high" | "medium" | "low";
export type CaseKind = "manual" | "automated";
/** §15.9 G7: "review" = Chờ duyệt (draft → review → approved; a rejection goes back to draft). */
export type CaseStatus = "draft" | "review" | "approved" | "obsolete";

export const PRIORITY_LABEL: Record<Priority, string> = { critical: "Rất cao", high: "Cao", medium: "Trung bình", low: "Thấp" };
export const PRIORITY_TONE: Record<Priority, "bad" | "warn" | "info" | undefined> = { critical: "bad", high: "warn", medium: "info", low: undefined };
export const KIND_LABEL: Record<CaseKind, string> = { manual: "Thủ công", automated: "Tự động" };
export const STATUS_LABEL: Record<CaseStatus, string> = { draft: "Nháp", review: "Chờ duyệt", approved: "Đã duyệt", obsolete: "Lỗi thời" };
export const STATUS_TONE: Record<CaseStatus, "good" | "warn" | "info" | undefined> = { draft: "warn", review: "info", approved: "good", obsolete: undefined };
export const PRIORITIES: Priority[] = ["critical", "high", "medium", "low"];
export const STATUSES: CaseStatus[] = ["draft", "review", "approved", "obsolete"];

export interface TcProject {
  id: number;
  name: string;
  suites: number;
  cases: number;
  /** tests.edit: design the cases. */
  can_edit: boolean;
  /** tests.run: record results of any test. */
  can_run: boolean;
  /** tests.manage: runs, plans, milestones, configurations, custom fields. */
  can_manage: boolean;
  /** §15.9 tests.delete (absent on older servers: tests.edit decides). */
  can_delete?: boolean;
  /** §15.9 tests.approve (absent on older servers: tests.manage decides). */
  can_approve?: boolean;
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
  /** "Vùng code" every case inherits: "{repo}:{glob}" or a bare glob (§15.2 G2). */
  code_paths?: string[];
}

/* ---- TestRail model (docs/specs/30 §13, §14.1) ---- */

/** §15.12 G8: "bdd" (Gherkin scenario) and "exploratory" (charter + timebox) beside text / steps. */
export type CaseTemplate = "text" | "steps" | "bdd" | "exploratory";
export const TEMPLATE_LABEL: Record<CaseTemplate, string> = { text: "Văn bản", steps: "Từng bước", bdd: "BDD (Gherkin)", exploratory: "Khám phá (Exploratory)" };
export type CaseType =
  | "acceptance"
  | "accessibility"
  | "automated"
  | "compatibility"
  | "destructive"
  | "functional"
  | "other"
  | "performance"
  | "regression"
  | "security"
  | "smoke"
  | "usability";

export const CASE_TYPES: CaseType[] = [
  "acceptance",
  "accessibility",
  "automated",
  "compatibility",
  "destructive",
  "functional",
  "other",
  "performance",
  "regression",
  "security",
  "smoke",
  "usability",
];
export const TYPE_LABEL: Record<CaseType, string> = {
  acceptance: "Acceptance",
  accessibility: "Accessibility",
  automated: "Automated",
  compatibility: "Compatibility",
  destructive: "Destructive",
  functional: "Functional",
  other: "Other",
  performance: "Performance",
  regression: "Regression",
  security: "Security",
  smoke: "Smoke & Sanity",
  usability: "Usability",
};

/** One step as stored: its own text, or a pointer to a shared-steps set. */
export type StepItem = { content: string; expected?: string | null } | { shared: number };
/** One step as shown (shared steps expanded). */
export interface StepView {
  content: string;
  expected: string | null;
  shared_id?: number | null;
  shared_title?: string | null;
}

export interface Section {
  id: number;
  suite_id: number;
  parent_id: number | null;
  name: string;
  description: string | null;
  position: number;
  depth: number;
  path: string;
  /** Cases right in this section. */
  cases: number;
  /** Cases here and in the sub-sections. */
  total: number;
  /** "Vùng code" its cases and sub-sections inherit. */
  code_paths?: string[];
}

export interface SharedSteps {
  id: number;
  key: string;
  title: string;
  steps: { content: string; expected: string | null }[];
  used_by: number;
  updated_at: string | null;
  can_edit: boolean;
}

/** The fields of a milestone the case pages need (the full shape lives with the runs). */
export interface CaseMilestone {
  id: number;
  key: string;
  name: string;
  completed_at: string | null;
}

export type ResultStatus = "passed" | "failed" | "blocked" | "retest";
export const RESULT_LABEL: Record<ResultStatus | "untested", string> = {
  untested: "Chưa test",
  passed: "Passed",
  failed: "Failed",
  blocked: "Blocked",
  retest: "Retest",
};
export const RESULT_TONE: Record<ResultStatus | "untested", "good" | "bad" | "warn" | "info" | undefined> = {
  untested: undefined,
  passed: "good",
  failed: "bad",
  blocked: "warn",
  retest: "info",
};

/** One result of a case, from any run (GET /testcases/{id}/results). */
export interface CaseResult {
  result: {
    id: number;
    status: ResultStatus;
    comment: string | null;
    version: string | null;
    elapsed: string | null;
    defects: string[];
    created_by: Person | null;
    created_at: string | null;
  };
  test_id: number;
  test_key: string;
  run_id: number;
  run_key: string;
  run_name: string;
  config: string | null;
  completed: boolean;
}

/** "C12" (the server's key, or made from the id for an older answer). */
export const caseKey = (c: { id: number; key?: string }) => c.key ?? `C${c.id}`;

/**
 * Items of a numbered text ("1. a\n2. b", "- a"): [] when it is not such a list. Same rule as
 * the server's `content.split_numbered`.
 */
export function splitNumbered(text: string | null | undefined): string[] {
  const lines = (text ?? "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) return [];
  const items: string[] = [];
  for (const line of lines) {
    const m = /^(?:(?:bước|buoc|step)\s*)?(\d{1,3})[.):-]\s*(.+)$/i.exec(line) ?? /^[-*•]\s+(.+)$/.exec(line);
    if (m) items.push(m[m.length - 1].trim());
    else if (items.length) items[items.length - 1] = `${items[items.length - 1]}\n${line}`;
    else return [];
  }
  return items.length >= 2 ? items : [];
}

/** Template "steps" from the numbered texts: expected items pair by number when counts match. */
export function stepsFromText(steps: string | null | undefined, expected: string | null | undefined): { content: string; expected: string }[] {
  const items = splitNumbered(steps);
  if (!items.length) return [];
  const results = splitNumbered(expected);
  if (results.length === items.length) return items.map((content, i) => ({ content, expected: results[i] }));
  const out = items.map((content) => ({ content, expected: "" }));
  if ((expected ?? "").trim()) out[out.length - 1].expected = (expected ?? "").trim();
  return out;
}

export interface CaseValues {
  section_id?: number | null;
  template?: CaseTemplate;
  steps_list?: StepItem[];
  type?: CaseType;
  estimate?: string | null;
  milestone_id?: number | null;
  /** Older fields, still accepted by the type; `code` is not sent any more (§14.1). */
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
  /** §15 G1: "classname.name" in CI reports, unique in the suite. */
  automation_id?: string | null;
  tags?: string[];
  jira_keys?: string[];
  code_paths?: string[];
  status?: CaseStatus;
  custom?: CustomValues;
  /** §15.12: the Gherkin scenario of template "bdd". */
  bdd?: string | null;
  /** §15.12: the mission of template "exploratory". */
  charter?: string | null;
  /** §15.15 G13: one test per row in a run; null clears the table. */
  params?: ParamTable | null;
}

export interface TestCase {
  id: number;
  suite_id: number;
  /** "C12": the case's fixed key, made by the server (§13.1). */
  key?: string;
  /** "Mã gốc": the sheet row's own code when imported, else null (read only). */
  code: string | null;
  section: string | null;
  section_id?: number | null;
  template?: CaseTemplate;
  steps_list?: StepItem[];
  steps_view?: StepView[];
  type?: CaseType;
  estimate_s?: number | null;
  estimate?: string | null;
  milestone_id?: number | null;
  title: string;
  preconditions: string | null;
  steps: string | null;
  test_data: string | null;
  expected: string | null;
  notes: string | null;
  priority: Priority;
  kind: CaseKind;
  automation: string | null;
  automation_id?: string | null;
  /** §15 G1: its CI results disagree. */
  flaky?: boolean;
  tags: string[];
  jira_keys: string[];
  code_paths: string[];
  status: CaseStatus;
  /** §15.8 G5: custom field values {key: value}. */
  custom?: CustomValues;
  /** §15.12 G8. */
  bdd?: string | null;
  charter?: string | null;
  /** §15.15 G13. */
  params?: ParamTable | null;
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
  via: "editor" | "import" | "ai" | "restore" | "bulk" | (string & {});
  changed_by: Person | null;
  changed_at: string | null;
  snapshot: Record<string, unknown>;
}

export interface CaseDetail extends TestCase {
  can_edit: boolean;
  /** §15.9 tests.delete (absent on older servers: can_edit decides). */
  can_delete?: boolean;
  /** §15.9 tests.approve. */
  can_approve?: boolean;
  /** The dự án requires approval (require_approval). */
  approval_on?: boolean;
  created_by: Person | null;
  updated_by: Person | null;
  created_at: string | null;
  revisions: Revision[];
}

export interface CaseList {
  items: TestCase[];
  total: number;
  /** Gone with the section tree (§14.1); older servers still send it. */
  sections?: string[];
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

export type AiKind = "generate" | "audit" | "impact" | "links" | "duplicates" | "convert";
export type AiStatus = "queued" | "fetching" | "writing" | "ready" | "failed" | "applied" | "discarded";

export interface Proposal {
  section?: string | null;
  template?: CaseTemplate;
  steps_list?: { content: string; expected?: string | null }[];
  type?: CaseType;
  estimate?: string | null;
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
  input: { direction?: ConvertDirection; case_ids?: number[]; instruction?: string | null; doc_ids?: number[]; jira_keys?: string[]; urls?: string[]; files?: { name: string; kind: string }[]; count?: number };
  sources: { url?: string | null; name?: string | null; title?: string | null; via: string; chars: number; error: string | null }[] | null;
  result: {
    summary?: string;
    cases?: Proposal[] | { id: number; reason: string; confidence: string }[];
    missing?: Proposal[];
    gaps?: string[];
    issues?: AiIssue[];
    links?: LinkSuggestion[];
    /** §15.10 kind "duplicates": groups of the same case, with the one to keep. */
    groups?: DuplicateGroup[];
    /** §15.12 kind "convert": the new form of each case. */
    proposals?: ConvertProposal[];
    direction?: ConvertDirection;
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

export const AI_KIND_LABEL: Record<AiKind, string> = { generate: "Sinh testcase", audit: "Rà soát bộ", impact: "Phân tích MR", links: "Gợi ý liên kết", duplicates: "Tìm trùng lặp", convert: "Chuyển dạng testcase" };
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

/* ---- Links to Jira and code (docs/specs/30 §15.2 G2) ---- */

/** One AI link suggestion: what to add to a case. */
export interface LinkSuggestion {
  id: number;
  key: string;
  title: string;
  current_jira: string[];
  current_code: string[];
  jira_keys: string[];
  code_paths: string[];
  reason: string;
}

export interface RepoRef {
  id: number;
  /** path_with_namespace: "{path}:{glob}". */
  path: string;
  /** Its last part: "{name}:{glob}" works too. */
  name: string;
  folders: string[];
}

export interface IssueDoc {
  id: number;
  title: string;
  keys: string[];
}

export interface TraceCase {
  id: number;
  key: string;
  title: string;
  status: CaseStatus;
  suite: string;
  run_id: number | null;
  run_key: string | null;
  run_name: string | null;
  test_status: ResultStatus | "untested" | null;
  defects: string[];
}

export interface TraceRow {
  key: string;
  title: string;
  type: string | null;
  state: string | null;
  sprint: string | null;
  labels: string[];
  url: string | null;
  cases: TraceCase[];
}

export interface Trace {
  rows: TraceRow[];
  issues: number;
  covered: number;
  without_requirement: number;
  sprints: string[];
  labels: string[];
  /** Issues the pages go through ("chưa có testcase" applied); `rows` is one page of them. */
  total: number;
  limit: number;
  offset: number;
}

/* ---- Custom fields (docs/specs/30 §15.8 G5, API §15.11) ---- */

export type FieldEntity = "case" | "result";
export type FieldType = "text" | "textarea" | "number" | "date" | "dropdown" | "multiselect" | "checkbox" | "user" | "url";
/** A stored value: text / textarea / url / date / dropdown a string, number, multi-select a list, checkbox a bool, user an id. */
export type CustomValue = string | number | boolean | string[] | null;
export type CustomValues = Record<string, CustomValue>;

export const FIELD_TYPES: FieldType[] = ["text", "textarea", "number", "date", "dropdown", "multiselect", "checkbox", "user", "url"];
export const FIELD_TYPE_LABEL: Record<FieldType, string> = {
  text: "Văn bản ngắn",
  textarea: "Văn bản dài",
  number: "Số",
  date: "Ngày",
  dropdown: "Chọn một",
  multiselect: "Chọn nhiều",
  checkbox: "Có / không",
  user: "Người dùng",
  url: "Liên kết (URL)",
};
export const FIELD_ENTITY_LABEL: Record<FieldEntity, string> = { case: "Testcase", result: "Kết quả" };
/** Types with a list of options. */
export const OPTION_TYPES: FieldType[] = ["dropdown", "multiselect"];
/** Types the case list filter compares for equality; the others match "contains". */
export const EXACT_FILTER_TYPES: FieldType[] = ["dropdown", "multiselect", "checkbox", "user"];
/** Limits the server checks (§15.8). */
export const FIELD_MAX_LEN: Partial<Record<FieldType, number>> = { text: 2000, url: 2000, textarea: 20000 };

export interface TestField {
  id: number;
  /** Slug made from the name when created; never changes. */
  key: string;
  name: string;
  entity: FieldEntity;
  type: FieldType;
  options: string[];
  required: boolean;
  default: CustomValue;
  /** Empty: every suite. */
  suite_ids: number[];
  /** Empty: both templates. */
  templates: CaseTemplate[];
  position: number;
  archived_at: string | null;
}

/** The live fields that apply to a case of `suiteId` with `template` (§15.8); unknown suite / template: no narrowing. */
export function applicableFields(fields: TestField[] | undefined, suiteId: number | null | undefined, template: string | null | undefined): TestField[] {
  return (fields ?? []).filter(
    (f) =>
      !f.archived_at &&
      (!f.suite_ids.length || suiteId == null || f.suite_ids.includes(suiteId)) &&
      (!f.templates.length || !template || f.templates.includes(template as CaseTemplate)),
  );
}

/** No value given (false of a checkbox is a value). */
export function isEmptyValue(v: CustomValue | undefined): boolean {
  return v === null || v === undefined || (typeof v === "string" && !v.trim()) || (Array.isArray(v) && !v.length);
}

/** A value as text for a cell or the case view ("—" when empty). */
export function fieldText(f: TestField, v: CustomValue | undefined, people?: Person[]): string {
  if (isEmptyValue(v)) return "—";
  switch (f.type) {
    case "checkbox":
      return v ? "Có" : "Không";
    case "multiselect":
      return Array.isArray(v) ? v.join(", ") : String(v);
    case "user": {
      const p = people?.find((x) => x.id === Number(v));
      return p ? p.name : `#${String(v)}`;
    }
    case "date":
      return typeof v === "string" ? v.split("-").reverse().join("/") : String(v);
    default:
      return Array.isArray(v) ? v.join(", ") : String(v);
  }
}

/**
 * Names of required fields left empty, and values the server would refuse (§15.8), for the
 * client-side check before a save. Checkbox: false is a value.
 */
export function customProblems(fields: TestField[], values: CustomValues): string[] {
  const out: string[] = [];
  for (const f of fields) {
    const v = values[f.key];
    if (isEmptyValue(v)) {
      if (f.required && f.type !== "checkbox") out.push(`“${f.name}” là bắt buộc`);
      continue;
    }
    const max = FIELD_MAX_LEN[f.type];
    if (max && typeof v === "string" && v.length > max) out.push(`“${f.name}” dài quá ${max.toLocaleString("vi-VN")} ký tự`);
    if (f.type === "url" && typeof v === "string" && !/^https?:\/\/\S+$/i.test(v.trim())) out.push(`“${f.name}” cần là link http(s)`);
    if (f.type === "number" && (typeof v !== "number" || !Number.isFinite(v))) out.push(`“${f.name}” cần là số`);
  }
  return out;
}

/* ---- Approval (§15.9 G7) ---- */

export interface CaseReview {
  id: number;
  decision: "submit" | "approve" | "reject" | (string & {});
  comment: string | null;
  revision: number | null;
  user_id: number | null;
  user_name: string | null;
  created_at: string | null;
}

/* ---- Bulk changes, find & replace, undo (§15.10 G10) ---- */

export interface BulkOut {
  changed: number;
  batch_id?: number | string | null;
  /** A count, or the cases left as they were. */
  skipped?: number | { case_id: number; key?: string | null; reason?: string | null }[] | null;
}

export interface BulkOp {
  id: number | string;
  kind: string;
  summary: string | null;
  count: number;
  created_at: string | null;
  undone_at: string | null;
  can_undo: boolean;
}

export interface UndoOut {
  restored: number;
  deleted: number;
  skipped: { case_id: number; key?: string | null; reason?: string | null }[];
}

export type ReplaceField = "title" | "preconditions" | "steps" | "expected" | "notes" | "test_data";
export const REPLACE_FIELDS: ReplaceField[] = ["title", "preconditions", "steps", "expected", "notes", "test_data"];
export const REPLACE_FIELD_LABEL: Record<string, string> = {
  title: "Tiêu đề",
  preconditions: "Tiền điều kiện",
  steps: "Các bước",
  expected: "Kết quả mong đợi",
  notes: "Ghi chú",
  test_data: "Dữ liệu test",
};

export interface ReplacePreview {
  total: number;
  truncated?: boolean;
  matches: { case_id: number; key: string; title: string; changes: { field: string; before: string; after: string }[] }[];
}

export const BULK_KIND_LABEL: Record<string, string> = {
  status: "Đổi trạng thái",
  priority: "Đổi ưu tiên",
  type: "Đổi type",
  kind: "Đổi cách chạy",
  estimate: "Đặt ước tính",
  milestone: "Gắn milestone",
  move: "Chuyển bộ",
  section: "Chuyển thư mục",
  tags: "Sửa nhãn",
  jira: "Thêm mã Jira",
  custom: "Đặt trường tuỳ biến",
  copy: "Sao chép",
  replace: "Tìm & thay",
  duplicates: "Gộp trùng (AI)",
};

export interface DuplicateGroup {
  keep: number;
  duplicates: number[];
  reason: string;
}

/* ---- BDD, exploratory, parameters (§15.12, §15.15, API §15.16) ---- */

export interface ParamTable {
  columns: string[];
  rows: string[][];
}

export const PARAM_MAX_COLS = 20;
export const PARAM_MAX_ROWS = 200;
export const PARAM_MAX_VALUE = 500;
export const PARAM_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Names used as {{name}} in a text, once each, in order. */
export function paramVars(text: string | null | undefined): string[] {
  const out: string[] = [];
  for (const m of (text ?? "").matchAll(/\{\{\s*([^{}\s]+)\s*\}\}/g)) if (!out.includes(m[1])) out.push(m[1]);
  return out;
}

/** What the server would refuse in a table (§15.15), as sentences. */
export function paramProblems(t: ParamTable): string[] {
  const out: string[] = [];
  if (t.columns.length > PARAM_MAX_COLS) out.push(`Tối đa ${PARAM_MAX_COLS} cột`);
  if (t.rows.length > PARAM_MAX_ROWS) out.push(`Tối đa ${PARAM_MAX_ROWS} dòng`);
  const seen = new Set<string>();
  t.columns.forEach((c, i) => {
    if (!PARAM_NAME.test(c)) out.push(`Tên cột ${i + 1} “${c}” chỉ gồm chữ không dấu, số, _ và không bắt đầu bằng số`);
    else if (seen.has(c)) out.push(`Tên cột “${c}” bị trùng`);
    seen.add(c);
  });
  if (t.rows.some((r) => r.some((v) => (v ?? "").length > PARAM_MAX_VALUE))) out.push(`Giá trị dài quá ${PARAM_MAX_VALUE} ký tự`);
  return out;
}

/** A test's parameter values as [name, value] pairs: a {name: value} map, or values paired with `columns`. */
export function paramPairs(values: Record<string, unknown> | unknown[] | null | undefined, columns?: string[] | null): [string, string][] {
  if (!values) return [];
  if (Array.isArray(values)) return values.map((v, i) => [columns?.[i] ?? `cột ${i + 1}`, v == null ? "" : String(v)]);
  return Object.entries(values).map(([k, v]) => [k, v == null ? "" : String(v)]);
}

/** POST /testcases/bdd/check. */
export interface BddCheck {
  ok: boolean;
  errors: { line: number | null; message: string }[];
  /** A count, or the scenarios found. */
  scenarios?: number | unknown[];
  outline_columns?: string[] | null;
}

/** POST /testcases/import/feature (one file). Counts, or the cases / sections themselves. */
export interface FeatureImportOut {
  created: number | unknown[];
  updated: number | unknown[];
  sections?: number | unknown[];
  errors?: (string | { line?: number | null; message?: string; scenario?: string | null })[];
}

export const countOf = (v: number | unknown[] | null | undefined): number => (typeof v === "number" ? v : Array.isArray(v) ? v.length : 0);

export type ConvertDirection = "to_bdd" | "to_steps";
export const CONVERT_LABEL: Record<ConvertDirection, string> = { to_bdd: "Chuyển sang BDD", to_steps: "Chuyển sang dạng bước" };

export interface ConvertProposal {
  case_id: number;
  template: CaseTemplate;
  bdd?: string | null;
  steps_list?: { content: string; expected?: string | null }[] | null;
  preconditions?: string | null;
}

/* ---- Baselines (§15.13 G9) ---- */

export interface Baseline {
  id: number;
  business_project_id: number;
  suite_id: number;
  suite_name: string | null;
  name: string;
  description: string | null;
  milestone_id: number | null;
  milestone_name: string | null;
  case_count: number;
  created_by: number | Person | null;
  created_by_name: string | null;
  created_at: string | null;
}

export interface BaselineCase {
  id: number;
  case_id: number | null;
  key: string;
  title: string;
  section: string | null;
  priority: string | null;
  type: string | null;
}

export interface BaselineDetail extends Baseline {
  cases: BaselineCase[];
}

/** One case on either side of a comparison. */
export interface CompareCase {
  key: string;
  title?: string | null;
  case_id?: number | null;
  section?: string | null;
}

export interface BaselineCompare {
  added: (CompareCase | string)[];
  removed: (CompareCase | string)[];
  changed: { key: string; title: string | null; fields: { field: string; before: unknown; after: unknown }[] }[];
  /** A count, or the unchanged cases. */
  same: number | unknown[];
  total_added?: number;
  total_removed?: number;
  total_changed?: number;
  offset?: number;
  limit?: number;
}

/* ---- Audit log (§15.14 G12) ---- */

export interface AuditChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface AuditRow {
  id: number;
  at: string | null;
  actor_id: number | null;
  actor_name: string | null;
  action: string;
  entity: string | null;
  entity_id: number | string | null;
  target: string | null;
  business_project_id: number | null;
  changes: AuditChange[] | null;
  details: unknown;
  request_id: string | null;
}

/** A facet value: a bare key, or {key|value|id, label|name, count}. */
export type AuditFacet = string | number | { key?: string; value?: string; id?: number | string; label?: string; name?: string; count?: number };

export interface AuditFacets {
  actions: AuditFacet[];
  entities: AuditFacet[];
  actors: AuditFacet[];
}

/** A facet as [value sent back, label shown]. */
export function facetOption(f: AuditFacet): [string, string] {
  if (typeof f === "string" || typeof f === "number") return [String(f), String(f)];
  const value = String(f.key ?? f.value ?? f.id ?? f.name ?? "");
  const label = `${f.label ?? f.name ?? value}${f.count != null ? ` (${f.count.toLocaleString("vi-VN")})` : ""}`;
  return [value, label];
}

export const AUDIT_ACTION_LABEL: Record<string, string> = {
  create: "Tạo",
  insert: "Tạo",
  created: "Tạo",
  update: "Sửa",
  updated: "Sửa",
  delete: "Xoá",
  deleted: "Xoá",
};

export const AUDIT_ENTITY_LABEL: Record<string, string> = {
  suite: "Bộ testcase",
  test_suites: "Bộ testcase",
  section: "Thư mục",
  test_sections: "Thư mục",
  case: "Testcase",
  test_cases: "Testcase",
  shared_steps: "Bước dùng chung",
  test_shared_steps: "Bước dùng chung",
  run: "Test run",
  test_runs: "Test run",
  plan: "Test plan",
  test_plans: "Test plan",
  milestone: "Milestone",
  test_milestones: "Milestone",
  config: "Cấu hình",
  test_configs: "Cấu hình",
  field: "Trường tuỳ biến",
  test_fields: "Trường tuỳ biến",
  status: "Trạng thái kết quả",
  test_statuses: "Trạng thái kết quả",
  project_settings: "Cài đặt dự án",
  test_project_settings: "Cài đặt dự án",
  baseline: "Baseline",
  test_baselines: "Baseline",
  permission_set: "Bộ quyền",
  permission_sets: "Bộ quyền",
  permission_item: "Mục quyền",
  permission_grant: "Gán bộ quyền",
  api_token: "Token API",
  api_tokens: "Token API",
};

/** A stored value as one line of text for a diff cell ("—" when empty). */
export function valueText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v, null, 1);
  } catch {
    return String(v);
  }
}
