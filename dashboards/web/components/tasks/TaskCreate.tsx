"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Message } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { Me, TaskDetail, TaskPriority, TaskSpace, TaskType } from "@/lib/types";

import {
  defaultPriority,
  priorityList,
  TASK_TYPES,
  TYPE_LABEL,
  errorText,
  personName,
  refreshTasks,
  useSprints,
  useTaskPeople,
} from "./taskParts";

/** Quick-create (§13 "Tạo task"): the main fields in one modal; the rest in the task's detail. */
export function TaskCreate({
  space,
  parentKey,
  onClose,
  onCreated,
}: {
  space: TaskSpace;
  parentKey?: string;
  onClose: () => void;
  onCreated: (key: string) => void;
}) {
  const bp = space.business_project_id;
  const people = useTaskPeople(bp);
  const { data: me } = useApi<Me>("/auth/me");
  // Without tasks.assign one may only take the task oneself (docs/specs/31 §4.1).
  const assignable = (people.data ?? []).filter((p) => space.can_assign || p.id === me?.id);
  const sprints = useSprints(bp);
  const types = TASK_TYPES.filter((t) => space.types.includes(t));
  const [type, setType] = useState<TaskType>(parentKey && types.includes("subtask") ? "subtask" : types.includes("task") ? "task" : (types[0] ?? "task"));
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TaskPriority>(() => defaultPriority(space));
  const [assignee, setAssignee] = useState("");
  const [parent, setParent] = useState(parentKey ?? "");
  const [sprint, setSprint] = useState("");
  const [due, setDue] = useState("");
  const [points, setPoints] = useState("");
  const [labels, setLabels] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = title.trim().length > 0 && !busy;

  async function create() {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<TaskDetail>("/tasks", {
        space: bp,
        type,
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
        assignee_id: assignee ? Number(assignee) : undefined,
        parent_key: parent.trim().toUpperCase() || undefined,
        sprint_id: sprint ? Number(sprint) : undefined,
        due_date: due || undefined,
        story_points: points.trim() ? Number(points) : undefined,
        labels: labels
          .split(",")
          .map((l) => l.trim())
          .filter(Boolean),
      });
      await refreshTasks();
      onCreated(created.key);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Tạo task · ${space.key ?? space.name}`}
      onClose={onClose}
      busy={busy}
      width={640}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void create()} disabled={!ready}>
            {busy ? "Đang tạo…" : "Tạo task"}
          </button>
        </>
      }
    >
      <form
        className="adm-form tk-form"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <div className="rq-grid tk-grid-2">
          <label className="rq-field">
            <span className="rq-label">Loại</span>
            <select value={type} onChange={(e) => setType(e.target.value as TaskType)}>
              {types.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABEL[t]}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Ưu tiên</span>
            <select value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
              {priorityList(space).map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="rq-field">
          <span className="rq-label">
            Tiêu đề <span className="error">*</span>
          </span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} autoFocus required placeholder="Việc cần làm" />
        </label>
        <label className="rq-field">
          <span className="rq-label">Mô tả (markdown)</span>
          <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div className="rq-grid">
          <label className="rq-field">
            <span className="rq-label">Người làm</span>
            <select value={assignee} onChange={(e) => setAssignee(e.target.value)} disabled={!people.data}>
              <option value="">Chưa giao</option>
              {assignable.map((p) => (
                <option key={p.id} value={p.id}>
                  {personName(p)}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Task cha</span>
            <input value={parent} onChange={(e) => setParent(e.target.value)} placeholder={`${space.key ?? "KEY"}-12`} />
          </label>
          <label className="rq-field">
            <span className="rq-label">Sprint</span>
            <select value={sprint} onChange={(e) => setSprint(e.target.value)} disabled={!sprints.data}>
              <option value="">Backlog</option>
              {(sprints.data ?? [])
                .filter((s) => s.state !== "closed")
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.state === "active" ? " (đang chạy)" : ""}
                  </option>
                ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Hạn</span>
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          <label className="rq-field">
            <span className="rq-label">Story point</span>
            <input type="number" min={0} step="0.5" value={points} onChange={(e) => setPoints(e.target.value)} />
          </label>
          <label className="rq-field">
            <span className="rq-label">Nhãn</span>
            <input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="backend, api" />
          </label>
        </div>
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}
