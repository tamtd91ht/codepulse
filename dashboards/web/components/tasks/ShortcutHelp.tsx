"use client";

import { useCallback, useState } from "react";

import { Modal } from "@/components/CriteriaParts";

import { TASK_SHORTCUTS, useTaskShortcuts } from "./useTaskShortcuts";

/** Mounts the task shortcuts and the "?" overlay listing them (docs/specs/31 §19.8). */
export function ShortcutHelp() {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);
  useTaskShortcuts(toggle);
  if (!open) return null;
  return (
    <Modal title="Phím tắt" onClose={() => setOpen(false)} width={400}>
      <p className="small muted nl-help-note">Không chạy khi đang gõ trong ô nhập hoặc giữ Ctrl / Alt.</p>
      <dl className="nl-keys">
        {TASK_SHORTCUTS.map((s) => (
          <div key={s.label} className="nl-keys-row">
            <dt>
              {s.keys.map((k) => (
                <kbd key={k} className="nl-kbd">
                  {k}
                </kbd>
              ))}
            </dt>
            <dd>{s.label}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}
