"use client";

import { type DragEvent, useEffect, useMemo, useState } from "react";

import { type MultiOption, MultiSelect } from "@/components/MultiSelect";
import { Alert, Avatar, EmptyState, Loading } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Me, TaskPage, TaskRow, TaskSpace, TaskType } from "@/lib/types";

import {
  GitBadge,
  PersonAvatar,
  PointsChip,
  PriorityIcon,
  RowPriority,
  TASK_TYPES,
  TYPE_LABEL,
  TypeIcon,
  addDays,
  canMoveTo,
  confirmDone,
  errorText,
  personName,
  priorityList,
  refreshTasks,
  sortedStatuses,
  today,
  useConfirm,
  useSprints,
  useTaskPeople,
} from "./taskParts";

/** Order inside one column / list: rank, then key number. */
export function byRank(a: TaskRow, b: TaskRow): number {
  const x = a.rank ?? Number.MAX_SAFE_INTEGER;
  const y = b.rank ?? Number.MAX_SAFE_INTEGER;
  return x - y || a.id - b.id;
}

/** before_key / after_key for dropping `key` into `list` before `beforeKey` (null: at the end). */
export function neighbours(list: TaskRow[], key: string, beforeKey: string | null) {
  const rest = list.filter((t) => t.key !== key);
  const index = beforeKey ? Math.max(0, rest.findIndex((t) => t.key === beforeKey)) : rest.length;
  return { rest, index, before_key: rest[index]?.key ?? null, after_key: rest[index - 1]?.key ?? null };
}

const DRAG_TYPE = "text/x-task-key";

type BoardFilters = { assignees: number[]; priorities: string[]; type: TaskType | ""; label: string; mine: boolean };

/** Board (§13): one column per status, drag a card to change its status or order. */
export function TaskBoard({ space, me, onOpen }: { space: TaskSpace; me: Me | undefined; onOpen: (key: string) => void }) {
  const bp = space.business_project_id;
  const statuses = useMemo(() => sortedStatuses(space), [space]);
  // Kanban (§19.6): no sprint, the board is always every open task plus what was done lately.
  const kanban = space.board_mode === "kanban";
  const sprints = useSprints(kanban ? null : bp);
  const people = useTaskPeople(bp);
  const active = kanban ? null : (sprints.data?.find((s) => s.state === "active") ?? null);
  const ready = kanban || !!sprints.data || !!sprints.error;
  // With a running sprint the board is that sprint; otherwise every open task plus what was done lately.
  const sprintQ = useApi<TaskPage>(ready && active ? `/tasks?space=${bp}&sprint=active&sort=rank&limit=500` : null);
  const todoQ = useApi<TaskPage>(ready && !active ? `/tasks?space=${bp}&category=todo&sort=rank&limit=500` : null);
  const doingQ = useApi<TaskPage>(ready && !active ? `/tasks?space=${bp}&category=doing&sort=rank&limit=500` : null);
  const doneQ = useApi<TaskPage>(
    ready && !active ? `/tasks?space=${bp}&category=done&updated_from=${addDays(today(), -14)}&sort=rank&limit=500` : null,
  );
  // Memoised: the optimistic order below is dropped only when the server's answer changes.
  const loaded = useMemo(
    () =>
      active
        ? sprintQ.data?.items
        : todoQ.data && doingQ.data && doneQ.data
          ? [...todoQ.data.items, ...doingQ.data.items, ...doneQ.data.items]
          : undefined,
    [active, sprintQ.data, todoQ.data, doingQ.data, doneQ.data],
  );
  const loadError = sprints.error ?? sprintQ.error ?? todoQ.error ?? doingQ.error ?? doneQ.error;

  const [local, setLocal] = useState<TaskRow[] | null>(null);
  useEffect(() => setLocal(null), [loaded]);
  const rows = local ?? loaded;

  const [filters, setFilters] = useState<BoardFilters>({ assignees: [], priorities: [], type: "", label: "", mine: false });
  const [drag, setDrag] = useState<{ key: string; from: number } | null>(null);
  const [over, setOver] = useState<{ status: number; before: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();

  const labels = useMemo(() => [...new Set((rows ?? []).flatMap((r) => r.labels ?? []))].sort(), [rows]);
  const shown = useMemo(
    () =>
      (rows ?? []).filter(
        (r) =>
          r.type !== "epic" && // epics live in the Backlog's Epic bar (§19.1)
          (!filters.mine || r.assignee?.id === me?.id) &&
          (!filters.assignees.length || filters.assignees.includes(r.assignee?.id ?? 0)) &&
          (!filters.priorities.length || filters.priorities.includes(r.priority)) &&
          (!filters.type || r.type === filters.type) &&
          (!filters.label || (r.labels ?? []).includes(filters.label)),
      ),
    [rows, filters, me?.id],
  );
  const columns = useMemo(
    () => statuses.map((s) => ({ status: s, tasks: shown.filter((t) => t.status.id === s.id).sort(byRank) })),
    [statuses, shown],
  );

  const priorityOptions: MultiOption<string>[] = priorityList(space).map((p) => ({
    value: p.key,
    label: p.name,
    lead: <PriorityIcon name={p.name} level={p.level} color={p.color} />,
  }));
  const assigneeOptions: MultiOption<number>[] = [
    { value: 0, label: "Chưa giao" },
    ...(people.data ?? []).map((p) => ({
      value: p.id,
      label: personName(p),
      hint: p.primary_email ?? undefined,
      lead: <Avatar name={p.full_name} email={p.primary_email} src={p.avatar_url} size="sm" />,
    })),
  ];

  async function move(key: string, statusId: number, beforeKey: string | null) {
    const all = rows ?? [];
    const task = all.find((t) => t.key === key);
    const target = statuses.find((s) => s.id === statusId);
    if (!task || !target) return;
    const column = all.filter((t) => t.status.id === statusId).sort(byRank);
    const { rest, index, before_key, after_key } = neighbours(column, key, beforeKey);
    if (task.status.id === statusId && column.findIndex((t) => t.key === key) === index) return;
    if (!canMoveTo(statuses, task.status.id, statusId)) {
      setError(`Không chuyển được ${key} từ “${task.status.name}” sang “${target.name}” (không có trong luồng chuyển trạng thái).`);
      return;
    }
    if (!(await confirmDone(ask, task.git?.open_critical ?? 0, target))) return;
    const moved: TaskRow = { ...task, status: { id: target.id, name: target.name, category: target.category } };
    const ordered = [...rest.slice(0, index), moved, ...rest.slice(index)].map((t, i) => ({ ...t, rank: i }));
    const byKey = new Map(ordered.map((t) => [t.key, t]));
    setLocal(all.map((t) => byKey.get(t.key) ?? t));
    setError(null);
    try {
      await api.post(`/tasks/${encodeURIComponent(key)}/move`, { status_id: statusId, before_key, after_key });
    } catch (e) {
      setError(`Lỗi: ${errorText(e)}`);
      setLocal(null);
    }
    await refreshTasks();
  }

  const onCardDragOver = (e: DragEvent<HTMLElement>, statusId: number, list: TaskRow[], i: number) => {
    if (!drag) return;
    e.preventDefault();
    e.stopPropagation();
    const box = e.currentTarget.getBoundingClientRect();
    const lower = e.clientY > box.top + box.height / 2;
    const before = lower ? (list[i + 1]?.key ?? null) : list[i].key;
    if (over?.status !== statusId || over.before !== before) setOver({ status: statusId, before });
  };

  return (
    <div className="tk-board-wrap">
      {confirmEl}
      <div className="rq-grid tk-filters">
        <label className="rq-field">
          <span className="rq-label">Người làm</span>
          <MultiSelect label="Người làm" options={assigneeOptions} value={filters.assignees} onChange={(v) => setFilters({ ...filters, assignees: v })} empty="Mọi người" />
        </label>
        <label className="rq-field">
          <span className="rq-label">Ưu tiên</span>
          <MultiSelect label="Ưu tiên" options={priorityOptions} value={filters.priorities} onChange={(v) => setFilters({ ...filters, priorities: v })} empty="Mọi mức" />
        </label>
        <label className="rq-field">
          <span className="rq-label">Loại</span>
          <select value={filters.type} onChange={(e) => setFilters({ ...filters, type: e.target.value as TaskType | "" })}>
            <option value="">Mọi loại</option>
            {TASK_TYPES.filter((t) => space.types.includes(t)).map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </label>
        <div className="rq-field">
          <span className="rq-label">Nhãn</span>
          <div className="tk-inline">
            <select value={filters.label} onChange={(e) => setFilters({ ...filters, label: e.target.value })} aria-label="Nhãn">
              <option value="">Mọi nhãn</option>
              {labels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <label className="rq-check">
              <input type="checkbox" checked={filters.mine} onChange={(e) => setFilters({ ...filters, mine: e.target.checked })} />
              Chỉ của tôi
            </label>
          </div>
        </div>
      </div>
      <p className="small muted tk-board-scope">
        {active ? (
          <>
            Sprint đang chạy: <strong>{active.name}</strong>
            {active.end_date && <> · kết thúc {date(active.end_date)}</>}
          </>
        ) : kanban ? (
          "Kanban: board hiện mọi task chưa xong và task xong trong 14 ngày qua."
        ) : (
          "Chưa có sprint đang chạy: board hiện mọi task chưa xong và task xong trong 14 ngày qua."
        )}
      </p>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!rows ? (
        <Loading error={loadError} rows={6} />
      ) : !statuses.length ? (
        <EmptyState icon="settings" title="Dự án chưa có trạng thái nào">
          Thêm trạng thái trong tab Cài đặt.
        </EmptyState>
      ) : (
        <div className="tk-board" role="list" aria-label="Board">
          {columns.map(({ status, tasks }) => {
            const allowed = !drag || canMoveTo(statuses, drag.from, status.id);
            const points = tasks.reduce((n, t) => n + (t.story_points ?? 0), 0);
            return (
              <section
                key={status.id}
                role="listitem"
                className={`tk-col tk-cat-${status.category}${drag && !allowed ? " blocked" : ""}${over?.status === status.id ? " over" : ""}`}
                aria-label={status.name}
                onDragOver={(e) => {
                  if (!drag || !allowed) return;
                  e.preventDefault();
                  if (over?.status !== status.id) setOver({ status: status.id, before: null });
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const key = drag?.key ?? e.dataTransfer.getData(DRAG_TYPE);
                  const before = over?.status === status.id ? over.before : null;
                  setDrag(null);
                  setOver(null);
                  if (key && allowed) void move(key, status.id, before);
                }}
              >
                <header className="tk-col-head">
                  <span className="tk-col-name">{status.name}</span>
                  <span className="tab-count">{tasks.length}</span>
                  {points > 0 && <span className="small muted" title="Tổng story point">{points} SP</span>}
                </header>
                <div className="tk-col-body">
                  {tasks.map((t, i) => (
                    <div key={t.key}>
                      {over?.status === status.id && over.before === t.key && drag?.key !== t.key && <div className="tk-drop-line" />}
                      <TaskCard
                        task={t}
                        dragging={drag?.key === t.key}
                        onOpen={() => onOpen(t.key)}
                        onDragStart={(e) => {
                          e.dataTransfer.setData(DRAG_TYPE, t.key);
                          e.dataTransfer.effectAllowed = "move";
                          setDrag({ key: t.key, from: t.status.id });
                        }}
                        onDragEnd={() => {
                          setDrag(null);
                          setOver(null);
                        }}
                        onDragOver={(e) => allowed && onCardDragOver(e, status.id, tasks, i)}
                      />
                    </div>
                  ))}
                  {over?.status === status.id && over.before === null && drag && <div className="tk-drop-line" />}
                  {!tasks.length && !drag && <p className="small faint tk-col-empty">Không có task</p>}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function TaskCard({
  task,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
  onDragOver,
}: {
  task: TaskRow;
  dragging?: boolean;
  onOpen: () => void;
  onDragStart?: (e: DragEvent<HTMLElement>) => void;
  onDragEnd?: () => void;
  onDragOver?: (e: DragEvent<HTMLElement>) => void;
}) {
  return (
    <article
      className={`tk-card${dragging ? " dragging" : ""}`}
      draggable={!!onDragStart}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
    >
      <button type="button" className="tk-card-title" onClick={onOpen}>
        {task.title}
      </button>
      {(task.labels ?? []).length > 0 && (
        <div className="tk-card-labels">
          {task.labels.slice(0, 3).map((l) => (
            <span key={l} className="tk-label">
              {l}
            </span>
          ))}
          {task.labels.length > 3 && <span className="small faint">+{task.labels.length - 3}</span>}
        </div>
      )}
      <div className="tk-card-foot">
        <TypeIcon type={task.type} />
        <span className={`tk-key${task.status.category === "done" ? " done" : ""}`}>{task.key}</span>
        <GitBadge row={task} />
        <span className="spacer" />
        {task.subtasks && task.subtasks.total > 0 && (
          <span className="small muted" title="Sub-task xong / tổng">
            {task.subtasks.done}/{task.subtasks.total}
          </span>
        )}
        <PointsChip points={task.story_points} />
        <RowPriority row={task} />
        <PersonAvatar person={task.assignee} />
      </div>
    </article>
  );
}
