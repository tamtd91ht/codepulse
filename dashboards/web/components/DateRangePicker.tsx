"use client";

import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/icons";
import { rangeLabel, shiftDays, shiftMonths } from "@/lib/format";

export type DayRange = { from: string; to: string };

const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

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
 * quick picks and a two-month calendar (one month on narrow screens). Click a start day, then an
 * end day; "Áp dụng" commits. Days outside [min, max] are disabled.
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
  const [hover, setHover] = useState<string | null>(null);
  // The right-hand month; the left one is the month before it.
  const [view, setView] = useState(monthOf(value.to || max));
  const [focusDay, setFocusDay] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialogId = useId();

  const show = () => {
    setDraft(value);
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
    if (draft.to === null) {
      setDraft(day < draft.from ? { from: day, to: draft.from } : { from: draft.from, to: day });
    } else {
      setDraft({ from: day, to: null });
    }
    setFocusDay(day);
  };

  const moveFocus = (from: string, by: number) => {
    const next = clamp(shiftDays(from, by), min, max);
    const left = shiftMonths(view, -1);
    if (next < left) setView(shiftMonths(monthOf(next), 1));
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

  // The shown range: a finished draft, or start..hovered day while choosing the end.
  const end = draft.to ?? hover;
  const [lo, hi] = end === null ? [draft.from, draft.from] : end < draft.from ? [end, draft.from] : [draft.from, end];
  const prevDisabled = shiftMonths(view, -1) <= monthOf(min);
  const nextDisabled = view >= monthOf(max);
  const quick = presets(min, max);
  const activePreset = quick.find((p) => draft.to !== null && p.range.from === draft.from && p.range.to === draft.to)?.label;

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
          const disabled = day < min || day > max;
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
          const tabbable = focusDay ? day === focusDay : day === draft.from;
          return (
            <button
              key={day}
              type="button"
              className={cls}
              data-day={day}
              disabled={disabled}
              tabIndex={tabbable ? 0 : -1}
              aria-pressed={edge}
              aria-label={`${Number(day.slice(8))} ${monthTitle(m).toLowerCase()}`}
              onClick={() => pick(day)}
              onMouseEnter={() => draft.to === null && setHover(day)}
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
                  setView(monthOf(p.range.to));
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="drp-body">
            <div className="drp-nav">
              <button type="button" className="icon-btn" aria-label="Tháng trước" disabled={prevDisabled} onClick={() => setView((v) => shiftMonths(v, -1))}>
                <Icon name="chevronLeft" size={16} />
              </button>
              <button type="button" className="icon-btn" aria-label="Tháng sau" disabled={nextDisabled} onClick={() => setView((v) => shiftMonths(v, 1))}>
                <Icon name="chevronRight" size={16} />
              </button>
            </div>
            <div className="drp-months" onMouseLeave={() => setHover(null)}>
              {month(shiftMonths(view, -1))}
              {month(view)}
            </div>
            <div className="drp-foot">
              <span className="small muted" aria-live="polite">
                {draft.to === null ? `Từ ${rangeLabel(draft.from, draft.from)} — chọn ngày kết thúc` : rangeLabel(draft.from, draft.to)}
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
