"use client";

import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/icons";
import { dayLabel, rangeLabel, shiftDays, shiftMonths } from "@/lib/format";

export type DayRange = { from: string; to: string };
type Field = "from" | "to";

/** Matches the CSS breakpoint where the popover becomes a bottom sheet. */
const SINGLE_MONTH_QUERY = "(max-width: 720px)";
const WEEKDAYS =["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

/** Days (YYYY-MM-DD) of the month starting on `month`, with blanks before the 1st (weeks start Monday). */
function monthCells(month: string): (string | null)[] {
  const first = new Date(`${month}T00:00:00Z`);
  const blanks = (first.getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  const cells: (string | null)[] = Array.from({ length: blanks }, () => null);
  for (let d = 1; d <= days; d++) cells.push(`${month.slice(0, 8)}${String(d).padStart(2, "0")}`);
  return cells;
}

const monthOf = (day: string) => `${day.slice(0, 7)}-01`;
const monthTitle = (month: string) => `Tháng ${Number(month.slice(5, 7))}, ${month.slice(0, 4)}`;
const clamp = (day: string, min: string, max: string) => (day < min ? min : day > max ? max : day);
/** Days in from..to, both included. */
const dayCount = (from: string, to: string) => Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;

/** Quick picks, clipped to [min, max]. */
function presets(min: string, max: string): { label: string; range: DayRange }[] {
  const thisMonth = monthOf(max);
  const lastMonth = shiftMonths(thisMonth, -1);
  const list = [
    { label: "7 ngày qua", range: { from: shiftDays(max, -6), to: max } },
    { label: "30 ngày qua", range: { from: shiftDays(max, -29), to: max } },
    { label: "90 ngày qua", range: { from: shiftDays(max, -89), to: max } },
    { label: "Tháng này", range: { from: thisMonth, to: max } },
    { label: "Tháng trước", range: { from: lastMonth, to: shiftDays(thisMonth, -1) } },
    { label: "Toàn bộ", range: { from: min, to: max } },
  ];
  return list
    .filter((p) => p.range.to >= min)
    .map((p) => ({ label: p.label, range: { from: clamp(p.range.from, min, max), to: clamp(p.range.to, min, max) } }));
}

/**
 * A range-of-days filter in the house style: a trigger showing the range, and a popover with
 * quick picks, a "Từ ngày" and a "Đến ngày" field, and a two-month calendar (one month on
 * narrow screens). A day click fills the active field (picking the start moves on to the end);
 * moving between months never drops a picked day. "Áp dụng" commits. Days outside [min, max],
 * and days before the start while picking the end, are disabled.
 */
export function DateRangePicker({
  value,
  min,
  max,
  onChange,
  label = "Khoảng ngày",
}: {
  value: DayRange;
  min: string;
  max: string;
  onChange: (range: DayRange) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<{ from: string; to: string | null }>(value);
  const [active, setActive] = useState<Field>("from");
  const [hover, setHover] = useState<string | null>(null);
  // The right-hand month; the left one is the month before it.
  const [view, setView] = useState(monthOf(value.to || max));
  const [focusDay, setFocusDay] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialogId = useId();
  // Narrow screens show one month (`view`) instead of two.
  const [single, setSingle] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(SINGLE_MONTH_QUERY);
    const sync = () => setSingle(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);
  const firstShown = single ? view : shiftMonths(view, -1);

  /** Months so that `day` shows: in the left month, or the right one when it is the last allowed. */
  const showDay = (day: string) => {
    const m = monthOf(day);
    setView(single || m >= monthOf(max) ? m : shiftMonths(m, 1));
  };

  const show = () => {
    setDraft(value);
    setActive("from");
    setHover(null);
    setView(monthOf(value.to || max));
    setFocusDay(null);
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  // Keep keyboard focus on the day the arrows moved to.
  useEffect(() => {
    if (open && focusDay) root.current?.querySelector<HTMLButtonElement>(`[data-day="${focusDay}"]`)?.focus();
  }, [open, focusDay, view]);

  const pick = (day: string) => {
    if (active === "from") {
      // A start after the end empties the end; either way the end is picked next.
      setDraft((d) => ({ from: day, to: d.to !== null && d.to >= day ? d.to : null }));
      setActive("to");
    } else {
      setDraft((d) => ({ ...d, to: day }));
    }
    setHover(null);
    setFocusDay(day);
  };

  const choose = (field: Field) => {
    setActive(field);
    setHover(null);
    setFocusDay(null);
    const day = field === "from" ? draft.from : draft.to;
    if (day) showDay(day);
  };

  const moveFocus = (from: string, by: number) => {
    const next = clamp(shiftDays(from, by), min, max);
    if (next < firstShown) setView(single ? monthOf(next) : shiftMonths(monthOf(next), 1));
    else if (next >= shiftMonths(view, 1)) setView(monthOf(next));
    setFocusDay(next);
  };

  const onGridKey = (e: KeyboardEvent<HTMLButtonElement>, day: string) => {
    const by = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
    if (by !== undefined) {
      e.preventDefault();
      moveFocus(day, by);
    }
  };

  // The shown range; while picking the end, start..the hovered day previews it.
  const end = active === "to" && hover !== null && hover >= draft.from ? hover : draft.to;
  const lo = draft.from;
  const hi = end ?? draft.from;
  const prevDisabled = firstShown <= monthOf(min);
  const nextDisabled = view >= monthOf(max);
  const quick = presets(min, max);
  const activePreset = quick.find((p) => draft.to !== null && p.range.from === draft.from && p.range.to === draft.to)?.label;
  const focusTarget = focusDay ?? (active === "to" ? (draft.to ?? draft.from) : draft.from);

  const month = (m: string) => (
    <div className="drp-month" key={m}>
      <div className="drp-month-title">{monthTitle(m)}</div>
      <div className="drp-grid" role="grid" aria-label={monthTitle(m)}>
        {WEEKDAYS.map((w) => (
          <span key={w} className="drp-weekday" role="columnheader">
            {w}
          </span>
        ))}
        {monthCells(m).map((day, i) => {
          if (day === null) return <span key={`b${i}`} aria-hidden="true" />;
          const disabled = day < min || day > max || (active === "to" && day < draft.from);
          const edge = day === lo || day === hi;
          const inside = day > lo && day < hi;
          const cls = [
            "drp-day",
            inside && "in-range",
            edge && "edge",
            day === lo && "start",
            day === hi && "end",
            day === max && "today",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <button
              key={day}
              type="button"
              className={cls}
              data-day={day}
              disabled={disabled}
              tabIndex={day === focusTarget ? 0 : -1}
              aria-pressed={edge}
              aria-label={`${Number(day.slice(8))} ${monthTitle(m).toLowerCase()}`}
              onClick={() => pick(day)}
              onMouseEnter={() => active === "to" && setHover(day)}
              onKeyDown={(e) => onGridKey(e, day)}
            >
              {Number(day.slice(8))}
            </button>
          );
        })}
      </div>
    </div>
  );

  return (
    <div
      className="drp"
      ref={root}
      onKeyDown={(e) => {
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation();
        close();
      }}
    >
      <button
        ref={trigger}
        type="button"
        className={`drp-trigger${open ? " open" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? dialogId : undefined}
        onClick={() => (open ? close(false) : show())}
      >
        <Icon name="calendar" size={16} />
        <span className="drp-trigger-label">
          <span className="muted">{label}:</span> {value.from && value.to ? rangeLabel(value.from, value.to) : "Chọn"}
        </span>
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="drp-pop" id={dialogId} role="dialog" aria-label={`Chọn ${label.toLowerCase()}`}>
          <div className="drp-presets" role="group" aria-label="Chọn nhanh">
            {quick.map((p) => (
              <button
                key={p.label}
                type="button"
                className={p.label === activePreset ? "active" : undefined}
                aria-pressed={p.label === activePreset}
                onClick={() => {
                  setDraft(p.range);
                  setActive("to");
                  setHover(null);
                  setView(monthOf(p.range.to));
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="drp-body">
            <div className="drp-fields" role="group" aria-label="Ngày đang chọn">
              {(["from", "to"] as const).map((f) => {
                const day = f === "from" ? draft.from : draft.to;
                return (
                  <button key={f} type="button" className={`drp-field${active === f ? " active" : ""}`} aria-pressed={active === f} onClick={() => choose(f)}>
                    <span className="drp-field-label">{f === "from" ? "Từ ngày" : "Đến ngày"}</span>
                    <span className={`drp-field-value${day ? "" : " empty"}`}>{day ? dayLabel(day) : "dd/mm/yyyy"}</span>
                  </button>
                );
              })}
            </div>
            <div className="drp-cal">
              <div className="drp-nav">
                <button type="button" className="icon-btn" aria-label="Lùi một tháng" disabled={prevDisabled} onClick={() => setView((v) => shiftMonths(v, -1))}>
                  <Icon name="chevronLeft" size={16} />
                </button>
                <button type="button" className="icon-btn" aria-label="Tới một tháng" disabled={nextDisabled} onClick={() => setView((v) => shiftMonths(v, 1))}>
                  <Icon name="chevronRight" size={16} />
                </button>
              </div>
              <div className="drp-months" onMouseLeave={() => setHover(null)}>
                {!single && month(shiftMonths(view, -1))}
                {month(view)}
              </div>
            </div>
            <div className="drp-foot">
              <span className="small muted" aria-live="polite">
                {active === "from" ? "Chọn ngày bắt đầu" : draft.to === null ? "Chọn ngày kết thúc" : `${dayCount(draft.from, draft.to)} ngày`}
              </span>
              <span className="drp-actions">
                <button type="button" className="ghost" onClick={() => close()}>
                  Hủy
                </button>
                <button
                  type="button"
                  className="primary ink"
                  disabled={draft.to === null}
                  onClick={() => {
                    if (draft.to === null) return;
                    onChange({ from: draft.from, to: draft.to });
                    close();
                  }}
                >
                  Áp dụng
                </button>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
