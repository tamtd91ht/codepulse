"use client";

import { type DragEvent, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { IconButton } from "@/components/icons";
import { Alert, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Sprint, TaskEpic, TaskPage, TaskRow, TaskSpace } from "@/lib/types";

import { byRank, neighbours } from "./TaskBoard";
import {
  GitBadge,
  PersonAvatar,
  PointsChip,
  RowPriority,
  StatusPill,
  TypeIcon,
  addDays,
  errorText,
  refreshTasks,
  today,
  useConfirm,
  useSprints,
} from "./taskParts";

const DRAG_TYPE = "text/x-task-key";
type Box = number | "backlog";

/** Backlog (§13): planned / active sprints with their tasks, then the backlog; drag between them. */
export function TaskBacklog({ space, onOpen }: { space: TaskSpace; onOpen: (key: string) => void }) {
  const bp = space.business_project_id;
  const sprints = useSprints(bp);
  const backlog = useApi<TaskPage>(`/tasks?space=${bp}&sprint=backlog&sort=rank&limit=500`);
  const [drag, setDrag] = useState<string | null>(null);
  const [over, setOver] = useState<{ box: Box; before: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ kind: "edit" | "start" | "complete"; sprint: Sprint | null } | null>(null);
  const [confirmEl, ask] = useConfirm();
  const canManage = space.can_sprint;
  const epics = useApi<TaskEpic[]>(`/tasks/spaces/${bp}/epics`);
  // The Epic bar's pick (§19.1): only that epic's children; null = all.
  const [epic, setEpic] = useState<string | null>(null);
  // Epics live in the Epic bar, sub-tasks inside their parent: neither is a Backlog row.
  const shows = (t: TaskRow) => t.type !== "epic" && t.type !== "subtask" && (!epic || t.parent_key === epic);

  const open = useMemo(
    () =>
      (sprints.data ?? [])
        .filter((s) => s.state !== "closed")
        .sort((a, b) => (a.state === "active" ? -1 : b.state === "active" ? 1 : a.id - b.id)),
    [sprints.data],
  );
  const backlogRows = useMemo(
    () => (backlog.data?.items ?? []).filter((t) => t.status.category !== "done" && shows(t)).sort(byRank),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [backlog.data, epic],
  );
  const hasActive = open.some((s) => s.state === "active");

  function rowsOf(box: Box): TaskRow[] {
    return box === "backlog" ? backlogRows : [...(open.find((s) => s.id === box)?.tasks ?? [])].filter(shows).sort(byRank);
  }

  async function move(key: string, box: Box, beforeKey: string | null) {
    const { before_key, after_key } = neighbours(rowsOf(box), key, beforeKey);
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`/tasks/${encodeURIComponent(key)}/move`, { sprint_id: box === "backlog" ? null : box, before_key, after_key });
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    await refreshTasks();
    setBusy(false);
  }

  async function remove(s: Sprint) {
    const ok = await ask({
      title: `Xoá sprint “${s.name}”?`,
      body: <p>Task của sprint quay về backlog.</p>,
      ok: "Xoá sprint",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.delete(`/tasks/sprints/${s.id}`);
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  const dropProps = (box: Box) => ({
    onDragOver: (e: DragEvent<HTMLElement>) => {
      if (!drag) return;
      e.preventDefault();
      if (over?.box !== box) setOver({ box, before: null });
    },
    onDragLeave: (e: DragEvent<HTMLElement>) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null);
    },
    onDrop: (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      const key = drag ?? e.dataTransfer.getData(DRAG_TYPE);
      const before = over?.box === box ? over.before : null;
      setDrag(null);
      setOver(null);
      if (key) void move(key, box, before);
    },
  });

  const list = (box: Box, rows: TaskRow[]) => (
    <ul className={`tk-blist${over?.box === box ? " over" : ""}`} {...dropProps(box)}>
      {rows.map((t, i) => (
        <li
          key={t.key}
          className={`tk-brow${drag === t.key ? " dragging" : ""}${over?.box === box && over.before === t.key && drag !== t.key ? " drop-before" : ""}`}
          draggable={!busy}
          onDragStart={(e) => {
            e.dataTransfer.setData(DRAG_TYPE, t.key);
            e.dataTransfer.effectAllowed = "move";
            setDrag(t.key);
          }}
          onDragEnd={() => {
            setDrag(null);
            setOver(null);
          }}
          onDragOver={(e) => {
            if (!drag) return;
            e.preventDefault();
            e.stopPropagation();
            const r = e.currentTarget.getBoundingClientRect();
            const before = e.clientY > r.top + r.height / 2 ? (rows[i + 1]?.key ?? null) : t.key;
            if (over?.box !== box || over.before !== before) setOver({ box, before });
          }}
        >
          <TypeIcon type={t.type} />
          <span className={`tk-key${t.status.category === "done" ? " done" : ""}`}>{t.key}</span>
          <button type="button" className="tk-brow-title" onClick={() => onOpen(t.key)}>
            {t.title}
          </button>
          <span className="tk-brow-meta">
            <GitBadge row={t} />
            <StatusPill status={t.status} resolution={t.resolution} />
            <PointsChip points={t.story_points} />
            <RowPriority row={t} />
            <PersonAvatar person={t.assignee} />
          </span>
        </li>
      ))}
      {over?.box === box && over.before === null && drag && <li className="tk-drop-line" aria-hidden="true" />}
      {!rows.length && <li className="small faint tk-blist-empty">{box === "backlog" ? "Backlog trống" : "Kéo task vào đây để đưa vào sprint"}</li>}
    </ul>
  );

  return (
    <div className="tk-backlog-wrap">
      <EpicBar epics={epics.data} error={epics.error} value={epic} onChange={setEpic} onOpen={onOpen} />
    <div className="tk-backlog">
      {confirmEl}
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {canManage && (
        <div className="toolbar">
          <span className="spacer" />
          <button type="button" onClick={() => setDialog({ kind: "edit", sprint: null })}>
            Tạo sprint
          </button>
        </div>
      )}
      {!sprints.data ? (
        <Loading error={sprints.error} rows={3} />
      ) : (
        open.map((s) => {
          const tasks = rowsOf(s.id);
          const points = epic ? sumPoints(tasks) : (s.points ?? sumPoints(tasks));
          const donePoints = epic ? sumPoints(tasks.filter((t) => t.status.category === "done")) : (s.done_points ?? sumPoints(tasks.filter((t) => t.status.category === "done")));
          return (
            <section key={s.id} className="card tk-sprint">
              <header className="tk-sprint-head">
                <div className="tk-sprint-title">
                  <strong>{s.name}</strong>
                  {s.state === "active" ? <Pill tone="info">Đang chạy</Pill> : <Pill>Kế hoạch</Pill>}
                  <span className="small muted">
                    {s.start_date ? `${date(s.start_date)} – ${date(s.end_date)}` : "Chưa đặt ngày"} · {tasks.length} task · {donePoints}/{points} SP
                  </span>
                </div>
                {s.goal && <p className="small muted tk-sprint-goal">{s.goal}</p>}
                {canManage && (
                  <div className="actions">
                    {s.state === "planned" && (
                      <button type="button" className="sm" disabled={hasActive} title={hasActive ? "Đã có sprint đang chạy" : undefined} onClick={() => setDialog({ kind: "start", sprint: s })}>
                        Bắt đầu sprint
                      </button>
                    )}
                    {s.state === "active" && (
                      <button type="button" className="sm" onClick={() => setDialog({ kind: "complete", sprint: s })}>
                        Hoàn tất sprint
                      </button>
                    )}
                    <IconButton icon="edit" label="Sửa sprint" onClick={() => setDialog({ kind: "edit", sprint: s })} />
                    {s.state === "planned" && <IconButton icon="remove" tone="danger" label="Xoá sprint" onClick={() => void remove(s)} />}
                  </div>
                )}
              </header>
              {list(s.id, tasks)}
            </section>
          );
        })
      )}
      <section className="card tk-sprint">
        <header className="tk-sprint-head">
          <div className="tk-sprint-title">
            <strong>Backlog</strong>
            <span className="small muted">
              {backlogRows.length} task · {sumPoints(backlogRows)} SP
            </span>
          </div>
        </header>
        {!backlog.data ? <Loading error={backlog.error} rows={4} /> : list("backlog", backlogRows)}
      </section>
      {sprints.data && !open.length && !canManage && <EmptyState icon="calendar" title="Chưa có sprint nào" />}
      {dialog?.kind === "edit" && <SprintModal bp={bp} sprint={dialog.sprint} onClose={() => setDialog(null)} />}
      {dialog?.kind === "start" && dialog.sprint && <StartModal sprint={dialog.sprint} onClose={() => setDialog(null)} />}
      {dialog?.kind === "complete" && dialog.sprint && (
        <CompleteModal sprint={dialog.sprint} others={open.filter((s) => s.id !== dialog.sprint?.id)} onClose={() => setDialog(null)} />
      )}
    </div>
    </div>
  );
}

/** Story points of a sprint's work: Story / Task / Bug only (docs/specs/31 §19.1). */
function sumPoints(rows: TaskRow[]): number {
  return Math.round(rows.filter((t) => t.type === "story" || t.type === "task" || t.type === "bug").reduce((n, t) => n + (t.story_points ?? 0), 0) * 10) / 10;
}

/** The Epic bar beside the Backlog (§19.1): each epic's progress; picking one filters the lists. */
function EpicBar({
  epics,
  error,
  value,
  onChange,
  onOpen,
}: {
  epics: TaskEpic[] | undefined;
  error: unknown;
  value: string | null;
  onChange: (key: string | null) => void;
  onOpen: (key: string) => void;
}) {
  return (
    <aside className="card tk-epicbar" aria-label="Epic">
      <h3 className="tk-epicbar-title">Epic</h3>
      {!epics ? (
        <Loading error={error as never} rows={3} />
      ) : (
        <ul className="tk-epicbar-list">
          <li>
            <button type="button" className={`tk-epic-pick${value === null ? " active" : ""}`} aria-pressed={value === null} onClick={() => onChange(null)}>
              Mọi task
            </button>
          </li>
          {epics.map((e) => {
            const pct = e.total ? Math.round((e.done / e.total) * 100) : 0;
            return (
              <li key={e.key}>
                <button
                  type="button"
                  className={`tk-epic-pick${value === e.key ? " active" : ""}${e.closed ? " closed" : ""}`}
                  aria-pressed={value === e.key}
                  onClick={() => onChange(value === e.key ? null : e.key)}
                  title={`${e.done}/${e.total} task xong · còn ${e.points_open} SP`}
                >
                  <span className="tk-epic-name">{e.title}</span>
                  <span className="tk-progress" aria-hidden="true">
                    <span style={{ width: `${pct}%` }} />
                  </span>
                  <span className="small muted">
                    {pct}% · còn {e.points_open} SP
                  </span>
                </button>
                <button type="button" className="link sm tk-epic-open" onClick={() => onOpen(e.key)}>
                  {e.key}
                </button>
              </li>
            );
          })}
          {!epics.length && <li className="small faint">Chưa có epic. Tạo task loại Epic rồi đặt nó làm task cha.</li>}
        </ul>
      )}
    </aside>
  );
}

function SprintModal({ bp, sprint, onClose }: { bp: number; sprint: Sprint | null; onClose: () => void }) {
  const [name, setName] = useState(sprint?.name ?? "");
  const [goal, setGoal] = useState(sprint?.goal ?? "");
  const [start, setStart] = useState(sprint?.start_date ?? "");
  const [end, setEnd] = useState(sprint?.end_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    const body = { name: name.trim(), goal: goal.trim() || null, start_date: start || null, end_date: end || null };
    try {
      if (sprint) await api.patch(`/tasks/sprints/${sprint.id}`, body);
      else await api.post(`/tasks/spaces/${bp}/sprints`, body);
      await refreshTasks();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={sprint ? `Sửa sprint “${sprint.name}”` : "Tạo sprint"}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            {sprint ? "Lưu" : "Tạo sprint"}
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="rq-field">
          <span className="rq-label">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Sprint 12" autoFocus />
        </label>
        <label className="rq-field">
          <span className="rq-label">Mục tiêu</span>
          <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} />
        </label>
        <div className="rq-grid tk-grid-2">
          <label className="rq-field">
            <span className="rq-label">Bắt đầu</span>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="rq-field">
            <span className="rq-label">Kết thúc</span>
            <input type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} />
          </label>
        </div>
      </div>
    </Modal>
  );
}

function StartModal({ sprint, onClose }: { sprint: Sprint; onClose: () => void }) {
  const [start, setStart] = useState(sprint.start_date ?? today());
  const [end, setEnd] = useState(sprint.end_date ?? addDays(sprint.start_date ?? today(), 13));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/tasks/sprints/${sprint.id}/start`, { start_date: start, end_date: end });
      await refreshTasks();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`Bắt đầu “${sprint.name}”`}
      onClose={onClose}
      busy={busy}
      width={460}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void go()} disabled={busy || !start || !end || end < start}>
            Bắt đầu
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <p className="small muted">Story point của sprint lúc bắt đầu là cam kết, dùng cho burndown và velocity.</p>
        <div className="rq-grid tk-grid-2">
          <label className="rq-field">
            <span className="rq-label">Bắt đầu</span>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="rq-field">
            <span className="rq-label">Kết thúc</span>
            <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
          </label>
        </div>
      </div>
    </Modal>
  );
}

function CompleteModal({ sprint, others, onClose }: { sprint: Sprint; others: Sprint[]; onClose: () => void }) {
  const unfinished = (sprint.tasks ?? []).filter((t) => t.status.category !== "done").length;
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/tasks/sprints/${sprint.id}/complete`, { move_to: to ? Number(to) : null });
      await refreshTasks();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`Hoàn tất “${sprint.name}”`}
      onClose={onClose}
      busy={busy}
      width={460}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void go()} disabled={busy}>
            Hoàn tất sprint
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        {error && <Alert tone="error">{error}</Alert>}
        <p>
          {unfinished ? (
            <>
              Còn <strong>{unfinished}</strong> task chưa xong.
            </>
          ) : (
            "Mọi task của sprint đã xong."
          )}
        </p>
        {unfinished > 0 && (
          <label className="rq-field">
            <span className="rq-label">Chuyển task chưa xong sang</span>
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Backlog</option>
              {others.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </Modal>
  );
}
