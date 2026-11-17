import type { SearchCond, SearchNode, SearchTree, TaskRow } from "@/lib/types";

import { addDays, today } from "./taskParts";

/*
 * The Board filters the rows it already holds (docs/specs/31 §22.4): the FilterBar's tree is
 * matched here, condition by condition, with the same meaning as app/tasks/search.py. A condition
 * the row does not carry (text in the description / comments, watcher, component, MR, failed test,
 * DoD, custom fields) cannot be matched here: `clientMatchable` says so and the Board asks
 * POST /tasks/search for the matching keys instead.
 */

export interface MatchContext {
  meId: number | undefined;
  /** Ids of the running sprints ("Sprint: đang chạy"). */
  activeSprints: number[];
  /** "YYYY-MM-DD" of today, local. */
  today?: string;
  /** Now, in ms (the age / "last N days" conditions). */
  now?: number;
}

/** Fields matched on a TaskRow (op support checked per kind in `leafOk`). */
const CLIENT_FIELDS = new Set([
  "status",
  "category",
  "type",
  "priority",
  "resolution",
  "assignee",
  "reporter",
  "label",
  "sprint",
  "version",
  "parent",
  "due",
  "created",
  "updated",
  "resolved",
  "points",
  "age_in_column_hours",
  "blocked",
  "has_critical",
  "text",
]);

const values = (v: SearchCond["value"]): string[] => (Array.isArray(v) ? v.map(String) : v === undefined || v === null ? [] : [String(v)]);

export function fold(v: string): string {
  return v
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

function leaves(n: SearchNode, out: SearchCond[] = []): SearchCond[] {
  if ("field" in n) out.push(n);
  else for (const x of n.all ?? n.any ?? []) leaves(x, out);
  return out;
}

/** Every condition can be matched on the rows; `text` only roughly (key and title), see below. */
export function clientMatchable(tree: SearchTree, withText: boolean): boolean {
  return leaves(tree).every((c) => CLIENT_FIELDS.has(c.field) && (withText || c.field !== "text"));
}

export const hasText = (tree: SearchTree) => leaves(tree).some((c) => c.field === "text");

export function matchTree(t: TaskRow, n: SearchNode, ctx: MatchContext): boolean {
  if ("field" in n) return matchCond(t, n, ctx);
  if (n.all) return n.all.every((x) => matchTree(t, x, ctx));
  if (n.any) return n.any.length === 0 || n.any.some((x) => matchTree(t, x, ctx));
  return true;
}

function positive(t: TaskRow, c: SearchCond, ctx: MatchContext): boolean {
  const vs = values(c.value);
  switch (c.field) {
    case "status":
      return vs.includes(String(t.status.id));
    case "category":
      return vs.includes(t.status.category);
    case "type":
      return vs.includes(t.type);
    case "priority":
      return vs.includes(t.priority);
    case "resolution":
      return !!t.resolution && vs.includes(t.resolution);
    case "assignee":
    case "reporter": {
      const who = c.field === "assignee" ? t.assignee : t.reporter;
      if (!who) return vs.includes("none");
      return vs.includes(String(who.id)) || (vs.includes("me") && who.id === ctx.meId);
    }
    case "label":
      return vs.some((v) => (t.labels ?? []).includes(v));
    case "sprint":
      if (t.sprint_id === null || t.sprint_id === undefined) return vs.includes("backlog") || vs.includes("none");
      return vs.includes(String(t.sprint_id)) || (vs.includes("active") && ctx.activeSprints.includes(t.sprint_id));
    case "version":
      return t.version_id !== null && t.version_id !== undefined && vs.includes(String(t.version_id));
    case "parent":
      return !!t.parent_key && vs.map((v) => v.toUpperCase()).includes(t.parent_key.toUpperCase());
  }
  return false;
}

/** The field's value, for empty / not empty. */
function present(t: TaskRow, field: string): boolean {
  switch (field) {
    case "resolution":
      return !!t.resolution;
    case "assignee":
      return !!t.assignee;
    case "reporter":
      return !!t.reporter;
    case "label":
      return (t.labels ?? []).length > 0;
    case "sprint":
      return t.sprint_id !== null && t.sprint_id !== undefined;
    case "version":
      return t.version_id !== null && t.version_id !== undefined;
    case "parent":
      return !!t.parent_key;
    case "due":
      return !!t.due_date;
    case "created":
      return !!t.created_at;
    case "updated":
      return !!t.updated_at;
    case "resolved":
      return !!t.resolved_at;
    case "points":
      return t.story_points !== null && t.story_points !== undefined;
    case "age_in_column_hours":
      return !!t.status_changed_at;
    case "category":
    case "status":
    case "type":
    case "priority":
      return true;
  }
  return false;
}

function matchDate(t: TaskRow, c: SearchCond, ctx: MatchContext): boolean {
  const day = ctx.today ?? today();
  const now = ctx.now ?? Date.now();
  const raw = c.field === "due" ? t.due_date : c.field === "created" ? t.created_at : c.field === "updated" ? t.updated_at : t.resolved_at;
  if (!raw) return false;
  const v = values(c.value)[0] ?? "";
  if (c.op === "within_days") {
    const n = Number(v);
    if (!Number.isFinite(n)) return false;
    if (c.field === "due") return raw >= day && raw <= addDays(day, n); // the next N days
    return new Date(raw).getTime() >= now - n * 86_400_000; // the last N days
  }
  if (v === "overdue") return raw < day && t.status.category !== "done";
  if (v === "week") return raw >= day && raw <= addDays(day, 7);
  // Timestamps are UTC on the server: their day is the first 10 characters.
  const d = raw.slice(0, 10);
  if (c.op === "before") return d < v;
  if (c.op === "after") return d > v;
  return d === v;
}

function matchCond(t: TaskRow, c: SearchCond, ctx: MatchContext): boolean {
  if (c.op === "empty") return !present(t, c.field);
  if (c.op === "not_empty") return present(t, c.field);
  switch (c.field) {
    case "text": {
      // Roughly, while the server's answer (description, comments too) is on its way.
      const words = fold(values(c.value)[0] ?? "").split(/\s+/).filter(Boolean);
      const hay = fold(`${t.key} ${t.title}`);
      const hit = words.every((w) => hay.includes(w));
      return c.op === "is_not" ? !hit : hit;
    }
    case "due":
    case "created":
    case "updated":
    case "resolved":
      return matchDate(t, c, ctx);
    case "points": {
      const p = t.story_points;
      const n = Number(values(c.value)[0]);
      if (p === null || p === undefined || !Number.isFinite(n)) return false;
      return c.op === "gte" ? p >= n : c.op === "lte" ? p <= n : p === n;
    }
    case "age_in_column_hours": {
      if (!t.status_changed_at) return false;
      const edge = (ctx.now ?? Date.now()) - Number(values(c.value)[0]) * 3_600_000;
      const at = new Date(t.status_changed_at).getTime();
      return c.op === "gte" ? at <= edge : at >= edge;
    }
    case "blocked":
      return !!t.blocked === (c.value === true || c.value === "true");
    case "has_critical":
      return (t.git?.open_critical ?? 0) > 0 === (c.value === true || c.value === "true");
  }
  const hit = positive(t, c, ctx);
  return c.op === "is_not" ? !hit : hit;
}
