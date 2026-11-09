"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { localDay, shiftDays, shiftMonths, shiftWeeks, thisMonth, thisWeek, weekOf } from "@/lib/format";

/*
 * One time filter for the whole dashboard. Whatever period a screen picks (Tổng quan, Insights,
 * a dự án, a team, Thành viên, the reports, DPI) becomes the period every other screen opens
 * with, until someone picks another one. Kept in localStorage so it survives a reload and other
 * tabs follow it; a choice older than SCOPE_TTL is dropped so a new working day starts from each
 * screen's own default. Never chosen (or expired) → null, and each screen uses its own default.
 *
 * Screens show the shared period in their own vocabulary: a week on a 7/30/90-day screen is a
 * custom range of that week's days; a range on a week/month screen that cannot ask for ranges
 * is the week or month around its last day. Only an explicit change writes the shared period,
 * so merely opening a screen never moves it.
 */

export type TimeScope =
  | { kind: "week"; start: string } // the Monday
  | { kind: "month"; start: string } // the first day
  | { kind: "range"; from: string; to: string }
  | { kind: "last"; days: number }; // rolling: the last `days` days up to today

const KEY = "cp:time-scope";
const SCOPE_TTL = 12 * 3_600_000;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function valid(s: unknown): s is TimeScope {
  if (!s || typeof s !== "object") return false;
  const v = s as Record<string, unknown>;
  if (v.kind === "week" || v.kind === "month") return typeof v.start === "string" && DAY.test(v.start);
  if (v.kind === "range") return typeof v.from === "string" && typeof v.to === "string" && DAY.test(v.from) && DAY.test(v.to) && v.from <= v.to;
  if (v.kind === "last") return typeof v.days === "number" && Number.isInteger(v.days) && v.days > 0 && v.days <= 3660;
  return false;
}

function read(): TimeScope | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const { scope, at } = JSON.parse(raw) as { scope?: unknown; at?: unknown };
    if (typeof at !== "number" || Date.now() - at > SCOPE_TTL || !valid(scope)) return null;
    return scope;
  } catch {
    return null;
  }
}

let cache: TimeScope | null | undefined;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

function snapshot(): TimeScope | null {
  if (cache === undefined) cache = read();
  return cache;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab picked a period: follow it.
  const onStorage = (e: StorageEvent) => {
    if (e.key !== KEY) return;
    cache = read();
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

const same = (a: TimeScope | null, b: TimeScope | null) => JSON.stringify(a) === JSON.stringify(b);

/** Make `scope` the period of every screen. */
export function setTimeScope(scope: TimeScope): void {
  if (!valid(scope)) return;
  const prev = snapshot();
  cache = scope;
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ scope, at: Date.now() }));
  } catch {
    /* blocked storage: shared within this tab only */
  }
  if (!same(prev, scope)) notify();
}

/** The shared period; null on the server, before mount, or when none is chosen. */
export function useTimeScope(): TimeScope | null {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

/** False on the server and the first client render: "today" is the viewer's, not the server's. */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

/* ---- the shared period as days ---- */

/** The days a period covers, the end clipped to today. */
export function scopeDays(s: TimeScope, today = localDay()): { from: string; to: string } {
  const clip = (d: string) => (d < today ? d : today);
  switch (s.kind) {
    case "week":
      return { from: s.start, to: clip(shiftDays(s.start, 6)) };
    case "month":
      return { from: s.start, to: clip(shiftDays(shiftMonths(s.start, 1), -1)) };
    case "range":
      return { from: s.from, to: clip(s.to) };
    case "last":
      return { from: shiftDays(today, -(s.days - 1)), to: today };
  }
}

const span = (from: string, to: string) => Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) + 1;

/* ---- week / month / range screens ---- */

export type CyclePeriod = "week" | "month" | "range";

export interface CycleOptions {
  /** The screen can ask for a custom range of days. */
  range?: boolean;
  /** Earliest day a range may start. */
  rangeMin?: string;
  /** Longest range, in days. */
  rangeMaxDays?: number;
}

export interface CycleState {
  period: CyclePeriod;
  /** Monday of the week or first day of the month (week / month). */
  start: string;
  /** The range (period "range"). */
  range: { from: string; to: string };
  /** The shared period is a range this screen cannot show: it shows the calendar period around its end. */
  approximated: boolean;
}

/** A shared period as a week/month/range screen shows it. */
export function toCycle(s: TimeScope | null, opts: CycleOptions = {}, today = localDay()): CycleState {
  const fallbackRange = scopeDays({ kind: "week", start: thisWeek() }, today);
  if (!s) return { period: "week", start: thisWeek(), range: fallbackRange, approximated: false };
  if (s.kind === "week" || s.kind === "month") return { period: s.kind, start: s.start, range: scopeDays(s, today), approximated: false };
  let { from, to } = scopeDays(s, today);
  if (opts.range) {
    if (opts.rangeMaxDays && span(from, to) > opts.rangeMaxDays) from = shiftDays(to, -(opts.rangeMaxDays - 1));
    if (opts.rangeMin && from < opts.rangeMin) from = opts.rangeMin;
    if (to < from) to = from;
    return { period: "range", start: weekOf(to), range: { from, to }, approximated: false };
  }
  // A week/month-only screen: the week of a range of up to 7 days inside one week, else its month.
  if (span(from, to) <= 7 && weekOf(from) === weekOf(to)) return { period: "week", start: weekOf(to), range: { from, to }, approximated: true };
  return { period: "month", start: `${to.slice(0, 7)}-01`, range: { from, to }, approximated: true };
}

/**
 * The shared period on a Tuần / Tháng (/ Khoảng ngày) screen, with the moves its header offers.
 * Null until mounted.
 */
export function useCycleScope(opts: CycleOptions = {}) {
  const shared = useTimeScope();
  const mounted = useMounted();
  if (!mounted) return null;
  const today = localDay();
  const state = toCycle(shared, opts, today);
  const { period, start, range } = state;
  const latest = period === "month" ? thisMonth() : thisWeek();

  const changePeriod = (p: CyclePeriod) => {
    if (p === period) return;
    if (p === "range") {
      // The shown week / month as days, clipped to what the screen may ask for.
      const days = scopeDays({ kind: period === "month" ? "month" : "week", start }, today);
      const from = opts.rangeMin && days.from < opts.rangeMin ? opts.rangeMin : days.from;
      setTimeScope({ kind: "range", from, to: days.to > from ? days.to : from });
      return;
    }
    const anchor = period === "range" ? range.to : start;
    if (p === "month") setTimeScope({ kind: "month", start: `${anchor.slice(0, 7)}-01` });
    else setTimeScope({ kind: "week", start: anchor >= thisMonth() && period === "month" ? thisWeek() : weekOf(anchor) });
  };
  const step = (by: number) => {
    if (period === "range") return;
    setTimeScope(period === "week" ? { kind: "week", start: shiftWeeks(start, by) } : { kind: "month", start: shiftMonths(start, by) });
  };
  const setRange = (r: { from: string; to: string }) => setTimeScope({ kind: "range", from: r.from, to: r.to });
  const goTo = (cycle: "week" | "month", s: string) => setTimeScope({ kind: cycle, start: s });

  return { ...state, today, latest, changePeriod, step, setRange, goTo };
}

/* ---- rolling-days screens (7 / 30 / 90 ngày, Tháng này, Tháng trước, Tuỳ chọn) ---- */

/** A preset of a rolling-days screen as a shared period. */
export function presetScope(preset: string, today = localDay()): TimeScope | null {
  if (preset === "month") return { kind: "month", start: thisMonth() };
  if (preset === "last_month") return { kind: "month", start: shiftMonths(thisMonth(), -1) };
  const days = Number(preset);
  return Number.isInteger(days) && days > 0 ? { kind: "last", days } : null;
}

/**
 * The shared period as a rolling-days screen shows it: one of its presets when one matches,
 * otherwise "custom" with the period's days (capped at `maxSpan` days, ending on its last day).
 */
export function toPreset<P extends string>(
  s: TimeScope,
  presets: readonly P[],
  maxSpan: number,
  today = localDay(),
): { preset: P | "custom"; from: string; to: string } {
  for (const p of presets) {
    const ps = presetScope(p, today);
    if (ps && same(ps, s)) return { preset: p, ...scopeDays(ps, today) };
  }
  let { from, to } = scopeDays(s, today);
  if (span(from, to) > maxSpan) from = shiftDays(to, -(maxSpan - 1));
  return { preset: "custom", from, to };
}

/** A rolling-days screen's choice as a shared period. */
export function fromPreset(r: { preset: string; from: string; to: string }): TimeScope | null {
  if (r.preset === "custom") return { kind: "range", from: r.from, to: r.to };
  return presetScope(r.preset);
}

/* ---- month screens (DPI, minh chứng) ---- */

/** The month (YYYY-MM) a shared period shows on a one-month screen: the month of its last day. */
export function scopeMonth(s: TimeScope, today = localDay()): string {
  if (s.kind === "month") return s.start.slice(0, 7);
  return scopeDays(s, today).to.slice(0, 7);
}

const dmy = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}`;

/** Why a week/month-only screen shows another period than the shared range; null when it does not. */
export function scopeNote(state: Pick<CycleState, "approximated" | "period" | "range"> | null): string | null {
  if (!state?.approximated) return null;
  const shown = state.period === "month" ? "tháng" : "tuần";
  return `Khoảng ${dmy(state.range.from)} – ${dmy(state.range.to)} đang chọn chung: trang này chỉ xem theo tuần hoặc tháng, nên hiện ${shown} chứa ngày cuối.`;
}
