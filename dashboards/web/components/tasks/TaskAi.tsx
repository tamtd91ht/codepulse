"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { Loading, Message, copyText } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TaskAiItem, TaskAiRun, TaskDetail, TaskType } from "@/lib/types";

import { TASK_TYPES, TYPE_LABEL, errorText, refreshTasks } from "./taskParts";

type Kind = "breakdown" | "summary";
const FINISHED = ["done", "failed"];

/** "AI tách việc" and "AI tóm tắt" (§11): queue a run, poll every 3 s, show the result. */
export function TaskAiButtons({ task, bp, types }: { task: TaskDetail; bp: number | null; types: TaskType[] }) {
  const [open, setOpen] = useState<Kind | null>(null);
  return (
    <>
      <div className="tk-ai-buttons">
        {(task.type === "epic" || task.type === "story" || task.type === "task") && (
          <button type="button" className="sm" onClick={() => setOpen("breakdown")}>
            <Icon name="sparkle" size={14} /> AI tách việc
          </button>
        )}
        <button type="button" className="sm" onClick={() => setOpen("summary")}>
          <Icon name="sparkle" size={14} /> AI tóm tắt
        </button>
      </div>
      {open && <AiModal kind={open} task={task} bp={bp} types={types} onClose={() => setOpen(null)} />}
    </>
  );
}

function AiModal({ kind, task, bp, types, onClose }: { kind: Kind; task: TaskDetail; bp: number | null; types: TaskType[]; onClose: () => void }) {
  const [hint, setHint] = useState("");
  const [runId, setRunId] = useState<string | number | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useApi<TaskAiRun>(runId !== null ? `/tasks/ai/${runId}` : null, {
    refreshInterval: (latest) => (latest && FINISHED.includes(latest.status) ? 0 : 3000),
  });
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const items: TaskAiItem[] = run.data?.output?.items ?? [];
  useEffect(() => {
    if (run.data?.status === "done") setPicked(new Set(items.map((_, i) => i)));
    // Only when the run finishes: re-picking on every poll would undo the reader's choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.data?.status]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const res = await api.post<{ run_id: string | number }>(`/tasks/${encodeURIComponent(task.key)}/ai`, { kind, ...(hint.trim() ? { hint: hint.trim() } : {}) });
      setRunId(res.run_id);
    } catch (e) {
      setError(errorText(e));
    }
    setStarting(false);
  }

  async function createPicked() {
    if (!bp || !picked.size) return;
    setCreating(true);
    setError(null);
    // A type the dự án does not use falls back to a sub-task / task.
    const fallback: TaskType = types.includes("subtask") ? "subtask" : types.includes("task") ? "task" : (types[0] ?? "task");
    try {
      await api.post("/tasks/batch", {
        space: bp,
        parent_key: task.key,
        items: items
          .filter((_, i) => picked.has(i))
          .map((it) => ({
            type: types.includes(it.type) ? it.type : fallback,
            title: it.title,
            description: it.description ?? undefined,
            story_points: it.story_points ?? undefined,
          })),
      });
      await refreshTasks();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setCreating(false);
    }
  }

  const status = run.data?.status;
  const running = runId !== null && (!status || !FINISHED.includes(status));
  const title = kind === "breakdown" ? `AI tách việc · ${task.key}` : `AI tóm tắt tiến độ · ${task.key}`;
  const markdown = run.data?.output?.markdown ?? "";

  return (
    <Modal
      title={title}
      onClose={onClose}
      busy={creating}
      width={720}
      footer={
        runId === null ? (
          <>
            <button type="button" className="ghost" onClick={onClose}>
              Huỷ
            </button>
            <button type="button" className="primary" disabled={starting} onClick={() => void start()}>
              {starting ? "Đang gửi…" : kind === "breakdown" ? "Đề xuất việc con" : "Tóm tắt"}
            </button>
          </>
        ) : kind === "breakdown" && status === "done" && items.length > 0 ? (
          <>
            <button type="button" className="ghost" onClick={onClose} disabled={creating}>
              Đóng
            </button>
            <button type="button" className="primary" disabled={creating || !picked.size} onClick={() => void createPicked()}>
              {creating ? "Đang tạo…" : `Tạo ${picked.size} task`}
            </button>
          </>
        ) : kind === "summary" && status === "done" ? (
          <>
            {copied && <span className="small muted">{copied}</span>}
            <button type="button" className="ghost" onClick={() => void copyText(markdown).then((ok) => setCopied(ok ? "Đã chép" : "Không chép được"))}>
              Chép markdown
            </button>
            <button type="button" className="primary ink" onClick={onClose}>
              Đóng
            </button>
          </>
        ) : undefined
      }
    >
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        {runId === null ? (
          <>
            <p className="small muted">
              {kind === "breakdown"
                ? "AI đọc tiêu đề, mô tả của task và đề xuất 3–12 việc con có loại, mô tả, story point. Bạn chọn việc nào tạo."
                : "AI đọc mô tả, bình luận, commit, MR, điểm review và finding còn mở để tóm tắt tiến độ."}
            </p>
            <label className="rq-field">
              <span className="rq-label">Gợi ý thêm (tuỳ chọn)</span>
              <textarea rows={2} value={hint} onChange={(e) => setHint(e.target.value)} placeholder={kind === "breakdown" ? "Ví dụ: tách theo backend / frontend" : "Ví dụ: viết cho khách hàng"} />
            </label>
          </>
        ) : running ? (
          <Loading error={run.error}>AI đang làm… (thường mất 10–60 giây)</Loading>
        ) : status === "failed" ? (
          <Message text={`Lỗi: ${run.data?.error ?? "AI không trả lời được"}`} />
        ) : kind === "summary" ? (
          markdown ? <Markdown text={markdown} /> : <p className="small faint">AI không trả về nội dung.</p>
        ) : !items.length ? (
          <p className="small faint">AI không đề xuất việc con nào.</p>
        ) : (
          <ul className="tk-ai-items">
            {items.map((it, i) => (
              <li key={i}>
                <label className="tk-ai-item">
                  <input
                    type="checkbox"
                    checked={picked.has(i)}
                    onChange={(e) => {
                      const next = new Set(picked);
                      if (e.target.checked) next.add(i);
                      else next.delete(i);
                      setPicked(next);
                    }}
                  />
                  <span className="tk-ai-item-body">
                    <span>
                      <span className="tk-chip">{TASK_TYPES.includes(it.type) ? TYPE_LABEL[it.type] : it.type}</span> <strong>{it.title}</strong>
                      {it.story_points !== null && it.story_points !== undefined && <span className="small muted"> · {it.story_points} SP</span>}
                    </span>
                    {it.description && <span className="small muted">{it.description}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
