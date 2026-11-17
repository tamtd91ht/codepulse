"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import type { SearchCond, SearchTree } from "@/lib/types";

import { type ChipCond, chipId, toTree } from "./FilterChips";

/*
 * The state behind the FilterBar (docs/specs/31 §22.2): the search text plus the chips, kept on the
 * URL (`fq` = text, `f` = chips as JSON, `v` = display options) so a link shares the filter and
 * Back / Forward walk through it, and remembered per person × dự án × màn in localStorage so
 * coming back to a screen brings its last filter. A link wins over the memory.
 */

export interface FilterValue {
  /** Free text: key, title, description (the `text` condition). */
  text: string;
  chips: ChipCond[];
}

export const EMPTY_FILTER: FilterValue = { text: "", chips: [] };

/** The value as a search tree: the text, then the chips ("là" chips of one field ORed). */
export function filterTree(v: FilterValue): SearchTree {
  const text: SearchCond[] = v.text.trim() ? [{ field: "text", op: "is", value: v.text.trim() }] : [];
  return toTree([...text, ...v.chips]);
}

/** How many filters are on (the text counts as one). */
export const filterCount = (v: FilterValue) => v.chips.length + (v.text.trim() ? 1 : 0);

type Packed = [field: string, op: SearchCond["op"], value?: SearchCond["value"]];

export function packChips(chips: ChipCond[]): string {
  return JSON.stringify(chips.map((c): Packed => (c.value === undefined ? [c.field, c.op] : [c.field, c.op, c.value])));
}

export function unpackChips(raw: string | null): ChipCond[] {
  if (!raw) return [];
  try {
    const list: unknown = JSON.parse(raw);
    if (!Array.isArray(list)) return [];
    const out: ChipCond[] = [];
    for (const item of list.slice(0, 60)) {
      if (!Array.isArray(item) || typeof item[0] !== "string" || typeof item[1] !== "string") continue;
      const value = item[2] as SearchCond["value"];
      out.push({ id: chipId(), field: item[0], op: item[1] as SearchCond["op"], ...(value === undefined ? {} : { value }) });
    }
    return out;
  } catch {
    return [];
  }
}

const memoryKey = (screen: string, bp: number, userId: number) => `cp:fb:${userId}:${bp}:${screen}`;

function readMemory(key: string): FilterValue | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const m = JSON.parse(raw) as { text?: unknown; f?: unknown };
    return { text: typeof m.text === "string" ? m.text : "", chips: unpackChips(typeof m.f === "string" ? m.f : null) };
  } catch {
    return null;
  }
}

function writeMemory(key: string, v: FilterValue) {
  try {
    if (!filterCount(v)) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify({ text: v.text, f: packChips(v.chips) }));
  } catch {
    /* private window: the filter lasts for this page only */
  }
}

/*
 * Links into the Danh sách (docs/specs/31 §22.9). The FilterBar reads `f` / `fq`; older links, the
 * Ctrl+K palette and saved filters of the GET /tasks era carry its parameter names instead
 * (`category=done&resolution=done&updated_from=…`, `assignee=12`, `q=…`). `fromQuery` turns those
 * into chips the way the server's `search.from_query` does, so both kinds of link land on the same
 * filter; `listHref` builds a link with `f` directly.
 */

/** GET /tasks parameters that are filters (the rest — space, tab, task, sort … — are not). */
const QUERY_FIELDS = new Set(["status", "version", "category", "type", "priority", "resolution", "assignee", "reporter", "watcher", "label", "component", "sprint", "parent"]);
export const LEGACY_PARAMS = ["q", "due", "updated_from", ...QUERY_FIELDS];

/** "YYYY-MM-DD" of the day before (a date filter "from D" is "after D − 1"). */
export const dayBefore = (day: string): string | null => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) - 1));
  return d.toISOString().slice(0, 10);
};

/** GET /tasks parameters → a filter value (one "là" chip per value; comma lists split). */
export function fromQuery(query: Record<string, string | string[] | undefined> | URLSearchParams): FilterValue {
  const get = (k: string): string[] => {
    const raw = query instanceof URLSearchParams ? query.getAll(k) : query[k];
    const list = raw === undefined ? [] : Array.isArray(raw) ? raw : [raw];
    return list.flatMap((x) => String(x).split(",")).map((x) => x.trim()).filter(Boolean);
  };
  const chips: ChipCond[] = [];
  const add = (c: SearchCond) => chips.push({ ...c, id: chipId() });
  const due = get("due")[0];
  if (due) add({ field: "due", op: "is", value: due });
  const from = get("updated_from")[0];
  const after = from ? dayBefore(from) : null; // "from that day" = after the day before
  if (after) add({ field: "updated", op: "after", value: after });
  for (const field of QUERY_FIELDS) for (const v of get(field)) add({ field, op: "is", value: [v] });
  return { text: get("q")[0] ?? "", chips };
}

/**
 * `/tasks?space=&tab=list&f=…` for a filter, given as a value or as GET /tasks parameters;
 * `sort` / `order` set the List's first sort.
 */
export function listHref(bp: number, v: FilterValue | Record<string, string>, sort?: { sort: string; order?: "asc" | "desc" }): string {
  const value: FilterValue = Array.isArray(v.chips) ? (v as FilterValue) : fromQuery(v as Record<string, string>);
  const p = new URLSearchParams({ space: String(bp), tab: "list" });
  if (value.chips.length) p.set("f", packChips(value.chips));
  if (value.text.trim()) p.set("fq", value.text.trim());
  if (sort) {
    p.set("sort", sort.sort);
    if (sort.order) p.set("order", sort.order);
  }
  return `/tasks?${p}`;
}

function fromUrl(): FilterValue | null {
  try {
    const p = new URLSearchParams(window.location.search);
    if (p.has("f") || p.has("fq")) return { text: p.get("fq") ?? "", chips: unpackChips(p.get("f")) };
    // An older link: its GET /tasks names, read once and rewritten as `f` / `fq`.
    if (LEGACY_PARAMS.some((k) => p.has(k))) {
      const v = fromQuery(p);
      return filterCount(v) ? v : null;
    }
    return null;
  } catch {
    return null;
  }
}

function writeUrl(v: FilterValue, push: boolean) {
  const p = new URLSearchParams(window.location.search);
  for (const k of LEGACY_PARAMS) p.delete(k);
  if (v.text.trim()) p.set("fq", v.text);
  else p.delete("fq");
  if (v.chips.length) p.set("f", packChips(v.chips));
  else p.delete("f");
  const next = `${window.location.pathname}${p.size ? `?${p}` : ""}`;
  if (next === `${window.location.pathname}${window.location.search}`) return;
  if (push) window.history.pushState(window.history.state, "", next);
  else window.history.replaceState(window.history.state, "", next);
}

/**
 * `[value, setValue, reset]` for one screen of one dự án. `setValue(next)` updates the URL (a new
 * history entry for a chip, the same one while the text is typed) and the memory; `reset()` is
 * "Khôi phục mặc định": no filter, memory dropped. `userId` may arrive later (/auth/me): the memory
 * is read then, unless the URL already had a filter or the person changed it meanwhile.
 */
export function useFilterState(screen: string, bp: number, userId: number | undefined): [FilterValue, (next: FilterValue) => void, () => void] {
  const [value, setValueState] = useState<FilterValue>(EMPTY_FILTER);
  const settled = useRef(false); // the URL or the person decided: the memory no longer applies
  const last = useRef<FilterValue>(EMPTY_FILTER);

  // Mount: a link's filter.
  useEffect(() => {
    const url = fromUrl();
    if (url) {
      settled.current = true;
      last.current = url;
      setValueState(url);
      writeUrl(url, false); // an older link's GET /tasks names become `f` / `fq`
    }
  }, []);

  // The memory, once the person is known.
  useEffect(() => {
    if (settled.current || userId === undefined) return;
    settled.current = true;
    const m = readMemory(memoryKey(screen, bp, userId));
    if (m && filterCount(m)) {
      last.current = m;
      setValueState(m);
      writeUrl(m, false);
    }
  }, [screen, bp, userId]);

  // A link followed while this screen is open (Ctrl+K "Mở Danh sách lọc theo …"): the router
  // changes the URL without a remount. Our own writes leave the packed value equal, so no loop.
  const params = useSearchParams();
  const linkSig = params ? `${params.get("f") ?? ""}\n${params.get("fq") ?? ""}\n${LEGACY_PARAMS.map((k) => params.getAll(k).join(",")).join("\n")}` : "";
  useEffect(() => {
    const url = fromUrl();
    if (!url) return;
    if (packChips(url.chips) === packChips(last.current.chips) && url.text === last.current.text) return;
    settled.current = true;
    last.current = url;
    setValueState(url);
    writeUrl(url, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkSig]);

  // Back / Forward.
  useEffect(() => {
    const onPop = () => {
      const url = fromUrl() ?? EMPTY_FILTER;
      last.current = url;
      setValueState(url);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const setValue = useCallback(
    (next: FilterValue) => {
      settled.current = true;
      const chipsChanged = packChips(next.chips) !== packChips(last.current.chips);
      last.current = next;
      setValueState(next);
      writeUrl(next, chipsChanged);
      if (userId !== undefined) writeMemory(memoryKey(screen, bp, userId), next);
    },
    [screen, bp, userId],
  );

  const reset = useCallback(() => setValue(EMPTY_FILTER), [setValue]);

  return [value, setValue, reset];
}

/**
 * Display options ("Hiển thị", §3.5): per person and screen in localStorage, not per filter; a
 * link carries the ones that differ from the defaults (`v`), so a shared view looks the same.
 */
export function useViewOptions<T extends Record<string, string | boolean | string[]>>(screen: string, userId: number | undefined, defaults: T): [T, (patch: Partial<T>) => void] {
  const [opts, setOpts] = useState<T>(defaults);
  const fromLink = useRef(false);
  const key = userId === undefined ? null : `cp:fb-view:${userId}:${screen}`;

  useEffect(() => {
    try {
      const raw = new URLSearchParams(window.location.search).get("v");
      if (!raw) return;
      fromLink.current = true;
      setOpts((o) => ({ ...o, ...pick(JSON.parse(raw), defaults) }));
    } catch {
      /* a broken link param: the defaults */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!key || fromLink.current) return;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw) setOpts((o) => ({ ...o, ...pick(JSON.parse(raw), defaults) }));
    } catch {
      /* storage blocked */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const update = useCallback(
    (patch: Partial<T>) => {
      setOpts((o) => {
        const next = { ...o, ...patch };
        const changed = Object.fromEntries(Object.entries(next).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(defaults[k])));
        try {
          if (key) window.localStorage.setItem(key, JSON.stringify(next));
        } catch {
          /* storage blocked */
        }
        try {
          const p = new URLSearchParams(window.location.search);
          if (Object.keys(changed).length) p.set("v", JSON.stringify(changed));
          else p.delete("v");
          window.history.replaceState(window.history.state, "", `${window.location.pathname}${p.size ? `?${p}` : ""}`);
        } catch {
          /* no URL to keep it on */
        }
        return next;
      });
    },
    [key, defaults],
  );

  return [opts, update];
}

/** Only known keys, of the default's type. */
function pick<T extends Record<string, unknown>>(raw: unknown, defaults: T): Partial<T> {
  if (!raw || typeof raw !== "object") return {};
  const out: Partial<T> = {};
  for (const k of Object.keys(defaults) as (keyof T)[]) {
    const v = (raw as Record<string, unknown>)[k as string];
    const d = defaults[k];
    if (Array.isArray(d) ? Array.isArray(v) && v.every((x) => typeof x === "string") : typeof v === typeof d) out[k] = v as T[keyof T];
  }
  return out;
}
