"use client";

import { type FormEvent, useState } from "react";

import { Alert, Card, Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { PurgeInfo, PurgeResult, PurgeScope } from "@/lib/types";

const SCOPE: Record<PurgeScope, { label: string; detail: string }> = {
  reviews: {
    label: "Chỉ review và điểm",
    detail:
      "Xoá review, finding, khiếu nại, điểm/báo cáo và lịch sử lệnh Telegram. Giữ commit, MR và mọi cấu hình; điểm được tính lại từ MR còn lại.",
  },
  collected: {
    label: "Dữ liệu đã thu thập",
    detail:
      "Như trên, thêm commit, MR, ghi chú MR, pipeline và dữ liệu GitLab thô. Giữ repo, dự án và cấu hình; lần đồng bộ tới thu thập lại từ đầu.",
  },
  people: {
    label: "Toàn bộ kể cả người dùng",
    detail:
      "Như “Toàn bộ”, thêm mọi người dùng, team, thành viên team và tài khoản Git/GitLab/Telegram đã gắn. Chỉ giữ các admin trong ADMIN_EMAILS (để còn đăng nhập) và kết nối GitLab.",
  },
  all: {
    label: "Toàn bộ (reset)",
    detail:
      "Như trên, thêm repo, dự án, bot Telegram, webhook thông báo, cài đặt review và token Claude đã lưu. Chỉ giữ người dùng, team, tài khoản GitLab của họ và kết nối GitLab.",
  },
};

/**
 * Admin card (ADMIN_EMAILS only, the API checks it too): wipe this server's data to start clean.
 * Only this server's database changes; nothing is sent to GitLab.
 */
export function PurgeData() {
  const info = useApi<PurgeInfo>("/admin/purge");
  const [scope, setScope] = useState<PurgeScope>("reviews");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<PurgeResult | null>(null);

  if (info.error) return null; // not an ADMIN_EMAILS admin
  if (!info.data) return <Loading rows={2} />;

  const current = info.data.scopes.find((s) => s.scope === scope);
  const counts = info.data.counts;
  const total = current ? current.tables.reduce((sum, t) => sum + (counts[t] ?? 0), 0) : 0;
  const matches = !!current && typed.trim().toLowerCase() === current.confirm;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!current || !matches) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      setDone(await api.post<PurgeResult>("/admin/purge", { scope, confirm: typed }));
      setTyped("");
      await info.mutate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Xoá dữ liệu"
      subtitle="Làm sạch dữ liệu trên server này. Không đụng tới GitLab: repo, commit và webhook trên GitLab giữ nguyên."
    >
      <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
        <div style={{ display: "grid", gap: 8 }}>
          {info.data.scopes.map((s) => (
            <label key={s.scope} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <input
                type="radio"
                name="scope"
                value={s.scope}
                checked={scope === s.scope}
                onChange={() => {
                  setScope(s.scope);
                  setTyped("");
                }}
              />
              <span>
                <b>{SCOPE[s.scope].label}</b>
                <span className="muted small" style={{ display: "block" }}>
                  {SCOPE[s.scope].detail}
                </span>
              </span>
            </label>
          ))}
        </div>
        {current && (
          <details className="small">
            <summary className="muted">
              Sẽ xoá {total.toLocaleString("vi-VN")} dòng trong {current.tables.length} bảng
            </summary>
            <ul className="muted" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {current.tables.map((t) => (
                <li key={t}>
                  <code>{t}</code>: {(counts[t] ?? 0).toLocaleString("vi-VN")}
                </li>
              ))}
            </ul>
          </details>
        )}
        <Alert tone="warning">
          Không hoàn tác được. Audit log được giữ lại và ghi lần xoá này.
        </Alert>
        <label style={{ display: "grid", gap: 4 }}>
          <span className="small muted">
            Gõ <code>{current?.confirm}</code> để xác nhận
          </span>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
        </label>
        <div className="toolbar">
          <button className="danger" type="submit" disabled={busy || !matches}>
            {busy ? "Đang xoá…" : `Xoá: ${SCOPE[scope].label}`}
          </button>
        </div>
        {error && <Alert tone="error">Lỗi: {error}</Alert>}
        {done && (
          <Alert tone="success">
            Đã xoá {Object.values(done.rows).reduce((a, b) => a + b, 0).toLocaleString("vi-VN")} dòng.
            {done.recompute_queued ? "" : " Không xếp hàng được việc tính lại điểm (Redis?)."}
          </Alert>
        )}
      </form>
    </Card>
  );
}
