"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { DateRangePicker } from "@/components/DateRangePicker";
import { Icon } from "@/components/icons";
import { Alert, Card, EmptyState, Kpi, Loading, PageHeader, Pager, PersonLink, Pill, Segmented, SeverityBadge, SortTh, useSort } from "@/components/ui";
import { can, qs, useApi } from "@/lib/api";
import { dateTime, localDay, rangeLabel, relative, shiftDays, shiftMonths, thisMonth } from "@/lib/format";
import type { Me, Person, Repo, Severity, Team } from "@/lib/types";

/* ---- API shapes (docs/specs/26 §13.5). Read defensively: the numbers may be missing or null. ---- */

type Rate = number | null;

interface RequestCounts {
  closed: number;
  unmet: number;
  rejected: number;
  pending: number;
  cancelled: number;
  failed: number;
}

interface MemberRow {
  user_id: number | null;
  name: string | null;
  email: string | null;
  avatar_url?: string | null;
  calls: number;
  active_days: number;
  findings_read: number;
  error_rate: Rate;
  rate_limited: number;
  requests: RequestCounts;
  first_pass_rate: Rate;
  resubmits: number;
  median_hours_to_request: number | null;
  median_hours_to_close: number | null;
  with_tests_rate: Rate;
  tests_pass_rate: Rate;
  backlog: number;
  web: { requests: number; closed: number; pass_rate: Rate };
}

interface Kpis {
  calls: number;
  active_users: number;
  requests: number;
  first_pass_rate: Rate;
  median_hours_to_close: number | null;
  /** The same numbers for the previous period: `prev: {…}`, or flat `prev_calls`, … (either is read). */
  prev?: Partial<Kpis> | null;
  [key: string]: unknown;
}

interface Summary {
  period: { from: string; to: string };
  previous: { from: string; to: string; kpis?: Partial<Kpis> };
  /** What the server applied (names resolved), for the scope line. */
  filters?: {
    business_project_id: number | null;
    business_project_name: string | null;
    project_id: number | null;
    project_path: string | null;
    team_id: number | null;
    team_name: string | null;
    user_id: number | null;
    severity: string[];
  };
  kpis: Kpis;
  /** Pre-sorted by calls, most first. */
  members: MemberRow[];
  /** Same shape as a row: user_id null, name "Tổng". */
  total: Partial<MemberRow> | null;
  /** "Not part of DPI" wording from the server; the page has its own when absent. */
  note?: string;
}

interface UserRef {
  id: number;
  name: string | null;
  email: string | null;
}

interface RequestRow {
  id: number;
  created_at: string;
  decided_at: string | null;
  status: string;
  closed_as: string | null;
  /** The check's reason; null when the viewer cannot see the review. */
  reason: string | null;
  /** 1 for the first MCP request on this finding, 2+ for a resubmit. */
  attempt: number | null;
  finding: { id: number; severity: Severity | null; title: string | null; file_path: string | null; line_start: number | null };
  review_id: number | null;
  project: { id: number; path: string } | null;
  user: UserRef | null;
  commits: string[];
  /** A count of files. */
  files_changed: number | null;
  tests: TestSummary | null;
  token_name: string | null;
  client: string | null;
}

interface TestSummary {
  total: number;
  passed: number;
  failed: number;
  not_run: number;
}

interface CallRow {
  id: number;
  created_at: string;
  user: UserRef | null;
  token_id: number | null;
  token_name: string | null;
  tool: string;
  ok: boolean;
  error_code: string | null;
  duration_ms: number | null;
  project: { id: number; path: string } | null;
  finding_id: number | null;
  closure_id: number | null;
  commit_sha: string | null;
  result_count: number | null;
  client: string | null;
}

/** One page of a log: `items` (or `rows`), the total and the page size (50, §13.5). */
interface Paged<T> {
  items?: T[];
  rows?: T[];
  total?: number;
  page?: number;
  page_size?: number;
}

const TOOLS = ["list_findings", "get_commit_findings", "get_mr_findings", "get_finding", "request_closure", "list_close_requests", "cancel_close_request"];

/** Final outcome of a close request: a short chip label, the full wording on hover. */
const OUTCOMES: { key: keyof RequestCounts; label: string; long: string; tone?: "good" | "bad" | "warn" | "info" }[] = [
  { key: "closed", label: "đóng", long: "đã đóng", tone: "good" },
  { key: "unmet", label: "chưa đạt", long: "chưa đạt", tone: "bad" },
  { key: "rejected", label: "từ chối", long: "bị từ chối", tone: "bad" },
  { key: "pending", label: "chờ", long: "đang chờ (chờ duyệt, đang kiểm, đề xuất)", tone: "info" },
  { key: "failed", label: "lỗi kiểm", long: "lỗi khi kiểm", tone: "warn" },
  { key: "cancelled", label: "rút", long: "đã rút" },
];

const STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  pending: { label: "Chờ duyệt", tone: "info" },
  checking: { label: "Đang kiểm", tone: "info" },
  proposed: { label: "Đề xuất chấp nhận rủi ro", tone: "warn" },
  unmet: { label: "Chưa đạt", tone: "bad" },
  closed: { label: "Đã đóng", tone: "good" },
  rejected: { label: "Bị từ chối", tone: "bad" },
  failed: { label: "Lỗi kiểm", tone: "warn" },
  cancelled: { label: "Đã rút" },
  reopened: { label: "Mở lại", tone: "warn" },
};

/* ---- small helpers ---- */

const n0 = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const nOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
/** A 0..1 share as "42%"; "—" when the denominator was zero (null). */
const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);
const count = (v: unknown) => n0(v).toLocaleString("vi-VN");
const requestTotal = (r: Partial<RequestCounts> | undefined) => OUTCOMES.reduce((s, o) => s + n0(r?.[o.key]), 0);

/** A median in hours as "45 phút", "6 giờ", "2,5 ngày"; "—" without one. */
function hours(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value < 1) return `${Math.max(1, Math.round(value * 60))} phút`;
  if (value < 48) return `${(Math.round(value * 10) / 10).toLocaleString("vi-VN")} giờ`;
  return `${(Math.round((value / 24) * 10) / 10).toLocaleString("vi-VN")} ngày`;
}

/** The previous period's value of one KPI, wherever the response puts it. */
function prevKpi(s: Summary, key: keyof Kpis): number | null {
  const k = s.kpis as Record<string, unknown>;
  const nested = (s.kpis.prev ?? s.previous?.kpis ?? null) as Record<string, unknown> | null;
  return nOrNull(nested?.[key]) ?? nOrNull(k[`prev_${String(key)}`]) ?? nOrNull(k[`previous_${String(key)}`]);
}

const testsOf = (row: RequestRow): TestSummary | null => (row.tests && n0(row.tests.total) ? row.tests : null);

/* ---- URL state ---- */

function readParam(name: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(name) ?? "";
}

/** One query parameter as state (read on arrival and on Back / Forward, written with replaceState). */
function useUrlParam(name: string): [string, (v: string) => void] {
  const [value, setValue] = useState("");
  useEffect(() => {
    const read = () => setValue(readParam(name));
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [name]);
  const update = useCallback(
    (next: string) => {
      setValue(next);
      const url = new URL(window.location.href);
      if (next) url.searchParams.set(name, next);
      else url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    },
    [name],
  );
  return [value, update];
}

type Preset = "7" | "30" | "90" | "month" | "last_month" | "custom";
interface Range {
  preset: Preset;
  from: string;
  to: string;
}
const PRESETS: Exclude<Preset, "custom">[] = ["7", "30", "90", "month", "last_month"];

function presetRange(p: Exclude<Preset, "custom">): { from: string; to: string } {
  const today = localDay();
  if (p === "month") return { from: thisMonth(), to: today };
  if (p === "last_month") {
    const first = thisMonth();
    return { from: shiftMonths(first, -1), to: shiftDays(first, -1) };
  }
  return { from: shiftDays(today, -(Number(p) - 1)), to: today };
}

/** ?range=7|30|90|month|last_month (rolling) or ?from=&to= (custom); default the last 30 days (§13.2). */
function useReportRange(): [Range | null, (r: Range) => void] {
  const [range, setRange] = useState<Range | null>(null);
  useEffect(() => {
    const read = () => {
      const p = new URLSearchParams(window.location.search);
      const preset = PRESETS.find((x) => x === p.get("range"));
      const from = p.get("from") ?? "";
      const to = p.get("to") ?? "";
      if (preset) setRange({ preset, ...presetRange(preset) });
      else if (/^\d{4}-\d{2}-\d{2}$/.test(from) && /^\d{4}-\d{2}-\d{2}$/.test(to) && from <= to) setRange({ preset: "custom", from, to });
      else setRange({ preset: "30", ...presetRange("30") });
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  const update = useCallback((next: Range) => {
    setRange(next);
    const url = new URL(window.location.href);
    for (const key of ["range", "from", "to"]) url.searchParams.delete(key);
    if (next.preset === "custom") {
      url.searchParams.set("from", next.from);
      url.searchParams.set("to", next.to);
    } else url.searchParams.set("range", next.preset);
    window.history.replaceState(null, "", url);
  }, []);
  return [range, update];
}

function RangeFilter({ value, onChange }: { value: Range | null; onChange: (r: Range) => void }) {
  if (!value) return null;
  return (
    <div className="range-picker mcp-range">
      <Segmented<Preset>
        label="Khoảng thời gian"
        value={value.preset}
        onChange={(preset) => onChange(preset === "custom" ? { ...value, preset } : { preset, ...presetRange(preset) })}
        options={[
          { value: "7", label: "7 ngày" },
          { value: "30", label: "30 ngày" },
          { value: "90", label: "90 ngày" },
          { value: "month", label: "Tháng này" },
          { value: "last_month", label: "Tháng trước" },
          { value: "custom", label: "Tuỳ chọn" },
        ]}
      />
      {value.preset === "custom" && (
        <DateRangePicker label="Thời gian" value={{ from: value.from, to: value.to }} max={localDay()} maxSpan={366} onChange={(r) => onChange({ preset: "custom", ...r })} />
      )}
    </div>
  );
}

type Tab = "members" | "requests" | "calls";

/**
 * Báo cáo Claude Code (docs/specs/26 §13): how people use the MCP flow and how well the fixes it
 * sends land. KPIs against the previous period, one row per member, the close requests sent
 * through MCP and (admins) every tool call. Scope: `report.mcp`; not part of DPI or review scores.
 */
export default function McpReportPage() {
  const [range, setRange] = useReportRange();
  const [tabParam, setTab] = useUrlParam("tab");
  const [bp, setBp] = useUrlParam("bp");
  const [repo, setRepo] = useUrlParam("repo");
  const [teamId, setTeamId] = useUrlParam("team");
  const [user, setUser] = useUrlParam("user");
  const [severity, setSeverity] = useUrlParam("severity");

  const me = useApi<Me>("/auth/me");
  const scope = me.data?.is_admin ? "all" : me.data?.permissions?.["report.mcp"];
  const ownOnly = scope === "own";
  const isAdmin = !!me.data?.is_admin;
  const tab: Tab = tabParam === "requests" || (tabParam === "calls" && isAdmin) ? tabParam : "members";

  const people = useApi<Person[]>(!ownOnly && scope ? "/people" : null);
  const repos = useApi<Repo[]>(scope ? "/repos" : null);
  const teams = useApi<Team[]>(!ownOnly && scope ? "/teams/visible" : null);
  const projects = useMemo(() => {
    const seen = new Map<number, string>();
    for (const r of repos.data ?? []) if (r.business_project_id !== null) seen.set(r.business_project_id, r.business_project ?? `#${r.business_project_id}`);
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]));
  }, [repos.data]);
  const repoOptions = (repos.data ?? [])
    .filter((r) => !bp || String(r.business_project_id) === bp)
    .sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));

  const filters =
    range && scope
      ? {
          from: range.from,
          to: range.to,
          business_project_id: bp || null,
          project_id: repo || null,
          team_id: ownOnly ? null : teamId || null,
          user_id: ownOnly ? null : user || null,
          severity: severity || null,
        }
      : null;
  const filterKey = filters ? qs(filters) : null;
  const summary = useApi<Summary>(filterKey !== null ? `/mcp-report/summary${filterKey}` : null, { keepPreviousData: true });
  const exportHref = filterKey !== null && summary.data ? `/api/v1/mcp-report/export.xlsx${filterKey}` : undefined;

  const tabs: SubTab<Tab>[] = [
    { key: "members", label: "Theo thành viên", count: summary.data?.members.length, hint: "Sử dụng, hiệu quả sửa, test, backlog của từng người" },
    { key: "requests", label: "Yêu cầu đóng qua MCP", hint: "Từng yêu cầu đóng gửi qua Claude Code" },
    ...(isAdmin ? [{ key: "calls", label: "Nhật ký gọi", hint: "Mọi lần gọi tool (chỉ admin)" } as SubTab<Tab>] : []),
  ];

  if (me.data && !scope) {
    return (
      <>
        <PageHeader title="Báo cáo Claude Code" foldable={false} />
        <Card collapsible={false}>
          <EmptyState icon="alert" title="Bạn chưa có quyền xem báo cáo Claude Code">
            Quyền &ldquo;Xem báo cáo Claude Code&rdquo; (report.mcp) do admin cấp ở Admin › Phân quyền.
          </EmptyState>
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Báo cáo Claude Code"
        subtitle="Ai dùng Claude Code (MCP) để sửa finding, và yêu cầu đóng gửi qua đó đạt đến đâu."
        foldable={false}
        actions={
          <span className="no-print report-actions">
            {exportHref ? (
              <a className="button" href={exportHref} download>
                <Icon name="download" size={14} /> Xuất Excel
              </a>
            ) : (
              <button type="button" disabled>
                <Icon name="download" size={14} /> Xuất Excel
              </button>
            )}
          </span>
        }
      />

      <div className="report-bar no-print">
        <div className="report-filters" role="group" aria-label="Bộ lọc báo cáo">
          <div className="report-filter report-range">
            <span className="small muted">Thời gian</span>
            <RangeFilter value={range} onChange={setRange} />
          </div>
          <label className="report-filter">
            <span className="small muted">Dự án</span>
            <select
              value={bp}
              onChange={(e) => {
                setBp(e.target.value);
                setRepo("");
              }}
              aria-label="Lọc theo dự án"
            >
              <option value="">Mọi dự án</option>
              {projects.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="report-filter">
            <span className="small muted">Repo</span>
            <select value={repo} onChange={(e) => setRepo(e.target.value)} aria-label="Lọc theo repo">
              <option value="">{bp ? "Mọi repo của dự án" : "Mọi repo"}</option>
              {repoOptions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.path_with_namespace}
                </option>
              ))}
            </select>
          </label>
          {!ownOnly && (
            <>
              <label className="report-filter">
                <span className="small muted">Team</span>
                <select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Lọc theo team">
                  <option value="">Mọi team</option>
                  {(teams.data ?? []).map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="report-filter">
                <span className="small muted">Thành viên</span>
                <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="Lọc theo thành viên">
                  <option value="">Mọi người</option>
                  {(people.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.full_name ?? p.primary_email ?? `#${p.id}`}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label className="report-filter">
            <span className="small muted">Mức độ</span>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Lọc theo mức độ">
              <option value="">Critical và high</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
            </select>
          </label>
        </div>
      </div>

      {!summary.data ? (
        summary.error ? (
          <Card collapsible={false} className="mcp-kpis">
            <ErrorBlock error={summary.error} onRetry={() => void summary.mutate()} />
          </Card>
        ) : (
          <KpiSkeleton />
        )
      ) : (
        <div className={summary.isLoading ? "eval-dim" : undefined}>
          {summary.error && <Alert tone="error">Không tải được bộ lọc mới: {summary.error.message}.</Alert>}
          <p className="muted small report-scope">
            {rangeLabel(summary.data.period.from, summary.data.period.to)}
            {summary.data.filters && <> · {scopeText(summary.data.filters)}</>}
            {summary.data.previous && <> · so với {rangeLabel(summary.data.previous.from, summary.data.previous.to)}</>}
          </p>
          <KpiTiles data={summary.data} />
        </div>
      )}

      <SubTabs<Tab> id="mcp-tab" label="Phần của báo cáo" variant="bar" tabs={tabs} value={tab} onChange={setTab} />
      {tab === "members" && (
        <SubPanel id="mcp-tab" tab="members">
          <MembersTable summary={summary.data} error={summary.error} search={range ? `?from=${range.from}&to=${range.to}` : ""} />
        </SubPanel>
      )}
      {tab === "requests" && filterKey !== null && (
        <SubPanel id="mcp-tab" tab="requests">
          <RequestsLog filters={filters ?? {}} />
        </SubPanel>
      )}
      {tab === "calls" && isAdmin && filterKey !== null && (
        <SubPanel id="mcp-tab" tab="calls">
          <CallsLog filters={filters ?? {}} />
        </SubPanel>
      )}

      <p className="muted small mcp-note">
        <Icon name="info" size={14} />{" "}
        {summary.data?.note ??
          "Các chỉ số này là tín hiệu coaching, không cộng vào điểm DPI hay điểm review: dùng hay không dùng Claude Code không làm ai bị trừ điểm."}
      </p>
    </>
  );
}

/** "CRM / omicx/crm-api · team Backend · critical": what the numbers cover, as the server applied it. */
function scopeText(f: NonNullable<Summary["filters"]>): string {
  const parts = [
    (f.business_project_name ?? "Mọi dự án") + (f.project_path ? ` / ${f.project_path}` : ""),
    f.team_name ? `team ${f.team_name}` : null,
    f.severity?.length ? f.severity.join(", ") : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

/* ---- states ---- */

function ErrorBlock({ error, onRetry }: { error: Error & { status?: number }; onRetry: () => void }) {
  return (
    <div className="mcp-error">
      <Alert tone="error">
        Không tải được dữ liệu: {error.message}
        {error.status ? <> (mã {error.status})</> : null}.
      </Alert>
      <button type="button" className="sm" onClick={onRetry}>
        <Icon name="sync" size={14} /> Thử lại
      </button>
    </div>
  );
}

function KpiSkeleton() {
  return (
    <div className="grid kpis mcp-kpis" aria-busy="true">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="card kpi">
          <Loading rows={3} />
        </div>
      ))}
    </div>
  );
}

/* ---- KPI tiles (§13.3) ---- */

function KpiTiles({ data }: { data: Summary }) {
  const k = data.kpis;
  const ch = (now: number | null, prev: number | null, scale = 1, digits = 0) =>
    now === null || prev === null ? null : Math.round((now - prev) * scale * 10 ** digits) / 10 ** digits;
  const firstPass = nOrNull(k.first_pass_rate);
  const close = nOrNull(k.median_hours_to_close);
  const prevClose = prevKpi(data, "median_hours_to_close");
  const membersShown = data.members.length;
  return (
    <div className="grid kpis mcp-kpis">
      <Kpi label="Lần gọi tool" value={n0(k.calls)} digits={0} change={ch(n0(k.calls), prevKpi(data, "calls"))} hint={`${count(data.total?.findings_read)} finding đã đọc`} />
      <Kpi
        label="Người dùng hoạt động"
        value={n0(k.active_users)}
        digits={0}
        change={ch(n0(k.active_users), prevKpi(data, "active_users"))}
        hint={`trên ${count(membersShown)} người trong phạm vi`}
      />
      <Kpi
        label="Yêu cầu đóng qua MCP"
        value={n0(k.requests)}
        digits={0}
        change={ch(n0(k.requests), prevKpi(data, "requests"))}
        hint={`web: ${count(data.total?.web?.requests)} yêu cầu`}
      />
      <Kpi
        label="Đạt ngay lần đầu"
        value={firstPass === null ? null : firstPass * 100}
        digits={0}
        unit={firstPass === null ? undefined : "%"}
        change={ch(firstPass, prevKpi(data, "first_pass_rate"), 100)}
        hint={`web: ${pct(nOrNull(data.total?.web?.pass_rate))} đạt`}
      />
      <div className="card kpi">
        <div className="label">Trung vị đến khi đóng</div>
        <div className="value">{hours(close)}</div>
        <div className={`delta ${close !== null && prevClose !== null && close !== prevClose ? (close < prevClose ? "up" : "down") : ""}`}>
          {prevClose === null ? (
            <span className="muted">Chưa có kỳ trước</span>
          ) : (
            <>
              <span className="chip">
                <Icon name={close === null || close === prevClose ? "minus" : close > prevClose ? "trendUp" : "trendDown"} size={12} />
                {hours(prevClose)}
              </span>
              <span>kỳ trước</span>
            </>
          )}
        </div>
        <div className="muted small">từ lúc có finding đến khi đóng</div>
      </div>
    </div>
  );
}

/* ---- Theo thành viên ---- */

type SortKey =
  | "name"
  | "calls"
  | "active_days"
  | "findings_read"
  | "error_rate"
  | "requests"
  | "first_pass_rate"
  | "resubmits"
  | "to_request"
  | "to_close"
  | "with_tests"
  | "tests_pass"
  | "backlog"
  | "web";

function sortValue(r: MemberRow, key: string): number | string | null {
  switch (key as SortKey) {
    case "name":
      return r.name ?? r.email ?? "";
    case "requests":
      return requestTotal(r.requests);
    case "to_request":
      return nOrNull(r.median_hours_to_request);
    case "to_close":
      return nOrNull(r.median_hours_to_close);
    case "with_tests":
      return nOrNull(r.with_tests_rate);
    case "tests_pass":
      return nOrNull(r.tests_pass_rate);
    case "web":
      return n0(r.web?.requests);
    default:
      return nOrNull((r as unknown as Record<string, unknown>)[key]);
  }
}

function Outcomes({ counts }: { counts: Partial<RequestCounts> | undefined }) {
  const shown = OUTCOMES.filter((o) => n0(counts?.[o.key]) > 0);
  if (!shown.length) return <span className="muted">—</span>;
  return (
    <span className="mcp-outcomes">
      {shown.map((o) => (
        <Pill key={o.key} tone={o.tone} title={`${n0(counts?.[o.key])} yêu cầu ${o.long}`}>
          {n0(counts?.[o.key])} {o.label}
        </Pill>
      ))}
    </span>
  );
}

function rateCell(v: unknown, bad?: (x: number) => boolean) {
  const x = nOrNull(v);
  return <span className={x !== null && bad?.(x) ? "tone-bad-text" : undefined}>{pct(x)}</span>;
}

function MemberCells({ r }: { r: Partial<MemberRow> }) {
  return (
    <>
      <td className="num">
        {count(r.calls)}
        <span className="muted small mcp-sub">{count(r.active_days)} ngày</span>
      </td>
      <td className="num">{count(r.findings_read)}</td>
      <td className="num">
        {rateCell(r.error_rate, (x) => x >= 0.2)}
        {n0(r.rate_limited) > 0 && (
          <span className="muted small mcp-sub" title="Số lần bị giới hạn 60 lần/phút (không tính vào tỉ lệ lỗi)">
            {count(r.rate_limited)} lần bị chặn
          </span>
        )}
      </td>
      <td className="mcp-req">
        <strong>{count(requestTotal(r.requests))}</strong>
        {requestTotal(r.requests) > 0 && <Outcomes counts={r.requests} />}
      </td>
      <td className="num">
        {rateCell(r.first_pass_rate)}
        {n0(r.resubmits) > 0 && (
          <span className="muted small mcp-sub" title="Yêu cầu thứ 2 trở đi cho cùng finding">{count(r.resubmits)} gửi lại</span>
        )}
      </td>
      <td className="num">
        {nOrNull(r.median_hours_to_request) === null && nOrNull(r.median_hours_to_close) === null ? (
          <span className="muted">—</span>
        ) : (
          <>
            <span title="Trung vị từ lúc review xong đến khi gửi yêu cầu">gửi {hours(nOrNull(r.median_hours_to_request))}</span>
            <span className="mcp-sub" title="Trung vị từ lúc review xong đến khi đóng">đóng {hours(nOrNull(r.median_hours_to_close))}</span>
          </>
        )}
      </td>
      <td className="num">
        {nOrNull(r.with_tests_rate) === null ? (
          <span className="muted">—</span>
        ) : (
          <>
            <span title="Tỉ lệ yêu cầu có kèm test">{rateCell(r.with_tests_rate)} có</span>
            <span className="mcp-sub" title="Tỉ lệ yêu cầu mọi test đều passed">{rateCell(r.tests_pass_rate)} pass</span>
          </>
        )}
      </td>
      <td className="num">
        <span className={n0(r.backlog) ? "tone-warn" : undefined}>{count(r.backlog)}</span>
      </td>
      <td className="num">
        {count(r.web?.requests)}
        {n0(r.web?.requests) > 0 && <span className="muted small mcp-sub">{pct(nOrNull(r.web?.pass_rate))} đạt</span>}
      </td>
    </>
  );
}

function MembersTable({ summary, error, search }: { summary?: Summary; error?: Error; search: string }) {
  const rows = summary?.members ?? [];
  const { sorted, sort, toggle } = useSort(rows, { key: "calls", dir: "desc" }, sortValue);
  if (!summary)
    return (
      <Card collapsible={false}>{error ? <p className="muted small mcp-pad">Bảng theo thành viên chưa tải được (xem lỗi phía trên).</p> : <Loading rows={6} />}</Card>
    );
  if (!rows.length)
    return (
      <Card collapsible={false}>
        <EmptyState icon="inbox" title="Chưa có ai dùng Claude Code trong khoảng này">
          Đổi khoảng thời gian hoặc bộ lọc. Cách kết nối: <Link href="/guide#mcp">Hướng dẫn › Kết nối Claude Code (MCP)</Link>.
        </EmptyState>
      </Card>
    );
  const th = (id: SortKey, label: string, numeric = true, title?: string) => (
    <SortTh id={id} sort={sort} onSort={toggle} num={numeric}>
      <span title={title}>{label}</span>
    </SortTh>
  );
  return (
    <Card flush collapsible={false}>
      <div className="table-wrap">
        <table className="mcp-members">
          <thead>
            <tr className="mcp-groups">
              <th />
              <th colSpan={3}>Sử dụng</th>
              <th colSpan={3}>Hiệu quả sửa (yêu cầu qua MCP)</th>
              <th />
              <th />
              <th />
            </tr>
            <tr>
              {th("name", "Thành viên", false)}
              {th("calls", "Lần gọi", true, "Số lần gọi tool; dòng dưới: số ngày hoạt động")}
              {th("findings_read", "Đã đọc", true, "Số finding khác nhau đã đọc qua get_*")}
              {th("error_rate", "Tỉ lệ lỗi", true, "Lần gọi lỗi / tổng, không tính lần chạm giới hạn")}
              {th("requests", "Yêu cầu theo kết quả", false, "Yêu cầu đóng qua MCP tạo trong kỳ, theo kết quả cuối")}
              {th("first_pass_rate", "Đạt lần đầu", true, "Finding đóng ngay ở yêu cầu MCP đầu tiên; dòng dưới: số lần gửi lại (yêu cầu thứ 2 trở đi cho cùng finding)")}
              {th("to_close", "Thời gian", true, "Trung vị từ lúc review xong đến khi gửi yêu cầu, và đến khi đóng")}
              {th("with_tests", "Test", true, "Tỉ lệ có kèm test; dòng dưới: tỉ lệ mọi test đều pass")}
              {th("backlog", "Backlog", true, "Critical/high còn mở hiện tại (không theo thời gian)")}
              {th("web", "Qua web", true, "Yêu cầu đóng gửi trên web cùng kỳ và tỉ lệ đạt")}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.user_id ?? `x-${r.email}`}>
                <td>
                  {r.user_id !== null ? (
                    <PersonLink id={r.user_id} name={r.name} email={r.email} avatar={r.avatar_url} search={search} />
                  ) : (
                    <span className="muted">{r.name ?? r.email ?? "(không rõ)"}</span>
                  )}
                </td>
                <MemberCells r={r} />
              </tr>
            ))}
          </tbody>
          {summary.total && (
            <tfoot>
              <tr className="mcp-total">
                <td>
                  <strong>Tổng ({count(rows.length)} người)</strong>
                </td>
                <MemberCells r={summary.total} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </Card>
  );
}

/* ---- Yêu cầu đóng qua MCP ---- */

type Filters = Record<string, string | number | null | undefined>;

function usePage(filters: Filters, extra: Filters = {}) {
  const [page, setPage] = useState(1);
  const key = qs({ ...filters, ...extra });
  // A new filter starts again on page 1.
  useEffect(() => setPage(1), [key]);
  return { page, setPage, path: (base: string) => `${base}${qs({ ...filters, ...extra, page })}` };
}

function LogPager<T>({ data, page, setPage }: { data: Paged<T>; page: number; setPage: (p: number) => void }) {
  const size = data.page_size ?? 50;
  const total = data.total ?? null;
  const pages = total !== null ? Math.max(1, Math.ceil(total / size)) : null;
  const shown = (data.items ?? data.rows ?? []).length;
  if (page === 1 && pages === 1) return null;
  const from = (page - 1) * size + 1;
  return (
    <div className="mcp-pager">
      <span className="muted small">
        {shown ? `${count(from)}–${count(from + shown - 1)}` : "0"}
        {total !== null ? ` / ${count(total)}` : ""}
      </span>
      <Pager
        label={pages !== null ? `Trang ${page} / ${pages}` : `Trang ${page}`}
        onPrev={() => setPage(page - 1)}
        onNext={() => setPage(page + 1)}
        prevDisabled={page <= 1}
        nextDisabled={pages !== null ? page >= pages : shown < size}
      />
    </div>
  );
}

function RequestsLog({ filters }: { filters: Filters }) {
  const { page, setPage, path } = usePage(filters);
  const res = useApi<Paged<RequestRow>>(path("/mcp-report/requests"), { keepPreviousData: true });
  const rows = res.data?.items ?? res.data?.rows ?? [];
  return (
    <Card flush collapsible={false}>
      {!res.data ? (
        <div className="adm-pad">{res.error ? <ErrorBlock error={res.error} onRetry={() => void res.mutate()} /> : <Loading rows={6} />}</div>
      ) : !rows.length ? (
        <EmptyState icon="inbox" title="Chưa có yêu cầu đóng nào gửi qua Claude Code">
          Trong khoảng và bộ lọc này không ai gửi yêu cầu đóng qua MCP.
        </EmptyState>
      ) : (
        <div className={res.isLoading ? "eval-dim" : undefined}>
          {res.error && <Alert tone="error">Không tải được trang mới: {res.error.message}.</Alert>}
          <div className="table-wrap">
            <table className="mcp-requests">
              <thead>
                <tr>
                  <th>Finding</th>
                  <th>Mức</th>
                  <th>Repo</th>
                  <th>Thành viên</th>
                  <th>Commit</th>
                  <th className="num">File</th>
                  <th>Test</th>
                  <th>Trạng thái</th>
                  <th>Lý do của bước kiểm</th>
                  <th>Thời điểm</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const f = r.finding;
                  const title = f?.title ?? (f?.id ? `Finding #${f.id}` : `Yêu cầu #${r.id}`);
                  const href = r.review_id ? `/reviews/${r.review_id}${f?.id ? `#finding-${f.id}` : ""}` : null;
                  const tests = testsOf(r);
                  const files = nOrNull(r.files_changed);
                  const st = STATUS[r.status] ?? { label: r.status };
                  return (
                    <tr key={r.id}>
                      <td className="mcp-finding">
                        {href ? <Link href={href}>{title}</Link> : title}
                        {f?.file_path && (
                          <span className="muted small mcp-sub">
                            <code>
                              {f.file_path}
                              {f.line_start ? `:${f.line_start}` : ""}
                            </code>
                          </span>
                        )}
                        {n0(r.attempt) > 1 && <span className="muted small mcp-sub">lần gửi thứ {r.attempt}</span>}
                      </td>
                      <td>{f?.severity ? <SeverityBadge severity={f.severity} /> : "—"}</td>
                      <td className="mcp-repo">{r.project ? <code>{r.project.path}</code> : <span className="muted">—</span>}</td>
                      <td>{r.user ? <Link href={`/members/${r.user.id}`}>{r.user.name ?? r.user.email ?? `#${r.user.id}`}</Link> : "—"}</td>
                      <td>
                        {r.commits?.length ? (
                          <span className="mcp-commits">
                            {r.commits.slice(0, 2).map((c) => (
                              <code key={c}>{c.slice(0, 8)}</code>
                            ))}
                            {r.commits.length > 2 && <span className="muted small">+{r.commits.length - 2}</span>}
                          </span>
                        ) : (
                          <span className="muted" title="Không có commit: gửi phản biện">—</span>
                        )}
                      </td>
                      <td className="num">{files === null ? "—" : count(files)}</td>
                      <td>
                        {tests ? (
                          <Pill tone={tests.failed ? "bad" : tests.passed === tests.total ? "good" : "warn"} title={`${tests.passed} passed · ${tests.failed} failed · ${tests.not_run} không chạy`}>
                            {tests.passed}/{tests.total} pass
                          </Pill>
                        ) : (
                          <span className="muted">không có</span>
                        )}
                      </td>
                      <td>
                        <Pill tone={st.tone}>{st.label}</Pill>
                      </td>
                      <td className="mcp-reason">{r.reason ? <span className="small">{r.reason}</span> : <span className="muted">—</span>}</td>
                      <td className="nowrap" title={dateTime(r.created_at)}>
                        <time dateTime={r.created_at}>{relative(r.created_at)}</time>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <LogPager data={res.data} page={page} setPage={setPage} />
        </div>
      )}
    </Card>
  );
}

/* ---- Nhật ký gọi (admin) ---- */

function CallsLog({ filters }: { filters: Filters }) {
  const [tool, setTool] = useState("");
  const [ok, setOk] = useState<"" | "true" | "false">("");
  const { page, setPage, path } = usePage(filters, { tool: tool || null, ok: ok || null });
  const res = useApi<Paged<CallRow>>(path("/mcp-report/calls"), { keepPreviousData: true });
  const rows = res.data?.items ?? res.data?.rows ?? [];
  return (
    <Card
      flush
      collapsible={false}
      title="Nhật ký gọi tool"
      subtitle="Mọi lần gọi, kể cả bị từ chối; giữ 180 ngày. Không lưu nội dung trả về hay tham số tự do."
      actions={
        <span className="mcp-call-filters">
          <select className="compact" value={tool} onChange={(e) => setTool(e.target.value)} aria-label="Lọc theo tool">
            <option value="">Mọi tool</option>
            {TOOLS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <Segmented<"" | "true" | "false">
            label="Kết quả"
            value={ok}
            onChange={setOk}
            options={[
              { value: "", label: "Tất cả" },
              { value: "true", label: "Thành công" },
              { value: "false", label: "Lỗi" },
            ]}
          />
        </span>
      }
    >
      {!res.data ? (
        <div className="adm-pad">{res.error ? <ErrorBlock error={res.error} onRetry={() => void res.mutate()} /> : <Loading rows={6} />}</div>
      ) : !rows.length ? (
        <EmptyState icon="inbox" title="Không có lần gọi nào">
          Đổi khoảng thời gian, tool hoặc kết quả.
        </EmptyState>
      ) : (
        <div className={res.isLoading ? "eval-dim" : undefined}>
          {res.error && <Alert tone="error">Không tải được trang mới: {res.error.message}.</Alert>}
          <div className="table-wrap">
            <table className="mcp-calls">
              <thead>
                <tr>
                  <th>Thời điểm</th>
                  <th>Thành viên</th>
                  <th>Tool</th>
                  <th>Kết quả</th>
                  <th className="num">Thời gian</th>
                  <th>Repo</th>
                  <th>Liên quan</th>
                  <th className="num">Số kết quả</th>
                  <th>Token · client</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id}>
                    <td className="nowrap">{dateTime(c.created_at)}</td>
                    <td>{c.user ? <Link href={`/members/${c.user.id}`}>{c.user.name ?? c.user.email ?? `#${c.user.id}`}</Link> : "—"}</td>
                    <td>
                      <code>{c.tool}</code>
                    </td>
                    <td>{c.ok ? <Pill tone="good">OK</Pill> : <Pill tone={c.error_code === "rate_limited" ? "warn" : "bad"}>{c.error_code ?? "lỗi"}</Pill>}</td>
                    <td className="num">{c.duration_ms === null ? "—" : `${count(c.duration_ms)} ms`}</td>
                    <td>{c.project ? <code>{c.project.path}</code> : <span className="muted">—</span>}</td>
                    <td className="small">
                      {[c.finding_id && `finding #${c.finding_id}`, c.closure_id && `yêu cầu #${c.closure_id}`, c.commit_sha && c.commit_sha.slice(0, 8)].filter(Boolean).join(" · ") || (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num">{c.result_count === null ? "—" : count(c.result_count)}</td>
                    <td className="small">
                      {c.token_name ?? "—"}
                      {c.client && <span className="muted mcp-sub">{c.client}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LogPager data={res.data} page={page} setPage={setPage} />
        </div>
      )}
    </Card>
  );
}
