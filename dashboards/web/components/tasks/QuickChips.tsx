"use client";

import type { QuickAmbiguity } from "./TaskBoard";

/** A pick per ambiguous token: a value, or null for "leave it out". */
export type QuickPicks = Record<string, number | string | null>;

const FIELD_LABEL: Record<QuickAmbiguity["field"], string> = { assignee_id: "Người làm", priority: "Ưu tiên" };

/**
 * Quick create (docs/specs/31 §20.2): when a token of the line matches several values ("@an" for
 * two people) the Board shows these chips under the input instead of an error. Each token gets one
 * pick (or "Bỏ"); "Tạo task" (or Enter in the input) saves once every token has one.
 */
export function QuickChips({
  ambiguous,
  picks,
  busy,
  onPick,
  onSave,
  onCancel,
}: {
  ambiguous: QuickAmbiguity[];
  picks: QuickPicks;
  busy: boolean;
  onPick: (token: string, value: number | string | null) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const ready = ambiguous.every((a) => a.token in picks);
  return (
    <div className="qk-chips" role="group" aria-label="Chọn giá trị cho dòng tạo nhanh">
      {ambiguous.map((a) => (
        <div key={a.token} className="qk-chip-row">
          <span className="small muted">
            {FIELD_LABEL[a.field]} <span className="qk-token">{a.token}</span> là:
          </span>
          <div className="qk-chip-list">
            {a.options.map((o) => (
              <button
                key={String(o.value)}
                type="button"
                className="qk-chip"
                aria-pressed={picks[a.token] === o.value}
                title={o.hint}
                disabled={busy}
                onClick={() => onPick(a.token, o.value)}
              >
                {o.label}
              </button>
            ))}
            <button type="button" className="qk-chip qk-chip-none" aria-pressed={a.token in picks && picks[a.token] === null} disabled={busy} onClick={() => onPick(a.token, null)}>
              Bỏ
            </button>
          </div>
        </div>
      ))}
      <div className="qk-chip-actions">
        <button type="button" className="sm primary" disabled={!ready || busy} onClick={onSave}>
          Tạo task
        </button>
        <button type="button" className="sm ghost" disabled={busy} onClick={onCancel}>
          Huỷ
        </button>
        {!ready && <span className="small muted">Chọn một giá trị cho mỗi chỗ trên.</span>}
      </div>
    </div>
  );
}
