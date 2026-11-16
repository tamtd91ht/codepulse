"use client";

import { Modal } from "@/components/CriteriaParts";

import { GLOBAL_SHORTCUTS, TASK_SHORTCUTS, type TaskShortcut, useTaskShortcuts } from "./useTaskShortcuts";

/**
 * Mounted by the task detail: runs the task shortcuts (E, A, S, C, .). The "?" overlay itself is
 * global (ShortcutHelpModal, opened from the command palette's mount), docs/specs/31 §20.2.
 */
export function ShortcutHelp() {
  useTaskShortcuts();
  return null;
}

function Keys({ title, list }: { title: string; list: TaskShortcut[] }) {
  return (
    <section className="qk-help-group">
      <h3 className="qk-help-title">{title}</h3>
      <dl className="nl-keys">
        {list.map((s) => (
          <div key={s.label} className="nl-keys-row">
            <dt>
              {s.keys.map((k, i) => (
                <span key={k}>
                  {i > 0 && <span className="small faint"> {s.keys[0] === "Ctrl" ? "+" : "rồi"} </span>}
                  <kbd className="nl-kbd">{k}</kbd>
                </span>
              ))}
            </dt>
            <dd>{s.label}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** The "?" overlay: every shortcut, global first, then those on an open task. */
export function ShortcutHelpModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Phím tắt" onClose={onClose} width={440}>
      <p className="small muted nl-help-note">Không chạy khi đang gõ trong ô nhập, giữ Ctrl / Alt, hoặc đang mở hộp thoại. Trên Mac dùng ⌘ thay Ctrl.</p>
      <Keys title="Mọi trang" list={GLOBAL_SHORTCUTS} />
      <Keys title="Khi đang mở một task" list={TASK_SHORTCUTS} />
    </Modal>
  );
}
