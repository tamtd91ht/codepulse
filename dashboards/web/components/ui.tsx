"use client";

import Link from "next/link";
import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Line, LineChart, YAxis } from "recharts";

import { Icon, type IconName, IconButton } from "@/components/icons";
import { SEVERITY_LABEL, delta, initials, num } from "@/lib/format";
import type { Num, Severity } from "@/lib/types";

/* ---------- Page & card chrome ---------- */

/* Every card with a title folds with a chevron; the page header folds or opens them all at
   once. A card remembers its own state per browser (localStorage), so a long page stays the way
   the reader left it. */
const CARDS_EVENT = "cp:cards";
const CARD_STORE = "cp:card:";

function readFold(key: string | null): boolean | null {
  if (!key) return null;
  try {
    const v = window.localStorage.getItem(CARD_STORE + key);
    return v === "1" ? true : v === "0" ? false : null;
  } catch {
    return null;
  }
}

function writeFold(key: string | null, collapsed: boolean) {
  if (!key) return;
  try {
    window.localStorage.setItem(CARD_STORE + key, collapsed ? "1" : "0");
  } catch {
    /* private mode or blocked storage: the state just does not persist */
  }
}

function foldAll(collapsed: boolean) {
  window.dispatchEvent(new CustomEvent(CARDS_EVENT, { detail: { collapsed } }));
}

export function PageHeader({
  title,
  eyebrow,
  subtitle,
  avatar,
  actions,
  foldable = true,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  avatar?: ReactNode;
  actions?: ReactNode;
  /** Show the fold-all / open-all chevrons (off on pages without cards). */
  foldable?: boolean;
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
      {(actions || foldable) && (
        <div className="actions">
          {actions}
          {foldable && (
            <span className="fold-all">
              <IconButton icon="chevronsUp" label="Thu gọn tất cả" onClick={() => foldAll(true)} />
              <IconButton icon="chevronsDown" label="Mở rộng tất cả" onClick={() => foldAll(false)} />
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  flush,
  className,
  id,
  collapsible,
  defaultCollapsed = false,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  className?: string;
  /** Key the fold state is remembered under; a string title is used when absent. */
  id?: string;
  /** Default: any card with a title folds. */
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  children: ReactNode;
}) {
  const key = id ?? (typeof title === "string" ? title : null);
  const canFold = collapsible ?? Boolean(title);
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  useEffect(() => {
    if (!canFold) return;
    const stored = readFold(key);
    if (stored !== null) setCollapsed(stored);
  }, [key, canFold]);
  useEffect(() => {
    if (!canFold) return;
    const onAll = (e: Event) => {
      const next = Boolean((e as CustomEvent<{ collapsed: boolean }>).detail?.collapsed);
      setCollapsed(next);
      writeFold(key, next);
    };
    window.addEventListener(CARDS_EVENT, onAll);
    return () => window.removeEventListener(CARDS_EVENT, onAll);
  }, [key, canFold]);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    writeFold(key, next);
  };
  const folded = canFold && collapsed;
  return (
    <section
      className={`card${flush ? " flush" : ""}${folded ? " collapsed" : ""}${className ? ` ${className}` : ""}`}
    >
      {(title || actions || canFold) && (
        <div className="card-head">
          <div
            className={canFold ? "card-title fold" : "card-title"}
            style={{ minWidth: 0 }}
            onClick={canFold ? toggle : undefined}
          >
            {title && <h2>{title}</h2>}
            {subtitle && !folded && <div className="sub">{subtitle}</div>}
          </div>
          {(actions || canFold) && (
            <div className="actions" onClick={(e) => e.stopPropagation()}>
              {!folded && actions}
              {canFold && (
                <IconButton
                  icon={folded ? "chevronDown" : "chevronUp"}
                  label={folded ? "Mở rộng" : "Thu gọn"}
                  aria-expanded={!folded}
                  onClick={toggle}
                />
              )}
            </div>
          )}
        </div>
      )}
      {!folded && children}
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
  href,
}: {
  label: string;
  value: Num | number | undefined;
  change?: number | null;
  higherIsBetter?: boolean;
  digits?: number;
  unit?: string;
  hint?: string;
  /** Where the number comes from: the tile links there. */
  href?: string;
}) {
  const has = change !== null && change !== undefined && change !== 0;
  const good = has && change > 0 === higherIsBetter;
  const bad = has && !good;
  const trend: IconName = !has ? "minus" : change > 0 ? "trendUp" : "trendDown";
  const body = (
    <>
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
    </>
  );
  return href ? (
    <Link href={href} className="card kpi link">
      {body}
      <span className="visually-hidden">, xem danh sách</span>
    </Link>
  ) : (
    <div className="card kpi">{body}</div>
  );
}

/** A count that opens the findings it counts (nothing to open at 0, or without `href`). */
export function CountLink({ n, href }: { n: number; href: string | null }) {
  return n && href ? <Link href={href}>{n}</Link> : <>{n}</>;
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

/* ---------- Side drawer ---------- */

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A panel sliding in from the right for "details of one thing" (a dự án, a bot). Escape (inside
 * the panel) and a click on the backdrop close it; Tab stays inside; the page behind stops
 * scrolling; focus goes to the close button on open and back to the opener on close.
 */
export function Drawer({
  title,
  onClose,
  width = 720,
  actions,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  /** px, or "wide": most of the screen (tables with many columns), a strip of page left visible. */
  width?: number | "wide";
  /** Buttons shown next to the title, always in view (not scrolled away with the body). */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const aside = useRef<HTMLElement>(null);
  const pressedBackdrop = useRef(false);
  // The latest onClose without re-running the mount effect (callers pass inline arrows).
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      opener?.focus();
    };
  }, []);
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close.current();
      return;
    }
    if (e.key !== "Tab" || !aside.current) return;
    const focusable = [...aside.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  return (
    <div
      className="side-drawer-backdrop"
      onMouseDown={(e) => {
        pressedBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        // Only a press that started on the backdrop closes: a text selection that ends there must not.
        if (pressedBackdrop.current && e.target === e.currentTarget) close.current();
        pressedBackdrop.current = false;
      }}
    >
      <aside
        ref={aside}
        className="side-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="side-drawer-title"
        style={{ width: width === "wide" ? "min(1360px, max(calc(100vw - 64px), min(100vw, 480px)))" : `min(${width}px, 100%)` }}
        onKeyDown={onKeyDown}
      >
        <div className="side-drawer-head">
          <h2 id="side-drawer-title">{title}</h2>
          <div className="side-drawer-actions">
            {actions}
            <IconButton ref={closeButton} icon="close" label="Đóng" onClick={onClose} />
          </div>
        </div>
        <div className="side-drawer-body">{children}</div>
      </aside>
    </div>
  );
}
