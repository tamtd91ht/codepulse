/* Phản ánh & Đề xuất (docs/specs/32): shapes of app/api/routers/feedback.py and small helpers. */

export type FeedbackKind = "bug" | "feature" | "improvement" | "question";
export type FeedbackStatus = "new" | "accepted" | "planned" | "done" | "rejected";
export type FeedbackPriority = "p1" | "p2" | "p3";
export type AnalysisStatus = "queued" | "analyzing" | "ready" | "failed";

export interface FeedbackPerson {
  id: number;
  name: string;
  avatar_url: string | null;
  teams: string[];
}

export interface FeedbackOption {
  key: string;
  label: string;
}

export interface FeedbackMeta {
  areas: FeedbackOption[];
  kinds: FeedbackOption[];
  statuses: FeedbackOption[];
  priorities: FeedbackOption[];
  suggested_area: string;
  can_manage: boolean;
  per_day: number;
  title_max: number;
  body_max: number;
}

export interface FeedbackRow {
  id: number;
  title: string;
  kind: FeedbackKind;
  area: string;
  feature: string;
  priority: FeedbackPriority | null;
  status: FeedbackStatus;
  analysis_status: AnalysisStatus;
  summary: string | null;
  duplicates: number[];
  author: FeedbackPerson | null;
  created_at: string;
  exported: number;
  path: string;
  /** Queued or running and not given up as stale (the lists poll on it). */
  analysing: boolean;
}

export interface FeedbackResult {
  problem?: string;
  proposal?: string;
  acceptance?: string[];
  tags?: string[];
  duplicates?: number[];
  questions?: string[];
}

export interface FeedbackDetail extends FeedbackRow {
  raw_title: string;
  body: string;
  page_url: string | null;
  author_kind: FeedbackKind;
  author_area: string;
  analysis_md: string | null;
  result: FeedbackResult | null;
  triage_note: string | null;
  error: string | null;

  can_edit: boolean;
  can_manage: boolean;
  triaged_by: FeedbackPerson | null;
  triaged_at: string | null;
  exported_at: string | null;
}

export interface FeedbackTreeFeature {
  name: string;
  count: number;
  open: number;
}

export interface FeedbackTreeArea {
  key: string;
  label: string;
  count: number;
  open: number;
  features: FeedbackTreeFeature[];
}

export interface FeedbackBoard {
  items: FeedbackRow[];
  tree: FeedbackTreeArea[];
  truncated: boolean;
}

export interface FeedbackExportRow {
  id: number;
  title: string;
  note: string | null;
  items: number;
  item_ids: number[];
  created_by: FeedbackPerson | null;
  created_at: string;
}

export const KIND_LABEL: Record<FeedbackKind, string> = {
  bug: "Lỗi",
  feature: "Đề xuất tính năng",
  improvement: "Cải tiến",
  question: "Câu hỏi",
};

export const KIND_ICON: Record<FeedbackKind, "alertCircle" | "sparkle" | "trendUp" | "info"> = {
  bug: "alertCircle",
  feature: "sparkle",
  improvement: "trendUp",
  question: "info",
};

export const STATUS_LABEL: Record<FeedbackStatus, string> = {
  new: "Mới",
  accepted: "Đã nhận",
  planned: "Đã lên kế hoạch",
  done: "Đã xong",
  rejected: "Từ chối",
};

export const STATUS_TONE: Record<FeedbackStatus, "info" | "primary" | "warn" | "good" | "bad" | undefined> = {
  new: undefined,
  accepted: "info",
  planned: "primary",
  done: "good",
  rejected: "bad",
};

export const PRIORITY_TONE: Record<FeedbackPriority, "bad" | "warn" | undefined> = {
  p1: "bad",
  p2: "warn",
  p3: undefined,
};

export const OPEN_STATUSES: FeedbackStatus[] = ["new", "accepted", "planned"];

export const isAnalysing = (row: { analysing: boolean }) => row.analysing;

/** A tree node: an area, or one feature of it. */
export type TreeNode = { area: string; feature?: string } | null;

export function inNode(row: FeedbackRow, node: TreeNode): boolean {
  if (!node) return true;
  return row.area === node.area && (node.feature === undefined || row.feature === node.feature);
}

export const exportUrl = (id: number) => `/api/v1/feedback/exports/${id}/download`;
export const itemMdUrl = (id: number) => `/api/v1/feedback/${id}/markdown`;

/** Start the download of a same-origin file (the session cookie goes with it). */
export function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
