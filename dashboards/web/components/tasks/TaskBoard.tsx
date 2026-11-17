"use client";

import Link from "next/link";
import { type DragEvent, type KeyboardEvent, type RefObject, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import { Icon, IconButton } from "@/components/icons";
import { Alert, EmptyState, Loading, Segmented } from "@/components/ui";
import { useUndo } from "@/components/UndoToast";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import { useTaskLive } from "@/lib/useTaskLive";
import type { Me, SavedTaskFilter, TaskDodGaps, TaskPage, TaskPerson, TaskResolution, TaskRow, TaskSearchPage, TaskSpace, TaskStatusRef, TaskType } from "@/lib/types";

import { clientMatchable, hasText, matchTree } from "./boardFilter";
import { selectCard, useCardSelected } from "./cardSelection";
import { useDrawerList } from "./drawerNav";
import { FilterBar } from "./FilterBar";
import { fromTree } from "./FilterChips";
import { type FilterValue, filterCount, filterTree, useFilterState, useViewOptions } from "./filterState";
import { type QuickPicks, QuickChips, QuickPreview } from "./QuickChips";
import { SavedFilterMenu } from "./SavedFilterMenu";
import { SwitchoverBoardNote } from "./TaskSwitchover";
import { SubtaskGroup } from "./SubtaskGroup";
import {
  GitBadge,
  PersonAvatar,
  PointsChip,
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
  spaceOf,
  today,
  useConfirm,
  useSprints,
  useTaskPeople,
  useTaskSpaces,
} from "./taskParts";
import { cardSignal } from "./taskSignal";

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

/** Swimlanes (docs/specs/31 §19.3): rows of the board grouped by one field. */
type LaneMode = "none" | "epic" | "parent" | "assignee" | "priority";
const LANE_LABEL: Record<LaneMode, string> = {
  none: "Không chia",
  epic: "Theo epic",
  parent: "Theo task cha",
  assignee: "Theo người làm",
  priority: "Theo ưu tiên",
};

/** Fields a card can show (Hiển thị › Trường trên card). */
const CARD_FIELDS = [
  { id: "points", label: "Story point" },
  { id: "assignee", label: "Người làm" },
  { id: "labels", label: "Nhãn" },
  { id: "due", label: "Hạn" },
] as const;

/**
 * "⧉ Hiển thị" of the Board (UI/UX §3.5, docs/specs/31 §22.4): per person in localStorage, those
 * differing from the defaults on the link (`v`). Module level: useViewOptions needs a stable object.
 */
type BoardView = {
  lane: string;
  groupSubs: boolean;
  density: "dense" | "comfy";
  hiddenFields: string[];
  hideEmpty: boolean;
  foldDone: boolean;
};
const VIEW_DEFAULTS: BoardView = { lane: "none", groupSubs: false, density: "dense", hiddenFields: [], hideEmpty: false, foldDone: false };

/** Columns folded / opened by hand, per dự án: status id → folded (wins over "Thu gọn cột Done"). */
const foldKey = (bp: number) => `cp:tasks:board:folded:${bp}`;
function readFolds(bp: number): Record<string, boolean> {
  try {
    const v: unknown = JSON.parse(window.localStorage.getItem(foldKey(bp)) ?? "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

/** Under 768 px the Board shows one column at a time (UI/UX §6): no folded strips there. */
function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return narrow;
}

/** `data-fade-l` / `data-fade-r` on a sideways scroller while there is more to see that way. */
function useEdgeFade(ref: RefObject<HTMLElement | null>, deps: unknown) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const sync = () => {
      el.toggleAttribute("data-fade-l", el.scrollLeft > 1);
      el.toggleAttribute("data-fade-r", el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
    };
    sync();
    el.addEventListener("scroll", sync, { passive: true });
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    for (const child of el.children) ro.observe(child);
    return () => {
      el.removeEventListener("scroll", sync);
      ro.disconnect();
    };
  }, [ref, deps]);
}

/** Whole days from today to `day` (negative: past). */
function daysTo(day: string): number {
  return Math.round((new Date(`${day.slice(0, 10)}T00:00:00`).getTime() - new Date(`${today()}T00:00:00`).getTime()) / 86_400_000);
}

/** Hours since the task entered its status. */
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
  /** @/#/!/^ tokens that matched nothing: they stay in the title, the preview underlines them (§21.0 F6). */
  unmatched: string[];
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
  const out: QuickParsed = { title: "", ambiguous: [], unmatched: [] };
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
    if (/^[@#!^]./.test(word)) out.unmatched.push(word);
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
  // §21.7: changes by others, git rules and automation reach the board without a reload.
  useTaskLive(bp);
  const [undoEl, offerUndo] = useUndo();
  // §21.7 dev-only: how long the first load of the board takes (target < 1.5 s for 500 tasks).
  const firstLoad = useRef(false);
  useEffect(() => {
    if (process.env.NODE_ENV === "production" || typeof performance === "undefined") return;
    if (!firstLoad.current) performance.mark("tk-board-start");
  }, []);
  useEffect(() => {
    if (process.env.NODE_ENV === "production" || !loaded || firstLoad.current || typeof performance === "undefined") return;
    firstLoad.current = true;
    const n = loaded.length;
    requestAnimationFrame(() => {
      performance.mark("tk-board-ready");
      try {
        const m = performance.measure("tk-board-first-load", "tk-board-start", "tk-board-ready");
        console.info(`[tk-board] first load: ${n} task, ${Math.round(m.duration)} ms`);
      } catch {
        /* the start mark was cleared */
      }
    });
  }, [loaded]);

  // Filters (UI/UX §4.1, docs/specs/31 §22.4): the shared FilterBar, matched on the loaded rows.
  const [value, setValue, reset] = useFilterState("board", bp, me?.id);
  const [view, setView] = useViewOptions<BoardView>("board", me?.id, VIEW_DEFAULTS);
  const lane: LaneMode = view.lane in LANE_LABEL ? (view.lane as LaneMode) : "none";
  const dense = view.density === "dense";
  const groupSubs = view.groupSubs;
  const narrow = useNarrow();
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  useEffect(() => setFolds(readFolds(bp)), [bp]);
  const saveFolds = (next: Record<string, boolean>) => {
    setFolds(next);
    try {
      window.localStorage.setItem(foldKey(bp), JSON.stringify(next));
    } catch {
      /* remembered for this page only */
    }
  };
  const isFolded = (s: { id: number; category: string }) => !narrow && (folds[String(s.id)] ?? (view.foldDone && s.category === "done"));
  const [drag, setDrag] = useState<{ key: string; from: number } | null>(null);
  const [over, setOver] = useState<{ status: number; before: string | null; lane: string } | null>(null);
  const [quick, setQuick] = useState<Record<number, string>>({});
  const [quickOpen, setQuickOpen] = useState<number | null>(null);
  const [quickBusy, setQuickBusy] = useState(false);
  // Ambiguous quick-create lines waiting for a pick, per column (§20.2).
  const [quickAsk, setQuickAsk] = useState<Record<number, { text: string; parsed: QuickParsed; picks: QuickPicks }>>({});
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

  // The chips are matched here (boardFilter.ts); a condition the rows do not carry (text in the
  // description, watcher, MR, DoD, custom fields…) asks POST /tasks/search for the matching keys.
  // Text is matched on key + title meanwhile, so typing never empties the board while it waits.
  const tree = useMemo(() => filterTree(value), [value]);
  const filtersOn = filterCount(value) > 0;
  const needServer = filtersOn && (!clientMatchable(tree, false) || hasText(tree));
  const serverBody = needServer ? { space: [bp], tree, sort: "rank", limit: 500 } : null;
  const serverSig = serverBody ? `/tasks/search#board#${JSON.stringify(serverBody)}` : null;
  const serverKeys = useSWR<{ sig: string; keys: Set<string> }>(
    serverSig,
    async () => {
      const keys = new Set<string>();
      for (let offset = 0; offset < 5000; offset += 500) {
        const page = await api.post<TaskSearchPage>("/tasks/search", { ...serverBody, offset });
        for (const t of page.items) keys.add(t.key);
        if (offset + 500 >= page.total) break;
      }
      return { sig: serverSig ?? "", keys };
    },
    // Kept while the next answer loads (no flash); only used while it is this filter's own.
    { keepPreviousData: true, revalidateOnFocus: false },
  );
  const allSprints = useSprints(bp);
  const activeSprints = useMemo(() => (allSprints.data ?? []).filter((s) => s.state === "active").map((s) => s.id), [allSprints.data]);
  const shown = useMemo(() => {
    const base = (rows ?? []).filter((r) => r.type !== "epic"); // epics live in the Backlog's Epic bar (§19.1)
    if (!filtersOn) return base;
    const got = needServer && serverKeys.data?.sig === serverSig ? serverKeys.data.keys : undefined;
    if (got) return base.filter((r) => got.has(r.key));
    // Waiting for the server: the conditions known here (text on key + title) narrow it already.
    const ctx = { meId: me?.id, activeSprints };
    return clientMatchable(tree, true) ? base.filter((r) => matchTree(r, tree, ctx)) : base;
  }, [rows, filtersOn, needServer, serverKeys.data, serverSig, tree, me?.id, activeSprints]);
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
  // Columns on screen: "Ẩn cột rỗng" drops those with no card, except while a card is moving.
  const columns = useMemo(
    () => statuses.filter((st) => !view.hideEmpty || drag || (colCount.get(st.id) ?? 0) > 0),
    [statuses, view.hideEmpty, drag, colCount],
  );
  // §22.6: J/K in the task drawer walk the cards in board order (lane, then column), as filtered
  // and shown: cards of folded columns are not on screen, so they are skipped.
  useDrawerList(
    lanes
      .flatMap((l) => columns.filter((st) => !isFolded(st)).flatMap((st) => l.rows.filter((t) => t.status.id === st.id).sort(byRank)))
      .map((t) => t.key),
  );
  const scroller = useRef<HTMLDivElement>(null);
  useEdgeFade(scroller, `${lane}|${columns.length}|${!!rows}|${JSON.stringify(folds)}|${view.foldDone}|${dense}`);

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
    // §21.7 undo: where it was (status with its resolution, the cards around it).
    const from = all.filter((t) => t.status.id === task.status.id).sort(byRank);
    const at = from.findIndex((t) => t.key === key);
    const back = {
      status_id: task.status.id,
      resolution: task.status.category === "done" ? (task.resolution ?? undefined) : undefined,
      before_key: from[at + 1]?.key ?? null,
      after_key: from[at - 1]?.key ?? null,
    };
    const moved: TaskRow = { ...task, status: { id: target.id, name: target.name, category: target.category } };
    const ordered = [...rest.slice(0, index), moved, ...rest.slice(index)].map((t, i) => ({ ...t, rank: i }));
    const byKey = new Map(ordered.map((t) => [t.key, t]));
    setLocal(all.map((t) => byKey.get(t.key) ?? t));
    setError(null);
    try {
      await api.post(`/tasks/${encodeURIComponent(key)}/move`, { status_id: statusId, before_key, after_key });
      if (target.category === "done" && task.status.category !== "done") setClosed(key);
      offerUndo({
        text: task.status.id === statusId ? `Đã đổi thứ tự ${key} trong “${target.name}”` : `Đã chuyển ${key} sang ${target.name}`,
        undo: async () => {
          setClosed((c) => (c === key ? null : c));
          try {
            await api.post(`/tasks/${encodeURIComponent(key)}/move`, back);
          } finally {
            await refreshTasks();
          }
        },
      });
    } catch (e) {
      setError(`Lỗi: ${errorText(e)}`);
      setLocal(null);
    }
    await refreshTasks();
  }

  // -- keyboard moves (§21.7): Space picks a card up, arrows move it, Space drops, Esc cancels ----
  const [announce, setAnnounce] = useState("");
  const [kb, setKbState] = useState<{ key: string; lane: string } | null>(null);
  // Read by blur handlers, which may run before the state update renders.
  const kbRef = useRef(kb);
  const setKb = (v: { key: string; lane: string } | null) => {
    kbRef.current = v;
    setKbState(v);
  };
  const refocus = useRef<string | null>(null);
  useEffect(() => {
    const key = refocus.current;
    if (!key) return;
    const el = document.querySelector<HTMLElement>(`[data-kb-card="${CSS.escape(key)}"] .tk-card-title`);
    if (el) {
      el.focus();
      refocus.current = null;
    }
  }, [rows]);

  /** Cards of one lane in one column, by rank, without `skip`. */
  const laneColumn = (laneId: string, statusId: number, skip: string) =>
    (lanes.find((l) => l.id === laneId)?.rows ?? []).filter((t) => t.status.id === statusId && t.key !== skip).sort(byRank);

  function place(key: string, laneId: string, statusId: number, index: number) {
    const list = laneColumn(laneId, statusId, key);
    const i = Math.max(0, Math.min(index, list.length));
    // At a lane's end: the column's end (a lane is a rank-ordered part of the column).
    setOver({ status: statusId, before: list[i]?.key ?? null, lane: laneId });
    const name = statuses.find((s) => s.id === statusId)?.name ?? "";
    setAnnounce(`${key}: cột ${name}, vị trí ${i + 1} trên ${list.length + 1}.`);
  }

  function cancelKb(say = true) {
    const was = kbRef.current;
    if (say && was) setAnnounce(`Đã huỷ di chuyển ${was.key}.`);
    setKb(null);
    setDrag(null);
    setOver(null);
  }

  function focusCard(el: Element | null | undefined) {
    el?.querySelector<HTMLElement>(".tk-card-title")?.focus();
  }

  function onCardKey(e: KeyboardEvent<HTMLElement>, t: TaskRow, laneId: string) {
    const card = (e.target as HTMLElement).closest(".tk-card");
    if (!card || !e.currentTarget.contains(card) || e.altKey || e.ctrlKey || e.metaKey) return;
    const picked = kb?.key === t.key;
    if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      if (e.repeat) return;
      if (!picked) {
        if (kb) cancelKb(false);
        setKb({ key: t.key, lane: laneId });
        setDrag({ key: t.key, from: t.status.id });
        const list = laneColumn(laneId, t.status.id, "");
        const i = list.findIndex((x) => x.key === t.key);
        setOver({ status: t.status.id, before: list[i + 1]?.key ?? null, lane: laneId });
        setAnnounce(`Đã nhấc ${t.key} ở cột ${t.status.name}, vị trí ${i + 1} trên ${list.length}. Mũi tên để di chuyển, Space để thả, Esc để huỷ.`);
        return;
      }
      const target = over ?? { status: t.status.id, before: null };
      cancelKb(false);
      const name = statuses.find((s) => s.id === target.status)?.name ?? "";
      setAnnounce(`Đã thả ${t.key} vào cột ${name}.`);
      refocus.current = t.key;
      void move(t.key, target.status, target.before);
      return;
    }
    if (e.key === "Escape" && picked) {
      e.preventDefault();
      cancelKb();
      return;
    }
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
    e.preventDefault();
    if (picked) {
      const statusId = over?.status ?? t.status.id;
      const list = laneColumn(laneId, statusId, t.key);
      const index = over?.before ? Math.max(0, list.findIndex((x) => x.key === over.before)) : list.length;
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        place(t.key, laneId, statusId, index + (e.key === "ArrowUp" ? -1 : 1));
        return;
      }
      const step = e.key === "ArrowLeft" ? -1 : 1;
      const at = statuses.findIndex((s) => s.id === statusId);
      for (let i = at + step; i >= 0 && i < statuses.length; i += step) {
        if (canMoveTo(statuses, t.status.id, statuses[i].id)) {
          place(t.key, laneId, statuses[i].id, index);
          return;
        }
      }
      setAnnounce(`Không có cột nào ${step < 0 ? "bên trái" : "bên phải"} mà ${t.key} chuyển tới được.`);
      return;
    }
    // Not picked up: arrows walk between cards (up / down in a column, left / right across).
    const wrap = e.currentTarget;
    const col = wrap.closest(".tk-col");
    const cardsIn = (c: Element | null | undefined) => [...(c?.querySelectorAll("[data-kb-card]") ?? [])];
    const mine = cardsIn(col);
    const i = mine.indexOf(wrap);
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      focusCard(mine[i + (e.key === "ArrowUp" ? -1 : 1)]);
      return;
    }
    const cols = [...(wrap.closest(".tk-board")?.querySelectorAll(":scope > .tk-col") ?? [])];
    const step = e.key === "ArrowLeft" ? -1 : 1;
    for (let c = cols.indexOf(col as Element) + step; c >= 0 && c < cols.length; c += step) {
      const there = cardsIn(cols[c]);
      if (there.length) {
        focusCard(there[Math.min(i, there.length - 1)]);
        return;
      }
    }
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

  const cardFields = new Set<string>(CARD_FIELDS.map((f) => f.id).filter((f) => !view.hiddenFields.includes(f)));

  // The FilterBar's status label (UI/UX §4.1): what the board holds; the sprint opens Sức khoẻ.
  const scopeTip = active
    ? `Board hiện task của sprint đang chạy${active.end_date ? `, kết thúc ${date(active.end_date)}` : ""}. Bấm tên sprint để mở Sức khoẻ sprint.`
    : kanban
      ? "Kanban: board hiện mọi task chưa xong và task xong trong 14 ngày qua."
      : "Chưa có sprint đang chạy: board hiện mọi task chưa xong và task xong trong 14 ngày qua.";
  let left = "";
  if (active?.end_date) {
    const n = daysTo(active.end_date);
    left = n > 0 ? `còn ${n} ngày` : n === 0 ? "kết thúc hôm nay" : `trễ ${-n} ngày`;
  }
  const scope = (
    <span className="bd-scope">
      {active ? (
        <Link href={`/tasks?space=${bp}&tab=health`} className="bd-scope-text bd-scope-link">
          {active.name}
          {left && ` · ${left}`}
        </Link>
      ) : (
        <span className="bd-scope-text">{kanban ? "Kanban" : "Không có sprint"} · hiện task chưa xong + Done 14 ngày</span>
      )}
      <button type="button" className="icon-btn bd-scope-info" aria-label={scopeTip} data-tip={scopeTip}>
        <Icon name="info" size={14} />
      </button>
    </span>
  );

  const display = (
    <div className="fb-display-pop">
      <label className="fb-display-row">
        <span className="fb-display-label">Swimlane</span>
        <select value={lane} onChange={(e) => setView({ lane: e.target.value })}>
          {(Object.keys(LANE_LABEL) as LaneMode[]).map((m) => (
            <option key={m} value={m}>
              {LANE_LABEL[m]}
            </option>
          ))}
        </select>
      </label>
      {space.types.includes("subtask") && (
        <label className="inline-check" title={lane === "parent" ? "“Theo task cha” đã gom sub-task theo lane" : "Ẩn card sub-task có task cha trên board; card cha hiện tiến độ"}>
          <input type="checkbox" checked={groupSubs} disabled={lane === "parent"} onChange={(e) => setView({ groupSubs: e.target.checked })} /> Gom sub-task vào card cha
        </label>
      )}
      <div className="fb-display-row">
        <span className="fb-display-label">Mật độ</span>
        <Segmented<BoardView["density"]>
          label="Mật độ card"
          value={view.density}
          onChange={(density) => setView({ density })}
          options={[
            { value: "comfy", label: "Thoáng" },
            { value: "dense", label: "Gọn" },
          ]}
        />
      </div>
      <fieldset className="fb-display-cols">
        <legend className="fb-display-label">Trường trên card</legend>
        {CARD_FIELDS.map((f) => (
          <label key={f.id} className="inline-check">
            <input
              type="checkbox"
              checked={cardFields.has(f.id)}
              onChange={(e) => setView({ hiddenFields: e.target.checked ? view.hiddenFields.filter((h) => h !== f.id) : [...view.hiddenFields, f.id] })}
            />{" "}
            {f.label}
          </label>
        ))}
      </fieldset>
      <label className="inline-check">
        <input type="checkbox" checked={view.hideEmpty} onChange={(e) => setView({ hideEmpty: e.target.checked })} /> Ẩn cột rỗng
      </label>
      <label className="inline-check">
        <input
          type="checkbox"
          checked={view.foldDone}
          onChange={(e) => {
            // The Done columns follow the switch again (hand folds of other columns stay).
            const doneIds = new Set(statuses.filter((s) => s.category === "done").map((s) => String(s.id)));
            saveFolds(Object.fromEntries(Object.entries(folds).filter(([id]) => !doneIds.has(id))));
            setView({ foldDone: e.target.checked });
          }}
        />{" "}
        Thu gọn cột Done
      </label>
      <button type="button" className="link small" onClick={() => setView(VIEW_DEFAULTS)}>
        Về mặc định
      </button>
    </div>
  );

  const renderColumn = (ln: { id: string; rows: TaskRow[] }, status: (typeof statuses)[number]) => {
    const tasks = ln.rows.filter((t) => t.status.id === status.id).sort(byRank);
    const allowed = !drag || canMoveTo(statuses, drag.from, status.id);
    const points = tasks.reduce((n, t) => n + (t.type === "story" || t.type === "task" || t.type === "bug" ? (t.story_points ?? 0) : 0), 0);
    const here = over?.status === status.id && over.lane === ln.id;
    const total = colCount.get(status.id) ?? 0;
    const overWip = !!status.wip_limit && total > status.wip_limit;
    const folded = isFolded(status);
    const toggleFold = () => saveFolds({ ...folds, [String(status.id)]: !folded });
    const count = status.wip_limit ? `${total}/${status.wip_limit}` : String(tasks.length);
    const wipTip = status.wip_limit ? `Giới hạn WIP ${status.wip_limit}${overWip ? ": đang vượt" : ""}` : undefined;
    const className = `tk-col tk-cat-${status.category}${drag && !allowed ? " blocked" : ""}${here ? " over" : ""}${overWip ? " tk-wip-over" : ""}${folded ? " bd-folded" : ""}${tasks.length ? "" : " bd-empty"}`;
    const dropProps = {
      onDragOver: (e: DragEvent<HTMLElement>) => {
        if (!drag || !allowed) return;
        e.preventDefault();
        if (!here) setOver({ status: status.id, before: null, lane: ln.id });
      },
      onDragLeave: (e: DragEvent<HTMLElement>) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(null);
      },
      onDrop: (e: DragEvent<HTMLElement>) => {
        e.preventDefault();
        const key = drag?.key ?? e.dataTransfer.getData(DRAG_TYPE);
        const before = here ? (over?.before ?? null) : null;
        setDrag(null);
        setOver(null);
        if (key && allowed) void move(key, status.id, before);
      },
    };
    if (folded)
      return (
        <section key={status.id} role="listitem" className={className} aria-label={`${status.name} (đã thu gọn)`} {...dropProps}>
          <button type="button" className="bd-strip" aria-expanded={false} aria-label={`Mở cột ${status.name}: ${count} task`} title={`Mở cột ${status.name}`} onClick={toggleFold}>
            <span className={`tab-count${overWip ? " tk-wip-badge" : ""}`}>{count}</span>
            <span className="bd-strip-name">{status.name}</span>
          </button>
        </section>
      );
    const typing = quickOpen === status.id || !!(quick[status.id] ?? "") || !!quickAsk[status.id];
    return (
      <section key={status.id} role="listitem" className={className} aria-label={status.name} {...dropProps}>
        <header className="tk-col-head bd-col-head">
          <span className="tk-col-name" title={status.name}>
            {status.name}
          </span>
          <span className={`tab-count${overWip ? " tk-wip-badge" : ""}`} title={wipTip}>
            {count}
          </span>
          {points > 0 && (
            <span className="bd-col-sp" title="Tổng story point (Story / Task / Bug)">
              {points} SP
            </span>
          )}
          {!narrow && <IconButton icon="minus" size={14} className="bd-fold" label={`Thu gọn cột ${status.name}`} aria-expanded onClick={toggleFold} />}
        </header>
        <div className="tk-col-body">
          {tasks.map((t, i) => (
            <div
              key={t.key}
              data-kb-card={t.key}
              className={kb?.key === t.key ? "lv-kb-picked" : undefined}
              onKeyDown={(e) => onCardKey(e, t, ln.id)}
              onKeyUp={(e) => {
                // Space picks up / drops: it must not also press the card's title button.
                if (e.key === " " && (e.target as HTMLElement).closest(".tk-card")) e.preventDefault();
              }}
              onBlur={(e) => {
                if (kbRef.current?.key === t.key && !e.currentTarget.contains(e.relatedTarget as Node)) cancelKb();
              }}
            >
              {here && over?.before === t.key && drag?.key !== t.key && <div className="tk-drop-line" />}
              <TaskCard
                task={t}
                dense={dense}
                fields={cardFields}
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
              {grouping && t.type !== "subtask" && (t.subtasks?.total ?? 0) > 0 && <SubtaskGroup task={t} items={subsOf.get(t.key) ?? []} onOpen={onOpen} />}
            </div>
          ))}
          {here && over?.before === null && drag && <div className="tk-drop-line" />}
          {!tasks.length && <p className="bd-drop-hint">Thả task vào đây</p>}
          {lane === "none" &&
            space.can_create &&
            status.category !== "done" &&
            (typing ? (
              <div className="bd-quick-box">
                <input
                  className="tk-quick"
                  autoFocus={quickOpen === status.id}
                  value={quick[status.id] ?? ""}
                  disabled={quickBusy}
                  placeholder="Tiêu đề @người #bug !cao 3sp ^OCX-1"
                  title="@người · #loại · !ưu tiên · 3sp · ^mã task cha (sub-task)"
                  aria-label={`Tạo nhanh task ở ${status.name}`}
                  onChange={(e) => {
                    setQuick((q) => ({ ...q, [status.id]: e.target.value }));
                    // Another line: the picks for the old one no longer apply.
                    if (quickAsk[status.id]) dropAsk(status.id);
                  }}
                  onBlur={(e) => {
                    const stay = e.currentTarget.closest(".bd-quick-box")?.contains(e.relatedTarget as Node);
                    if (!stay && !(quick[status.id] ?? "").trim() && !quickAsk[status.id]) setQuickOpen((o) => (o === status.id ? null : o));
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void quickCreate(status);
                    if (e.key === "Escape") {
                      e.preventDefault();
                      setQuick((q) => ({ ...q, [status.id]: "" }));
                      dropAsk(status.id);
                      setQuickOpen(null);
                    }
                  }}
                />
                {!quickAsk[status.id] && (quick[status.id] ?? "").trim() && (
                  <QuickPreview parsed={parseQuick(quick[status.id] ?? "", people.data ?? [], space)} people={people.data ?? []} space={space} />
                )}
                {quickAsk[status.id] && (
                  <QuickChips
                    ambiguous={quickAsk[status.id].parsed.ambiguous}
                    picks={quickAsk[status.id].picks}
                    busy={quickBusy}
                    onPick={(token, value) => setQuickAsk((a) => (a[status.id] ? { ...a, [status.id]: { ...a[status.id], picks: { ...a[status.id].picks, [token]: value } } } : a))}
                    onSave={() => void quickCreate(status)}
                    onCancel={() => dropAsk(status.id)}
                  />
                )}
              </div>
            ) : (
              <button type="button" className="bd-quick-add" aria-label={`Tạo nhanh task ở ${status.name}`} onClick={() => setQuickOpen(status.id)}>
                <Icon name="plus" size={14} /> Tạo nhanh
              </button>
            ))}
        </div>
      </section>
    );
  };

  return (
    <div className={`tk-board-wrap bd-screen${dense ? " tk-dense bd-dense" : " bd-comfy"}`}>
      {confirmEl}
      {undoEl}
      <p className="sr-only" id="lv-kb-help">
        Board: Tab tới một card, mũi tên để đi giữa các card. Space nhấc card lên, mũi tên chuyển cột hoặc vị trí, Space thả, Esc huỷ.
      </p>
      <div className="sr-only" role="status" aria-live="assertive" aria-atomic="true">
        {announce}
      </div>
      <FilterBar
        space={space}
        me={me}
        screen="board"
        value={value}
        onChange={setValue}
        total={rows ? shown.length : null}
        status={scope}
        saved={
          <>
            <BoardPins bp={bp} value={value} onApply={setValue} />
            <SavedFilterMenu bp={bp} me={me} value={value} onApply={setValue} onReset={reset} />
          </>
        }
        display={display}
      />
      <SwitchoverBoardNote space={space} />
      <div className={`bd-area${lane === "none" ? "" : " bd-lanes"}`} ref={lane === "none" ? undefined : scroller}>
        {!rows ? (
          <Loading error={loadError} rows={6} />
        ) : !statuses.length ? (
          <EmptyState icon="settings" title="Dự án chưa có trạng thái nào">
            Thêm trạng thái trong tab Cài đặt.
          </EmptyState>
        ) : lane === "none" ? (
          <div className="tk-board bd-board" role="list" aria-label="Board" ref={scroller}>
            {columns.map((st) => renderColumn(lanes[0] ?? { id: "all", rows: [] }, st))}
          </div>
        ) : (
          lanes.map((ln) => (
            <section key={ln.id} className="tk-lane bd-lane" aria-label={ln.label}>
              <h3 className="tk-lane-head bd-lane-head">
                {ln.label} <span className="tab-count">{ln.rows.length}</span>
              </h3>
              <div className="tk-board" role="list" aria-label={ln.label}>
                {columns.map((st) => renderColumn(ln, st))}
              </div>
            </section>
          ))
        )}
        {rows && filtersOn && !shown.length && (
          <div className="bd-nomatch" role="status">
            <span>Không có task khớp</span>
            <button type="button" className="sm" onClick={() => setValue({ text: "", chips: [] })}>
              Xoá lọc
            </button>
          </div>
        )}
        {(closed || error) && (
          <div className="bd-notes">
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
          </div>
        )}
      </div>
    </div>
  );
}

/** Pinned saved filters (§21.4) as one-click chips next to "★ Bộ lọc": a pick opens as chips;
 * the one in use is pressed, a second click clears it. Two at most (all are in ★ Bộ lọc). */
function BoardPins({ bp, value, onApply }: { bp: number; value: FilterValue; onApply: (v: FilterValue) => void }) {
  const pinned = useApi<SavedTaskFilter[]>(`/tasks/search/filters?space=${bp}&pinned=true`);
  const list = (pinned.data ?? []).slice(0, 2);
  if (!list.length) return null;
  const now = JSON.stringify(filterTree(value));
  return (
    <div className="bd-pins" role="group" aria-label="Bộ lọc đã ghim">
      {list.map((f) => {
        const next: FilterValue = { text: "", chips: fromTree(f.tree) };
        const on = JSON.stringify(filterTree(next)) === now;
        return (
          <button
            key={f.id}
            type="button"
            className={`fb-chip fb-toggle bd-pin${on ? " on" : ""}`}
            aria-pressed={on}
            title={on ? `Bỏ bộ lọc “${f.name}”` : `Lọc theo “${f.name}”`}
            onClick={() => onApply(on ? { text: "", chips: [] } : next)}
          >
            <Icon name="pin" size={12} /> <span className="bd-pin-name">{f.name}</span>
          </button>
        );
      })}
    </div>
  );
}

/** What the enforced DoD still misses on this card's task (§21.1): one request per dự án (SWR
 * shares it between the cards), only when the dự án enforces a DoD. */
function useDodMissing(task: TaskRow): string[] | null {
  const bp = spaceOf(task);
  const spaces = useTaskSpaces();
  const dod = spaces.data?.find((s) => s.business_project_id === bp)?.rules?.dod;
  const on = !!bp && !!dod?.enforce && (dod.checks ?? []).length > 0 && task.status.category === "doing";
  const gaps = useApi<TaskDodGaps>(on ? `/tasks/spaces/${bp}/dod-gaps` : null);
  return gaps.data?.gaps[task.key] ?? null;
}

/**
 * A Board card (UI/UX §4.1, docs/specs/31 §22.4). Gọn (≈56 px): the title on one line, then type,
 * key, one badge (the health signal, else the priority), SP and the avatar. Thoáng (≈88 px): the title
 * on up to two lines, the signal, labels, git badges, sub-task progress, due date. `fields` (Hiển thị ›
 * Trường trên card) hides SP / người làm / nhãn / hạn; absent, all are shown.
 */
export function TaskCard({
  task,
  dense = false,
  fields,
  ageWarnHours = null,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
  onDragOver,
}: {
  task: TaskRow;
  dense?: boolean;
  fields?: Set<string>;
  ageWarnHours?: number | null;
  dragging?: boolean;
  onOpen: () => void;
  onDragStart?: (e: DragEvent<HTMLElement>) => void;
  onDragEnd?: () => void;
  onDragOver?: (e: DragEvent<HTMLElement>) => void;
}) {
  const dodMissing = useDodMissing(task);
  const selected = useCardSelected(task.key);
  const has = (f: string) => !fields || fields.has(f);
  const signal = cardSignal(task, ageWarnHours, dodMissing);
  const signalEl = signal ? (
    <span className={`tk-signal tk-signal-${signal.tone}`} title={signal.title}>
      {signal.text}
    </span>
  ) : null;
  const due = has("due") && task.due_date && task.status.category !== "done" && signal?.kind !== "overdue" ? task.due_date : null;
  return (
    <article
      className={`tk-card${dense ? " bd-card-dense" : ""}${dragging ? " dragging" : ""}${selected ? " cp-card-selected" : ""}`}
      data-card-key={task.key}
      tabIndex={-1}
      aria-current={selected ? "true" : undefined}
      onMouseDown={() => selectCard(task.key)}
      onFocus={() => selectCard(task.key)}
      draggable={!!onDragStart}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={onDragOver}
    >
      <button type="button" className="tk-card-title" onClick={onOpen} title={task.title}>
        {task.title}
      </button>
      {!dense && (signalEl || (has("labels") && (task.labels ?? []).length > 0)) && (
        <div className="tk-card-labels bd-card-meta">
          {signalEl}
          {(has("labels") ? (task.labels ?? []) : []).slice(0, 3).map((l) => (
            <span key={l} className="tk-label">
              {l}
            </span>
          ))}
          {has("labels") && (task.labels ?? []).length > 3 && <span className="small faint">+{task.labels.length - 3}</span>}
        </div>
      )}
      <div className="tk-card-foot">
        <TypeIcon type={task.type} />
        <span className={`tk-key${task.status.category === "done" ? " done" : ""}`}>{task.key}</span>
        {dense ? (signalEl ?? <RowPriority row={task} />) : <GitBadge row={task} />}
        <span className="spacer" />
        {!dense && task.subtasks && task.subtasks.total > 0 && (
          <span className="small muted" title="Sub-task xong / tổng">
            {task.subtasks.done}/{task.subtasks.total}
          </span>
        )}
        {!dense && due && (
          <span className="bd-due" title={`Hạn ${date(due)}`}>
            {`${due.slice(8, 10)}/${due.slice(5, 7)}`}
          </span>
        )}
        {has("points") && <PointsChip points={task.story_points} />}
        {!dense && <RowPriority row={task} />}
        {has("assignee") && <PersonAvatar person={task.assignee} />}
      </div>
    </article>
  );
}
