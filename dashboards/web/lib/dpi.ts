/* DPI: monthly developer performance (docs/specs/25). API shapes and labels. */

export type Rating = "excellent" | "good" | "meets" | "improve" | "below";
export type Confidence = "high" | "medium" | "low";
export type DpiStatus = "draft" | "self_review" | "lead_review" | "calibration" | "finalized" | "locked";
export type Group = "A" | "B" | "C" | "D" | "E";

export const GROUPS: Group[] = ["A", "B", "C", "D", "E"];
export const GROUP_LABEL: Record<Group, string> = {
  A: "Work Output",
  B: "Code Quality",
  C: "Delivery",
  D: "Collaboration",
  E: "Ownership & Growth",
};
export const RATING_LABEL: Record<Rating, string> = {
  excellent: "Xuất sắc",
  good: "Tốt",
  meets: "Đạt",
  improve: "Cần cải thiện",
  below: "Không đạt kỳ vọng",
};
export const RATING_TONE: Record<Rating, "good" | "info" | "warn" | "bad"> = {
  excellent: "good",
  good: "good",
  meets: "info",
  improve: "warn",
  below: "bad",
};
export const RATINGS: Rating[] = ["excellent", "good", "meets", "improve", "below"];
export const CONFIDENCE_LABEL: Record<Confidence, string> = { high: "Tin cậy cao", medium: "Tin cậy vừa", low: "Ít dữ liệu" };
export const STATUS_LABEL: Record<DpiStatus, string> = {
  draft: "Nháp",
  self_review: "Tự khai",
  lead_review: "Lead chấm",
  calibration: "Hiệu chỉnh",
  finalized: "Đã chốt",
  locked: "Đã khoá",
};
export const STATUS_STEPS: DpiStatus[] = ["self_review", "lead_review", "calibration", "finalized", "locked"];

/** What each metric means, on the scorecard (docs/specs/25 §2, §3). */
export const METRIC_HELP: Record<string, string> = {
  A1: "Điểm độ phức tạp (complexity::XS..XL = 1/2/3/5/8) của issue đóng trong tháng được giao cho bạn, so với target của level (trừ ngày nghỉ).",
  A2: "Số đóng góp đã xác nhận: issue đóng có type::*, cộng mục tự khai được lead duyệt.",
  A3: "Lead chấm: tác động nghiệp vụ / kỹ thuật của việc đã làm.",
  B1: "50% điểm review (bộ tiêu chí chính của dự án), 25% tỉ lệ pipeline thành công, 25% tỉ lệ review không có critical/high.",
  B2: "Lỗi production đã được lead xác nhận RCA: P1 trừ 4, P2 trừ 2, P3 trừ 1 (theo tỉ lệ trách nhiệm), trên 8.",
  B3: "Coverage của pipeline trên MR (mục tiêu 80%) và mật độ finding loại testing.",
  B4: "Rework 21 ngày: phần dòng bạn thêm rồi tự xoá lại trong cùng file trong 21 ngày (≤ 10% là tốt nhất, ≥ 40% là 0). Commit refactor không tính.",
  B5: "Lead chấm thiết kế code trên các MR mẫu của tháng.",
  C1: "Thời gian của chính bạn trên mỗi MR (viết code + xử lý góp ý), so với trung vị của repo. Không tính thời gian chờ người khác.",
  C2: "Tỉ lệ issue có hạn trong tháng được đóng đúng hạn (issue blocked không tính).",
  C3: "Tỉ lệ thay đổi đạt quy trình: MR có link issue, checklist đủ, pipeline xanh, có review trước merge. Commit đẩy thẳng là một thay đổi không đạt.",
  D1: "Comment review có giá trị (correctness, security, design, testability, actionable) trên MR người khác, được tác giả sửa theo hoặc lead xác nhận; tối đa 10.",
  D2: "Review MR ở module bạn không làm 90 ngày qua, cộng chia sẻ (design, mentoring) được duyệt.",
  D3: "Lead chấm tinh thần làm việc nhóm, có tham khảo phản hồi đồng nghiệp.",
  E1: "Lead chấm: chủ động nhận và theo việc tới cùng.",
  E2: "Lead chấm: học hỏi, tiến bộ.",
  E3: "Lead chấm: sáng kiến, kèm cặp người khác.",
};

export interface Evidence {
  kind: string;
  id?: number;
  ref?: string;
  label?: string | null;
  url?: string | null;
  ok?: boolean;
  share?: number;
  added?: number;
  reworked?: number;
}

export interface MetricView {
  value: number | null;
  confidence: Confidence;
  detail: Record<string, unknown>;
  evidence: Evidence[];
  group: Group;
  max: number;
  source: "auto" | "manager";
  label: string;
  points: number | null;
  overridden?: boolean;
}

export interface Card {
  rubric_version: string;
  profile: string;
  profile_label: string;
  profile_assigned?: boolean;
  metrics: Record<string, MetricView>;
  groups: Record<Group, { label: string; weight: number; score: number | null; ratio: number | null; metrics: string[] }>;
  total: number | null;
  coverage: number;
  confidence: Confidence;
  provisional: boolean;
  adjustment: number;
  rating: Rating | null;
  split: { auto: number; auto_max: number; manager: number; manager_max: number };
  flags?: { split_mrs: { issue_id: number; day: string; mrs: string[] }[] };
}

export interface ScorecardView {
  user: { id: number; name: string | null; email: string | null };
  month: string;
  shadow: boolean;
  review: {
    id: number;
    status: DpiStatus;
    profile: string;
    leave_days: number;
    adjustment: number;
    adjustment_reason: string | null;
    action_plan: string | null;
    one_on_one_at: string | null;
    submitted_at: string | null;
    assessed_at: string | null;
    finalized_at: string | null;
    locked_at: string | null;
    deadlines: Partial<Record<DpiStatus, string>>;
    overdue: boolean;
  };
  card: Card;
  trend: { month: string; total: number | null; rating: Rating | null; confidence: Confidence | null; kind: string | null; groups: Partial<Record<Group, number | null>> }[];
  rolling: { value: number | null; months: number; partial: boolean; rating?: Rating | null };
  assessments: { metric: string; score: number; comment: string; evidence: string[]; by: string | null; updated_at: string }[];
  disputes: { id: number; metric: string; reason: string; decision: "accepted" | "rejected" | null; answer: string | null; decided_at: string | null; created_at: string }[];
  peer: { count: number; average: number | null; items: { score: number; comment: string | null; from?: string | null }[] };
  b5_suggestions: Evidence[];
  manager_metrics: { key: string; label: string; max: number }[];
  can: { self: boolean; dispute: boolean; assess: boolean; calibrate: boolean; finalize: boolean; lock: boolean; decide: boolean };
}

export interface OverviewRow {
  user: { id: number; name: string | null };
  profile: string;
  total: number | null;
  rating: Rating | null;
  confidence: Confidence;
  provisional: boolean;
  status: DpiStatus | null;
  groups: Partial<Record<Group, number | null>>;
  split: Card["split"] | null;
  flags: Card["flags"] | null;
}

export interface Aggregates {
  people: number;
  avg_total: number | null;
  ratings: Record<Rating, number>;
  unrated: number;
  avg_delivery_predictability: number | null;
  avg_pipeline_pass_rate: number | null;
  median_author_hours: number | null;
  avg_coverage: number | null;
}

export interface Overview {
  month: string;
  shadow: boolean;
  summary: Aggregates;
  rows: OverviewRow[];
}

/** "2026-10" of the month before today (the month the cycle is about). */
export function lastMonth(today = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(month: string): string {
  const [y, m] = month.slice(0, 7).split("-");
  return `Tháng ${Number(m)}/${y}`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}
