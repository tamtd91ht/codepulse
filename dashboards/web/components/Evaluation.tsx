"use client";

import { type ReactNode, useEffect, useState } from "react";

import { Icon } from "@/components/icons";
import { Segmented } from "@/components/ui";
import { VERDICT_LABEL, localDay, num } from "@/lib/format";
import type { EvaluationBucket, Verdict } from "@/lib/types";

/* Shared pieces of the per-member evaluation (the Thành viên page and the member page). */

export type Preset = "7" | "30" | "90" | "custom";

export interface Range {
  preset: Preset;
  from: string;
  to: string;
}

const DAY = 86_400_000;

function daysAgo(n: number, today = new Date()): string {
  return localDay(new Date(today.getTime() - n * DAY));
}

/** The range a preset means, ending today. */
export function presetRange(preset: Exclude<Preset, "custom">): { from: string; to: string } {
  return { from: daysAgo(Number(preset) - 1), to: localDay() };
}

/** Initial range: the last 30 days. Computed after mount so "today" is the viewer's. */
export function useRange(initial: Exclude<Preset, "custom"> = "30"): [Range | null, (r: Range) => void] {
  const [range, setRange] = useState<Range | null>(null);
  useEffect(() => {
    setRange({ preset: initial, ...presetRange(initial) });
  }, [initial]);
  return [range, setRange];
}

/** The label of the range a response covers (its own from/to, not the picker's draft). */
export function rangeLabel(from: string, to: string): string {
  return `${from} → ${to}`;
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
const MAX_SPAN_DAYS = 366;

/** Why a typed range cannot be asked for yet; null when it can (the API's own rules). */
export function rangeProblem(from: string, to: string): string | null {
  if (!ISO_DAY.test(from) || !ISO_DAY.test(to)) return "Chọn đủ hai ngày.";
  const a = Date.parse(from);
  const b = Date.parse(to);
  const years = [Number(from.slice(0, 4)), Number(to.slice(0, 4))];
  if (Number.isNaN(a) || Number.isNaN(b) || years.some((y) => y < 2000 || y > 2100)) return "Ngày không hợp lệ.";
  if (a > b) return "Ngày bắt đầu phải trước ngày kết thúc.";
  if ((b - a) / DAY >= MAX_SPAN_DAYS) return `Tối đa ${MAX_SPAN_DAYS} ngày.`;
  return null;
}

/**
 * 7 / 30 / 90 days, or two dates. Typed dates stay a draft until they make a valid range, so a
 * half-typed year never reaches the API.
 */
export function RangePicker({ value, onChange }: { value: Range | null; onChange: (r: Range) => void }) {
  const [draft, setDraft] = useState<{ from: string; to: string } | null>(null);
  const from = value?.from;
  const to = value?.to;
  useEffect(() => {
    if (from !== undefined && to !== undefined) setDraft({ from, to });
  }, [from, to]);
  if (!value || !draft) return null;
  const problem = value.preset === "custom" ? rangeProblem(draft.from, draft.to) : null;
  const edit = (next: { from: string; to: string }) => {
    setDraft(next);
    if (!rangeProblem(next.from, next.to)) onChange({ preset: "custom", ...next });
  };
  return (
    <div className="range-picker">
      <Segmented<Preset>
        label="Khoảng thời gian"
        value={value.preset}
        onChange={(preset) =>
          onChange(preset === "custom" ? { ...value, preset } : { preset, ...presetRange(preset) })
        }
        options={[
          { value: "7", label: "7 ngày" },
          { value: "30", label: "30 ngày" },
          { value: "90", label: "90 ngày" },
          { value: "custom", label: "Tuỳ chọn" },
        ]}
      />
      {value.preset === "custom" && (
        <span className="range-dates">
          <input
            type="date"
            value={draft.from}
            max={draft.to}
            onChange={(e) => edit({ ...draft, from: e.target.value })}
            aria-label="Từ ngày"
            aria-invalid={!!problem}
            aria-describedby={problem ? "range-problem" : undefined}
          />
          <span className="muted" aria-hidden="true">
            →
          </span>
          <input
            type="date"
            value={draft.to}
            min={draft.from}
            onChange={(e) => edit({ ...draft, to: e.target.value })}
            aria-label="Đến ngày"
            aria-invalid={!!problem}
            aria-describedby={problem ? "range-problem" : undefined}
          />
        </span>
      )}
      {problem && (
        <span id="range-problem" className="range-problem small" role="status">
          {problem}
        </span>
      )}
    </div>
  );
}

/* ---- numbers ---- */

export function scoreTone(score: number | null): "good" | "warn" | "bad" | undefined {
  if (score === null) return undefined;
  return score >= 80 ? "good" : score >= 65 ? "warn" : "bad";
}

/** A score coloured by how good it is; "—" without one. */
export function Score({ value, strong }: { value: number | null; strong?: boolean }) {
  if (value === null) return <span className="faint">—</span>;
  const tone = scoreTone(value);
  const text = num(value, 1);
  return <span className={`score ${tone ?? ""}`}>{strong ? <strong>{text}</strong> : text}</span>;
}

export const VERDICTS: Verdict[] = ["approve", "needs_human", "fix_required", "block"];

/**
 * One bar split by verdict, with the counts in the tooltip and for screen readers. Two
 * stacked reads: the fill shows the share, the legend under it the numbers.
 */
export function VerdictMix({ verdicts, compact }: { verdicts: Record<Verdict, number>; compact?: boolean }) {
  const total = VERDICTS.reduce((n, v) => n + (verdicts[v] ?? 0), 0);
  if (!total) return <span className="faint">—</span>;
  const label = VERDICTS.filter((v) => verdicts[v])
    .map((v) => `${verdicts[v]} ${VERDICT_LABEL[v]}`)
    .join(", ");
  return (
    <div className={`mix${compact ? " compact" : ""}`} role="img" aria-label={label} title={label}>
      <div className="mix-bar">
        {VERDICTS.map((v) => (verdicts[v] ? <span key={v} className={v} style={{ flex: verdicts[v] }} /> : null))}
      </div>
      {!compact && (
        <div className="mix-legend">
          {VERDICTS.map((v) => (
            <span key={v} className={verdicts[v] ? undefined : "faint"}>
              <i className={`dot ${v}`} />
              {verdicts[v]} {VERDICT_LABEL[v].toLowerCase()}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Severity counts in one compact row; zero counts fade. */
export function SeverityRow({ b }: { b: EvaluationBucket }) {
  const items: [string, number][] = [
    ["critical", b.critical],
    ["high", b.high],
    ["medium", b.medium],
    ["low", b.low],
  ];
  const label = items.map(([s, n]) => `${n} ${s}`).join(", ");
  return (
    <div className="sev-row" role="img" aria-label={label} title={label}>
      {items.map(([s, n]) => (
        <span key={s} className={`sev-count ${s}${n ? "" : " zero"}`} aria-hidden="true">
          {n}
        </span>
      ))}
    </div>
  );
}

/** A big number with a label, for the summary strip of a card. */
export function Stat({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <div className="stat-value">{children}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

/** Where a person's flagged row points: the person page, with why they are flagged. */
export function AttentionBadge({ b }: { b: EvaluationBucket }) {
  if (!b.needs_attention) return null;
  const why = b.critical ? `${b.critical} critical` : `điểm TB ${num(b.avg_score, 1)}`;
  return (
    <span className="pill bad" title={`Cần hỗ trợ: ${why}`}>
      <Icon name="flag" size={12} />
      {why}
    </span>
  );
}
