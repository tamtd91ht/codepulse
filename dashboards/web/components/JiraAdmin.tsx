"use client";

/**
 * DPI › Jira (docs/specs/27 §12): connection, task source, registered Jira projects, sync and
 * the field mapping. Needs `dpi.admin`; the API answers 403 otherwise.
 */
import { useState } from "react";

import { Alert, Card, EmptyState, Loading, Message, Pill } from "@/components/ui";
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

interface JiraView {
  configured: boolean;
  base_url: string | null;
  task_source: "gitlab" | "jira";
  mapping: JiraMapping;
  dpi_types: string[];
  projects: JiraProject[];
  accounts: { total: number; linked: number };
  interval_min: number;
}

interface FieldRef {
  id: string;
  name: string;
}

interface TestResult {
  ok: boolean;
  version: string | null;
  server_title: string | null;
  fields: { story_points: FieldRef | null; sprint: FieldRef | null; flagged: FieldRef | null };
}

const errText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function JiraAdmin() {
  const { data, error, mutate } = useApi<JiraView>("/dpi/jira");
  const [message, setMessage] = useState<string | null>(null);
  if (error) return error.status === 403 ? <Alert tone="info">Cần quyền quản trị DPI (dpi.admin) để cấu hình Jira.</Alert> : <Loading error={error} />;
  if (!data) return <Loading rows={6} />;
  const reload = () => void mutate();
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      <Connection data={data} />
      {data.configured && (
        <>
          <Source data={data} onDone={(m) => (setMessage(m), reload())} />
          <Projects key={data.projects.map((p) => `${p.key}:${p.enabled}:${p.business_project?.id ?? ""}`).join("|")} data={data} onDone={(m) => (setMessage(m), reload())} />
          <MappingEditor key={JSON.stringify(data.mapping)} data={data} onDone={(m) => (setMessage(m), reload())} />
        </>
      )}
    </>
  );
}

function Connection({ data }: { data: JiraView }) {
  const [result, setResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const test = async () => {
    setBusy(true);
    setTestError(null);
    try {
      setResult(await api.post<TestResult>("/dpi/jira/test"));
    } catch (e) {
      setResult(null);
      setTestError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  if (!data.configured)
    return (
      <Card title="Kết nối Jira" collapsible={false}>
        <EmptyState icon="info" title="Chưa cấu hình Jira Data Center.">
          Đặt biến môi trường <code>JIRA_BASE_URL</code> (ví dụ <code>https://jira.company.vn</code>) và <code>JIRA_TOKEN</code> (PAT của tài khoản dịch vụ chỉ
          đọc, quyền Browse projects) cho API và worker, rồi khởi động lại. Tuỳ chọn: <code>JIRA_CA_BUNDLE</code>, <code>JIRA_SYNC_INTERVAL_MIN</code> (mặc định 15),{" "}
          <code>JIRA_BACKFILL_DAYS</code> (mặc định 180).
        </EmptyState>
      </Card>
    );
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
  return (
    <Card
      title="Kết nối Jira"
      collapsible={false}
      subtitle={`Chỉ đọc. Đồng bộ mỗi ${data.interval_min} phút. Tài khoản Jira đã nối người dùng: ${data.accounts.linked} / ${data.accounts.total}.`}
      actions={
        <button type="button" className="sm" disabled={busy} onClick={() => void test()}>
          {busy ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
        </button>
      }
    >
      <p>
        <Pill tone="good">đã cấu hình</Pill> <code>{data.base_url}</code>
      </p>
      {testError && <Alert tone="error">Kết nối lỗi: {testError}</Alert>}
      {result && (
        <Alert tone={result.ok ? "success" : "warning"}>
          <div>
            {result.server_title ?? "Jira"} · phiên bản {result.version ?? "—"}
          </div>
          <ul className="small" style={{ margin: "4px 0 0" }}>
            {field("Story Points", result.fields.story_points)}
            {field("Sprint", result.fields.sprint)}
            {field("Flagged", result.fields.flagged)}
          </ul>
        </Alert>
      )}
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
