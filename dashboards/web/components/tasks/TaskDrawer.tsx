"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, useCallback, useEffect, useRef, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { useApi } from "@/lib/api";
import type { TaskDetail as Detail } from "@/lib/types";

import { stepKey, useDrawerKeys } from "./drawerNav";
import { overlayOpen, shortcutBlocked, typingIn } from "./shortcutEvents";
import { TaskDetail } from "./TaskDetail";
import { TypeIcon } from "./taskParts";

/*
 * Ngăn chi tiết task (docs/specs/31 §22.6, UI/UX §4.8): a non-modal drawer on the right. The view
 * behind stays visible and usable (no backdrop; on wide screens the page makes room for it, except
 * with `overlay`: the Danh sách view, whose table keeps its width under the drawer), the
 * width is dragged from the left edge between 420 px and 60% of the screen and remembered per
 * browser. Header: key, type, position in the list, ⤢ full page, ↗ copy link, ✕. Keys: J / K next /
 * previous task of the list the screen registered (drawerNav.ts), Esc closes (not while typing or
 * while a popover / dialog is open).
 */

const WIDTH_KEY = "cp:tasks:drawer-w";
const MIN_W = 420;
const DEFAULT_W = 560;
const STEP_W = 16;

const maxWidth = () => Math.max(MIN_W, Math.round(window.innerWidth * 0.6));
const clamp = (w: number) => Math.min(maxWidth(), Math.max(MIN_W, Math.round(w)));

function storedWidth(): number {
  try {
    const v = Number(window.localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(v) && v > 0 ? v : DEFAULT_W;
  } catch {
    return DEFAULT_W;
  }
}

function storeWidth(w: number) {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(w));
  } catch {
    /* per-viewer convenience only */
  }
}

/** A popover, menu or dialog is open somewhere: Esc belongs to it, not to the drawer (shared guard, §22.10). */
const popoverOpen = overlayOpen;

export function TaskDrawer({
  taskKey,
  onOpenTask,
  onClose,
  onDeleted,
  overlay = false,
}: {
  taskKey: string;
  /** Show another task in the drawer (J/K, sub-task, link, parent). */
  onOpenTask: (key: string) => void;
  onClose: () => void;
  onDeleted: () => void;
  /** Float over the view instead of making room for it (Danh sách: the table keeps its width, feedback #2). */
  overlay?: boolean;
}) {
  const detail = useApi<Detail>(`/tasks/${encodeURIComponent(taskKey)}`);
  const keys = useDrawerKeys();
  const prev = stepKey(keys, taskKey, -1);
  const next = stepKey(keys, taskKey, 1);
  const index = keys.indexOf(taskKey);

  const [width, setWidth] = useState(DEFAULT_W);
  const [maxW, setMaxW] = useState(DEFAULT_W);
  const widthNow = useRef(DEFAULT_W);
  widthNow.current = width;
  const [resizing, setResizing] = useState(false);
  const [copied, setCopied] = useState(false);
  const aside = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; w: number } | null>(null);

  // Latest callbacks for the window listeners without re-binding them on every render.
  const live = useRef({ onOpenTask, onClose, prev, next });
  live.current = { onOpenTask, onClose, prev, next };

  // Width from this browser, kept inside the bounds as the window changes.
  useEffect(() => {
    setWidth(clamp(storedWidth()));
    setMaxW(maxWidth());
    const onResize = () => {
      setMaxW(maxWidth());
      setWidth((w) => clamp(w));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // The page makes room for the drawer (globals.css, wide screens only).
  useEffect(() => {
    document.documentElement.style.setProperty("--td-w", `${width}px`);
  }, [width]);
  useEffect(() => {
    if (overlay) return;
    document.body.classList.add("td-open");
    return () => document.body.classList.remove("td-open");
  }, [overlay]);
  useEffect(
    () => () => {
      document.documentElement.style.removeProperty("--td-w");
    },
    [],
  );

  // Focus moves into the drawer when it opens and goes back to the opener when it closes.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    heading.current?.focus({ preventScroll: true });
    return () => {
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  // Another task: back to its top.
  useEffect(() => {
    body.current?.scrollTo({ top: 0 });
    setCopied(false);
  }, [taskKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (e.defaultPrevented || typingIn(e.target) || popoverOpen()) return;
        e.preventDefault();
        live.current.onClose();
        return;
      }
      if (e.shiftKey || shortcutBlocked(e)) return;
      const k = e.key.toLowerCase();
      const to = k === "j" ? live.current.next : k === "k" ? live.current.prev : null;
      if (k !== "j" && k !== "k") return;
      e.preventDefault();
      if (to) live.current.onOpenTask(to);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const setAndStore = useCallback((w: number) => {
    const c = clamp(w);
    setWidth(c);
    storeWidth(c);
  }, []);

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, w: width };
    setResizing(true);
  }
  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    setWidth(clamp(drag.current.w + drag.current.x - e.clientX));
  }
  function onPointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    drag.current = null;
    setResizing(false);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    storeWidth(widthNow.current);
  }
  function onHandleKey(e: ReactKeyboardEvent<HTMLDivElement>) {
    const by = e.shiftKey ? STEP_W * 4 : STEP_W;
    const to =
      e.key === "ArrowLeft" ? width + by : e.key === "ArrowRight" ? width - by : e.key === "Home" ? MIN_W : e.key === "End" ? maxWidth() : null;
    if (to === null) return;
    e.preventDefault();
    setAndStore(to);
  }

  async function copyLink() {
    const url = `${window.location.origin}/tasks/${encodeURIComponent(taskKey)}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      window.prompt("Sao chép link task", url);
    }
  }

  const task = detail.data;
  const full = `/tasks/${encodeURIComponent(taskKey)}`;
  return (
    <aside
      ref={aside}
      className={`td-drawer${resizing ? " td-resizing" : ""}`}
      role="dialog"
      aria-modal="false"
      aria-labelledby="td-title"
      style={{ width }}
    >
      <div
        className="td-handle"
        role="separator"
        aria-orientation="vertical"
        aria-label="Kéo để đổi độ rộng ngăn"
        aria-valuemin={MIN_W}
        aria-valuemax={maxW}
        aria-valuenow={width}
        tabIndex={0}
        title="Kéo để đổi độ rộng (mũi tên trái / phải)"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onHandleKey}
        onDoubleClick={() => setAndStore(DEFAULT_W)}
      />
      <header className="td-head">
        <div className="td-head-id">
          {task && <TypeIcon type={task.type} withLabel />}
          <h2 id="td-title" ref={heading} tabIndex={-1} className="td-key">
            {taskKey}
          </h2>
          {index >= 0 && keys.length > 1 && (
            <span className="td-pos small muted" aria-label={`Task ${index + 1} trên ${keys.length} của danh sách`}>
              {index + 1}/{keys.length}
            </span>
          )}
        </div>
        <div className="td-head-actions">
          {keys.length > 1 && index >= 0 && (
            <>
              <IconButton icon="chevronUp" label="Task trước (K)" disabled={!prev} onClick={() => prev && onOpenTask(prev)} />
              <IconButton icon="chevronDown" label="Task kế tiếp (J)" disabled={!next} onClick={() => next && onOpenTask(next)} />
              <span className="td-sep" aria-hidden="true" />
            </>
          )}
          <Link href={full} className="icon-btn" aria-label="Mở toàn trang" data-tip="Mở toàn trang">
            <Icon name="expand" size={16} />
          </Link>
          <IconButton icon={copied ? "check" : "link"} label={copied ? "Đã sao chép link" : "Sao chép link"} onClick={() => void copyLink()} />
          <IconButton icon="close" label="Đóng (Esc)" onClick={onClose} />
          <span className="visually-hidden" aria-live="polite">
            {copied ? "Đã sao chép link task" : ""}
          </span>
        </div>
      </header>
      <div className="td-body" ref={body}>
        <TaskDetail key={taskKey} taskKey={taskKey} mode="drawer" onOpenTask={onOpenTask} onDeleted={onDeleted} />
      </div>
    </aside>
  );
}

/**
 * The drawer driven by `?task=KEY` on the current path (for screens outside /tasks, e.g. Việc của
 * tôi): opening another task or closing rewrites only that parameter.
 */
export function UrlTaskDrawer() {
  const params = useSearchParams();
  const raw = params.get("task");
  const taskKey = raw && /^[A-Za-z][A-Za-z0-9]*-\d+$/.test(raw) ? raw.toUpperCase() : null;
  const set = useCallback((key: string | null) => {
    const p = new URLSearchParams(window.location.search);
    if (key) p.set("task", key);
    else p.delete("task");
    const q = p.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${q ? `?${q}` : ""}`);
  }, []);
  if (!taskKey) return null;
  return <TaskDrawer taskKey={taskKey} onOpenTask={(k) => set(k)} onClose={() => set(null)} onDeleted={() => set(null)} />;
}
