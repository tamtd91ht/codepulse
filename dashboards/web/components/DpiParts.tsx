"use client";

import { useEffect, useState } from "react";

import { Alert, Pill } from "@/components/ui";
import { CONFIDENCE_LABEL, type Confidence, RATING_LABEL, RATING_TONE, type Rating, lastMonth } from "@/lib/dpi";

/** The month in the URL (?month=YYYY-MM), last month by default; kept on change. */
export function useMonth(): [string | null, (m: string) => void] {
  const [month, setMonth] = useState<string | null>(null);
  useEffect(() => {
    let wanted = lastMonth();
    try {
      const m = new URLSearchParams(window.location.search).get("month");
      if (m && /^\d{4}-\d{2}$/.test(m)) wanted = m;
    } catch {
      /* default month */
    }
    setMonth(wanted);
  }, []);
  const go = (m: string) => {
    setMonth(m);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("month", m);
      window.history.replaceState(null, "", url);
    } catch {
      /* not kept in the URL */
    }
  };
  return [month, go];
}

export function MonthPicker({ value, onChange }: { value: string; onChange: (m: string) => void }) {
  return (
    <label className="dpi-month">
      <span className="muted small">Tháng</span>
      <input
        type="month"
        value={value}
        max={lastMonth(new Date(new Date().getFullYear(), new Date().getMonth() + 2, 1))}
        onChange={(e) => e.target.value && onChange(e.target.value)}
        aria-label="Tháng đánh giá"
      />
    </label>
  );
}

export function RatingPill({ rating }: { rating: Rating | null | undefined }) {
  if (!rating) return <Pill title="Độ tin cậy thấp: không xếp loại">Chưa đủ dữ liệu</Pill>;
  return <Pill tone={RATING_TONE[rating]}>{RATING_LABEL[rating]}</Pill>;
}

export function ConfidencePill({ value }: { value: Confidence | null | undefined }) {
  if (!value) return null;
  const tone = value === "high" ? "good" : value === "medium" ? "info" : "warn";
  return (
    <Pill tone={tone} title="Độ phủ và cỡ mẫu của dữ liệu">
      {CONFIDENCE_LABEL[value]}
    </Pill>
  );
}

export function ShadowBanner({ on }: { on: boolean }) {
  if (!on) return null;
  return (
    <Alert tone="warning">
      <strong>Chạy thử</strong> — điểm DPI đang ở giai đoạn shadow, <strong>không dùng cho lương thưởng</strong>. Số liệu dùng để hiệu
      chỉnh công thức và trao đổi 1:1.
    </Alert>
  );
}

/** A 0–100 bar with the score printed; tone by rating band. */
export function ScoreBar({ value, max = 100, label }: { value: number | null; max?: number; label: string }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, (100 * value) / max));
  const tone = value === null ? "" : pct >= 80 ? " good" : pct >= 60 ? " warn" : " bad";
  return (
    <span className="dpi-bar" role="img" aria-label={`${label}: ${value === null ? "không có dữ liệu" : value.toFixed(1)}`}>
      <span className={`dpi-bar-fill${tone}`} style={{ width: `${pct}%` }} />
    </span>
  );
}
