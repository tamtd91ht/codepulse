"use client";

import { useRef, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Message } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type {
  Me,
  TaskDetail,
  TaskPriority,
  TaskSpace,
  TaskType,
} from "@/lib/types";

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
import {
  DraftFileList,
  MAX_FILE_MB,
  clipboardFiles,
  draftMark,
  useDraftFiles,
  type DraftFile,
} from "./taskFiles";

const NL = String.fromCharCode(10);

/** A starting description per type (docs/specs/31 §19.3): a Story's acceptance criteria, a Bug's
 * steps to reproduce. Swapped only while the description is still an untouched template. */
const DESCRIPTION_TEMPLATE: Partial<Record<TaskType, string>> = {
  story: ["**Là** …, **tôi muốn** …, **để** …", "", "### Tiêu chí chấp nhận", "- [ ] ", "- [ ] "].join(NL),
  bug: [
    "### Các bước tái hiện",
    "1. ",
    "2. ",
    "",
    "### Kết quả mong đợi",
    "",
    "### Kết quả thực tế",
    "",
    "### Môi trường",
    "- Phiên bản / build: ",
    "- Trình duyệt / thiết bị: ",
  ].join(NL),
};

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
  const assignable = (people.data ?? []).filter(
    (p) => space.can_assign || p.id === me?.id,
  );
  const sprints = useSprints(bp);
  const types = TASK_TYPES.filter((t) => space.types.includes(t));
  const [type, setType] = useState<TaskType>(
    parentKey && types.includes("subtask")
      ? "subtask"
      : types.includes("task")
        ? "task"
        : (types[0] ?? "task"),
  );
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState(() => DESCRIPTION_TEMPLATE[type] ?? "");
  const [priority, setPriority] = useState<TaskPriority>(() =>
    defaultPriority(space),
  );
  const [assignee, setAssignee] = useState("");
  const [parent, setParent] = useState(parentKey ?? "");
  const [sprint, setSprint] = useState("");
  const [due, setDue] = useState("");
  const [points, setPoints] = useState("");
  const [labels, setLabels] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = title.trim().length > 0 && !busy;
  // Files and screenshots chosen before the task exists: uploaded right after it is created (§17.1).
  const drafts = useDraftFiles();
  const canAttach = !!space.can_attach;
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  /** Hold the files; images pasted / dropped into the description also go where the caret is. */
  function take(list: File[], intoText: boolean) {
    if (!canAttach || !list.length) return;
    const { held, refused } = drafts.hold(list);
    if (refused.length) setError(refused.join("; "));
    const shots = intoText ? held.filter((f) => f.image) : [];
    if (!shots.length) return;
    const at = area.current ? area.current.selectionEnd : description.length;
    setDescription((text) => {
      const pos = Math.min(at, text.length);
      const before = text.slice(0, pos);
      const lead = !before || before.endsWith(NL) ? "" : NL;
      return `${before}${lead}${shots.map(draftMark).join(NL)}${NL}${text.slice(pos)}`;
    });
  }

  function removeDraft(f: DraftFile) {
    drafts.drop(f.tmp);
    setDescription((text) =>
      text
        .split(`${draftMark(f)}${NL}`)
        .join("")
        .split(draftMark(f))
        .join(""),
    );
  }

  async function create() {
    if (!ready || createdKey) return;
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
      if (drafts.files.length) {
        const sent = description.trim();
        const done = await drafts.uploadTo(created.key, sent);
        const problems = [...done.failed];
        if (done.description !== sent) {
          try {
            await api.patch(`/tasks/${encodeURIComponent(created.key)}`, {
              description: done.description,
            });
          } catch (e) {
            problems.push(`chèn ảnh vào mô tả: ${errorText(e)}`);
          }
        }
        if (problems.length) {
          // The task exists: say what did not make it; the button now opens it.
          await refreshTasks();
          setCreatedKey(created.key);
          setError(`Đã tạo ${created.key}, nhưng: ${problems.join("; ")}`);
          setBusy(false);
          return;
        }
      }
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
          <button
            type="button"
            className="ghost"
            onClick={onClose}
            disabled={busy}
          >
            Huỷ
          </button>
          {createdKey ? (
            <button
              type="button"
              className="primary"
              onClick={() => onCreated(createdKey)}
            >
              Mở {createdKey}
            </button>
          ) : (
            <button
              type="button"
              className="primary"
              onClick={() => void create()}
              disabled={!ready}
            >
              {busy
                ? drafts.files.length
                  ? "Đang tạo và tải file…"
                  : "Đang tạo…"
                : "Tạo task"}
            </button>
          )}
        </>
      }
    >
      <form
        className={`adm-form tk-form${over ? " tk-form-over" : ""}`}
        onDragOver={(e) => {
          if (!canAttach || ![...e.dataTransfer.types].includes("Files"))
            return;
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null))
            setOver(false);
        }}
        onDrop={(e) => {
          setOver(false);
          if (!canAttach || !e.dataTransfer.files.length) return;
          e.preventDefault();
          take([...e.dataTransfer.files], e.target === area.current);
        }}
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        {error && (
          <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />
        )}
        <div className="rq-grid tk-grid-2">
          <label className="rq-field">
            <span className="rq-label">Loại</span>
            <select
              value={type}
              onChange={(e) => {
                const next = e.target.value as TaskType;
                if (!description.trim() || Object.values(DESCRIPTION_TEMPLATE).includes(description)) {
                  setDescription(DESCRIPTION_TEMPLATE[next] ?? "");
                }
                setType(next);
              }}
            >
              {types.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABEL[t]}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Ưu tiên</span>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as TaskPriority)}
            >
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
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={300}
            autoFocus
            required
            placeholder="Việc cần làm"
          />
        </label>
        <label className="rq-field">
          <span className="rq-label">Mô tả (markdown)</span>
          <textarea
            ref={area}
            rows={5}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onPaste={(e) => {
              const list = canAttach ? clipboardFiles(e) : [];
              if (!list.length) return;
              e.preventDefault();
              take(list, true);
            }}
            placeholder={
              canAttach
                ? "Mô tả, các bước tái hiện lỗi… Dán ảnh chụp màn hình (Ctrl+V) vào đây."
                : undefined
            }
          />
        </label>
        {canAttach && (
          <div className="rq-field">
            <span className="rq-label">Đính kèm</span>
            <div className="tk-file-hint small muted">
              <button
                type="button"
                className="link sm"
                onClick={() => picker.current?.click()}
                disabled={busy}
              >
                <Icon name="upload" size={13} /> Chọn file, ảnh
              </button>
              <span>
                hoặc kéo thả vào khung này, dán ảnh (Ctrl+V) vào ô mô tả. Mỗi
                file tối đa {MAX_FILE_MB} MB.
              </span>
              <input
                ref={picker}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  take([...(e.target.files ?? [])], false);
                  e.target.value = "";
                }}
              />
            </div>
            <DraftFileList files={drafts.files} onRemove={removeDraft} />
          </div>
        )}
        <div className="rq-grid">
          <label className="rq-field">
            <span className="rq-label">Người làm</span>
            <select
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              disabled={!people.data}
            >
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
            <input
              value={parent}
              onChange={(e) => setParent(e.target.value)}
              placeholder={`${space.key ?? "KEY"}-12`}
            />
          </label>
          <label className="rq-field">
            <span className="rq-label">Sprint</span>
            <select
              value={sprint}
              onChange={(e) => setSprint(e.target.value)}
              disabled={!sprints.data}
            >
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
            <input
              type="date"
              value={due}
              onChange={(e) => setDue(e.target.value)}
            />
          </label>
          <label className="rq-field">
            <span className="rq-label">Story point</span>
            <input
              type="number"
              min={0}
              step="0.5"
              value={points}
              onChange={(e) => setPoints(e.target.value)}
            />
          </label>
          <label className="rq-field">
            <span className="rq-label">Nhãn</span>
            <input
              value={labels}
              onChange={(e) => setLabels(e.target.value)}
              placeholder="backend, api"
            />
          </label>
        </div>
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
