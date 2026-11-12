"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";


import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { CopyBlock, mcpAddCommand, useMcpUrl } from "@/components/McpConnect";
import { Alert, Card, Drawer, EmptyState, Expander, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { date, relative } from "@/lib/format";
import type { TcProject } from "@/lib/testcases";
import type { ApiToken, ApiTokenCreated, ApiTokenScope } from "@/lib/types";

/** docs/specs/26 §2: at most this many tokens still valid per person. */
export const TOKEN_LIMIT = 10;
const NAME_MAX = 80;
const DAYS = [30, 90, 180, 365] as const;
type Days = (typeof DAYS)[number];

const SCOPE_LABEL: Record<ApiTokenScope, { label: string; hint: string }> = {
  "findings:read": { label: "Đọc finding", hint: "Đọc finding critical/high và yêu cầu đóng của bạn" },
  "findings:close_request": { label: "Gửi yêu cầu đóng", hint: "Gửi và rút yêu cầu đóng finding (vẫn qua manager duyệt)" },
  "reviews:request": { label: "Yêu cầu review", hint: "Yêu cầu review commit đã push và xem tiến độ (tốn lượt review)" },
  "testcases:read": { label: "Đọc testcase", hint: "API testcase: đọc bộ testcase, case, test run, kết quả" },
  "testcases:write": { label: "Sửa testcase", hint: "API testcase: tạo, sửa testcase, test run, plan (gồm cả đọc và ghi kết quả)" },
  "results:write": { label: "Ghi kết quả test", hint: "API testcase: chỉ ghi kết quả test (CI), không sửa testcase" },
};

/** docs/specs/30 §15: the testcase API scopes, in the order shown. */
const TESTCASE_SCOPES = ["testcases:read", "testcases:write", "results:write"] as const;

/** The curl example of the "Dùng API" help. */
export function apiCurlExample(base: string, token = "cpat_…"): string {
  return [
    `curl -H "Authorization: Bearer ${token}" \\`,
    `  "${base}/api/v1/testcases/projects"`,
    "",
    "# Ghi kết quả một test (scope results:write)",
    `curl -X POST -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" \\`,
    `  -d '{"status": "failed", "comment": "Lỗi 500"}' \\`,
    `  "${base}/api/v1/testcases/tests/123/results"`,
  ].join("\n");
}

/** The dashboard's origin (the API is served under it, like /mcp). */
function useApiBase(): string {
  return useMcpUrl().replace(/\/mcp$/, "");
}

/** "Dùng API": how a script or CI calls the testcase API with a token. */
function ApiHelp({ token }: { token?: string }) {
  const base = useApiBase();
  return (
    <div className="token-reveal-field">
      <span className="small muted">
        Dùng API testcase (token cần scope Đọc / Sửa testcase hoặc Ghi kết quả). Tài liệu đầy đủ:{" "}
        <a href="/api/v1/testcases/api-docs" target="_blank" rel="noreferrer">
          OpenAPI
        </a>
        .
      </span>
      <CopyBlock label="Ví dụ curl" text={apiCurlExample(base, token)} secret={Boolean(token)} />
    </div>
  );
}

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** `cpat_ab12cd34…`, whether the API's prefix carries the `cpat_` part or not. */
export function tokenMask(prefix: string): string {
  return `${prefix.startsWith("cpat_") ? prefix : `cpat_${prefix}`}…`;
}

function daysUntil(iso: string, now = Date.now()): number {
  return Math.ceil((new Date(iso).getTime() - now) / 86_400_000);
}

/** Valid tokens first (soonest to expire last), then the rest, newest first. */
function sortTokens(tokens: ApiToken[]): ApiToken[] {
  return [...tokens].sort((a, b) => Number(b.active) - Number(a.active) || b.created_at.localeCompare(a.created_at));
}

/** Where a token stands: revoked, expired, or how long it has left (warning in the last week). */
function TokenState({ t }: { t: ApiToken }) {
  if (t.revoked_at)
    return (
      <Pill tone="bad" title={`Thu hồi ${date(t.revoked_at)}`}>
        Đã thu hồi
      </Pill>
    );
  const left = daysUntil(t.expires_at);
  if (!t.active || left <= 0)
    return (
      <Pill tone="bad" title={`Hết hạn ${date(t.expires_at)}`}>
        Hết hạn
      </Pill>
    );
  return (
    <Pill tone={left <= 7 ? "warn" : "good"} title={`Hết hạn ${date(t.expires_at)}`}>
      Còn {left} ngày
    </Pill>
  );
}

function TokenRow({
  t,
  revoke,
  ownerLabel,
}: {
  t: ApiToken;
  revoke: (t: ApiToken) => Promise<boolean>;
  /** Admin view: whose token it is, for the confirm text. */
  ownerLabel?: string;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <li className={`token-row${t.active ? "" : " inactive"}`}>
      <span className="token-icon" aria-hidden="true">
        <Icon name="key" size={16} />
      </span>
      <div className="token-body">
        <div className="token-title">
          <strong>{t.name}</strong>
          <TokenState t={t} />
          {t.scopes.map((s) => (
            <span key={s} className="chip small" title={SCOPE_LABEL[s]?.hint}>
              {SCOPE_LABEL[s]?.label ?? s}
            </span>
          ))}
          {t.business_project_id != null && (
            <span className="chip small" title="Token chỉ dùng được trong dự án này">
              Dự án: {t.business_project_name ?? `#${t.business_project_id}`}
            </span>
          )}
        </div>
        <div className="meta small muted">
          <code>{tokenMask(t.prefix)}</code> · tạo {date(t.created_at)} ·{" "}
          {t.revoked_at ? `thu hồi ${date(t.revoked_at)}` : `hết hạn ${date(t.expires_at)}`} ·{" "}
          {t.last_used_at ? <span title={date(t.last_used_at)}>dùng lần cuối {relative(t.last_used_at)}</span> : "chưa dùng lần nào"}
        </div>
        {asking && (
          <div className="token-confirm small" role="group" aria-label={`Xác nhận thu hồi ${t.name}`}>
            <span>
              Thu hồi <strong>{t.name}</strong>
              {ownerLabel ? ` của ${ownerLabel}` : ""}? Claude Code đang dùng token này mất quyền ngay, không hoàn tác được.
            </span>
            <span className="token-confirm-actions">
              <button
                type="button"
                className="danger sm"
                disabled={busy}
                autoFocus
                onClick={async () => {
                  setBusy(true);
                  const ok = await revoke(t);
                  setBusy(false);
                  if (ok) setAsking(false);
                }}
              >
                {busy ? "Đang thu hồi…" : "Thu hồi"}
              </button>
              <button type="button" className="ghost sm" disabled={busy} onClick={() => setAsking(false)}>
                Huỷ
              </button>
            </span>
          </div>
        )}
      </div>
      {t.active && !asking && (
        <button type="button" className="sm token-revoke" onClick={() => setAsking(true)}>
          Thu hồi
        </button>
      )}
    </li>
  );
}

/**
 * The tokens of one person: valid ones listed, expired and revoked ones folded below. `revokePath`
 * is the API path for one id (own page or Admin).
 */
export function TokenList({
  path,
  revokePath,
  ownerLabel,
  empty,
}: {
  path: string;
  revokePath: (id: number) => string;
  ownerLabel?: string;
  empty: ReactNode;
}) {
  const tokens = useApi<ApiToken[]>(path);
  const [message, setMessage] = useState<string | null>(null);

  async function revoke(t: ApiToken): Promise<boolean> {
    setMessage(null);
    try {
      await api.post(revokePath(t.id));
      setMessage(`Đã thu hồi token “${t.name}”.`);
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
      return false;
    } finally {
      await tokens.mutate();
    }
  }

  if (!tokens.data)
    return (
      <>
        <Loading error={tokens.error} rows={3} />
        {tokens.error && (
          <button type="button" className="sm" style={{ marginTop: 8 }} onClick={() => void tokens.mutate()}>
            Thử lại
          </button>
        )}
      </>
    );
  const sorted = sortTokens(tokens.data);
  const valid = sorted.filter((t) => t.active);
  const rest = sorted.filter((t) => !t.active);
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      {sorted.length === 0 ? (
        empty
      ) : (
        <>
          {valid.length ? (
            <ul className="token-list">
              {valid.map((t) => (
                <TokenRow key={t.id} t={t} revoke={revoke} ownerLabel={ownerLabel} />
              ))}
            </ul>
          ) : (
            <p className="muted small">Không còn token nào dùng được.</p>
          )}
          {rest.length > 0 && (
            <Expander summary={`${rest.length} token đã hết hạn hoặc đã thu hồi`}>
              <ul className="token-list">
                {rest.map((t) => (
                  <TokenRow key={t.id} t={t} revoke={revoke} />
                ))}
              </ul>
            </Expander>
          )}
        </>
      )}
    </>
  );
}

/** Quick-create (omicx-ui): name, the optional scopes (close requests, review requests), how long it lives. */
function CreateTokenModal({ onClose, onCreated }: { onClose: () => void; onCreated: (t: ApiTokenCreated) => void }) {
  const [name, setName] = useState("");
  const [closeScope, setCloseScope] = useState(true);
  const [reviewScope, setReviewScope] = useState(true);
  const [mcp, setMcp] = useState(true);
  const [tcScopes, setTcScopes] = useState<ApiTokenScope[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const projects = useApi<TcProject[]>(tcScopes.length ? "/testcases/projects" : null);
  const [days, setDays] = useState<Days>(90);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = name.trim().length > 0 && name.trim().length <= NAME_MAX && (mcp || tcScopes.length > 0);

  function toggleTc(scope: ApiTokenScope, on: boolean) {
    setTcScopes((cur) => (on ? [...cur.filter((s) => s !== scope), scope] : cur.filter((s) => s !== scope)));
  }

  async function create() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const scopes: ApiTokenScope[] = [];
      if (mcp) {
        scopes.push("findings:read");
        if (closeScope) scopes.push("findings:close_request");
        if (reviewScope) scopes.push("reviews:request");
      }
      scopes.push(...tcScopes);
      onCreated(
        await api.post<ApiTokenCreated>("/me/tokens", {
          name: name.trim(),
          scopes,
          days,
          business_project_id: tcScopes.length ? projectId : null,
        }),
      );
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? `Bạn đã có ${TOKEN_LIMIT} token còn hiệu lực: thu hồi một token không dùng rồi tạo lại.`
          : errorText(e),
      );
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Tạo token tích hợp MCP"
      onClose={onClose}
      busy={busy}
      width={468}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Đóng
          </button>
          <button type="button" className="primary" onClick={() => void create()} disabled={!ready || busy}>
            {busy ? "Đang tạo…" : "Tạo token"}
          </button>
        </>
      }
    >
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="adm-field">
          <span className="small muted">
            Tên token <span className="error">*</span>
          </span>
          <input
            value={name}
            maxLength={NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            placeholder="Ví dụ: Laptop công ty"
            autoFocus
            required
          />
        </label>
        <label className="inline-check token-scope">
          <input type="checkbox" checked={mcp} onChange={(e) => setMcp(e.target.checked)} />
          <span>
            Dùng cho Claude Code (MCP)
            <span className="muted small"> Đọc finding critical/high của bạn.</span>
          </span>
        </label>
        {mcp && (
          <>
            <label className="inline-check token-scope">
              <input type="checkbox" checked={closeScope} onChange={(e) => setCloseScope(e.target.checked)} />
              <span>
                Cho phép gửi yêu cầu đóng finding
                <span className="muted small"> Bỏ tick nếu token chỉ để đọc (CI, script). Đọc finding luôn được bật.</span>
              </span>
            </label>
            <label className="inline-check token-scope">
              <input type="checkbox" checked={reviewScope} onChange={(e) => setReviewScope(e.target.checked)} />
              <span>
                Cho phép yêu cầu review commit đã push
                <span className="muted small"> Claude Code gọi review ngay sau khi push; mỗi review tốn một lượt, có giới hạn mỗi ngày.</span>
              </span>
            </label>
          </>
        )}
        <fieldset className="adm-field">
          <legend className="small muted">API testcase (/api/v1/testcases)</legend>
          {TESTCASE_SCOPES.map((scope) => (
            <label key={scope} className="inline-check token-scope">
              <input type="checkbox" checked={tcScopes.includes(scope)} onChange={(e) => toggleTc(scope, e.target.checked)} />
              <span>
                {SCOPE_LABEL[scope].label}
                <span className="muted small"> {SCOPE_LABEL[scope].hint}.</span>
              </span>
            </label>
          ))}
        </fieldset>
        {tcScopes.length > 0 && (
          <label className="adm-field">
            <span className="small muted">Chỉ dùng trong dự án</span>
            <select
              value={projectId ?? ""}
              onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}
              disabled={!projects.data}
            >
              <option value="">Mọi dự án bạn xem được</option>
              {(projects.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="adm-field">
          <span className="small muted">Hạn dùng</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value) as Days)}>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} ngày
              </option>
            ))}
          </select>
        </label>
        <p className="muted small">
          Token dùng cho Claude Code (MCP) và API testcase, có quyền như bạn trong phạm vi đã chọn; không dùng được cho các API
          khác.
        </p>
      </form>
    </Modal>
  );
}

/** Shown once after creating: the token and the ready-to-run command, never again after "Xong". */
function RevealPanel({ token, onDone }: { token: ApiTokenCreated; onDone: () => void }) {
  const url = useMcpUrl();
  return (
    <div className="token-reveal" role="region" aria-label="Token vừa tạo">
      <Alert tone="warning">
        <strong>Chép token ngay.</strong> CodePulse chỉ hiện token “{token.name}” một lần này; đóng lại là không xem lại được, chỉ
        tạo token mới.
      </Alert>
      <div className="token-reveal-field">
        <span className="small muted">Token</span>
        <CopyBlock label="Token" text={token.token} secret />
      </div>
      {token.scopes.includes("findings:read") && (
        <div className="token-reveal-field">
          <span className="small muted">Chạy trong terminal để thêm CodePulse vào Claude Code</span>
          <CopyBlock label="Lệnh claude mcp add" text={mcpAddCommand(url, token.token)} secret />
        </div>
      )}
      {token.scopes.some((s) => (TESTCASE_SCOPES as readonly string[]).includes(s)) && <ApiHelp token={token.token} />}
      <div className="adm-form-foot">
        <button type="button" onClick={onDone}>
          Tôi đã lưu token
        </button>
      </div>
    </div>
  );
}

/** "Của tôi" › Tích hợp MCP: the person's own tokens (docs/specs/26 §2). */
export function MyTokensCard() {
  const tokens = useApi<ApiToken[]>("/me/tokens");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const activeCount = tokens.data?.filter((t) => t.active).length ?? 0;
  const full = activeCount >= TOKEN_LIMIT;

  return (
    <Card
      id="member-tokens"
      title="Token tích hợp MCP"
      subtitle={
        tokens.data
          ? `${activeCount} / ${TOKEN_LIMIT} token còn hiệu lực · Claude Code dùng token để đọc finding critical/high của bạn và gửi yêu cầu đóng`
          : "Claude Code dùng token để đọc finding critical/high của bạn và gửi yêu cầu đóng"
      }
      collapsible={false}
      actions={
        <button
          type="button"
          className="primary sm"
          disabled={!tokens.data || full}
          title={full ? `Đã đủ ${TOKEN_LIMIT} token còn hiệu lực: thu hồi một token trước` : undefined}
          onClick={() => setCreating(true)}
        >
          <Icon name="plus" size={14} />
          Tạo token
        </button>
      }
    >
      {creating && (
        <CreateTokenModal
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            setCreating(false);
            setCreated(t);
            void tokens.mutate();
          }}
        />
      )}
      {created && <RevealPanel token={created} onDone={() => setCreated(null)} />}
      <TokenList
        path="/me/tokens"
        revokePath={(id) => `/me/tokens/${id}/revoke`}
        empty={
          <EmptyState icon="key" title="Chưa có token nào">
            Tạo token rồi chạy lệnh <code>claude mcp add</code> hiện ra để Claude Code đọc finding của bạn.
          </EmptyState>
        }
      />
      <div className="muted small token-foot">
        Cách dùng và mẫu <code>.mcp.json</code>: <Link href="/guide/mcp">Hướng dẫn › Tích hợp MCP</Link>.
      </div>
      <Expander summary="Dùng API testcase">
        <ApiHelp />
      </Expander>
    </Card>
  );
}

/** Admin › Người dùng › key: one person's Claude Code tokens; the admin may revoke any of them. */
export function UserTokensDrawer({ userId, name, onClose }: { userId: number; name: string; onClose: () => void }) {
  return (
    <Drawer title={`Token tích hợp MCP · ${name}`} width={560} onClose={onClose}>
      <p className="muted small" style={{ marginTop: 0 }}>
        Token người này tạo ở trang “Của tôi” để Claude Code đọc finding và gửi yêu cầu đóng qua MCP. Thu hồi khi máy bị mất,
        người nghỉ việc hoặc token bị lộ; người dùng tự tạo lại token mới nếu cần.
      </p>
      <TokenList
        path={`/admin/users/${userId}/tokens`}
        revokePath={(id) => `/admin/tokens/${id}/revoke`}
        ownerLabel={name}
        empty={<EmptyState icon="key" title="Người này chưa tạo token nào" />}
      />
    </Drawer>
  );
}
