"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { can } from "@/lib/api";
import type { Me } from "@/lib/types";

import { openPalette, shortcutBlocked } from "./shortcutEvents";

/*
 * Keyboard shortcuts (docs/specs/31 §19.8, §20.2). Global ones (N, /, G B, G M, ?) run on every
 * page from the command palette's mount; the task ones (E, A, S, C, .) run while a task is open and
 * find their target by a stable `data-shortcut="..."` attribute, so the detail's components need no
 * wiring. None fires while typing (input, textarea, select, contenteditable), with Ctrl / Alt / Meta
 * held, or behind a dialog.
 */

export interface TaskShortcut {
  keys: string[];
  label: string;
}

export const GLOBAL_SHORTCUTS: TaskShortcut[] = [
  { keys: ["Ctrl", "K"], label: "Mở bảng lệnh (task, trang, lệnh)" },
  { keys: ["/"], label: "Tìm task" },
  { keys: ["N"], label: "Tạo task" },
  { keys: ["G", "B"], label: "Mở Board" },
  { keys: ["G", "M"], label: "Mở Việc của tôi" },
  { keys: ["?"], label: "Hiện / ẩn danh sách phím tắt" },
];

export const TASK_SHORTCUTS: TaskShortcut[] = [
  { keys: ["E"], label: "Sửa mô tả" },
  { keys: ["A"], label: "Chọn người làm" },
  { keys: ["S"], label: "Chọn trạng thái" },
  { keys: ["C"], label: "Viết bình luận" },
  { keys: ["."], label: "Mở bảng lệnh với lệnh trên task" },
];

/** The page frame's keys (TaskFrame, docs/specs/31 §22.1); C yields to "Viết bình luận" while a task is open. */
export const FRAME_SHORTCUTS: TaskShortcut[] = [
  { keys: ["1 … 6"], label: "Chuyển tab Board, Backlog, Danh sách, Báo cáo, Sức khoẻ, Phiên bản (7: Timeline)" },
  { keys: ["C"], label: "Tạo task (khi không mở task nào)" },
];

function find(name: string): HTMLElement | null {
  const el = document.querySelector<HTMLElement>(`[data-shortcut="${name}"]`);
  if (!el || (el as HTMLButtonElement | HTMLSelectElement).disabled) return null;
  return el;
}

function focus(el: HTMLElement) {
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.focus({ preventScroll: true });
}

// Every key here goes through the one shared guard (shortcutEvents.ts, docs/specs/31 §22.10).

/** The task shortcuts (E, A, S, C, .); mounted by the task detail. */
export function useTaskShortcuts(enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (shortcutBlocked(e) || e.shiftKey) return;
      const key = e.key.toLowerCase();
      let el: HTMLElement | null = null;
      if (key === "e") {
        el = find("edit-description");
        if (el) {
          e.preventDefault();
          el.click();
          // The editor mounts on the next render; put the caret in it.
          window.setTimeout(() => {
            const area = document.querySelector<HTMLTextAreaElement>(".tk-editor textarea");
            if (area) focus(area);
          }, 0);
        }
        return;
      }
      if (key === ".") {
        e.preventDefault();
        openPalette("all");
        return;
      }
      const target = ({ a: "assignee", s: "status", c: "comment" } as Record<string, string>)[key];
      if (!target) return;
      el = find(target);
      if (!el) return;
      e.preventDefault();
      focus(el);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

/** A second key of "G …" counts within this many ms. */
const CHORD_MS = 1500;

/**
 * The global shortcuts. `onHelp` toggles the "?" overlay; `helpOpen` lets "?" close it although it
 * is a dialog. N needs tasks.create, G B / G M / and the search need tasks.view.
 */
export function useGlobalShortcuts(me: Me, onHelp: () => void, helpOpen: boolean) {
  const router = useRouter();
  const chord = useRef(0);
  useEffect(() => {
    const tasks = can(me, "tasks.view");
    const onKey = (e: KeyboardEvent) => {
      if (shortcutBlocked(e, { allowOverlay: true })) return;
      // "?" needs Shift on most layouts; any other key with Shift is left alone.
      if (e.key === "?") {
        if (!helpOpen && shortcutBlocked(e)) return;
        e.preventDefault();
        onHelp();
        return;
      }
      if (e.shiftKey || shortcutBlocked(e)) return;
      const key = e.key.toLowerCase();
      const afterG = Date.now() - chord.current < CHORD_MS;
      chord.current = 0;
      if (afterG && tasks && (key === "b" || key === "m")) {
        e.preventDefault();
        router.push(key === "b" ? "/tasks?tab=board" : "/tasks/me");
        return;
      }
      if (key === "g" && tasks) {
        chord.current = Date.now();
        return;
      }
      if (key === "/" && tasks) {
        e.preventDefault();
        openPalette("search");
        return;
      }
      if (key === "n" && can(me, "tasks.create")) {
        e.preventDefault();
        // Keep the dự án in view when already on the Task page.
        const space = window.location.pathname === "/tasks" ? new URLSearchParams(window.location.search).get("space") : null;
        router.push(space && /^\d+$/.test(space) ? `/tasks?space=${space}&create=1` : "/tasks?create=1");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [me, onHelp, helpOpen, router]);
}
