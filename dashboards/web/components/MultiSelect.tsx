"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/icons";

export interface MultiOption<T extends string | number> {
  value: T;
  label: string;
  /** Extra text the search also matches (an email, a path). */
  hint?: string;
  /** Shown before the label (an avatar). */
  lead?: ReactNode;
  /** Shown, not pickable; `note` says why. */
  disabled?: boolean;
  note?: string;
}

/**
 * A select box that picks several: the trigger names what is picked ("An, Bình +3"), the
 * popover has a search, "Chọn tất cả" for what the search shows, "Bỏ chọn" and a checklist with
 * the picked ones first. Closes on a click away or Escape. `empty` is what nothing picked means
 * ("Mọi người").
 */
export function MultiSelect<T extends string | number>({
  label,
  options,
  value,
  onChange,
  empty,
  placeholder = "Tìm…",
  unit,
  disabled,
}: {
  label: string;
  options: MultiOption<T>[];
  value: T[];
  onChange: (next: T[]) => void;
  empty: string;
  placeholder?: string;
  /** "repo": the trigger says "12 repo" past three picked. */
  unit?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // The order when opened: picked first, kept while the box is open so rows do not jump.
  const [order, setOrder] = useState<T[]>([]);
  const root = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const popId = useId();

  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    search.current?.focus();
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  const picked = new Set(value);
  const byValue = new Map(options.map((o) => [o.value, o]));
  const names = value.map((v) => byValue.get(v)?.label).filter((s): s is string => !!s);
  const summary =
    names.length === 0 ? empty : names.length <= 2 ? names.join(", ") : unit ? `${names.length} ${unit}` : `${names.slice(0, 2).join(", ")} +${names.length - 2}`;

  const q = query.trim().toLowerCase();
  const rank = new Map(order.map((v, i) => [v, i]));
  const shown = options
    .filter((o) => !q || `${o.label} ${o.hint ?? ""}`.toLowerCase().includes(q))
    .sort((a, b) => (rank.get(a.value) ?? order.length) - (rank.get(b.value) ?? order.length));
  const pickable = shown.filter((o) => !o.disabled);
  const allShown = pickable.length > 0 && pickable.every((o) => picked.has(o.value));

  const toggle = (v: T, on: boolean) => onChange(on ? (picked.has(v) ? value : [...value, v]) : value.filter((x) => x !== v));
  const show = () => {
    setOrder(value);
    setQuery("");
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  return (
    <div
      className={`msel${value.length ? " set" : ""}`}
      ref={root}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className={`msel-trigger${open ? " open" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        aria-label={`${label}: ${summary}`}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : show())}
        title={names.length > 2 ? names.join(", ") : undefined}
      >
        <span className={`msel-value${value.length ? "" : " muted"}`}>{summary}</span>
        {value.length > 0 && <span className="msel-count">{value.length}</span>}
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="msel-pop" id={popId} role="dialog" aria-label={label}>
          <div className="msel-search">
            <Icon name="search" size={14} />
            <input
              ref={search}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={placeholder}
              aria-label={`Tìm ${label.toLowerCase()}`}
            />
          </div>
          <div className="msel-bar">
            <button
              type="button"
              className="link"
              disabled={pickable.length === 0 || allShown}
              onClick={() => onChange([...value, ...pickable.map((o) => o.value).filter((v) => !picked.has(v))])}
            >
              Chọn tất cả{q ? " đang hiện" : ""} ({pickable.length})
            </button>
            {value.length > 0 && (
              <button type="button" className="link" onClick={() => onChange([])}>
                Bỏ chọn ({value.length})
              </button>
            )}
          </div>
          <ul className="msel-list" role="group" aria-label={label}>
            {shown.length === 0 ? (
              <li className="muted small msel-none">Không có kết quả khớp.</li>
            ) : (
              shown.map((o) => (
                <li key={String(o.value)}>
                  <label className={o.disabled ? "disabled" : picked.has(o.value) ? "on" : undefined} title={o.note ?? o.hint}>
                    <input
                      type="checkbox"
                      checked={picked.has(o.value)}
                      disabled={o.disabled}
                      onChange={(e) => toggle(o.value, e.target.checked)}
                    />
                    {o.lead}
                    <span className="msel-label">
                      <span>{o.label}</span>
                      {(o.note || o.hint) && <span className="muted small">{o.note ?? o.hint}</span>}
                    </span>
                  </label>
                </li>
              ))
            )}
          </ul>
          <div className="msel-foot">
            <span className="muted small">{value.length ? `Đã chọn ${value.length}` : empty}</span>
            <button type="button" className="sm" onClick={close}>
              Xong
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
