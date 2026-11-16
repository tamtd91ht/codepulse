"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Message } from "@/components/ui";
import { api } from "@/lib/api";
import type { TaskResolution } from "@/lib/types";

import { errorText, refreshTasks } from "./taskParts";

/*
 * Quy tắc "Đóng task cha thì hỏi đóng sub-task" (docs/specs/31 §19.7, parent_done_ask).
 * Wiring (TaskDetail, after a successful move of a parent into a "done" status):
 *   const ask = await api.get<OpenSubtasks>(`/tasks/${key}/open-subtasks`);
 *   if (ask.ask) show <ParentDoneAsk taskKey={key} items={ask.items} resolution={…} onClose={…} />
 * Move the parent first, then close the sub-tasks (the "sub-tasks done → parent to review" rule
 * then leaves the closed parent alone).
 */

export type OpenSubtasks = { ask: boolean; items: { key: string; title: string; can_close: boolean }[] };
type Closed = { closed: string[]; skipped: { key: string; reason: string }[] };

export function ParentDoneAsk({
  taskKey,
  items,
  resolution = "done",
  onClose,
}: {
  taskKey: string;
  items: OpenSubtasks["items"];
  resolution?: TaskResolution;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const closable = items.filter((i) => i.can_close).length;

  async function closeAll() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.post<Closed>(`/tasks/${taskKey}/close-subtasks`, { resolution });
      await refreshTasks();
      if (res.skipped.length) {
        setMessage(`Đã đóng ${res.closed.length}. Chưa đóng: ${res.skipped.map((s) => `${s.key} (${s.reason})`).join("; ")}`);
      } else {
        onClose();
      }
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Modal
      title={`${taskKey} còn ${items.length} sub-task mở`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Để nguyên
          </button>
          <button type="button" className="primary" onClick={() => void closeAll()} disabled={busy || closable === 0}>
            Đóng luôn {closable} sub-task
          </button>
        </>
      }
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <p>Task cha đã chuyển sang Xong. Đóng luôn các sub-task còn mở?</p>
      <ul className="ta-sublist">
        {items.map((i) => (
          <li key={i.key}>
            <strong>{i.key}</strong> {i.title}
            {!i.can_close && <span className="small muted"> · bạn không chuyển được</span>}
          </li>
        ))}
      </ul>
    </Modal>
  );
}
