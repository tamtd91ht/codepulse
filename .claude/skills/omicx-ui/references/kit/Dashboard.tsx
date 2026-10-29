import type { ReactNode } from "react";
import { cx } from "./cx";
import type { Status } from "./States";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 pt-2 pb-6">
      <div className="min-w-0">
        <h1 className="text-title font-bold text-primary">{title}</h1>
        {description && <p className="mt-1 text-sm text-secondary">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Page-wide filter shown as a chip; clearable in place when it has a value. */
export function FilterChip({
  label,
  value,
  onOpen,
  onClear,
}: {
  label: string;
  value?: string;
  onOpen: () => void;
  onClear?: () => void;
}) {
  return (
    <span className="inline-flex h-8 items-center rounded-medium bg-quaternary text-sm text-primary">
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup="listbox"
        className="flex h-full items-center gap-1.5 rounded-medium px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {value ? `${label}: ${value}` : label}
        <span aria-hidden className="text-secondary">⌄</span>
      </button>
      {value && onClear && (
        <button
          type="button"
          onClick={onClear}
          aria-label={`Bỏ lọc ${label}`}
          className="mr-1 flex size-5 items-center justify-center rounded-full text-secondary hover:bg-default focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          ×
        </button>
      )}
    </span>
  );
}

/** A KPI value always carries context: a comparison or a denominator. */
export function KpiTile({ label, value, context, icon }: { label: string; value: ReactNode; context: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-card border border-tertiary bg-surface p-4 shadow-card transition-colors duration-150 hover:bg-quaternary">
      <p className="flex items-center gap-2 text-primary">
        {icon}
        {label}
      </p>
      <p className="text-title font-bold text-primary">{value}</p>
      <p className="text-xs text-secondary">{context}</p>
    </div>
  );
}

export function KpiRow({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">{children}</div>;
}

export function SectionCard({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("flex flex-col gap-6 rounded-card border border-tertiary bg-surface p-6 shadow-card", className)}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-primary">{title}</h2>
          {subtitle && <p className="text-xs text-secondary">{subtitle}</p>}
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

/** Switches the dimension of one chart instead of adding more cards. */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex h-8 items-center gap-0.5 rounded-large bg-quaternary p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cx(
              "h-7 rounded-medium px-3 text-sm transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              active ? "bg-surface text-primary shadow-card" : "text-secondary hover:text-primary",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

const dot: Record<Status, string> = {
  active: "bg-success",
  inactive: "bg-secondary",
  risk: "bg-danger",
  lost: "bg-warning",
};

/** Legend row: dot, label, count and percentage. */
export function LegendRow({
  status,
  label,
  count,
  total,
  selected = false,
  onSelect,
}: {
  status: Status;
  label: string;
  count: number;
  total: number;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const pct = total === 0 ? 0 : Math.round((count / total) * 100);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cx(
        "flex h-8 w-full items-center gap-2 rounded-medium border px-3 text-sm",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        selected ? "border-tertiary" : "border-transparent hover:border-tertiary",
      )}
    >
      <span aria-hidden className={cx("size-2 rounded-full", dot[status])} />
      <span className="flex-1 truncate text-left text-primary">{label}</span>
      <span className="w-12 text-right text-primary">{count.toLocaleString("vi-VN")}</span>
      <span className="w-12 text-right text-secondary">{pct}%</span>
    </button>
  );
}
