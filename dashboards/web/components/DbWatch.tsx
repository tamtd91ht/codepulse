"use client";

import { type FormEvent, useState } from "react";

import { Alert, Card, Loading, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { DbTransactions, ReviewSetting } from "@/lib/types";

const fmt = (seconds: number) => {
  const s = Math.round(seconds);
  if (s < 120) return `${s}s`;
  if (s < 7200) return `${Math.floor(s / 60)} phút ${s % 60}s`;
  return `${Math.floor(s / 3600)} giờ ${Math.floor((s % 3600) / 60)} phút`;
};

/**
 * Admin › Hệ thống (app/dbwatch.py): the database transactions open right now, each with the flow
 * that holds it (worker job, API request, scheduler), and the threshold past which the admins'
 * bell rings. Re-read every 10 seconds while open.
 */
export function DbWatch() {
  const [minS, setMinS] = useState(1);
  const list = useApi<DbTransactions>(`/admin/db/transactions?min_s=${minS}`, { refreshInterval: 10_000 });
  const settings = useApi<ReviewSetting[]>("/admin/review-settings");
  const setting = settings.data?.find((s) => s.key === "long_tx_alert_s");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  async function save(value: number | null) {
    setBusy(true);
    setResult(null);
    try {
      await api.put("/admin/review-settings", { long_tx_alert_s: value });
      await Promise.all([settings.mutate(), list.mutate()]);
      setResult({
        tone: "success",
        text: value === 0 ? "Đã tắt cảnh báo transaction lâu." : "Đã lưu ngưỡng cảnh báo.",
      });
    } catch (e) {
      setResult({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }
  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const raw = new FormData(e.currentTarget).get("long_tx_alert_s");
    void save(Number(raw));
  };
  const threshold = list.data?.threshold_s ?? (setting ? Number(setting.value) : null);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Cảnh báo transaction DB chạy lâu" collapsible={false}>
        <p className="muted small" style={{ marginTop: 0 }}>
          Mỗi phút hệ thống xem các transaction đang mở trên PostgreSQL. Transaction nào mở lâu hơn ngưỡng thì chuông của mọi
          admin báo một lần, kèm luồng đang giữ nó (job worker, request API, scheduler), trạng thái và câu lệnh. Còn chạy quá 10 lần
          ngưỡng thì báo thêm một lần nữa.
        </p>
        {result && (
          <Alert tone={result.tone} onClose={() => setResult(null)}>
            {result.text}
          </Alert>
        )}
        {!setting ? (
          <Loading error={settings.error} rows={1} />
        ) : (
          <form onSubmit={onSubmit} className="setting-tile">
            <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
              <span className="small muted">Ngưỡng cảnh báo (giây, 0 = tắt) ({setting.env})</span>
              <input
                key={String(setting.value)}
                name="long_tx_alert_s"
                type="number"
                required
                min={setting.minimum ?? 0}
                max={setting.maximum ?? undefined}
                defaultValue={Number(setting.value)}
              />
            </label>
            <p className="muted small">
              {setting.overridden ? `Mặc định trong .env: ${setting.default}.` : "Đang dùng giá trị trong .env."}
            </p>
            <div className="toolbar">
              <button type="submit" disabled={busy}>
                Lưu
              </button>
              {setting.overridden && (
                <button type="button" onClick={() => void save(null)} disabled={busy}>
                  Về mặc định ({String(setting.default)})
                </button>
              )}
            </div>
          </form>
        )}
      </Card>

      <Card title="Transaction đang mở" collapsible={false}>
        <div className="toolbar" style={{ marginTop: 0 }}>
          <label className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            Lâu hơn
            <select value={minS} onChange={(e) => setMinS(Number(e.target.value))} aria-label="Chỉ hiện transaction lâu hơn">
              {[1, 5, 30, 60, 300].map((s) => (
                <option key={s} value={s}>
                  {s}s
                </option>
              ))}
            </select>
          </label>
          <span className="spacer" />
          {threshold !== null && (
            <span className="small muted">{threshold > 0 ? `Báo chuông khi quá ${threshold}s` : "Đang tắt báo chuông"}</span>
          )}
          <button type="button" onClick={() => void list.mutate()} disabled={list.isValidating}>
            Làm mới
          </button>
        </div>
        {!list.data ? (
          <Loading error={list.error} rows={3} />
        ) : list.data.items.length === 0 ? (
          <p className="muted small">Không có transaction nào mở lâu hơn {minS}s.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Luồng</th>
                  <th>Trạng thái</th>
                  <th>Câu lệnh</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((tx) => (
                  <tr key={`${tx.pid}-${tx.xact_start}`}>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <Pill tone={threshold && tx.seconds >= threshold ? "bad" : undefined}>{fmt(tx.seconds)}</Pill>
                      <div className="small muted">pid {tx.pid}</div>
                    </td>
                    <td>
                      <strong>{tx.flow}</strong>
                      {tx.client && <div className="small muted">từ {tx.client}</div>}
                    </td>
                    <td className="small">
                      {tx.state_text}
                      {tx.waiting && <div className="muted">chờ {tx.waiting}</div>}
                    </td>
                    <td className="small">
                      <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{tx.query ?? ""}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
