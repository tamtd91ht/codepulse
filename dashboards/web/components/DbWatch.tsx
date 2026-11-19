"use client";

import { type FormEvent, useState } from "react";

import { Alert, Card, Loading, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { DbSlowEntry, DbTransactions, ReviewSetting } from "@/lib/types";

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
  const history = useApi<DbSlowEntry[]>("/admin/db/slow", { refreshInterval: 30_000 });
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
          Mọi transaction của ứng dụng được đo thời gian ngay khi chạy: transaction nào lâu hơn ngưỡng thì khi kết thúc, chuông của
          mọi admin báo kèm luồng (job worker, request API, scheduler), dòng code đã kết thúc nó và câu lệnh chậm nhất. Thêm vào đó,
          mỗi phút hệ thống xem các transaction <b>vẫn đang mở</b> trên PostgreSQL (bị treo, worker chết giữa chừng, kết nối ngoài ứng
          dụng) và báo ngay khi chúng vượt ngưỡng; khi transaction đó kết thúc, chuông báo bổ sung "đã xong sau…, tại…". Cùng một
          luồng ở cùng một chỗ báo tối đa 30 phút một lần.
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

      <Card title="Đã báo trong 24 giờ qua" collapsible={false}>
        {!history.data ? (
          <Loading error={history.error} rows={2} />
        ) : history.data.length === 0 ? (
          <p className="muted small" style={{ marginTop: 0 }}>
            Không có transaction nào vượt ngưỡng trong 24 giờ qua.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Lúc</th>
                  <th>Thời gian</th>
                  <th>Luồng · vị trí code</th>
                  <th>Câu lệnh chậm nhất / cuối</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((h, i) => (
                  <tr key={`${h.at}-${i}`}>
                    <td className="small" style={{ whiteSpace: "nowrap" }}>
                      {new Date(h.at * 1000).toLocaleTimeString("vi-VN")}
                      <div className="muted">{h.source === "open" ? "đang mở" : "đã kết thúc"}</div>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <Pill tone={h.source === "open" ? "bad" : "warn"}>{fmt(Number(h.seconds ?? 0))}</Pill>
                    </td>
                    <td className="small">
                      <strong>{h.flow}</strong>
                      {h.where && (
                        <div>
                          <code>{h.where}</code>
                        </div>
                      )}
                      <div className="muted">
                        {h.state}
                        {h.waiting ? ` · chờ ${h.waiting}` : ""}
                        {h.statements ? ` · ${h.statements} câu lệnh` : ""}
                      </div>
                    </td>
                    <td className="small">
                      {h.slowest_s ? <span className="muted">{fmt(Number(h.slowest_s))} · </span> : null}
                      <code style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{h.sql ?? ""}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
