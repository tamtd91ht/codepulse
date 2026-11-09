"use client";

import { useCallback, useEffect } from "react";

import { Alert, Pill } from "@/components/ui";
import { CONFIDENCE_LABEL, type Confidence, RATING_LABEL, RATING_TONE, type Rating, lastMonth } from "@/lib/dpi";
import { scopeMonth, setTimeScope, useMounted, useTimeScope } from "@/lib/timeScope";

/**
 * The month (YYYY-MM), mirrored in the URL (?month=). It follows the dashboard-wide period
 * (lib/timeScope): the month of its last day; a month in the URL on arrival or picked here
 * becomes the shared period. Last month when none is chosen.
 */
export function useMonthScope(fallback: () => string, accept: (m: string) => boolean = () => true): [string | null, (m: string) => void] {
  const shared = useTimeScope();
  const mounted = useMounted();
  useEffect(() => {
    try {
      const m = new URLSearchParams(window.location.search).get("month");
      if (m && /^\d{4}-\d{2}$/.test(m) && accept(m)) setTimeScope({ kind: "month", start: `${m}-01` });
    } catch {
      /* no URL: keep the shared period */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- read once on arrival
  }, []);
  const fromShared = shared ? scopeMonth(shared) : null;
  const month = !mounted ? null : fromShared && accept(fromShared) ? fromShared : fallback();
  useEffect(() => {
    if (!month) return;
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.get("month") === month) return;
      url.searchParams.set("month", month);
      window.history.replaceState(null, "", url);
    } catch {
      /* not kept in the URL */
    }
  }, [month]);
  const go = useCallback((m: string) => setTimeScope({ kind: "month", start: `${m}-01` }), []);
  return [month, go];
}

/** The DPI month: last month by default. */
export function useMonth(): [string | null, (m: string) => void] {
  return useMonthScope(lastMonth);
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
