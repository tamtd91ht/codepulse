"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { LlmLogin, LlmToken } from "@/lib/types";

const ACTIVE_LABEL: Record<LlmToken["active"], string> = {
  stored: "Phiên gói Claude (đăng nhập từ trang Admin)",
  env_oauth: "Phiên gói Claude (CLAUDE_CODE_OAUTH_TOKEN trong .env)",
  env_api_key: "API key (LLM_API_KEY trong .env, tính tiền theo credit)",
  none: "Chưa có token",
};

/**
 * Admin card: log the review bot in with a Claude subscription (`claude setup-token` run by the
 * api), or paste a token made elsewhere. The token is stored encrypted and never shown again.
 */
export function ClaudeLogin() {
  const status = useApi<LlmToken>("/admin/llm-token");
  const [login, setLogin] = useState<LlmLogin | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);

  async function run(action: () => Promise<void>, done?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const start = () => {
    // Opened on the click itself: a window opened after the request would be blocked as a popup.
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    return run(async () => {
      try {
        const started = await api.post<LlmLogin>("/admin/llm-login");
        setLogin(started);
        if (tab) tab.location.href = started.url;
      } catch (e) {
        tab?.close();
        throw e;
      }
    });
  };

  const finish = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const code = String(new FormData(e.currentTarget).get("code") ?? "").trim();
    if (!login) return;
    run(async () => {
      await status.mutate(await api.post<LlmToken>(`/admin/llm-login/${login.login_id}`, { code }), false);
      setLogin(null);
    }, "Đã đăng nhập. Review tiếp theo sẽ dùng phiên gói Claude này.");
  };

  const cancel = () =>
    run(async () => {
      if (login) await api.delete(`/admin/llm-login/${login.login_id}`);
      setLogin(null);
    });

  const paste = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const token = String(new FormData(e.currentTarget).get("token") ?? "").trim();
    run(async () => {
      await status.mutate(await api.put<LlmToken>("/admin/llm-token", { token }), false);
      setPasting(false);
    }, "Đã lưu token. Review tiếp theo sẽ dùng phiên gói Claude này.");
  };

  const logout = () =>
    run(async () => {
      await api.delete("/admin/llm-token");
      await status.mutate();
    }, "Đã xoá token đã lưu; review quay về cấu hình trong .env (nếu có).");

  const s = status.data;
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Đăng nhập Claude</h2>
          <div className="sub">
            Review chạy bằng Claude Code CLI với phiên của gói Claude (Pro/Max), không dùng API credit.
          </div>
        </div>
      </div>
      {status.error && <Alert tone="error">Lỗi: {status.error.message}</Alert>}
      {s && (
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <th>Đang dùng</th>
                <td>{ACTIVE_LABEL[s.active]}</td>
              </tr>
              <tr>
                <th>Token</th>
                <td>{s.token_hint ?? "—"}</td>
              </tr>
              {s.stored && (
                <tr>
                  <th>Đã lưu</th>
                  <td>
                    {s.stored_source === "login" ? "Đăng nhập" : "Dán token"}
                    {s.stored_by && ` · ${s.stored_by}`}
                    {s.stored_at && ` · ${new Date(s.stored_at).toLocaleString("vi-VN")}`}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {s?.active === "env_api_key" && (
        <Alert tone="warning">Đang dùng API key: mỗi review bị tính tiền theo credit. Đăng nhập bằng gói để thay.</Alert>
      )}
      {error && <Alert tone="error">Lỗi: {error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      {login ? (
        <form onSubmit={finish} style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}>
          <p className="muted">
            1. Đăng nhập tài khoản có gói Claude trong tab vừa mở (không mở được?{" "}
            <a href={login.url} target="_blank" rel="noopener noreferrer">
              mở link đăng nhập
            </a>
            ). 2. Bấm <b>Authorize</b>, copy mã claude.ai hiện ra. 3. Dán mã vào đây.
          </p>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">Mã từ claude.ai</span>
            <input name="code" required autoComplete="off" spellCheck={false} placeholder="Dán mã ở đây" />
          </label>
          <div className="toolbar">
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Đang xác nhận…" : "Xác nhận"}
            </button>
            <button type="button" onClick={cancel} disabled={busy}>
              Huỷ
            </button>
            <span className="muted small">Mã hết hạn sau {Math.round(login.expires_in_s / 60)} phút.</span>
          </div>
        </form>
      ) : (
        <div className="toolbar">
          <button
            className="primary ink"
            onClick={start}
            disabled={busy || !s?.login_supported}
            title={s && !s.login_supported ? "Chỉ chạy được khi api chạy trong container Linux; hãy dán token." : undefined}
          >
            {busy && !pasting ? "Đang mở…" : s?.stored ? "Đăng nhập lại" : "Đăng nhập Claude"}
          </button>
          <button onClick={() => setPasting((p) => !p)} disabled={busy}>
            Dán token
          </button>
          {s?.stored && (
            <button className="danger" onClick={logout} disabled={busy}>
              Đăng xuất
            </button>
          )}
        </div>
      )}

      {pasting && !login && (
        <form onSubmit={paste} style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">
              Token từ <code>claude setup-token</code> chạy trên máy bạn
            </span>
            <input
              name="token"
              type="password"
              required
              autoComplete="new-password"
              placeholder="sk-ant-oat01-…"
              pattern="^\s*sk-ant-oat\d*-[A-Za-z0-9_-]{20,}\s*$"
              title="Dạng sk-ant-oat01-… (token của gói, không phải API key)"
            />
          </label>
          <div className="toolbar">
            <button className="primary" type="submit" disabled={busy}>
              Lưu token
            </button>
          </div>
        </form>
      )}
      <p className="muted small" style={{ padding: "0 18px 14px" }}>
        Token được mã hoá bằng APP_ENCRYPTION_KEY, có hiệu lực khoảng 1 năm và được ưu tiên hơn token/API key trong
        .env. Review dùng chung giới hạn sử dụng của gói với tài khoản đã đăng nhập.
      </p>
    </section>
  );
}
