"use client";

import { useId, useState } from "react";

import { Icon } from "@/components/icons";
import type { TaskRow } from "@/lib/types";

import { PersonAvatar, StatusPill } from "./taskParts";

/** localStorage key of the Board's "Gom sub-task vào card cha" switch (docs/specs/31 §20.2). */
export const GROUP_SUBS_KEY = "tk-board-group-subs";

export function readGroupSubs(): boolean {
  try {
    return localStorage.getItem(GROUP_SUBS_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeGroupSubs(on: boolean) {
  try {
    localStorage.setItem(GROUP_SUBS_KEY, on ? "1" : "0");
  } catch {
    /* a convenience only */
  }
}

/**
 * Under a parent card while sub-tasks are grouped: a `done/total` bar that opens the list of its
 * sub-tasks (key, title, status, assignee; a click opens one). `items` are the sub-tasks loaded on
 * the board; the counts are the server's, so sub-tasks outside the board still count.
 */
export function SubtaskGroup({ task, items, onOpen }: { task: TaskRow; items: TaskRow[]; onOpen: (key: string) => void }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const done = task.subtasks?.done ?? items.filter((s) => s.status.category === "done").length;
  const total = Math.max(task.subtasks?.total ?? 0, items.length);
  if (!total) return null;
  const pct = Math.round((done / total) * 100);
  const outside = total - items.length;
  return (
    <div className="qk-sub">
      <button type="button" className="qk-sub-toggle" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((v) => !v)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
        <span className="qk-sub-bar" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-label={`Sub-task xong ${done}/${total}`}>
          <span className="qk-sub-fill" style={{ width: `${pct}%` }} />
        </span>
        <span className="small muted">
          {done}/{total} sub-task
        </span>
      </button>
      {open && (
        <ul id={listId} className="qk-sub-list">
          {items.map((s) => (
            <li key={s.key}>
              <button type="button" className="qk-sub-item" onClick={() => onOpen(s.key)} title={s.title}>
                <span className={`tk-key${s.status.category === "done" ? " done" : ""}`}>{s.key}</span>
                <span className="qk-sub-title">{s.title}</span>
                <StatusPill status={s.status} resolution={s.resolution} />
                <PersonAvatar person={s.assignee} />
              </button>
            </li>
          ))}
          {outside > 0 && <li className="small faint qk-sub-more">+{outside} sub-task không ở board này</li>}
        </ul>
      )}
    </div>
  );
}
