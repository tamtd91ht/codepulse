"use client";

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { IconButton } from "@/components/icons";

/** How long an action can be taken back (docs/specs/31 §21.7). */
export const UNDO_MS = 10_000;

export type UndoOffer = {
  /** "Đã chuyển OCX-2 sang Đang làm". */
  text: string;
  /** The inverse call(s); a thrown error is shown in the toast. */
  undo: () => Promise<void>;
  /** A second action before "Hoàn tác" ("Mở" on "Đã tạo OCX-12", docs/specs/31 §22.10). */
  action?: { label: string; run: () => void };
};

type Shown = UndoOffer & { id: number; state: "ready" | "busy" | "done" | "error"; error?: string };

/**
 * "Hoàn tác" for 10 seconds after a change (Board drag, a field in the task detail, a bulk edit):
 * `const [undoEl, offerUndo] = useUndo()`, render `undoEl`, call `offerUndo({text, undo})` after the
 * change saved. A newer offer replaces the older one (only the last change can be taken back).
 * Ctrl+Z (outside a text field) does the same as the button.
 */
export function useUndo(): [ReactNode, (offer: UndoOffer) => void] {
  const [shown, setShown] = useState<Shown | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const seq = useRef(0);

  const close = useCallback(() => {
    clearTimeout(timer.current);
    setShown(null);
  }, []);

  const offer = useCallback((o: UndoOffer) => {
    clearTimeout(timer.current);
    seq.current += 1;
    const id = seq.current;
    setShown({ ...o, id, state: "ready" });
    timer.current = setTimeout(() => setShown((s) => (s?.id === id && s.state === "ready" ? null : s)), UNDO_MS);
  }, []);

  const run = useCallback(async () => {
    const current = shown;
    if (!current || current.state !== "ready") return;
    clearTimeout(timer.current);
    setShown({ ...current, state: "busy" });
    try {
      await current.undo();
      setShown({ ...current, state: "done" });
      timer.current = setTimeout(() => setShown((s) => (s?.id === current.id ? null : s)), 2_500);
    } catch (e) {
      setShown({ ...current, state: "error", error: e instanceof Error ? e.message : String(e) });
    }
  }, [shown]);

  useEffect(() => {
    if (!shown || shown.state !== "ready") return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.key.toLowerCase() !== "z") return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      e.preventDefault();
      void run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, run]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const element = (
    <div className="lv-undo-wrap" role="status" aria-live="polite">
      {shown && (
        <div className={`lv-undo${shown.state === "error" ? " lv-undo-error" : ""}`}>
          <span className="lv-undo-text">
            {shown.state === "done" ? "Đã hoàn tác." : shown.state === "error" ? `Không hoàn tác được: ${shown.error}` : shown.text}
          </span>
          {shown.action && shown.state === "ready" && (
            <button
              type="button"
              className="sm ghost lv-undo-btn"
              onClick={() => {
                shown.action?.run();
                close();
              }}
            >
              {shown.action.label}
            </button>
          )}
          {(shown.state === "ready" || shown.state === "busy") && (
            <button type="button" className="sm ghost lv-undo-btn" disabled={shown.state === "busy"} onClick={() => void run()} title="Hoàn tác (Ctrl+Z)">
              {shown.state === "busy" ? "Đang hoàn tác…" : "Hoàn tác"}
            </button>
          )}
          <IconButton icon="close" label="Đóng" onClick={close} />
          {shown.state === "ready" && <span key={shown.id} className="lv-undo-timer" aria-hidden="true" />}
        </div>
      )}
    </div>
  );
  return [element, offer];
}
