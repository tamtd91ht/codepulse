"use client";

import Link from "next/link";
import { type ReactNode, useMemo, useState } from "react";
import { Line, LineChart, YAxis } from "recharts";

import { Icon, type IconName, IconButton } from "@/components/icons";
import { SEVERITY_LABEL, delta, initials, num } from "@/lib/format";
import type { Num, Severity } from "@/lib/types";

/* ---------- Page & card chrome ---------- */

export function PageHeader({
  title,
  eyebrow,
  subtitle,
  avatar,
  actions,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  avatar?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div className="titles">
        {avatar}
        <div style={{ minWidth: 0 }}>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h1>{title}</h1>
          {subtitle && <div className="sub">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  flush,
  className,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`card${flush ? " flush" : ""}${className ? ` ${className}` : ""}`}>
      {(title || actions) && (
        <div className="card-head">
          <div style={{ minWidth: 0 }}>
            {title && <h2>{title}</h2>}
            {subtitle && <div className="sub">{subtitle}</div>}
          </div>
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/* ---------- Numbers ---------- */

export function Kpi({
  label,
  value,
  change,
  higherIsBetter = true,
  digits = 1,
  unit,
  hint,
}: {
  label: string;
  value: Num | number | undefined;
  change?: number | null;
  higherIsBetter?: boolean;
  digits?: number;
  unit?: string;
  hint?: string;
}) {
  const has = change !== null && change !== undefined && change !== 0;
  const good = has && change > 0 === higherIsBetter;
  const bad = has && !good;
  const trend: IconName = !has ? "minus" : change > 0 ? "trendUp" : "trendDown";
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">
        {num(value ?? null, digits)}
        {unit && <span className="unit">{unit}</span>}
      </div>
      <div className={`delta ${good ? "up" : bad ? "down" : ""}`}>
        {change === null || change === undefined ? (
          <span className="muted">Chưa có kỳ trước</span>
        ) : (
          <>
            <span className="chip">
              <Icon name={trend} size={12} />
              {change === 0 ? "0" : delta(change, digits)}
              {has && <span className="visually-hidden">{good ? ", tốt hơn" : ", kém hơn"}</span>}
            </span>
            <span>so với kỳ trước</span>
          </>
        )}
      </div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`sev ${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}

export function Pill({
  tone,
  icon,
  children,
}: {
  tone?: "good" | "bad" | "warn" | "info" | "primary";
  icon?: IconName;
  children: ReactNode;
}) {
  return (
    <span className={`pill${tone ? ` ${tone}` : ""}`}>
      {icon && <Icon name={icon} size={12} />}
      {children}
    </span>
  );
}

export function Avatar({ name, email, size }: { name: string | null; email?: string | null; size?: "sm" | "lg" }) {
  return (
    <span className={`avatar${size ? ` ${size}` : ""}`} aria-hidden="true">
      {initials(name, email ?? null)}
    </span>
  );
}

export function Sparkline({ values }: { values: Num[] }) {
  const data = values.map((v, i) => ({ i, v: v === null ? null : Number(v) }));
  const points = data.filter((d) => d.v !== null);
  if (points.length === 0) return <span className="faint">—</span>;
  const first = points[0].v ?? 0;
  const last = points[points.length - 1].v ?? 0;
  const stroke = points.length < 2 || last === first ? "var(--muted)" : last > first ? "var(--good)" : "var(--bad)";
  const label = `${points.length} tuần có dữ liệu, gần nhất ${num(last, 1)}`;
  return (
    <div style={{ width: 96, height: 28 }} role="img" aria-label={label} title={label}>
      <LineChart width={96} height={28} data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <YAxis hide domain={[0, 100]} />
        <Line dataKey="v" stroke={stroke} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
      </LineChart>
    </div>
  );
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  // The API builds these from an admin-validated base URL; keep the http(s) boundary explicit here too.
  if (!/^https?:\/\//i.test(href)) return <>{children}</>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="ext-link">
      {children}
      <Icon name="external" size={12} />
    </a>
  );
}

/* ---------- Feedback & states ---------- */

export function Alert({
  tone = "info",
  children,
  onClose,
}: {
  tone?: "success" | "error" | "warning" | "info";
  children: ReactNode;
  onClose?: () => void;
}) {
  const icon: IconName =
    tone === "success" ? "checkCircle" : tone === "error" ? "alertCircle" : tone === "warning" ? "alert" : "info";
  return (
    <div className={`alert ${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon name={icon} size={16} />
      <div className="body">{children}</div>
      {onClose && <IconButton icon="close" label="Đóng" onClick={onClose} size={14} />}
    </div>
  );
}

/** An action's outcome shown inline: errors stand out, everything else is a quiet success note. */
export function Message({
  text,
  tone,
  onClose,
}: {
  text: string | null;
  tone?: "success" | "error" | "warning" | "info";
  onClose?: () => void;
}) {
  if (!text) return null;
  const failed = /^(lỗi|error|không thể|chưa đưa được)/i.test(text);
  const partial = /\b(lỗi|chưa đưa được)\b/i.test(text);
  return (
    <Alert tone={tone ?? (failed ? "error" : partial ? "warning" : "success")} onClose={onClose}>
      {text}
    </Alert>
  );
}

export function Skeleton({ width, height = 14, className }: { width?: number | string; height?: number; className?: string }) {
  return <span className={`skeleton${className ? ` ${className}` : ""}`} style={{ width: width ?? "100%", height }} />;
}

export function Loading({ error, children, rows = 3 }: { error?: Error; children?: ReactNode; rows?: number }) {
  if (error) return <Alert tone="error">Lỗi: {error.message}</Alert>;
  if (children)
    return (
      <p className="loading-line" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        {children}
      </p>
    );
  return (
    <div className="skeleton-rows" role="status" aria-live="polite" aria-label="Đang tải">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} width={`${100 - (i % 3) * 18}%`} />
      ))}
    </div>
  );
}

export function EmptyState({
  icon = "inbox",
  tone,
  title,
  children,
}: {
  icon?: IconName;
  tone?: "good";
  title: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className={`empty-icon${tone ? ` ${tone}` : ""}`}>
        <Icon name={icon} size={20} />
      </span>
      <strong>{title}</strong>
      {children && <span className="small">{children}</span>}
    </div>
  );
}

/* ---------- Controls ---------- */

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: ReactNode }[];
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? "active" : undefined}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Previous / label / next, e.g. for stepping through weeks. */
export function Pager({
  label,
  onPrev,
  onNext,
  nextDisabled,
  prevDisabled,
}: {
  label: ReactNode;
  onPrev: () => void;
  onNext: () => void;
  nextDisabled?: boolean;
  prevDisabled?: boolean;
}) {
  return (
    <div className="pager">
      <IconButton icon="chevronLeft" label="Kỳ trước" onClick={onPrev} disabled={prevDisabled} />
      <span className="current">{label}</span>
      <IconButton icon="chevronRight" label="Kỳ sau" onClick={onNext} disabled={nextDisabled} />
    </div>
  );
}

/* ---------- Sorting for tables ---------- */

export function useSort<T>(rows: T[], initial: { key: string; dir: "asc" | "desc" }, get: (row: T, key: string) => number | string | null) {
  const [sort, setSort] = useState(initial);
  const sorted = useMemo(() => {
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = get(a, sort.key);
      const y = get(b, sort.key);
      if (x === null && y === null) return 0;
      if (x === null) return 1; // nulls last regardless of direction
      if (y === null) return -1;
      if (typeof x === "string" || typeof y === "string") return dir * String(x).localeCompare(String(y), "vi");
      return dir * (x - y);
    });
  }, [rows, sort, get]);
  const toggle = (key: string) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  return { sorted, sort, toggle };
}

export function SortTh({
  id,
  sort,
  onSort,
  num: numeric,
  children,
}: {
  id: string;
  sort: { key: string; dir: "asc" | "desc" };
  onSort: (key: string) => void;
  num?: boolean;
  children: ReactNode;
}) {
  const active = sort.key === id;
  return (
    <th className={`sortable${numeric ? " num" : ""}`} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="sort-btn" onClick={() => onSort(id)}>
        {children}
        <span className="sort-ind" aria-hidden="true">
          {active ? (sort.dir === "asc" ? "↑" : "↓") : ""}
        </span>
      </button>
    </th>
  );
}

/* ---------- Charts ---------- */

type TipItem = { name?: string | number; value?: number | string | null; color?: string; dataKey?: string | number };

/** Recharts tooltip drawn with the theme tokens. */
export function ChartTip({
  active,
  payload,
  label,
  digits = 1,
}: {
  active?: boolean;
  payload?: ReadonlyArray<TipItem>;
  label?: string | number;
  digits?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tip">
      {label !== undefined && <div className="tip-title">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="tip-row">
          <span>
            <span className="dot" style={{ background: p.color }} />
            {p.name}
          </span>
          <strong>{typeof p.value === "number" ? num(p.value, digits) : (p.value ?? "—")}</strong>
        </div>
      ))}
    </div>
  );
}

/** Link to a person's page with avatar and name. */
export function PersonLink({ id, name, email }: { id: number; name: string | null; email?: string | null }) {
  return (
    <div className="cell-main">
      <Avatar name={name} email={email} size="sm" />
      <div className="who">
        <Link href={`/members/${id}`}>{name ?? email ?? `#${id}`}</Link>
      </div>
    </div>
  );
}
