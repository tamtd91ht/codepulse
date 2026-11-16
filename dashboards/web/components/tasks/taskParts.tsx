"use client";

import { type ReactNode, useCallback, useRef, useState } from "react";
import { mutate } from "swr";

import { Modal } from "@/components/CriteriaParts";
import { Icon, type IconName } from "@/components/icons";
import { Avatar, Pill } from "@/components/ui";
import { ApiError, useApi } from "@/lib/api";
import type {
  Repo,
  Sprint,
  TaskCategory,
  TaskField,
  TaskPerson,
  TaskPriorityColor,
  TaskPriorityDef,
  TaskPriorityLevel,
  TaskResolution,
  TaskRow,
  TaskSpace,
  TaskStatus,
  TaskStatusRef,
  TaskType,
  TaskVersion,
} from "@/lib/types";

/* Shared bits of the Task module (docs/specs/31): labels, small badges, hooks, helpers. */

export const TASK_TYPES: TaskType[] = ["epic", "story", "task", "bug", "subtask"];
export const TYPE_LABEL: Record<TaskType, string> = {
  epic: "Epic",
  story: "Story",
  task: "Task",
  bug: "Bug",
  subtask: "Sub-task",
};
const TYPE_ICON: Record<TaskType, IconName> = {
  epic: "flag",
  story: "book",
  task: "check",
  bug: "alertCircle",
  subtask: "link",
};

/* Priorities (docs/specs/31 §18): each dự án names its own; the level gives the arrow. */
export const PRIORITY_LEVELS: TaskPriorityLevel[] = ["highest", "high", "medium", "low", "lowest"];
export const LEVEL_LABEL: Record<TaskPriorityLevel, string> = {
  highest: "Cao nhất",
  high: "Cao",
  medium: "Trung bình",
  low: "Thấp",
  lowest: "Thấp nhất",
};
export const PRIORITY_COLORS: { value: TaskPriorityColor; label: string }[] = [
  { value: "red", label: "Đỏ" },
  { value: "orange", label: "Cam" },
  { value: "amber", label: "Vàng" },
  { value: "green", label: "Xanh lá" },
  { value: "blue", label: "Xanh dương" },
  { value: "purple", label: "Tím" },
  { value: "gray", label: "Xám" },
];
const LEVEL_COLOR: Record<TaskPriorityLevel, TaskPriorityColor> = { highest: "red", high: "orange", medium: "amber", low: "blue", lowest: "gray" };

/** The dự án's priorities, most urgent first; the five defaults when the API sends none. */
export function priorityList(space: Pick<TaskSpace, "priorities"> | null | undefined): TaskPriorityDef[] {
  if (space?.priorities?.length) return [...space.priorities].sort((a, b) => a.position - b.position);
  return PRIORITY_LEVELS.map((level, i) => ({ id: -i - 1, key: level, name: LEVEL_LABEL[level], level, color: LEVEL_COLOR[level], position: i, is_default: level === "medium" }));
}

export function defaultPriority(space: Pick<TaskSpace, "priorities"> | null | undefined): string {
  const list = priorityList(space);
  return (list.find((p) => p.is_default) ?? list[Math.floor(list.length / 2)]).key;
}

/** Why a task was closed (docs/specs/31 §19.1); only "done" counts as delivered. */
export const RESOLUTION_LABEL: Record<TaskResolution, string> = {
  done: "Đã xong",
  wont_do: "Không làm",
  duplicate: "Trùng",
  cannot_reproduce: "Không tái hiện được",
  obsolete: "Lỗi thời",
};
export const RESOLUTIONS: TaskResolution[] = ["done", "wont_do", "duplicate", "cannot_reproduce", "obsolete"];

export const CATEGORY_LABEL: Record<TaskCategory, string> = { todo: "Cần làm", doing: "Đang làm", done: "Xong" };

export const LINK_KIND_LABEL: Record<string, { out: string; in: string }> = {
  blocks: { out: "Chặn", in: "Bị chặn bởi" },
  relates: { out: "Liên quan", in: "Liên quan" },
  duplicates: { out: "Trùng với", in: "Bị trùng bởi" },
  clones: { out: "Nhân bản từ", in: "Được nhân bản thành" },
};

export const VIA_LABEL: Record<string, string> = {
  web: "Web",
  git: "Git",
  smart_commit: "Smart commit",
  mcp: "MCP",
  ai: "AI",
};

/** Every SWR key of the module is under /tasks: one call refreshes board, lists and the open task. */
export function refreshTasks(): Promise<unknown> {
  return mutate((k) => typeof k === "string" && k.startsWith("/tasks"));
}

export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** The dự án id of a row; `space` may come as the id or as an object carrying it. */
export function spaceOf(row: Pick<TaskRow, "space">): number | null {
  const s = row.space;
  if (typeof s === "number") return s;
  if (s && typeof s === "object") return s.business_project_id ?? s.id ?? null;
  return null;
}

export function personName(p: TaskPerson | null | undefined): string {
  return p ? (p.full_name ?? p.primary_email ?? `#${p.id}`) : "Chưa giao";
}

export function sortedStatuses(space: TaskSpace | null | undefined): TaskStatus[] {
  return [...(space?.statuses ?? [])].sort((a, b) => a.position - b.position || a.id - b.id);
}

/** May a task in status `from` move to `to` (the space's transition table; null = any). */
export function canMoveTo(statuses: TaskStatus[], from: number, to: number): boolean {
  if (from === to) return true;
  const src = statuses.find((s) => s.id === from);
  return !src || src.transitions === null || src.transitions === undefined || src.transitions.includes(to);
}

/** "1h 30m" from minutes. */
export function fmtMinutes(m: number | null | undefined): string {
  if (!m) return "0m";
  const h = Math.floor(m / 60);
  const r = Math.round(m % 60);
  return h ? (r ? `${h}h ${r}m` : `${h}h`) : `${r}m`;
}

/** Minutes from "1h30m", "2h", "45m", "1.5h" or a bare number of minutes; null when unreadable. */
export function parseDuration(text: string): number | null {
  const t = text.trim().toLowerCase().replace(/\s+/g, "");
  if (!t) return null;
  if (/^\d+$/.test(t)) return Number(t);
  const m = /^(?:(\d+(?:[.,]\d+)?)d)?(?:(\d+(?:[.,]\d+)?)h)?(?:(\d+)m)?$/.exec(t);
  if (!m || (!m[1] && !m[2] && !m[3])) return null;
  const n = (s: string | undefined) => (s ? Number(s.replace(",", ".")) : 0);
  return Math.round(n(m[1]) * 8 * 60 + n(m[2]) * 60 + n(m[3]));
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/* ---------- Badges ---------- */

export function TypeIcon({ type, withLabel }: { type: TaskType; withLabel?: boolean }) {
  return (
    <span className={`tk-type tk-type-${type}${withLabel ? " labelled" : ""}`} title={TYPE_LABEL[type] ?? type}>
      <Icon name={TYPE_ICON[type] ?? "check"} size={12} />
      {withLabel ? <span>{TYPE_LABEL[type] ?? type}</span> : <span className="visually-hidden">{TYPE_LABEL[type] ?? type}</span>}
    </span>
  );
}

export function PriorityIcon({ name, level, color, withLabel }: { name: string; level: TaskPriorityLevel; color: TaskPriorityColor; withLabel?: boolean }) {
  const icon: IconName = level === "medium" ? "minus" : "arrowUp";
  return (
    <span className={`tk-prio tk-prio-c-${color} tk-prio-l-${level}`} title={`Ưu tiên: ${name}`}>
      <Icon name={icon} size={14} />
      {withLabel ? <span>{name}</span> : <span className="visually-hidden">{name}</span>}
    </span>
  );
}

/** A task row's priority, as its dự án names it (the row carries name, level, colour). */
export function RowPriority({ row, withLabel }: { row: Pick<TaskRow, "priority" | "priority_name" | "priority_level" | "priority_color">; withLabel?: boolean }) {
  const level = row.priority_level ?? (PRIORITY_LEVELS.includes(row.priority as TaskPriorityLevel) ? (row.priority as TaskPriorityLevel) : "medium");
  return (
    <PriorityIcon
      name={row.priority_name ?? LEVEL_LABEL[row.priority as TaskPriorityLevel] ?? row.priority}
      level={level}
      color={row.priority_color ?? LEVEL_COLOR[level]}
      withLabel={withLabel}
    />
  );
}

export function StatusPill({ status, resolution }: { status: TaskStatusRef | null | undefined; resolution?: TaskResolution | null }) {
  if (!status) return <span className="faint">—</span>;
  // Closed without output (§19.1): say why, not as a success.
  if (status.category === "done" && resolution && resolution !== "done") {
    return <Pill>{`${status.name} · ${RESOLUTION_LABEL[resolution]}`}</Pill>;
  }
  return <Pill tone={status.category === "done" ? "good" : status.category === "doing" ? "info" : undefined}>{status.name}</Pill>;
}

/** Red when critical findings are still open on the task's code, orange for high only. */
export function GitBadge({ row }: { row: Pick<TaskRow, "git"> }) {
  const g = row.git;
  if (!g) return null;
  return (
    <>
      {g.open_critical > 0 && (
        <span className="tk-badge bad" title={`${g.open_critical} finding critical còn mở trên code của task`}>
          {g.open_critical} critical
        </span>
      )}
      {g.open_critical === 0 && g.open_high > 0 && (
        <span className="tk-badge warn" title={`${g.open_high} finding high còn mở trên code của task`}>
          {g.open_high} high
        </span>
      )}
      {(g.commits > 0 || g.mrs > 0) && (
        <span className="tk-git-count" title={`${g.commits} commit · ${g.mrs} MR`}>
          <Icon name="code" size={12} />
          {g.commits}/{g.mrs}
        </span>
      )}
    </>
  );
}

export function PersonAvatar({ person, size = "sm" }: { person: TaskPerson | null | undefined; size?: "sm" | "lg" }) {
  if (!person)
    return (
      <span className="tk-unassigned" title="Chưa giao">
        <Icon name="user" size={12} />
      </span>
    );
  return (
    <span title={personName(person)} className="tk-avatar">
      <Avatar name={person.full_name} email={person.primary_email} src={person.avatar_url} size={size} />
    </span>
  );
}

export function PointsChip({ points }: { points: number | null | undefined }) {
  if (points === null || points === undefined) return null;
  return (
    <span className="tk-points" title="Story point">
      {points}
    </span>
  );
}

/* ---------- Data hooks ---------- */

export const useTaskSpaces = () => useApi<TaskSpace[]>("/tasks/spaces");
export const useTaskPeople = (bp: number | null) => useApi<TaskPerson[]>(bp ? `/tasks/people?space=${bp}` : null);
export const useSprints = (bp: number | null) => useApi<Sprint[]>(bp ? `/tasks/spaces/${bp}/sprints` : null);
export const useVersions = (bp: number | null) => useApi<TaskVersion[]>(bp ? `/tasks/spaces/${bp}/versions` : null);
export const useTaskFields = (bp: number | null) => useApi<TaskField[]>(bp ? `/tasks/spaces/${bp}/fields` : null);

/** The tracked repos of dự án `bp` (for "Tạo nhánh" and linking an MR). */
export function useSpaceRepos(bp: number | null) {
  const repos = useApi<Repo[]>(bp ? "/repos" : null);
  return { ...repos, list: (repos.data ?? []).filter((r) => r.business_project_id === bp) };
}

/* ---------- A promise-based confirm in the house modal ---------- */

export function useConfirm(): [ReactNode, (opts: { title: string; body: ReactNode; ok: string; danger?: boolean }) => Promise<boolean>] {
  const [ask, setAsk] = useState<{ title: string; body: ReactNode; ok: string; danger?: boolean } | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);
  const open = useCallback((opts: { title: string; body: ReactNode; ok: string; danger?: boolean }) => {
    setAsk(opts);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);
  const done = (v: boolean) => {
    resolver.current?.(v);
    resolver.current = null;
    setAsk(null);
  };
  const element = ask ? (
    <Modal
      title={ask.title}
      onClose={() => done(false)}
      width={440}
      footer={
        <>
          <button type="button" className="ghost" onClick={() => done(false)}>
            Huỷ
          </button>
          <button type="button" className={ask.danger ? "primary danger" : "primary"} onClick={() => done(true)}>
            {ask.ok}
          </button>
        </>
      }
    >
      <div className="tk-confirm-body">{ask.body}</div>
    </Modal>
  ) : null;
  return [element, open];
}

/** Moving a task with open critical findings into a done-category status asks first (§12.3). */
export async function confirmDone(
  ask: ReturnType<typeof useConfirm>[1],
  openCritical: number,
  target: TaskStatusRef | undefined,
): Promise<boolean> {
  if (!target || target.category !== "done" || openCritical <= 0) return true;
  return ask({
    title: "Còn finding critical chưa xử lý",
    body: (
      <p>
        Code của task còn <strong>{openCritical} critical</strong> chưa xử lý. Vẫn chuyển sang “{target.name}”?
      </p>
    ),
    ok: "Vẫn chuyển",
    danger: true,
  });
}

/** Days for `<input type="date">` and the API. */
export function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
