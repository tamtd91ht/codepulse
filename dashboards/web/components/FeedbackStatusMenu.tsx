"use client";

import { useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/icons";
import { type FeedbackStatus, STATUS_LABEL } from "@/lib/feedback";

/*
 * Chuyển trạng thái phản ánh (docs/specs/32 §7.3), OMICX style: a trigger and a dropdown of the
 * five statuses, each with its dot. Two triggers:
 *  - "chip": the report's stage chip in the detail header (outline, dot, label, chevron);
 *  - "button": the outline "Chuyển trạng thái" action of the list's selection bar.
 * Keyboard: Enter / Space / ↓ opens, ↑ ↓ Home End move, Enter picks, Escape closes back to the
 * trigger; a click away or Tab closes.
 */

export const STATUS_ORDER: FeedbackStatus[] = ["new", "accepted", "planned", "done", "rejected"];

export function StatusDot({ status }: { status: FeedbackStatus }) {
  return <span className={`fbk-dot is-${status}`} aria-hidden="true" />;
}

export function StatusMenu({
  variant,
  current,
  counts,
  total,
  disabled,
  align = "start",
  onPick,
}: {
  variant: "chip" | "button";
  /** The report's status (chip): marked and not offered as a move. */
  current?: FeedbackStatus;
  /** Selection bar: how many of the picked reports are already in each status. */
  counts?: Partial<Record<FeedbackStatus, number>>;
  total?: number;
  disabled?: boolean;
  align?: "start" | "end";
  onPick: (status: FeedbackStatus) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    const start = Math.max(0, current ? STATUS_ORDER.indexOf(current) : 0);
    requestAnimationFrame(() => items.current[start]?.focus());
    return () => document.removeEventListener("mousedown", away);
  }, [open, current]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const onMenuKey = (e: React.KeyboardEvent) => {
    const list = items.current.filter((b): b is HTMLButtonElement => !!b && !b.disabled);
    const at = list.indexOf(document.activeElement as HTMLButtonElement);
    const go = (i: number) => list[(i + list.length) % list.length]?.focus();
    if (e.key === "ArrowDown") go(at + 1);
    else if (e.key === "ArrowUp") go(at - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Escape") close();
    else if (e.key === "Tab") close(false);
    else return;
    if (e.key !== "Tab") e.preventDefault();
  };

  return (
    <div className={`fbk-status-menu${open ? " open" : ""}`} ref={box}>
      <button
        type="button"
        ref={trigger}
        className={variant === "chip" ? "fbk-stage-chip" : "sm fbk-status-trigger"}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={variant === "chip" && current ? `Trạng thái: ${STATUS_LABEL[current]}. Đổi trạng thái` : undefined}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {variant === "chip" && current ? (
          <>
            <StatusDot status={current} />
            {STATUS_LABEL[current]}
          </>
        ) : (
          <>
            <Icon name="checkCircle" size={14} /> Chuyển trạng thái
          </>
        )}
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div id={menuId} className={`fbk-status-pop align-${align}`} role="menu" aria-label="Chuyển sang trạng thái" onKeyDown={onMenuKey}>
          <div className="fbk-status-pop-head">{variant === "chip" ? "Chuyển sang" : `Chuyển ${total ?? ""} phản ánh sang`}</div>
          {STATUS_ORDER.map((s, i) => {
            const isCurrent = s === current;
            const already = counts?.[s] ?? 0;
            const all = total !== undefined && already === total;
            return (
              <button
                key={s}
                type="button"
                role="menuitemradio"
                aria-checked={isCurrent}
                ref={(el) => {
                  items.current[i] = el;
                }}
                disabled={isCurrent || all}
                onClick={() => {
                  close();
                  onPick(s);
                }}
              >
                <StatusDot status={s} />
                <span className="fbk-status-pop-label">{STATUS_LABEL[s]}</span>
                {isCurrent && <Icon name="check" size={14} />}
                {!isCurrent && already > 0 && <span className="fbk-status-pop-hint">{all ? "tất cả đã ở đây" : `${already} đã ở đây`}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
