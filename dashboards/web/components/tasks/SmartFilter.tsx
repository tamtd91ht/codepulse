"use client";

import { useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { api } from "@/lib/api";

import { errorText } from "./taskParts";

/*
 * Lọc nhanh bằng câu (docs/specs/31 §19.8): "bug cao chưa giao sprint này" → POST
 * /tasks/nl-filter → chips + the GET /tasks params. Applying replaces the List's filters; removing
 * a chip clears that one filter. A chip hides once its filter was changed by hand.
 */

export interface NlChip {
  field: string;
  value: string;
  label: string;
}
interface NlResult {
  chips: NlChip[];
  query: Record<string, string>;
  used_ai: boolean;
}

export function SmartFilter({
  bp,
  active,
  onApply,
  onRemove,
}: {
  bp: number;
  /** The List's current filters, to show only chips still in force. */
  active: Record<string, string>;
  onApply: (query: Record<string, string>) => void;
  onRemove: (field: string) => void;
}) {
  const [text, setText] = useState("");
  const [chips, setChips] = useState<NlChip[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [none, setNone] = useState(false);

  async function run() {
    const sentence = text.trim();
    if (!sentence || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<NlResult>("/tasks/nl-filter", { space: bp, text: sentence });
      setChips(res.chips);
      setNone(!res.chips.length);
      onApply(res.query);
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }

  const shown = chips.filter((c) => active[c.field] === c.value);

  return (
    <div className="nl-filter">
      <form
        className="nl-filter-bar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label className="nl-filter-input">
          <Icon name="search" size={16} />
          <span className="sr-only">Lọc nhanh bằng câu</span>
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Lọc nhanh: gõ ‘bug cao chưa giao sprint này’"
            maxLength={300}
            disabled={busy}
          />
        </label>
        <button type="submit" className="sm" disabled={busy || !text.trim()}>
          {busy ? "Đang hiểu…" : "Lọc"}
        </button>
      </form>
      {error && (
        <p className="small nl-filter-error" role="alert">
          Không lọc được: {error}{" "}
          <button type="button" className="link" onClick={() => void run()}>
            Thử lại
          </button>
        </p>
      )}
      {none && !error && <p className="small muted">Chưa hiểu câu này; thử “bug quá hạn của tôi”, “@an đang làm”, “nhãn backend”.</p>}
      {shown.length > 0 && (
        <ul className="nl-chips" aria-label="Bộ lọc từ câu">
          {shown.map((c) => (
            <li key={c.field} className="nl-chip">
              <span>{c.label}</span>
              <IconButton
                icon="close"
                size={14}
                label={`Bỏ lọc ${c.label}`}
                onClick={() => {
                  setChips((all) => all.filter((x) => x.field !== c.field));
                  onRemove(c.field);
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
