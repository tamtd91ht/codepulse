import type { ReactNode } from "react";
import { useId, useState } from "react";
import { cx } from "./cx";
import { CountBadge, EmptyState } from "./States";

/**
 * Detail header: avatar, name + status, contact line, stage, quick actions, owner, edit and
 * menu, then the created / updated / source meta line. Every slot is optional except `name`.
 */
export function DetailHeader({
  avatar,
  name,
  status,
  subtitle,
  stage,
  quickActions,
  owner,
  actions,
  meta,
}: {
  avatar?: ReactNode;
  name: string;
  status?: ReactNode;
  subtitle?: ReactNode;
  stage?: ReactNode;
  quickActions?: ReactNode;
  owner?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3 border-b border-tertiary px-6 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-4">
          {avatar}
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate font-semibold text-primary">{name}</h1>
              {status}
            </div>
            {subtitle && <p className="truncate text-sm text-secondary">{subtitle}</p>}
          </div>
        </div>
        {owner && <div className="flex items-center gap-2">{owner}</div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {stage}
          {quickActions && <div className="flex items-center gap-1 border-l border-tertiary pl-3">{quickActions}</div>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {meta && <p className="text-sm text-secondary">{meta}</p>}
    </header>
  );
}

/** Info column, tabbed centre, related-object rail. The rail drops below on narrow screens. */
export function DetailLayout({ info, main, rail }: { info: ReactNode; main: ReactNode; rail?: ReactNode }) {
  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[300px_minmax(0,1fr)_400px]">
      <aside className="border-b border-tertiary p-4 lg:border-r lg:border-b-0">{info}</aside>
      <section className="min-w-0 p-4">{main}</section>
      {rail && <aside className="border-t border-tertiary p-4 lg:col-span-2 xl:col-span-1 xl:border-t-0 xl:border-l">{rail}</aside>}
    </div>
  );
}

/** Label in muted, value in ink, "-" when empty; `copy` adds a copy button for contact values. */
export function KeyValue({ label, value, copy = false }: { label: string; value?: string | null; copy?: boolean }) {
  const shown = value && value.trim() ? value : "-";
  return (
    <div className="grid grid-cols-[130px_minmax(0,1fr)] gap-2 py-1.5 text-sm">
      <dt className="text-secondary">{label}</dt>
      <dd className="flex min-w-0 items-start gap-1 break-words text-primary">
        {shown}
        {copy && value && (
          <button
            type="button"
            aria-label={`Sao chép ${label}`}
            onClick={() => void navigator.clipboard?.writeText(value)}
            className="shrink-0 rounded-tiny px-1 text-secondary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            ⧉
          </button>
        )}
      </dd>
    </div>
  );
}

/** Related-object section: chevron, title, count, add and menu; empty text in place. */
export function CollapsibleSection({
  title,
  count,
  countLabel,
  onAdd,
  menu,
  defaultOpen = true,
  children,
}: {
  title: string;
  count?: number;
  countLabel?: string;
  onAdd?: () => void;
  menu?: ReactNode;
  defaultOpen?: boolean;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  const empty = children === undefined || children === null || (Array.isArray(children) && children.length === 0);
  return (
    <section className="border-b border-tertiary py-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((o) => !o)}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-small text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <span aria-hidden className={cx("text-secondary transition-transform duration-150", !open && "-rotate-90")}>
            ⌄
          </span>
          <span className="truncate text-sm font-semibold text-primary hover:underline">{title}</span>
          {count !== undefined && count > 0 && <CountBadge>{countLabel ?? count}</CountBadge>}
        </button>
        {onAdd && (
          <button
            type="button"
            onClick={onAdd}
            aria-label={`Thêm ${title}`}
            className="flex size-7 items-center justify-center rounded-medium text-secondary hover:bg-quaternary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            +
          </button>
        )}
        {menu}
      </div>
      <div id={bodyId} hidden={!open} className="pt-2 pl-6">
        {empty ? <EmptyState /> : children}
      </div>
    </section>
  );
}
