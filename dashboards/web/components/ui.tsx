"use client";

import type { ReactNode } from "react";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";

import { SEVERITY_LABEL, delta, num } from "@/lib/format";
import type { Num, Severity } from "@/lib/types";

export function Kpi({
  label,
  value,
  change,
  higherIsBetter = true,
  digits = 1,
}: {
  label: string;
  value: Num | number | undefined;
  change?: number | null;
  higherIsBetter?: boolean;
  digits?: number;
}) {
  const good = change !== null && change !== undefined && change !== 0 && change > 0 === higherIsBetter;
  const bad = change !== null && change !== undefined && change !== 0 && !good;
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{num(value ?? null, digits)}</div>
      <div className={`delta ${good ? "up" : bad ? "down" : "muted"}`}>
        {change === null || change === undefined ? "chưa có kỳ trước" : `${delta(change, digits)} so với kỳ trước`}
      </div>
    </div>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`sev ${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}

export function Sparkline({ values }: { values: Num[] }) {
  const data = values.map((v, i) => ({ i, v: v === null ? null : Number(v) }));
  if (data.every((d) => d.v === null)) return <span className="muted">—</span>;
  return (
    <div style={{ width: 96, height: 28 }}>
      <ResponsiveContainer>
        <LineChart data={data}>
          <YAxis hide domain={[0, 100]} />
          <Line dataKey="v" stroke="var(--accent)" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Loading({ error, children }: { error?: Error; children?: ReactNode }) {
  if (error) return <p className="error">Lỗi: {error.message}</p>;
  return <p className="muted">{children ?? "Đang tải…"}</p>;
}
