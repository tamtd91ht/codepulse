"use client";

import { useEffect, useSyncExternalStore } from "react";

/*
 * The ordered task keys the screen behind the task drawer shows (docs/specs/31 §22.6), so `J` / `K`
 * in the drawer go to the next / previous task of *that* list. A module store, not a context: Board,
 * List, Backlog and Việc của tôi register with one hook call and need no provider or prop.
 * The last screen to register wins; a screen that unmounts clears only its own list.
 */

let keys: readonly string[] = [];
let owner = 0;
let nextOwner = 1;
const subs = new Set<() => void>();

function emit() {
  for (const fn of subs) fn();
}

/** Replace the list (the keys in the order the screen shows them). */
export function setDrawerList(next: readonly string[]): void {
  keys = [...next];
  owner = 0;
  emit();
}

export function getDrawerList(): readonly string[] {
  return keys;
}

function subscribe(fn: () => void) {
  subs.add(fn);
  return () => {
    subs.delete(fn);
  };
}

const EMPTY: readonly string[] = [];

/** The current list, re-rendering when a screen registers a new one. */
export function useDrawerKeys(): readonly string[] {
  return useSyncExternalStore(subscribe, getDrawerList, () => EMPTY);
}

/** The key `step` places from `current` in the list (+1 next, -1 previous); null at either end or when absent. */
export function stepKey(list: readonly string[], current: string, step: 1 | -1): string | null {
  const i = list.indexOf(current);
  if (i < 0) return null;
  return list[i + step] ?? null;
}

/**
 * Register the keys this screen shows, in display order: `useDrawerList(rows.map((t) => t.key))`.
 * Re-registers only when the order changes (compared as one string), clears on unmount.
 */
export function useDrawerList(list: readonly string[]): void {
  const sig = list.join("\n");
  useEffect(() => {
    const me = nextOwner++;
    keys = sig ? sig.split("\n") : [];
    owner = me;
    emit();
    return () => {
      if (owner !== me) return;
      keys = [];
      owner = 0;
      emit();
    };
  }, [sig]);
}
