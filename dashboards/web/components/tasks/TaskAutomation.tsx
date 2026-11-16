"use client";

import { useEffect, useMemo, useState } from "react";

import { Card, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TaskSpace } from "@/lib/types";

import { errorText } from "./taskParts";

/* Quy tắc tự động (docs/specs/31 §19.7): a fixed library of rules, each on/off per dự án, some with one number. */

type RuleParam = { key: string; label: string; value: number; default: number; min: number; max: number; unit: string };
type Rule = { key: string; title: string; hint: string; trigger: "ui" | "event" | "schedule"; on: boolean; params: RuleParam[] };
type AutomationOut = { rules: Rule[]; can_manage: boolean };
type Draft = Record<string, { on: boolean } & Record<string, number | boolean>>;

const TRIGGER: Record<Rule["trigger"], { label: string; tone: "info" | "good" | "warn" }> = {
  ui: { label: "Hỏi khi thao tác", tone: "info" },
  event: { label: "Khi có sự kiện", tone: "good" },
  schedule: { label: "Hằng ngày", tone: "warn" },
};

function draftOf(rules: Rule[]): Draft {
  const out: Draft = {};
  for (const r of rules) {
    const row: { on: boolean } & Record<string, number | boolean> = { on: r.on };
    for (const p of r.params) row[p.key] = p.value;
    out[r.key] = row;
  }
  return out;
}

export function TaskAutomation({ space }: { space: TaskSpace }) {
  const path = `/tasks/spaces/${space.business_project_id}/automation`;
  const { data, error, mutate } = useApi<AutomationOut>(path);
  const initial = useMemo(() => (data ? draftOf(data.rules) : {}), [data]);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => setDraft(initial), [initial]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const canManage = !!data?.can_manage;

  function patch(key: string, change: Record<string, number | boolean>) {
    setDraft((d) => ({ ...d, [key]: { ...d[key], ...change } }));
  }

  async function save() {
    if (!data) return;
    const invalid = data.rules.flatMap((r) =>
      r.params.filter((p) => {
        const v = Number(draft[r.key]?.[p.key]);
        return !Number.isInteger(v) || v < p.min || v > p.max;
      }).map((p) => `${r.title}: ${p.label} từ ${p.min} đến ${p.max}`),
    );
    if (invalid.length) {
      setMessage(`Lỗi: ${invalid.join("; ")}`);
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const fresh = await api.put<AutomationOut>(path, draft);
      await mutate(fresh, { revalidate: false });
      setMessage("Đã lưu quy tắc tự động.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card
      title="Quy tắc tự động"
      id="tk-set-automation"
      subtitle="Bật các quy tắc có sẵn cho dự án. Tự động chạy dưới tên hệ thống, theo luồng trạng thái của dự án; nhắc việc mỗi task tối đa một lần mỗi ngày."
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!data ? (
        <Loading error={error} rows={4} />
      ) : (
        <>
          <ul className="ta-list">
            {data.rules.map((r) => {
              const row = draft[r.key] ?? { on: r.on };
              return (
                <li key={r.key} className={`ta-rule${row.on ? " on" : ""}`}>
                  <label className="ta-head">
                    <input
                      type="checkbox"
                      role="switch"
                      checked={!!row.on}
                      disabled={!canManage || busy}
                      onChange={(e) => patch(r.key, { on: e.target.checked })}
                      aria-describedby={`ta-hint-${r.key}`}
                    />
                    <strong>{r.title}</strong>
                    <Pill tone={TRIGGER[r.trigger].tone}>{TRIGGER[r.trigger].label}</Pill>
                  </label>
                  <p id={`ta-hint-${r.key}`} className="ta-hint small muted">
                    {r.hint}
                  </p>
                  {r.params.length > 0 && (
                    <div className="ta-params">
                      {r.params.map((p) => (
                        <label key={p.key} className="ta-param">
                          <span className="small">{p.label}</span>
                          <input
                            type="number"
                            min={p.min}
                            max={p.max}
                            step={1}
                            value={String(row[p.key] ?? p.value)}
                            disabled={!canManage || busy || !row.on}
                            onChange={(e) => patch(r.key, { [p.key]: e.target.value === "" ? Number.NaN : Number(e.target.value) })}
                          />
                          <span className="small muted">{p.unit}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {canManage ? (
            <div className="adm-form-foot">
              <button type="button" disabled={!dirty || busy} onClick={() => void save()}>
                Lưu quy tắc
              </button>
            </div>
          ) : (
            <p className="small muted">Chỉ người quản lý Task của dự án đổi được quy tắc.</p>
          )}
        </>
      )}
    </Card>
  );
}
