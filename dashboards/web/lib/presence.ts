"use client";

/*
 * Presence (docs/specs/33; the approach: docs/PRESENCE_PATTERN.md). Every signed-in tab keeps one
 * WebSocket open (`/api/v1/presence/ws`): while it is open the server counts the tab as alive, and
 * a closed tab or a dropped network is known at once. The tab only says when the person starts or
 * stops using it: `{active}`, true while there was a mouse, key, scroll or touch in the last
 * IDLE_MS. Nothing else leaves the browser: no page, no coordinates, no text.
 *
 * A browser whose socket never opens (a proxy without WebSocket) posts `/presence/beat` every
 * BEAT_MS instead and keeps trying the socket.
 *
 * `PresenceBeacon` is mounted once by the app shell for signed-in people and renders nothing.
 */

import { useEffect } from "react";

export const IDLE_MS = 5 * 60_000;
/** Fired on `window` when this tab's presence has been recorded by the server. */
export const PRESENCE_READY = "cp:presence-ready";
const BEAT_MS = 30_000;
const CHECK_MS = 15_000;
// The socket must answer `{"ok":true}` this soon after opening, or it does not count as working.
const READY_MS = 10_000;
const RETRY_MS = [1_000, 2_000, 5_000, 15_000, 30_000, 60_000];
// Failed attempts in a row before the tab posts instead (it keeps trying the socket).
const FALLBACK_AFTER = 2;
const SIGNED_OUT = 4401;
const BAD_REQUEST = 4400;
const TOO_MANY = 4429;
const INPUTS = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"] as const;

function randomId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A stable id kept in `storage`; a fresh one for this page when storage is unavailable. */
function storedId(storage: () => Storage, key: string): string {
  try {
    const store = storage();
    const found = store.getItem(key);
    if (found && /^[A-Za-z0-9_-]{8,40}$/.test(found)) return found;
    const made = randomId();
    store.setItem(key, made);
    return made;
  } catch {
    return randomId();
  }
}

function startPresence(): () => void {
  const tab = storedId(() => window.sessionStorage, "cp_tab");
  const device = storedId(() => window.localStorage, "cp_device");
  let lastInput = Date.now();
  let reported = true; // what the server was last told
  let socket: WebSocket | null = null;
  let ready = false; // the socket answered
  let posted = false; // a beat was accepted since posting began
  let failures = 0;
  let stopped = false;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let readyTimer: ReturnType<typeof setTimeout> | null = null;
  let beatTimer: ReturnType<typeof setInterval> | null = null;

  const active = () => Date.now() - lastInput < IDLE_MS;

  const post = (path: string, body: object) =>
    fetch(`/api/v1/presence/${path}`, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

  const stopForGood = () => {
    stopped = true;
    stopPosting(false);
    if (retryTimer) clearTimeout(retryTimer);
  };

  /** The session ended (it expired, or an admin signed the person out, §14): this tab can do
   *  nothing more, so it goes to the sign-in page instead of waiting for a click to fail. */
  const signedOut = () => {
    stopForGood();
    if (window.location.pathname !== "/login") window.location.href = "/login";
  };

  const beat = () => {
    reported = active();
    const first = !posted;
    post("beat", { tab, device, active: reported })
      .then((res) => {
        if (res.status === 401) signedOut();
        else if (res.status === 403) stopForGood();
        else if (res.ok && first) {
          posted = true;
          window.dispatchEvent(new Event(PRESENCE_READY));
        }
      })
      .catch(() => undefined);
  };

  const startPosting = () => {
    if (beatTimer || stopped) return;
    beat();
    beatTimer = setInterval(beat, BEAT_MS);
  };

  function stopPosting(sayLeave: boolean) {
    if (!beatTimer) return;
    clearInterval(beatTimer);
    beatTimer = null;
    posted = false;
    if (sayLeave) void post("leave", { tab }).catch(() => undefined);
  }

  const say = () => {
    const now = active();
    if (now === reported) return;
    if (socket && ready && socket.readyState === WebSocket.OPEN) {
      reported = now;
      socket.send(JSON.stringify({ active: now }));
    } else if (beatTimer) {
      beat();
    }
  };

  const retry = () => {
    if (stopped || retryTimer) return;
    const wait = RETRY_MS[Math.min(failures, RETRY_MS.length) - 1] ?? RETRY_MS[0];
    retryTimer = setTimeout(() => {
      retryTimer = null;
      connect();
    }, wait);
  };

  function connect() {
    if (stopped || socket) return;
    let ws: WebSocket;
    try {
      const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${scheme}//${window.location.host}/api/v1/presence/ws?tab=${tab}&device=${device}`);
    } catch {
      failures += 1;
      if (failures >= FALLBACK_AFTER) startPosting();
      retry();
      return;
    }
    socket = ws;
    ready = false;
    readyTimer = setTimeout(() => ws.close(), READY_MS);
    ws.onmessage = () => {
      if (ready) return;
      ready = true;
      failures = 0;
      if (readyTimer) clearTimeout(readyTimer);
      stopPosting(true); // the socket took over
      reported = true; // a new socket starts as "in use"
      say();
      // The monitoring screen may have loaded before this tab was counted: let it read again.
      window.dispatchEvent(new Event(PRESENCE_READY));
    };
    ws.onclose = (event) => {
      if (readyTimer) clearTimeout(readyTimer);
      if (socket === ws) socket = null;
      // The server accepts, then closes with a code: signed out, bad ids, or too many tabs.
      if (event.code === SIGNED_OUT) {
        signedOut();
        return;
      }
      if (event.code === BAD_REQUEST || event.code === TOO_MANY) {
        stopForGood();
        return;
      }
      if (stopped) return;
      failures = ready ? 1 : failures + 1;
      ready = false;
      if (failures >= FALLBACK_AFTER) startPosting();
      retry();
    };
  }

  const onInput = () => {
    lastInput = Date.now();
    if (!reported) say();
  };
  const reconnectNow = () => {
    if (stopped || socket) return;
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
    connect();
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") reconnectNow();
  };
  const onHide = () => {
    // The browser closes the socket itself; a posting tab says goodbye.
    if (beatTimer) void post("leave", { tab }).catch(() => undefined);
  };
  const onShow = (event: PageTransitionEvent) => {
    if (!event.persisted) return; // back from the back/forward cache: its socket is gone
    reconnectNow();
    if (beatTimer) beat();
  };

  for (const name of INPUTS) window.addEventListener(name, onInput, { passive: true, capture: true });
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("pagehide", onHide);
  window.addEventListener("pageshow", onShow);
  window.addEventListener("online", reconnectNow);
  const checkTimer = setInterval(say, CHECK_MS);
  connect();

  return () => {
    stopped = true;
    clearInterval(checkTimer);
    if (retryTimer) clearTimeout(retryTimer);
    if (readyTimer) clearTimeout(readyTimer);
    stopPosting(true);
    for (const name of INPUTS) window.removeEventListener(name, onInput, { capture: true });
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("pagehide", onHide);
    window.removeEventListener("pageshow", onShow);
    window.removeEventListener("online", reconnectNow);
    socket?.close();
    socket = null;
  };
}

/** Mounted once by the app shell for signed-in people; renders nothing. */
export function PresenceBeacon() {
  useEffect(() => {
    if (typeof window === "undefined" || typeof WebSocket === "undefined") return;
    return startPresence();
  }, []);
  return null;
}

/* ---- the monitoring screen's data ---- */

export type PresenceState = "online" | "away" | "offline";

export interface PresenceTotals {
  online_s: number;
  away_s: number;
  offline_s: number;
  first_seen: string | null;
  last_seen: string | null;
  sessions: number;
}

export interface PresencePerson {
  id: number;
  full_name: string | null;
  email: string | null;
  avatar_url: string | null;
}

export interface PresenceDevice {
  /** "Chrome · Windows": browser and system, from the User-Agent. */
  label: string;
  kind: string;
  tabs: number;
  active: boolean;
  since: string;
}

export interface PresenceRow {
  user: PresencePerson;
  teams: { id: number; name: string }[];
  state: PresenceState;
  since: string | null;
  tabs: number;
  devices: number;
  kinds: string[];
  /** The devices behind `devices` / `tabs`, those in use first. */
  open: PresenceDevice[];
  day: PresenceTotals;
}

export interface PresenceBoard {
  now: string;
  day: string;
  today: string;
  tz: string;
  scope: "own" | "team" | "all";
  /** False: the live store did not answer; states come from the history. */
  live: boolean;
  counts: Record<PresenceState, number>;
  teams: { id: number; name: string }[];
  rows: PresenceRow[];
  idle_minutes: number;
  keep_days: number;
  /** docs/specs/33 §14: the viewer (an admin) may sign people out. */
  can_sign_out: boolean;
}

export interface PresenceStretch {
  state: "online" | "away";
  start: string;
  end: string;
}

export interface PresenceDay extends PresenceTotals {
  day: string;
  start: string;
  end: string;
  stretches: PresenceStretch[];
}

export interface PresenceUserDays {
  user: PresencePerson;
  from: string;
  to: string;
  tz: string;
  now: string;
  totals: { online_s: number; away_s: number; offline_s: number };
  days: PresenceDay[];
}

export interface PresenceFrame {
  state: PresenceState;
  start: string;
  end: string;
  seconds: number;
}

/** Gaps shorter than this are not frames of their own (a page reload leaves a second or two). */
const FRAME_GAP_MS = 60_000;

/**
 * A day's stretches as the frames one reads: "08:24 – 10:30 Online", and the offline frames
 * between them (offline is not stored: it is the gap). Neighbours in the same state with less
 * than a minute between them are one frame. Nothing before the first frame or after the last:
 * that is not "offline time" (docs/specs/33 §6).
 */
export function framesOf(stretches: PresenceStretch[]): PresenceFrame[] {
  const merged: { state: PresenceState; from: number; to: number }[] = [];
  for (const s of stretches) {
    const from = new Date(s.start).getTime();
    const to = new Date(s.end).getTime();
    const last = merged[merged.length - 1];
    if (last && from - last.to < FRAME_GAP_MS) {
      if (last.state === s.state) {
        last.to = Math.max(last.to, to);
        continue;
      }
      merged.push({ state: s.state, from: last.to, to }); // no sliver of a gap between them
      continue;
    }
    if (last) merged.push({ state: "offline", from: last.to, to: from });
    merged.push({ state: s.state, from, to });
  }
  return merged
    .filter((f) => f.to > f.from)
    .map((f) => ({
      state: f.state,
      start: new Date(f.from).toISOString(),
      end: new Date(f.to).toISOString(),
      seconds: Math.round((f.to - f.from) / 1000),
    }));
}

export const STATE_LABEL: Record<PresenceState, string> = {
  online: "Online",
  away: "Tạm vắng",
  offline: "Offline",
};

/** 7 530 s -> "2 giờ 5 phút"; under a minute -> "< 1 phút"; 0 -> "–". */
export function duration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "–";
  if (seconds < 60) return "< 1 phút";
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return hours % 24 ? `${days} ngày ${hours % 24} giờ` : `${days} ngày`;
  }
  if (!hours) return `${minutes} phút`;
  return minutes % 60 ? `${hours} giờ ${minutes % 60} phút` : `${hours} giờ`;
}

/** "09:05" in the business time zone. */
export function clock(value: string | null, tz: string): string {
  if (!value) return "–";
  return new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz }).format(new Date(value));
}
