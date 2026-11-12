"use client";

import { useEffect, useRef } from "react";

import { Icon, type IconName } from "@/components/icons";

/** A "…" button opening a short menu of less used actions (closes on a click away or Escape). */
export function MoreMenu({ items, disabled }: { items: { icon: IconName; label: string; danger?: boolean; run: () => void }[]; disabled?: boolean }) {
  const box = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent | KeyboardEvent) => {
      const el = box.current;
      if (!el?.open) return;
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !el.contains(e.target as Node)) el.open = false;
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", away);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", away);
    };
  }, []);
  if (!items.length) return null;
  return (
    <details className="llm-acc-more docs-more" ref={box}>
      <summary className="icon-btn" aria-label="Thêm thao tác" title="Thêm thao tác" aria-disabled={disabled}>
        <Icon name="more" />
      </summary>
      <div className="llm-acc-menu" role="menu">
        {items.map((it) => (
          <button
            key={it.label}
            type="button"
            role="menuitem"
            className={it.danger ? "bad" : undefined}
            disabled={disabled}
            onClick={() => {
              if (box.current) box.current.open = false;
              it.run();
            }}
          >
            <Icon name={it.icon} size={14} /> {it.label}
          </button>
        ))}
      </div>
    </details>
  );
}
