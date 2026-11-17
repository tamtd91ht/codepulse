import type { SearchCond, TaskRow } from "@/lib/types";

import type { FilterValue } from "./filterState";

/*
 * The FilterBar's value applied in the browser (docs/specs/31 §22.9), for screens that already hold
 * their rows (Backlog). Same rules as POST /tasks/search: "là" chips of one field are ORed, the rest
 * ANDed; the text matches the key or every word of the title (accents ignored). A field this file
 * does not know lets every row through, so a chip from another screen never empties the view.
 */

/** Fields `matchRow` understands: the Backlog offers only these under "+ Lọc". */
export const CLIENT_FIELDS = ["status", "type", "priority", "assignee", "reporter", "label", "due", "points", "blocked", "parent"];

const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/gi, "d")
    .toLowerCase();

const localDay = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const plusDays = (day: string, n: number) => {
  const [y, m, d] = day.split("-").map(Number);
  return localDay(new Date(y, m - 1, d + n));
};

/** The row's values of a list field (people as ids, "none" when empty). */
function valuesOf(row: TaskRow, field: string, meId: number | undefined): string[] | null {
  switch (field) {
    case "status":
      return [String(row.status.id)];
    case "type":
      return [row.type];
    case "priority":
      return [row.priority];
    case "assignee":
    case "reporter": {
      const p = field === "assignee" ? row.assignee : row.reporter;
      if (!p) return ["none"];
      return p.id === meId ? [String(p.id), "me"] : [String(p.id)];
    }
    case "label":
      return row.labels ?? [];
    case "parent":
      return row.parent_key ? [row.parent_key] : [];
    default:
      return null;
  }
}

const list = (v: SearchCond["value"]): string[] => (Array.isArray(v) ? v.map(String) : v === undefined || v === null ? [] : [String(v)]);

function matchCond(row: TaskRow, c: SearchCond, meId: number | undefined): boolean {
  const today = localDay();
  if (c.field === "due") {
    const due = row.due_date;
    const v = list(c.value)[0] ?? "";
    if (c.op === "empty") return !due;
    if (c.op === "not_empty") return !!due;
    if (!due) return false;
    if (c.op === "is" && v === "overdue") return due < today && row.status.category !== "done";
    if (c.op === "is" && v === "week") return due >= today && due <= plusDays(today, 7);
    if (c.op === "before") return due < v;
    if (c.op === "after") return due > v;
    if (c.op === "within_days") return due >= today && due <= plusDays(today, Number(v) || 0);
    return due === v;
  }
  if (c.field === "points") {
    const n = row.story_points;
    if (c.op === "empty") return n === null;
    if (c.op === "not_empty") return n !== null;
    if (n === null) return false;
    const v = Number(list(c.value)[0]);
    return c.op === "gte" ? n >= v : c.op === "lte" ? n <= v : n === v;
  }
  if (c.field === "blocked") return !!row.blocked === (c.value === true || list(c.value)[0] === "true");
  const have = valuesOf(row, c.field, meId);
  if (have === null) return true; // unknown here: not this screen's filter
  const want = list(c.value);
  const real = have.filter((x) => x !== "none");
  switch (c.op) {
    case "is":
      return want.some((w) => have.includes(w));
    case "is_not":
      return !want.some((w) => have.includes(w));
    case "empty":
      return real.length === 0;
    case "not_empty":
      return real.length > 0;
    default:
      return true;
  }
}

/** A predicate for the value (built once per value, applied to every row). */
export function rowMatcher(value: FilterValue, meId: number | undefined): (row: TaskRow) => boolean {
  const words = fold(value.text.trim()).split(/\s+/).filter(Boolean);
  const keyText = value.text.trim().toUpperCase();
  const groups = new Map<string, SearchCond[]>();
  const rest: SearchCond[] = [];
  for (const c of value.chips) {
    if (c.field === "text") {
      words.push(...fold(String(list(c.value)[0] ?? "")).split(/\s+/).filter(Boolean));
      continue;
    }
    if (c.op === "is") groups.set(c.field, [...(groups.get(c.field) ?? []), c]);
    else rest.push(c);
  }
  return (row) => {
    if (words.length && row.key !== keyText) {
      const hay = fold(`${row.key} ${row.title}`);
      if (!words.every((w) => hay.includes(w))) return false;
    }
    for (const g of groups.values()) if (!g.some((c) => matchCond(row, c, meId))) return false;
    return rest.every((c) => matchCond(row, c, meId));
  };
}
