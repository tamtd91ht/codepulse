"use client";

import { type ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/icons";

import { refreshTasks } from "./taskParts";

/*
 * Cài đặt's one save bar (docs/specs/31 §22.7): each section with a draft registers it here while
 * it differs from the saved setup; the sticky bar at the bottom says "Có thay đổi chưa lưu" and
 * saves or reverts every such section at once. Sections save in page order and the task data is
 * refreshed once at the end, so a section's refresh never resets another's unsaved draft.
 */

export type SaveOpts = { refresh?: boolean };
type Entry = { label: string; order: number; valid: boolean; save: (opts: SaveOpts) => Promise<boolean>; reset: () => void };
type Register = (id: string, entry: Entry | null) => void;

const DraftCtx = createContext<Register | null>(null);

/**
 * Registers a section's draft with the save bar while `dirty`. `save` returns whether it worked
 * (it shows its own error); with `{ refresh: false }` it must not refresh the task data.
 */
export function useSettingsDraft(
  id: string,
  label: string,
  order: number,
  { dirty, valid = true, save, reset }: { dirty: boolean; valid?: boolean; save: (opts: SaveOpts) => Promise<boolean>; reset: () => void },
) {
  const register = useContext(DraftCtx);
  const latest = useRef({ save, reset });
  latest.current = { save, reset };
  useEffect(() => {
    register?.(id, dirty ? { label, order, valid, save: (o) => latest.current.save(o), reset: () => latest.current.reset() } : null);
  }, [register, id, label, order, dirty, valid]);
  useEffect(() => () => register?.(id, null), [register, id]);
}

export function SettingsDrafts({ children }: { children: ReactNode }) {
  const [drafts, setDrafts] = useState<Record<string, Entry>>({});
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const register = useCallback<Register>((id, entry) => {
    setDrafts((d) => {
      if (!entry) {
        if (!(id in d)) return d;
        const { [id]: _gone, ...rest } = d;
        return rest;
      }
      return { ...d, [id]: entry };
    });
    if (entry) setStatus(null);
  }, []);

  const list = Object.values(drafts).sort((a, b) => a.order - b.order);
  const dirty = list.length > 0;
  const valid = list.every((e) => e.valid);

  // Leaving the page with unsaved changes asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function saveAll() {
    setBusy(true);
    setStatus(null);
    let failed = 0;
    for (const entry of list) {
      if (!(await entry.save({ refresh: false }))) failed += 1;
    }
    await refreshTasks();
    setBusy(false);
    setStatus(failed ? `${failed} phần chưa lưu được, xem thông báo lỗi ở phần đó.` : "Đã lưu mọi thay đổi.");
  }

  return (
    <DraftCtx.Provider value={register}>
      {children}
      {dirty ? (
        <div className="st-savebar" role="region" aria-label="Thay đổi chưa lưu">
          <Icon name="alertCircle" size={16} />
          <span className="st-savebar-text">
            <strong>Có thay đổi chưa lưu</strong>
            <span className="small muted"> · {list.map((e) => e.label).join(", ")}</span>
            {!valid && <span className="small error"> · sửa ô đang lỗi trước khi lưu</span>}
            {status && (
              <span className="small error" role="status">
                {" "}
                · {status}
              </span>
            )}
          </span>
          <button type="button" className="ghost sm" disabled={busy} onClick={() => list.forEach((e) => e.reset())}>
            Hoàn tác
          </button>
          <button type="button" className="primary sm" disabled={busy || !valid} onClick={() => void saveAll()}>
            {busy ? "Đang lưu…" : "Lưu"}
          </button>
        </div>
      ) : (
        status && (
          <p className="st-saved small" role="status">
            {status}
          </p>
        )
      )}
    </DraftCtx.Provider>
  );
}
