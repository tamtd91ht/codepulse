import type { ReactNode } from "react";
import { useState } from "react";
import { cx } from "./cx";

/** Placeholder in the shape of the final content. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded-small bg-surface-secondary", className)} />;
}

/** In-place empty text; offer the create action when the user can fix the emptiness. */
export function EmptyState({ text = "Chưa có dữ liệu", action }: { text?: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 text-xs text-secondary">
      <span>{text}</span>
      {action}
    </div>
  );
}

/** Inline error for one block: the rest of the page keeps working. */
export function ErrorNotice({
  title = "Không tải được dữ liệu",
  code,
  onRetry,
}: {
  title?: string;
  code?: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-large border border-danger/40 bg-danger/5 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-primary">{title}</p>
        {code && <p className="truncate font-mono text-xs text-secondary">{code}</p>}
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="h-8 shrink-0 rounded-medium border border-tertiary px-3 text-sm font-medium text-primary hover:bg-quaternary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Thử lại
        </button>
      )}
    </div>
  );
}

/** Toast body for a failed action: title, machine code, copy action. Render inside the project's toast region. */
export function ErrorToast({ title, code, copyLabel = "Sao chép mã lỗi" }: { title: string; code: string; copyLabel?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div role="status" className="flex w-96 max-w-full items-start gap-3 rounded-large bg-overlay px-4 py-3 shadow-popup">
      <span aria-hidden className="mt-0.5 size-4 shrink-0 rounded-full bg-danger" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-primary">{title}</p>
        <p className="truncate font-mono text-xs text-secondary">{code}</p>
      </div>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard?.writeText(code).then(() => setCopied(true));
        }}
        className="h-8 shrink-0 rounded-medium bg-default px-3 text-sm font-medium text-primary hover:bg-default/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {copied ? "Đã sao chép" : copyLabel}
      </button>
    </div>
  );
}

export type Status = "active" | "inactive" | "risk" | "lost";
const dot: Record<Status, string> = {
  active: "bg-success",
  inactive: "bg-secondary",
  risk: "bg-danger",
  lost: "bg-warning",
};

export function StatusChip({ status, children }: { status: Status; children: ReactNode }) {
  return (
    <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-quaternary px-2 text-xs text-primary">
      <span aria-hidden className={cx("size-2 rounded-full", dot[status])} />
      {children}
    </span>
  );
}

export function CountBadge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex min-h-5 items-center justify-center rounded-tiny bg-tertiary px-1.5 text-xs text-primary">
      {children}
    </span>
  );
}

/** Relative age of an overdue item, e.g. "-14d". */
export function OverdueBadge({ days }: { days: number }) {
  return (
    <span className="inline-flex min-h-5 items-center rounded-tiny bg-danger px-1.5 text-xs font-medium text-white">
      -{days}d
    </span>
  );
}
