"use client";

import { useState } from "react";

import { Icon } from "@/components/icons";
import { api } from "@/lib/api";
import type { SearchCond } from "@/lib/types";

import { errorText } from "./taskParts";

/*
 * Lọc nhanh bằng câu (docs/specs/31 §19.8): "bug cao chưa giao sprint này" → POST
 * /tasks/nl-filter → chips. Since §21.4 each chip carries its condition (`cond`): applying puts
 * them in the chip bar, where they are seen and edited like any other condition.
 */

export interface NlChip {
  field: string;
  value: string;
  label: string;
  op?: string;
  cond?: SearchCond;
}
interface NlResult {
  chips: NlChip[];
  query: Record<string, string>;
  used_ai: boolean;
}

export function SmartFilter({ bp, onApply }: { bp: number; onApply: (conds: SearchCond[]) => void }) {
  const [text, setText] = useState("");
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
      const conds = res.chips.map((c) => c.cond).filter((c): c is SearchCond => !!c);
      setNone(!conds.length);
      if (conds.length) {
        onApply(conds);
        setText("");
      }
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }

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
    </div>
  );
}
