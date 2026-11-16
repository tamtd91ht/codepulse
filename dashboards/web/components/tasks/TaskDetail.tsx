"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { EmptyState, Loading, Message, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTimeMin, relative } from "@/lib/format";
import type { TaskDetail as Detail, TaskLinkKind, TaskPriority, TaskResolution, TaskSpace, TaskStatusRef, TaskType } from "@/lib/types";

import { TaskActivity, TaskAttachments } from "./TaskActivity";
import { FileTextarea } from "./taskFiles";
import { TaskAiButtons } from "./TaskAi";
import { TaskCode } from "./TaskCode";
import { ShortcutHelp } from "./ShortcutHelp";
import {
  LINK_KIND_LABEL,
  RESOLUTION_LABEL,
  RESOLUTIONS,
  priorityList,
  PersonAvatar,
  StatusPill,
  TASK_TYPES,
  TYPE_LABEL,
  TypeIcon,
  confirmDone,
  errorText,
  fmtMinutes,
  parseDuration,
  personName,
  refreshTasks,
  spaceOf,
  useConfirm,
  useSprints,
  useTaskFields,
  useTaskPeople,
  useTaskSpaces,
  useVersions,
} from "./taskParts";

/**
 * One task (§13 "Chi tiết task"), the same in the drawer (`/tasks?task=KEY`) and on `/tasks/KEY`.
 * Left: title, description, sub-tasks, links, Code, activity, files. Right: the fields.
 */
export function TaskDetail({
  taskKey,
  onOpenTask,
  onDeleted,
}: {
  taskKey: string;
  onOpenTask: (key: string) => void;
  onDeleted: () => void;
}) {
  const detail = useApi<Detail>(`/tasks/${encodeURIComponent(taskKey)}`);
  const task = detail.data;
  const bp = task ? spaceOf(task) : null;
  const spaces = useTaskSpaces();
  const space = spaces.data?.find((s) => s.business_project_id === bp) ?? null;

  if (!task) {
    if (detail.error instanceof ApiError && detail.error.status === 404)
      return <EmptyState icon="search" title={`Không tìm thấy task ${taskKey}`}>Task đã bị xoá hoặc bạn không xem được dự án của nó.</EmptyState>;
    if (detail.error instanceof ApiError && detail.error.status === 403)
      return <EmptyState icon="alert" title="Bạn không có quyền xem task này" />;
    return <Loading error={detail.error} rows={8} />;
  }
  return <Body task={task} bp={bp} space={space} onOpenTask={onOpenTask} onDeleted={onDeleted} />;
}

function Body({
  task,
  bp,
  space,
  onOpenTask,
  onDeleted,
}: {
  task: Detail;
  bp: number | null;
  space: TaskSpace | null;
  onOpenTask: (key: string) => void;
  onDeleted: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const path = `/tasks/${encodeURIComponent(task.key)}`;
  const canEdit = task.can.edit;

  async function patch(body: Record<string, unknown>): Promise<boolean> {
    setMessage(null);
    try {
      await api.patch(path, body);
      await refreshTasks();
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
      return false;
    }
  }

  const [closing, setClosing] = useState<TaskStatusRef | null>(null);

  async function transition(target: TaskStatusRef, resolution?: TaskResolution, comment?: string) {
    if (target.id === task.status.id) return;
    // Into "done": ask why first (§19.1).
    if (target.category === "done" && !resolution) {
      setClosing(target);
      return;
    }
    if (!(await confirmDone(ask, task.git?.summary.open_critical ?? 0, target))) return;
    setMessage(null);
    try {
      await api.post(`${path}/transition`, { status_id: target.id, resolution, comment: comment || undefined });
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  async function remove() {
    const subs = task.subtasks.length;
    const ok = await ask({
      title: `Xoá ${task.key}?`,
      body: <p>{subs ? `${subs} sub-task cũng bị xoá theo. ` : ""}Không hoàn tác được.</p>,
      ok: "Xoá task",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(path);
      await refreshTasks();
      onDeleted();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  return (
    <div className="tk-detail">
      {confirmEl}
      <ShortcutHelp />
      <div className="tk-detail-main">
        <div className="tk-detail-crumbs small muted">
          <TypeIcon type={task.type} withLabel />
          <span className="tk-key">{task.key}</span>
          {task.parent_key && (
            <>
              <span>· thuộc</span>
              <button type="button" className="link" onClick={() => onOpenTask(task.parent_key as string)}>
                {task.parent_key}
              </button>
            </>
          )}
        </div>
        <TitleEdit task={task} canEdit={canEdit} onSave={(title) => patch({ title })} />
        <TaskHealth task={task} />
        {message && <Message text={message} onClose={() => setMessage(null)} />}
        <DescriptionEdit task={task} canEdit={canEdit} onSave={(description) => patch({ description })} />
        <Subtasks task={task} bp={bp} space={space} onOpenTask={onOpenTask} />
        <Links task={task} onOpenTask={onOpenTask} />
        <TaskCode task={task} bp={bp} />
        <TaskActivity task={task} />
        <TaskAttachments task={task} />
      </div>
      <aside className="tk-detail-side" aria-label="Thông tin task">
        <Fields task={task} bp={bp} space={space} onPatch={patch} onTransition={(s) => void transition(s)} />
        {closing && (
          <ResolutionModal
            target={closing}
            onClose={() => setClosing(null)}
            onPick={(resolution, comment) => {
              const target = closing;
              setClosing(null);
              void transition(target, resolution, comment);
            }}
          />
        )}
        {canEdit && <TaskAiButtons task={task} bp={bp} types={space?.types ?? TASK_TYPES} />}
        <dl className="tk-meta small">
          <dt>Người tạo</dt>
          <dd>{personName(task.reporter)}</dd>
          <dt>Tạo lúc</dt>
          <dd title={dateTimeMin(task.created_at)}>{relative(task.created_at)}</dd>
          <dt>Cập nhật</dt>
          <dd title={dateTimeMin(task.updated_at)}>{relative(task.updated_at)}</dd>
          {task.resolved_at && (
            <>
              <dt>Xong lúc</dt>
              <dd>{dateTimeMin(task.resolved_at)}</dd>
            </>
          )}
        </dl>
        {task.can.delete && (
          <button type="button" className="ghost sm tc-danger tk-delete" onClick={() => void remove()}>
            <Icon name="remove" size={14} /> Xoá task
          </button>
        )}
      </aside>
    </div>
  );
}

/* ---------- Left column ---------- */

function TitleEdit({ task, canEdit, onSave }: { task: Detail; canEdit: boolean; onSave: (t: string) => Promise<boolean> }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null)
    return (
      <h2 className="tk-title">
        {canEdit ? (
          <button type="button" className="tk-title-btn" onClick={() => setDraft(task.title)} title="Bấm để sửa tiêu đề">
            {task.title}
          </button>
        ) : (
          task.title
        )}
      </h2>
    );
  const commit = async () => {
    const t = draft.trim();
    if (!t || t === task.title) return setDraft(null);
    if (await onSave(t)) setDraft(null);
  };
  return (
    <input
      className="tk-title-input"
      value={draft}
      autoFocus
      maxLength={300}
      aria-label="Tiêu đề"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === "Enter") void commit();
        if (e.key === "Escape") {
          e.stopPropagation();
          setDraft(null);
        }
      }}
    />
  );
}

function DescriptionEdit({ task, canEdit, onSave }: { task: Detail; canEdit: boolean; onSave: (d: string) => Promise<boolean> }) {
  const [draft, setDraft] = useState<string | null>(null);
  const [mode, setMode] = useState<"write" | "preview">("write");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(0);
  return (
    <section className="tk-section" aria-labelledby="tk-desc-title">
      <div className="tk-section-head">
        <h3 id="tk-desc-title">Mô tả</h3>
        {canEdit && draft === null && <IconButton icon="edit" label="Sửa mô tả" data-shortcut="edit-description" onClick={() => (setDraft(task.description ?? ""), setMode("write"))} />}
        {draft !== null && <Segmented<"write" | "preview"> label="Chế độ" value={mode} onChange={setMode} options={[{ value: "write", label: "Viết" }, { value: "preview", label: "Xem trước" }]} />}
      </div>
      {draft === null ? (
        task.description?.trim() ? (
          <Markdown text={task.description} />
        ) : (
          <p className="small faint">Chưa có mô tả.</p>
        )
      ) : (
        <div className="tk-editor">
          {mode === "write" ? (
            <FileTextarea
              rows={10}
              value={draft}
              taskKey={task.key}
              canAttach={task.can.attach}
              onUploaded={() => void refreshTasks()}
              onChange={(next) => setDraft((cur) => (typeof next === "function" ? next(cur ?? "") : next))}
              onPendingChange={setUploading}
              aria-label="Mô tả (markdown)"
              autoFocus
            />
          ) : (
            <div className="tk-preview">{draft.trim() ? <Markdown text={draft} /> : <p className="small faint">Trống.</p>}</div>
          )}
          <div className="adm-form-foot">
            <button type="button" className="ghost sm" onClick={() => setDraft(null)} disabled={busy}>
              Huỷ
            </button>
            <button
              type="button"
              className="sm primary"
              disabled={busy || uploading > 0}
              onClick={async () => {
                setBusy(true);
                if (await onSave(draft)) setDraft(null);
                setBusy(false);
              }}
            >
              Lưu mô tả
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function Subtasks({ task, bp, space, onOpenTask }: { task: Detail; bp: number | null; space: TaskSpace | null; onOpenTask: (k: string) => void }) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const types = space?.types ?? TASK_TYPES;
  const type: TaskType = types.includes("subtask") ? "subtask" : "task";
  const done = task.subtasks.filter((s) => s.status.category === "done").length;
  if (task.type === "subtask") return null;
  async function add() {
    if (!title.trim() || !bp) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/tasks", { space: bp, type, title: title.trim(), parent_key: task.key });
      setTitle("");
      await refreshTasks();
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }
  return (
    <section className="tk-section" aria-labelledby="tk-sub-title">
      <div className="tk-section-head">
        <h3 id="tk-sub-title">
          {task.type === "epic" ? "Việc con" : "Sub-task"}
          {task.subtasks.length > 0 && (
            <span className="small muted">
              {" "}
              {done}/{task.subtasks.length} xong
            </span>
          )}
        </h3>
      </div>
      {task.subtasks.length > 0 && (
        <>
          <div className="tk-progress" aria-hidden="true">
            <span style={{ width: `${(done / task.subtasks.length) * 100}%` }} />
          </div>
          <ul className="tk-rows">
            {task.subtasks.map((s) => (
              <li key={s.key}>
                <TypeIcon type={s.type} />
                <span className={`tk-key${s.status.category === "done" ? " done" : ""}`}>{s.key}</span>
                <button type="button" className="link tk-row-title" onClick={() => onOpenTask(s.key)}>
                  {s.title}
                </button>
                <span className="spacer" />
                <StatusPill status={s.status} resolution={s.resolution} />
                <PersonAvatar person={s.assignee} />
              </li>
            ))}
          </ul>
        </>
      )}
      {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
      {task.can.edit && (
        <form
          className="tk-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={`Thêm ${task.type === "epic" ? "việc con" : "sub-task"}…`} aria-label="Tiêu đề sub-task" />
          <button type="submit" className="sm" disabled={busy || !title.trim()}>
            Thêm
          </button>
        </form>
      )}
    </section>
  );
}

function Links({ task, onOpenTask }: { task: Detail; onOpenTask: (k: string) => void }) {
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<TaskLinkKind>("relates");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = `/tasks/${encodeURIComponent(task.key)}/links`;
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refreshTasks();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="tk-section" aria-labelledby="tk-links-title">
      <div className="tk-section-head">
        <h3 id="tk-links-title">Liên kết</h3>
      </div>
      {!task.links.length && <p className="small faint">Chưa liên kết task nào.</p>}
      {task.links.length > 0 && (
        <ul className="tk-rows">
          {task.links.map((l) => (
            <li key={l.id}>
              <span className="small muted tk-link-kind">{LINK_KIND_LABEL[l.kind]?.[l.direction] ?? l.kind}</span>
              <span className={`tk-key${l.task.status.category === "done" ? " done" : ""}`}>{l.task.key}</span>
              <button type="button" className="link tk-row-title" onClick={() => onOpenTask(l.task.key)}>
                {l.task.title}
              </button>
              <span className="spacer" />
              <StatusPill status={l.task.status} />
              {task.can.link && <IconButton icon="close" label="Gỡ liên kết" disabled={busy} onClick={() => void run(() => api.delete(`${path}/${l.id}`))} />}
            </li>
          ))}
        </ul>
      )}
      {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
      {task.can.link && (
        <form
          className="tk-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            const k = key.trim().toUpperCase();
            if (k) void run(() => api.post(path, { key: k, kind })).then((ok) => ok && setKey(""));
          }}
        >
          <select value={kind} onChange={(e) => setKind(e.target.value as TaskLinkKind)} aria-label="Kiểu liên kết">
            {(Object.keys(LINK_KIND_LABEL) as TaskLinkKind[]).map((k) => (
              <option key={k} value={k}>
                {LINK_KIND_LABEL[k].out}
              </option>
            ))}
          </select>
          <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="Mã task, ví dụ CRM-12" aria-label="Mã task" />
          <button type="submit" className="sm" disabled={busy || !key.trim()}>
            Liên kết
          </button>
        </form>
      )}
    </section>
  );
}

/* ---------- Right column ---------- */

/** An input that saves on blur / Enter when its text changed. */
function BlurInput({
  value,
  onCommit,
  disabled,
  label,
  type = "text",
  placeholder,
  invalid,
}: {
  value: string;
  onCommit: (v: string) => void;
  disabled?: boolean;
  label: string;
  type?: string;
  placeholder?: string;
  invalid?: (v: string) => boolean;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (text === value) return;
    if (invalid?.(text)) return setText(value);
    onCommit(text);
  };
  return (
    <input
      type={type}
      value={text}
      disabled={disabled}
      aria-label={label}
      placeholder={placeholder}
      step={type === "number" ? "0.5" : undefined}
      min={type === "number" ? 0 : undefined}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="tk-field">
      <span className="tk-field-label">{label}</span>
      <div className="tk-field-value">{children}</div>
    </div>
  );
}

function Fields({
  task,
  bp,
  space,
  onPatch,
  onTransition,
}: {
  task: Detail;
  bp: number | null;
  space: TaskSpace | null;
  onPatch: (b: Record<string, unknown>) => Promise<boolean>;
  onTransition: (s: TaskStatusRef) => void;
}) {
  const people = useTaskPeople(bp);
  const sprints = useSprints(bp);
  const versions = useVersions(bp);
  const fields = useTaskFields(bp);
  const [watchBusy, setWatchBusy] = useState(false);
  const off = !task.can.edit;
  const statuses = useMemo(() => {
    const list = [task.status, ...(task.transitions ?? []).filter((s) => s.id !== task.status.id)];
    return list;
  }, [task.status, task.transitions]);
  const types = (space?.types ?? TASK_TYPES).includes(task.type) ? (space?.types ?? TASK_TYPES) : [...(space?.types ?? TASK_TYPES), task.type];
  const custom = task.custom ?? {};
  const liveFields = (fields.data ?? []).filter((f) => !f.archived && f.id !== undefined).sort((a, b) => a.position - b.position);

  async function toggleWatch() {
    setWatchBusy(true);
    try {
      await api.put(`/tasks/${encodeURIComponent(task.key)}/watch`, { watching: !task.watching });
      await refreshTasks();
    } catch {
      /* the button simply stays as it was */
    }
    setWatchBusy(false);
  }

  const setCustom = (id: number, v: unknown) => void onPatch({ custom: { ...custom, [String(id)]: v === "" ? null : v } });

  return (
    <div className="tk-fields">
      <Row label="Trạng thái">
        <select
          value={task.status.id}
          disabled={!task.can.transition || statuses.length < 2}
          aria-label="Trạng thái"
          data-shortcut="status"
          className={`tk-status-select tk-cat-${task.status.category}`}
          onChange={(e) => {
            const s = statuses.find((x) => x.id === Number(e.target.value));
            if (s) onTransition(s);
          }}
        >
          {statuses.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Người làm">
        <select value={task.assignee?.id ?? ""} disabled={!task.can.assign} aria-label="Người làm" data-shortcut="assignee" onChange={(e) => void onPatch({ assignee_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">Chưa giao</option>
          {task.assignee && !(people.data ?? []).some((p) => p.id === task.assignee?.id) && <option value={task.assignee.id}>{personName(task.assignee)}</option>}
          {(people.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {personName(p)}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Ưu tiên">
        <select value={task.priority} disabled={off} aria-label="Ưu tiên" onChange={(e) => void onPatch({ priority: e.target.value as TaskPriority })}>
          {priorityList(space).map((p) => (
            <option key={p.key} value={p.key}>
              {p.name}
            </option>
          ))}
          {!priorityList(space).some((p) => p.key === task.priority) && <option value={task.priority}>{task.priority_name ?? task.priority}</option>}
        </select>
      </Row>
      <Row label="Loại">
        <select value={task.type} disabled={off} aria-label="Loại" onChange={(e) => void onPatch({ type: e.target.value })}>
          {types.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </select>
      </Row>
      {task.status.category === "done" && (
        <Row label="Lý do đóng">
          <select
            value={task.resolution ?? "done"}
            disabled={off}
            aria-label="Lý do đóng"
            onChange={(e) => void onPatch({ resolution: e.target.value as TaskResolution })}
          >
            {RESOLUTIONS.map((r) => (
              <option key={r} value={r}>
                {RESOLUTION_LABEL[r]}
              </option>
            ))}
          </select>
        </Row>
      )}
      <Row label="Nhãn">
        <BlurInput
          label="Nhãn (cách nhau bằng dấu phẩy)"
          value={(task.labels ?? []).join(", ")}
          disabled={off}
          placeholder="backend, api"
          onCommit={(v) =>
            void onPatch({
              labels: v
                .split(",")
                .map((l) => l.trim())
                .filter(Boolean),
            })
          }
        />
      </Row>
      <Row label="Thành phần">
        <BlurInput
          label="Thành phần (cách nhau bằng dấu phẩy)"
          value={(task.components ?? []).join(", ")}
          disabled={off}
          placeholder="api, web, mobile"
          onCommit={(v) =>
            void onPatch({
              components: v
                .split(",")
                .map((l) => l.trim())
                .filter(Boolean),
            })
          }
        />
      </Row>
      <Row label="Sprint">
        <select value={task.sprint_id ?? ""} disabled={off} aria-label="Sprint" onChange={(e) => void onPatch({ sprint_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">Backlog</option>
          {(sprints.data ?? [])
            .filter((s) => s.state !== "closed" || s.id === task.sprint_id)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.state === "active" ? " (đang chạy)" : s.state === "closed" ? " (đã đóng)" : ""}
              </option>
            ))}
        </select>
      </Row>
      <Row label="Phiên bản">
        <select value={task.version_id ?? ""} disabled={off} aria-label="Phiên bản" onChange={(e) => void onPatch({ version_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">Không</option>
          {(versions.data ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Story point">
        <BlurInput
          type="number"
          label="Story point"
          value={task.story_points === null || task.story_points === undefined ? "" : String(task.story_points)}
          disabled={off}
          onCommit={(v) => void onPatch({ story_points: v.trim() ? Number(v) : null })}
        />
      </Row>
      <Row label="Ước lượng">
        <BlurInput
          label="Ước lượng (ví dụ 2h30m)"
          value={task.estimate_min ? fmtMinutes(task.estimate_min).replace(" ", "") : ""}
          disabled={off}
          placeholder="2h30m"
          invalid={(v) => !!v.trim() && parseDuration(v) === null}
          onCommit={(v) => void onPatch({ estimate_min: v.trim() ? parseDuration(v) : null })}
        />
      </Row>
      <Row label="Đã làm">
        <span className="tk-field-text">
          {fmtMinutes(task.spent_min)}
          {task.estimate_min ? <span className="muted"> / {fmtMinutes(task.estimate_min)}</span> : null}
        </span>
      </Row>
      <Row label="Hạn">
        <input type="date" value={task.due_date ?? ""} disabled={off} aria-label="Hạn" onChange={(e) => void onPatch({ due_date: e.target.value || null })} />
      </Row>
      {liveFields.map((f) => {
        const id = f.id as number;
        const raw = custom[String(id)];
        const v = raw === null || raw === undefined ? "" : String(raw);
        return (
          <Row key={id} label={f.required ? `${f.name} *` : f.name}>
            {f.kind === "select" ? (
              <select value={v} disabled={off} aria-label={f.name} onChange={(e) => setCustom(id, e.target.value)}>
                <option value="">—</option>
                {(f.options ?? []).map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : f.kind === "user" ? (
              <select value={v} disabled={off} aria-label={f.name} onChange={(e) => setCustom(id, e.target.value ? Number(e.target.value) : "")}>
                <option value="">—</option>
                {(people.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {personName(p)}
                  </option>
                ))}
              </select>
            ) : f.kind === "date" ? (
              <input type="date" value={v} disabled={off} aria-label={f.name} onChange={(e) => setCustom(id, e.target.value)} />
            ) : (
              <BlurInput
                type={f.kind === "number" ? "number" : "text"}
                label={f.name}
                value={v}
                disabled={off}
                onCommit={(text) => setCustom(id, f.kind === "number" ? (text.trim() ? Number(text) : "") : text.trim())}
              />
            )}
          </Row>
        );
      })}
      <Row label="Theo dõi">
        <span className="tk-watch">
          <button type="button" className="sm" aria-pressed={task.watching} disabled={watchBusy} onClick={() => void toggleWatch()}>
            <Icon name={task.watching ? "bell" : "track"} size={14} /> {task.watching ? "Đang theo dõi" : "Theo dõi"}
          </button>
          <span className="small muted" title={task.watchers.map((w) => personName(w)).join(", ")}>
            {task.watchers.length} người
          </span>
        </span>
      </Row>
    </div>
  );
}

/** Closing a task (§19.1): why, and an optional note (a comment on the task). */
function ResolutionModal({ target, onClose, onPick }: { target: TaskStatusRef; onClose: () => void; onPick: (r: TaskResolution, comment: string) => void }) {
  const [value, setValue] = useState<TaskResolution>("done");
  const [comment, setComment] = useState("");
  return (
    <Modal
      title={`Chuyển sang “${target.name}”`}
      onClose={onClose}
      width={460}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => onPick(value, comment.trim())}>
            Đóng task
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        <fieldset className="rq-field">
          <legend className="rq-label">Lý do đóng</legend>
          {RESOLUTIONS.map((r) => (
            <label key={r} className="inline-check">
              <input type="radio" name="tk-resolution" checked={value === r} onChange={() => setValue(r)} />
              {RESOLUTION_LABEL[r]}
            </label>
          ))}
        </fieldset>
        <p className="small muted">Chỉ “Đã xong” được tính là việc hoàn thành (cycle time, DPI, velocity).</p>
        <label className="rq-field">
          <span className="rq-label">Ghi chú (tuỳ chọn)</span>
          <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Ví dụ: trùng với CRM-12" />
        </label>
      </div>
    </Modal>
  );
}

/**
 * Sức khoẻ task (docs/specs/31 §19.3): what the code says, in one line under the title — the
 * MRs, their review score, open critical findings, failed pipelines, production. Hidden while the
 * task has no code.
 */
function TaskHealth({ task }: { task: Detail }) {
  const g = task.git?.summary;
  if (!g || (!g.mrs && !g.commits)) return null;
  const score = g.avg_score === null || g.avg_score === undefined ? null : Number(g.avg_score);
  const merged = (task.git?.mrs ?? []).filter((m) => m.state === "merged").length;
  const items: { text: string; tone: "good" | "bad" | "warn" | "muted"; title: string }[] = [
    { text: g.mrs ? `${g.mrs} MR${merged ? ` (${merged} đã merge)` : ""}` : `${g.commits} commit`, tone: "muted", title: "MR và commit nối với task" },
    ...(score !== null && !Number.isNaN(score) ? [{ text: `điểm ${score.toFixed(1)}`, tone: score >= 7 ? ("good" as const) : ("warn" as const), title: "Điểm review trung bình" }] : []),
    { text: g.open_critical ? `${g.open_critical} critical` : "0 critical", tone: g.open_critical ? "bad" : "good", title: "Finding critical còn mở trên code của task" },
    ...(g.open_high ? [{ text: `${g.open_high} high`, tone: "warn" as const, title: "Finding high còn mở" }] : []),
    { text: g.pipelines_failed ? `${g.pipelines_failed} pipeline lỗi` : "pipeline ✓", tone: g.pipelines_failed ? "bad" : "good", title: "Pipeline của commit / MR" },
    { text: g.deployed ? `đã lên ${g.deployed.environment}` : "chưa deploy", tone: g.deployed ? "good" : "muted", title: g.deployed ? `Deploy lúc ${g.deployed.at}` : "Chưa thấy deploy production" },
  ];
  return (
    <div className="tk-health" aria-label="Sức khoẻ task">
      {items.map((it, i) => (
        <span key={i} className={`tk-health-item tk-health-${it.tone}`} title={it.title}>
          {it.text}
        </span>
      ))}
    </div>
  );
}
