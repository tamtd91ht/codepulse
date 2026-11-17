"use client";

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import { Icon, IconButton } from "@/components/icons";
import { EmptyState, Loading, Message, Segmented, SortTh } from "@/components/ui";
import { useUndo } from "@/components/UndoToast";
import { type ApiError, api, qs } from "@/lib/api";
import { date, relative } from "@/lib/format";
import type { Me, TaskRow, TaskSearchPage, TaskSpace, TaskStatusRef } from "@/lib/types";
import { useTaskLive } from "@/lib/useTaskLive";

import { FilterBar, Popover } from "./FilterBar";
import { filterCount, filterTree, useFilterState, useViewOptions } from "./filterState";
import { SavedFilterMenu } from "./SavedFilterMenu";
import {
  GitBadge,
  PersonAvatar,
  RowPriority,
  StatusPill,
  TYPE_LABEL,
  TypeIcon,
  canMoveTo,
  confirmDone,
  errorText,
  personName,
  priorityList,
  refreshTasks,
  sortedStatuses,
  useConfirm,
  useSprints,
  useTaskPeople,
  useVersions,
} from "./taskParts";
import { useDrawerList } from "./drawerNav";

/*
 * Danh sách (§13, UI/UX §4.2, docs/specs/31 §22.2): the FilterBar on top (the bulk bar takes its
 * row while rows are picked), then the table filling the rest of the window: sticky header, its own
 * scroll, more rows loaded near the bottom, only the visible rows drawn past 200. Status, priority,
 * assignee, SP and due are edited in place (with undo); a row click opens the task (`?task=KEY`).
 * Reads POST /tasks/search with the bar's tree.
 */

type InverseCall = [url: string, body: Record<string, unknown>, method: "post" | "patch"];

/**
 * §21.7 undo of a bulk edit: per task, its previous value, grouped so that tasks sharing one go back
 * in one /tasks/bulk call. A status goes back with the resolution it had (a done status closed for
 * another reason than "Đã xong" gets that reason again).
 */
function bulkInverse(before: TaskRow[], set: Record<string, unknown>): InverseCall[] {
  const calls: InverseCall[] = [];
  const group = (value: (r: TaskRow) => unknown, changed: (r: TaskRow) => boolean, field: string) => {
    const by = new Map<string, { value: unknown; keys: string[] }>();
    for (const r of before.filter(changed)) {
      const v = value(r);
      const id = JSON.stringify(v ?? null);
      const g = by.get(id) ?? { value: v ?? null, keys: [] };
      g.keys.push(r.key);
      by.set(id, g);
    }
    for (const g of by.values()) calls.push(["/tasks/bulk", { keys: g.keys, set: { [field]: g.value } }, "post"]);
  };
  if ("status_id" in set) {
    group((r) => r.status.id, (r) => r.status.id !== set.status_id, "status_id");
    for (const r of before)
      if (r.status.id !== set.status_id && r.status.category === "done" && r.resolution && r.resolution !== "done")
        calls.push([`/tasks/${encodeURIComponent(r.key)}`, { resolution: r.resolution }, "patch"]);
  }
  if ("assignee_id" in set) group((r) => r.assignee?.id ?? null, (r) => (r.assignee?.id ?? null) !== set.assignee_id, "assignee_id");
  if ("priority" in set) group((r) => r.priority, (r) => r.priority !== set.priority, "priority");
  if ("sprint_id" in set) group((r) => r.sprint_id, (r) => r.sprint_id !== set.sprint_id, "sprint_id");
  if ("version_id" in set) group((r) => r.version_id, (r) => r.version_id !== set.version_id, "version_id");
  for (const label of (set.labels_add as string[] | undefined) ?? []) {
    const keys = before.filter((r) => !(r.labels ?? []).includes(label)).map((r) => r.key);
    if (keys.length) calls.push(["/tasks/bulk", { keys, set: { labels_remove: [label] } }, "post"]);
  }
  for (const label of (set.labels_remove as string[] | undefined) ?? []) {
    const keys = before.filter((r) => (r.labels ?? []).includes(label)).map((r) => r.key);
    if (keys.length) calls.push(["/tasks/bulk", { keys, set: { labels_add: [label] } }, "post"]);
  }
  return calls;
}

/* ------------------------------------------------------------------ view options */

type ColumnId = "status" | "priority" | "assignee" | "points" | "due" | "updated" | "labels" | "type";
const COLUMNS: { id: ColumnId; label: string }[] = [
  { id: "type", label: "Loại" },
  { id: "status", label: "Trạng thái" },
  { id: "priority", label: "Ưu tiên" },
  { id: "assignee", label: "Người làm" },
  { id: "points", label: "SP" },
  { id: "due", label: "Hạn" },
  { id: "updated", label: "Cập nhật" },
  { id: "labels", label: "Nhãn" },
];
type GroupBy = "none" | "status" | "assignee" | "sprint" | "priority" | "type";
const GROUPS: { value: GroupBy; label: string }[] = [
  { value: "none", label: "Không" },
  { value: "status", label: "Trạng thái" },
  { value: "assignee", label: "Người làm" },
  { value: "sprint", label: "Sprint" },
  { value: "priority", label: "Ưu tiên" },
  { value: "type", label: "Loại" },
];
type ListView = { density: "dense" | "comfy"; group: GroupBy; hidden: string[] };
const VIEW_DEFAULTS: ListView = { density: "dense", group: "none", hidden: ["type", "labels"] };

/** Sorts a link may ask for (POST /tasks/search `sort`). */
const LINK_SORTS = ["updated", "created", "due", "key", "priority", "title"];

/** Rows asked per step while scrolling; the server gives at most 500 per call. */
const STEP = 100;
const CALL_MAX = 500;
/** Past this many rows only the visible ones are drawn. */
const VIRTUAL_FROM = 200;
const OVERSCAN = 12;

/** The table box: from its top to the bottom of the window, so only the table scrolls. */
function useFillHeight(ref: React.RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      // What sits below the box (main's padding, a footer) stays in view too.
      const below = Math.max(0, document.documentElement.scrollHeight - (top + el.offsetHeight));
      el.style.height = `${Math.max(320, Math.floor(window.innerHeight - top - Math.min(below, 56)))}px`;
    };
    fit();
    window.addEventListener("resize", fit);
    const ro = new ResizeObserver(fit);
    ro.observe(document.body);
    return () => {
      window.removeEventListener("resize", fit);
      ro.disconnect();
    };
  }, [ref]);
}

export function TaskList({ space, me, onOpen }: { space: TaskSpace; me: Me | undefined; onOpen: (key: string) => void }) {
  const bp = space.business_project_id;
  const statuses = useMemo(() => sortedStatuses(space), [space]);
  const people = useTaskPeople(bp);
  const sprints = useSprints(bp);
  const versions = useVersions(bp);
  const [value, setValue, reset] = useFilterState("list", bp, me?.id);
  const [view, setView] = useViewOptions<ListView>("list", me?.id, VIEW_DEFAULTS);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "updated", dir: "desc" });
  // A link's first sort (`sort` / `order`, e.g. a Báo cáo tile, §22.9), read once after mount.
  useEffect(() => {
    try {
      const p = new URLSearchParams(window.location.search);
      const key = p.get("sort");
      if (key && LINK_SORTS.includes(key)) setSort({ key, dir: p.get("order") === "asc" ? "asc" : "desc" });
    } catch {
      /* no URL: the default sort */
    }
  }, []);
  const [limit, setLimit] = useState(STEP);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const [undoEl, offerUndo] = useUndo();
  useTaskLive(bp); // §21.7: the list follows others' changes without a reload
  const box = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  useFillHeight(box);

  const tree = useMemo(() => filterTree(value), [value]);
  const treeJson = JSON.stringify(tree);
  useEffect(() => {
    setLimit(STEP);
    setSelected(new Set());
    scroller.current?.scrollTo({ top: 0 });
  }, [treeJson, sort.key, sort.dir, bp]);

  // A string key under /tasks/search# with the dự án in its body: refreshTasks() and the live
  // stream refresh it. Past 500 rows the pages are asked in parallel.
  const body = { space: [bp], tree, sort: sort.key, order: sort.dir, limit };
  const list = useSWR<TaskSearchPage, ApiError>(
    `/tasks/search#${JSON.stringify(body)}`,
    async () => {
      const calls: Promise<TaskSearchPage>[] = [];
      for (let offset = 0; offset < limit; offset += CALL_MAX) calls.push(api.post<TaskSearchPage>("/tasks/search", { ...body, limit: Math.min(CALL_MAX, limit - offset), offset }));
      const pages = await Promise.all(calls);
      return { items: pages.flatMap((p) => p.items), total: pages[0].total, tree: pages[0].tree };
    },
    { keepPreviousData: true, shouldRetryOnError: (err: ApiError) => !err.status || err.status >= 500 },
  );
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  // §22.6: J/K in the task drawer walk the rows in list order.
  useDrawerList(rows.map((t) => t.key));
  const total = list.data?.total ?? null;
  const filtersOn = filterCount(value) > 0;
  const exportHref = `/api/v1/tasks/search/export.csv${qs({ space: bp, tree: treeJson, sort: sort.key, order: sort.dir })}`;
  const toggleSort = (key: string) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "key" ? "asc" : "desc" }));
  const shown = (c: ColumnId) => !view.hidden.includes(c);

  // Groups (Hiển thị › Nhóm theo) over the loaded rows, in the order the sort gives.
  type Item = { kind: "group"; id: string; label: ReactNode; n: number } | { kind: "row"; t: TaskRow };
  const items: Item[] = useMemo(() => {
    if (view.group === "none") return rows.map((t) => ({ kind: "row", t }));
    const groupOf = (t: TaskRow): [string, ReactNode] => {
      switch (view.group) {
        case "status":
          return [String(t.status.id), t.status.name];
        case "assignee":
          return [String(t.assignee?.id ?? 0), t.assignee ? personName(t.assignee) : "Chưa giao"];
        case "sprint":
          return [String(t.sprint_id ?? 0), t.sprint_id ? ((sprints.data ?? []).find((s) => s.id === t.sprint_id)?.name ?? `Sprint #${t.sprint_id}`) : "Backlog"];
        case "priority":
          return [t.priority, t.priority_name ?? t.priority];
        default:
          return [t.type, TYPE_LABEL[t.type] ?? t.type];
      }
    };
    const order: string[] = [];
    const by = new Map<string, { label: ReactNode; rows: TaskRow[] }>();
    for (const t of rows) {
      const [id, label] = groupOf(t);
      const g = by.get(id);
      if (g) g.rows.push(t);
      else {
        by.set(id, { label, rows: [t] });
        order.push(id);
      }
    }
    if (view.group === "status") order.sort((a, b) => statuses.findIndex((s) => String(s.id) === a) - statuses.findIndex((s) => String(s.id) === b));
    return order.flatMap((id) => {
      const g = by.get(id)!;
      return [{ kind: "group" as const, id, label: g.label, n: g.rows.length }, ...g.rows.map((t) => ({ kind: "row" as const, t }))];
    });
  }, [rows, view.group, sprints.data, statuses]);

  // Windowing past 200 rows: fixed row height (measured from a drawn row), spacers above / below.
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(600);
  const [rowH, setRowH] = useState(36);
  const virtual = items.length > VIRTUAL_FROM;
  const firstRow = useRef<HTMLTableRowElement>(null);
  useLayoutEffect(() => {
    const h = firstRow.current?.getBoundingClientRect().height;
    if (h && Math.abs(h - rowH) > 0.5) setRowH(h);
  });
  const start = virtual ? Math.max(0, Math.floor(scrollTop / rowH) - OVERSCAN) : 0;
  const end = virtual ? Math.min(items.length, Math.ceil((scrollTop + viewH) / rowH) + OVERSCAN) : items.length;

  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    setScrollTop(el.scrollTop);
    setViewH(el.clientHeight);
    if (total !== null && rows.length < total && !list.isValidating && el.scrollTop + el.clientHeight > el.scrollHeight - rowH * 10) setLimit((l) => (l <= rows.length ? l + STEP * 2 : l));
  }, [total, rows.length, list.isValidating, rowH]);
  useEffect(() => {
    // A short first page that does not fill the box: ask for more right away.
    const el = scroller.current;
    if (el && total !== null && rows.length < total && !list.isValidating && el.scrollHeight <= el.clientHeight + rowH * 10) setLimit((l) => (l <= rows.length ? l + STEP * 2 : l));
  }, [rows.length, total, list.isValidating, rowH]);

  /* ---------------- bulk ---------------- */

  async function bulk(payload: Record<string, unknown>, what: string) {
    if (!selected.size) return;
    setBusy(true);
    setMessage(null);
    try {
      const before = rows.filter((r) => selected.has(r.key));
      const res = await api.post<{ skipped?: { key: string }[] }>("/tasks/bulk", { keys: [...selected], ...payload });
      setSelected(new Set());
      const set = (payload as { set?: Record<string, unknown> }).set;
      if (set && !payload.delete) {
        const skipped = new Set((res?.skipped ?? []).map((s) => s.key));
        const calls = bulkInverse(before.filter((r) => !skipped.has(r.key)), set);
        if (calls.length)
          offerUndo({
            text: `Đã ${what} ${before.length} task`,
            undo: async () => {
              try {
                for (const [url, b, method] of calls) await (method === "patch" ? api.patch(url, b) : api.post(url, b));
              } finally {
                await refreshTasks();
              }
            },
          });
        if (skipped.size) setMessage(`Bỏ qua ${skipped.size} task không đổi được.`);
      }
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    await refreshTasks();
    setBusy(false);
  }

  async function bulkDelete() {
    const n = selected.size;
    if (!(await ask({ title: `Xoá ${n} task?`, body: <p>Sub-task của chúng bị xoá theo. Không hoàn tác được.</p>, ok: `Xoá ${n} task`, danger: true }))) return;
    await bulk({ delete: true }, "xoá");
  }

  /* ---------------- one cell ---------------- */

  const [editing, setEditing] = useState<string | null>(null); // "KEY:column"
  async function patchCell(t: TaskRow, change: Record<string, unknown>, back: Record<string, unknown>, text: string) {
    setEditing(null);
    const path = `/tasks/${encodeURIComponent(t.key)}`;
    try {
      await api.patch(path, change);
      offerUndo({
        text,
        undo: async () => {
          try {
            await api.patch(path, back);
          } finally {
            await refreshTasks();
          }
        },
      });
    } catch (e) {
      setMessage(`Lỗi ${t.key}: ${errorText(e)}`);
    }
    await refreshTasks();
  }
  async function moveCell(t: TaskRow, target: TaskStatusRef) {
    setEditing(null);
    if (target.id === t.status.id) return;
    if (!(await confirmDone(ask, t.git?.open_critical ?? 0, target))) return;
    const path = `/tasks/${encodeURIComponent(t.key)}/transition`;
    const back = { status_id: t.status.id, resolution: t.status.category === "done" ? (t.resolution ?? undefined) : undefined };
    try {
      await api.post(path, { status_id: target.id });
      offerUndo({
        text: `Đã chuyển ${t.key} sang ${target.name}`,
        undo: async () => {
          try {
            await api.post(path, back);
          } finally {
            await refreshTasks();
          }
        },
      });
    } catch (e) {
      setMessage(`Lỗi ${t.key}: ${errorText(e)}`);
    }
    await refreshTasks();
  }

  const allChecked = rows.length > 0 && rows.every((r) => selected.has(r.key));
  const sprintOptions = (sprints.data ?? []).filter((s) => s.state !== "closed");
  const colCount = 3 + COLUMNS.filter((c) => shown(c.id)).length + 1;
  const peopleList = people.data ?? [];

  const bulkBar =
    selected.size > 0 ? (
      <BulkBar
        n={selected.size}
        busy={busy}
        statuses={statuses}
        people={peopleList}
        priorities={priorityList(space)}
        sprints={sprintOptions}
        versions={versions.data ?? []}
        canDelete={space.can_manage}
        onBulk={(p, what) => void bulk(p, what)}
        onDelete={() => void bulkDelete()}
        onClear={() => setSelected(new Set())}
      />
    ) : undefined;

  const display = (
    <div className="fb-display-pop">
      <div className="fb-display-row">
        <span className="fb-display-label">Mật độ</span>
        <Segmented<ListView["density"]>
          label="Mật độ hiển thị"
          value={view.density}
          onChange={(density) => setView({ density })}
          options={[
            { value: "dense", label: "Gọn" },
            { value: "comfy", label: "Thoáng" },
          ]}
        />
      </div>
      <label className="fb-display-row">
        <span className="fb-display-label">Nhóm theo</span>
        <select value={view.group} onChange={(e) => setView({ group: e.target.value as GroupBy })}>
          {GROUPS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="fb-display-cols">
        <legend className="fb-display-label">Cột hiển thị</legend>
        {COLUMNS.map((c) => (
          <label key={c.id} className="inline-check">
            <input
              type="checkbox"
              checked={shown(c.id)}
              onChange={(e) => setView({ hidden: e.target.checked ? view.hidden.filter((h) => h !== c.id) : [...view.hidden, c.id] })}
            />{" "}
            {c.label}
          </label>
        ))}
      </fieldset>
      <button type="button" className="link small" onClick={() => setView(VIEW_DEFAULTS)}>
        Về mặc định
      </button>
    </div>
  );

  const renderRow = (t: TaskRow, i: number) => {
    const overdue = t.due_date && t.status.category !== "done" && t.due_date < new Date().toISOString().slice(0, 10);
    const id = (c: string) => `${t.key}:${c}`;
    const edit = (c: string) => setEditing(id(c));
    return (
      <tr
        key={t.key}
        ref={i === 0 ? firstRow : undefined}
        className={`fb-row${selected.has(t.key) ? " selected" : ""}`}
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("button, a, input, select, label, .fb-cell-edit")) return;
          onOpen(t.key);
        }}
      >
        <td className="check-col">
          <input
            type="checkbox"
            aria-label={`Chọn ${t.key}`}
            checked={selected.has(t.key)}
            onChange={(e) => {
              const next = new Set(selected);
              if (e.target.checked) next.add(t.key);
              else next.delete(t.key);
              setSelected(next);
            }}
          />
        </td>
        <td className="fb-c-key">
          <span className="tk-keycell">
            <TypeIcon type={t.type} />
            <span className={`tk-key${t.status.category === "done" ? " done" : ""}`}>{t.key}</span>
          </span>
        </td>
        <td className="fb-c-title">
          <button type="button" className="link tk-row-title fb-title" onClick={() => onOpen(t.key)} title={t.title}>
            {t.title}
          </button>
          <GitBadge row={t} />
        </td>
        {shown("type") && <td className="fb-c-nowrap">{TYPE_LABEL[t.type]}</td>}
        {shown("status") && (
          <td className="fb-c-nowrap">
            {editing === id("status") ? (
              <select
                className="fb-cell-edit compact"
                autoFocus
                aria-label={`Trạng thái của ${t.key}`}
                value={t.status.id}
                onBlur={() => setEditing(null)}
                onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                onChange={(e) => {
                  const target = statuses.find((s) => s.id === Number(e.target.value));
                  if (target) void moveCell(t, target);
                }}
              >
                {statuses.map((s) => (
                  <option key={s.id} value={s.id} disabled={!canMoveTo(statuses, t.status.id, s.id)}>
                    {s.name}
                  </option>
                ))}
              </select>
            ) : (
              <CellButton label={`Đổi trạng thái ${t.key}`} onClick={() => edit("status")}>
                <StatusPill status={t.status} resolution={t.resolution} />
              </CellButton>
            )}
          </td>
        )}
        {shown("priority") && (
          <td className="fb-c-nowrap">
            {editing === id("priority") ? (
              <select
                className="fb-cell-edit compact"
                autoFocus
                aria-label={`Ưu tiên của ${t.key}`}
                value={t.priority}
                onBlur={() => setEditing(null)}
                onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                onChange={(e) => {
                  const p = priorityList(space).find((x) => x.key === e.target.value);
                  void patchCell(t, { priority: e.target.value }, { priority: t.priority }, `Đã đổi ưu tiên ${t.key} thành ${p?.name ?? e.target.value}`);
                }}
              >
                {priorityList(space).map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : (
              <CellButton label={`Đổi ưu tiên ${t.key}`} onClick={() => edit("priority")}>
                <RowPriority row={t} withLabel />
              </CellButton>
            )}
          </td>
        )}
        {shown("assignee") && (
          <td className="fb-c-nowrap">
            {editing === id("assignee") ? (
              <select
                className="fb-cell-edit compact"
                autoFocus
                aria-label={`Người làm của ${t.key}`}
                value={t.assignee?.id ?? ""}
                onBlur={() => setEditing(null)}
                onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                onChange={(e) => {
                  const next = e.target.value ? Number(e.target.value) : null;
                  const who = peopleList.find((p) => p.id === next);
                  void patchCell(t, { assignee_id: next }, { assignee_id: t.assignee?.id ?? null }, next ? `Đã giao ${t.key} cho ${personName(who ?? null)}` : `Đã bỏ giao ${t.key}`);
                }}
              >
                <option value="">Chưa giao</option>
                {(space.can_assign ? peopleList : peopleList.filter((p) => p.id === me?.id || p.id === t.assignee?.id)).map((p) => (
                  <option key={p.id} value={p.id}>
                    {personName(p)}
                  </option>
                ))}
              </select>
            ) : (
              <CellButton label={`Đổi người làm ${t.key}`} onClick={() => edit("assignee")}>
                <span className="tk-person">
                  <PersonAvatar person={t.assignee} />
                  <span className={t.assignee ? undefined : "faint"}>{personName(t.assignee)}</span>
                </span>
              </CellButton>
            )}
          </td>
        )}
        {shown("points") && (
          <td className="num">
            {editing === id("points") ? (
              <input
                className="fb-cell-edit fb-cell-num"
                type="number"
                min={0}
                max={999}
                step={0.5}
                autoFocus
                aria-label={`Story point của ${t.key}`}
                defaultValue={t.story_points ?? ""}
                onBlur={(e) => {
                  const raw = e.target.value.trim();
                  const next = raw === "" ? null : Number(raw);
                  if (next === (t.story_points ?? null) || (next !== null && Number.isNaN(next))) return setEditing(null);
                  void patchCell(t, { story_points: next }, { story_points: t.story_points }, `Đã đổi SP ${t.key} thành ${next ?? "trống"}`);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                  if (e.key === "Escape") setEditing(null);
                }}
              />
            ) : (
              <CellButton label={`Đổi story point ${t.key}`} onClick={() => edit("points")}>
                {t.story_points ?? "—"}
              </CellButton>
            )}
          </td>
        )}
        {shown("due") && (
          <td className={`fb-c-nowrap${overdue ? " tk-overdue" : ""}`}>
            {editing === id("due") ? (
              <input
                className="fb-cell-edit"
                type="date"
                autoFocus
                aria-label={`Hạn của ${t.key}`}
                defaultValue={t.due_date ?? ""}
                onBlur={(e) => {
                  const next = e.target.value || null;
                  if (next === (t.due_date ?? null)) return setEditing(null);
                  void patchCell(t, { due_date: next }, { due_date: t.due_date }, next ? `Đã đặt hạn ${t.key}: ${date(next)}` : `Đã bỏ hạn ${t.key}`);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                  if (e.key === "Escape") setEditing(null);
                }}
              />
            ) : (
              <CellButton label={`Đổi hạn ${t.key}`} onClick={() => edit("due")}>
                {t.due_date ? date(t.due_date) : "—"}
              </CellButton>
            )}
          </td>
        )}
        {shown("updated") && (
          <td className="fb-c-nowrap muted small" title={t.updated_at}>
            {relative(t.updated_at)}
          </td>
        )}
        {shown("labels") && (
          <td className="fb-c-labels">
            {(t.labels ?? []).map((l) => (
              <span key={l} className="tk-label">
                {l}
              </span>
            ))}
          </td>
        )}
        <td className="tk-actions-col">
          <Link href={`/tasks/${encodeURIComponent(t.key)}`} className="icon-btn" aria-label={`Mở ${t.key} trong trang riêng`} data-tip="Mở trang riêng">
            <Icon name="external" size={16} />
          </Link>
        </td>
      </tr>
    );
  };

  // The table never gets narrower than its shown columns plus a 280 px title (widths live in CSS).
  const minWidth = `calc(${["check", "key", "title-min", ...COLUMNS.filter((c) => shown(c.id)).map((c) => c.id), "actions"].map((id) => `var(--fb-w-${id})`).join(" + ")})`;

  let rowIndex = 0;
  return (
    <div className={`tk-list fb-listwrap${view.density === "dense" ? " fb-dense" : ""}`} ref={box}>
      {confirmEl}
      {undoEl}
      <FilterBar
        space={space}
        me={me}
        screen="list"
        value={value}
        onChange={setValue}
        total={total}
        saved={<SavedFilterMenu bp={bp} me={me} value={value} onApply={setValue} onReset={reset} />}
        display={display}
        actions={<IconLink href={exportHref} label="Xuất CSV theo bộ lọc hiện tại" />}
        replaceWith={bulkBar}
      />
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="fb-tablebox card flush">
        {!list.data ? (
          <div className="fb-scroll">
            <Loading error={list.error} rows={10} />
          </div>
        ) : !rows.length ? (
          <div className="fb-scroll fb-empty-box">
            <EmptyState icon="search" title={filtersOn ? "Không có task khớp" : "Dự án chưa có task nào"}>
              {filtersOn && (
                <button type="button" className="sm" onClick={() => setValue({ text: "", chips: [] })}>
                  Xoá lọc
                </button>
              )}
            </EmptyState>
          </div>
        ) : (
          <div className="fb-scroll" ref={scroller} onScroll={onScroll} aria-busy={list.isValidating}>
            <table className="fb-table" style={{ ["--fb-min" as string]: minWidth }}>
              {/* Fixed layout: the title takes what the other columns leave, never under --fb-w-title-min. */}
              <colgroup>
                <col className="fb-w-check" />
                <col className="fb-w-key" />
                <col className="fb-w-title" />
                {COLUMNS.filter((c) => shown(c.id)).map((c) => (
                  <col key={c.id} className={`fb-w-${c.id}`} />
                ))}
                <col className="fb-w-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th className="check-col">
                    <input
                      type="checkbox"
                      aria-label="Chọn mọi task đã tải"
                      checked={allChecked}
                      onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((r) => r.key)) : new Set())}
                    />
                  </th>
                  <SortTh id="key" sort={sort} onSort={toggleSort}>
                    Mã
                  </SortTh>
                  <SortTh id="title" sort={sort} onSort={toggleSort}>
                    Tiêu đề
                  </SortTh>
                  {shown("type") && <th>Loại</th>}
                  {shown("status") && <th>Trạng thái</th>}
                  {shown("priority") && (
                    <SortTh id="priority" sort={sort} onSort={toggleSort}>
                      Ưu tiên
                    </SortTh>
                  )}
                  {shown("assignee") && <th>Người làm</th>}
                  {shown("points") && <th className="num">SP</th>}
                  {shown("due") && (
                    <SortTh id="due" sort={sort} onSort={toggleSort}>
                      Hạn
                    </SortTh>
                  )}
                  {shown("updated") && (
                    <SortTh id="updated" sort={sort} onSort={toggleSort}>
                      Cập nhật
                    </SortTh>
                  )}
                  {shown("labels") && <th>Nhãn</th>}
                  <th className="tk-actions-col" aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {start > 0 && (
                  <tr aria-hidden="true" className="fb-spacer">
                    <td colSpan={colCount} style={{ height: start * rowH }} />
                  </tr>
                )}
                {items.slice(start, end).map((it) =>
                  it.kind === "group" ? (
                    <tr key={`g:${it.id}`} className="fb-group">
                      <th colSpan={colCount} scope="rowgroup">
                        {it.label} <span className="muted">· {it.n}</span>
                      </th>
                    </tr>
                  ) : (
                    renderRow(it.t, rowIndex++)
                  ),
                )}
                {end < items.length && (
                  <tr aria-hidden="true" className="fb-spacer">
                    <td colSpan={colCount} style={{ height: (items.length - end) * rowH }} />
                  </tr>
                )}
              </tbody>
            </table>
            {total !== null && rows.length < total && (
              <p className="fb-more-rows small muted" role="status">
                Đang hiện {rows.length}/{total} task · cuộn để tải thêm
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CellButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className="fb-cell" aria-label={label} onClick={onClick}>
      {children}
    </button>
  );
}

function IconLink({ href, label }: { href: string; label: string }) {
  return (
    <a className="icon-btn fb-icon-link" href={href} download aria-label={label} data-tip={label}>
      <Icon name="download" size={16} />
    </a>
  );
}

/* ------------------------------------------------------------------ bulk bar */

/** "✓ 3 đã chọn │ Trạng thái ▾ Giao ▾ Sprint ▾ Ưu tiên ▾ Nhãn ▾ ⋯ │ ✕ Bỏ chọn" in the bar's row. */
function BulkBar({
  n,
  busy,
  statuses,
  people,
  priorities,
  sprints,
  versions,
  canDelete,
  onBulk,
  onDelete,
  onClear,
}: {
  n: number;
  busy: boolean;
  statuses: { id: number; name: string }[];
  people: { id: number; full_name: string | null; primary_email: string | null }[];
  priorities: { key: string; name: string }[];
  sprints: { id: number; name: string }[];
  versions: { id: number; name: string }[];
  canDelete: boolean;
  onBulk: (payload: Record<string, unknown>, what: string) => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  const [labelOpen, setLabelOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [label, setLabel] = useState("");
  const labelRef = useRef<HTMLButtonElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  return (
    <div className="fb-bulk" role="region" aria-label="Thao tác hàng loạt">
      <strong className="fb-bulk-n">
        <Icon name="check" size={14} /> {n} đã chọn
      </strong>
      <span className="fb-sep" aria-hidden="true" />
      <BulkSelect label="Trạng thái" disabled={busy} onPick={(v) => onBulk({ set: { status_id: Number(v) } }, "chuyển trạng thái")}>
        {statuses.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </BulkSelect>
      <BulkSelect label="Giao" disabled={busy} onPick={(v) => onBulk({ set: { assignee_id: v === "none" ? null : Number(v) } }, "giao")}>
        <option value="none">Bỏ giao</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>
            {personName(p)}
          </option>
        ))}
      </BulkSelect>
      <BulkSelect label="Sprint" disabled={busy} onPick={(v) => onBulk({ set: { sprint_id: v === "none" ? null : Number(v) } }, "chuyển sprint")}>
        <option value="none">Backlog</option>
        {sprints.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </BulkSelect>
      <BulkSelect label="Ưu tiên" disabled={busy} onPick={(v) => onBulk({ set: { priority: v } }, "đổi ưu tiên")}>
        {priorities.map((p) => (
          <option key={p.key} value={p.key}>
            {p.name}
          </option>
        ))}
      </BulkSelect>
      <button ref={labelRef} type="button" className="fb-chip" aria-haspopup="dialog" aria-expanded={labelOpen} disabled={busy} onClick={() => setLabelOpen((o) => !o)}>
        Nhãn <Icon name="more" size={12} />
      </button>
      <IconButton ref={moreRef} icon="more" label="Thao tác khác" aria-haspopup="dialog" aria-expanded={moreOpen} disabled={busy} onClick={() => setMoreOpen((o) => !o)} />
      <span className="fb-spacer-flex" />
      <button type="button" className="ghost sm" onClick={onClear}>
        <Icon name="close" size={14} /> Bỏ chọn
      </button>
      {labelOpen && (
        <Popover anchor={labelRef} onClose={() => setLabelOpen(false)} label="Nhãn cho task đã chọn">
          <form
            className="fb-label-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (!label.trim()) return;
              onBulk({ set: { labels_add: [label.trim()] } }, "thêm nhãn cho");
              setLabel("");
              setLabelOpen(false);
            }}
          >
            <label className="rq-field">
              <span className="rq-label">Nhãn</span>
              <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="backend" maxLength={60} data-autofocus />
            </label>
            <div className="fb-pop-foot fb-pop-foot-row">
              <button
                type="button"
                className="ghost sm"
                disabled={!label.trim()}
                onClick={() => {
                  onBulk({ set: { labels_remove: [label.trim()] } }, "bỏ nhãn của");
                  setLabel("");
                  setLabelOpen(false);
                }}
              >
                <Icon name="minus" size={14} /> Bỏ nhãn
              </button>
              <button type="submit" className="sm" disabled={!label.trim()}>
                <Icon name="plus" size={14} /> Thêm nhãn
              </button>
            </div>
          </form>
        </Popover>
      )}
      {moreOpen && (
        <Popover anchor={moreRef} onClose={() => setMoreOpen(false)} label="Thao tác khác">
          <div className="fb-label-form">
            <label className="rq-field">
              <span className="rq-label">Phiên bản</span>
              <BulkSelect
                label="Phiên bản"
                disabled={busy}
                onPick={(v) => {
                  setMoreOpen(false);
                  onBulk({ set: { version_id: v === "none" ? null : Number(v) } }, "đổi phiên bản");
                }}
              >
                <option value="none">Bỏ phiên bản</option>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </BulkSelect>
            </label>
            {canDelete && (
              <button
                type="button"
                className="ghost sm tc-danger"
                onClick={() => {
                  setMoreOpen(false);
                  onDelete();
                }}
              >
                <Icon name="remove" size={14} /> Xoá {n} task
              </button>
            )}
          </div>
        </Popover>
      )}
    </div>
  );
}

function BulkSelect({ label, disabled, onPick, children }: { label: string; disabled?: boolean; onPick: (v: string) => void; children: ReactNode }) {
  return (
    <select
      className="compact fb-bulk-select"
      value=""
      disabled={disabled}
      aria-label={label}
      onChange={(e) => {
        if (e.target.value) onPick(e.target.value);
      }}
    >
      <option value="">{label} ▾</option>
      {children}
    </select>
  );
}
