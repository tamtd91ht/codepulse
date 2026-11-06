"use client";

/**
 * Admin › Jira and DPI › Jira (docs/specs/27 §12, §15-16). One screen:
 * - a status card on top (is it working, last sync, projects, accounts, the DPI task source)
 *   with "Đồng bộ ngay";
 * - not configured yet: a numbered setup (1. connect, 2. projects, 3. task source), later steps
 *   muted until the connection works;
 * - configured: tabs "Dự án & đồng bộ" (the Jira projects, each feeding one or more CodePulse
 *   dự án by component / label, edited in a drawer with a live preview), "Kết nối" (read-only
 *   summary, "Chỉnh sửa" opens the form) and "Quy đổi trường".
 * Needs `jira.admin` (or admin); the API answers 403 otherwise.
 */
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { IconButton } from "@/components/icons";
import { Alert, Drawer, EmptyState, Loading, Message, Pill, Segmented } from "@/components/ui";
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

/** One CodePulse dự án a Jira project feeds (§15); no component and no label: the other issues. */
export interface JiraScope {
  business_project: { id: number; name: string };
  components: string[];
  labels: string[];
  /** Issues it gets, as stored. */
  issues: number;
}

export interface JiraProject {
  id: number;
  key: string;
  name: string | null;
  enabled: boolean;
  scopes: JiraScope[];
  /** Issues no rule matched, counted for every dự án of the project. */
  unmatched: number;
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
  sync: { last_at: string | null; failed: number; enabled: number; never: number };
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
const n = (v: number) => v.toLocaleString("vi-VN");

type Tab = "projects" | "connection" | "mapping";

export function JiraAdmin() {
  const { data, error, mutate } = useApi<JiraView>("/dpi/jira");
  const [message, setMessage] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("projects");
  if (error)
    return error.status === 403 ? (
      <Alert tone="info">Cần quyền &ldquo;Cấu hình tích hợp Jira&rdquo; (jira.admin): nhờ admin cấp ở Admin › Phân quyền.</Alert>
    ) : (
      <Loading error={error} />
    );
  if (!data) return <Loading rows={6} />;
  const reload = () => void mutate();
  const done = (m: string) => (setMessage(m), reload());
  const tabs: SubTab<Tab>[] = [
    { key: "projects", label: "Dự án & đồng bộ", count: data.projects.length },
    { key: "connection", label: "Kết nối" },
    { key: "mapping", label: "Quy đổi trường" },
  ];
  return (
    <div className="jira-admin">
      <Message text={message} onClose={() => setMessage(null)} />
      <StatusCard data={data} onDone={done} />
      {data.configured ? (
        <>
          <SubTabs id="jira" label="Cấu hình Jira" tabs={tabs} value={tab} onChange={setTab} />
          <SubPanel id="jira" tab={tab}>
            {tab === "projects" && <ProjectsPanel data={data} onDone={done} />}
            {tab === "connection" && <ConnectionPanel key={JSON.stringify(data.connection)} data={data} onDone={done} />}
            {tab === "mapping" && <MappingEditor key={JSON.stringify(data.mapping)} data={data} onDone={done} />}
          </SubPanel>
        </>
      ) : (
        <SetupSteps data={data} onDone={done} />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- status ---- */

type State = { tone: "good" | "bad" | "warn" | undefined; label: string };

function stateOf(data: JiraView): State {
  const c = data.connection;
  if (data.auth_failure) return { tone: "bad", label: "Tạm dừng: Jira từ chối đăng nhập" };
  if (!c.configured) return c.base_url && c.token_set && !c.enabled ? { tone: "warn", label: "Đang tắt" } : { tone: undefined, label: "Chưa cấu hình" };
  if (data.sync.failed) return { tone: "warn", label: `Hoạt động, ${data.sync.failed} dự án lỗi` };
  if (!data.sync.enabled) return { tone: "warn", label: "Chưa bật dự án nào" };
  return { tone: "good", label: "Đang hoạt động" };
}

function Fact({ label, children, sub }: { label: string; children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="jira-fact">
      <dt>{label}</dt>
      <dd>
        <div className="jira-fact-value">{children}</div>
        {sub && <div className="muted small">{sub}</div>}
      </dd>
    </div>
  );
}

function StatusCard({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const c = data.connection;
  const state = stateOf(data);
  const [syncing, setSyncing] = useState(false);
  const [switching, setSwitching] = useState(false);
  const issues = data.projects.reduce((sum, p) => sum + p.issues_count, 0);
  const sync = async () => {
    setSyncing(true);
    try {
      await api.post("/dpi/jira/sync");
      onDone("Đã đưa việc đồng bộ mọi dự án đang bật vào hàng đợi. Kết quả hiện ở đây sau ít phút.");
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    } finally {
      setSyncing(false);
    }
  };
  return (
    <section className="card jira-status" aria-labelledby="jira-status-title">
      <div className="jira-status-head">
        <div>
          <h2 id="jira-status-title">
            Tích hợp Jira <Pill tone={state.tone}>{state.label}</Pill>
          </h2>
          <p className="muted small">Đọc task từ Jira Data Center / Server cho DPI. Chỉ đọc, không ghi gì lên Jira.</p>
        </div>
        {data.configured && (
          <button type="button" className="sm" disabled={syncing || !data.sync.enabled} onClick={() => void sync()} title={data.sync.enabled ? "Đồng bộ mọi dự án đang bật" : "Bật ít nhất một dự án trước"}>
            {syncing ? "Đang gửi…" : "Đồng bộ ngay"}
          </button>
        )}
      </div>
      <dl className="jira-facts">
        <Fact label="Máy chủ" sub={c.base_url ? (c.auth_type === "basic" ? `Tài khoản ${c.username ?? "?"}` : "Personal Access Token") : undefined}>
          {c.base_url ? <span className="jira-url">{c.base_url}</span> : <span className="muted">Chưa có</span>}
        </Fact>
        <Fact
          label="Đồng bộ"
          sub={
            data.sync.failed ? (
              <span className="tone-bad">{data.sync.failed} dự án lỗi lần gần nhất</span>
            ) : data.sync.last_at ? (
              <span title={dateTime(data.sync.last_at)}>gần nhất {relative(data.sync.last_at)}</span>
            ) : (
              "chưa đồng bộ lần nào"
            )
          }
        >
          {c.configured ? `Mỗi ${c.interval_min} phút` : "—"}
        </Fact>
        <Fact label="Dự án Jira" sub={`${n(data.projects.length)} đã thêm · ${n(issues)} issue`}>
          {n(data.sync.enabled)} đang đồng bộ
        </Fact>
        <Fact label="Người được nối" sub="tài khoản Jira đã gắn người dùng CodePulse">
          {n(data.accounts.linked)} / {n(data.accounts.total)}
        </Fact>
        <Fact
          label="Nguồn task của DPI"
          sub={
            data.configured ? (
              <button type="button" className="link sm" onClick={() => setSwitching(true)}>
                Đổi nguồn
              </button>
            ) : (
              "đổi được sau khi kết nối"
            )
          }
        >
          {data.task_source === "jira" ? "Jira" : "GitLab Issues"}
        </Fact>
      </dl>
      {data.auth_failure && (
        <Alert tone="error">
          Lần đồng bộ {relative(data.auth_failure.at)} bị Jira từ chối đăng nhập: {data.auth_failure.error}. Đồng bộ tự động đang tạm dừng để không khoá tài khoản dịch vụ. Sửa thông tin
          đăng nhập ở tab Kết nối; nếu Jira đã bật CAPTCHA, đăng nhập web bằng tài khoản dịch vụ để giải CAPTCHA rồi bấm Kiểm tra kết nối.
        </Alert>
      )}
      {c.token_unreadable && (
        <Alert tone="warning">
          {c.auth_type === "basic" ? "Mật khẩu" : "Token"} đã lưu không giải mã được (APP_ENCRYPTION_KEY đã đổi?). Nhập lại ở tab Kết nối
          {c.env.token ? "; trong lúc đó dùng JIRA_TOKEN nếu cùng máy chủ" : ""}.
        </Alert>
      )}
      {switching && <SourceSwitch current={data.task_source} onClose={() => setSwitching(false)} onDone={onDone} />}
    </section>
  );
}

function SourceSwitch({ current, onClose, onDone }: { current: "gitlab" | "jira"; onClose: () => void; onDone: (msg: string) => void }) {
  const next = current === "jira" ? "gitlab" : "jira";
  const [busy, setBusy] = useState(false);
  const apply = async () => {
    setBusy(true);
    try {
      await api.put("/dpi/jira/source", { source: next });
      onDone(next === "jira" ? "DPI đã chuyển sang đọc task từ Jira." : "DPI đã chuyển sang đọc task từ GitLab Issues.");
      onClose();
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Alert tone="warning">
      Chuyển nguồn task của DPI sang <strong>{next === "jira" ? "Jira" : "GitLab Issues"}</strong>? DPI chỉ đọc issue của một nguồn nên không tính trùng.{" "}
      {next === "jira"
        ? "Collector GitLab ngừng lấy issue; dữ liệu GitLab Issues cũ vẫn giữ."
        : "Jira vẫn đồng bộ nền để dữ liệu sẵn sàng khi chuyển lại; GitLab lấy lại issue của mọi repo."}{" "}
      Scorecard các tháng đã tính không đổi cho tới khi chạy lại dữ liệu DPI.
      <div className="jira-inline-actions">
        <button type="button" className="ghost sm" onClick={onClose} disabled={busy}>
          Huỷ
        </button>
        <button type="button" className="primary ink sm" onClick={() => void apply()} disabled={busy}>
          {busy ? "Đang chuyển…" : "Chuyển nguồn"}
        </button>
      </div>
    </Alert>
  );
}

/* ------------------------------------------------------------ setup steps ---- */

function SetupSteps({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const c = data.connection;
  const off = Boolean(c.base_url && c.token_set && !c.enabled);
  const [busy, setBusy] = useState(false);
  const turnOn = async () => {
    setBusy(true);
    try {
      await api.put("/dpi/jira/connection", { enabled: true });
      onDone("Đã bật lại tích hợp Jira.");
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <ol className="jira-steps" aria-label="Các bước thiết lập Jira">
      <li className="jira-step active">
        <div className="jira-step-head">
          <span className="jira-step-no" aria-hidden="true">
            1
          </span>
          <div>
            <h3>Kết nối máy chủ Jira</h3>
            <p className="muted small">Một tài khoản dịch vụ chỉ có quyền Browse projects. Jira 8.14 trở lên dùng Personal Access Token; cũ hơn dùng tài khoản + mật khẩu.</p>
          </div>
        </div>
        {off ? (
          <Alert tone="warning">
            Tích hợp Jira đang tắt: kết nối vẫn giữ, chỉ ngừng đồng bộ.{" "}
            <button type="button" className="primary ink sm" disabled={busy} onClick={() => void turnOn()}>
              {busy ? "Đang bật…" : "Bật lại"}
            </button>
          </Alert>
        ) : (
          <ConnectionForm key={JSON.stringify(c)} c={c} submitLabel="Lưu kết nối" onSaved={() => onDone("Đã kết nối Jira. Bước tiếp theo: thêm dự án Jira ở tab Dự án & đồng bộ.")} />
        )}
      </li>
      <li className="jira-step muted-step" aria-disabled="true">
        <div className="jira-step-head">
          <span className="jira-step-no" aria-hidden="true">
            2
          </span>
          <div>
            <h3>Thêm dự án Jira và gắn dự án CodePulse</h3>
            <p className="muted small">Mỗi dự án Jira gắn với một hoặc nhiều dự án CodePulse (ví dụ dự án Jira chung tách FE và BE theo component).</p>
          </div>
        </div>
      </li>
      <li className="jira-step muted-step" aria-disabled="true">
        <div className="jira-step-head">
          <span className="jira-step-no" aria-hidden="true">
            3
          </span>
          <div>
            <h3>Dùng Jira làm nguồn task của DPI</h3>
            <p className="muted small">Khi dữ liệu đã đồng bộ, đổi nguồn task ở thẻ trạng thái phía trên.</p>
          </div>
        </div>
      </li>
    </ol>
  );
}

/* ------------------------------------------------------------- connection ---- */

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
function From({ source }: { source: ValueSource }) {
  if (source === "web") return <Pill tone="primary">đã lưu trên web</Pill>;
  if (source === "env") return <Pill>biến môi trường</Pill>;
  return null;
}

function FieldError({ text }: { text?: string }) {
  return text ? <div className="tone-bad small">{text}</div> : null;
}

function TestOutcome({ result, error, unsaved, authType }: { result: TestResult | null; error: string | null; unsaved: boolean; authType: AuthType }) {
  if (error) return <Alert tone="error">Kết nối lỗi: {error}</Alert>;
  if (!result) return null;
  const field = (label: string, f: FieldRef | null) => (
    <li>
      {label}:{" "}
      {f ? (
        <>
          <strong>{f.name}</strong> <code className="small">{f.id}</code>
        </>
      ) : (
        <span className="tone-warn">không tìm thấy (sửa tên ở tab Quy đổi trường)</span>
      )}
    </li>
  );
  return (
    <>
      {result.pat_supported === false && authType === "pat" && (
        <Alert tone="warning">Jira {result.version} chưa có Personal Access Token (có từ 8.14): chọn &ldquo;Tài khoản + mật khẩu&rdquo; và nhập tài khoản dịch vụ.</Alert>
      )}
      <Alert tone="success">
        <div>
          Kết nối được: {result.server_title ?? "Jira"} · phiên bản {result.version ?? "—"}
          {result.deployment_type ? ` (${result.deployment_type})` : ""}
          {result.user ? ` · đăng nhập là ${result.user}` : ""}
          {unsaved ? " (giá trị chưa lưu)" : ""}
        </div>
        <ul className="small jira-test-fields">
          {field("Story Points", result.fields.story_points)}
          {field("Sprint", result.fields.sprint)}
          {field("Flagged", result.fields.flagged)}
        </ul>
      </Alert>
    </>
  );
}

/** The connection form: the login first, the rest under "Nâng cao". */
function ConnectionForm({ c, submitLabel, onSaved, onCancel }: { c: JiraConnection; submitLabel: string; onSaved: () => void; onCancel?: () => void }) {
  const [baseUrl, setBaseUrl] = useState(c.base_url ?? "");
  const [authType, setAuthType] = useState<AuthType>(c.auth_type);
  const [username, setUsername] = useState(c.username ?? "");
  const [token, setToken] = useState("");
  const [caPem, setCaPem] = useState("");
  const [intervalMin, setIntervalMin] = useState(String(c.interval_min));
  const [backfill, setBackfill] = useState(String(c.backfill_days));
  const [timeoutS, setTimeoutS] = useState(String(c.timeout_s));
  const [result, setResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [error, setError] = useState<{ key: ConnKey | null; text: string } | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const advancedErr = error && ["ca_pem", "interval_min", "backfill_days", "timeout_s"].includes(error.key ?? "");

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
  if (num(intervalMin) !== c.interval_min) changes.interval_min = num(intervalMin);
  if (num(backfill) !== c.backfill_days) changes.backfill_days = num(backfill);
  if (num(timeoutS) !== c.timeout_s) changes.timeout_s = num(timeoutS);
  // The first save of a connection switches the integration on.
  if (!c.configured && !c.enabled) changes.enabled = true;
  const dirty = Object.keys(changes).length > 0;
  const canTest = Boolean((trimmedUrl || c.base_url) && (secret || c.token_set) && (!basic || trimmedUser));
  const secretName = basic ? "mật khẩu" : "token";

  const save = async () => {
    setBusy("save");
    setError(null);
    try {
      await api.put("/dpi/jira/connection", changes);
      onSaved();
    } catch (e) {
      const text = errText(e);
      setError(e instanceof ApiError && e.status === 422 ? connError(text) : { key: null, text });
    } finally {
      setBusy(null);
    }
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
  const tokenState =
    c.token_source === "web"
      ? `Đã lưu${c.token_updated_at ? ` ${relative(c.token_updated_at)}` : ""}: để trống là giữ nguyên.`
      : c.token_source === "env"
        ? "Đang dùng JIRA_TOKEN: nhập để thay bằng giá trị lưu trên web."
        : c.token_other_server
          ? `${secretName === "token" ? "Token" : "Mật khẩu"} hiện có thuộc máy chủ khác, không được gửi sang đây: nhập mới.`
          : "Được mã hoá khi lưu và không bao giờ hiện lại.";
  const cleartext = /^http:\/\//i.test(trimmedUrl);
  const number = (label: string, value: string, set: (v: string) => void, key: "interval_min" | "backfill_days" | "timeout_s", min: number, max: number, hint: string) => (
    <label className="jira-field">
      <span>
        {label} <From source={c.sources[key] === "web" ? "web" : null} />
      </span>
      <input type="number" inputMode="numeric" min={min} max={max} step={1} value={value} onChange={(e) => set(e.target.value)} aria-invalid={!!fe(key)} />
      <span className="muted small">{hint}</span>
      <FieldError text={fe(key)} />
    </label>
  );

  return (
    <form
      className="jira-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty && !busy) void save();
      }}
    >
      <label className="jira-field">
        <span>
          URL Jira <span className="req">*</span> <From source={c.base_url_source} />
        </span>
        <input type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://jira.congty.vn" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} aria-invalid={!!fe("base_url")} />
        <span className="muted small">Địa chỉ gốc, có thể kèm context path (ví dụ /jira).</span>
        {cleartext &&
          (basic ? (
            <span className="tone-bad small">http:// không mã hoá: tài khoản và mật khẩu đi qua mạng dạng rõ. Chỉ dùng https://.</span>
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
        <span className="muted small">Xem phiên bản ở &lt;URL Jira&gt;/rest/api/2/serverInfo, hoặc bấm Kiểm tra kết nối.</span>
        <FieldError text={fe("auth_type")} />
      </div>
      <div className="jira-grid">
        {basic && (
          <label className="jira-field">
            <span>
              Username <span className="req">*</span> <From source={c.username_source} />
            </span>
            <input type="text" autoComplete="off" spellCheck={false} placeholder="codepulse-bot" value={username} onChange={(e) => setUsername(e.target.value)} aria-invalid={!!fe("username")} />
            <FieldError text={fe("username")} />
          </label>
        )}
        <label className="jira-field">
          <span>
            {basic ? "Mật khẩu" : "Personal Access Token"} {!c.token_set && <span className="req">*</span>} <From source={c.token_source} />
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
            {tokenState}
          </span>
          <FieldError text={fe("token")} />
        </label>
      </div>
      <details className="jira-advanced" open={Boolean(advancedErr) || undefined}>
        <summary>Nâng cao: CA nội bộ, chu kỳ, số ngày lấy lùi, timeout</summary>
        <label className="jira-field jira-wide">
          <span>
            CA nội bộ (PEM) <From source={c.ca_source} />
          </span>
          <textarea
            rows={4}
            spellCheck={false}
            placeholder={c.ca_source === "web" ? "Đã lưu một CA. Dán CA khác để thay, để trống: giữ nguyên." : "-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----"}
            value={caPem}
            onChange={(e) => setCaPem(e.target.value)}
            aria-invalid={!!fe("ca_pem")}
          />
          <span className="muted small">Chỉ cần khi Jira dùng chứng chỉ do CA nội bộ cấp. Không có: dùng {c.env.ca_bundle ? "JIRA_CA_BUNDLE" : "CA của hệ thống"}.</span>
          <FieldError text={fe("ca_pem")} />
        </label>
        <div className="jira-grid">
          {number("Chu kỳ đồng bộ (phút)", intervalMin, setIntervalMin, "interval_min", 1, 1440, "1–1440, mặc định 15.")}
          {number("Số ngày lấy lùi lần đầu", backfill, setBackfill, "backfill_days", 1, 3650, "Dự án mới lấy issue cập nhật trong N ngày.")}
          {number("Timeout (giây)", timeoutS, setTimeoutS, "timeout_s", 1, 300, "Cho mỗi lời gọi Jira, 1–300.")}
        </div>
      </details>
      {error && error.key === null && <Alert tone="error">Chưa lưu được: {error.text}</Alert>}
      <TestOutcome result={result} error={testError} unsaved={dirty} authType={authType} />
      <div className="jira-form-actions">
        {onCancel && (
          <button type="button" className="ghost sm" disabled={!!busy} onClick={onCancel}>
            Huỷ
          </button>
        )}
        <span className="dpi-spacer" />
        <button type="button" className="sm" disabled={!!busy || !canTest} onClick={() => void test()} title={canTest ? "Thử giá trị đang nhập, chưa lưu" : basic ? "Nhập URL, username và mật khẩu trước" : "Nhập URL và token trước"}>
          {busy === "test" ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
        </button>
        <button type="submit" className="primary ink sm" disabled={!dirty || !!busy}>
          {busy === "save" ? "Đang lưu…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

/** Tab "Kết nối": what is in effect, read-only; "Chỉnh sửa" opens the form. */
function ConnectionPanel({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const c = data.connection;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<"test" | "toggle" | "reset" | "clear" | null>(null);
  const [result, setResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const basic = c.auth_type === "basic";
  const secretName = basic ? "mật khẩu" : "token";
  const fromWeb =
    c.base_url_source === "web" || c.auth_type_source === "web" || c.username_source === "web" || c.token_source === "web" || c.ca_source === "web" || Object.values(c.sources).includes("web");
  const act = async (kind: "toggle" | "reset" | "clear", work: () => Promise<unknown>, msg: string) => {
    setBusy(kind);
    try {
      await work();
      onDone(msg);
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    } finally {
      setBusy(null);
    }
  };
  const test = async () => {
    setBusy("test");
    setResult(null);
    setTestError(null);
    try {
      setResult(await api.post<TestResult>("/dpi/jira/test"));
    } catch (e) {
      setTestError(errText(e));
    } finally {
      setBusy(null);
    }
  };
  const toggle = () => {
    if (c.enabled && !window.confirm("Tắt tích hợp Jira? Đồng bộ dừng lại; dữ liệu đã lấy vẫn giữ.")) return;
    void act("toggle", () => api.put("/dpi/jira/connection", { enabled: !c.enabled }), c.enabled ? "Đã tắt tích hợp Jira." : "Đã bật tích hợp Jira.");
  };
  const reset = () => {
    if (!window.confirm("Xoá mọi giá trị kết nối Jira đã lưu trên web (URL, cách đăng nhập, token / mật khẩu, CA, chu kỳ…) và dùng lại biến môi trường JIRA_*?")) return;
    void act("reset", () => api.delete("/dpi/jira/connection"), "Đã xoá cấu hình Jira trên web, đang dùng biến môi trường.");
  };
  const clearSecret = () => {
    if (!window.confirm(c.env.token ? `Xoá ${secretName} đã lưu trên web? Sau đó dùng JIRA_TOKEN.` : `Xoá ${secretName} đã lưu trên web? Jira ngừng đồng bộ cho tới khi nhập ${secretName} mới.`)) return;
    void act("clear", () => api.put("/dpi/jira/connection", { token: "" }), `Đã xoá ${secretName} đã lưu.`);
  };

  if (editing)
    return (
      <section className="card">
        <div className="card-head">
          <div className="card-title">
            <h2>Sửa kết nối</h2>
            <div className="sub">Bấm Kiểm tra kết nối để thử giá trị đang nhập trước khi lưu.</div>
          </div>
        </div>
        <ConnectionForm c={c} submitLabel="Lưu" onCancel={() => setEditing(false)} onSaved={() => (setEditing(false), onDone("Đã lưu kết nối Jira. Lịch đồng bộ dùng cấu hình mới trong vòng một phút."))} />
      </section>
    );

  const row = (label: string, value: React.ReactNode, source?: ValueSource) => (
    <div className="jira-kv">
      <dt>{label}</dt>
      <dd>
        {value} {source !== undefined && <From source={source} />}
      </dd>
    </div>
  );
  return (
    <section className="card">
      <div className="card-head">
        <div className="card-title">
          <h2>Kết nối</h2>
          <div className="sub">Giá trị lưu trên web được ưu tiên; ô không lưu dùng biến môi trường JIRA_*.</div>
        </div>
        <div className="actions">
          <button type="button" className="sm" disabled={!!busy} onClick={() => void test()}>
            {busy === "test" ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
          </button>
          <button type="button" className="sm" disabled={!!busy} onClick={() => setEditing(true)}>
            Chỉnh sửa
          </button>
        </div>
      </div>
      <dl className="jira-kvs">
        {row("URL Jira", c.base_url ?? "—", c.base_url_source)}
        {row("Cách đăng nhập", basic ? `Tài khoản + mật khẩu (${c.username ?? "chưa có username"})` : "Personal Access Token", c.auth_type_source === "web" ? "web" : c.auth_type_source)}
        {row(
          basic ? "Mật khẩu" : "Token",
          c.token_set ? (
            <>
              Đã có{c.token_updated_at && c.token_source === "web" ? `, cập nhật ${relative(c.token_updated_at)}` : ""}
              {c.token_source === "web" && (
                <>
                  {" · "}
                  <button type="button" className="link sm" disabled={!!busy} onClick={clearSecret}>
                    Xoá {secretName} đã lưu
                  </button>
                </>
              )}
            </>
          ) : (
            <span className="tone-bad">Chưa có</span>
          ),
          c.token_source,
        )}
        {row("CA nội bộ", c.ca_set ? "Đã có" : c.env.ca_bundle ? "JIRA_CA_BUNDLE" : "CA của hệ thống", c.ca_source)}
        {row("Đồng bộ", `Mỗi ${c.interval_min} phút · lần đầu lấy lùi ${c.backfill_days} ngày · timeout ${c.timeout_s} giây`)}
        <div className="jira-kv">
          <dt>Tích hợp</dt>
          <dd>
            <label className="jira-switch">
              <input type="checkbox" role="switch" checked={c.enabled} disabled={!!busy} onChange={toggle} /> {c.enabled ? "Đang bật" : "Đang tắt"}
            </label>
            <span className="muted small"> Tắt: ngừng đồng bộ, dữ liệu đã lấy vẫn giữ.</span>
          </dd>
        </div>
      </dl>
      <TestOutcome result={result} error={testError} unsaved={false} authType={c.auth_type} />
      {fromWeb && (
        <div className="jira-form-actions">
          <button type="button" className="link sm" disabled={!!busy} onClick={reset}>
            {busy === "reset" ? "Đang xoá…" : "Dùng lại biến môi trường"}
          </button>
        </div>
      )}
    </section>
  );
}

/* --------------------------------------------------------------- projects ---- */

function syncState(p: JiraProject) {
  if (!p.enabled) return <span className="muted">Không đồng bộ</span>;
  if (p.last_error)
    return (
      <span className="tone-bad jira-error" title={p.last_error}>
        Lỗi{p.last_synced_at ? ` ${relative(p.last_synced_at)}` : ""}: {p.last_error}
      </span>
    );
  if (!p.synced_until) return <span className="tone-warn">Chờ đồng bộ lần đầu</span>;
  return p.last_synced_at ? <span title={dateTime(p.last_synced_at)}>{relative(p.last_synced_at)}</span> : <span className="muted">—</span>;
}

/** "component Frontend, Web · label api", or what a scope without rules gets. */
function ruleText(s: { components: string[]; labels: string[] }, ruled: boolean) {
  const parts = [s.components.length ? `component ${s.components.join(", ")}` : "", s.labels.length ? `label ${s.labels.join(", ")}` : ""].filter(Boolean);
  if (parts.length) return parts.join(" · ");
  return ruled ? "các issue còn lại" : "mọi issue";
}

function ProjectsPanel({ data, onDone }: { data: JiraView; onDone: (msg: string) => void }) {
  const [editing, setEditing] = useState<JiraProject | null>(null);
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const q = query.trim().toLowerCase();
  const rows = data.projects.filter((p) => !q || p.key.toLowerCase().includes(q) || (p.name ?? "").toLowerCase().includes(q) || p.scopes.some((s) => s.business_project.name.toLowerCase().includes(q)));
  const syncOne = async (p: JiraProject) => {
    setBusyKey(p.key);
    try {
      await api.post("/dpi/jira/sync", { keys: [p.key] });
      onDone(`Đã đưa việc đồng bộ ${p.key} vào hàng đợi.`);
    } catch (e) {
      onDone(`Lỗi: ${errText(e)}`);
    } finally {
      setBusyKey(null);
    }
  };
  return (
    <section className="card flush">
      <div className="jira-toolbar">
        <h2>
          Dự án Jira <span className="tab-count">{n(data.projects.length)}</span>
        </h2>
        {data.projects.length > 5 && <input type="search" className="jira-search" placeholder="Tìm theo key, tên, dự án CodePulse" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Tìm dự án Jira" />}
        <span className="dpi-spacer" />
        <button type="button" className="primary sm" onClick={() => setAdding(true)}>
          Thêm dự án
        </button>
      </div>
      {data.projects.length === 0 ? (
        <EmptyState icon="folder" title="Chưa có dự án Jira nào">
          Bấm &ldquo;Thêm dự án&rdquo;, chọn dự án trên Jira và gắn nó với dự án CodePulse. Một dự án Jira chung có thể tách cho nhiều dự án CodePulse (ví dụ FE và BE) theo component hoặc label.
        </EmptyState>
      ) : !rows.length ? (
        <EmptyState title="Không có dự án khớp">Thử từ khoá khác.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table jira-projects">
            <thead>
              <tr>
                <th>Dự án Jira</th>
                <th>Tính cho dự án CodePulse</th>
                <th>Đồng bộ</th>
                <th className="num">Issue</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const ruled = p.scopes.some((s) => s.components.length || s.labels.length);
                return (
                  <tr key={p.key} className={p.enabled ? undefined : "jira-off"}>
                    <td className="jira-key">
                      <code>{p.key}</code> {!p.enabled && <Pill>đang tắt</Pill>}
                      {p.name && <div className="muted small">{p.name}</div>}
                    </td>
                    <td>
                      {p.scopes.length === 0 ? (
                        <span className="tone-warn small">Chưa gắn: chỉ admin thấy issue của dự án này</span>
                      ) : (
                        <ul className="jira-scope-list">
                          {p.scopes.map((s) => (
                            <li key={s.business_project.id}>
                              <strong>{s.business_project.name}</strong> <span className="muted small">{ruleText(s, ruled)}</span>
                              <span className="muted small"> · {n(s.issues)} issue</span>
                            </li>
                          ))}
                        </ul>
                      )}
                      {p.unmatched > 0 && <div className="tone-warn small">{n(p.unmatched)} issue không khớp quy tắc nào: tính cho mọi dự án ở trên.</div>}
                    </td>
                    <td className="small">{syncState(p)}</td>
                    <td className="num">{n(p.issues_count)}</td>
                    <td className="jira-row-actions">
                      <IconButton icon="sync" label={`Đồng bộ ${p.key}`} disabled={!p.enabled || busyKey === p.key} onClick={() => void syncOne(p)} />
                      <IconButton icon="edit" label={`Sửa ${p.key}`} onClick={() => setEditing(p)} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {adding && (
        <AddProject
          registered={data.projects.map((p) => p.key)}
          onClose={() => setAdding(false)}
          onPick={(key, name) => {
            setAdding(false);
            setEditing({ id: 0, key, name, enabled: true, scopes: [], unmatched: 0, last_synced_at: null, synced_until: null, last_error: null, issues_count: 0 });
          }}
        />
      )}
      {editing && <ProjectEditor project={editing} isNew={editing.id === 0} onClose={() => setEditing(null)} onDone={(m) => (setEditing(null), onDone(m))} />}
    </section>
  );
}

function AddProject({ registered, onClose, onPick }: { registered: string[]; onClose: () => void; onPick: (key: string, name: string | null) => void }) {
  const remote = useApi<{ items: { key: string; name: string | null }[] }>("/dpi/jira/remote-projects");
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const items = (remote.data?.items ?? []).filter((r) => !registered.includes(r.key) && (!q || r.key.toLowerCase().includes(q) || (r.name ?? "").toLowerCase().includes(q)));
  return (
    <Drawer title="Thêm dự án Jira" width={520} onClose={onClose}>
      <p className="muted small">Chọn một dự án mà tài khoản dịch vụ xem được. Bước sau: gắn nó với dự án CodePulse.</p>
      <input type="search" className="jira-search wide" placeholder="Tìm theo key hoặc tên" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Tìm dự án trên Jira" autoFocus />
      {remote.error ? (
        <Alert tone="error">
          Không lấy được danh sách dự án: {remote.error.message}{" "}
          <button type="button" className="link sm" onClick={() => void remote.mutate()}>
            Thử lại
          </button>
        </Alert>
      ) : !remote.data ? (
        <Loading rows={5} />
      ) : !items.length ? (
        <EmptyState title={q ? "Không có dự án khớp" : "Đã thêm hết dự án trên Jira"}>{q ? "Thử từ khoá khác." : "Mọi dự án tài khoản dịch vụ xem được đều đã có trong danh sách."}</EmptyState>
      ) : (
        <ul className="jira-pick-list">
          {items.map((r) => (
            <li key={r.key}>
              <button type="button" onClick={() => onPick(r.key, r.name)}>
                <code>{r.key}</code>
                <span>{r.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Drawer>
  );
}

/** One scope on the editor: a dự án, and either every (other) issue or a rule. */
interface ScopeDraft {
  uid: number;
  business_project_id: number | null;
  mode: "all" | "rule";
  components: string[];
  labels: string[];
}

interface Preview {
  total: number;
  per_business_project: Record<string, number>;
  unmatched: number;
  components_missing: boolean;
}

let uidSeq = 1;
const toDraft = (s: JiraScope): ScopeDraft => ({
  uid: uidSeq++,
  business_project_id: s.business_project.id,
  mode: s.components.length || s.labels.length ? "rule" : "all",
  components: s.components,
  labels: s.labels,
});

function ProjectEditor({ project, isNew, onClose, onDone }: { project: JiraProject; isNew: boolean; onClose: () => void; onDone: (msg: string) => void }) {
  const businessProjects = useApi<BusinessProject[]>("/business-projects");
  const options = useApi<{ components: string[]; components_source: "jira" | "local"; labels: string[] }>(`/dpi/jira/projects/${project.key}/options`);
  const [enabled, setEnabled] = useState(project.enabled);
  const [scopes, setScopes] = useState<ScopeDraft[]>(() => project.scopes.map(toDraft));
  const [preview, setPreview] = useState<Preview | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resyncing, setResyncing] = useState(false);

  // A rule without any value means nothing: it is saved as a catch-all only if the admin says so.
  const payload = scopes
    .filter((s) => s.business_project_id !== null)
    .map((s) => ({
      business_project_id: s.business_project_id as number,
      components: s.mode === "rule" ? s.components : [],
      labels: s.mode === "rule" ? s.labels : [],
    }));
  const key = JSON.stringify(payload);
  useEffect(() => {
    if (isNew) return;
    const timer = window.setTimeout(() => {
      api
        .post<Preview>(`/dpi/jira/projects/${project.key}/preview`, { scopes: JSON.parse(key) })
        .then(setPreview)
        .catch(() => setPreview(null));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [key, isNew, project.key]);

  const ruled = payload.some((s) => s.components.length || s.labels.length);
  const catchAll = payload.some((s) => !s.components.length && !s.labels.length);
  const emptyRule = scopes.some((s) => s.mode === "rule" && !s.components.length && !s.labels.length);
  const unpicked = scopes.some((s) => s.business_project_id === null);
  const set = (uid: number, patch: Partial<ScopeDraft>) => setScopes(scopes.map((s) => (s.uid === uid ? { ...s, ...patch } : s)));
  const usedIds = new Set(scopes.map((s) => s.business_project_id));
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await api.put<{ refetch: string[]; refetch_queued: boolean }>("/dpi/jira/projects", {
        items: [{ key: project.key, name: project.name, enabled, scopes: payload }],
      });
      const refetched = res.refetch.includes(project.key);
      onDone(
        isNew
          ? `Đã thêm ${project.key}. ${enabled ? "Issue sẽ có sau lần đồng bộ tới (hoặc bấm Đồng bộ trên dòng của nó)." : ""}`
          : refetched
            ? `Đã lưu ${project.key}. Issue cũ chưa có component nên dự án được đồng bộ lại từ đầu${res.refetch_queued ? " (đã vào hàng đợi)" : ""}.`
            : `Đã lưu ${project.key}; issue đã có được chia lại theo cấu hình mới.`,
      );
    } catch (e) {
      setError(errText(e));
    } finally {
      setSaving(false);
    }
  };
  const resync = async () => {
    if (!window.confirm(`Đồng bộ lại ${project.key} từ đầu? Lấy lại mọi issue cập nhật trong khoảng lấy lùi; dữ liệu hiện có được ghi đè, không mất.`)) return;
    setResyncing(true);
    try {
      await api.post("/dpi/jira/sync", { keys: [project.key], full: true });
      onDone(`Đã đưa việc đồng bộ lại ${project.key} từ đầu vào hàng đợi.`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setResyncing(false);
    }
  };
  const bpName = (id: number | null) => businessProjects.data?.find((b) => b.id === id)?.name ?? "";
  const countFor = (id: number | null) => (preview && id !== null ? (preview.per_business_project[String(id)] ?? 0) : null);

  return (
    <Drawer
      title={
        <>
          {isNew ? "Thêm" : "Sửa"} <code>{project.key}</code> {project.name && <span className="muted">· {project.name}</span>}
        </>
      }
      width={680}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="ghost sm" onClick={onClose} disabled={saving}>
            Huỷ
          </button>
          <button type="button" className="primary ink sm" disabled={saving || emptyRule || unpicked} onClick={() => void save()} title={emptyRule ? "Dòng theo component / label cần ít nhất một giá trị" : unpicked ? "Chọn dự án CodePulse cho mọi dòng" : undefined}>
            {saving ? "Đang lưu…" : isNew ? "Thêm dự án" : "Lưu"}
          </button>
        </>
      }
    >
      <div className="jira-editor">
        <label className="jira-switch-row">
          <input type="checkbox" role="switch" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>
            <strong>Đồng bộ dự án này</strong>
            <span className="muted small"> Tắt: ngừng lấy issue mới và ngừng nối key với MR; issue đã có vẫn giữ.</span>
          </span>
        </label>

        <h3>Tính cho dự án CodePulse</h3>
        <p className="muted small">
          Issue của dự án Jira này được tính vào báo cáo, phân quyền và lỗi production của các dự án CodePulse dưới đây. Dự án Jira chung cho nhiều đội (ví dụ FE và BE) thì thêm nhiều dòng và
          chọn component hoặc label cho từng dòng.
        </p>
        {businessProjects.error && <Alert tone="error">Không tải được dự án CodePulse: {businessProjects.error.message}</Alert>}
        {scopes.length === 0 ? (
          <div className="jira-scope-empty muted small">Chưa gắn dự án CodePulse nào: chỉ admin thấy issue của dự án này trong báo cáo.</div>
        ) : (
          <ol className="jira-scopes">
            {scopes.map((s, i) => {
              const count = countFor(s.business_project_id);
              return (
                <li key={s.uid} className="jira-scope">
                  <div className="jira-scope-head">
                    <select
                      value={s.business_project_id ?? ""}
                      aria-label={`Dự án CodePulse dòng ${i + 1}`}
                      onChange={(e) => set(s.uid, { business_project_id: e.target.value ? Number(e.target.value) : null })}
                    >
                      <option value="">Chọn dự án CodePulse…</option>
                      {(businessProjects.data ?? []).map((b) => (
                        <option key={b.id} value={b.id} disabled={b.id !== s.business_project_id && usedIds.has(b.id)}>
                          {b.name}
                        </option>
                      ))}
                    </select>
                    {count !== null && <span className="muted small">≈ {n(count)} issue</span>}
                    <span className="dpi-spacer" />
                    <IconButton icon="remove" label={`Bỏ dòng ${bpName(s.business_project_id) || i + 1}`} onClick={() => setScopes(scopes.filter((x) => x.uid !== s.uid))} />
                  </div>
                  <Segmented<"all" | "rule">
                    value={s.mode}
                    onChange={(mode) => set(s.uid, { mode })}
                    label={`Phạm vi dòng ${i + 1}`}
                    options={[
                      { value: "all", label: ruled && scopes.length > 1 ? "Các issue còn lại" : "Mọi issue" },
                      { value: "rule", label: "Theo component / label" },
                    ]}
                  />
                  {s.mode === "rule" && (
                    <div className="jira-grid">
                      <ChipInput
                        label="Component"
                        values={s.components}
                        onChange={(components) => set(s.uid, { components })}
                        suggestions={options.data?.components ?? []}
                        hint={options.data?.components_source === "local" ? "Gợi ý từ issue đã lưu (không gọi được Jira)." : "Gợi ý từ Jira."}
                      />
                      <ChipInput label="Label" values={s.labels} onChange={(labels) => set(s.uid, { labels })} suggestions={options.data?.labels ?? []} hint="Gợi ý từ issue đã lưu." />
                    </div>
                  )}
                  {s.mode === "rule" && !s.components.length && !s.labels.length && <div className="tone-warn small">Nhập ít nhất một component hoặc label.</div>}
                </li>
              );
            })}
          </ol>
        )}
        <button
          type="button"
          className="link sm"
          disabled={scopes.length >= 20}
          onClick={() => setScopes([...scopes, { uid: uidSeq++, business_project_id: null, mode: scopes.length ? "rule" : "all", components: [], labels: [] }])}
        >
          + Thêm dự án CodePulse
        </button>

        {ruled && (
          <Alert tone="info">
            Issue khớp nhiều dòng thì tính cho cả các dự án đó. Issue không khớp quy tắc nào
            {catchAll ? " tính cho dòng “Các issue còn lại”." : " tính cho mọi dự án ở trên (thêm một dòng “Các issue còn lại” để dồn về một dự án)."}
            {preview && preview.unmatched > 0 && ` Hiện có ${n(preview.unmatched)} / ${n(preview.total)} issue như vậy.`}
          </Alert>
        )}
        {preview?.components_missing && payload.some((s) => s.components.length) && (
          <Alert tone="warning">Một số issue đã lưu từ trước chưa có component. Sau khi lưu, dự án được đồng bộ lại từ đầu để lấy component; số issue ở trên lúc này có thể chưa đúng.</Alert>
        )}
        {error && <Alert tone="error">Chưa lưu được: {error}</Alert>}

        {!isNew && (
          <div className="jira-danger">
            <div>
              <strong>Đồng bộ lại từ đầu</strong>
              <div className="muted small">Lấy lại mọi issue trong khoảng lấy lùi, ví dụ sau khi đổi quy đổi trường. Lần đồng bộ gần nhất: {project.last_synced_at ? relative(project.last_synced_at) : "chưa có"}.</div>
            </div>
            <button type="button" className="sm" disabled={resyncing || !project.enabled} onClick={() => void resync()}>
              {resyncing ? "Đang gửi…" : "Đồng bộ lại"}
            </button>
          </div>
        )}
      </div>
    </Drawer>
  );
}

/** Values as chips: Enter or comma adds, Backspace on an empty box removes the last one. */
function ChipInput({ label, values, onChange, suggestions, hint }: { label: string; values: string[]; onChange: (v: string[]) => void; suggestions: string[]; hint?: string }) {
  const [text, setText] = useState("");
  const id = useRef(`chip-${Math.random().toString(36).slice(2)}`).current;
  const add = (raw: string) => {
    const value = raw.trim();
    if (!value || values.some((v) => v.toLowerCase() === value.toLowerCase())) return setText("");
    onChange([...values, value]);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(text);
    } else if (e.key === "Backspace" && !text && values.length) {
      onChange(values.slice(0, -1));
    }
  };
  const left = useMemo(() => suggestions.filter((s) => !values.some((v) => v.toLowerCase() === s.toLowerCase())), [suggestions, values]);
  return (
    <div className="jira-field">
      <label htmlFor={id}>{label}</label>
      <div className="jira-chips">
        {values.map((v) => (
          <span key={v} className="jira-chip">
            {v}
            <button type="button" aria-label={`Bỏ ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
              ×
            </button>
          </span>
        ))}
        <input id={id} list={`${id}-list`} value={text} placeholder={values.length ? "" : "Gõ rồi Enter"} onChange={(e) => (e.target.value.endsWith(",") ? add(e.target.value.slice(0, -1)) : setText(e.target.value))} onKeyDown={onKey} onBlur={() => text && add(text)} />
        <datalist id={`${id}-list`}>
          {left.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      </div>
      {hint && <span className="muted small">{hint}</span>}
    </div>
  );
}

/* ---------------------------------------------------------------- mapping ---- */

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

function PairTable({ rows, onChange, left, right, options, addLabel }: { rows: Pair[]; onChange: (rows: Pair[]) => void; left: string; right: string; options: string[]; addLabel: string }) {
  return (
    <>
      <table className="table compact jira-pairs">
        <thead>
          <tr>
            <th>{left}</th>
            <th>{right}</th>
            <th aria-label="Xoá" />
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
                <IconButton icon="remove" label={`Xoá ${k || `dòng ${i + 1}`}`} onClick={() => onChange(rows.filter((_, j) => j !== i))} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="link sm" onClick={() => onChange([...rows, ["", options[0] ?? ""]])}>
        + {addLabel}
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
      onDone("Đã lưu quy đổi trường. Áp dụng từ lần đồng bộ sau; muốn áp cho issue cũ thì Đồng bộ lại từ đầu ở từng dự án.");
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setErrors(splitErrors(e.message));
      else setErrors({ fields: {}, rest: [errText(e)] });
    } finally {
      setSaving(false);
    }
  };
  const reset = () => {
    setFields({ story_points_field: m.story_points_field, sprint_field: m.sprint_field, flagged_field: m.flagged_field });
    setTypes(toPairs(m.types));
    setDefaultType(m.default_type);
    setProdLabels(m.prod_labels.join(", "));
    setProdTypes(m.prod_issue_types.join(", "));
    setPriorities(toPairs(m.priorities));
    setBlocked(m.blocked_statuses.join(", "));
    setErrors(null);
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
    <div className="jira-mapping">
      <section className="card">
        <div className="card-head">
          <div className="card-title">
            <h2>Trường tuỳ chỉnh</h2>
            <div className="sub">Tên custom field trên Jira; hệ thống tự tìm id (Kiểm tra kết nối cho biết có tìm thấy không).</div>
          </div>
        </div>
        <div className="jira-grid">
          {text("story_points_field", "Story Points", "Độ phức tạp: ≤1 XS, ≤2 S, ≤3 M, ≤5 L, lớn hơn XL.")}
          {text("sprint_field", "Sprint", "Hạn (ngày kết thúc sprint) và milestone.")}
          {text("flagged_field", "Flagged", "Đang Flagged → blocked::impediment.")}
        </div>
      </section>
      <section className="card">
        <div className="card-head">
          <div className="card-title">
            <h2>Loại issue → loại DPI</h2>
            <div className="sub">Sub-task theo loại của issue cha. Epic không được lấy.</div>
          </div>
        </div>
        <PairTable rows={types} onChange={setTypes} left="Loại issue Jira" right="Loại DPI" options={data.dpi_types} addLabel="Thêm loại issue" />
        <FieldError text={fe.types} />
        <label className="jira-field">
          <span>Loại không có trong bảng</span>
          <select value={defaultType} onChange={(e) => setDefaultType(e.target.value)}>
            {data.dpi_types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <FieldError text={fe.default_type} />
        </label>
      </section>
      <section className="card">
        <div className="card-head">
          <div className="card-title">
            <h2>Lỗi production và blocked</h2>
            <div className="sub">Lỗi production vào chỉ số B2 sau khi lead xác nhận RCA; blocked vào báo cáo R4.</div>
          </div>
        </div>
        <div className="jira-two">
          <div>
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
            <label className="jira-field">
              <span>Status coi là blocked</span>
              <input value={blocked} onChange={(e) => setBlocked(e.target.value)} placeholder="Blocked" aria-invalid={!!fe.blocked_statuses} />
              <span className="muted small">Phân cách bằng dấu phẩy → label blocked::&lt;tên status&gt;.</span>
              <FieldError text={fe.blocked_statuses} />
            </label>
          </div>
          <div>
            <h4 className="jira-h4">Priority → mức lỗi</h4>
            <PairTable rows={priorities} onChange={setPriorities} left="Priority Jira" right="Mức" options={["P1", "P2", "P3"]} addLabel="Thêm priority" />
            <p className="muted small">Priority không có trong bảng → P3.</p>
            <FieldError text={fe.priorities} />
          </div>
        </div>
      </section>
      {errors && <Alert tone="error">{errors.rest.length ? `Chưa lưu được: ${errors.rest.join("; ")}` : "Chưa lưu được: sửa các ô báo lỗi ở trên."}</Alert>}
      <div className={`jira-savebar${dirty ? " dirty" : ""}`}>
        <span className="muted small">{dirty ? "Có thay đổi chưa lưu." : "Không có thay đổi."}</span>
        <span className="dpi-spacer" />
        {dirty && (
          <button type="button" className="ghost sm" onClick={reset} disabled={saving}>
            Huỷ thay đổi
          </button>
        )}
        <button type="button" className="primary ink sm" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? "Đang lưu…" : "Lưu quy đổi"}
        </button>
      </div>
    </div>
  );
}
