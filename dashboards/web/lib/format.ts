import type { Num, Severity } from "./types";

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

export const CATEGORY_LABEL: Record<string, string> = {
  security: "Bảo mật",
  bug: "Lỗi logic",
  performance: "Hiệu năng",
  maintainability: "Bảo trì",
  testing: "Kiểm thử",
  style: "Convention",
  docs: "Tài liệu",
};

export function num(value: Num | number | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

export function percent(value: Num): string {
  return value === null ? "—" : `${num(value, 0)}%`;
}

/** A 0..1 share shown as a percentage. */
export function ratio(value: Num): string {
  return value === null ? "—" : `${(Number(value) * 100).toFixed(0)}%`;
}

export function delta(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export function date(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("vi-VN") : "—";
}

/** Monday of the current ISO week, as YYYY-MM-DD. */
export function thisWeek(today = new Date()): string {
  const d = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}

export function shiftWeeks(week: string, by: number): string {
  const d = new Date(`${week}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7 * by);
  return d.toISOString().slice(0, 10);
}

export function weekLabel(week: string): string {
  const d = new Date(`${week}T00:00:00Z`);
  return `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}
