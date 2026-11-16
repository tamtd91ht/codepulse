"use client";

import { type DragEvent, useEffect, useMemo, useState } from "react";

import { type MultiOption, MultiSelect } from "@/components/MultiSelect";
import { Alert, Avatar, EmptyState, Loading } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Me, TaskPage, TaskPerson, TaskResolution, TaskRow, TaskSpace, TaskStatusRef, TaskType } from "@/lib/types";

import { DensityToggle } from "./DensityToggle";
import { type QuickPicks, QuickChips } from "./QuickChips";
import { SubtaskGroup, readGroupSubs, writeGroupSubs } from "./SubtaskGroup";
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
  RESOLUTION_LABEL,
  RESOLUTIONS,
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

/** Swimlanes (docs/specs/31 §19.3): rows of the board grouped by one field. */
type LaneMode = "none" | "epic" | "parent" | "assignee" | "priority";
const LANE_LABEL: Record<LaneMode, string> = {
  none: "Không chia",
  epic: "Theo epic",
  parent: "Theo task cha",
  assignee: "Theo người làm",
  priority: "Theo ưu tiên",
};
const LANE_KEY = "tk-board-lane";

function readLane(): LaneMode {
  try {
    const v = localStorage.getItem(LANE_KEY);
    return v && v in LANE_LABEL ? (v as LaneMode) : "none";
  } catch {
    return "none";
  }
}

/** Hours since the task entered its status. */
function hoursIn(t: TaskRow): number | null {
  if (!t.status_changed_at) return null;
  return (Date.now() - new Date(t.status_changed_at).getTime()) / 3_600_000;
}

/** One way to read an ambiguous quick-create token (§20.2): the chips under the input. */
export type QuickChoice = { value: number | string; label: string; hint?: string };
export type QuickAmbiguity = { token: string; field: "assignee_id" | "priority"; options: QuickChoice[] };
export type QuickParsed = {
  title: string;
  assignee_id?: number;
  type?: TaskType;
  priority?: string;
  story_points?: number;
  parent_key?: string;
  /** Tokens matching more than one value: the user picks (nothing is guessed). */
  ambiguous: QuickAmbiguity[];
};

/**
 * Quick create (§19.3, §20.2): "Sửa lỗi OTP @an #bug !cao 3sp ^OCX-12". @ a person (email name or
 * a word of the name), # a type, ! a priority (its name or key, accents optional), "3sp" story
 * points, ^ the parent task (its key, or only the number in this dự án). A token matching several
 * people (or priorities of one level) comes back in `ambiguous` with its candidates.
 */
export function parseQuick(text: string, people: TaskPerson[], space: TaskSpace): QuickParsed {
  const fold = (v: string) =>
    v
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/đ/g, "d")
      .replace(/Đ/g, "D")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  const out: QuickParsed = { title: "", ambiguous: [] };
  const words: string[] = [];
  for (const word of text.trim().split(/\s+/)) {
    const sp = /^(\d+(?:[.,]\d+)?)sp$/i.exec(word);
    if (sp) {
      out.story_points = Number(sp[1].replace(",", "."));
      continue;
    }
    const parent = /^\^(?:([a-z][a-z0-9]*)-)?(\d+)$/i.exec(word);
    if (parent && (parent[1] || space.key)) {
      out.parent_key = `${(parent[1] ?? space.key ?? "").toUpperCase()}-${parent[2]}`;
      continue;
    }
    if (word.startsWith("@") && word.length > 1) {
      const w = fold(word.slice(1));
      const byMail = people.filter((p) => fold((p.primary_email ?? "").split("@")[0]) === w);
      const byName = people.filter((p) => fold(p.full_name ?? "").split("-").includes(w));
      // Only a sure match: Vietnamese names share words ("@van", "@anh"); otherwise ask.
      const who = byMail.length === 1 ? byMail[0] : !byMail.length && byName.length === 1 ? byName[0] : null;
      if (who) {
        out.assignee_id = who.id;
        continue;
      }
      const all = [...new Map([...byMail, ...byName].map((p) => [p.id, p])).values()];
      if (all.length > 1) {
        out.ambiguous.push({
          token: word,
          field: "assignee_id",
          options: all.map((p) => ({ value: p.id, label: p.full_name || p.primary_email || `#${p.id}`, hint: p.primary_email ?? undefined })),
        });
        continue;
      }
    }
    if (word.startsWith("#") && word.length > 1) {
      const w = fold(word.slice(1)).replace("sub-task", "subtask");
      const type = TASK_TYPES.find((t) => t === w || fold(TYPE_LABEL[t]) === w);
      if (type && space.types.includes(type)) {
        out.type = type;
        continue;
      }
    }
    if (word.startsWith("!") && word.length > 1) {
      const w = fold(word.slice(1));
      const list = priorityList(space);
      const exact = list.find((p) => p.key === w || fold(p.name) === w);
      const byLevel = list.filter((p) => p.level === w);
      if (exact || byLevel.length === 1) {
        out.priority = (exact ?? byLevel[0]).key;
        continue;
      }
      if (byLevel.length > 1) {
        out.ambiguous.push({ token: word, field: "priority", options: byLevel.map((p) => ({ value: p.key, label: p.name })) });
        continue;
      }
    }
    words.push(word);
  }
  out.title = words.join(" ").trim();
  return out;
}

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
  const [over, setOver] = useState<{ status: number; before: string | null; lane: string } | null>(null);
  const [lane, setLaneState] = useState<LaneMode>("none");
  useEffect(() => setLaneState(readLane()), []);
  const setLane = (v: LaneMode) => {
    setLaneState(v);
    try {
      localStorage.setItem(LANE_KEY, v);
    } catch {
      /* a convenience only */
    }
  };
  const [quick, setQuick] = useState<Record<number, string>>({});
  const [quickBusy, setQuickBusy] = useState(false);
  // Ambiguous quick-create lines waiting for a pick, per column (§20.2).
  const [quickAsk, setQuickAsk] = useState<Record<number, { text: string; parsed: QuickParsed; picks: QuickPicks }>>({});
  // "Gom sub-task vào card cha" (§20.2): off by default, remembered per browser.
  const [groupSubs, setGroupSubsState] = useState(false);
  useEffect(() => setGroupSubsState(readGroupSubs()), []);
  const setGroupSubs = (on: boolean) => {
    setGroupSubsState(on);
    writeGroupSubs(on);
  };
  const [error, setError] = useState<string | null>(null);
  // The task just dropped into Done: closed as "Đã xong", another reason one click away.
  const [closed, setClosed] = useState<string | null>(null);
  async function reclose(key: string, resolution: TaskResolution) {
    try {
      await api.patch(`/tasks/${encodeURIComponent(key)}`, { resolution });
      await refreshTasks();
    } catch (e) {
      setError(`Lỗi: ${errorText(e)}`);
    }
    setClosed(null);
  }
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
  // Per column, across lanes: the WIP count (§19.3).
  const colCount = useMemo(() => {
    const n = new Map<number, number>();
    for (const t of shown) n.set(t.status.id, (n.get(t.status.id) ?? 0) + 1);
    return n;
  }, [shown]);
  // Grouped sub-tasks (§20.2): a sub-task whose parent is on the board folds into the parent's
  // card; one whose parent is not stays a card. Not with "Theo task cha" lanes (already grouped).
  const grouping = groupSubs && lane !== "parent";
  const { cards, subsOf } = useMemo(() => {
    const subsOf = new Map<string, TaskRow[]>();
    if (!grouping) return { cards: shown, subsOf };
    const onBoard = new Set(shown.map((t) => t.key));
    for (const t of rows ?? []) {
      if (t.type !== "subtask" || !t.parent_key || !onBoard.has(t.parent_key)) continue;
      subsOf.set(t.parent_key, [...(subsOf.get(t.parent_key) ?? []), t]);
    }
    for (const list of subsOf.values()) list.sort((a, b) => a.id - b.id);
    return { cards: shown.filter((t) => !(t.type === "subtask" && t.parent_key && onBoard.has(t.parent_key))), subsOf };
  }, [grouping, shown, rows]);
  const lanes = useMemo(() => {
    const shown = cards;
    if (lane === "none") return [{ id: "all", label: "", rows: shown }];
    const byKey = new Map((rows ?? []).map((r) => [r.key, r]));
    const groups = new Map<string, { id: string; label: string; order: number; rows: TaskRow[] }>();
    const put = (id: string, label: string, order: number, t: TaskRow) => {
      const g = groups.get(id) ?? { id, label, order, rows: [] };
      g.rows.push(t);
      groups.set(id, g);
    };
    const prios = priorityList(space);
    for (const t of shown) {
      if (lane === "assignee") put(`a${t.assignee?.id ?? 0}`, t.assignee ? personName(t.assignee) : "Chưa giao", t.assignee ? 0 : 1, t);
      else if (lane === "priority") {
        const i = prios.findIndex((p) => p.key === t.priority);
        put(`p${t.priority}`, t.priority_name ?? t.priority, i < 0 ? 99 : i, t);
      } else if (lane === "parent") {
        const parent = t.type === "subtask" ? t.parent_key : t.subtasks && t.subtasks.total > 0 ? t.key : null;
        const head = parent ? byKey.get(parent) : undefined;
        put(parent ? `t${parent}` : "t-", parent ? `${parent}${head ? ` · ${head.title}` : ""}` : "Task không có sub-task", parent ? 0 : 1, t);
      } else {
        const up = t.type === "subtask" && t.parent_key ? byKey.get(t.parent_key) : t;
        const epic = up?.parent_key ?? null;
        const head = epic ? byKey.get(epic) : undefined;
        put(epic ? `e${epic}` : "e-", epic ? `${epic}${head ? ` · ${head.title}` : ""}` : "Không thuộc epic", epic ? 0 : 1, t);
      }
    }
    return [...groups.values()].sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
  }, [lane, cards, rows, space]);

  const dropAsk = (statusId: number) =>
    setQuickAsk((a) => {
      const { [statusId]: _, ...rest } = a;
      return rest;
    });

  /**
   * Enter in a column's quick-create line (§19.3, §20.2). An unambiguous line saves at once; one with
   * an ambiguous token shows chips to pick from, and saves once each token has its pick.
   */
  async function quickCreate(status: TaskStatusRef & { id: number }) {
    const text = (quick[status.id] ?? "").trim();
    if (!text) return;
    const waiting = quickAsk[status.id];
    let parsed: QuickParsed;
    if (waiting && waiting.text === text) {
      if (!waiting.parsed.ambiguous.every((a) => a.token in waiting.picks)) return;
      parsed = { ...waiting.parsed };
      for (const a of waiting.parsed.ambiguous) {
        const v = waiting.picks[a.token];
        if (v === null || v === undefined) continue;
        if (a.field === "assignee_id") parsed.assignee_id = Number(v);
        else parsed.priority = String(v);
      }
    } else {
      parsed = parseQuick(text, people.data ?? [], space);
      if (parsed.ambiguous.length) {
        setError(null);
        setQuickAsk((a) => ({ ...a, [status.id]: { text, parsed, picks: {} } }));
        return;
      }
    }
    const { ambiguous: _, parent_key, ...fields } = parsed;
    if (!fields.title) {
      setError("Cần tiêu đề: ví dụ “Sửa lỗi OTP @an #bug !cao 3sp ^OCX-12”.");
      return;
    }
    setQuickBusy(true);
    setError(null);
    try {
      let type = fields.type;
      if (parent_key) {
        // ^KEY: a sub-task of a story / task / bug when the dự án has sub-tasks; under an epic the
        // type stays as typed (or the default).
        const parent = (rows ?? []).find((t) => t.key === parent_key) ?? (await api.get<TaskRow>(`/tasks/${encodeURIComponent(parent_key)}`).catch(() => null));
        const wrong = !parent ? `Không tìm thấy task cha ${parent_key} trong dự án.` : parent.type === "subtask" ? `${parent_key} là sub-task, không làm task cha được.` : null;
        if (!parent || wrong) {
          setError(wrong);
          setQuickBusy(false);
          return;
        }
        if (!type && parent.type !== "epic" && space.types.includes("subtask")) type = "subtask";
      }
      await api.post("/tasks", { space: bp, ...fields, type, parent_key, status_id: status.id, sprint_id: active?.id ?? undefined });
      setQuick((q) => ({ ...q, [status.id]: "" }));
      dropAsk(status.id);
      await refreshTasks();
    } catch (e) {
      setError(`Lỗi: ${errorText(e)}`);
    }
    setQuickBusy(false);
  }

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
      if (target.category === "done" && task.status.category !== "done") setClosed(key);
    } catch (e) {
      setError(`Lỗi: ${errorText(e)}`);
      setLocal(null);
    }
    await refreshTasks();
  }

  const onCardDragOver = (e: DragEvent<HTMLElement>, statusId: number, list: TaskRow[], i: number, laneId: string) => {
    if (!drag) return;
    e.preventDefault();
    e.stopPropagation();
    const box = e.currentTarget.getBoundingClientRect();
    const lower = e.clientY > box.top + box.height / 2;
    // "Before" is in the whole column (lanes only show part of it): below a lane's last card is
    // before the card that follows it in the column, not the column's end.
    let before: string | null = list[i].key;
    if (lower) {
      const column = (rows ?? []).filter((t) => t.status.id === statusId).sort(byRank);
      const at = column.findIndex((t) => t.key === list[i].key);
      before = column[at + 1]?.key ?? null;
    }
    if (over?.status !== statusId || over.before !== before || over.lane !== laneId) setOver({ status: statusId, before, lane: laneId });
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
        <label className="rq-field">
          <span className="rq-label">Swimlane</span>
          <select value={lane} onChange={(e) => setLane(e.target.value as LaneMode)}>
            {(Object.keys(LANE_LABEL) as LaneMode[]).map((m) => (
              <option key={m} value={m}>
                {LANE_LABEL[m]}
              </option>
            ))}
          </select>
        </label>
        {space.types.includes("subtask") && (
          <div className="rq-field">
            <span className="rq-label">Sub-task</span>
            <label className="rq-check" title={lane === "parent" ? "“Theo task cha” đã gom sub-task theo lane" : "Ẩn card sub-task có task cha trên board; card cha hiện tiến độ"}>
              <input type="checkbox" checked={groupSubs} disabled={lane === "parent"} onChange={(e) => setGroupSubs(e.target.checked)} />
              Gom sub-task vào card cha
            </label>
          </div>
        )}
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
        <DensityToggle view="board" />
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
      {closed && (
        <Alert tone="success" onClose={() => setClosed(null)}>
          {closed} đã đóng với lý do “Đã xong”. Lý do khác:{" "}
          {RESOLUTIONS.filter((r) => r !== "done").map((r) => (
            <button key={r} type="button" className="link sm" onClick={() => void reclose(closed, r)}>
              {RESOLUTION_LABEL[r]}
            </button>
          ))}
        </Alert>
      )}
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
        <>
          {lanes.map((ln) => (
            <section key={ln.id} className={lane === "none" ? "tk-lane-none" : "tk-lane"} aria-label={ln.label || "Board"}>
              {lane !== "none" && (
                <h3 className="tk-lane-head">
                  {ln.label} <span className="tab-count">{ln.rows.length}</span>
                </h3>
              )}
              <div className="tk-board" role="list" aria-label={ln.label || "Board"}>
                {statuses.map((status) => {
                  const tasks = ln.rows.filter((t) => t.status.id === status.id).sort(byRank);
                  const allowed = !drag || canMoveTo(statuses, drag.from, status.id);
                  const points = tasks.reduce((n, t) => n + (t.type === "story" || t.type === "task" || t.type === "bug" ? (t.story_points ?? 0) : 0), 0);
                  const here = over?.status === status.id && over.lane === ln.id;
                  const total = colCount.get(status.id) ?? 0;
                  const overWip = !!status.wip_limit && total > status.wip_limit;
                  return (
                    <section
                      key={status.id}
                      role="listitem"
                      className={`tk-col tk-cat-${status.category}${drag && !allowed ? " blocked" : ""}${here ? " over" : ""}${overWip ? " tk-wip-over" : ""}`}
                      aria-label={status.name}
                      onDragOver={(e) => {
                        if (!drag || !allowed) return;
                        e.preventDefault();
                        if (!here) setOver({ status: status.id, before: null, lane: ln.id });
                      }}
                      onDragLeave={(e) => {
                        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null);
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        const key = drag?.key ?? e.dataTransfer.getData(DRAG_TYPE);
                        const before = here ? (over?.before ?? null) : null;
                        setDrag(null);
                        setOver(null);
                        if (key && allowed) void move(key, status.id, before);
                      }}
                    >
                      <header className="tk-col-head">
                        <span className="tk-col-name">{status.name}</span>
                        {status.wip_limit ? (
                          <span className={`tab-count${overWip ? " tk-wip-badge" : ""}`} title={`Giới hạn WIP ${status.wip_limit}${overWip ? ": đang vượt" : ""}`}>
                            {total}/{status.wip_limit}
                          </span>
                        ) : (
                          <span className="tab-count">{lane === "none" ? tasks.length : `${tasks.length}`}</span>
                        )}
                        {points > 0 && (
                          <span className="small muted" title="Tổng story point (Story / Task / Bug)">
                            {points} SP
                          </span>
                        )}
                      </header>
                      <div className="tk-col-body">
                        {tasks.map((t, i) => (
                          <div key={t.key}>
                            {here && over?.before === t.key && drag?.key !== t.key && <div className="tk-drop-line" />}
                            <TaskCard
                              task={t}
                              ageWarnHours={status.age_warning_hours ?? null}
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
                              onDragOver={(e) => {
                                if (!allowed) return;
                                onCardDragOver(e, status.id, tasks, i, ln.id);
                              }}
                            />
                            {grouping && t.type !== "subtask" && (t.subtasks?.total ?? 0) > 0 && (
                              <SubtaskGroup task={t} items={subsOf.get(t.key) ?? []} onOpen={onOpen} />
                            )}
                          </div>
                        ))}
                        {here && over?.before === null && drag && <div className="tk-drop-line" />}
                        {!tasks.length && !drag && <p className="small faint tk-col-empty">Không có task</p>}
                        {lane === "none" && space.can_create && status.category !== "done" && (
                          <>
                            <input
                              className="tk-quick"
                              value={quick[status.id] ?? ""}
                              disabled={quickBusy}
                              placeholder="+ Tạo nhanh: tiêu đề @người #bug !cao 3sp ^OCX-1"
                              title="@người · #loại · !ưu tiên · 3sp · ^mã task cha (sub-task)"
                              aria-label={`Tạo nhanh task ở ${status.name}`}
                              onChange={(e) => {
                                setQuick((q) => ({ ...q, [status.id]: e.target.value }));
                                // Another line: the picks for the old one no longer apply.
                                if (quickAsk[status.id]) dropAsk(status.id);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") void quickCreate(status);
                                if (e.key === "Escape") {
                                  setQuick((q) => ({ ...q, [status.id]: "" }));
                                  dropAsk(status.id);
                                }
                              }}
                            />
                            {quickAsk[status.id] && (
                              <QuickChips
                                ambiguous={quickAsk[status.id].parsed.ambiguous}
                                picks={quickAsk[status.id].picks}
                                busy={quickBusy}
                                onPick={(token, value) =>
                                  setQuickAsk((a) => (a[status.id] ? { ...a, [status.id]: { ...a[status.id], picks: { ...a[status.id].picks, [token]: value } } } : a))
                                }
                                onSave={() => void quickCreate(status)}
                                onCancel={() => dropAsk(status.id)}
                              />
                            )}
                          </>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

/** The one signal a card shows (§19.3), most important first: critical findings, overdue, too
 * long in its column. */
function cardSignal(task: TaskRow, ageWarnHours: number | null): { tone: "bad" | "warn"; text: string; title: string } | null {
  const crit = task.git?.open_critical ?? 0;
  if (crit > 0 && task.status.category !== "done") return { tone: "bad", text: `${crit} critical`, title: "Code của task còn finding critical mở" };
  if (task.due_date && task.status.category !== "done" && task.due_date < today()) return { tone: "bad", text: "Quá hạn", title: `Hạn ${task.due_date}` };
  const h = hoursIn(task);
  if (ageWarnHours && h !== null && task.status.category !== "done" && h > ageWarnHours) {
    const days = Math.floor(h / 24);
    return { tone: "warn", text: days >= 1 ? `${days} ngày ở đây` : `${Math.floor(h)} giờ ở đây`, title: `Ở “${task.status.name}” quá ngưỡng ${ageWarnHours} giờ của dự án` };
  }
  return null;
}

export function TaskCard({
  task,
  ageWarnHours = null,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
  onDragOver,
}: {
  task: TaskRow;
  ageWarnHours?: number | null;
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
      {(() => {
        const signal = cardSignal(task, ageWarnHours);
        return signal ? (
          <span className={`tk-signal tk-signal-${signal.tone}`} title={signal.title}>
            {signal.text}
          </span>
        ) : null;
      })()}
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
