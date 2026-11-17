"use client";

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

function fromUrl(): FilterValue | null {
  try {
    const p = new URLSearchParams(window.location.search);
    if (!p.has("f") && !p.has("fq")) return null;
    return { text: p.get("fq") ?? "", chips: unpackChips(p.get("f")) };
  } catch {
    return null;
  }
}

function writeUrl(v: FilterValue, push: boolean) {
  const p = new URLSearchParams(window.location.search);
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
