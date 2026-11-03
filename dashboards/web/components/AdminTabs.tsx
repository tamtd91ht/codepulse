"use client";

import { type KeyboardEvent, type ReactNode } from "react";

/** Where an Admin area keeps its sub-tab: the second part of the URL hash (`#people/teams`). */
export interface SubNav {
  /** The sub-tab in the URL, unchecked (null when the hash has none). */
  sub: string | null;
  onSub: (sub: string) => void;
}

export interface SubTab<T extends string> {
  key: T;
  label: ReactNode;
  /** A count shown after the label (people, bots, accounts waiting). */
  count?: number;
  /** Shown on hover: what the sub-tab holds. */
  hint?: string;
}

/** The sub-tab of `nav`, or `fallback` when the URL names none (or one that does not exist). */
export function pickSub<T extends string>(nav: SubNav | undefined, tabs: readonly SubTab<T>[], fallback: T): T {
  const found = tabs.find((t) => t.key === nav?.sub);
  return found ? found.key : fallback;
}

/**
 * Pill tabs inside one Admin area (Người & Team › Người dùng | Team | …). Arrow keys, Home and
 * End move between them (roving tabindex); each tab controls the panel `${id}-panel-${key}`.
 */
export function SubTabs<T extends string>({
  id,
  label,
  tabs,
  value,
  onChange,
  actions,
  variant = "pill",
}: {
  id: string;
  label: string;
  tabs: readonly SubTab<T>[];
  value: T;
  onChange: (key: T) => void;
  /** Shown at the right of the tab row (the area's main action). */
  actions?: ReactNode;
  /** "pill" (Admin areas), "bar" (a page's main tabs, segmented look) or "mini" (small underline
   * tabs inside a page tab). */
  variant?: "pill" | "bar" | "mini";
}) {
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex((t) => t.key === value);
    const next =
      e.key === "ArrowRight" ? (index + 1) % tabs.length
      : e.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : -1;
    if (next < 0) return;
    e.preventDefault();
    onChange(tabs[next].key);
    document.getElementById(`${id}-tab-${tabs[next].key}`)?.focus();
  };
  return (
    <div className={variant === "pill" ? "sub-tabs-row" : `sub-tabs-row ${variant}`}>
      <div className={variant === "pill" ? "tabs compact sub-tabs" : `tabs compact sub-tabs ${variant}`} role="tablist" aria-label={label} onKeyDown={onKey}>
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`${id}-tab-${t.key}`}
            aria-selected={value === t.key}
            aria-controls={`${id}-panel-${t.key}`}
            tabIndex={value === t.key ? 0 : -1}
            className={value === t.key ? "active" : undefined}
            onClick={() => onChange(t.key)}
            title={t.hint}
          >
            {t.label}
            {t.count !== undefined && <span className="tab-count">{t.count.toLocaleString("vi-VN")}</span>}
          </button>
        ))}
      </div>
      {actions && <div className="sub-tabs-actions">{actions}</div>}
    </div>
  );
}

/** The panel of one sub-tab (pairs with SubTabs above). */
export function SubPanel({ id, tab, children }: { id: string; tab: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`${id}-panel-${tab}`} aria-labelledby={`${id}-tab-${tab}`}>
      {children}
    </div>
  );
}
