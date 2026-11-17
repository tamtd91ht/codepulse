"use client";

import { useEffect, useRef } from "react";
import { mutate } from "swr";

/**
 * Live Board / List / task detail (docs/specs/31 §21.7). One EventSource per dự án, shared by every
 * component of the page that watches it (Board and the open task's drawer use one connection).
 * Each `task` event `{type: "task", key, changed}` refreshes, after a short pause that gathers a
 * burst (a bulk edit sends one event per task), the SWR keys that show that dự án's tasks: the
 * lists `/tasks?…space=BP…`, the task `/tasks/KEY` and what hangs off it, the sprints. A dropped
 * stream reconnects with backoff (1 s, 2 s, 4 s … 60 s); after a reconnect everything of the dự án
 * is reloaded once, since the stream does not replay what was missed.
 */

export type TaskLiveEvent = { type: "task" | "space"; key: string; changed: string[] };

const GATHER_MS = 400;
const BACKOFF_MIN = 1_000;
const BACKOFF_MAX = 60_000;

type Channel = {
  count: number;
  source: EventSource | null;
  retry: ReturnType<typeof setTimeout> | undefined;
  flush: ReturnType<typeof setTimeout> | undefined;
  backoff: number;
  connectedOnce: boolean;
  keys: Set<string>;
  all: boolean;
  listeners: Set<(e: TaskLiveEvent) => void>;
};

const channels = new Map<number, Channel>();

function refresh(bp: number, ch: Channel) {
  const keys = ch.keys;
  const all = ch.all;
  ch.keys = new Set();
  ch.all = false;
  const space = new RegExp(`[?&]space=${bp}(&|$)`);
  void mutate((k) => {
    if (typeof k !== "string") return false;
    if (k.startsWith("/tasks/my-work")) return true;
    if (k.startsWith("/tasks?")) return space.test(k);
    if (k.startsWith(`/tasks/spaces/${bp}/sprints`) || k.startsWith(`/tasks/spaces/${bp}/epics`)) return true;
    if (!k.startsWith("/tasks/")) return false;
    const key = decodeURIComponent(k.slice(7).split(/[/?]/)[0]).toUpperCase();
    return all ? /^[A-Z][A-Z0-9]*-\d+$/.test(key) : keys.has(key);
  });
}

function schedule(bp: number, ch: Channel) {
  if (ch.flush) return;
  ch.flush = setTimeout(() => {
    ch.flush = undefined;
    refresh(bp, ch);
  }, GATHER_MS);
}

function connect(bp: number, ch: Channel) {
  if (ch.count <= 0) return;
  const source = new EventSource(`/api/v1/tasks/spaces/${bp}/live`);
  ch.source = source;
  source.addEventListener("ready", () => {
    ch.backoff = BACKOFF_MIN;
    if (ch.connectedOnce) {
      // Back after a gap: what changed meanwhile was not sent.
      ch.all = true;
      schedule(bp, ch);
    }
    ch.connectedOnce = true;
  });
  source.addEventListener("task", (event) => {
    let data: TaskLiveEvent;
    try {
      data = JSON.parse((event as MessageEvent<string>).data) as TaskLiveEvent;
    } catch {
      return;
    }
    if (!data?.key) return;
    // Many tasks at once (bulk edit, import, a rank respread): reload the dự án's tasks once.
    if (data.type === "space") ch.all = true;
    else ch.keys.add(data.key.toUpperCase());
    schedule(bp, ch);
    for (const listener of ch.listeners) listener(data);
  });
  source.onerror = () => {
    // The server ends a stream every few minutes (the browser reconnects on its own); a refused
    // or broken one is CLOSED: try again later, waiting longer each time.
    if (source.readyState !== EventSource.CLOSED) return;
    source.close();
    ch.source = null;
    const wait = ch.backoff;
    ch.backoff = Math.min(BACKOFF_MAX, ch.backoff * 2);
    ch.retry = setTimeout(() => connect(bp, ch), wait);
  };
}

/** Watch dự án `bp` (null: nothing); `onEvent` hears each change, after the refresh is queued. */
export function useTaskLive(bp: number | null | undefined, onEvent?: (e: TaskLiveEvent) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    if (bp === null || bp === undefined || typeof window === "undefined" || typeof EventSource === "undefined") return;
    let ch = channels.get(bp);
    if (!ch) {
      ch = { count: 0, source: null, retry: undefined, flush: undefined, backoff: BACKOFF_MIN, connectedOnce: false, keys: new Set(), all: false, listeners: new Set() };
      channels.set(bp, ch);
    }
    ch.count += 1;
    if (ch.count === 1) connect(bp, ch);
    const listener = (e: TaskLiveEvent) => handler.current?.(e);
    ch.listeners.add(listener);
    const channel = ch;
    return () => {
      channel.listeners.delete(listener);
      channel.count -= 1;
      if (channel.count > 0) return;
      channel.source?.close();
      clearTimeout(channel.retry);
      clearTimeout(channel.flush);
      channels.delete(bp);
    };
  }, [bp]);
}
