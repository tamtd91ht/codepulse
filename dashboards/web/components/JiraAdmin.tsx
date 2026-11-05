"use client";

/**
 * Admin › Jira and DPI › Jira (docs/specs/27 §12): connection (saved on the web, else the JIRA_*
 * variables), task source, registered Jira projects, sync and the field mapping. Needs
 * `jira.admin` (or admin); the API answers 403 otherwise.
 */
import { useState } from "react";

import { Alert, Card, EmptyState, Loading, Message, Pill, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import type { BusinessProject } from "@/lib/types";

type Priority = "P1" | "P2" | "P3";

export interface JiraMapping {
  story_points_field: string;
  sprint_field: string;
  flagged_field: string;
  types: Record<string, string>;
  default_type: string;
  prod_labels: string[];
  prod_issue_types: string[];
  priorities: Record<string, Priority>;
  blocked_statuses: string[];
}

export interface JiraProject {
  id: number;
  key: string;
  name: string;
  enabled: boolean;
  business_project: { id: number; name: string } | null;
  last_synced_at: string | null;
  synced_until: string | null;
  last_error: string | null;
  issues_count: number;
}

type ValueSource = "web" | "env" | null;
/** "pat": Personal Access Token (Jira 8.14+); "basic": service account + password (older). */
type AuthType = "pat" | "basic";

export interface JiraConnection {
  base_url: string | null;
  base_url_source: ValueSource;
  auth_type: AuthType;
  auth_type_source: ValueSource;
  username: string | null;
  username_source: ValueSource;
  /** The secret in effect (the PAT, or the password with basic auth). */
  token_set: boolean;
  token_source: ValueSource;
  token_updated_at: string | null;
  /** A token is saved on the web but cannot be decrypted (APP_ENCRYPTION_KEY changed). */
  token_unreadable: boolean;
  /** A token exists but belongs to another server than the URL: never sent there. */
  token_other_server: boolean;
  ca_set: boolean;
  ca_source: ValueSource;
  interval_min: number;
  backfill_days: number;
  timeout_s: number;
  enabled: boolean;
  configured: boolean;
  sources: Record<"interval_min" | "backfill_days" | "timeout_s" | "enabled", "web" | "env">;
  env: { base_url: boolean; token: boolean; ca_bundle: boolean; auth_type: boolean; username: boolean };
}

interface JiraView {
  configured: boolean;
  base_url: string | null;
  connection: JiraConnection;
  task_source: "gitlab" | "jira";
  mapping: JiraMapping;
  dpi_types: string[];
  projects: JiraProject[];
  accounts: { total: number; linked: number };
  interval_min: number;
  /** The last sync's login was refused: scheduled syncs wait (CAPTCHA protection). */
  auth_failure: { at: string; error: string } | null;
}

interface FieldRef {
  id: string;
  name: string;
}

interface TestResult {
  ok: boolean;
  version: string | null;
  server_title: string | null;
  deployment_type: string | null;
  /** Jira 8.14+ has Personal Access Tokens; null: version unknown. */
  pat_supported: boolean | null;
  auth_type: AuthType;
  /** The account the credentials log in as. */
  user: string | null;
  fields: { story_points: FieldRef | null; sprint: FieldRef | null; flagged: FieldRef | null };
}

const errText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function JiraAdmin() {
  const { data, error, mutate } = useApi<JiraView>("/dpi/jira");
  const [message, setMessage] = useState<string | null>(null);
  if (error) return error.status === 403 ? <Alert tone="info">Cần quyền "Cấu hình tích hợp Jira" (jira.admin): nhờ admin cấp ở Admin › Phân quyền.</Alert> : <Loading error={error} />;
  if (!data) return <Loading rows={6} />;
  const reload = () => void mutate();
  const done = (m: string) => (setMessage(m), reload());
  const c = data.connection;
  return (
    <div className="jira-admin">
      <Message text={message} onClose={() => setMessage(null)} />
      <Connection key={JSON.stringify(c)} data={data} onDone={done} />
      {data.configured ? (
        <>
          <Source data={data} onDone={done} />
          <Projects key={data.projects.map((p) => `${p.key}:${p.enabled}:${p.business_project?.id ?? ""}`).join("|")} data={data} onDone={done} />
          <MappingEditor key={JSON.stringify(data.mapping)} data={data} onDone={done} />
        </>
      ) : (
        <Card title="Nguồn task, dự án Jira và quy đổi trường" collapsible={false}>
          <EmptyState icon="info" title={c.base_url && c.token_set && !c.enabled ? "Tích hợp Jira đang tắt." : "Điền thẻ Kết nối trước."}>
            {c.base_url && c.token_set && !c.enabled
              ? "Bật “Tích hợp Jira” ở thẻ Kết nối rồi Lưu để chọn nguồn task, đăng ký dự án và đồng bộ."
              : "Nhập URL Jira và Personal Access Token (Jira cũ hơn 8.14: tài khoản + mật khẩu) ở thẻ Kết nối, bấm Kiểm tra kết nối rồi Lưu. Sau đó chọn nguồn task, đăng ký dự án Jira và sửa quy đổi trường tại đây."}
          </EmptyState>
        </Card>
      )}
    </div>
  );
}

type ConnKey = "base_url" | "auth_type" | "username" | "token" | "ca_pem" | "interval_min" | "backfill_days" | "timeout_s" | "enabled";
const CONN_KEYS: readonly string[] = ["base_url", "auth_type", "username", "token", "ca_pem", "interval_min", "backfill_days", "timeout_s", "enabled"];
const AUTH_OPTIONS: { value: AuthType; label: string }[] = [
  { value: "pat", label: "Personal Access Token (Jira ≥ 8.14)" },
  { value: "basic", label: "Tài khoản + mật khẩu (Jira cũ hơn 8.14)" },
];

/** "base_url: message" (the API's 422 text) → the field it is about, else none. */
function connError(message: string): { key: ConnKey | null; text: string } {
  const m = /^([a-z_]+): (.*)$/s.exec(message);
  return m && CONN_KEYS.includes(m[1]) ? { key: m[1] as ConnKey, text: m[2] } : { key: null, text: message };
}

/** Where a value in effect comes from, next to its label. */
function From({ source, env }: { source: ValueSource; env?: boolean }) {
  if (source === "web") return <Pill tone="primary">đã lưu trên web</Pill>;
  if (source === "env") return <Pill>biến môi trường</Pill>;
  return env === false ? <span className="muted small">chưa có</span> : null;
}

function Connection({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const c = data.connection;
  const [baseUrl, setBaseUrl] = useState(c.base_url ?? "");
  const [authType, setAuthType] = useState<AuthType>(c.auth_type);
  const [username, setUsername] = useState(c.username ?? "");
  const [token, setToken] = useState("");
  const [caPem, setCaPem] = useState("");
  const [enabled, setEnabled] = useState(c.enabled);
  const [intervalMin, setIntervalMin] = useState(String(c.interval_min));
  const [backfill, setBackfill] = useState(String(c.backfill_days));
  const [timeoutS, setTimeoutS] = useState(String(c.timeout_s));
  const [result, setResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [error, setError] = useState<{ key: ConnKey | null; text: string } | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | "reset" | null>(null);

  const trimmedUrl = baseUrl.trim();
  const urlChanged = trimmedUrl.replace(/\/+$/, "") !== (c.base_url ?? "");
  const num = (v: string) => (v.trim() === "" ? NaN : Number(v));
  const basic = authType === "basic";
  // A password is sent as typed (spaces may be part of it); a PAT is trimmed.
  const secret = basic ? token : token.trim();
  const trimmedUser = username.trim();
  const userChanged = basic && trimmedUser !== (c.username ?? "");
  const changes: Record<string, unknown> = {};
  if (urlChanged) changes.base_url = trimmedUrl;
  if (authType !== c.auth_type) changes.auth_type = authType;
  if (userChanged) changes.username = trimmedUser;
  if (secret) changes.token = secret;
  if (caPem.trim()) changes.ca_pem = caPem;
  if (enabled !== c.enabled) changes.enabled = enabled;
  if (num(intervalMin) !== c.interval_min) changes.interval_min = num(intervalMin);
  if (num(backfill) !== c.backfill_days) changes.backfill_days = num(backfill);
  if (num(timeoutS) !== c.timeout_s) changes.timeout_s = num(timeoutS);
  const dirty = Object.keys(changes).length > 0;
  const fromWeb =
    c.base_url_source === "web" || c.auth_type_source === "web" || c.username_source === "web" || c.token_source === "web" || c.ca_source === "web" || Object.values(c.sources).includes("web");
  const canTest = Boolean((trimmedUrl || c.base_url) && (secret || c.token_set) && (!basic || trimmedUser));
  // What the secret is called on this screen.
  const secretName = basic ? "mật khẩu" : "token";

  const fail = (e: unknown) => {
    const text = errText(e);
    setError(e instanceof ApiError && e.status === 422 ? connError(text) : { key: null, text });
  };
  const run = async (kind: "save" | "reset", work: () => Promise<unknown>, done: string) => {
    setBusy(kind);
    setError(null);
    try {
      await work();
      onDone(done);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };
  const save = () =>
    void run("save", () => api.put("/dpi/jira/connection", changes), "Đã lưu kết nối Jira. Lịch đồng bộ dùng cấu hình mới trong vòng một phút.");
  const clearToken = () => {
    if (
      !window.confirm(
        c.env.token ? `Xoá ${secretName} đã lưu trên web? Sau đó dùng JIRA_TOKEN của biến môi trường.` : `Xoá ${secretName} đã lưu trên web? Jira sẽ ngừng đồng bộ cho tới khi nhập ${secretName} mới.`,
      )
    )
      return;
    void run("save", () => api.put("/dpi/jira/connection", { token: "" }), `Đã xoá ${secretName} Jira đã lưu.`);
  };
  const clearCa = () => void run("save", () => api.put("/dpi/jira/connection", { ca_pem: "" }), "Đã xoá CA đã lưu.");
  const reset = () => {
    if (!window.confirm("Xoá mọi giá trị kết nối Jira đã lưu trên web (URL, cách đăng nhập, token / mật khẩu, CA, chu kỳ…) và dùng lại biến môi trường JIRA_*?")) return;
    void run("reset", () => api.delete("/dpi/jira/connection"), "Đã xoá cấu hình Jira trên web, đang dùng biến môi trường.");
  };
  const test = async () => {
    setBusy("test");
    setTestError(null);
    setResult(null);
    const body: Record<string, string> = {};
    if (urlChanged && trimmedUrl) body.base_url = trimmedUrl;
    if (authType !== c.auth_type) body.auth_type = authType;
    if (userChanged && trimmedUser) body.username = trimmedUser;
    if (secret) body.token = secret;
    if (caPem.trim()) body.ca_pem = caPem;
    try {
      setResult(await api.post<TestResult>("/dpi/jira/test", body));
    } catch (e) {
      setTestError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const fe = (key: ConnKey) => (error?.key === key ? error.text : undefined);
  const tokenState = c.token_source === "web"
    ? `Đã lưu${c.token_updated_at ? ` (cập nhật ${relative(c.token_updated_at)})` : ""}`
    : c.token_source === "env"
      ? "Đang dùng biến môi trường JIRA_TOKEN"
      : c.token_other_server
        ? `Chưa có ${secretName} cho máy chủ này (${secretName} hiện có thuộc máy chủ khác, không được gửi sang đây)`
        : "Chưa có";
  const cleartext = /^http:\/\//i.test(trimmedUrl);
  const oldJira = result !== null && result.pat_supported === false;
  const field = (label: string, f: FieldRef | null) => (
    <li>
      {label}:{" "}
      {f ? (
        <>
          <strong>{f.name}</strong> <code className="small">{f.id}</code>
        </>
      ) : (
        <span className="tone-warn">không tìm thấy</span>
      )}
    </li>
  );
  const number = (label: string, value: string, set: (v: string) => void, key: "interval_min" | "backfill_days" | "timeout_s", min: number, max: number, hint: string) => (
    <label className="jira-field">
      <span>
        {label} <From source={c.sources[key]} />
      </span>
      <input type="number" inputMode="numeric" min={min} max={max} step={1} value={value} onChange={(e) => set(e.target.value)} aria-invalid={!!fe(key)} />
      <span className="muted small">{hint}</span>
      <FieldError text={fe(key)} />
    </label>
  );

  return (
    <Card
      title="Kết nối"
      collapsible={false}
      subtitle={
        c.configured
          ? `Chỉ đọc. Đồng bộ mỗi ${c.interval_min} phút. Tài khoản Jira đã nối người dùng: ${data.accounts.linked} / ${data.accounts.total}.`
          : "Jira Data Center / Server, chỉ đọc: một tài khoản dịch vụ có quyền Browse projects, đăng nhập bằng Personal Access Token (Jira ≥ 8.14) hoặc tài khoản + mật khẩu."
      }
      actions={c.configured ? <Pill tone="good">đã cấu hình</Pill> : !c.enabled && c.base_url && c.token_set ? <Pill tone="warn">đang tắt</Pill> : <Pill>chưa cấu hình</Pill>}
    >
      {data.auth_failure && (
        <Alert tone="error">
          Lần đồng bộ gần nhất ({relative(data.auth_failure.at)}) bị Jira từ chối đăng nhập: {data.auth_failure.error}. Đồng bộ tự động đang tạm dừng để không khoá tài khoản dịch vụ. Sửa
          thông tin đăng nhập rồi Lưu; nếu Jira đã bật CAPTCHA, đăng nhập web bằng tài khoản dịch vụ để giải CAPTCHA rồi bấm Kiểm tra kết nối (hoặc Đồng bộ ngay).
        </Alert>
      )}
      {c.token_unreadable && (
        <Alert tone="warning">
          {basic ? "Mật khẩu" : "Token"} đã lưu trên web không giải mã được (APP_ENCRYPTION_KEY đã đổi?). Nhập lại {secretName} rồi Lưu
          {c.env.token ? "; trong lúc đó dùng JIRA_TOKEN nếu cùng máy chủ" : ""}.
        </Alert>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty && !busy) save();
        }}
      >
        <div className="jira-grid">
          <label className="jira-field">
            <span>
              URL Jira <From source={c.base_url_source} env={c.env.base_url} />
            </span>
            <input type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://jira.congty.vn" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} aria-invalid={!!fe("base_url")} />
            <span className="muted small">Địa chỉ gốc, có thể kèm context path (ví dụ /jira). {basic ? "Mật khẩu" : "Token"} chỉ gửi tới máy chủ nó được nhập cho: đổi máy chủ thì nhập lại.</span>
            {cleartext &&
              (basic ? (
                <span className="tone-bad small">http:// không mã hoá: tài khoản và mật khẩu đi qua mạng dạng rõ, ai nghe lén được là đăng nhập được. Chỉ dùng https://.</span>
              ) : (
                <span className="tone-warn small">http:// không mã hoá: token đi qua mạng dạng rõ. Nên dùng https://.</span>
              ))}
            <FieldError text={fe("base_url")} />
          </label>
          <div className="jira-field jira-auth">
            <span>
              Cách đăng nhập <From source={c.auth_type_source === "web" ? "web" : null} />
            </span>
            <Segmented<AuthType> value={authType} onChange={setAuthType} options={AUTH_OPTIONS} label="Cách đăng nhập Jira" />
            <span className="muted small">
              {basic
                ? "Cho Jira Server/Data Center cũ hơn 8.14 (chưa có PAT). Dùng tài khoản dịch vụ riêng chỉ có quyền Browse projects, mật khẩu mạnh không dùng ở nơi khác."
                : "Jira 8.14 trở lên. Xem phiên bản ở <URL Jira>/rest/api/2/serverInfo."}
            </span>
            <FieldError text={fe("auth_type")} />
          </div>
          {basic && (
            <label className="jira-field">
              <span>
                Username <From source={c.username_source} env={c.env.username} />
              </span>
              <input
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="codepulse-bot"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-invalid={!!fe("username")}
              />
              <span className="muted small">Tên đăng nhập của tài khoản dịch vụ{c.username_source === "env" ? " (đang dùng JIRA_USERNAME)" : ""}.</span>
              <FieldError text={fe("username")} />
            </label>
          )}
          <label className="jira-field">
            <span>
              {basic ? "Mật khẩu" : "Personal Access Token"} <From source={c.token_source} env={c.env.token} />
            </span>
            <input
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              placeholder={c.token_set ? `Để trống: giữ ${secretName} hiện tại` : basic ? "Mật khẩu của tài khoản dịch vụ" : "Dán PAT của tài khoản dịch vụ"}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              aria-invalid={!!fe("token")}
              aria-describedby="jira-token-state"
            />
            <span id="jira-token-state" className="muted small">
              {tokenState}. {basic ? "Mật khẩu" : "Token"} được mã hoá khi lưu và không bao giờ hiện lại.
            </span>
            {c.token_source === "web" && (
              <span>
                <button type="button" className="ghost sm" disabled={!!busy} onClick={clearToken}>
                  Xoá {secretName} đã lưu
                </button>
              </span>
            )}
            <FieldError text={fe("token")} />
          </label>
        </div>
        <label className="jira-field jira-wide">
          <span>
            CA nội bộ (PEM, tuỳ chọn) <From source={c.ca_source} />
          </span>
          <textarea
            rows={4}
            spellCheck={false}
            placeholder={c.ca_source === "web" ? "Đã lưu một CA. Dán CA khác để thay, để trống: giữ nguyên." : "-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----"}
            value={caPem}
            onChange={(e) => setCaPem(e.target.value)}
            aria-invalid={!!fe("ca_pem")}
          />
          <span className="muted small">
            Chỉ cần khi Jira dùng chứng chỉ do CA nội bộ cấp. Không có: dùng {c.env.ca_bundle ? "JIRA_CA_BUNDLE" : "CA của hệ thống"}.
          </span>
          {c.ca_source === "web" && (
            <span>
              <button type="button" className="ghost sm" disabled={!!busy} onClick={clearCa}>
                Xoá CA đã lưu
              </button>
            </span>
          )}
          <FieldError text={fe("ca_pem")} />
        </label>
        <div className="jira-grid">
          {number("Chu kỳ đồng bộ (phút)", intervalMin, setIntervalMin, "interval_min", 1, 1440, "1–1440. Mặc định 15.")}
          {number("Số ngày lấy lùi lần đầu", backfill, setBackfill, "backfill_days", 1, 3650, "1–3650. Dự án mới đồng bộ lần đầu lấy issue cập nhật trong N ngày.")}
          {number("Timeout (giây)", timeoutS, setTimeoutS, "timeout_s", 1, 300, "1–300, cho mỗi lời gọi Jira.")}
        </div>
        <label className="jira-check">
          <input type="checkbox" role="switch" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Tích hợp Jira <From source={c.sources.enabled === "web" ? "web" : null} />
          <span className="muted small"> Tắt: ngừng đồng bộ, dữ liệu đã lấy vẫn giữ.</span>
        </label>
        <FieldError text={fe("enabled")} />

        {error && error.key === null && <Alert tone="error">Lỗi: {error.text}</Alert>}
        {testError && <Alert tone="error">Kết nối lỗi: {testError}</Alert>}
        {result && oldJira && authType === "pat" && (
          <Alert tone="warning">
            Jira {result.version} chưa có Personal Access Token (có từ 8.14): chọn “Tài khoản + mật khẩu” và nhập tài khoản dịch vụ.
          </Alert>
        )}
        {result && (
          <Alert tone="success">
            <div>
              Kết nối được: {result.server_title ?? "Jira"} · phiên bản {result.version ?? "—"}
              {result.deployment_type ? ` (${result.deployment_type})` : ""}
              {result.user ? ` · đăng nhập là ${result.user}` : ""}
              {dirty ? " (chưa lưu)" : ""}
            </div>
            <ul className="small" style={{ margin: "4px 0 0" }}>
              {field("Story Points", result.fields.story_points)}
              {field("Sprint", result.fields.sprint)}
              {field("Flagged", result.fields.flagged)}
            </ul>
          </Alert>
        )}
        <div className="dpi-row" style={{ marginTop: 12 }}>
          {fromWeb && (
            <button type="button" className="link sm" disabled={!!busy} onClick={reset} title="Xoá các giá trị đã lưu trên web">
              {busy === "reset" ? "Đang xoá…" : "Dùng lại biến môi trường"}
            </button>
          )}
          <span className="dpi-spacer" />
          <button type="button" className="sm" disabled={!!busy || !canTest} onClick={() => void test()} title={canTest ? "Thử giá trị đang nhập, chưa lưu" : basic ? "Nhập URL, username và mật khẩu trước" : "Nhập URL và token trước"}>
            {busy === "test" ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
          </button>
          <button type="submit" className={dirty ? "primary sm" : "sm"} disabled={!dirty || !!busy}>
            {busy === "save" ? "Đang lưu…" : "Lưu"}
          </button>
        </div>
      </form>
    </Card>
  );
}

function Source({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const [pending, setPending] = useState<"gitlab" | "jira" | null>(null);
  const apply = async () => {
    if (!pending) return;
    try {
      await api.put("/dpi/jira/source", { source: pending });
      onDone(pending === "jira" ? "DPI đã chuyển sang đọc task từ Jira." : "DPI đã chuyển sang đọc task từ GitLab Issues.");
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    }
    setPending(null);
  };
  const label = (s: "gitlab" | "jira") => (s === "jira" ? "Jira" : "GitLab Issues");
  return (
    <Card title="Nguồn task của DPI" collapsible={false} subtitle="A1, A2, C2, C3, R4 và lỗi production đọc issue của đúng một nguồn, nên không tính trùng.">
      <div className="dpi-row" role="radiogroup" aria-label="Nguồn task">
        {(["gitlab", "jira"] as const).map((s) => (
          <label key={s}>
            <input type="radio" name="jira-task-source" checked={(pending ?? data.task_source) === s} onChange={() => setPending(s === data.task_source ? null : s)} /> {label(s)}
            {data.task_source === s && <span className="muted small"> (đang dùng)</span>}
          </label>
        ))}
      </div>
      {pending && (
        <Alert tone="warning">
          Từ giờ DPI chỉ đọc issue từ <strong>{label(pending)}</strong>.{" "}
          {pending === "jira"
            ? "Collector GitLab ngừng lấy issue; dữ liệu GitLab Issues cũ vẫn giữ, chuyển lại thì lấy tiếp."
            : "Jira vẫn đồng bộ nền để dữ liệu sẵn sàng khi chuyển lại."}{" "}
          Scorecard các tháng đã tính không đổi cho tới khi chạy lại dữ liệu DPI.{" "}
          <button type="button" className="primary ink sm" onClick={() => void apply()}>
            Xác nhận
          </button>{" "}
          <button type="button" className="ghost sm" onClick={() => setPending(null)}>
            Huỷ
          </button>
        </Alert>
      )}
    </Card>
  );
}

interface Draft {
  key: string;
  name: string;
  enabled: boolean;
  business_project_id: number | null;
}

function Projects({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const businessProjects = useApi<BusinessProject[]>("/business-projects");
  const initial: Draft[] = data.projects.map((p) => ({ key: p.key, name: p.name, enabled: p.enabled, business_project_id: p.business_project?.id ?? null }));
  const [rows, setRows] = useState<Draft[]>(initial);
  const [picking, setPicking] = useState(false);
  const remote = useApi<{ items: { key: string; name: string }[] }>(picking ? "/dpi/jira/remote-projects" : null);
  const [pick, setPick] = useState("");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const byKey = new Map(data.projects.map((p) => [p.key, p]));
  const dirty = JSON.stringify(rows) !== JSON.stringify(initial);
  const available = (remote.data?.items ?? []).filter((r) => !rows.some((x) => x.key === r.key));
  const set = (i: number, patch: Partial<Draft>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await api.put("/dpi/jira/projects", { items: rows });
      onDone("Đã lưu danh sách dự án Jira.");
    } catch (e) {
      setSaveError(errText(e));
    } finally {
      setSaving(false);
    }
  };
  const sync = async () => {
    setSyncing(true);
    try {
      await api.post("/dpi/jira/sync");
      onDone("Đã đưa việc đồng bộ Jira vào hàng đợi. Tải lại sau ít phút để xem kết quả.");
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    } finally {
      setSyncing(false);
    }
  };
  const add = () => {
    const r = available.find((x) => x.key === pick);
    if (!r) return;
    setRows([...rows, { key: r.key, name: r.name, enabled: true, business_project_id: null }]);
    setPick("");
  };
  return (
    <Card
      title="Dự án Jira"
      collapsible={false}
      subtitle="Chỉ dự án bật mới được đồng bộ và nối key với MR. Gán dự án CodePulse để lọc báo cáo và phân quyền; chưa gán thì chỉ admin thấy."
      actions={
        <button type="button" className="sm" disabled={syncing || data.projects.every((p) => !p.enabled)} onClick={() => void sync()}>
          {syncing ? "Đang gửi…" : "Đồng bộ ngay"}
        </button>
      }
    >
      {rows.length === 0 ? (
        <EmptyState title="Chưa đăng ký dự án Jira nào.">Bấm “Thêm dự án” để chọn từ danh sách dự án trên Jira.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Dự án</th>
                <th>Bật</th>
                <th>Dự án CodePulse</th>
                <th>Đồng bộ gần nhất</th>
                <th className="num">Issue</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const p = byKey.get(r.key);
                return (
                  <tr key={r.key}>
                    <td className="jira-key">
                      <code>{r.key}</code>
                      <div className="muted small">{r.name}</div>
                    </td>
                    <td>
                      <input type="checkbox" role="switch" checked={r.enabled} aria-label={`Bật đồng bộ ${r.key}`} onChange={(e) => set(i, { enabled: e.target.checked })} />
                    </td>
                    <td>
                      <select
                        value={r.business_project_id ?? ""}
                        aria-label={`Dự án CodePulse của ${r.key}`}
                        onChange={(e) => set(i, { business_project_id: e.target.value ? Number(e.target.value) : null })}
                      >
                        <option value="">Chưa gán</option>
                        {(businessProjects.data ?? []).map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="small">
                      {!p ? (
                        <span className="muted">chưa lưu</span>
                      ) : p.last_synced_at ? (
                        <span title={dateTime(p.last_synced_at)}>{relative(p.last_synced_at)}</span>
                      ) : (
                        <span className="muted">chưa đồng bộ</span>
                      )}
                      {p?.last_error && <div className="tone-bad">{p.last_error}</div>}
                    </td>
                    <td className="num">{p ? p.issues_count.toLocaleString("vi-VN") : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="dpi-row" style={{ marginTop: 8 }}>
        {picking ? (
          remote.error ? (
            <span className="tone-bad small">Không lấy được danh sách dự án: {remote.error.message}</span>
          ) : !remote.data ? (
            <span className="muted small">Đang tải dự án trên Jira…</span>
          ) : (
            <>
              <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Dự án Jira cần thêm">
                <option value="">{available.length ? "Chọn dự án…" : "Đã thêm hết"}</option>
                {available.map((r) => (
                  <option key={r.key} value={r.key}>
                    {r.key} · {r.name}
                  </option>
                ))}
              </select>
              <button type="button" className="sm" disabled={!pick} onClick={add}>
                Thêm
              </button>
              <button type="button" className="ghost sm" onClick={() => setPicking(false)}>
                Đóng
              </button>
            </>
          )
        ) : (
          <button type="button" className="ghost sm" onClick={() => setPicking(true)}>
            Thêm dự án
          </button>
        )}
        <span className="dpi-spacer" />
        {dirty && (
          <button type="button" className="ghost sm" onClick={() => setRows(initial)}>
            Huỷ thay đổi
          </button>
        )}
        <button type="button" className={dirty ? "primary sm" : "sm"} disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? "Đang lưu…" : "Lưu"}
        </button>
      </div>
      {saveError && <Alert tone="error">Lỗi: {saveError}</Alert>}
    </Card>
  );
}

/* ---- mapping ---- */

type Pair = [string, string];
const toPairs = (o: Record<string, string>): Pair[] => Object.entries(o);
const toList = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

const FIELD_KEYS = ["story_points_field", "sprint_field", "flagged_field", "types", "default_type", "prod_labels", "prod_issue_types", "priorities", "blocked_statuses"] as const;
type FieldKey = (typeof FIELD_KEYS)[number];

/** Split "field: message; field2: message" (lib/api's 422 text) into per-field errors. */
function splitErrors(message: string): { fields: Partial<Record<FieldKey, string>>; rest: string[] } {
  const fields: Partial<Record<FieldKey, string>> = {};
  const rest: string[] = [];
  for (const part of message.split("; ")) {
    const m = /^([a-z_]+)(?:\.[^:]*)?: (.*)$/.exec(part);
    const key = m?.[1] as FieldKey | undefined;
    if (m && key && (FIELD_KEYS as readonly string[]).includes(key)) fields[key] = fields[key] ? `${fields[key]}; ${m[2]}` : m[2];
    else rest.push(part);
  }
  return { fields, rest };
}

function FieldError({ text }: { text?: string }) {
  return text ? <div className="tone-bad small">{text}</div> : null;
}

function PairTable({
  rows,
  onChange,
  left,
  right,
  options,
  addLabel,
}: {
  rows: Pair[];
  onChange: (rows: Pair[]) => void;
  left: string;
  right: string;
  options: string[];
  addLabel: string;
}) {
  return (
    <>
      <table className="table compact jira-pairs">
        <thead>
          <tr>
            <th>{left}</th>
            <th>{right}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, v], i) => (
            <tr key={i}>
              <td>
                <input value={k} aria-label={`${left} dòng ${i + 1}`} onChange={(e) => onChange(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
              </td>
              <td>
                <select value={v} aria-label={`${right} dòng ${i + 1}`} onChange={(e) => onChange(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))}>
                  {options.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <button type="button" className="ghost sm" title="Xoá" aria-label={`Xoá ${k || `dòng ${i + 1}`}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                  Xoá
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="link sm" onClick={() => onChange([...rows, ["", options[0] ?? ""]])}>
        {addLabel}
      </button>
    </>
  );
}

function MappingEditor({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const m = data.mapping;
  const [fields, setFields] = useState({ story_points_field: m.story_points_field, sprint_field: m.sprint_field, flagged_field: m.flagged_field });
  const [types, setTypes] = useState<Pair[]>(toPairs(m.types));
  const [defaultType, setDefaultType] = useState(m.default_type);
  const [prodLabels, setProdLabels] = useState(m.prod_labels.join(", "));
  const [prodTypes, setProdTypes] = useState(m.prod_issue_types.join(", "));
  const [priorities, setPriorities] = useState<Pair[]>(toPairs(m.priorities));
  const [blocked, setBlocked] = useState(m.blocked_statuses.join(", "));
  const [errors, setErrors] = useState<{ fields: Partial<Record<FieldKey, string>>; rest: string[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const body: JiraMapping = {
    ...fields,
    types: Object.fromEntries(types.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v])),
    default_type: defaultType,
    prod_labels: toList(prodLabels),
    prod_issue_types: toList(prodTypes),
    priorities: Object.fromEntries(priorities.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v as Priority])),
    blocked_statuses: toList(blocked),
  };
  const canon = (x: JiraMapping) => JSON.stringify(FIELD_KEYS.map((k) => x[k]));
  const dirty = canon(body) !== canon(m);
  const save = async () => {
    setSaving(true);
    setErrors(null);
    try {
      await api.put("/dpi/jira/mapping", body);
      onDone("Đã lưu bảng quy đổi Jira. Áp dụng cho các lần đồng bộ sau.");
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setErrors(splitErrors(e.message));
      else setErrors({ fields: {}, rest: [errText(e)] });
    } finally {
      setSaving(false);
    }
  };
  const fe = errors?.fields ?? {};
  const text = (key: "story_points_field" | "sprint_field" | "flagged_field", label: string, hint: string) => (
    <label className="jira-field">
      <span>{label}</span>
      <input value={fields[key]} onChange={(e) => setFields({ ...fields, [key]: e.target.value })} aria-invalid={!!fe[key]} />
      <span className="muted small">{hint}</span>
      <FieldError text={fe[key]} />
    </label>
  );
  return (
    <Card title="Quy đổi trường" collapsible={false} subtitle="Cách đọc issue Jira thành dữ liệu DPI (docs/specs/27 §5–6). Thay đổi áp dụng từ lần đồng bộ sau; issue đã lấy cập nhật lại khi có thay đổi trên Jira.">
      <div className="jira-grid">
        {text("story_points_field", "Trường Story Points", "Tên custom field, hệ thống tự tìm id.")}
        {text("sprint_field", "Trường Sprint", "Dùng cho hạn (ngày kết thúc sprint) và milestone.")}
        {text("flagged_field", "Trường Flagged", "Đang Flagged → blocked::impediment.")}
      </div>
      <div className="jira-two">
        <div>
          <h4>Loại issue → loại DPI</h4>
          <PairTable rows={types} onChange={setTypes} left="Loại issue Jira" right="Loại DPI" options={data.dpi_types} addLabel="Thêm loại issue" />
          <FieldError text={fe.types} />
          <label className="jira-field">
            <span>Loại khác</span>
            <select value={defaultType} onChange={(e) => setDefaultType(e.target.value)}>
              {data.dpi_types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <span className="muted small">Sub-task theo loại của issue cha.</span>
            <FieldError text={fe.default_type} />
          </label>
        </div>
        <div>
          <h4>Lỗi production</h4>
          <label className="jira-field">
            <span>Label của Bug production</span>
            <input value={prodLabels} onChange={(e) => setProdLabels(e.target.value)} placeholder="production, prod-bug" aria-invalid={!!fe.prod_labels} />
            <span className="muted small">Phân cách bằng dấu phẩy. Bug có một trong các label này là lỗi production.</span>
            <FieldError text={fe.prod_labels} />
          </label>
          <label className="jira-field">
            <span>Loại issue luôn là lỗi production</span>
            <input value={prodTypes} onChange={(e) => setProdTypes(e.target.value)} placeholder="Incident" aria-invalid={!!fe.prod_issue_types} />
            <FieldError text={fe.prod_issue_types} />
          </label>
          <h4>Priority → mức lỗi</h4>
          <PairTable rows={priorities} onChange={setPriorities} left="Priority Jira" right="Mức" options={["P1", "P2", "P3"]} addLabel="Thêm priority" />
          <p className="muted small">Priority không có trong bảng → P3.</p>
          <FieldError text={fe.priorities} />
          <label className="jira-field">
            <span>Status coi là blocked</span>
            <input value={blocked} onChange={(e) => setBlocked(e.target.value)} placeholder="Blocked" aria-invalid={!!fe.blocked_statuses} />
            <span className="muted small">Phân cách bằng dấu phẩy → label blocked::&lt;tên status&gt; cho báo cáo R4.</span>
            <FieldError text={fe.blocked_statuses} />
          </label>
        </div>
      </div>
      {errors && errors.rest.length > 0 && <Alert tone="error">Lỗi: {errors.rest.join("; ")}</Alert>}
      {errors && errors.rest.length === 0 && <Alert tone="error">Chưa lưu được: sửa các ô báo lỗi ở trên.</Alert>}
      <div className="dpi-row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className={dirty ? "primary sm" : "sm"} disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? "Đang lưu…" : "Lưu bảng quy đổi"}
        </button>
      </div>
    </Card>
  );
}
