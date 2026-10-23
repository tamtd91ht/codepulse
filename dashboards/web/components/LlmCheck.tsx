"use client";

import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { LlmCheck as Report } from "@/lib/types";

const AUTH_LABEL: Record<string, string> = {
  "subscription session (CLAUDE_CODE_OAUTH_TOKEN)": "Phiên gói Claude (CLAUDE_CODE_OAUTH_TOKEN)",
  "API key (LLM_API_KEY)": "API key (LLM_API_KEY)",
  none: "Chưa cấu hình token (CLI dùng đăng nhập sẵn có, nếu có)",
};

/** Admin card: what LLM is configured, which account it runs on, and whether it answers now. */
export function LlmCheck() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    setError(null);
    try {
      setReport(await api.post<Report>("/admin/llm-check"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const account = report?.account;
  const status = report?.status;
  const rows: [string, string][] = report
    ? [
        ["Provider", report.provider],
        ["Model cấu hình", report.model === "default" ? "mặc định của CLI" : report.model],
        ["CLI", report.cli_binary ? `${report.cli_binary} ${report.cli_version ?? "(không chạy được)"}` : "—"],
        ["Xác thực", AUTH_LABEL[report.auth.method] ?? report.auth.method],
        ["Token", report.auth.token_hint ?? "—"],
        [
          "Tài khoản",
          account
            ? [account.email, account.organization, account.subscription_type && `gói ${account.subscription_type}`]
                .filter(Boolean)
                .join(" · ")
            : "Không xác định được",
        ],
        [
          "Dùng tháng này",
          `${report.usage.reviews} review · $${report.usage.cost_usd.toFixed(2)}` +
            (report.usage.budget_pct === null ? "" : ` (${report.usage.budget_pct}% ngân sách $${report.usage.budget_usd})`),
        ],
      ]
    : [];

  return (
    <div className="card">
      <h2>Kết nối LLM</h2>
      <div className="toolbar">
        <button className="primary" onClick={check} disabled={busy}>
          {busy ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
        </button>
        <span className="muted">Gửi một prompt rất nhỏ (không có code) qua đúng đường review; tốn một ít quota.</span>
      </div>
      {error && <p className="error">Lỗi: {error}</p>}
      {report && status && (
        <>
          <p style={{ color: status.ok ? "var(--good)" : "var(--bad)", fontWeight: 600 }}>
            {status.ok
              ? `Hoạt động · ${status.latency_ms} ms · model ${status.model} · $${(status.cost_usd ?? 0).toFixed(4)}`
              : `Lỗi: ${status.error ?? "không rõ"}`}
          </p>
          <div className="table-wrap">
            <table>
              <tbody>
                {rows.map(([label, value]) => (
                  <tr key={label}>
                    <th>{label}</th>
                    <td>{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!account &&
            (report.auth.cli_method ? (
              <p className="muted">
                Claude Code CLI chỉ cho biết email/tổ chức/gói khi đăng nhập trực tiếp (<code>claude auth login</code>);
                khi dùng token qua biến môi trường như trên server, CLI chỉ biết phương thức xác thực. Token thuộc tài
                khoản đã chạy <code>claude setup-token</code>.
              </p>
            ) : (
              <p className="muted">
                Không đọc được tài khoản: CLI không chạy được hoặc <code>claude auth status</code> không trả lời.
              </p>
            ))}
          <p className="muted">Kiểm tra lúc {new Date(report.checked_at).toLocaleString("vi-VN")}.</p>
        </>
      )}
    </div>
  );
}
