"use client";

/*
 * Anonymous usage beacon (docs/specs/31 §21.2). The server keeps only (role, event, surface, time):
 * nothing here names the person or the task. Events are batched and sent fire-and-forget
 * (`POST /api/v1/usage`, keepalive on page hide); a failure is ignored.
 *
 * `UsageTracker` (mounted once by the app shell) records, without touching each screen:
 * - pages opened: Việc của tôi, Board, Backlog, Timeline, List, Reports, task detail…;
 * - the Ctrl+K palette: opened, a task found (search_task), a page reached (navigate), a command
 *   run (command). A command also sets the short-lived `cp_surface=palette` cookie, so the status
 *   change it makes is counted as "palette" by the server, not "detail";
 * - single-key shortcuts.
 * Status changes and task creation are recorded by the server where they happen.
 */

import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, createElement, useEffect, useRef } from "react";

type UsageEventName = "page_open" | "palette" | "shortcut";
type Item = { event: UsageEventName; surface: string };

const FLUSH_MS = 5000;
const MAX_BATCH = 50;
let queue: Item[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function send(items: Item[]) {
  if (!items.length || typeof window === "undefined") return;
  try {
    void fetch("/api/v1/usage", {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: items.slice(0, MAX_BATCH) }),
    }).catch(() => undefined);
  } catch {
    /* fire-and-forget */
  }
}

export function flushUsage() {
  if (timer) clearTimeout(timer);
  timer = null;
  const items = queue;
  queue = [];
  for (let i = 0; i < items.length; i += MAX_BATCH) send(items.slice(i, i + MAX_BATCH));
}

/** Record one event (batched). */
export function track(event: UsageEventName, surface: string) {
  if (typeof window === "undefined") return;
  queue.push({ event, surface });
  if (queue.length >= MAX_BATCH) flushUsage();
  else if (!timer) timer = setTimeout(flushUsage, FLUSH_MS);
}

const TASK_KEY = /^[A-Za-z][A-Za-z0-9]*-\d+$/;
const TABS: Record<string, string> = {
  board: "board",
  backlog: "backlog",
  list: "list",
  reports: "reports",
  timeline: "timeline",
  health: "health",
  versions: "versions",
};

/** The page a URL shows, as a usage surface (null: not measured). */
export function pageOf(pathname: string, params: URLSearchParams): string | null {
  if (pathname === "/tasks/me") return "my_work";
  if (pathname === "/tasks/overview") return "overview";
  const tail = pathname.startsWith("/tasks/") ? pathname.slice("/tasks/".length) : "";
  if (tail && TASK_KEY.test(decodeURIComponent(tail))) return "task_detail";
  if (pathname === "/tasks" || pathname === "/tasks/") {
    if (params.get("task")) return "task_detail";
    return TABS[params.get("tab") ?? "board"] ?? null;
  }
  return null;
}

const SHORTCUT_KEYS = new Set(["e", ".", "?", "/", "n", "g"]);

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || !!target.closest("input, textarea, select, [contenteditable='true']");
}

/** What a palette row does: a task, a page ("Trang"), a sub-menu (null) or a command. */
function paletteChoice(item: Element): "search_task" | "navigate" | "command" | null {
  if (item.querySelector(".cmdk-key")) return "search_task";
  if (item.querySelector(".cmdk-hint")?.textContent?.trim() === "Trang") return "navigate";
  if (item.querySelectorAll("svg").length > 1) return null; // opens a stage (chevron)
  return "command";
}

function choosePalette(item: Element | null) {
  if (!item) return;
  const kind = paletteChoice(item);
  if (!kind) return;
  track("palette", kind);
  if (kind === "command") {
    try {
      document.cookie = "cp_surface=palette; Max-Age=5; Path=/; SameSite=Strict";
    } catch {
      /* cookies off: counted as "detail" */
    }
  }
}

function Tracker() {
  const pathname = usePathname();
  const params = useSearchParams();
  const last = useRef<string | null>(null);

  useEffect(() => {
    const page = pageOf(pathname ?? "", new URLSearchParams(params?.toString() ?? ""));
    const key = page ? `${page}|${params?.get("task") ?? ""}|${pathname}` : null;
    if (page && key !== last.current) track("page_open", page);
    last.current = key;
  }, [pathname, params]);

  useEffect(() => {
    const paletteOpen = () => !!document.querySelector(".cmdk-backdrop");
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        if (!paletteOpen()) track("palette", "open");
        return;
      }
      if (e.key === "Enter" && paletteOpen()) {
        choosePalette(document.querySelector(".cmdk .cmdk-item.active"));
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || paletteOpen()) return;
      if (document.querySelector(".modal-backdrop")) return;
      if (SHORTCUT_KEYS.has(e.key.toLowerCase())) track("shortcut", "key");
    };
    const onClick = (e: MouseEvent) => {
      const item = e.target instanceof Element ? e.target.closest(".cmdk .cmdk-item") : null;
      if (item) choosePalette(item);
    };
    const onOpen = () => track("palette", "open");
    const onHide = () => {
      if (document.visibilityState === "hidden") flushUsage();
    };
    // Capture: seen before the palette's own handlers run the command.
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("click", onClick, true);
    window.addEventListener("cp:palette-open", onOpen);
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", flushUsage);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("click", onClick, true);
      window.removeEventListener("cp:palette-open", onOpen);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", flushUsage);
    };
  }, []);

  return null;
}

/** Mounted once by the app shell for signed-in people; renders nothing. */
export function UsageTracker() {
  return createElement(Suspense, { fallback: null }, createElement(Tracker));
}
