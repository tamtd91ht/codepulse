import type { ReactNode } from "react";
import { cx } from "./cx";
import { CountBadge, EmptyState, ErrorNotice, Skeleton } from "./States";

/* Saved views ------------------------------------------------------------------------- */

export interface SavedView {
  id: string;
  label: string;
}

export function SavedViewTabs({
  views,
  activeId,
  onSelect,
  onCreate,
  createLabel = "Tạo bộ lọc mới",
}: {
  views: SavedView[];
  activeId: string;
  onSelect: (id: string) => void;
  onCreate?: () => void;
  createLabel?: string;
}) {
  return (
    <div role="tablist" aria-label="Saved views" className="flex flex-wrap items-center gap-2">
      {views.map((v) => {
        const active = v.id === activeId;
        return (
          <button
            key={v.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onSelect(v.id)}
            className={cx(
              "flex h-8 shrink-0 items-center rounded-medium px-3 text-sm transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              active ? "bg-primary text-primary-foreground" : "bg-quaternary text-primary hover:bg-default",
            )}
          >
            {v.label}
          </button>
        );
      })}
      {onCreate && (
        <button
          type="button"
          onClick={onCreate}
          className="h-8 rounded-medium border border-tertiary px-3 text-sm font-medium text-secondary hover:bg-quaternary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {createLabel}
        </button>
      )}
    </div>
  );
}

/* Toolbar ----------------------------------------------------------------------------- */

/**
 * Fixed order: title + count + refresh, search, at most three inline filters, "more filters";
 * right side: view switch, layout, import (outline-success), create (solid). Pass the pieces;
 * the component only lays them out.
 */
export function ListToolbar({
  title,
  count,
  refresh,
  search,
  filters,
  moreFilters,
  end,
}: {
  title: string;
  count?: number;
  refresh?: ReactNode;
  search?: ReactNode;
  filters?: ReactNode;
  moreFilters?: ReactNode;
  end?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <h1 className="flex items-center gap-2 font-semibold text-primary">
          {title}
          {count !== undefined && <CountBadge>{count.toLocaleString("vi-VN")}</CountBadge>}
        </h1>
        {refresh}
        {search}
        {filters}
        {moreFilters}
      </div>
      <div className="flex items-center gap-2">{end}</div>
    </div>
  );
}

export function SearchField({
  value,
  onChange,
  placeholder = "Tìm kiếm",
  icon,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  icon?: ReactNode;
}) {
  return (
    <label className="group relative flex h-8 w-44 items-center rounded-full border border-tertiary bg-surface transition-[width] duration-300 focus-within:w-64 focus-within:ring-2 focus-within:ring-accent">
      <span aria-hidden className="pointer-events-none absolute left-3 text-secondary [&>svg]:size-4">
        {icon}
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="h-full w-full rounded-full bg-transparent pr-3 pl-10 text-sm text-primary placeholder:text-secondary focus:outline-none"
      />
    </label>
  );
}

/* Table ------------------------------------------------------------------------------- */

export interface Column<T> {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
  sortable?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowActions,
  loading = false,
  error,
  onRetry,
  emptyText,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  rowActions?: (row: T) => ReactNode;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  emptyText?: string;
}) {
  if (error) return <ErrorNotice code={error} onRetry={onRetry} />;
  return (
    <div className="max-w-full overflow-x-auto border-t border-tertiary">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="shadow-[inset_0_-1px_0_0_var(--border)]">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                aria-sort={c.sortable ? "none" : undefined}
                className={cx("h-9 px-2 text-left text-xs font-medium whitespace-nowrap text-secondary first:pl-4", c.className)}
              >
                {c.header}
                {c.sortable && <span aria-hidden className="ml-1">⇅</span>}
              </th>
            ))}
            {rowActions && <th scope="col" className="sticky right-0 w-20 bg-surface" />}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 5 }, (_, i) => (
              <tr key={`sk-${i}`} className="border-b border-tertiary">
                {columns.map((c) => (
                  <td key={c.key} className="px-2 py-4 first:pl-4">
                    <Skeleton className="h-4 w-3/4" />
                  </td>
                ))}
                {rowActions && <td />}
              </tr>
            ))}
          {!loading && rows.length === 0 && (
            <tr>
              <td colSpan={columns.length + (rowActions ? 1 : 0)} className="px-4">
                <EmptyState text={emptyText} />
              </td>
            </tr>
          )}
          {!loading &&
            rows.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cx(
                  "group border-b border-tertiary transition-colors duration-150 hover:bg-quaternary",
                  onRowClick && "cursor-pointer",
                )}
              >
                {columns.map((c) => (
                  <td key={c.key} className={cx("px-2 py-3 align-middle first:pl-4", c.className)}>
                    {c.cell(row)}
                  </td>
                ))}
                {rowActions && (
                  <td
                    onClick={(e) => e.stopPropagation()}
                    className="sticky right-0 bg-[var(--sticky-cell-bg)] px-2 group-hover:bg-quaternary"
                  >
                    <div className="flex items-center justify-end gap-1">{rowActions(row)}</div>
                  </td>
                )}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

/* Pagination -------------------------------------------------------------------------- */

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  pageSizeControl,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  pageSizeControl?: ReactNode;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);
  const shown = Array.from({ length: pages }, (_, i) => i + 1).filter(
    (p) => p === 1 || p === pages || Math.abs(p - page) <= 1,
  );
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <nav aria-label="Pagination" className="flex items-center gap-1">
        {shown.map((p, i) => (
          <span key={p} className="flex items-center gap-1">
            {i > 0 && p - shown[i - 1] > 1 && <span className="px-1 text-secondary">…</span>}
            <button
              type="button"
              aria-current={p === page ? "page" : undefined}
              onClick={() => onPage(p)}
              className={cx(
                "size-8 rounded-medium text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                p === page ? "bg-primary text-primary-foreground" : "text-primary hover:bg-quaternary",
              )}
            >
              {p}
            </button>
          </span>
        ))}
      </nav>
      <div className="flex items-center gap-3 text-sm text-secondary">
        <span>
          {from}-{to} trên tổng {total.toLocaleString("vi-VN")} dữ liệu
        </span>
        {pageSizeControl && <span className="border-l border-tertiary pl-3">{pageSizeControl}</span>}
      </div>
    </div>
  );
}
