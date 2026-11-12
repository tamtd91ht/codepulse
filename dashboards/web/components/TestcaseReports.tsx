"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Icon, type IconName } from "@/components/icons";
import { Alert, Card, ChartTip, EmptyState, Kpi, Loading, Pill, Segmented } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";

/**
 * Tab "Báo cáo" of Testcase (docs/specs/30 §15, G4): a report picker, the filters, the report
 * (charts + tables), its Excel export, a print view ("In / Lưu PDF") and a shareable link (the
 * filters live in the URL: ?tab=reports&report=…&r_from=…). Managers also see the report
 * schedules sent to a webhook.
 */

type Kind = "summary" | "compare" | "coverage" | "defects" | "productivity" | "automation" | "readiness";
type Counts = Record<string, number>;
interface StatusMeta {
  key: string;
  name: string;
  color: string;
  done: boolean;
}
interface Options {
  suites: { id: number; name: string }[];
  milestones: { id: number; name: string; completed: boolean }[];
  plans: { id: number; name: string }[];
  configs: { id: number; name: string }[];
  runs: { id: number; key: string; name: string; config: string | null; created_at: string | null; completed: boolean }[];
  branches: string[];
  statuses: StatusMeta[];
  can_manage: boolean;
  user_id: number;
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- report JSON, shaped per kind
type Data = Record<string, any>;

const REPORTS: { kind: Kind; title: string; text: string; icon: IconName }[] = [
  { kind: "summary", title: "Tổng kết run / plan", text: "Trạng thái, tiến độ theo ngày, theo người, theo cấu hình", icon: "activity" },
  { kind: "readiness", title: "Sẵn sàng release", text: "Go / No-go của milestone kèm lý do, xu hướng pass", icon: "flag" },
  { kind: "compare", title: "So sánh run", text: "Ma trận testcase × run, hồi quy và đã sửa", icon: "scale" },
  { kind: "coverage", title: "Độ phủ", text: "Theo yêu cầu Jira, thư mục, milestone", icon: "clipboardCheck" },
  { kind: "defects", title: "Defect", text: "Bug theo testcase, thư mục; mở / đóng; chặn release", icon: "alert" },
  { kind: "automation", title: "Automation", text: "% tự động theo tuần, flaky, thời gian CI", icon: "code" },
  { kind: "productivity", title: "Năng suất", text: "Testcase tạo / duyệt, kết quả ghi theo người", icon: "user" },
];
const KINDS = REPORTS.map((r) => r.kind);
const TONE_VAR: Record<string, string> = {
  good: "var(--good)",
  bad: "var(--bad)",
  warn: "var(--warning)",
  info: "var(--chart-1)",
  muted: "var(--muted)",
  primary: "var(--chart-4)",
};
const PILL_TONE: Record<string, "good" | "bad" | "warn" | "info" | "primary" | undefined> = { good: "good", bad: "bad", warn: "warn", info: "info", primary: "primary" };
const PRIORITY: Record<string, string> = { critical: "Rất cao", high: "Cao", medium: "Trung bình", low: "Thấp" };
const BUG_STATE: Record<string, string> = { open: "Đang mở", closed: "Đã đóng", unknown: "Chưa đồng bộ" };
const CHANGE: Record<string, { label: string; tone: "bad" | "good" | "info" }> = {
  regression: { label: "Hồi quy", tone: "bad" },
  fixed: { label: "Đã sửa", tone: "good" },
  changed: { label: "Thay đổi", tone: "info" },
};
const PERIODS = [
  { value: "7", label: "7 ngày" },
  { value: "30", label: "30 ngày" },
  { value: "90", label: "90 ngày" },
  { value: "all", label: "Tất cả" },
  { value: "custom", label: "Tuỳ chọn" },
] as const;
type Period = (typeof PERIODS)[number]["value"];
const WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"];

interface F {
  report: Kind;
  period: Period;
  from: string;
  to: string;
  suite: string;
  milestone: string;
  plan: string;
  config: string;
  branch: string;
  runs: number[];
  rate: string;
}
const START: F = { report: "summary", period: "30", from: "", to: "", suite: "", milestone: "", plan: "", config: "", branch: "", runs: [], rate: "95" };
const URL_KEYS: Record<Exclude<keyof F, "report" | "runs" | "period">, string> = {
  from: "r_from",
  to: "r_to",
  suite: "r_suite",
  milestone: "r_ms",
  plan: "r_plan",
  config: "r_cfg",
  branch: "r_branch",
  rate: "r_rate",
};

function iso(d: Date): string {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 10);
}

function readUrl(): F {
  if (typeof window === "undefined") return START;
  const p = new URLSearchParams(window.location.search);
  const f: F = { ...START };
  const report = p.get("report");
  if (report && (KINDS as string[]).includes(report)) f.report = report as Kind;
  for (const [k, u] of Object.entries(URL_KEYS) as [keyof typeof URL_KEYS, string][]) {
    const v = p.get(u);
    if (v) f[k] = v.slice(0, 200);
  }
  const period = p.get("r_period");
  if (period && PERIODS.some((x) => x.value === period)) f.period = period as Period;
  else if (f.from || f.to) f.period = "custom";
  f.runs = (p.get("r_runs") ?? "")
    .split(",")
    .filter((x) => /^\d+$/.test(x))
    .map(Number)
    .slice(0, 10);
  return f;
}

function writeUrl(f: F) {
  const p = new URLSearchParams(window.location.search);
  for (const k of ["report", "r_period", "r_runs", ...Object.values(URL_KEYS)]) p.delete(k);
  p.set("tab", "reports");
  p.set("report", f.report);
  if (f.period !== START.period) p.set("r_period", f.period);
  if (f.period === "custom") {
    if (f.from) p.set(URL_KEYS.from, f.from);
    if (f.to) p.set(URL_KEYS.to, f.to);
  }
  for (const k of ["suite", "milestone", "plan", "config", "branch"] as const) if (f[k]) p.set(URL_KEYS[k], f[k]);
  if (f.rate !== START.rate) p.set(URL_KEYS.rate, f.rate);
  if (f.runs.length) p.set("r_runs", f.runs.join(","));
  window.history.replaceState(null, "", `${window.location.pathname}?${p}`);
}

function range(f: F): { from?: string; to?: string } {
  if (f.period === "all") return {};
  if (f.period === "custom") return { from: f.from || undefined, to: f.to || undefined };
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - Number(f.period) + 1);
  return { from: iso(from), to: iso(to) };
}

function query(projectId: number, f: F): string {
  const r = range(f);
  return qs({
    business_project_id: projectId,
    suite_id: f.suite,
    milestone_id: f.milestone,
    plan_id: f.plan,
    config_id: f.config,
    branch: f.branch,
    from: r.from,
    to: r.to,
    run_ids: f.report === "compare" ? f.runs.join(",") : undefined,
    min_pass_rate: f.report === "readiness" && f.rate !== "95" ? f.rate : undefined,
  });
}

const n0 = (v: unknown) => (typeof v === "number" ? v.toLocaleString("vi-VN") : "—");
const p1 = (v: unknown) => (typeof v === "number" ? `${v.toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%` : "—");
function dur(s: number): string {
  if (!s) return "0";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} giờ ${m} phút` : m ? `${m} phút ${s % 60}s` : `${s}s`;
}
const shortDay = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}`;

/** Statuses present in some counts, in the dự án's order (unknown ones last). */
function present(meta: StatusMeta[], rows: Counts[]): StatusMeta[] {
  const seen = new Set<string>();
  for (const c of rows) for (const [k, v] of Object.entries(c)) if (k !== "total" && k !== "done" && v) seen.add(k);
  const known = meta.filter((m) => seen.has(m.key));
  const extra = [...seen].filter((k) => !meta.some((m) => m.key === k)).map((k) => ({ key: k, name: k, color: "muted", done: true }));
  return [...known, ...extra];
}

export function TestcaseReports({ projectId }: { projectId: number }) {
  const [f, setF] = useState<F>(START);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setF(readUrl());
    setReady(true);
  }, []);
  useEffect(() => {
    if (ready) writeUrl(f);
  }, [f, ready]);
  const set = (patch: Partial<F>) => setF((x) => ({ ...x, ...patch }));

  const options = useApi<Options>(`/testcases/reports/options?business_project_id=${projectId}`);
  const o = options.data;
  const needsRuns = f.report === "compare" && (f.runs.length < 2 || f.runs.length > 10);
  const needsMilestone = f.report === "readiness" && !f.milestone;
  const q = query(projectId, f);
  const report = useApi<Data>(ready && !needsRuns && !needsMilestone ? `/testcases/reports/${f.report}${q}` : null, { keepPreviousData: true });
  const meta = o?.statuses ?? [];
  const [copied, setCopied] = useState(false);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt("Sao chép link", window.location.href);
    }
  };
  const info = REPORTS.find((r) => r.kind === f.report)!;
  const r = range(f);

  return (
    <div className="tc-reports">
      <div className="tc-report-picker no-print" role="group" aria-label="Chọn báo cáo">
        {REPORTS.map((x) => (
          <button key={x.kind} type="button" className={`tc-report-card${f.report === x.kind ? " active" : ""}`} aria-pressed={f.report === x.kind} onClick={() => set({ report: x.kind })}>
            <span className="tc-report-icon">
              <Icon name={x.icon} size={18} />
            </span>
            <span>
              <strong>{x.title}</strong>
              <span className="small muted">{x.text}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="toolbar filters tc-report-filters no-print">
        {f.report !== "readiness" && f.report !== "compare" && (
          <div className="filter">
            <span className="small muted">Thời gian</span>
            <Segmented value={f.period} onChange={(v) => set({ period: v })} options={PERIODS.map((p) => ({ value: p.value, label: p.label }))} label="Khoảng thời gian" />
          </div>
        )}
        {f.period === "custom" && f.report !== "readiness" && f.report !== "compare" && (
          <>
            <label className="filter">
              <span className="small muted">Từ ngày</span>
              <input type="date" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} />
            </label>
            <label className="filter">
              <span className="small muted">Đến ngày</span>
              <input type="date" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} />
            </label>
          </>
        )}
        {f.report !== "compare" && (
          <>
            <Select label={f.report === "readiness" ? "Milestone (bắt buộc)" : "Milestone"} value={f.milestone} onChange={(v) => set({ milestone: v })} all={f.report === "readiness" ? "Chọn milestone" : "Tất cả"} items={(o?.milestones ?? []).map((m) => ({ value: String(m.id), label: m.completed ? `${m.name} (đã xong)` : m.name }))} />
            {f.report !== "readiness" && (
              <>
                <Select label="Bộ" value={f.suite} onChange={(v) => set({ suite: v })} items={(o?.suites ?? []).map((s) => ({ value: String(s.id), label: s.name }))} />
                {f.report !== "productivity" && f.report !== "coverage" && (
                  <>
                    <Select label="Plan" value={f.plan} onChange={(v) => set({ plan: v })} items={(o?.plans ?? []).map((s) => ({ value: String(s.id), label: s.name }))} />
                    <Select label="Cấu hình" value={f.config} onChange={(v) => set({ config: v })} items={(o?.configs ?? []).map((s) => ({ value: String(s.id), label: s.name }))} />
                  </>
                )}
                {f.report !== "productivity" && (
                  <Select label="Nhánh CI" value={f.branch} onChange={(v) => set({ branch: v })} items={(o?.branches ?? []).map((b) => ({ value: b, label: b }))} />
                )}
              </>
            )}
            {f.report === "readiness" && (
              <label className="filter">
                <span className="small muted">% passed tối thiểu</span>
                <input type="number" min={0} max={100} step={1} value={f.rate} onChange={(e) => set({ rate: e.target.value })} style={{ width: 90 }} />
              </label>
            )}
          </>
        )}
        {f.report === "compare" && <RunPicker runs={o?.runs ?? []} value={f.runs} onChange={(runs) => set({ runs })} />}
        <span className="spacer" />
        <div className="tc-report-actions">
          <a className={`button sm${needsRuns || needsMilestone ? " disabled" : ""}`} href={needsRuns || needsMilestone ? undefined : `/api/v1/testcases/reports/${f.report}/export${q}`} download aria-disabled={needsRuns || needsMilestone}>
            <Icon name="download" size={14} /> Xuất Excel
          </a>
          <button type="button" className="ghost sm" onClick={() => window.print()}>
            <Icon name="printer" size={14} /> In / Lưu PDF
          </button>
          <button type="button" className="ghost sm" onClick={() => void copyLink()}>
            <Icon name={copied ? "check" : "link"} size={14} /> {copied ? "Đã sao chép" : "Sao chép link"}
          </button>
        </div>
      </div>

      <div className="tc-report-print-head">
        <strong>Báo cáo testcase: {info.title}</strong>
        <span className="small muted">
          {r.from || r.to ? `${r.from ?? "…"} → ${r.to ?? "…"}` : "Mọi thời gian"} · in lúc {new Date().toLocaleString("vi-VN")}
        </span>
      </div>

      {options.error ? (
        <Loading error={options.error} />
      ) : needsRuns ? (
        <EmptyState icon="scale" title="Chọn từ 2 đến 10 run để so sánh">
          Dùng ô “Run so sánh” ở trên.
        </EmptyState>
      ) : needsMilestone ? (
        <EmptyState icon="flag" title="Chọn milestone để xem mức sẵn sàng release" />
      ) : !report.data || !o ? (
        <ReportSkeleton error={report.error} />
      ) : report.data.kind !== f.report ? (
        <ReportSkeleton />
      ) : (
        <div className={report.isValidating ? "tc-report-body stale" : "tc-report-body"} aria-busy={report.isValidating}>
          {report.error && <Alert tone="error">Không tải lại được: {report.error.message}</Alert>}
          {f.report === "summary" && <SummaryReport d={report.data} meta={meta} />}
          {f.report === "compare" && <CompareReport d={report.data} meta={meta} />}
          {f.report === "coverage" && <CoverageReport d={report.data} />}
          {f.report === "defects" && <DefectsReport d={report.data} />}
          {f.report === "productivity" && <ProductivityReport d={report.data} />}
          {f.report === "automation" && <AutomationReport d={report.data} />}
          {f.report === "readiness" && <ReadinessReport d={report.data} meta={meta} />}
        </div>
      )}

      {o?.can_manage && <Schedules projectId={projectId} options={o} />}
    </div>
  );
}

function ReportSkeleton({ error }: { error?: Error }) {
  if (error) return <Loading error={error} />;
  return (
    <div className="tc-report-body" role="status" aria-label="Đang tải báo cáo">
      <div className="grid kpis">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card kpi">
            <span className="skeleton" style={{ width: "60%", height: 12 }} />
            <span className="skeleton" style={{ width: "40%", height: 24 }} />
          </div>
        ))}
      </div>
      <Card title="Đang tải" collapsible={false}>
        <Loading rows={6} />
      </Card>
    </div>
  );
}

function Select({ label, value, onChange, items, all = "Tất cả" }: { label: string; value: string; onChange: (v: string) => void; items: { value: string; label: string }[]; all?: string }) {
  return (
    <label className="filter">
      <span className="small muted">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{all}</option>
        {items.map((i) => (
          <option key={i.value} value={i.value}>
            {i.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function RunPicker({ runs, value, onChange }: { runs: Options["runs"]; value: number[]; onChange: (v: number[]) => void }) {
  const [q, setQ] = useState("");
  const shown = runs.filter((r) => !q.trim() || `${r.key} ${r.name} ${r.config ?? ""}`.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 100);
  const toggle = (id: number) => onChange(value.includes(id) ? value.filter((x) => x !== id) : value.length >= 10 ? value : [...value, id]);
  return (
    <details className="filter tc-run-picker">
      <summary>
        <span className="small muted">Run so sánh</span>
        <span>{value.length ? `${value.length} run đã chọn` : "Chọn 2 đến 10 run"}</span>
      </summary>
      <div className="tc-run-picker-pop">
        <input value={q} placeholder="Tìm run" aria-label="Tìm run" onChange={(e) => setQ(e.target.value)} />
        <ul>
          {shown.map((r) => (
            <li key={r.id}>
              <label className="inline-check small">
                <input type="checkbox" checked={value.includes(r.id)} disabled={!value.includes(r.id) && value.length >= 10} onChange={() => toggle(r.id)} />
                <span className="tc-codes">{r.key}</span> {r.name}
                {r.config ? <span className="muted"> · {r.config}</span> : null}
              </label>
            </li>
          ))}
          {!shown.length && <li className="small muted">Không có run nào</li>}
        </ul>
        {value.length > 0 && (
          <button type="button" className="ghost sm" onClick={() => onChange([])}>
            Bỏ chọn tất cả
          </button>
        )}
      </div>
    </details>
  );
}

/* ---------- pieces ---------- */

function StatusBar({ counts, meta, label }: { counts: Counts; meta: StatusMeta[]; label: string }) {
  const total = counts.total || 0;
  const parts = present(meta, [counts]);
  if (!total) return <span className="small muted">Chưa có</span>;
  return (
    <div className="tc-status-bar" role="img" aria-label={`${label}: ${parts.map((p) => `${p.name} ${counts[p.key]}`).join(", ")}`}>
      {parts.map((p) => (
        <span key={p.key} title={`${p.name}: ${counts[p.key]}`} style={{ width: `${(100 * (counts[p.key] || 0)) / total}%`, background: TONE_VAR[p.color] ?? "var(--muted)" }} />
      ))}
    </div>
  );
}

function Legend({ items }: { items: StatusMeta[] }) {
  return (
    <div className="chart-legend small" aria-hidden="true">
      {items.map((s) => (
        <span key={s.key} className="tc-legend-item">
          <span className="chart-legend-swatch" style={{ background: TONE_VAR[s.color] ?? "var(--muted)" }} /> {s.name}
        </span>
      ))}
    </div>
  );
}

function StatusCell({ status, meta }: { status: string | null; meta: StatusMeta[] }) {
  if (!status) return <span className="muted">—</span>;
  const m = meta.find((x) => x.key === status);
  const tone = m ? PILL_TONE[m.color] : undefined;
  return <Pill tone={tone}>{m?.name ?? status}</Pill>;
}

function Meter({ value }: { value: number | null }) {
  return (
    <span className="tc-meter" title={p1(value)}>
      <span style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
      <em>{p1(value)}</em>
    </span>
  );
}

function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: boolean }) {
  if (empty) return <p className="small muted tc-report-empty">Không có dữ liệu trong bộ lọc này.</p>;
  return (
    <div className="table-wrap">
      <table className="tc-table tc-report-table">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function IssueLink({ k, url }: { k: string; url?: string | null }) {
  return url ? (
    <a className="link" href={url} target="_blank" rel="noreferrer">
      {k}
    </a>
  ) : (
    <strong>{k}</strong>
  );
}

/* ---------- 1. summary ---------- */

function SummaryReport({ d, meta }: { d: Data; meta: StatusMeta[] }) {
  const tests: Counts = d.tests;
  const progress: { day: string; counts: Counts; total: number }[] = d.progress;
  const pStatuses = present(meta, progress.map((p) => p.counts));
  const rows = progress.map((p) => ({ day: shortDay(p.day), ...p.counts }));
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Run" value={d.runs} change={false} digits={0} />
        <Kpi label="Test" value={tests.total} change={false} digits={0} hint={`${n0(tests.done)} đã xong (${p1(d.done_rate)})`} />
        <Kpi label="Tỷ lệ passed" value={d.pass_rate ?? 0} unit="%" change={false} />
        <Kpi label="Kết quả trong kỳ" value={d.results.total} change={false} digits={0} />
      </div>
      <Card title="Trạng thái test" id="rep-sum-status" collapsible={false}>
        <StatusBar counts={tests} meta={meta} label="Trạng thái test" />
        <Legend items={present(meta, [tests]).map((s) => ({ ...s, name: `${s.name} ${n0(tests[s.key])}` }))} />
      </Card>
      <Card title="Tiến độ theo ngày" subtitle="Kết quả ghi mỗi ngày, theo trạng thái" id="rep-sum-progress" collapsible={false}>
        {rows.length ? (
          <>
            <div className="tc-chart" role="img" aria-label={`Kết quả theo ngày: ${progress.map((p) => `${p.day} ${p.total}`).join(", ")}`}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="day" tickLine={false} axisLine={false} fontSize={12} minTickGap={12} />
                  <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip content={<ChartTip digits={0} />} cursor={{ fill: "var(--surface-2)" }} />
                  {pStatuses.map((s, i) => (
                    <Bar key={s.key} dataKey={s.key} name={s.name} stackId="s" fill={TONE_VAR[s.color] ?? "var(--muted)"} maxBarSize={36} radius={i === pStatuses.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
            <Legend items={pStatuses} />
          </>
        ) : (
          <p className="small muted tc-report-empty">Chưa có kết quả nào trong kỳ.</p>
        )}
      </Card>
      <div className="grid two">
        <Card title="Theo người ghi" subtitle={d.people_all ? undefined : "Bạn thấy dòng của mình; số tổng ở trên tính mọi người"} id="rep-sum-people" collapsible={false}>
          <Table head={["Người", "Kết quả", "Trạng thái", "Thời gian"]} empty={!d.by_person.length}>
            {d.by_person.map((p: Data) => (
              <tr key={`${p.user_id}-${p.source}`}>
                <td>
                  {p.name}
                  {p.source === "ci" && <span className="small muted"> · CI</span>}
                </td>
                <td className="num">{n0(p.total)}</td>
                <td style={{ minWidth: 120 }}>
                  <StatusBar counts={{ ...p.counts, total: p.total }} meta={meta} label={p.name} />
                </td>
                <td className="num">{dur(p.elapsed_s)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Theo cấu hình" subtitle="Test của các run có cấu hình (plan)" id="rep-sum-config" collapsible={false}>
          <Table head={["Cấu hình", "Test", "Trạng thái", "% passed"]} empty={!d.by_config.length}>
            {d.by_config.map((c: Data) => (
              <tr key={c.config}>
                <td>{c.config}</td>
                <td className="num">{n0(c.counts.total)}</td>
                <td style={{ minWidth: 120 }}>
                  <StatusBar counts={c.counts} meta={meta} label={c.config} />
                </td>
                <td className="num">{p1(c.pass_rate)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}

/* ---------- 2. compare ---------- */

function CompareReport({ d, meta }: { d: Data; meta: StatusMeta[] }) {
  const [only, setOnly] = useState(true);
  const runs: Data[] = d.runs;
  const rows: Data[] = only ? d.rows.filter((r: Data) => r.change) : d.rows;
  const [limit, setLimit] = useState(200);
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Hồi quy (passed → failed)" value={d.summary.regression} change={false} digits={0} />
        <Kpi label="Đã sửa (failed → passed)" value={d.summary.fixed} change={false} digits={0} />
        <Kpi label="Thay đổi khác" value={d.summary.changed} change={false} digits={0} />
        <Kpi label="Không đổi" value={d.summary.same} change={false} digits={0} />
      </div>
      <Card title="Các run" id="rep-cmp-runs" collapsible={false}>
        <Table head={["Run", "Cấu hình", "Test", "Trạng thái", "% passed"]}>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>
                <span className="tc-codes">{r.key}</span> {r.name}
              </td>
              <td>{r.config ?? "—"}</td>
              <td className="num">{n0(r.counts.total)}</td>
              <td style={{ minWidth: 120 }}>
                <StatusBar counts={r.counts} meta={meta} label={r.name} />
              </td>
              <td className="num">{p1(r.pass_rate)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card
        title={`Ma trận testcase × run (${rows.length})`}
        id="rep-cmp-matrix"
        collapsible={false}
        actions={
          <label className="inline-check small no-print">
            <input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Chỉ testcase có thay đổi
          </label>
        }
      >
        <Table head={["Testcase", ...runs.map((r) => <span key={r.id} title={r.name}>{r.key}</span>), "Thay đổi"]} empty={!rows.length}>
          {rows.slice(0, limit).map((r) => (
            <tr key={r.case_id ?? r.title} className={r.change === "regression" ? "tc-row-bad" : undefined}>
              <td>
                {r.case_key && <span className="tc-codes">{r.case_key}</span>} {r.title}
                {r.section && <div className="small muted">{r.section}</div>}
              </td>
              {r.statuses.map((s: string | null, i: number) => (
                <td key={i}>
                  <StatusCell status={s} meta={meta} />
                </td>
              ))}
              <td>{r.change ? <Pill tone={CHANGE[r.change].tone}>{CHANGE[r.change].label}</Pill> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </Table>
        {rows.length > limit && (
          <button type="button" className="ghost sm no-print" onClick={() => setLimit((x) => x + 500)}>
            Xem thêm ({rows.length - limit})
          </button>
        )}
      </Card>
    </>
  );
}

/* ---------- 3. coverage ---------- */

function CoverageReport({ d }: { d: Data }) {
  const req = d.requirements;
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Issue Jira có testcase" value={req.rate ?? 0} unit="%" change={false} hint={`${n0(req.covered)}/${n0(req.issues)} issue`} />
        <Kpi label="Testcase đã chạy trong kỳ" value={d.executed_rate ?? 0} unit="%" change={false} hint={`${n0(d.executed)}/${n0(d.cases)} testcase`} />
        <Kpi label="Testcase passed (kết quả mới nhất)" value={d.passed_rate ?? 0} unit="%" change={false} hint={`${n0(d.passed)} testcase`} />
        <Kpi label="Testcase chưa gắn yêu cầu" value={req.cases_without_requirement} change={false} digits={0} />
      </div>
      <div className="grid two">
        <Card title="Theo loại issue Jira" id="rep-cov-type" collapsible={false}>
          <Table head={["Loại", "Issue", "Có testcase", "% phủ"]} empty={!req.by_type.length}>
            {req.by_type.map((t: Data) => (
              <tr key={t.type}>
                <td>{t.type}</td>
                <td className="num">{n0(t.issues)}</td>
                <td className="num">{n0(t.covered)}</td>
                <td>
                  <Meter value={t.rate} />
                </td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title={`Issue chưa có testcase (${n0(req.uncovered)})`} id="rep-cov-uncovered" collapsible={false}>
          <Table head={["Issue", "Trạng thái"]} empty={!req.uncovered_list.length}>
            {req.uncovered_list.map((u: Data) => (
              <tr key={u.key}>
                <td>
                  <IssueLink k={u.key} url={u.url} /> {u.title}
                  {u.type && <div className="small muted">{u.type}</div>}
                </td>
                <td>{BUG_STATE[u.state] ?? u.state}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
      <Card title="Theo thư mục" subtitle="% testcase có kết quả trong kỳ / % có kết quả mới nhất là Passed" id="rep-cov-sections" collapsible={false}>
        <Table head={["Thư mục", "Testcase", "Đã chạy", "Passed"]} empty={!d.sections.length}>
          {d.sections.map((s: Data) => (
            <tr key={`${s.suite_id}-${s.section_id}`}>
              <td>
                {s.section}
                <div className="small muted">{s.suite}</div>
              </td>
              <td className="num">{n0(s.cases)}</td>
              <td>
                <Meter value={s.executed_rate} />
              </td>
              <td>
                <Meter value={s.passed_rate} />
              </td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Theo milestone" id="rep-cov-ms" collapsible={false}>
        <Table head={["Milestone", "Hạn", "Testcase", "Test", "Đã chạy", "Passed"]} empty={!d.milestones.length}>
          {d.milestones.map((m: Data) => (
            <tr key={m.id}>
              <td>
                <span className="tc-codes">{m.key}</span> {m.name} {m.completed && <Pill tone="good">Đã xong</Pill>}
              </td>
              <td>{m.due_on ?? "—"}</td>
              <td className="num">{n0(m.cases)}</td>
              <td className="num">{n0(m.tests.total)}</td>
              <td>
                <Meter value={m.done_rate} />
              </td>
              <td>
                <Meter value={m.pass_rate} />
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}

/* ---------- 4. defects ---------- */

function DefectsReport({ d }: { d: Data }) {
  const t = d.totals;
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Bug được ghi" value={t.defects} change={false} digits={0} />
        <Kpi label="Đang mở" value={t.open} change={false} digits={0} />
        <Kpi label="Chặn release" value={t.blocking} change={false} digits={0} hint="Mở và test còn Failed / Blocked" />
        <Kpi label="Đã đóng" value={t.closed} change={false} digits={0} hint={t.unknown ? `${t.unknown} chưa đồng bộ từ Jira` : undefined} />
      </div>
      <Card title="Bug" id="rep-def-bugs" collapsible={false}>
        <Table head={["Bug", "Trạng thái", "Testcase", "Kết quả"]} empty={!d.defects.length}>
          {d.defects.map((b: Data) => (
            <tr key={b.key}>
              <td>
                <IssueLink k={b.key} url={b.url} /> {b.title}
                {b.blocking && (
                  <>
                    {" "}
                    <Pill tone="bad" icon="alert">
                      Chặn release
                    </Pill>
                  </>
                )}
              </td>
              <td>
                <Pill tone={b.state === "open" ? "warn" : b.state === "closed" ? "good" : undefined}>{BUG_STATE[b.state] ?? b.state}</Pill>
              </td>
              <td className="num">{n0(b.cases)}</td>
              <td className="num">{n0(b.results)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <div className="grid two">
        <Card title="Theo testcase" id="rep-def-cases" collapsible={false}>
          <Table head={["Testcase", "Bug", "Mở"]} empty={!d.by_case.length}>
            {d.by_case.map((c: Data) => (
              <tr key={c.case_id ?? c.title}>
                <td>
                  {c.case_key && <span className="tc-codes">{c.case_key}</span>} {c.title}
                </td>
                <td>{c.defects.join(", ")}</td>
                <td className="num">{n0(c.open)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Theo thư mục" id="rep-def-sections" collapsible={false}>
          <Table head={["Thư mục", "Bug", "Mở"]} empty={!d.by_section.length}>
            {d.by_section.map((s: Data) => (
              <tr key={s.section}>
                <td>{s.section}</td>
                <td className="num">{n0(s.defects)}</td>
                <td className="num">{n0(s.open)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}

/* ---------- 5. productivity ---------- */

function ProductivityReport({ d }: { d: Data }) {
  const t = d.totals;
  return (
    <>
      <Alert tone="info">Số liệu để trao đổi và hỗ trợ nhau, không dùng để xếp hạng. {d.people_all ? "" : "Bạn thấy dòng của mình; số tổng tính mọi người."}</Alert>
      <div className="grid kpis">
        <Kpi label="Testcase tạo" value={t.created} change={false} digits={0} />
        <Kpi label="Testcase duyệt" value={t.approved} change={false} digits={0} />
        <Kpi label="Kết quả ghi tay" value={t.results} change={false} digits={0} hint={`Thời gian chạy ${dur(t.elapsed_s)}`} />
        <Kpi label="Kết quả từ CI" value={t.ci_results} change={false} digits={0} />
      </div>
      <Card title="Theo người" id="rep-prod-people" collapsible={false}>
        <Table head={["Người", "Testcase tạo", "Testcase duyệt", "Kết quả ghi", "Thời gian chạy"]} empty={!d.people.length}>
          {d.people.map((p: Data) => (
            <tr key={p.user_id}>
              <td>{p.name}</td>
              <td className="num">{n0(p.created)}</td>
              <td className="num">{n0(p.approved)}</td>
              <td className="num">{n0(p.results)}</td>
              <td className="num">{dur(p.elapsed_s)}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}

/* ---------- 6. automation ---------- */

function AutomationReport({ d }: { d: Data }) {
  const trend = d.trend.map((t: Data) => ({ week: shortDay(t.week), rate: t.rate ?? 0, automated: t.automated, cases: t.cases }));
  return (
    <>
      <div className="grid kpis">
        <Kpi label="% tự động (CI 30 ngày)" value={d.rate ?? 0} unit="%" change={false} />
        <Kpi label="Testcase kiểu tự động" value={d.automated_kind} change={false} digits={0} hint={`/${n0(d.cases)} testcase`} />
        <Kpi label="Flaky" value={d.flaky_count} change={false} digits={0} />
        <Kpi label="Lượt CI trong kỳ" value={d.ci_runs.length} change={false} digits={0} />
      </div>
      <Card title="% tự động theo tuần" subtitle="Testcase automated có kết quả CI trong 30 ngày tính đến cuối tuần" id="rep-auto-trend" collapsible={false}>
        <div className="tc-chart" role="img" aria-label={`% tự động theo tuần: ${d.trend.map((t: Data) => `${t.week} ${p1(t.rate)}`).join(", ")}`}>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} minTickGap={12} />
              <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} unit="%" />
              <Tooltip content={<ChartTip digits={1} />} />
              <Line dataKey="rate" name="% tự động" stroke="var(--chart-1)" strokeWidth={2.5} dot={{ r: 3 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <div className="grid two">
        <Card title={`Testcase flaky (${n0(d.flaky_count)})`} id="rep-auto-flaky" collapsible={false}>
          <Table head={["Testcase", "Bộ / thư mục"]} empty={!d.flaky.length}>
            {d.flaky.map((c: Data) => (
              <tr key={c.case_id}>
                <td>
                  <span className="tc-codes">{c.case_key}</span> {c.title}
                </td>
                <td className="small">
                  {c.suite}
                  {c.section && ` › ${c.section}`}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Lượt CI" subtitle="Tổng thời gian chạy các test của mỗi lượt" id="rep-auto-ci" collapsible={false}>
          <Table head={["Run", "Nhánh", "Kết quả", "Failed", "Thời gian"]} empty={!d.ci_runs.length}>
            {d.ci_runs.map((c: Data, i: number) => (
              <tr key={`${c.run_id}-${c.import_id ?? i}`}>
                <td>
                  <span className="tc-codes">{c.run_key}</span> {c.run_name}
                  <div className="small muted">{c.at ? new Date(c.at).toLocaleString("vi-VN") : ""}</div>
                </td>
                <td className="small">
                  {c.branch ?? "—"}
                  {c.commit_sha && <div className="muted tc-codes">{c.commit_sha}</div>}
                </td>
                <td className="num">{n0(c.results)}</td>
                <td className="num">{n0(c.failed)}</td>
                <td className="num">{dur(c.elapsed_s)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}

/* ---------- 7 / 8. readiness ---------- */

function ReadinessReport({ d, meta }: { d: Data; meta: StatusMeta[] }) {
  const trend = d.trend.map((t: Data) => ({ day: shortDay(t.day), rate: t.rate ?? 0, results: t.results }));
  return (
    <>
      <section className={`tc-verdict ${d.go ? "go" : "nogo"}`} aria-live="polite">
        <span className="tc-verdict-badge">
          <Icon name={d.go ? "checkCircle" : "alertCircle"} size={22} />
          {d.go ? "Go" : "No-go"}
        </span>
        <div>
          <strong>
            {d.milestone.key} {d.milestone.name}
          </strong>
          {d.milestone.due_on && <span className="small muted"> · hạn {d.milestone.due_on}</span>}
          {d.reasons.length ? (
            <ul>
              {d.reasons.map((r: string) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : (
            <p className="small">Đạt mọi điều kiện (passed ≥ {d.min_pass_rate}%, không còn test ưu tiên cao Failed / chưa chạy, không còn bug mở).</p>
          )}
          {d.warnings.length > 0 && <p className="small muted">Lưu ý: {d.warnings.join("; ")}</p>}
        </div>
      </section>
      <div className="grid kpis">
        <Kpi label="Tỷ lệ passed" value={d.pass_rate ?? 0} unit="%" change={false} hint={`Ngưỡng ${d.min_pass_rate}%`} />
        <Kpi label="Đã chạy" value={d.done_rate ?? 0} unit="%" change={false} hint={`${n0(d.tests.done)}/${n0(d.tests.total)} test · ${n0(d.runs)} run`} />
        <Kpi label="Ưu tiên cao: Failed / chưa chạy" value={d.high_failed + d.high_untested} change={false} digits={0} hint={`${n0(d.high_failed)} Failed · ${n0(d.high_untested)} chưa chạy`} />
        <Kpi label="Bug mở · flaky" value={d.open_bugs} change={false} digits={0} hint={`${n0(d.flaky)} testcase flaky`} />
      </div>
      <Card title="Trạng thái test của milestone" id="rep-ready-status" collapsible={false}>
        <StatusBar counts={d.tests} meta={meta} label="Trạng thái test" />
        <Legend items={present(meta, [d.tests]).map((s) => ({ ...s, name: `${s.name} ${n0(d.tests[s.key])}` }))} />
      </Card>
      <Card title="Pass rate theo ngày" subtitle="Kết quả Passed / mọi kết quả ghi trong ngày" id="rep-ready-trend" collapsible={false}>
        {trend.length ? (
          <div className="tc-chart" role="img" aria-label={`Pass rate theo ngày: ${d.trend.map((t: Data) => `${t.day} ${p1(t.rate)}`).join(", ")}`}>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trend} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="day" tickLine={false} axisLine={false} fontSize={12} minTickGap={12} />
                <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} unit="%" />
                <Tooltip content={<ChartTip digits={1} />} />
                <ReferenceLine y={d.min_pass_rate} stroke="var(--muted)" strokeDasharray="4 4" />
                <Line dataKey="rate" name="% passed" stroke="var(--good)" strokeWidth={2.5} dot={{ r: 3 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="small muted tc-report-empty">Chưa có kết quả nào.</p>
        )}
      </Card>
      <div className="grid two">
        <Card title="Test ưu tiên cao Failed / chưa chạy" id="rep-ready-risky" collapsible={false}>
          <Table head={["Test", "Ưu tiên", "Trạng thái", "Run"]} empty={!d.risky_tests.length}>
            {d.risky_tests.map((t: Data) => (
              <tr key={t.test_id}>
                <td>
                  <span className="tc-codes">{t.case_key ?? t.test_key}</span> {t.title}
                </td>
                <td>{PRIORITY[t.priority] ?? t.priority}</td>
                <td>
                  <StatusCell status={t.status} meta={meta} />
                </td>
                <td className="tc-codes">{t.run_key}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="Bug của milestone" id="rep-ready-bugs" collapsible={false}>
          <Table head={["Bug", "Trạng thái"]} empty={!d.bugs.length}>
            {d.bugs.map((b: Data) => (
              <tr key={b.key}>
                <td>
                  <IssueLink k={b.key} url={b.url} /> {b.title}
                </td>
                <td>
                  <Pill tone={b.state === "open" ? "warn" : b.state === "closed" ? "good" : undefined}>{BUG_STATE[b.state] ?? b.state}</Pill>
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    </>
  );
}

/* ---------- 10. schedules ---------- */

interface Schedule {
  id: number;
  name: string;
  kind: Kind;
  params: Record<string, unknown>;
  cadence: "daily" | "weekly";
  weekday: number | null;
  hour: number;
  webhook_id: number | null;
  webhook_name: string | null;
  enabled: boolean;
  last_sent_at: string | null;
}
interface Hook {
  id: number;
  name: string;
  format: string;
  enabled: boolean;
}
const SCHEDULE_KINDS: Kind[] = ["summary", "readiness", "coverage", "defects", "automation"];
const EMPTY_FORM = { name: "", kind: "summary" as Kind, cadence: "weekly" as "daily" | "weekly", weekday: "0", hour: "8", webhook_id: "", milestone_id: "", suite_id: "" };

function Schedules({ projectId, options }: { projectId: number; options: Options }) {
  const list = useApi<Schedule[]>(`/testcases/report-schedules?business_project_id=${projectId}`);
  const hooks = useApi<Hook[]>(`/testcases/report-schedules/webhooks?business_project_id=${projectId}`);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const kindName = useMemo(() => Object.fromEntries(REPORTS.map((r) => [r.kind, r.title])), []);

  const run = async (what: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await what();
      setMsg({ tone: "success", text: ok });
      await list.mutate();
    } catch (e) {
      setMsg({ tone: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };
  const create = () =>
    run(async () => {
      const params: Record<string, number> = {};
      if (form.milestone_id) params.milestone_id = Number(form.milestone_id);
      if (form.suite_id) params.suite_id = Number(form.suite_id);
      await api.post("/testcases/report-schedules", {
        business_project_id: projectId,
        name: form.name.trim(),
        kind: form.kind,
        cadence: form.cadence,
        weekday: Number(form.weekday),
        hour: Number(form.hour),
        webhook_id: form.webhook_id ? Number(form.webhook_id) : null,
        params,
      });
      setForm(EMPTY_FORM);
    }, "Đã tạo lịch gửi");

  return (
    <Card title="Lịch gửi báo cáo" subtitle="Gửi bản tóm tắt qua webhook của dự án (Slack, Teams hoặc JSON; Admin › Webhook), theo giờ của hệ thống báo cáo" id="rep-schedules" className="no-print">
      {msg && (
        <Alert tone={msg.tone} onClose={() => setMsg(null)}>
          {msg.text}
        </Alert>
      )}
      {!list.data ? (
        <Loading error={list.error} rows={2} />
      ) : list.data.length ? (
        <Table head={["Tên", "Báo cáo", "Lịch", "Gửi tới", "Lần gửi gần nhất", ""]}>
          {list.data.map((s) => (
            <tr key={s.id}>
              <td>
                {s.name} {!s.enabled && <Pill>Tắt</Pill>}
              </td>
              <td>{kindName[s.kind] ?? s.kind}</td>
              <td>
                {s.cadence === "daily" ? "Hằng ngày" : `${WEEKDAYS[s.weekday ?? 0]} hằng tuần`}, {String(s.hour).padStart(2, "0")}:00
              </td>
              <td>{s.webhook_name ?? <span className="muted">Chưa chọn</span>}</td>
              <td className="small">{s.last_sent_at ? new Date(s.last_sent_at).toLocaleString("vi-VN") : "—"}</td>
              <td className="tc-row-actions">
                <button type="button" className="ghost sm" disabled={busy || !s.webhook_id} onClick={() => void run(() => api.post(`/testcases/report-schedules/${s.id}/send`), "Đã xếp lịch gửi ngay")}>
                  Gửi ngay
                </button>
                <button type="button" className="ghost sm" disabled={busy} onClick={() => void run(() => api.patch(`/testcases/report-schedules/${s.id}`, { enabled: !s.enabled }), s.enabled ? "Đã tắt lịch" : "Đã bật lịch")}>
                  {s.enabled ? "Tắt" : "Bật"}
                </button>
                <button
                  type="button"
                  className="ghost sm danger"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`Xoá lịch “${s.name}”?`)) void run(() => api.delete(`/testcases/report-schedules/${s.id}`), "Đã xoá lịch");
                  }}
                >
                  Xoá
                </button>
              </td>
            </tr>
          ))}
        </Table>
      ) : (
        <p className="small muted">Chưa có lịch gửi nào.</p>
      )}
      <form
        className="tc-schedule-form"
        onSubmit={(e) => {
          e.preventDefault();
          void create();
        }}
      >
        <label className="filter">
          <span className="small muted">Tên</span>
          <input required maxLength={200} value={form.name} placeholder="VD: Tổng kết tuần QA" onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="filter">
          <span className="small muted">Báo cáo</span>
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Kind })}>
            {SCHEDULE_KINDS.map((k) => (
              <option key={k} value={k}>
                {kindName[k]}
              </option>
            ))}
          </select>
        </label>
        <Select label={form.kind === "readiness" ? "Milestone (bắt buộc)" : "Milestone"} value={form.milestone_id} onChange={(v) => setForm({ ...form, milestone_id: v })} all={form.kind === "readiness" ? "Chọn" : "Tất cả"} items={options.milestones.map((m) => ({ value: String(m.id), label: m.name }))} />
        {form.kind !== "readiness" && <Select label="Bộ" value={form.suite_id} onChange={(v) => setForm({ ...form, suite_id: v })} items={options.suites.map((s) => ({ value: String(s.id), label: s.name }))} />}
        <label className="filter">
          <span className="small muted">Tần suất</span>
          <select value={form.cadence} onChange={(e) => setForm({ ...form, cadence: e.target.value as "daily" | "weekly" })}>
            <option value="weekly">Hằng tuần</option>
            <option value="daily">Hằng ngày</option>
          </select>
        </label>
        {form.cadence === "weekly" && (
          <label className="filter">
            <span className="small muted">Thứ</span>
            <select value={form.weekday} onChange={(e) => setForm({ ...form, weekday: e.target.value })}>
              {WEEKDAYS.map((w, i) => (
                <option key={w} value={i}>
                  {w}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="filter">
          <span className="small muted">Giờ</span>
          <select value={form.hour} onChange={(e) => setForm({ ...form, hour: e.target.value })}>
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {String(h).padStart(2, "0")}:00
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Gửi tới webhook</span>
          <select required value={form.webhook_id} onChange={(e) => setForm({ ...form, webhook_id: e.target.value })}>
            <option value="">{hooks.data && !hooks.data.length ? "Chưa có webhook (Admin › Webhook)" : "Chọn webhook"}</option>
            {(hooks.data ?? []).map((h) => (
              <option key={h.id} value={h.id} disabled={!h.enabled}>
                {h.name} ({h.format}){h.enabled ? "" : " - đang tắt"}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary sm" disabled={busy || !form.name.trim() || !form.webhook_id || (form.kind === "readiness" && !form.milestone_id)}>
          <Icon name="plus" size={14} /> Thêm lịch
        </button>
      </form>
    </Card>
  );
}
