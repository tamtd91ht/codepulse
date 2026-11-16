"use client";

import { useState } from "react";

import { Alert, Card, Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";

export type TaskSourceName = "gitlab" | "jira" | "codepulse";

interface SourceOption {
  value: TaskSourceName;
  label: string;
  note: string;
  available: boolean;
}

interface TaskSourceView {
  task_source: TaskSourceName;
  label: string;
  can_change: boolean;
  options: SourceOption[];
}

/**
 * Where DPI reads its tasks (docs/specs/27 §9, docs/specs/31 §15-16): GitLab Issues, Jira or
 * CodePulse's own tasks, one at a time. Moved here from the Jira tab (user 2026-11-16), so it
 * stays when Jira goes.
 */
export function DpiTaskSource({ onChanged }: { onChanged?: () => void }) {
  const { data, error, mutate } = useApi<TaskSourceView>("/dpi/task-source");
  const [next, setNext] = useState<TaskSourceName | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  if (error) return <Loading error={error} />;
  if (!data) return <Loading rows={2} />;
  const choices = data.options.filter((o) => o.value !== data.task_source);
  const chosen = data.options.find((o) => o.value === next) ?? null;

  const apply = async () => {
    if (!chosen) return;
    setBusy(true);
    setResult(null);
    try {
      const res = await api.put<{ tasks_projected?: number; full_syncs_queued?: number }>("/dpi/task-source", { source: chosen.value });
      const extra =
        chosen.value === "codepulse" && res.tasks_projected !== undefined
          ? ` ${res.tasks_projected} task đã được tính vào DPI.`
          : res.full_syncs_queued
            ? ` Đã xếp ${res.full_syncs_queued} repo đồng bộ lại đầy đủ.`
            : "";
      setResult({ tone: "success", text: `DPI đã chuyển sang đọc task từ ${chosen.label}.${extra}` });
      setNext(null);
      await mutate();
      onChanged?.();
    } catch (e) {
      setResult({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Nguồn task"
      collapsible={false}
      subtitle="DPI chỉ đọc task của một nguồn nên không tính trùng. Scorecard các tháng đã tính không đổi cho tới khi chạy lại dữ liệu DPI."
    >
      {result && (
        <Alert tone={result.tone} onClose={() => setResult(null)}>
          {result.text}
        </Alert>
      )}
      <p style={{ margin: "0 0 8px" }}>
        Đang đọc task từ <strong>{data.label}</strong>.
      </p>
      {data.can_change ? (
        <div className="toolbar" style={{ margin: 0, flexWrap: "wrap" }}>
          <label className="rq-field" style={{ minWidth: 220 }}>
            <span className="muted small">Chuyển sang</span>
            <select value={next ?? ""} onChange={(e) => setNext((e.target.value || null) as TaskSourceName | null)} disabled={busy}>
              <option value="">Chọn nguồn…</option>
              {choices.map((o) => (
                <option key={o.value} value={o.value} disabled={!o.available}>
                  {o.label}
                  {o.available ? "" : " (chưa kết nối)"}
                </option>
              ))}
            </select>
          </label>
          <span className="spacer" />
          <button type="button" className="primary ink sm" disabled={!chosen || busy} onClick={() => void apply()}>
            {busy ? "Đang chuyển…" : "Chuyển nguồn"}
          </button>
        </div>
      ) : (
        <p className="muted small" style={{ margin: 0 }}>
          Người có quyền cấu hình DPI đổi được nguồn này.
        </p>
      )}
      {chosen && (
        <p className="muted small" style={{ margin: "8px 0 0" }}>
          {chosen.note}
        </p>
      )}
    </Card>
  );
}
