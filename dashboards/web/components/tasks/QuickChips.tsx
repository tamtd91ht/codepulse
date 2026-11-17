"use client";

import type { TaskPerson, TaskSpace } from "@/lib/types";

import type { QuickAmbiguity, QuickParsed } from "./TaskBoard";
import { TYPE_LABEL, personName, priorityList } from "./taskParts";

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

/**
 * Live preview of a quick-create line (docs/specs/31 §21.0 F6): while typing, the values read so
 * far show as chips under the input; a @/#/!/^ token that matched nothing is underlined red (it
 * would stay in the title).
 */
export function QuickPreview({ parsed, people, space }: { parsed: QuickParsed; people: TaskPerson[]; space: TaskSpace }) {
  const chips: { key: string; label: string; bad?: boolean; title?: string }[] = [];
  if (parsed.assignee_id != null) {
    const who = people.find((p) => p.id === parsed.assignee_id);
    chips.push({ key: "who", label: `@ ${who ? personName(who) : `#${parsed.assignee_id}`}`, title: "Người làm" });
  }
  if (parsed.type) chips.push({ key: "type", label: `# ${TYPE_LABEL[parsed.type]}`, title: "Loại" });
  if (parsed.priority) {
    const pr = priorityList(space).find((p) => p.key === parsed.priority);
    chips.push({ key: "prio", label: `! ${pr?.name ?? parsed.priority}`, title: "Ưu tiên" });
  }
  if (parsed.story_points != null) chips.push({ key: "sp", label: `${parsed.story_points} SP`, title: "Story point" });
  if (parsed.parent_key) chips.push({ key: "parent", label: `^ ${parsed.parent_key}`, title: "Task cha" });
  for (const a of parsed.ambiguous) chips.push({ key: `amb-${a.token}`, label: `${a.token}: ${a.options.length} lựa chọn`, title: "Enter để chọn" });
  for (const u of parsed.unmatched) chips.push({ key: `bad-${u}`, label: u, bad: true, title: "Không khớp, sẽ nằm trong tiêu đề" });
  if (!chips.length) return null;
  return (
    <div className="qk-preview" aria-live="polite" aria-label="Xem trước dòng tạo nhanh">
      {chips.map((c) => (
        <span key={c.key} className={`qk-pv${c.bad ? " qk-pv-bad" : ""}`} title={c.title}>
          {c.label}
        </span>
      ))}
    </div>
  );
}
