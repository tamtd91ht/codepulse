"use client";

import { type FormEvent, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { Alert, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTimeMin, relative } from "@/lib/format";
import type { LlmAccount, LlmAccounts, LlmCheck, LlmLogin } from "@/lib/types";

const time = (iso: string) => new Date(iso).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });

/** Why an account is or is not used now, as a chip. */
function StateChip({ a, active }: { a: LlmAccount; active: boolean }) {
  if (!a.enabled) return <Pill>Đã tắt</Pill>;
  if (active) return <Pill tone="good" icon="check">Đang dùng</Pill>;
  if (a.state === "ok") return <Pill tone="info">Sẵn sàng</Pill>;
  const when = a.retry_at ? ` · thử lại ${time(a.retry_at)}` : "";
  const due = a.usable ? " · sẽ thử ở lần gọi tới" : when;
  if (a.state === "quota")
    return (
      <Pill tone="warn" title={a.last_error ?? undefined}>
        Hết hạn mức{due}
      </Pill>
    );
  if (a.state === "auth")
    return (
      <Pill tone="bad" title={a.last_error ?? undefined}>
        Cần đăng nhập lại{due}
      </Pill>
    );
  return (
    <Pill tone="bad" title={a.last_error ?? undefined}>
      Gói không dùng được {a.blocked_model ?? "model"}
    </Pill>
  );
}

type Mode = { kind: "login"; login: LlmLogin; accountId: number | null; label: string } | { kind: "paste"; accountId: number | null } | null;

/**
 * Admin › LLM: the Claude accounts reviews run on (app/review/llm_accounts.py). The default one
 * is always used while it works; when its login fails or its limit runs out, the backups take
 * over in order, and the default is tried again on its own (at the reset time, or after the
 * retry interval) — no polling. Tokens are stored encrypted and never shown again.
 */
export function ClaudeLogin() {
  const list = useApi<LlmAccounts>("/admin/llm-accounts");
  const [mode, setMode] = useState<Mode>(null);
  const [newLabel, setNewLabel] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [checks, setChecks] = useState<Record<number, LlmCheck["status"]>>({});

  async function run(key: string, action: () => Promise<void>, done?: string) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await action();
      if (done) setNotice(done);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  const apply = async (next: LlmAccounts): Promise<void> => {
    await list.mutate(next, false);
  };

  const startLogin = (accountId: number | null, label: string) => {
    // Opened on the click itself: a window opened after the request would be blocked as a popup.
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    return run("login", async () => {
      try {
        const login = await api.post<LlmLogin>("/admin/llm-login");
        setMode({ kind: "login", login, accountId, label });
        if (tab) tab.location.href = login.url;
      } catch (e) {
        tab?.close();
        throw e;
      }
    });
  };

  const finishLogin = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (mode?.kind !== "login") return;
    const code = String(new FormData(e.currentTarget).get("code") ?? "").trim();
    const body = mode.accountId !== null ? { code, account_id: mode.accountId } : { code, label: mode.label };
    run(
      "login",
      async () => {
        await api.post(`/admin/llm-login/${mode.login.login_id}`, body);
        await list.mutate();
        setMode(null);
        setNewLabel("");
      },
      mode.accountId !== null ? "Đã đăng nhập lại; tài khoản dùng được ngay." : "Đã thêm tài khoản.",
    );
  };

  const cancelLogin = () =>
    run("login", async () => {
      if (mode?.kind === "login") await api.delete(`/admin/llm-login/${mode.login.login_id}`);
      setMode(null);
    });

  const paste = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (mode?.kind !== "paste") return;
    const token = String(new FormData(e.currentTarget).get("token") ?? "").trim();
    const accountId = mode.accountId;
    run(
      "paste",
      async () => {
        await apply(
          accountId !== null
            ? await api.put<LlmAccounts>(`/admin/llm-accounts/${accountId}/token`, { token })
            : await api.post<LlmAccounts>("/admin/llm-accounts", { label: newLabel.trim() || "Tài khoản Claude", token }),
        );
        setMode(null);
        setNewLabel("");
      },
      accountId !== null ? "Đã thay token; tài khoản dùng được ngay." : "Đã thêm tài khoản.",
    );
  };

  const patch = (a: LlmAccount, body: Record<string, unknown>, done?: string) =>
    run(`patch-${a.id}`, async () => apply(await api.patch<LlmAccounts>(`/admin/llm-accounts/${a.id}`, body)), done);

  const move = (rows: LlmAccount[], id: number, by: -1 | 1) => {
    const backups = rows.filter((r) => !r.is_default).map((r) => r.id);
    const i = backups.indexOf(id);
    const j = i + by;
    if (i < 0 || j < 0 || j >= backups.length) return;
    [backups[i], backups[j]] = [backups[j], backups[i]];
    return run(`move-${id}`, async () => apply(await api.put<LlmAccounts>("/admin/llm-accounts/order", { ids: backups })));
  };

  const check = (a: LlmAccount) =>
    run(`check-${a.id}`, async () => {
      const report = await api.post<LlmCheck>(`/admin/llm-accounts/${a.id}/check`);
      setChecks((c) => ({ ...c, [a.id]: report.status }));
      await list.mutate();
    });

  const remove = (a: LlmAccount) =>
    run(
      `remove-${a.id}`,
      async () => {
        await api.delete(`/admin/llm-accounts/${a.id}`);
        setRemoving(null);
        await list.mutate();
      },
      a.is_default ? "Đã xoá; dự phòng đầu tiên thành tài khoản mặc định." : "Đã xoá tài khoản.",
    );

  const rename = (e: FormEvent<HTMLFormElement>, a: LlmAccount) => {
    e.preventDefault();
    const label = String(new FormData(e.currentTarget).get("label") ?? "").trim();
    if (!label) return;
    void patch(a, { label }).then(() => setRenaming(null));
  };

  const d = list.data;
  const rows = d?.accounts ?? [];
  const def = rows.find((r) => r.is_default);
  const onBackup = d && def && d.active_id !== null && d.active_id !== def.id;
  const backups = rows.filter((r) => !r.is_default);

  return (
    <section className="card llm-accounts">
      <div className="card-head">
        <div>
          <h2>Tài khoản Claude</h2>
          <div className="sub">
            Review luôn chạy bằng tài khoản <b>mặc định</b>. Chỉ khi tài khoản đó lỗi đăng nhập, hết hạn mức hoặc gói không có model
            review, hệ thống mới chuyển sang dự phòng theo thứ tự, rồi tự quay về mặc định khi dùng lại được.
          </div>
        </div>
      </div>

      {list.error && <Alert tone="error">Lỗi: {list.error.message}</Alert>}
      {d && (
        <div className="llm-acc-summary">
          <span>
            <span className="muted small">Đang dùng</span>{" "}
            <b>{d.active_label ?? (rows.length ? "Không có tài khoản nào dùng được" : d.env_token_hint ? "Token trong .env" : "Đăng nhập của CLI")}</b>
          </span>
          <span>
            <span className="muted small">Model chung</span> <code>{d.model}</code>
          </span>
          <span>
            <span className="muted small">Thử lại sau lỗi</span> {d.retry_min} phút{" "}
            <span className="muted small">(Admin › Cấu hình)</span>
          </span>
        </div>
      )}
      {onBackup && def && (
        <Alert tone="warning">
          Đang chạy bằng dự phòng <b>{d?.active_label}</b> vì tài khoản mặc định {def.label}{" "}
          {def.state === "quota" ? "hết hạn mức" : def.state === "auth" ? "lỗi đăng nhập" : def.state === "model" ? "không dùng được model" : "đang tắt"}
          {def.retry_at ? `; sẽ thử lại mặc định lúc ${time(def.retry_at)}` : ""}.
        </Alert>
      )}
      {d && rows.length > 0 && d.active_id === null && !d.env_token_hint && (
        <Alert tone="error">Mọi tài khoản đang chờ; review sẽ tự chạy lại khi một tài khoản dùng được (nếu bật “Tự review lại”).</Alert>
      )}
      {error && <Alert tone="error">Lỗi: {error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      {d && rows.length === 0 && (
        <p className="muted llm-acc-empty">
          Chưa có tài khoản nào. {d.env_token_hint ? `Review đang dùng token trong .env (${d.env_token_hint}).` : "Thêm tài khoản đầu tiên: nó sẽ là tài khoản mặc định."}
        </p>
      )}

      {rows.length > 0 && (
        <ol className="llm-acc-list">
          {rows.map((a) => {
            const bi = backups.findIndex((b) => b.id === a.id);
            const result = checks[a.id];
            return (
              <li key={a.id} className={`llm-acc${a.is_default ? " default" : ""}${a.enabled ? "" : " off"}`}>
                <div className="llm-acc-main">
                  <div className="llm-acc-title">
                    {renaming === a.id ? (
                      <form onSubmit={(e) => rename(e, a)} className="llm-acc-rename">
                        <input name="label" defaultValue={a.label} maxLength={80} autoFocus aria-label="Tên tài khoản" />
                        <button type="submit" className="sm">
                          Lưu
                        </button>
                        <button type="button" className="sm ghost" onClick={() => setRenaming(null)}>
                          Huỷ
                        </button>
                      </form>
                    ) : (
                      <>
                        <b>{a.label}</b>
                        {a.is_default ? <Pill tone="primary">Mặc định</Pill> : <span className="muted small">Dự phòng {bi + 1}</span>}
                        <StateChip a={a} active={d?.active_id === a.id} />
                      </>
                    )}
                  </div>
                  <div className="muted small llm-acc-meta">
                    <code>{a.token_hint ?? "—"}</code>
                    {" · "}
                    {a.source === "login" ? "đăng nhập" : a.source === "paste" ? "dán token" : "token cũ"}
                    {a.set_by_name && ` · ${a.set_by_name}`}
                    {a.last_used_at && ` · dùng ${relative(a.last_used_at)}`}
                    {a.last_failed_at && a.state !== "ok" && ` · lỗi lúc ${dateTimeMin(a.last_failed_at)}`}
                  </div>
                  {a.state !== "ok" && a.last_error && <div className="small llm-acc-error">{a.last_error}</div>}
                  {result && (
                    <div className={`small ${result.ok ? "good" : "bad"}`}>
                      {result.ok ? `Kiểm tra: hoạt động · ${result.latency_ms} ms · ${result.model}` : `Kiểm tra: ${result.error ?? "lỗi"}`}
                    </div>
                  )}
                  {removing === a.id && (
                    <div className="llm-acc-confirm">
                      <span>
                        Xoá <b>{a.label}</b>?{a.is_default ? " Dự phòng đầu tiên sẽ thành mặc định." : ""}
                      </span>
                      <button type="button" className="sm danger" onClick={() => remove(a)} disabled={busy !== null}>
                        Xoá
                      </button>
                      <button type="button" className="sm ghost" onClick={() => setRemoving(null)}>
                        Huỷ
                      </button>
                    </div>
                  )}
                </div>
                <div className="llm-acc-actions">
                  <button type="button" className="sm" onClick={() => check(a)} disabled={busy !== null}>
                    {busy === `check-${a.id}` ? "Đang kiểm tra…" : "Kiểm tra"}
                  </button>
                  {!a.is_default && a.enabled && (
                    <button type="button" className="sm" onClick={() => patch(a, { is_default: true }, `${a.label} là tài khoản mặc định.`)} disabled={busy !== null}>
                      Đặt mặc định
                    </button>
                  )}
                  <button
                    type="button"
                    className="sm"
                    onClick={() => startLogin(a.id, a.label)}
                    disabled={busy !== null || !d?.login_supported}
                    title={d && !d.login_supported ? "Chỉ chạy được khi api chạy trong container Linux; hãy dán token." : undefined}
                  >
                    Đăng nhập lại
                  </button>
                  {!a.is_default && (
                    <>
                      <IconButton icon="chevronUp" label="Lên trên" onClick={() => move(rows, a.id, -1)} disabled={busy !== null || bi <= 0} />
                      <IconButton
                        icon="chevronDown"
                        label="Xuống dưới"
                        onClick={() => move(rows, a.id, 1)}
                        disabled={busy !== null || bi >= backups.length - 1}
                      />
                    </>
                  )}
                  <details className="llm-acc-more">
                    <summary className="icon-btn" aria-label="Thêm thao tác">
                      <Icon name="more" />
                    </summary>
                    <div className="llm-acc-menu" role="menu">
                      <button type="button" role="menuitem" onClick={() => setRenaming(a.id)}>
                        Đổi tên
                      </button>
                      <button type="button" role="menuitem" onClick={() => setMode({ kind: "paste", accountId: a.id })}>
                        Dán token mới
                      </button>
                      {!a.is_default && (
                        <button type="button" role="menuitem" onClick={() => patch(a, { enabled: !a.enabled })}>
                          {a.enabled ? "Tắt" : "Bật"}
                        </button>
                      )}
                      <button type="button" role="menuitem" className="bad" onClick={() => setRemoving(a.id)}>
                        Xoá
                      </button>
                    </div>
                  </details>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {mode?.kind === "login" ? (
        <form onSubmit={finishLogin} className="llm-acc-form">
          <p className="muted">
            {mode.accountId !== null ? `Đăng nhập lại “${mode.label}”. ` : `Thêm “${mode.label}”. `}
            1. Đăng nhập tài khoản Claude trong tab vừa mở (không mở được?{" "}
            <a href={mode.login.url} target="_blank" rel="noopener noreferrer">
              mở link đăng nhập
            </a>
            ). 2. Bấm <b>Authorize</b>, copy mã claude.ai hiện ra. 3. Dán mã vào đây.
          </p>
          <label className="llm-acc-field">
            <span className="small muted">Mã từ claude.ai</span>
            <input name="code" required autoComplete="off" spellCheck={false} placeholder="Dán mã ở đây" />
          </label>
          <div className="toolbar">
            <button className="primary" type="submit" disabled={busy !== null}>
              {busy === "login" ? "Đang xác nhận…" : "Xác nhận"}
            </button>
            <button type="button" onClick={cancelLogin} disabled={busy !== null}>
              Huỷ
            </button>
            <span className="muted small">Mã hết hạn sau {Math.round(mode.login.expires_in_s / 60)} phút.</span>
          </div>
        </form>
      ) : mode?.kind === "paste" ? (
        <form onSubmit={paste} className="llm-acc-form">
          {mode.accountId === null && (
            <label className="llm-acc-field">
              <span className="small muted">Tên tài khoản</span>
              <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} maxLength={80} placeholder="vd. Team seat 2" />
            </label>
          )}
          <label className="llm-acc-field">
            <span className="small muted">
              Token từ <code>claude setup-token</code> (hoặc API key <code>sk-ant-api…</code> làm dự phòng)
            </span>
            <input
              name="token"
              type="password"
              required
              autoComplete="new-password"
              placeholder="sk-ant-oat01-…"
              pattern="^\s*sk-ant-(oat|api)\d*-[A-Za-z0-9_-]{20,}\s*$"
              title="Dạng sk-ant-oat01-… (token của gói) hoặc sk-ant-api03-… (API key)"
            />
          </label>
          <div className="toolbar">
            <button className="primary" type="submit" disabled={busy !== null}>
              {mode.accountId !== null ? "Lưu token" : "Thêm tài khoản"}
            </button>
            <button type="button" onClick={() => setMode(null)}>
              Huỷ
            </button>
          </div>
        </form>
      ) : (
        <div className="toolbar llm-acc-add">
          <input
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
            maxLength={80}
            placeholder={rows.length ? "Tên tài khoản dự phòng" : "Tên tài khoản mặc định"}
            aria-label="Tên tài khoản mới"
          />
          <button
            className="primary ink"
            onClick={() => startLogin(null, newLabel.trim() || (rows.length ? `Dự phòng ${backups.length + 1}` : "Tài khoản mặc định"))}
            disabled={busy !== null || !d?.login_supported}
            title={d && !d.login_supported ? "Chỉ chạy được khi api chạy trong container Linux; hãy dán token." : undefined}
          >
            <Icon name="plus" size={14} /> {rows.length ? "Thêm dự phòng (đăng nhập)" : "Đăng nhập Claude"}
          </button>
          <button onClick={() => setMode({ kind: "paste", accountId: null })} disabled={busy !== null}>
            Dán token
          </button>
        </div>
      )}

      <details className="llm-acc-help">
        <summary>Ngữ cảnh review có giữ nguyên khi đổi tài khoản không?</summary>
        <ul className="small">
          <li>
            Có. Mỗi lần review là một lần gọi độc lập: không dùng session, không đọc CLAUDE.md hay cài đặt riêng của tài khoản. Mọi ngữ
            cảnh (rubric, tiêu chí, knowledge của dự án, diff) do CodePulse gửi kèm, nên tài khoản nào trả lời cũng nhận đúng một đầu
            vào.
          </li>
          <li>
            Mọi tài khoản dùng chung một model (<code>{d?.model ?? "…"}</code>), không bao giờ tự hạ model. Tài khoản có gói không dùng
            được model này bị bỏ qua thay vì trả lời bằng model khác.
          </li>
          <li>Mỗi review ghi lại tài khoản đã trả lời, để đối chiếu khi cần.</li>
          <li>
            Chỉ lỗi của chính tài khoản (đăng nhập, hạn mức, model) mới chuyển tài khoản. Lỗi máy chủ Anthropic, quá tải, timeout thì
            đổi tài khoản cũng không giúp gì, nên giữ cơ chế thử lại và “Tự review lại khi LLM hoạt động lại”.
          </li>
          <li>
            Dùng tài khoản do công ty sở hữu (seat Team/Enterprise) hoặc API key làm dự phòng; gom nhiều gói cá nhân để vượt hạn mức có
            thể trái điều khoản của Anthropic.
          </li>
        </ul>
      </details>
    </section>
  );
}
