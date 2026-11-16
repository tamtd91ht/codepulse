"use client";

import type { TaskCategory, TaskType } from "@/lib/types";

import { TYPE_LABEL } from "./taskParts";

/* Project templates and board modes (docs/specs/31 §19.6). A template is picked once, when Task is
 * turned on; the workflow, types and git rules stay editable in Cài đặt afterwards. Mirrors
 * app/tasks/templates.py. */

export type TemplateKey = "scrum" | "kanban" | "qa";
export type BoardMode = "scrum" | "kanban";

export interface TaskTemplate {
  key: TemplateKey;
  name: string;
  description: string;
  statuses: { name: string; category: TaskCategory }[];
  types: TaskType[];
  board_mode: BoardMode;
}

export const TEMPLATES: TaskTemplate[] = [
  {
    key: "scrum",
    name: "Scrum",
    description: "Làm theo sprint: Backlog, lập sprint, Board của sprint đang chạy.",
    statuses: [
      { name: "Cần làm", category: "todo" },
      { name: "Đang làm", category: "doing" },
      { name: "Đang review", category: "doing" },
      { name: "Xong", category: "done" },
    ],
    types: ["epic", "story", "task", "bug", "subtask"],
    board_mode: "scrum",
  },
  {
    key: "kanban",
    name: "Kanban",
    description: "Luồng liên tục, không sprint: Board luôn hiện mọi task đang mở. Mở MR không đổi trạng thái.",
    statuses: [
      { name: "Cần làm", category: "todo" },
      { name: "Đang làm", category: "doing" },
      { name: "Xong", category: "done" },
    ],
    types: ["task", "bug", "subtask"],
    board_mode: "kanban",
  },
  {
    key: "qa",
    name: "QA / xử lý lỗi",
    description: "Theo dõi bug: mở MR sửa → Đã sửa; tester đóng sau khi kiểm lại (merge không tự đóng).",
    statuses: [
      { name: "Mới", category: "todo" },
      { name: "Đang xác minh", category: "doing" },
      { name: "Đã sửa", category: "doing" },
      { name: "Đã đóng", category: "done" },
    ],
    types: ["bug", "task", "subtask"],
    board_mode: "scrum",
  },
];

export const templateOf = (key: TemplateKey): TaskTemplate => TEMPLATES.find((t) => t.key === key) ?? TEMPLATES[0];

export const BOARD_MODE_TEXT: Record<BoardMode, { label: string; hint: string }> = {
  scrum: { label: "Scrum", hint: "Board là sprint đang chạy; có tab Backlog để lập sprint." },
  kanban: { label: "Kanban", hint: "Board hiện mọi task chưa xong và task xong 14 ngày qua; ẩn tab Backlog." },
};

/** The workflow of a template as a row of status chips. */
export function StatusFlow({ statuses }: { statuses: TaskTemplate["statuses"] }) {
  return (
    <ol className="tt-flow" aria-label="Trạng thái">
      {statuses.map((s) => (
        <li key={s.name} className={`tt-chip tt-chip-${s.category}`}>
          {s.name}
        </li>
      ))}
    </ol>
  );
}

/** Template cards as a radio group (arrow keys move between them, as native radios do). */
export function TemplatePicker({ value, onChange }: { value: TemplateKey; onChange: (key: TemplateKey) => void }) {
  return (
    <fieldset className="rq-field tt-picker">
      <legend className="rq-label">Chọn mẫu dự án</legend>
      <div className="tt-cards">
        {TEMPLATES.map((t) => (
          <label key={t.key} className={`tt-card${value === t.key ? " selected" : ""}`}>
            <input type="radio" name="tt-template" value={t.key} checked={value === t.key} onChange={() => onChange(t.key)} className="tt-radio" />
            <span className="tt-card-name">{t.name}</span>
            <span className="small muted">{t.description}</span>
            <StatusFlow statuses={t.statuses} />
            <span className="small muted">Loại: {t.types.map((x) => TYPE_LABEL[x]).join(", ")}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** Steps of the enable wizard; the current one is marked for screen readers. */
export function WizardSteps({ step, labels }: { step: number; labels: string[] }) {
  return (
    <ol className="tt-steps" aria-label="Các bước">
      {labels.map((label, i) => (
        <li key={label} className={i === step ? "current" : i < step ? "done" : undefined} aria-current={i === step ? "step" : undefined}>
          <span className="tt-step-no" aria-hidden="true">
            {i + 1}
          </span>
          {label}
        </li>
      ))}
    </ol>
  );
}

/** Scrum or Kanban, editable later in Cài đặt. */
export function BoardModeField({ value, onChange }: { value: BoardMode; onChange: (mode: BoardMode) => void }) {
  return (
    <fieldset className="rq-field tt-mode">
      <legend className="rq-label">Chế độ bảng</legend>
      {(Object.keys(BOARD_MODE_TEXT) as BoardMode[]).map((mode) => (
        <label key={mode} className="inline-check tt-mode-option">
          <input type="radio" name="tt-board-mode" value={mode} checked={value === mode} onChange={() => onChange(mode)} />
          <span>
            <strong>{BOARD_MODE_TEXT[mode].label}</strong>
            <span className="small muted"> · {BOARD_MODE_TEXT[mode].hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
