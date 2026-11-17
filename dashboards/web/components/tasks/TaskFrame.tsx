"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject, useEffect, useId, useMemo, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import type { TaskSpace } from "@/lib/types";

import { dialogOpen, typingIn } from "./shortcutEvents";

/*
 * The one page frame of every Task screen (docs/specs/31 §22.1, UI/UX §3.1–§3.2): a single 48 px
 * row pinned under the top bar — breadcrumb `Task / <Dự án> ▾`, underline tabs, a ⚙ menu, then
 * "Của tôi" and the green "+ Tạo" on the right. The subtitle lives in a `?` tooltip.
 *
 * Sizing for the screens below (globals.css, "UI/UX frame"):
 *   --tk-head-h   48px, this row
 *   --tk-bar-h    40px, the filter toolbar row a screen puts first
 *   --tk-bar-top  sticky `top` for that toolbar (top bar + this row)
 *   --tk-view-h   height left for the main view under a toolbar (board, table, backlog)
 *   --tk-view-h-full  the same with no toolbar
 *   --tk-frame-h  everything that is not the main view: 100dvh - --tk-frame-h = --tk-view-h
 *
 * Keys (§7): 1…6 switch to Board, Backlog, Danh sách, Báo cáo, Sức khoẻ, Phiên bản (7: Timeline),
 * C creates a task. Neither fires while typing, with a modifier, behind a dialog, or — for C — while
 * a task is open (C then means "Viết bình luận", useTaskShortcuts).
 */

export interface FrameTab {
  key: string;
  label: string;
  icon: IconName;
  /** The digit that opens it (1…9), shown in the tooltip. */
  digit?: number;
}

export interface FrameMenuItem {
  key: string;
  label: string;
  icon: IconName;
  /** A link, or an action. */
  href?: string;
  run?: () => void;
  active?: boolean;
  /** Listed only under 768 px (a tab still visible in the row between 768 and 1279 px). */
  narrowOnly?: boolean;
}

export interface TaskFrameProps {
  /** After "Task /": a ProjectSwitcher, or plain text such as "Việc của tôi". Omit on /tasks itself without a dự án. */
  crumb?: ReactNode;
  /** The old subtitle, shown in the `?` tooltip. */
  help?: ReactNode;
  tabs?: FrameTab[];
  tab?: string;
  onTab?: (key: string) => void;
  /** The ⚙ menu (dự án settings, Tình trạng bật Task …); hidden when empty. `settingsActive` underlines ⚙. */
  menu?: FrameMenuItem[];
  settingsActive?: boolean;
  /** Extra controls before "Của tôi" (e.g. a view switch). */
  actions?: ReactNode;
  /** Show the "Của tôi" link (→ /tasks/me); `"current"` when on that page. */
  mine?: boolean | "current";
  /** "+ Tạo": an action on /tasks, a link elsewhere; null hides it and turns C off. */
  create?: { run: () => void } | { href: string } | null;
  children?: ReactNode;
}

/** Close a popover on a click outside `box` or Escape (focus goes back to its trigger). */
function useDismiss(open: boolean, close: () => void, box: RefObject<HTMLElement | null>, trigger: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) close();
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      close();
      trigger.current?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, close, box, trigger]);
}

/** ↑ / ↓ / Home / End move focus between the menu's items. */
function menuKeys(e: ReactKeyboardEvent<HTMLElement>) {
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[role='menuitem']"));
  if (!items.length) return;
  const at = items.indexOf(document.activeElement as HTMLElement);
  let next = -1;
  if (e.key === "ArrowDown") next = (at + 1) % items.length;
  else if (e.key === "ArrowUp") next = (at - 1 + items.length) % items.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = items.length - 1;
  if (next < 0) return;
  e.preventDefault();
  items[next].focus();
}

/** A button opening a short menu of links / actions (the ⚙ menu and "Thêm ▾"). */
function FrameMenu({ label, icon, text, items, active, className }: { label: string; icon: IconName; text?: string; items: FrameMenuItem[]; active?: boolean; className?: string }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useDismiss(open, () => setOpen(false), box, trigger);
  useEffect(() => {
    if (open) box.current?.querySelector<HTMLElement>("[role='menuitem']")?.focus();
  }, [open]);
  return (
    <div className={`tf-menu${className ? ` ${className}` : ""}`} ref={box}>
      <button
        ref={trigger}
        type="button"
        className={`tf-tab tf-menu-btn${active ? " active" : ""}${text ? "" : " tf-icon-tab"}`}
        aria-label={text ? undefined : label}
        title={text ? undefined : label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        <Icon name={icon} size={16} />
        {text && <span>{text}</span>}
        {text && <Icon name="chevronDown" size={14} />}
      </button>
      {open && (
        <div className="tf-pop tf-pop-menu" role="menu" id={id} aria-label={label} onKeyDown={menuKeys}>
          {items.map((it) =>
            it.href ? (
              <Link key={it.key} href={it.href} role="menuitem" className="tf-menu-item" onClick={() => setOpen(false)}>
                <Icon name={it.icon} size={16} /> {it.label}
              </Link>
            ) : (
              <button
                key={it.key}
                type="button"
                role="menuitem"
                className={`tf-menu-item${it.active ? " active" : ""}${it.narrowOnly ? " tf-narrow-only" : ""}`}
                aria-current={it.active ? "page" : undefined}
                onClick={() => {
                  setOpen(false);
                  it.run?.();
                }}
              >
                <Icon name={it.icon} size={16} /> {it.label}
                {it.active && <Icon name="check" size={14} className="tf-menu-check" />}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}

const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/gi, "d").toLowerCase();

/** `<Dự án> ▾` in the breadcrumb: a popover with a search over the dự án (§3.2). */
export function ProjectSwitcher({ spaces, value, onChange }: { spaces: TaskSpace[] | undefined; value: TaskSpace | null; onChange: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const close = () => {
    setOpen(false);
    setQ("");
  };
  useDismiss(open, close, box, trigger);
  const shown = useMemo(() => {
    const w = fold(q.trim());
    return (spaces ?? []).filter((s) => !w || fold(`${s.name} ${s.key ?? ""}`).includes(w));
  }, [spaces, q]);
  useEffect(() => {
    if (!open) return;
    const at = shown.findIndex((s) => s.business_project_id === value?.business_project_id);
    setHi(at >= 0 ? at : 0);
    // Only on opening / a new search: the highlight follows the keys afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, q]);
  useEffect(() => {
    if (open) document.getElementById(`${listId}-${hi}`)?.scrollIntoView({ block: "nearest" });
  }, [open, hi, listId]);

  const pick = (s: TaskSpace | undefined) => {
    if (!s) return;
    close();
    trigger.current?.focus();
    if (s.business_project_id !== value?.business_project_id) onChange(s.business_project_id);
  };

  return (
    <div className="tf-switch" ref={box}>
      <button
        ref={trigger}
        type="button"
        className="tf-crumb-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={value ? `Dự án: ${value.name}${value.enabled ? "" : " (chưa bật Task)"}. Đổi dự án` : "Chọn dự án"}
        disabled={!spaces?.length}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span className="tf-crumb-name">{value ? value.name : spaces ? "Chọn dự án" : "Đang tải…"}</span>
        {value?.key && <span className="tf-crumb-key mono">{value.key}</span>}
        {value && !value.enabled && <span className="tf-crumb-off">· chưa bật</span>}
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="tf-pop tf-pop-switch">
          <div className="tf-pop-search">
            <Icon name="search" size={14} />
            <input
              autoFocus
              type="search"
              value={q}
              placeholder="Tìm dự án…"
              aria-label="Tìm dự án"
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={shown.length ? `${listId}-${hi}` : undefined}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                  e.preventDefault();
                  if (shown.length) setHi((h) => (h + (e.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length);
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  pick(shown[hi]);
                }
              }}
            />
          </div>
          <ul className="tf-pop-list" role="listbox" id={listId} aria-label="Dự án">
            {shown.map((s, i) => {
              const current = s.business_project_id === value?.business_project_id;
              return (
                <li
                  key={s.business_project_id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={current}
                  className={`tf-option${i === hi ? " hi" : ""}`}
                  onMouseEnter={() => setHi(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(s)}
                >
                  <span className="tf-option-name">{s.name}</span>
                  {s.key ? <span className="mono tf-option-key">{s.key}</span> : <span className="tf-crumb-off">chưa bật</span>}
                  {current && <Icon name="check" size={14} className="tf-menu-check" />}
                </li>
              );
            })}
            {!shown.length && <li className="tf-option-empty">Không có dự án khớp</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

/** The `?` button: the page's description as a tooltip (hover and keyboard focus). */
function HelpTip({ children }: { children: ReactNode }) {
  const id = useId();
  return (
    <span className="tf-help">
      <button type="button" className="icon-btn tf-help-btn" aria-label="Giới thiệu trang" aria-describedby={id}>
        <span aria-hidden="true">?</span>
      </button>
      <span role="tooltip" id={id} className="tf-help-tip">
        {children}
      </span>
    </span>
  );
}

/** Tabs from this index on go to "Thêm ▾" under 1280 px (§6); under 768 px all but the active one. */
const KEEP_WIDE = 3;

export function TaskFrame({ crumb, help, tabs, tab, onTab, menu, settingsActive, actions, mine = true, create, children }: TaskFrameProps) {
  const router = useRouter();

  // §7: 1…9 switch tabs, C creates.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return;
      if (typingIn(e.target) || dialogOpen()) return;
      if (/^[1-9]$/.test(e.key) && tabs && onTab) {
        const t = tabs.find((x) => x.digit === Number(e.key));
        if (!t) return;
        e.preventDefault();
        if (t.key !== tab) onTab(t.key);
        return;
      }
      if (e.key.toLowerCase() === "c" && create) {
        // An open task owns C (focus the comment box).
        if (document.querySelector("[data-shortcut='comment']")) return;
        e.preventDefault();
        if ("run" in create) create.run();
        else router.push(create.href);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tabs, tab, onTab, create, router]);

  const moreItems: FrameMenuItem[] = (tabs ?? []).map((t, i) => ({ key: t.key, label: t.label, icon: t.icon, active: t.key === tab, narrowOnly: i < KEEP_WIDE, run: () => onTab?.(t.key) }));
  const activeOver = (tabs ?? []).findIndex((t) => t.key === tab) >= KEEP_WIDE;

  return (
    <div className="tf-page">
      <header className="tf-frame">
        <nav className="tf-crumbs" aria-label="Vị trí">
          <Link href="/tasks" className="tf-crumb-root">
            Task
          </Link>
          {crumb && (
            <>
              <span className="tf-crumb-sep" aria-hidden="true">
                /
              </span>
              {typeof crumb === "string" ? (
                <span className="tf-crumb-here" aria-current="page">
                  {crumb}
                </span>
              ) : (
                crumb
              )}
            </>
          )}
          {help && <HelpTip>{help}</HelpTip>}
        </nav>
        {!!(tabs?.length || menu?.length) && (
          <nav className="tf-tabs" aria-label="Màn Task">
            {tabs?.map((t, i) => (
              <button
                key={t.key}
                type="button"
                className={`tf-tab${t.key === tab ? " active" : ""}`}
                data-over={i >= KEEP_WIDE ? "" : undefined}
                aria-current={t.key === tab ? "page" : undefined}
                aria-keyshortcuts={t.digit ? String(t.digit) : undefined}
                title={t.digit ? `${t.label} (phím ${t.digit})` : undefined}
                onClick={() => onTab?.(t.key)}
              >
                {t.label}
              </button>
            ))}
            {!!tabs?.length && (
              <FrameMenu className={`tf-more${activeOver ? " tf-more-active" : ""}`} label="Thêm màn" icon="more" text="Thêm" items={moreItems} active={activeOver} />
            )}
            {!!menu?.length && <FrameMenu className="tf-gear" label="Cài đặt và quản trị Task" icon="settings" items={menu} active={settingsActive} />}
          </nav>
        )}
        <div className="tf-actions">
          {actions}
          {mine && (
            <Link href="/tasks/me" className={`button sm tf-mine${mine === "current" ? " active" : ""}`} aria-label="Việc của tôi" aria-current={mine === "current" ? "page" : undefined}>
              <Icon name="user" size={16} /> <span className="tf-btn-text">Của tôi</span>
            </Link>
          )}
          {create &&
            ("run" in create ? (
              <button type="button" className="primary sm tf-create" onClick={create.run} aria-keyshortcuts="C" aria-label="Tạo task" title="Tạo task (C)">
                <Icon name="plus" size={16} /> <span className="tf-btn-text">Tạo</span>
              </button>
            ) : (
              <Link href={create.href} className="button primary sm tf-create" aria-keyshortcuts="C" aria-label="Tạo task" title="Tạo task (C)">
                <Icon name="plus" size={16} /> <span className="tf-btn-text">Tạo</span>
              </Link>
            ))}
        </div>
      </header>
      <div className="tf-body">{children}</div>
    </div>
  );
}
