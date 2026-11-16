"use client";

import { useEffect } from "react";

/*
 * Keyboard shortcuts on a task (docs/specs/31 §19.8). Each finds its target by a stable
 * `data-shortcut="..."` attribute, so the detail's components need no wiring. Never fires while
 * typing (input, textarea, select, contenteditable), with Ctrl / Alt / Meta held, or behind a
 * dialog.
 */

export interface TaskShortcut {
  keys: string[];
  label: string;
}

export const TASK_SHORTCUTS: TaskShortcut[] = [
  { keys: ["E"], label: "Sửa mô tả" },
  { keys: ["A"], label: "Chọn người làm" },
  { keys: ["S"], label: "Chọn trạng thái" },
  { keys: ["C"], label: "Viết bình luận" },
  { keys: ["."], label: "Mở bảng lệnh (như Ctrl+K)" },
  { keys: ["?"], label: "Hiện / ẩn danh sách phím tắt" },
  { keys: ["Esc"], label: "Đóng danh sách phím tắt" },
];

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return !!target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']");
}

function find(name: string): HTMLElement | null {
  const el = document.querySelector<HTMLElement>(`[data-shortcut="${name}"]`);
  if (!el || (el as HTMLButtonElement | HTMLSelectElement).disabled) return null;
  return el;
}

function focus(el: HTMLElement) {
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.focus({ preventScroll: true });
}

/** Runs the shortcuts; `onHelp` toggles the "?" overlay. */
export function useTaskShortcuts(onHelp: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      if (typing(e.target)) return;
      // "?" needs Shift on most layouts; any other key with Shift is left alone.
      if (e.key === "?") {
        e.preventDefault();
        onHelp();
        return;
      }
      if (e.shiftKey) return;
      if (document.querySelector(".modal-backdrop, .cmdk-backdrop")) return;
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
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
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
  }, [onHelp, enabled]);
}
