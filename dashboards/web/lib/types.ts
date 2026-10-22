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

export interface Finding {
  id: number;
  review_id: number | null;
  severity: Severity;
  category: string;
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

export interface Project {
  id: number;
  external_id: number;
  path_with_namespace: string;
  review_enabled: boolean | null;
  post_mr_comment: boolean | null;
  allow_external_llm: boolean | null;
  last_synced_at: string | null;
}

export interface Account {
  id: number;
  kind: string;
  external_key: string;
  display_name: string | null;
  user_id: number | null;
}

export interface LlmCosts {
  budget_usd: number;
  months: { month: string; cost_usd: number; reviews: number }[];
}
