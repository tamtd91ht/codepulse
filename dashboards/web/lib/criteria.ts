/* Weighted criteria sets (docs/specs/22): client-side checks that mirror the server's
   (app/scoring/criteria.py Components), weight arithmetic, version diffs and a leave guard. */

import { useEffect, useRef } from "react";

import type { CriteriaComponent, CriteriaMeta } from "./types";

/** Fixed colours by position, so a component keeps its colour in the bar, matrix and charts. */
export const CRIT_COLORS = Array.from({ length: 12 }, (_, i) => `var(--crit-${i + 1})`);
export const critColor = (i: number) => CRIT_COLORS[i % CRIT_COLORS.length];

/** The fallback when /criteria/meta is not there yet: the same values as the server. */
export const DEFAULT_META: CriteriaMeta = {
  categories: [
    { key: "security", label: "Bảo mật" },
    { key: "bug", label: "Lỗi chức năng" },
    { key: "performance", label: "Hiệu năng" },
    { key: "maintainability", label: "Dễ bảo trì" },
    { key: "testing", label: "Kiểm thử" },
    { key: "style", label: "Phong cách mã" },
    { key: "docs", label: "Tài liệu" },
  ],
  limits: { min_components: 2, max_components: 12, name_max: 60, description_max: 500 },
  templates: [],
};
export const SET_NAME_MAX = 100;
export const KEY_RE = /^[a-z][a-z0-9_]{1,30}$/;

/** A component being edited: a stable id for React and drag, and whether its key is fixed. */
export interface DraftComponent extends CriteriaComponent {
  uid: string;
  /** The key exists in the saved version: keys are immutable (docs/specs/22 §1). */
  locked: boolean;
  /** The admin typed the key; until then it follows the name. */
  keyTouched: boolean;
}

let seq = 0;
export const uid = () => `c${Date.now().toString(36)}${(seq++).toString(36)}`;

export function toDraft(components: CriteriaComponent[], locked: boolean): DraftComponent[] {
  return components.map((c) => ({
    ...c,
    description: c.description ?? "",
    categories: [...c.categories],
    uid: uid(),
    locked,
    keyTouched: true,
  }));
}

export function fromDraft(components: DraftComponent[]): CriteriaComponent[] {
  return components.map((c) => ({
    key: c.key,
    name: c.name.trim(),
    description: c.description.trim(),
    weight: c.weight,
    categories: c.categories,
  }));
}

/** "Bảo mật & dữ liệu" → "bao_mat_du_lieu" (a key the server accepts, when the name allows). */
export function slugify(name: string): string {
  const s = name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/gi, "d")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[0-9_]+/, "")
    .slice(0, 31)
    .replace(/_+$/, "");
  return s.length >= 2 ? s : "";
}

/** A key not used by the others: `base`, `base_2`, … */
export function uniqueKey(base: string, taken: string[]): string {
  const root = base || "thanh_phan";
  if (!taken.includes(root)) return root;
  for (let i = 2; ; i++) {
    const k = `${root.slice(0, 28)}_${i}`;
    if (!taken.includes(k)) return k;
  }
}

/** Weights summing to exactly 100, as even as whole numbers allow (the first ones get the rest). */
export function evenWeights(n: number): number[] {
  if (n <= 0) return [];
  const base = Math.floor(100 / n);
  return Array.from({ length: n }, (_, i) => base + (i < 100 - base * n ? 1 : 0));
}

/** Scale weights to sum to 100 keeping their ratio (largest remainder), each at least 1. */
export function normalizeWeights(weights: number[]): number[] {
  const n = weights.length;
  if (!n) return [];
  const safe = weights.map((w) => (Number.isFinite(w) && w > 0 ? w : 0));
  const total = safe.reduce((a, b) => a + b, 0);
  if (total === 0) return evenWeights(n);
  const raw = safe.map((w) => (w * 100) / total);
  const out = raw.map((r) => Math.max(1, Math.floor(r)));
  let diff = 100 - out.reduce((a, b) => a + b, 0);
  const order = raw.map((r, i) => ({ i, rem: r - Math.floor(r) })).sort((a, b) => b.rem - a.rem);
  for (let k = 0; diff > 0; k++, diff--) out[order[k % n].i] += 1;
  // Raising tiny weights to 1 can overshoot: take it back from the largest.
  while (diff < 0) {
    const big = out.indexOf(Math.max(...out));
    out[big] -= 1;
    diff++;
  }
  return out;
}

export interface CritError {
  /** The id of the field to focus (the error summary links to it). */
  field: string;
  message: string;
}

export const fieldId = {
  setName: "crit-set-name",
  setDesc: "crit-set-desc",
  name: (uid: string) => `crit-${uid}-name`,
  key: (uid: string) => `crit-${uid}-key`,
  desc: (uid: string) => `crit-${uid}-desc`,
  weight: (uid: string) => `crit-${uid}-weight`,
  category: (cat: string) => `crit-cat-${cat}`,
  total: "crit-total",
  list: "crit-components",
};

/** Every reason the server would refuse the draft, in reading order (docs/specs/22 §6). */
export function validate(
  name: string,
  components: DraftComponent[],
  meta: CriteriaMeta,
): CritError[] {
  const { min_components: min, max_components: max, name_max: nameMax, description_max: descMax } = meta.limits;
  const errors: CritError[] = [];
  if (!name.trim()) errors.push({ field: fieldId.setName, message: "Tên bộ tiêu chí không được trống." });
  else if (name.trim().length > SET_NAME_MAX)
    errors.push({ field: fieldId.setName, message: `Tên bộ tối đa ${SET_NAME_MAX} ký tự.` });
  if (components.length < min || components.length > max)
    errors.push({ field: fieldId.list, message: `Bộ cần từ ${min} đến ${max} thành phần (đang ${components.length}).` });
  const keys = new Map<string, number>();
  const names = new Map<string, number>();
  for (const c of components) {
    keys.set(c.key, (keys.get(c.key) ?? 0) + 1);
    const n = c.name.trim().toLowerCase();
    if (n) names.set(n, (names.get(n) ?? 0) + 1);
  }
  components.forEach((c, i) => {
    const label = c.name.trim() || `Thành phần ${i + 1}`;
    if (!c.name.trim()) errors.push({ field: fieldId.name(c.uid), message: `Thành phần ${i + 1}: tên không được trống.` });
    else if (c.name.trim().length > nameMax)
      errors.push({ field: fieldId.name(c.uid), message: `${label}: tên tối đa ${nameMax} ký tự.` });
    else if ((names.get(c.name.trim().toLowerCase()) ?? 0) > 1)
      errors.push({ field: fieldId.name(c.uid), message: `${label}: tên bị trùng với thành phần khác.` });
    if (!KEY_RE.test(c.key))
      errors.push({
        field: fieldId.key(c.uid),
        message: `${label}: mã chỉ gồm chữ thường, số, gạch dưới; 2–31 ký tự, bắt đầu bằng chữ.`,
      });
    else if ((keys.get(c.key) ?? 0) > 1)
      errors.push({ field: fieldId.key(c.uid), message: `${label}: mã "${c.key}" bị trùng.` });
    if (c.description.length > descMax)
      errors.push({ field: fieldId.desc(c.uid), message: `${label}: mô tả tối đa ${descMax} ký tự.` });
    if (!Number.isInteger(c.weight) || c.weight < 1 || c.weight > 100)
      errors.push({ field: fieldId.weight(c.uid), message: `${label}: trọng số là số nguyên từ 1 đến 100.` });
  });
  const total = components.reduce((s, c) => s + (Number.isFinite(c.weight) ? c.weight : 0), 0);
  if (total !== 100)
    errors.push({ field: fieldId.total, message: `Tổng trọng số phải bằng 100 (đang ${total}).` });
  const assigned = new Set(components.flatMap((c) => c.categories));
  for (const cat of meta.categories)
    if (!assigned.has(cat.key))
      errors.push({ field: fieldId.category(cat.key), message: `Loại "${cat.label}" chưa thuộc thành phần nào.` });
  return errors;
}

/** Whether saving makes a new version: components or the cap changed (name / description do not). */
export function versionChanged(
  before: { components: CriteriaComponent[]; apply_caps: boolean } | null,
  after: { components: CriteriaComponent[]; apply_caps: boolean },
): boolean {
  if (!before) return true;
  if (before.apply_caps !== after.apply_caps) return true;
  return JSON.stringify(canon(before.components)) !== JSON.stringify(canon(after.components));
}

function canon(components: CriteriaComponent[]) {
  return components.map((c) => ({
    key: c.key,
    name: c.name.trim(),
    description: (c.description ?? "").trim(),
    weight: c.weight,
    categories: [...c.categories].sort(),
  }));
}

export interface ComponentDiff {
  key: string;
  status: "added" | "removed" | "changed" | "same";
  name: string;
  before: CriteriaComponent | null;
  after: CriteriaComponent | null;
  changes: string[];
}

/** What changed from version A to B, component by component (by key). */
export function diffComponents(
  a: CriteriaComponent[],
  b: CriteriaComponent[],
  categoryLabel: (key: string) => string,
): ComponentDiff[] {
  const before = new Map(a.map((c) => [c.key, c]));
  const after = new Map(b.map((c) => [c.key, c]));
  const keys = [...b.map((c) => c.key), ...a.map((c) => c.key).filter((k) => !after.has(k))];
  return keys.map((key) => {
    const x = before.get(key) ?? null;
    const y = after.get(key) ?? null;
    if (!x) return { key, status: "added", name: y!.name, before: null, after: y, changes: [] };
    if (!y) return { key, status: "removed", name: x.name, before: x, after: null, changes: [] };
    const changes: string[] = [];
    if (x.name !== y.name) changes.push(`Tên: "${x.name}" → "${y.name}"`);
    if (x.weight !== y.weight) changes.push(`Trọng số: ${x.weight}% → ${y.weight}%`);
    const xs = new Set(x.categories);
    const ys = new Set(y.categories);
    const plus = y.categories.filter((c) => !xs.has(c)).map(categoryLabel);
    const minus = x.categories.filter((c) => !ys.has(c)).map(categoryLabel);
    if (plus.length) changes.push(`Thêm loại: ${plus.join(", ")}`);
    if (minus.length) changes.push(`Bỏ loại: ${minus.join(", ")}`);
    if ((x.description ?? "").trim() !== (y.description ?? "").trim()) changes.push("Mô tả đã đổi");
    return { key, status: changes.length ? "changed" : "same", name: y.name, before: x, after: y, changes };
  });
}

export const toNum = (v: number | string | null | undefined): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** 78.5 → "78,5"; whole numbers without decimals. */
export function score1(v: number | string | null | undefined): string {
  const n = toNum(v);
  if (n === null) return "—";
  return n.toLocaleString("vi-VN", { maximumFractionDigits: 1 });
}

export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/* ---------- Leaving with unsaved changes ---------- */

let guard: (() => boolean) | null = null;

/** True when it is fine to leave the current view (nothing unsaved, or the user said so). */
export function confirmLeave(): boolean {
  return guard ? guard() : true;
}

const LEAVE_TEXT = "Bộ tiêu chí có thay đổi chưa lưu. Rời trang và bỏ các thay đổi này?";

/**
 * While `dirty`: the browser asks before closing or reloading, a click on a link elsewhere in the
 * app asks first, and `confirmLeave()` (Admin tab switches, Back) asks too.
 */
export function useLeaveGuard(dirty: boolean) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    const ask = () => !dirtyRef.current || window.confirm(LEAVE_TEXT);
    guard = ask;
    const onUnload = (e: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    // Capture on the document runs before React's root listener, so Next's <Link> never fires.
    const onClick = (e: MouseEvent) => {
      if (!dirtyRef.current || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const a = (e.target as Element | null)?.closest?.("a[href]");
      if (!a || a.getAttribute("target") === "_blank") return;
      const href = a.getAttribute("href") ?? "";
      if (href.startsWith("#") && href.length > 1 && !href.startsWith("#criteria")) {
        // Another Admin tab: the hash change handler asks.
        return;
      }
      if (href.startsWith("#")) return;
      if (!window.confirm(LEAVE_TEXT)) {
        e.preventDefault();
        e.stopPropagation();
      } else {
        dirtyRef.current = false;
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      if (guard === ask) guard = null;
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, []);
  /** Call after a save, so leaving right away does not ask. */
  return () => {
    dirtyRef.current = false;
  };
}
