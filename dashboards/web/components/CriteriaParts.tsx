"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { Icon, IconButton } from "@/components/icons";
import { critColor } from "@/lib/criteria";

/** Revalidates every cached list of sets (both filters) and set detail after a change. */
export function useRefreshSets() {
  const { mutate } = useSWRConfig();
  return () => mutate((key) => typeof key === "string" && key.startsWith("/criteria-sets"));
}

/**
 * The weights of a set as one stacked bar, colours fixed by position. Each segment has its own
 * tooltip; screen readers get the whole split in one label.
 */
export function WeightBar({
  parts,
  label = "Trọng số",
  size = "md",
  showRest = false,
}: {
  parts: { name: string; weight: number }[];
  label?: string;
  size?: "sm" | "md" | "lg";
  /** Draw what is missing to 100 as a hatched segment (the editor's total bar). */
  showRest?: boolean;
}) {
  const total = parts.reduce((s, p) => s + Math.max(0, p.weight || 0), 0);
  const rest = Math.max(0, 100 - total);
  const text = parts.map((p) => `${p.name || "(chưa đặt tên)"} ${p.weight || 0}%`).join(" · ");
  return (
    <span className={`crit-bar ${size}`} role="img" aria-label={`${label}: ${text}`}>
      {parts.map((p, i) =>
        p.weight > 0 ? (
          <span
            key={i}
            className="seg"
            style={{ flexGrow: p.weight, background: critColor(i) }}
            title={`${p.name || "(chưa đặt tên)"}: ${p.weight}%`}
          />
        ) : null,
      )}
      {showRest && rest > 0 && <span className="seg rest" style={{ flexGrow: rest }} title={`Còn ${rest}% chưa chia`} />}
      {total === 0 && !showRest && <span className="seg rest" style={{ flexGrow: 1 }} />}
    </span>
  );
}

/** A small coloured square naming a component by its fixed colour. */
export function Swatch({ index }: { index: number }) {
  return <span className="crit-swatch" style={{ background: critColor(index) }} aria-hidden="true" />;
}

/** A score 0–100 as a thin bar, coloured by how good it is. */
export function ScoreBar({ value, label }: { value: number | null; label: string }) {
  const v = value === null ? 0 : Math.max(0, Math.min(100, value));
  const tone = value === null ? "" : value >= 80 ? "good" : value >= 65 ? "warn" : "bad";
  return (
    <span
      className={`crit-score-bar ${tone}`}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value ?? undefined}
    >
      <span style={{ width: `${v}%` }} />
    </span>
  );
}

/** An (i) button that opens a short explanation; hover and focus open it too. */
export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <span className={`crit-info${open ? " open" : ""}`} ref={box}>
      <button
        type="button"
        className="crit-info-btn"
        aria-label={label}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        <Icon name="info" size={14} />
      </button>
      <span className="crit-info-pop" id={id} role="tooltip">
        {children}
      </span>
    </span>
  );
}

/**
 * A centred dialog: Escape and the backdrop close it (unless busy), focus starts inside and
 * returns to the opener when it closes, Tab stays inside.
 */
export function Modal({
  title,
  onClose,
  busy,
  width = 560,
  children,
  footer,
}: {
  title: ReactNode;
  onClose: () => void;
  busy?: boolean;
  width?: number;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  close.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = box.current?.querySelector<HTMLElement>("[autofocus], input, textarea, select, button:not(.icon-btn)");
    first?.focus();
    return () => opener?.focus();
  }, []);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && !busyRef.current) {
      e.stopPropagation();
      close.current();
    }
    if (e.key !== "Tab" || !box.current) return;
    const all = [
      ...box.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ];
    if (!all.length) return;
    if (e.shiftKey && document.activeElement === all[0]) {
      e.preventDefault();
      all[all.length - 1].focus();
    } else if (!e.shiftKey && document.activeElement === all[all.length - 1]) {
      e.preventDefault();
      all[0].focus();
    }
  };
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div
        ref={box}
        className="modal card crit-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{ width: `min(${width}px, 100%)` }}
        onKeyDown={onKey}
      >
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <IconButton icon="close" label="Đóng" onClick={onClose} disabled={busy} />
        </div>
        <div className="crit-modal-body">{children}</div>
        {footer && <div className="adm-form-foot crit-modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Drag handle + up/down buttons for one row of a reorderable list (keyboard: the buttons). */
export function ReorderControls({
  index,
  count,
  name,
  onMove,
}: {
  index: number;
  count: number;
  name: string;
  onMove: (to: number) => void;
}) {
  return (
    <span className="crit-reorder">
      <span className="crit-grip" aria-hidden="true" title="Kéo để đổi thứ tự">
        ⋮⋮
      </span>
      <IconButton
        icon="chevronUp"
        label={`Đưa ${name} lên`}
        size={14}
        disabled={index === 0}
        onClick={() => onMove(index - 1)}
      />
      <IconButton
        icon="chevronDown"
        label={`Đưa ${name} xuống`}
        size={14}
        disabled={index === count - 1}
        onClick={() => onMove(index + 1)}
      />
    </span>
  );
}

/**
 * HTML5 drag-and-drop for a vertical list: spread `rowProps(i)` on each row. The up/down buttons
 * remain the keyboard way (docs/specs/22 §7.5).
 */
export function useDragList(onMove: (from: number, to: number) => void) {
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  // A row is draggable only while the grip is pressed, so selecting text in its fields still works.
  const [armed, setArmed] = useState<number | null>(null);
  useEffect(() => {
    if (armed === null) return;
    const up = () => setArmed(null);
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, [armed]);
  const rowProps = (i: number) => ({
    draggable: armed === i || dragging === i,
    onPointerDown: (e: React.PointerEvent) => {
      if ((e.target as HTMLElement).closest(".crit-grip")) setArmed(i);
    },
    onDragStart: (e: React.DragEvent) => {
      setArmed(null);
      setDragging(i);
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(i));
    },
    onDragOver: (e: React.DragEvent) => {
      if (dragging === null) return;
      e.preventDefault();
      setOver(i);
    },
    onDragLeave: () => setOver((o) => (o === i ? null : o)),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      if (dragging !== null && dragging !== i) onMove(dragging, i);
      setDragging(null);
      setOver(null);
    },
    onDragEnd: () => {
      setDragging(null);
      setOver(null);
    },
    "data-dragging": dragging === i ? "" : undefined,
    "data-over": over === i && dragging !== i ? "" : undefined,
  });
  return rowProps;
}
