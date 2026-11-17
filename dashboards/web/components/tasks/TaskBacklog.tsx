"use client";

import { type DragEvent, type ReactNode, useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import { Alert, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Sprint, SprintPlan, SprintPreflight, SprintReview, TaskEpic, TaskPage, TaskRow, TaskSpace } from "@/lib/types";

import { useDrawerList } from "./drawerNav";
import { byRank, neighbours } from "./TaskBoard";
import { RetroButton, ScopeNote, SprintReviewView } from "./TaskHealth";
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
/** Per-browser memory of the folded Epic column (docs/specs/31 §22.5). */
const EPIC_FOLD_KEY = "codepulse.tasks.backlog.epicCollapsed";

function readEpicFold(): boolean {
  try {
    return window.localStorage.getItem(EPIC_FOLD_KEY) === "1";
  } catch {
    return false;
  }
}
function writeEpicFold(v: boolean) {
  try {
    window.localStorage.setItem(EPIC_FOLD_KEY, v ? "1" : "0");
  } catch {
    // Storage blocked (private window): the fold just is not remembered.
  }
}

/**
 * Backlog (§13, layout §22.5): a 40 px toolbar, then the Epic column (foldable) beside the sprints
 * and the backlog; only that area scrolls. Rows drag between sprints and the backlog.
 */
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
  // Folded Epic column, read after mount so the server render matches the first client render.
  const [epicFolded, setEpicFolded] = useState(false);
  useEffect(() => setEpicFolded(readEpicFold()), []);
  const foldEpics = (v: boolean) => {
    setEpicFolded(v);
    writeEpicFold(v);
  };
  // A sprint's (or the backlog's) open/folded choice; unset = open when it has rows, folded when empty.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const isOpen = (box: Box, rows: TaskRow[]) => expanded[String(box)] ?? rows.length > 0;
  const toggle = (box: Box, rows: TaskRow[]) => setExpanded((m) => ({ ...m, [String(box)]: !isOpen(box, rows) }));
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
  // §22.6: J/K in the task drawer walk the sprints top-down, then the backlog.
  useDrawerList([...open.flatMap((s) => (s.tasks ?? []).filter(shows).sort(byRank)), ...backlogRows].map((t) => t.key));
  const hasActive = open.some((s) => s.state === "active");
  const pickedEpic = epic ? epics.data?.find((e) => e.key === epic) : undefined;

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

  /** The rows of a box; a folded box shows only a drop zone, and only while a row is dragged. */
  const list = (box: Box, rows: TaskRow[]) => {
    if (!isOpen(box, rows)) {
      if (!drag) return null;
      return (
        <ul className={`tk-blist bl-dropzone${over?.box === box ? " over" : ""}`} {...dropProps(box)}>
          <li className="small muted bl-drop-hint">Kéo task vào đây</li>
        </ul>
      );
    }
    return (
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
            <button type="button" className="tk-brow-title" onClick={() => onOpen(t.key)} title={t.title}>
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
        {!rows.length &&
          (drag ? (
            <li className="small muted bl-drop-hint">Kéo task vào đây</li>
          ) : (
            <li className="small faint tk-blist-empty">{box === "backlog" ? "Backlog trống" : epic ? "Không có task của epic này" : "Chưa có task"}</li>
          ))}
      </ul>
    );
  };

  return (
    <div className="bl-screen">
      {confirmEl}
      <div className="bl-toolbar" role="toolbar" aria-label="Backlog">
        {pickedEpic && (
          <span className="bl-chip" title={pickedEpic.title}>
            <span className="bl-chip-text">Epic: {pickedEpic.title}</span>
            <button type="button" className="bl-chip-x" aria-label={`Bỏ lọc Epic: ${pickedEpic.title}`} onClick={() => setEpic(null)}>
              <Icon name="close" size={12} />
            </button>
          </span>
        )}
        <span className="small muted bl-toolbar-count">
          {sprints.data ? `${open.length} sprint` : ""}
          {backlog.data ? ` · ${backlogRows.length} task trong backlog` : ""}
        </span>
        <span className="spacer" />
        {canManage && (
          <button type="button" className="sm" onClick={() => setDialog({ kind: "edit", sprint: null })}>
            <Icon name="plus" size={14} /> Tạo sprint
          </button>
        )}
      </div>
      <div className={`tk-backlog-wrap${epicFolded ? " bl-epic-folded" : ""}`}>
        <EpicBar
          epics={epics.data}
          error={epics.error}
          value={epic}
          onChange={setEpic}
          onOpen={onOpen}
          folded={epicFolded}
          onFold={foldEpics}
        />
        <div className="tk-backlog bl-scroll">
          {message && <Message text={message} onClose={() => setMessage(null)} />}
          {!sprints.data ? (
            <Loading error={sprints.error} rows={3} />
          ) : (
            open.map((s) => {
              const tasks = rowsOf(s.id);
              const points = epic ? sumPoints(tasks) : (s.points ?? sumPoints(tasks));
              const donePoints = epic ? sumPoints(tasks.filter((t) => t.status.category === "done")) : (s.done_points ?? sumPoints(tasks.filter((t) => t.status.category === "done")));
              return (
                <SprintSection
                  key={s.id}
                  sprint={s}
                  taskCount={tasks.length}
                  points={points}
                  donePoints={donePoints}
                  expanded={isOpen(s.id, tasks)}
                  onToggle={() => toggle(s.id, tasks)}
                  canManage={canManage}
                  hasActive={hasActive}
                  onStart={() => setDialog({ kind: "start", sprint: s })}
                  onComplete={() => setDialog({ kind: "complete", sprint: s })}
                  onEdit={() => setDialog({ kind: "edit", sprint: s })}
                  onRemove={() => void remove(s)}
                >
                  {list(s.id, tasks)}
                </SprintSection>
              );
            })
          )}
          {sprints.data && !open.length && <p className="small muted bl-note">Chưa có sprint nào{canManage ? ". Bấm “Tạo sprint” ở trên để lập sprint." : "."}</p>}
          <section className={`card tk-sprint${isOpen("backlog", backlogRows) ? "" : " bl-folded"}`} aria-label="Backlog">
            <header className="bl-sprint-head">
              <FoldButton open={isOpen("backlog", backlogRows)} name="Backlog" onClick={() => toggle("backlog", backlogRows)} />
              <strong className="bl-sprint-name">Backlog</strong>
              <span className="small muted bl-sprint-facts">
                {backlogRows.length} task · {sumPoints(backlogRows)} SP
              </span>
            </header>
            {!backlog.data ? <Loading error={backlog.error} rows={4} /> : list("backlog", backlogRows)}
          </section>
        </div>
      </div>
      {dialog?.kind === "edit" && <SprintModal bp={bp} sprint={dialog.sprint} onClose={() => setDialog(null)} />}
      {dialog?.kind === "start" && dialog.sprint && <StartModal sprint={dialog.sprint} onClose={() => setDialog(null)} />}
      {dialog?.kind === "complete" && dialog.sprint && (
        <CompleteModal sprint={dialog.sprint} others={open.filter((s) => s.id !== dialog.sprint?.id)} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}

function FoldButton({ open, name, onClick }: { open: boolean; name: string; onClick: () => void }) {
  return (
    <button type="button" className="icon-btn bl-fold" aria-expanded={open} aria-label={open ? `Thu gọn ${name}` : `Mở ${name}`} data-tip={open ? "Thu gọn" : "Mở"} onClick={onClick}>
      <Icon name={open ? "chevronDown" : "chevronRight"} size={16} />
    </button>
  );
}

/**
 * One sprint (§22.5): a single 40 px header row (name, dates, task count, SP against capacity,
 * Bắt đầu / Hoàn tất, ⋯ for Sửa / Xoá). The goal and the planning line (§21.3: capacity, load per
 * person) sit on a sub-line opened from the SP chip.
 */
function SprintSection({
  sprint: s,
  taskCount,
  points,
  donePoints,
  expanded,
  onToggle,
  canManage,
  hasActive,
  onStart,
  onComplete,
  onEdit,
  onRemove,
  children,
}: {
  sprint: Sprint;
  taskCount: number;
  points: number;
  donePoints: number;
  expanded: boolean;
  onToggle: () => void;
  canManage: boolean;
  hasActive: boolean;
  onStart: () => void;
  onComplete: () => void;
  onEdit: () => void;
  onRemove: () => void;
  children: ReactNode;
}) {
  const plan = useApi<SprintPlan>(`/tasks/sprints/${s.id}/plan`);
  const [details, setDetails] = useState(false);
  const p = plan.data;
  // No capacity yet and the viewer can set one: the sub-line holds the input, so say so on the chip.
  const needsCapacity = canManage && p != null && p.capacity === null;
  const capText = p ? (p.capacity !== null ? `${p.points}/${p.capacity} SP` : `${p.points} SP`) : `${donePoints}/${points} SP`;
  const capTip = p
    ? `${p.capacity !== null ? `${p.points} SP kéo vào / sức chứa ${p.capacity} SP (${p.source === "manual" ? "đặt tay" : "TB 3 sprint gần nhất"})` : `${p.points} SP, chưa có sức chứa`} · xong ${donePoints}/${points} SP. Bấm để xem tải theo người${canManage ? " và đặt sức chứa" : ""}.`
    : `Xong ${donePoints}/${points} SP`;
  const dates = s.start_date ? `${date(s.start_date)} – ${date(s.end_date)}` : "Chưa đặt ngày";
  const menu = canManage
    ? [
        { icon: "edit" as const, label: "Sửa sprint", run: onEdit },
        ...(s.state === "planned" ? [{ icon: "remove" as const, label: "Xoá sprint", danger: true, run: onRemove }] : []),
      ]
    : [];
  return (
    <section className={`card tk-sprint${expanded ? "" : " bl-folded"}`} aria-label={s.name}>
      <header className="bl-sprint-head">
        <FoldButton open={expanded} name={s.name} onClick={onToggle} />
        <strong className="bl-sprint-name" title={s.goal ? `Mục tiêu: ${s.goal}` : s.name}>
          {s.name}
        </strong>
        {s.state === "active" && <Pill tone="info">Đang chạy</Pill>}
        <span className="small muted bl-sprint-facts">
          <span className="bl-sprint-dates">{dates} · </span>
          {taskCount} task
        </span>
        <button
          type="button"
          className={`bl-cap${p?.over_capacity ? " over" : ""}${needsCapacity ? " todo" : ""}`}
          aria-expanded={details}
          aria-label={`${capText}. ${details ? "Ẩn" : "Hiện"} mục tiêu, sức chứa và tải theo người`}
          title={capTip}
          onClick={() => setDetails((v) => !v)}
        >
          {capText}
          {p?.over_capacity && <Icon name="alert" size={12} />}
          {needsCapacity && <span className="bl-cap-todo">· đặt sức chứa</span>}
        </button>
        {s.state === "active" && <ScopeNote sprintId={s.id} />}
        <span className="spacer" />
        {canManage && s.state === "planned" && (
          <button type="button" className="sm" disabled={hasActive} title={hasActive ? "Đã có sprint đang chạy" : undefined} onClick={onStart}>
            Bắt đầu
          </button>
        )}
        {canManage && s.state === "active" && (
          <button type="button" className="sm" onClick={onComplete}>
            Hoàn tất
          </button>
        )}
        <MoreMenu items={menu} />
      </header>
      {details && (
        <div className="bl-sprint-sub">
          {s.goal && (
            <p className="small bl-sprint-goal">
              <span className="muted">Mục tiêu:</span> {s.goal}
            </p>
          )}
          <SprintPlanBar sprintId={s.id} plan={plan} canManage={canManage} />
        </div>
      )}
      {children}
    </section>
  );
}

/**
 * Planning line of a sprint (docs/specs/31 §21.3): "10 / 18 SP · sức chứa = TB 3 sprint gần nhất",
 * a capacity typed in when there is no history (or to override it), and the load per person.
 */
function SprintPlanBar({ sprintId, plan, canManage }: { sprintId: number; plan: ReturnType<typeof useApi<SprintPlan>>; canManage: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = plan.data;
  async function save(points: number | null) {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/tasks/sprints/${sprintId}/capacity`, { capacity_points: points });
      setEditing(false);
      await plan.mutate();
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }
  if (plan.error && !p)
    return (
      <div className="rm-plan small">
        <span className="tk-over-capacity">Không tải được sức chứa</span>
        <button type="button" className="ghost sm" onClick={() => void plan.mutate()}>
          Thử lại
        </button>
      </div>
    );
  if (!p) return <div className="rm-plan rm-plan-skeleton" aria-hidden="true" />;
  const inputOpen = canManage && (editing || p.capacity === null);
  const assigned = p.load.filter((r) => r.points > 0);
  return (
    <div className="rm-plan">
      <div className="rm-plan-line small">
        {p.capacity !== null ? (
          <span className={p.over_capacity ? "tk-over-capacity" : undefined}>
            <strong>
              {p.points} / {p.capacity} SP
            </strong>{" "}
            · sức chứa {p.source === "manual" ? "đặt tay" : "= TB 3 sprint gần nhất"}
            {p.over_capacity ? ": đang kéo vào nhiều hơn" : ""}
          </span>
        ) : (
          <span className="muted">
            <strong>{p.points} SP</strong> · chưa có sức chứa (cần 3 sprint đã đóng{canManage ? ", hoặc nhập tay" : ""})
          </span>
        )}
        {inputOpen ? (
          <form
            className="rm-cap-form"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Number(value);
              if (value.trim() && Number.isFinite(n) && n >= 0) void save(n);
            }}
          >
            <label className="visually-hidden" htmlFor={`cap-${sprintId}`}>
              Sức chứa (SP)
            </label>
            <input id={`cap-${sprintId}`} type="number" min={0} step={0.5} inputMode="decimal" placeholder="Sức chứa SP" value={value} onChange={(e) => setValue(e.target.value)} disabled={busy} />
            <button type="submit" className="sm" disabled={busy || !value.trim()}>
              Lưu
            </button>
            {editing && (
              <button type="button" className="ghost sm" onClick={() => setEditing(false)} disabled={busy}>
                Huỷ
              </button>
            )}
          </form>
        ) : (
          canManage && (
            <>
              <IconButton
                icon="edit"
                label="Đặt sức chứa tay"
                size={14}
                onClick={() => {
                  setValue(p.capacity !== null ? String(p.capacity) : "");
                  setEditing(true);
                }}
              />
              {p.source === "manual" && p.auto !== null && (
                <button type="button" className="link sm" onClick={() => void save(null)} disabled={busy}>
                  Dùng TB 3 sprint ({p.auto} SP)
                </button>
              )}
            </>
          )
        )}
        {error && <span className="tk-over-capacity">Lỗi: {error}</span>}
      </div>
      {assigned.length > 0 && (
        <ul className="rm-load" aria-label="Tải theo người">
          {p.load.map((r) => (
            <li key={r.user?.id ?? "none"} className={r.over ? "over" : undefined} title={`${r.name}: ${r.points} SP, ${r.tasks} task, ${r.pct}% SP của sprint${r.over ? ` (hơn ${p.overload_pct}%)` : ""}`}>
              <span className="rm-load-name">{r.name}</span>
              <span className="rm-load-bar" aria-hidden="true">
                <span style={{ width: `${Math.min(100, r.pct)}%` }} />
              </span>
              <span className="rm-load-num">
                {r.points} SP · {r.pct}%{r.over && <Icon name="alert" size={12} />}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Story points of a sprint's work: Story / Task / Bug only (docs/specs/31 §19.1). */
function sumPoints(rows: TaskRow[]): number {
  return Math.round(rows.filter((t) => t.type === "story" || t.type === "task" || t.type === "bug").reduce((n, t) => n + (t.story_points ?? 0), 0) * 10) / 10;
}

/**
 * The Epic column beside the Backlog (§19.1, §22.5): 220 px, each epic's progress; picking one
 * filters the lists. Folds (⟨⟨) to a 40 px strip, remembered per browser.
 */
function EpicBar({
  epics,
  error,
  value,
  onChange,
  onOpen,
  folded,
  onFold,
}: {
  epics: TaskEpic[] | undefined;
  error: unknown;
  value: string | null;
  onChange: (key: string | null) => void;
  onOpen: (key: string) => void;
  folded: boolean;
  onFold: (folded: boolean) => void;
}) {
  if (folded)
    return (
      <aside className="card tk-epicbar bl-epic-strip" aria-label="Epic (đã thu gọn)">
        <IconButton icon="chevronsRight" label="Mở cột Epic" onClick={() => onFold(false)} />
        <span className="bl-epic-strip-label" aria-hidden="true">
          Epic{value ? ` · ${value}` : ""}
        </span>
      </aside>
    );
  return (
    <aside className="card tk-epicbar" aria-label="Epic">
      <h3 className="tk-epicbar-title">Epic</h3>
      {!epics ? (
        <Loading error={error as never} rows={3} />
      ) : (
        <ul className="tk-epicbar-list">
          <li>
            <button type="button" className={`tk-epic-pick${value === null ? " active" : ""}`} aria-pressed={value === null} onClick={() => onChange(null)}>
              <span className="tk-epic-name">Mọi task</span>
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
                  title={`${e.title}: ${e.done}/${e.total} task xong · còn ${e.points_open} SP`}
                >
                  <span className="tk-epic-name">{e.title}</span>
                  <span className="tk-progress" aria-hidden="true">
                    <span style={{ width: `${pct}%` }} />
                  </span>
                  <span className="small muted">
                    {pct}% · còn {e.points_open} SP
                  </span>
                </button>
                <button type="button" className="link sm tk-epic-open" aria-label={`Mở epic ${e.key}`} onClick={() => onOpen(e.key)}>
                  {e.key}
                </button>
              </li>
            );
          })}
          {!epics.length && <li className="small faint">Chưa có epic. Tạo task loại Epic rồi đặt nó làm task cha.</li>}
        </ul>
      )}
      <div className="bl-epic-foot">
        <IconButton icon="chevronsLeft" label="Thu gọn cột Epic" onClick={() => onFold(true)} />
      </div>
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
  // docs/specs/31 §21.3: warnings before confirming; starting anyway stays allowed.
  const pre = useApi<SprintPreflight>(`/tasks/sprints/${sprint.id}/preflight`);
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
      width={560}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void go()} disabled={busy || !start || !end || end < start}>
            {pre.data && !pre.data.ok ? "Vẫn bắt đầu" : "Bắt đầu"}
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
        <Preflight pre={pre} />
      </div>
    </Modal>
  );
}

/** The preflight of a sprint about to start: tasks without estimate / assignee, blocked from outside. */
function Preflight({ pre }: { pre: { data?: SprintPreflight; error?: Error; mutate: () => Promise<unknown> } }) {
  if (pre.error && !pre.data)
    return (
      <div className="th-error">
        <Loading error={pre.error} />
        <button type="button" className="ghost sm" onClick={() => void pre.mutate()}>
          Thử lại
        </button>
      </div>
    );
  if (!pre.data) return <Loading rows={2} />;
  if (pre.data.ok) return <Alert tone="success">Không có cảnh báo: mọi task đã ước lượng, có người làm và không bị chặn từ ngoài sprint.</Alert>;
  return (
    <Alert tone="warning">
      <strong>Kiểm tra trước khi bắt đầu</strong>
      <ul className="rm-preflight">
        {pre.data.warnings.map((w) => (
          <li key={w.kind}>
            {w.text}
            {w.tasks.length > 0 && (
              <span className="small muted">
                {": "}
                {w.tasks
                  .slice(0, 8)
                  .map((t) => (t.blocker ? `${t.key} ← ${t.blocker.key}` : t.key))
                  .join(", ")}
                {w.count > 8 ? ` +${w.count - 8}` : ""}
              </span>
            )}
          </li>
        ))}
      </ul>
      <span className="small muted">Vẫn bắt đầu được; có thể sửa sau trong sprint.</span>
    </Alert>
  );
}

function CompleteModal({ sprint, others, onClose }: { sprint: Sprint; others: Sprint[]; onClose: () => void }) {
  const unfinished = (sprint.tasks ?? []).filter((t) => t.status.category !== "done").length;
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // docs/specs/31 §20.6: the review before confirming; after closing, the same review is kept
  // (the close moves unfinished tasks without history, so a re-read would lose their list).
  const review = useApi<SprintReview>(`/tasks/sprints/${sprint.id}/review`);
  const [closed, setClosed] = useState<SprintReview | null>(null);
  const shown = closed ?? review.data;
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/tasks/sprints/${sprint.id}/complete`, { move_to: to ? Number(to) : null });
      if (review.data) setClosed({ ...review.data, sprint: { ...review.data.sprint, state: "closed" } });
      await refreshTasks();
      if (!review.data) onClose();
      setBusy(false);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={closed ? `Đã hoàn tất “${sprint.name}”` : `Hoàn tất “${sprint.name}”`}
      onClose={onClose}
      busy={busy}
      width={720}
      footer={
        closed ? (
          <>
            <RetroButton review={closed} />
            <button type="button" className="primary" onClick={onClose}>
              Đóng
            </button>
          </>
        ) : (
          <>
            <button type="button" className="ghost" onClick={onClose} disabled={busy}>
              Huỷ
            </button>
            <button type="button" className="primary" onClick={() => void go()} disabled={busy}>
              Hoàn tất sprint
            </button>
          </>
        )
      }
    >
      <div className="adm-form tk-form">
        {error && <Alert tone="error">{error}</Alert>}
        <section className="sr-modal-review" aria-label="Đánh giá sprint">
          {review.error && !shown ? (
            <div className="th-error">
              <Loading error={review.error} />
              <button type="button" className="ghost sm" onClick={() => void review.mutate()}>
                Thử lại
              </button>
            </div>
          ) : !shown ? (
            <Loading rows={2} />
          ) : (
            <SprintReviewView review={shown} />
          )}
        </section>
        {closed ? (
          <p className="small muted">
            Sprint đã đóng. {closed.can_create_retro ? "Tạo trang retro để ghi điều tốt, cần cải thiện và hành động vào Tài liệu dự án." : ""}
          </p>
        ) : (
          <>

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
          </>
        )}
      </div>
    </Modal>
  );
}
