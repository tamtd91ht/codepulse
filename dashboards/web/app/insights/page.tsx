"use client";

/**
 * Engineering Insights (docs/specs/20 §10): team-level flow, review load and knowledge risk.
 * Numbers describe a dự án or repo, never rank people; names show only where the API allows.
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { type SubNav, SubPanel, type SubTab, SubTabs, pickSub } from "@/components/AdminTabs";
import { DateRangePicker } from "@/components/DateRangePicker";
import { Alert, Card, ChartTip, EmptyState, ExternalLink, Loading, Pager, Pill, Segmented, SplitBar, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { date, dateTime, localDay, num, periodLabel, rangeLabel, shiftDays, shiftMonths, shiftWeeks, thisMonth, thisWeek, weekLabel, weekOf } from "@/lib/format";
import type {
  InsightConfidence,
  InsightPerson,
  InsightsFlow,
  BackfillProgress,
  BackfillStatus,
  InsightsDora,
  InsightComponents,
  InsightModule,
  InsightsKnowledge,
  Me,
  InsightsMrs,
  InsightsReviewLoad,
  InsightStage,
  Repo,
} from "@/lib/types";

type Period = "week" | "month" | "range";
type Tab = "flow" | "load" | "dora" | "knowledge";
type Only = "" | "unreviewed" | "xl";
type Query = Record<string, string | number | null | undefined>;

const TABS: { key: Tab; label: string }[] = [
  { key: "flow", label: "Luồng làm việc" },
  { key: "load", label: "Tải review" },
  { key: "dora", label: "Triển khai (DORA)" },
  { key: "knowledge", label: "Rủi ro tri thức" },
];
const STAGES: InsightStage[] = ["coding", "pickup", "review", "time_to_merge"];
const STAGE_LABEL: Record<InsightStage, string> = {
  coding: "Viết code",
  pickup: "Chờ review",
  review: "Review → merge",
  time_to_merge: "Mở → merge",
};
const STAGE_HINT: Record<InsightStage, string> = {
  coding: "Commit đầu tiên → mở MR (tác giả)",
  pickup: "MR sẵn sàng → phản hồi đầu tiên của người khác, kể cả approve (reviewer/team)",
  review: "Phản hồi đầu tiên → merge (tác giả + reviewer)",
  time_to_merge: "Mở MR → merge",
};
/** Earliest day a custom range may start, and its longest span (the API refuses beyond). */
const RANGE_MIN = "2026-04-01";
const RANGE_MAX_DAYS = 366;
const PAGE = 20;
const MAX_ROWS = 200;
const FOOTER = "Chỉ số cấp team để cải thiện quy trình, không dùng để đánh giá cá nhân.";

/** Hours as minutes, hours or days, whichever reads best. */
function hours(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  if (h < 1) return `${Math.round(h * 60)} phút`;
  if (h < 48) return `${num(h, 1)} giờ`;
  return `${num(h / 24, 1)} ngày`;
}

/** The same scale for a KPI value (number + unit), so a KPI and a table cell read alike. */
function kpiHours(h: number | null, change: number | null): { value: number | undefined; unit: string; change: number | undefined } {
  if (h === null) return { value: undefined, unit: "", change: undefined };
  const factor = h < 48 ? 1 : 24;
  return { value: h / factor, unit: factor === 1 ? "giờ" : "ngày", change: change === null ? undefined : change / factor };
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${num(v, 1)}%`);
const daysBetween = (from: string, to: string) => Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) + 1;

function Person({ p }: { p: InsightPerson }) {
  if (p.hidden) return <span className="faint" title="Bạn không có quyền xem việc của người này">{p.name}</span>;
  if (p.mapped === false) return <span className="muted" title="Tài khoản GitLab chưa gắn với người nào">{p.name}</span>;
  return <>{p.name}</>;
}

/** Gaps a re-sync fills (repos synced before Insights existed, or not synced lately). */
const FIXABLE = new Set(["no_file_data", "partial_file_data", "no_deployments", "stale_sync"]);

function ConfidenceNote({ c, repoId }: { c: InsightConfidence | undefined; repoId?: string | number | null }) {
  const { data: me } = useApi<Me>("/auth/me");
  if (!c || c.level === "ok") return null;
  const fixable = !!me?.is_admin && c.reasons.some((r) => FIXABLE.has(r.code));
  return (
    <Alert tone={c.level === "insufficient" ? "warning" : "info"}>
      <strong>{c.level === "insufficient" ? "Chưa đủ dữ liệu" : "Độ tin cậy thấp"}:</strong>{" "}
      {c.reasons.map((r) => r.message + (r.repos?.length ? ` (${r.repos.join(", ")})` : "")).join(" · ")}
      {fixable && <BackfillAction repoId={repoId ? Number(repoId) : null} />}
    </Alert>
  );
}

/** Admin: fill the Insights data of repos synced before it existed (docs/specs/20, 9.12), and
 * follow it repo by repo (polled while any is still running). */
function BackfillAction({ repoId }: { repoId: number | null }) {
  const path = `/admin/insights/backfill${repoId ? `?project_id=${repoId}` : ""}`;
  const { data, mutate } = useApi<BackfillProgress>(path, {
    refreshInterval: (latest) => (latest && latest.active > 0 ? 3000 : 0),
  });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const running = (data?.active ?? 0) > 0;
  const run = () => {
    const what = repoId ? "repo đang chọn" : "mọi repo đang theo dõi";
    if (
      !window.confirm(
        `Đồng bộ lại dữ liệu cho Insights (${what})? CodePulse dựng lại dữ liệu từ những gì đã thu thập rồi sync đầy đủ từ GitLab (deployment, sự cố); chạy nền, repo lớn có thể mất vài phút. Không tốn ngân sách LLM.`,
      )
    )
      return;
    setBusy(true);
    setNote("");
    void api
      .post<{ queued: number; running: number }>("/admin/insights/backfill", repoId ? { project_id: repoId } : {})
      .then((r) => {
        setNote(r.running ? `Xếp hàng ${r.queued} repo; ${r.running} repo đang chạy từ trước.` : `Đã xếp hàng ${r.queued} repo.`);
        return mutate();
      })
      .catch((e: unknown) => setNote(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`))
      .finally(() => setBusy(false));
  };
  const finished = (data?.done ?? 0) + (data?.failed ?? 0);
  return (
    <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
      <div className="toolbar" style={{ margin: 0 }}>
        <button type="button" className="sm" disabled={busy || running} onClick={run}>
          {running ? "Đang đồng bộ…" : busy ? "Đang xếp hàng…" : "Đồng bộ lại cho Insights"}
        </button>
        {note && (
          <span className="small" role="status">
            {note}
          </span>
        )}
      </div>
      {data && data.total > 0 && (
        <div style={{ display: "grid", gap: 6 }}>
          <div className="small" role="status" aria-live="polite">
            {running ? "Đang đồng bộ" : "Lần đồng bộ gần nhất"}: {finished}/{data.total} repo xong
            {data.failed ? ` · ${data.failed} lỗi` : ""}
            {data.reconverting + data.syncing ? ` · ${data.reconverting + data.syncing} đang chạy` : ""}
            {data.queued ? ` · ${data.queued} chờ` : ""}
          </div>
          <SplitBar
            label={`${finished}/${data.total} repo đã xong`}
            parts={[
              { name: "Xong", value: data.done, color: "var(--good)" },
              { name: "Lỗi", value: data.failed, color: "var(--sev-critical)" },
              { name: "Đang chạy", value: data.reconverting + data.syncing, color: "var(--chart-1)" },
              { name: "Chờ", value: data.queued, color: "var(--surface-2)" },
            ]}
          />
          <ul className="finding-list" style={{ maxHeight: 180, overflowY: "auto" }}>
            {data.items.map((i) => (
              <li key={i.project_id}>
                <Pill tone={BACKFILL_TONE[i.status]}>{BACKFILL_LABEL[i.status]}</Pill>
                <div className="body">
                  <span className="small">{i.path_with_namespace}</span>
                  <div className="meta">
                    {i.status === "done" && i.runs !== null ? `dựng lại ${i.runs} lần sync · ` : ""}
                    {i.finished_at ? `xong ${dateTime(i.finished_at)}` : i.started_at ? `bắt đầu ${dateTime(i.started_at)}` : i.queued_at ? `xếp hàng ${dateTime(i.queued_at)}` : ""}
                    {i.error ? ` · ${i.error}` : ""}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const BACKFILL_LABEL: Record<BackfillStatus, string> = {
  queued: "Chờ",
  reconverting: "Dựng lại dữ liệu",
  syncing: "Đồng bộ GitLab",
  done: "Xong",
  failed: "Lỗi",
};
const BACKFILL_TONE: Record<BackfillStatus, "good" | "bad" | "info" | undefined> = {
  queued: undefined,
  reconverting: "info",
  syncing: "info",
  done: "good",
  failed: "bad",
};

/** Loading skeleton, or the error with a retry. */
function Pending({ error, retry, rows = 4 }: { error?: ApiError; retry: () => void; rows?: number }) {
  return (
    <Loading error={error} rows={rows}>
      {error && (
        <button type="button" className="sm" onClick={retry}>
          Thử lại
        </button>
      )}
    </Loading>
  );
}

function HiddenNote({ people }: { people: InsightPerson[] }) {
  if (!people.some((p) => p.hidden)) return null;
  return <p className="small muted" style={{ margin: 0, padding: "8px 18px" }}>&quot;Một thành viên&quot;: người bạn không có quyền xem việc của họ; số liệu vẫn được tính.</p>;
}

const isTab = (v: string): v is Tab => TABS.some((t) => t.key === v);

/** `#flow/mrs` → ["flow", "mrs"]; the sub-tab part is optional. */
function readHash(): [string, string | null] {
  const [key, sub] = window.location.hash.replace("#", "").split("/");
  return [key ?? "", sub ? decodeURIComponent(sub) : null];
}

/**
 * The page tab and its sub-tab live in the URL hash (`#flow/mrs`), like Admin, so a link opens the
 * exact view and Back/Forward step through them. Filters stay in page state, kept across tabs.
 */
export default function InsightsPage() {
  // null until the hash is read, so a deep link never fetches the wrong tab first.
  const [tab, setTab] = useState<Tab | null>(null);
  const [sub, setSub] = useState<string | null>(null);
  const [period, setPeriod] = useState<Period>("week");
  const [cursor, setCursor] = useState<string | null>(null);
  const [range, setRange] = useState<{ from: string; to: string }>({ from: "", to: "" });
  const [bpId, setBpId] = useState("");
  const [repoId, setRepoId] = useState("");
  useEffect(() => {
    setCursor(thisWeek());
    const fromHash = () => {
      const [key, part] = readHash();
      if (isTab(key)) {
        setTab(key);
        setSub(part);
        return;
      }
      // Older links used ?tab=dora.
      let legacy: string | null = null;
      try {
        legacy = new URLSearchParams(window.location.search).get("tab");
      } catch {
        /* no URL: keep the default tab */
      }
      setTab(legacy && isTab(legacy) ? legacy : "flow");
      setSub(null);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);
  // The hash is the source of truth: setting it fires hashchange and keeps Back/Forward working.
  const go = (key: Tab, part?: string) => {
    const hash = part ? `#${key}/${encodeURIComponent(part)}` : `#${key}`;
    if (window.location.hash === hash) return;
    window.location.hash = hash;
  };
  const current: Tab = tab ?? "flow";
  const nav: SubNav = { sub, onSub: (part) => go(current, part) };

  const today = localDay();
  const start = cursor === null ? null : period === "week" ? cursor : `${cursor.slice(0, 7)}-01`;
  const latest = period === "week" ? thisWeek() : thisMonth();
  const rangeOk =
    range.from >= RANGE_MIN && range.from <= range.to && range.to <= today && daysBetween(range.from, range.to) <= RANGE_MAX_DAYS;
  const changePeriod = (p: Period) => {
    if (p === "range") {
      if (start !== null && period !== "range") {
        const last = period === "week" ? shiftDays(start, 6) : shiftDays(shiftMonths(start, 1), -1);
        const to = last < today ? last : today;
        const from = start > RANGE_MIN ? start : RANGE_MIN;
        setRange({ from, to: to > from ? to : from });
      }
      setPeriod(p);
      return;
    }
    setPeriod(p);
    setCursor((c) => (c === null ? c : p === "month" ? `${c.slice(0, 7)}-01` : c >= thisMonth() ? thisWeek() : weekOf(c)));
  };
  const step = (by: number) => setCursor((c) => (c === null ? c : period === "week" ? shiftWeeks(c, by) : shiftMonths(c, by)));
  const periodQuery: Query | null =
    period === "range" ? (rangeOk ? { period, start: range.from, end: range.to } : null) : start ? { period, start } : null;

  const { data: repos } = useApi<Repo[]>("/repos");
  const projects = useMemo(() => {
    const seen = new Map<number, string>();
    for (const r of repos ?? []) if (r.business_project_id !== null && r.business_project) seen.set(r.business_project_id, r.business_project);
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1], "vi"));
  }, [repos]);
  const repoOptions = useMemo(() => (repos ?? []).filter((r) => !bpId || String(r.business_project_id) === bpId), [repos, bpId]);
  const scope: Query = { business_project_id: bpId, project_id: repoId };
  const query = periodQuery && { ...scope, ...periodQuery };

  return (
    <>
      {/* One slim sticky bar: title, page tabs and every filter, so the content gets the screen. */}
      <div className="ins-bar">
        <h1 title={FOOTER}>Luồng làm việc</h1>
        <SubTabs id="ins" label="Nội dung Insights" variant="bar" tabs={TABS} value={current} onChange={(k) => go(k)} />
        <div className="ins-filters" role="group" aria-label="Bộ lọc">
          <select
            value={bpId}
            onChange={(e) => {
              setBpId(e.target.value);
              setRepoId("");
            }}
            aria-label="Lọc theo dự án"
            title="Dự án"
          >
            <option value="">Mọi dự án</option>
            {projects.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
          <select value={repoId} onChange={(e) => setRepoId(e.target.value)} aria-label="Lọc theo repo" title="Repo">
            <option value="">Mọi repo</option>
            {repoOptions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.path_with_namespace}
              </option>
            ))}
          </select>
          {current !== "knowledge" && (
            <>
              <Segmented<Period>
                label="Chu kỳ"
                value={period}
                onChange={changePeriod}
                options={[
                  { value: "week", label: "Tuần" },
                  { value: "month", label: "Tháng" },
                  { value: "range", label: "Khoảng ngày" },
                ]}
              />
              {period === "range" ? (
                <DateRangePicker label="Thời gian" value={range} min={RANGE_MIN} max={today} onChange={setRange} />
              ) : (
                <Pager
                  label={periodLabel(period, start)}
                  onPrev={() => step(-1)}
                  onNext={() => step(1)}
                  nextDisabled={start === null || start >= latest}
                  prevDisabled={start === null}
                />
              )}
            </>
          )}
        </div>
      </div>

      {tab && (
        <SubPanel id="ins" tab={tab}>
          {tab !== "knowledge" && period === "range" && !rangeOk && (
            <Alert tone="info">
              Chọn khoảng từ {date(RANGE_MIN)} đến hôm nay, dài tối đa {RANGE_MAX_DAYS} ngày.
            </Alert>
          )}
          {tab === "flow" && query && <FlowTab query={query} nav={nav} />}
          {tab === "load" && query && <LoadTab query={query} nav={nav} />}
          {tab === "dora" && query && <DoraTab query={query} nav={nav} />}
          {tab === "knowledge" && <KnowledgeTab query={scope} nav={nav} />}
        </SubPanel>
      )}

      <p className="small muted ins-foot">
        {FOOTER} Thời gian tính theo lịch (cả cuối tuần), dùng trung vị và p75 thay vì trung bình. Bấm các nút &quot;Xem MR&quot; để
        ra danh sách MR tạo nên con số.
      </p>
    </>
  );
}

type FlowSub = "overview" | "waiting" | "repos" | "mrs";

function FlowTab({ query, nav }: { query: Query; nav: SubNav }) {
  const { data, error, mutate } = useApi<InsightsFlow>(`/insights/flow${qs(query)}`, { keepPreviousData: true });
  const [drill, setDrill] = useState<{ stage: InsightStage; only: Only }>({ stage: "time_to_merge", only: "" });
  const open = (stage: InsightStage, only: Only = "") => {
    setDrill({ stage, only });
    nav.onSub("mrs");
  };
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  const t = data.totals;
  const trend = data.trend.map((p) => ({
    period: data.trend_period === "week" ? weekLabel(p.period_start) : `${p.period_start.slice(5, 7)}/${p.period_start.slice(2, 4)}`,
    coding: p.coding,
    pickup: p.pickup,
    review: p.review,
  }));
  const trendEmpty = data.trend.every((p) => p.coding === null && p.pickup === null && p.review === null);
  const sizes = (["XS", "S", "M", "L", "XL"] as const).map((b) => ({ bucket: b, mrs: t.size.buckets[b] }));
  const ttm = kpiHours(t.stages.time_to_merge.median, t.delta.time_to_merge);
  const pickup = kpiHours(t.stages.pickup.median, t.delta.pickup);
  const repos = [...data.repos].sort((a, b) => b.merged - a.merged || a.path_with_namespace.localeCompare(b.path_with_namespace));
  const label = data.period_type === "range" ? rangeLabel(data.period_start, data.period_end) : periodLabel(data.period_type, data.period_start);
  const mainBranch = data.pipelines.default_branch.success_rate;
  const tabs: SubTab<FlowSub>[] = [
    { key: "overview", label: "Tổng quan", hint: "Trung vị từng chặng theo kỳ và cỡ MR" },
    { key: "waiting", label: "Đang chờ review", count: data.open.waiting_first_response, hint: "MR đang mở chưa có phản hồi đầu tiên" },
    { key: "repos", label: "Theo repo", count: repos.length, hint: "Trung vị từng chặng của mỗi repo" },
    { key: "mrs", label: "Danh sách MR", count: t.merged, hint: "MR đã merge trong kỳ, sắp theo chặng" },
  ];
  const view = pickSub(nav, tabs, "overview");

  return (
    <>
      <ConfidenceNote c={data.confidence} repoId={query.project_id} />
      <StatStrip label={`Luồng làm việc · ${label}`}>
        <StripStat label="MR đã merge" value={t.merged} change={t.delta.merged} />
        <StripStat
          label="Mở → merge (trung vị)"
          title={`${STAGE_HINT.time_to_merge}. p75: ${hours(t.stages.time_to_merge.p75)}`}
          value={ttm.value}
          digits={1}
          unit={ttm.unit}
          change={ttm.change}
          higherIsBetter={false}
        />
        <StripStat
          label="Chờ review (trung vị)"
          title={`${STAGE_HINT.pickup}. p75: ${hours(t.stages.pickup.p75)}`}
          value={pickup.value}
          digits={1}
          unit={pickup.unit}
          change={pickup.change}
          higherIsBetter={false}
        />
        <StripStat
          label="Merge không review"
          title="MR không có phản hồi nào của người khác trước khi merge"
          value={t.merged_without_review_rate}
          digits={1}
          unit="%"
          change={t.delta.merged_without_review_rate === null ? undefined : t.delta.merged_without_review_rate}
          higherIsBetter={false}
          tone={t.merged_without_review_rate && t.merged_without_review_rate > 20 ? "warn" : undefined}
          sub={`${t.merged_without_review} MR`}
        />
        <StripStat
          label="MR cỡ XL"
          title="≥ 1000 dòng thay đổi: khó review kỹ"
          value={t.size.xl_share}
          digits={1}
          unit="%"
          change={t.delta.xl_share === null ? undefined : t.delta.xl_share}
          higherIsBetter={false}
          sub={`${t.size.buckets.XL} MR`}
        />
        <StripStat
          label="Pipeline thành công"
          title="success / (success + failed), mọi nhánh"
          value={data.pipelines.success_rate}
          digits={1}
          unit="%"
          change={t.delta.pipeline_success_rate === null ? undefined : t.delta.pipeline_success_rate}
          sub={mainBranch === null ? "nhánh chính: —" : `nhánh chính: ${num(mainBranch, 1)}%`}
        />
      </StatStrip>

      <SubTabs
        id="ins-flow"
        label="Phần của Luồng làm việc"
        variant="mini"
        tabs={tabs}
        value={view}
        onChange={nav.onSub}
        actions={data.data_as_of ? <span className="small muted">Dữ liệu tới {dateTime(data.data_as_of)}</span> : undefined}
      />

      <SubPanel id="ins-flow" tab={view}>
        {view === "overview" && (
          <>
            <div className="toolbar ins-drill">
              <span className="small muted">Xem MR:</span>
              <button type="button" className="sm ghost" onClick={() => open("time_to_merge")}>
                chậm merge nhất
              </button>
              <button type="button" className="sm ghost" onClick={() => open("pickup")}>
                chờ review lâu nhất
              </button>
              <button type="button" className="sm ghost" onClick={() => open("time_to_merge", "unreviewed")} disabled={!t.merged_without_review}>
                merge không review ({t.merged_without_review})
              </button>
              <button type="button" className="sm ghost" onClick={() => open("time_to_merge", "xl")} disabled={!t.size.buckets.XL}>
                cỡ XL ({t.size.buckets.XL})
              </button>
            </div>
            <div className="grid two">
              <Card id="insights-stages" title="Trung vị từng chặng theo kỳ" subtitle="giờ, theo lịch">
                {trendEmpty ? (
                  <EmptyState title="Chưa tính được trung vị">Mỗi kỳ có quá ít MR đã merge.</EmptyState>
                ) : (
                  <div className="chart-box">
                    <ResponsiveContainer>
                      <BarChart data={trend} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                        <XAxis dataKey="period" tickLine={false} axisLine={false} fontSize={12} />
                        <YAxis tickLine={false} axisLine={false} fontSize={12} unit="h" />
                        <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
                        <Legend iconType="circle" iconSize={8} />
                        <Bar dataKey="coding" name={`${STAGE_LABEL.coding} (giờ)`} stackId="s" fill="var(--chart-1)" />
                        <Bar dataKey="pickup" name={`${STAGE_LABEL.pickup} (giờ)`} stackId="s" fill="var(--chart-3)" />
                        <Bar dataKey="review" name={`${STAGE_LABEL.review} (giờ)`} stackId="s" fill="var(--chart-2)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>
              <Card id="insights-sizes" title="Cỡ MR" subtitle="dòng thay đổi: XS <50 · S <200 · M <500 · L <1000 · XL ≥1000">
                {t.merged === 0 ? (
                  <EmptyState title="Không có MR nào merge trong kỳ" />
                ) : (
                  <div className="chart-box">
                    <ResponsiveContainer>
                      <BarChart data={sizes} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="30%">
                        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                        <XAxis dataKey="bucket" tickLine={false} axisLine={false} fontSize={12} />
                        <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                        <Tooltip content={<ChartTip digits={0} />} cursor={{ fill: "var(--surface-2)" }} />
                        <Bar dataKey="mrs" name="MR" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={48} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>
            </div>
          </>
        )}

        {view === "waiting" && (
          <Card
            id="insights-waiting"
            title="MR đang chờ phản hồi đầu tiên"
            subtitle={`${data.open.waiting_first_response} / ${data.open.opened} MR đang mở (không tính draft), lâu nhất trước`}
            flush
          >
            {data.open.oldest.length === 0 ? (
              <EmptyState icon="checkCircle" tone="good" title="Không có MR nào đang chờ">
                Mọi MR đang mở (trừ draft) đã có người phản hồi.
              </EmptyState>
            ) : (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>MR</th>
                        <th>Repo</th>
                        <th>Tác giả</th>
                        <th className="num">Đã chờ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.open.oldest.map((m) => (
                        <tr key={m.id}>
                          <td>
                            <MrTitle url={m.web_url} iid={m.iid} title={m.title} />
                          </td>
                          <td className="muted">{m.path_with_namespace}</td>
                          <td>
                            <Person p={m.author} />
                          </td>
                          <td className="num nowrap">{hours(m.waiting_h)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <HiddenNote people={data.open.oldest.map((m) => m.author)} />
              </>
            )}
          </Card>
        )}

        {view === "repos" && (
          <Card id="insights-repos" title="Theo repo" subtitle="Trung vị từng chặng" flush>
            {repos.length === 0 ? (
              <EmptyState icon="folder" title="Không có repo nào trong phạm vi" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Repo</th>
                      <th className="num">Merge</th>
                      {STAGES.map((s) => (
                        <th key={s} className="num" title={STAGE_HINT[s]}>
                          {STAGE_LABEL[s]}
                        </th>
                      ))}
                      <th className="num">Không review</th>
                      <th className="num">Pipeline</th>
                    </tr>
                  </thead>
                  <tbody>
                    {repos.map((r) => (
                      <tr key={r.id} className={r.merged ? undefined : "muted"}>
                        <td>{r.path_with_namespace}</td>
                        <td className="num">{r.merged}</td>
                        {STAGES.map((s) => (
                          <td key={s} className="num nowrap">
                            {hours(r.stages[s].median)}
                          </td>
                        ))}
                        <td className="num">{pct(r.merged_without_review_rate)}</td>
                        <td className="num">{pct(r.pipelines.success_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}

        {view === "mrs" && <MrList key={JSON.stringify(query)} query={query} drill={drill} onDrill={setDrill} />}
      </SubPanel>
    </>
  );
}

function MrTitle({ url, iid, title }: { url: string | null; iid: number; title: string | null }) {
  const text = `!${iid} ${title ?? ""}`;
  return url ? <ExternalLink href={url}>{text}</ExternalLink> : <>{text}</>;
}

function MrList({
  query,
  drill,
  onDrill,
}: {
  query: Query;
  drill: { stage: InsightStage; only: Only };
  onDrill: (d: { stage: InsightStage; only: Only }) => void;
}) {
  const [limit, setLimit] = useState(PAGE);
  const { stage, only } = drill;
  const change = (d: { stage: InsightStage; only: Only }) => {
    setLimit(PAGE);
    onDrill(d);
  };
  useEffect(() => setLimit(PAGE), [drill]);
  const { data, error, mutate } = useApi<InsightsMrs>(`/insights/flow/mrs${qs({ ...query, stage, only: only || null, limit })}`, {
    keepPreviousData: true,
  });
  let body: ReactNode;
  if (!data) body = <Pending error={error} retry={() => void mutate()} rows={3} />;
  else if (data.items.length === 0) body = <EmptyState title="Không có MR nào khớp">Đổi kỳ, dự án hoặc bộ lọc.</EmptyState>;
  else
    body = (
      <>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>MR</th>
                <th>Tác giả</th>
                <th className="num">Cỡ</th>
                {STAGES.map((s) => (
                  <th key={s} className="num" title={STAGE_HINT[s]}>
                    {STAGE_LABEL[s]}
                  </th>
                ))}
                <th className="num">Vòng review</th>
                <th>Merge</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((m) => (
                <tr key={m.id}>
                  <td>
                    <MrTitle url={m.web_url} iid={m.iid} title={m.title} />
                    <div className="small muted">{m.path_with_namespace}</div>
                  </td>
                  <td>
                    <Person p={m.author} />
                  </td>
                  <td className="num nowrap">{m.size_bucket ? <Pill tone={m.size_bucket === "XL" ? "warn" : undefined}>{m.size_bucket}</Pill> : "—"}</td>
                  {STAGES.map((s) => {
                    const cell =
                      (s === "pickup" || s === "review") && !m.reviewed ? (
                        <span className="faint" title="Không có phản hồi nào trước khi merge">
                          không review
                        </span>
                      ) : (
                        hours(m[s])
                      );
                    return (
                      <td key={s} className="num nowrap">
                        {s === stage ? <strong>{cell}</strong> : cell}
                      </td>
                    );
                  })}
                  <td className="num">{m.review_rounds ?? "—"}</td>
                  <td className="nowrap muted">{date(m.merged_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <HiddenNote people={data.items.map((m) => m.author)} />
        {data.total > data.items.length && (
          <p className="small" style={{ margin: 0, padding: "10px 18px" }}>
            {limit < MAX_ROWS ? (
              <button type="button" className="sm" onClick={() => setLimit((l) => Math.min(MAX_ROWS, l + PAGE))}>
                Xem thêm ({data.items.length}/{data.total})
              </button>
            ) : (
              <span className="muted">
                Hiển thị {data.items.length}/{data.total} MR. Lọc theo dự án, repo hoặc kỳ ngắn hơn để xem phần còn lại.
              </span>
            )}
          </p>
        )}
      </>
    );
  return (
    <Card id="insights-mrs" title="MR trong kỳ" subtitle={data ? `${data.total} MR, chậm nhất trước theo chặng đang chọn` : undefined} flush>
      <div className="toolbar" style={{ padding: "10px 18px", flexWrap: "wrap" }}>
        <Segmented<InsightStage>
          label="Sắp theo"
          value={stage}
          onChange={(s) => change({ stage: s, only })}
          options={(["time_to_merge", "pickup", "review", "coding"] as const).map((s) => ({ value: s, label: STAGE_LABEL[s] }))}
        />
        <Segmented<Only>
          label="Lọc"
          value={only}
          onChange={(o) => change({ stage, only: o })}
          options={[
            { value: "", label: "Tất cả" },
            { value: "unreviewed", label: "Không review" },
            { value: "xl", label: "XL" },
          ]}
        />
      </div>
      {body}
    </Card>
  );
}

type LoadSub = "reviewers" | "pairs";

function LoadTab({ query, nav }: { query: Query; nav: SubNav }) {
  const { data, error, mutate } = useApi<InsightsReviewLoad>(`/insights/review-load${qs(query)}`, { keepPreviousData: true });
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  const tabs: SubTab<LoadSub>[] = [
    { key: "reviewers", label: "Ai review", count: data.reviewers.length, hint: "Số MR mỗi người có phản hồi" },
    { key: "pairs", label: "Review cho ai", count: data.pairs.length, hint: "Top cặp người review → tác giả" },
  ];
  const view = pickSub(nav, tabs, "reviewers");
  return (
    <>
      <ConfidenceNote c={data.confidence} repoId={query.project_id} />
      {data.concentrated && (
        <Alert tone="warning">
          Một người tham gia review {num(data.top_reviewer_share, 1)}% số MR đã merge: team đang phụ thuộc vào một reviewer.
        </Alert>
      )}
      <StatStrip label="Tải review trong kỳ">
        <StripStat label="MR đã merge" value={data.merged} />
        <StripStat label="Có review" value={data.reviewed} sub={data.merged ? `${num((100 * data.reviewed) / data.merged, 1)}%` : undefined} />
        <StripStat label="Người review" value={data.reviewers_count} />
        <StripStat
          label="Người review nhiều nhất"
          title="Phần trăm MR mà người review nhiều nhất có tham gia"
          value={data.top_reviewer_share}
          digits={1}
          unit="%"
          tone={data.concentrated ? "warn" : undefined}
        />
      </StatStrip>
      <SubTabs id="ins-load" label="Phần của Tải review" variant="mini" tabs={tabs} value={view} onChange={nav.onSub} />
      <SubPanel id="ins-load" tab={view}>
        {view === "reviewers" && (
          <Card id="insights-reviewers" title="Ai review" subtitle="Số MR có phản hồi (comment hoặc approve), không đếm số comment" flush>
            {data.reviewers.length === 0 ? (
              <EmptyState title="Chưa có review nào trong kỳ" />
            ) : (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Người review</th>
                        <th className="num">MR</th>
                        <th>Phần MR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.reviewers.map((r, i) => (
                        <tr key={`${r.user_id ?? "x"}-${i}`}>
                          <td>
                            <Person p={r} />
                          </td>
                          <td className="num">{r.reviewed_mrs}</td>
                          <td style={{ minWidth: 120 }}>
                            <SplitBar
                              label={`${num(r.share, 1)}% số MR`}
                              parts={[
                                { name: "Đã review", value: r.reviewed_mrs, color: "var(--chart-1)" },
                                { name: "Còn lại", value: Math.max(0, data.merged - r.reviewed_mrs), color: "var(--surface-2)" },
                              ]}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <HiddenNote people={data.reviewers} />
              </>
            )}
          </Card>
        )}
        {view === "pairs" && (
          <Card id="insights-pairs" title="Review cho ai" subtitle="Top cặp người review → tác giả" flush>
            {data.pairs.length === 0 ? (
              <EmptyState title="Chưa có review nào trong kỳ" />
            ) : (
              <>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Người review</th>
                        <th>Tác giả</th>
                        <th className="num">MR</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.pairs.map((p, i) => (
                        <tr key={i}>
                          <td>
                            <Person p={p.reviewer} />
                          </td>
                          <td>
                            <Person p={p.author} />
                          </td>
                          <td className="num">{p.mrs}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <HiddenNote people={data.pairs.flatMap((p) => [p.reviewer, p.author])} />
              </>
            )}
          </Card>
        )}
      </SubPanel>
    </>
  );
}

type KnowledgeSub = "warnings" | "tree" | "structure";

const nodeKey = (m: InsightModule) => `${m.project_id}:${m.module}`;
const parentKey = (m: InsightModule) => (m.parent ? `${m.project_id}:${m.parent}` : null);
const TIER_LABEL: Record<InsightModule["tier"], string> = {
  repo: "Repo",
  module: "Module",
  component: "Thành phần",
  part: "Thành phần",
  sub: "Package con",
};

function isFlagged(m: InsightModule) {
  return m.single_owner || m.orphaned || m.unattributed;
}

/** Owners of a node, compact: "dongnv 70% · truongnd 30%". */
function Owners({ m }: { m: InsightModule }) {
  return (
    <>
      {m.owners.map((o, i) => (
        <div key={i} className="small" style={{ whiteSpace: "nowrap" }}>
          <Person p={o} /> <span className="muted">{num(o.share, 0)}%</span>
        </div>
      ))}
    </>
  );
}

function Flags({ m }: { m: InsightModule }) {
  if (!isFlagged(m)) return null;
  const pills = (
    <>
      {m.orphaned && <Pill tone="bad">Người nắm đã vắng</Pill>} {m.single_owner && <Pill tone="warn">Một người nắm</Pill>}{" "}
      {m.unattributed && <Pill tone="info">Cần gắn account</Pill>}
    </>
  );
  if (m.counted) return <span title={m.why ?? undefined}>{pills}</span>;
  return (
    <span className="muted small" title={m.why ?? undefined} style={{ opacity: 0.75 }}>
      {m.inherited ? "Thừa hưởng cấp trên" : (m.why ?? "Chỉ để tham khảo")}
    </span>
  );
}

function NodeName({ m }: { m: InsightModule }) {
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <strong style={{ fontWeight: m.level === 0 ? 700 : 600 }}>{m.name}</strong>
      {m.tier !== "component" && m.tier !== "part" && <span className="small muted">{TIER_LABEL[m.tier]}</span>}
      {m.source === "llm" && <Pill tone="info">AI gom</Pill>}
      {KIND_LABEL[m.kind] && <Pill>{KIND_LABEL[m.kind]}</Pill>}
    </span>
  );
}

function KnowledgeTab({ query, nav }: { query: Query; nav: SubNav }) {
  const { data, error, mutate } = useApi<InsightsKnowledge>(`/insights/knowledge${qs(query)}`, { keepPreviousData: true });
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  const nodes = data.nodes ?? data.modules;
  const byKey = new Map(nodes.map((m) => [nodeKey(m), m]));
  const warnings = nodes.filter((m) => m.counted);
  const tabs: SubTab<KnowledgeSub>[] = [
    {
      key: "warnings",
      label: "Cảnh báo",
      count: warnings.length,
      hint: "Mỗi rủi ro chỉ tính một lần, ở cấp cao nhất nó đúng (repo, module hay thành phần)",
    },
    { key: "tree", label: "Cây thành phần", count: nodes.length, hint: "Repo › module › thành phần › package con" },
    { key: "structure", label: "Cấu trúc repo", hint: "Repo được chia thành những thành phần nào, tự động hoặc do AI gom theo nghiệp vụ" },
  ];
  const view = pickSub(nav, tabs, "warnings");
  const threshold = Math.round(data.bus_threshold * 100);
  const trail = (m: InsightModule) => {
    const names: string[] = [];
    let cur: InsightModule | undefined = m;
    while (cur) {
      names.unshift(cur.name);
      const pk = parentKey(cur);
      cur = pk ? byKey.get(pk) : undefined;
    }
    return names;
  };
  const empty = nodes.length === 0 && (
    <Card>
      <EmptyState icon="folder" title="Chưa có dữ liệu file trong cửa sổ">
        Repo chưa có commit trong {data.window_days} ngày qua, hoặc admin cần chạy <code>codepulse insights backfill-files</code>.
      </EmptyState>
    </Card>
  );
  const busTitle = `Số người tối thiểu nắm ≥ ${threshold}% số lần chạm file (giảm một nửa sau mỗi ${data.half_life_days ?? 90} ngày; commit hàng loạt nhẹ đi)`;
  return (
    <>
      <ConfidenceNote c={data.confidence} repoId={query.project_id} />
      <StatStrip label={`Rủi ro tri thức trong ${data.window_days} ngày qua`}>
        <StripStat
          label="Cảnh báo"
          title="Mỗi rủi ro tính một lần ở cấp cao nhất nó đúng; cấp dưới cùng người nắm chỉ là thừa hưởng"
          value={data.totals.warnings ?? warnings.length}
          tone={(data.totals.warnings ?? warnings.length) ? "warn" : undefined}
          sub={data.totals.inherited ? `${data.totals.inherited} cấp dưới thừa hưởng` : undefined}
        />
        <StripStat
          label="Một người nắm"
          title={`Một người nắm hơn ${threshold}%, có ≥ ${data.min_commits} commit`}
          value={data.totals.single_owner}
          tone={data.totals.single_owner ? "warn" : undefined}
        />
        <StripStat
          label="Người nắm đã vắng"
          title={`Người nắm nhiều nhất đã nghỉ (tắt trong CodePulse) hoặc không commit trong ${data.orphan_after_days} ngày`}
          value={data.totals.orphaned}
          tone={data.totals.orphaned ? "bad" : undefined}
        />
        {!!data.totals.unattributed && (
          <StripStat label="Cần gắn account" title="Người nắm nhiều nhất là account git chưa gắn người" value={data.totals.unattributed} tone="warn" />
        )}
        <StripStat label="Thành phần" value={data.totals.modules} sub="theo cấu trúc repo" />
      </StatStrip>
      <SubTabs id="ins-knowledge" label="Rủi ro tri thức" variant="mini" tabs={tabs} value={view} onChange={nav.onSub} />
      <SubPanel id="ins-knowledge" tab={view}>
        {view === "structure" ? (
          <RepoStructure repoId={query.project_id ? Number(query.project_id) : null} />
        ) : empty ? (
          empty
        ) : view === "warnings" ? (
          <Card
            id="insights-warnings"
            title="Cảnh báo"
            subtitle="Rủi ro của team khi kiến thức tập trung ở một người, không phải thành tích hay lỗi của người đó"
            flush
          >
            {warnings.length === 0 ? (
              <EmptyState icon="checkCircle" tone="good" title="Không có cảnh báo">
                Mọi repo, module và thành phần có thay đổi đều có nhiều người cùng nắm.
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Ở đâu</th>
                      <th className="num" title={busTitle}>
                        Bus factor
                      </th>
                      <th>Người nắm nhiều nhất</th>
                      <th className="num">Commit</th>
                      <th>Sửa gần nhất</th>
                      <th>Cảnh báo</th>
                    </tr>
                  </thead>
                  <tbody>
                    {warnings.map((m) => {
                      const path = trail(m);
                      return (
                        <tr key={nodeKey(m)}>
                          <td style={{ whiteSpace: "normal", minWidth: 240 }}>
                            <div className="small muted">{path.slice(0, -1).join(" › ") || TIER_LABEL[m.tier]}</div>
                            <NodeName m={m} />
                            {m.why && <div className="small">{m.why}</div>}
                          </td>
                          <td className="num">{m.bus_factor}</td>
                          <td>
                            <Owners m={m} />
                          </td>
                          <td className="num">{m.commits}</td>
                          <td className="nowrap muted">{date(m.last_change_at)}</td>
                          <td className="nowrap">
                            <Flags m={m} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <HiddenNote people={warnings.flatMap((m) => m.owners)} />
          </Card>
        ) : (
          <>
            <KnowledgeTree nodes={nodes} busTitle={busTitle} />
            {data.nodes_truncated && (
              <p className="small muted" style={{ margin: "8px 0 0" }}>
                Cây quá lớn nên chỉ hiện một phần (luôn giữ mọi cảnh báo và các cấp trên của chúng). Lọc theo dự án hoặc repo để xem đủ.
              </p>
            )}
          </>
        )}
      </SubPanel>
    </>
  );
}

/** §4.9c: repo › module › component › sub-package, folded; warnings stand out, inherited
 * flags are muted. Opens to the second level; flagged branches can be opened on demand. */
function KnowledgeTree({ nodes, busTitle }: { nodes: InsightModule[]; busTitle: string }) {
  const children = useMemo(() => {
    const out = new Map<string | null, InsightModule[]>();
    for (const m of nodes) {
      const pk = parentKey(m);
      const list = out.get(pk) ?? [];
      list.push(m);
      out.set(pk, list);
    }
    for (const list of out.values())
      list.sort((x, y) => Number(y.counted) - Number(x.counted) || Number(isFlagged(y)) - Number(isFlagged(x)) || y.commits - x.commits);
    return out;
  }, [nodes]);
  const roots = useMemo(() => {
    const keys = new Set(nodes.map(nodeKey));
    return nodes.filter((m) => !m.parent || !keys.has(parentKey(m) ?? "")).sort((x, y) => y.commits - x.commits);
  }, [nodes]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(roots.map(nodeKey)));
  // A new filter brings new repos: open them too, keep what the reader opened.
  useEffect(() => setOpen((prev) => new Set([...prev, ...roots.map(nodeKey)])), [roots]);
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  const rows: { m: InsightModule; depth: number }[] = [];
  const walk = (m: InsightModule, depth: number) => {
    rows.push({ m, depth });
    if (open.has(nodeKey(m))) for (const c of children.get(nodeKey(m)) ?? []) walk(c, depth + 1);
  };
  for (const r of roots) walk(r, 0);
  const all = () => setOpen(new Set(nodes.filter((m) => children.has(nodeKey(m))).map(nodeKey)));
  return (
    <Card
      id="insights-tree"
      title="Cây thành phần"
      subtitle="Mỗi cấp tính trên mọi file bên dưới nó; cấp dưới cùng người nắm với cấp trên chỉ thừa hưởng cảnh báo"
      actions={
        <span style={{ display: "flex", gap: 6 }}>
          <button type="button" className="sm" onClick={all}>
            Mở tất cả
          </button>
          <button type="button" className="sm" onClick={() => setOpen(new Set(roots.map(nodeKey)))}>
            Thu gọn
          </button>
        </span>
      }
      flush
    >
      <div className="table-wrap">
        <table role="treegrid" aria-label="Cây thành phần">
          <thead>
            <tr>
              <th>Thành phần</th>
              <th className="num" title={busTitle}>
                Bus factor
              </th>
              <th>Người nắm nhiều nhất</th>
              <th className="num">Commit</th>
              <th className="num" title="Không tính commit hàng loạt (import, đổi tên, format)">
                Dòng đổi
              </th>
              <th>Sửa gần nhất</th>
              <th>Cảnh báo</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ m, depth }) => {
              const k = nodeKey(m);
              const kids = children.get(k)?.length ?? 0;
              const expanded = open.has(k);
              return (
                <tr
                  key={k}
                  aria-level={depth + 1}
                  aria-expanded={kids ? expanded : undefined}
                  style={m.counted ? { background: "color-mix(in srgb, var(--sev-medium, #d97706) 8%, transparent)" } : undefined}
                >
                  <td style={{ whiteSpace: "normal", minWidth: 260 }}>
                    <div style={{ display: "flex", gap: 6, alignItems: "flex-start", paddingLeft: depth * 18 }}>
                      {kids ? (
                        <button
                          type="button"
                          className="sm ghost"
                          aria-label={`${expanded ? "Thu gọn" : "Mở"} ${m.name}`}
                          onClick={() => toggle(k)}
                          style={{ padding: "0 6px", minWidth: 24 }}
                        >
                          {expanded ? "▾" : "▸"}
                        </button>
                      ) : (
                        <span style={{ display: "inline-block", width: 24 }} />
                      )}
                      <div>
                        <NodeName m={m} />
                        {m.description && <div className="small">{m.description}</div>}
                        {m.level === 0 && (
                          <div className="small muted">
                            {m.path_with_namespace} · {m.contributors} người đóng góp
                          </div>
                        )}
                        {m.level > 0 && m.share_of_parent != null && (
                          <div className="small muted">{num(m.share_of_parent, 0)}% công việc của cấp trên</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="num">{m.bus_factor}</td>
                  <td>
                    <Owners m={m} />
                  </td>
                  <td className="num">{m.commits}</td>
                  <td className="num muted">{num(m.churn, 0)}</td>
                  <td className="nowrap muted">{date(m.last_change_at)}</td>
                  <td>
                    <Flags m={m} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <HiddenNote people={rows.flatMap((r) => r.m.owners)} />
    </Card>
  );
}

const KIND_LABEL: Partial<Record<InsightModule["kind"], string>> = {
  config: "Cấu hình & build",
  docs: "Tài liệu",
  platform: "Nền tảng",
  integration: "Tích hợp",
};

/** §4.9b: what one repo is made of, as knowledge risk measures it; admins may have an AI group
 * the automatic components by business (paths only, never source). */
function RepoStructure({ repoId }: { repoId: number | null }) {
  const { data, error, mutate } = useApi<InsightComponents>(repoId ? `/insights/components?project_id=${repoId}` : null, {
    refreshInterval: (latest) => (latest?.ai && (latest.ai.status === "queued" || latest.ai.status === "running") ? 4000 : 0),
  });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!repoId)
    return (
      <Card>
        <EmptyState icon="folder" title="Chọn một repo">
          Chọn repo ở bộ lọc phía trên để xem repo được chia thành những thành phần nào.
        </EmptyState>
      </Card>
    );
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  const ai = data.ai;
  const working = !!ai && (ai.status === "queued" || ai.status === "running");
  const act = (method: "post" | "delete") => {
    if (
      method === "post" &&
      !window.confirm(
        "Nhờ AI gom các thành phần tự động thành thành phần nghiệp vụ? AI chỉ đọc đường dẫn file (không đọc mã nguồn) và hồ sơ dự án; tốn một ít ngân sách LLM.",
      )
    )
      return;
    setBusy(true);
    setNote(null);
    const call = method === "post" ? api.post(`/admin/insights/components/${repoId}`, {}) : api.delete(`/admin/insights/components/${repoId}`);
    void call
      .then(() => {
        setNote(method === "post" ? "Đã xếp hàng; kết quả hiện ở đây sau ít phút." : "Đã quay về bản tự động.");
        return mutate();
      })
      .catch((e: unknown) => setNote(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`))
      .finally(() => setBusy(false));
  };
  const shown = data.components.filter((c) => c.kind !== "noise");
  const noise = data.components.filter((c) => c.kind === "noise").reduce((n, c) => n + c.files, 0);
  return (
    <Card
      title="Cấu trúc repo"
      subtitle={`${data.path_with_namespace} · ${data.paths} file từng được sửa · ${data.source === "llm" ? "AI gom theo nghiệp vụ" : "tự nhận từ cấu trúc thư mục và tên file"}`}
      actions={
        data.can_rebuild || (ai && data.source === "llm") ? (
          <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {data.can_rebuild && (
              <button
                type="button"
                className="sm primary"
                disabled={busy || working || !data.allow_external_llm}
                title={data.allow_external_llm ? undefined : "Repo này không cho dùng LLM bên ngoài"}
                onClick={() => act("post")}
              >
                {working ? "AI đang phân tích…" : data.source === "llm" ? "Phân tích lại bằng AI" : "Phân tích cấu trúc bằng AI"}
              </button>
            )}
            {data.can_rebuild && data.source === "llm" && (
              <button type="button" className="sm" disabled={busy || working} onClick={() => act("delete")}>
                Về bản tự động
              </button>
            )}
          </span>
        ) : undefined
      }
      flush
    >
      <div style={{ display: "grid", gap: 6, padding: "10px 18px" }}>
        {note && (
          <span className="small" role="status">
            {note}
          </span>
        )}
        {ai && (
          <span className="small" role="status" aria-live="polite">
            {working ? (
              <Pill tone="info">{ai.status === "queued" ? "Đang chờ AI" : "AI đang gom thành phần"}</Pill>
            ) : ai.status === "failed" ? (
              <>
                <Pill tone="bad">AI chưa gom được</Pill> <span className="muted">{ai.error}</span>
              </>
            ) : (
              <>
                <Pill tone="good">AI đã gom</Pill>{" "}
                <span className="muted">
                  {ai.built_at ? dateTime(ai.built_at) : ""}
                  {ai.confidence != null ? ` · độ tin cậy ${Math.round(ai.confidence * 100)}%` : ""}
                  {data.unmapped_share ? ` · ${num(data.unmapped_share, 0)}% file AI để nguyên bản tự động` : ""}
                </span>
                {ai.stale && (
                  <>
                    {" "}
                    <Pill tone="warn">Đã cũ: repo thêm nhiều file mới, nên phân tích lại</Pill>
                  </>
                )}
              </>
            )}
          </span>
        )}
        {!ai && data.layered_roots.length > 0 && (
          <span className="small muted">
            Mã tổ chức theo tầng (controller/service/repository…) ở {data.layered_roots.length} module build: thành phần được suy ra từ tên
            file (WalletController, WalletService → Wallet). AI có thể gom lại theo nghiệp vụ chính xác hơn.
          </span>
        )}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Thành phần</th>
              <th className="num">File</th>
              <th>Gồm</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.id}>
                <td style={{ whiteSpace: "normal", minWidth: 200 }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <strong>{c.name}</strong>
                    {c.source === "llm" && <Pill tone="info">AI gom</Pill>}
                    {KIND_LABEL[c.kind] && <Pill>{KIND_LABEL[c.kind]}</Pill>}
                  </div>
                  {c.description && <div className="small">{c.description}</div>}
                </td>
                <td className="num">
                  {c.files}
                  {c.tests > 0 && <div className="small muted">{c.tests} test</div>}
                </td>
                <td style={{ whiteSpace: "normal" }}>
                  {c.parts.length > 0 && <div className="small">{c.parts.join(", ")}</div>}
                  {c.samples.slice(0, 3).map((s) => (
                    <div key={s} className="small muted" style={{ wordBreak: "break-all" }}>
                      <code>{s}</code>
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {noise > 0 && (
        <p className="small muted" style={{ margin: 0, padding: "8px 18px" }}>
          Bỏ qua {noise} file mã sinh ra, thư viện hoặc lockfile (target/, node_modules/, *.lock…): không ai cần nắm chúng.
        </p>
      )}
    </Card>
  );
}

/** Admin: which environment counts as production for one repo (docs/specs/20 D2). */
function ProductionEnv({ repo, onSaved }: { repo: InsightsDora["repos"][number]; onSaved: () => void }) {
  const [value, setValue] = useState(repo.production_environment ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setValue(repo.production_environment ?? ""), [repo.production_environment]);
  const dirty = value.trim() !== (repo.production_environment ?? "");
  return (
    <form
      className="toolbar"
      style={{ gap: 6 }}
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        setSaved(false);
        void api
          .patch(`/projects/${repo.id}`, { production_environment: value.trim() })
          .then(() => {
            setSaved(true);
            onSaved();
          })
          .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)))
          .finally(() => setBusy(false));
      }}
    >
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="tự nhận (tier production)"
        aria-label={`Môi trường production của ${repo.path_with_namespace}`}
        maxLength={255}
        list={`envs-${repo.id}`}
        style={{ width: 170 }}
      />
      <datalist id={`envs-${repo.id}`}>
        {repo.environments.map((e) => (
          <option key={e} value={e} />
        ))}
      </datalist>
      <button type="submit" className="sm" disabled={busy || !dirty}>
        Lưu
      </button>
      {error && <span className="small" style={{ color: "var(--sev-critical)" }}>{error}</span>}
      {saved && !error && <span className="small muted">Đã lưu; repo đang đồng bộ lại để lấy MR của các deploy cũ.</span>}
    </form>
  );
}

type DoraSub = "trend" | "failures" | "incidents" | "repos";

function DoraTab({ query, nav }: { query: Query; nav: SubNav }) {
  const { data, error, mutate } = useApi<InsightsDora>(`/insights/dora${qs(query)}`, { keepPreviousData: true });
  const { data: me } = useApi<Me>("/auth/me");
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  const t = data.totals;
  const lead = kpiHours(t.lead_time_h.median, t.delta.lead_time_h);
  const recovery = kpiHours(t.recovery_h.median, t.delta.recovery_h);
  const incidentRestore = kpiHours(data.incidents.restore_h.median, data.incidents.delta.restore_h);
  const trend = data.trend.map((p) => ({
    period: data.trend_period === "week" ? weekLabel(p.period_start) : `${p.period_start.slice(5, 7)}/${p.period_start.slice(2, 4)}`,
    deployments: p.deployments,
  }));
  const tabs: SubTab<DoraSub>[] = [
    { key: "trend", label: "Theo kỳ", hint: "Deploy production theo kỳ" },
    { key: "failures", label: "Thay đổi lỗi", count: data.failures.length, hint: "Deploy thất bại và thay đổi bị rollback" },
    { key: "incidents", label: "Sự cố", count: data.incidents.opened, hint: "Issue GitLab loại incident" },
    { key: "repos", label: "Theo repo", count: data.repos.length, hint: "Chỉ số và môi trường production của mỗi repo" },
  ];
  const view = pickSub(nav, tabs, "trend");
  return (
    <>
      <ConfidenceNote c={data.confidence} repoId={query.project_id} />
      <StatStrip label="Triển khai production trong kỳ">
        <StripStat
          label="Deploy thành công"
          title="Số lần deploy production thành công (tần suất triển khai)"
          value={t.deployments}
          change={t.delta.deployments}
          sub={`${num(t.per_week, 1)} / tuần · ${t.deploy_days} ngày có deploy`}
        />
        <StripStat
          label="Lead time (trung vị)"
          title={`Commit đầu tiên của MR → deploy production đầu tiên mang MR đó. p75: ${hours(t.lead_time_h.p75)}`}
          value={lead.value}
          digits={1}
          unit={lead.unit}
          change={lead.change}
          higherIsBetter={false}
          sub={`${t.shipped_mrs} MR · merge → deploy ${hours(t.deploy_h.median)}`}
        />
        <StripStat
          label="Tỉ lệ thay đổi lỗi"
          title="(Deploy thất bại + thay đổi bị rollback) / mọi deploy đã kết thúc"
          value={t.change_failure_rate}
          digits={1}
          unit="%"
          change={t.delta.change_failure_rate === null ? undefined : t.delta.change_failure_rate}
          higherIsBetter={false}
          tone={t.change_failure_rate !== null && t.change_failure_rate > 15 ? "warn" : undefined}
          sub={`${t.failed} thất bại · ${t.rollbacks} rollback`}
        />
        <StripStat
          label="Khôi phục (trung vị)"
          title="Từ deploy thất bại (hoặc thay đổi bị rollback) tới deploy thành công kế tiếp"
          value={recovery.value}
          digits={1}
          unit={recovery.unit}
          change={recovery.change}
          higherIsBetter={false}
          sub={`${t.recovery_h.n} lần`}
        />
      </StatStrip>

      <SubTabs id="ins-dora" label="Phần của Triển khai" variant="mini" tabs={tabs} value={view} onChange={nav.onSub} />
      <SubPanel id="ins-dora" tab={view}>
        {view === "incidents" && (
          <Card
            id="dora-incidents"
            title="Sự cố (incident)"
            subtitle="Issue GitLab loại incident hoặc có nhãn incident; khôi phục = mở → đóng"
            flush
          >
            <StatStrip label="Sự cố trong kỳ" inline>
              <StripStat
                label="Sự cố mới"
                value={data.incidents.opened}
                change={data.incidents.delta.opened}
                higherIsBetter={false}
                tone={data.incidents.opened ? "warn" : undefined}
                sub={data.incidents.per_deployment === null ? undefined : `${num(data.incidents.per_deployment, 2)} / lần deploy`}
              />
              <StripStat
                label="Khôi phục (trung vị)"
                value={incidentRestore.value}
                digits={1}
                unit={incidentRestore.unit}
                change={incidentRestore.change}
                higherIsBetter={false}
                sub={`${data.incidents.closed} đã đóng`}
              />
              <StripStat label="Đang mở" value={data.incidents.open_now} tone={data.incidents.open_now ? "bad" : undefined} />
            </StatStrip>
            {data.incidents.recent.length === 0 ? (
              <EmptyState icon="checkCircle" tone="good" title="Không có sự cố mới trong kỳ">
                Chưa dùng issue loại incident? Gắn nhãn incident cho issue sự cố trên GitLab để CodePulse đếm được.
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Sự cố</th>
                      <th>Repo</th>
                      <th>Trạng thái</th>
                      <th>Mở lúc</th>
                      <th className="num">MR sửa</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.incidents.recent.map((i) => (
                      <tr key={i.id}>
                        <td>{i.web_url ? <ExternalLink href={i.web_url}>{`#${i.iid} ${i.title ?? ""}`}</ExternalLink> : `#${i.iid} ${i.title ?? ""}`}</td>
                        <td className="muted">{i.path_with_namespace}</td>
                        <td>{i.state === "closed" ? <Pill tone="good">Đã đóng</Pill> : <Pill tone="bad">Đang mở</Pill>}</td>
                        <td className="nowrap muted">{dateTime(i.opened_at)}</td>
                        <td className="num">{i.fix_mrs}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}

        {view === "trend" && (
          <Card id="dora-trend" title="Deploy production theo kỳ">
            {data.trend.every((p) => p.deployments === 0) ? (
              <EmptyState title="Chưa có deploy production nào trong các kỳ này" />
            ) : (
              <div className="chart-box">
                <ResponsiveContainer>
                  <BarChart data={trend} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="period" tickLine={false} axisLine={false} fontSize={12} />
                    <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                    <Tooltip content={<ChartTip digits={0} />} cursor={{ fill: "var(--surface-2)" }} />
                    <Bar dataKey="deployments" name="Deploy" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>
        )}
        {view === "failures" && (
          <Card id="dora-failures" title="Thay đổi lỗi gần nhất" subtitle="Deploy thất bại và thay đổi bị rollback trong kỳ" flush>
            {data.failures.length === 0 ? (
              <EmptyState icon="checkCircle" tone="good" title="Không có thay đổi lỗi nào trong kỳ" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Repo</th>
                      <th>Môi trường</th>
                      <th>Loại</th>
                      <th>Commit</th>
                      <th>Lúc</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.failures.map((f) => (
                      <tr key={f.id}>
                        <td>{f.path_with_namespace}</td>
                        <td>{f.environment}</td>
                        <td>{f.kind === "failed" ? <Pill tone="bad">Deploy thất bại</Pill> : <Pill tone="warn">Bị rollback</Pill>}</td>
                        <td>
                          <code>{(f.sha ?? "").slice(0, 8)}</code>
                        </td>
                        <td className="nowrap muted">{dateTime(f.finished_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}

        {view === "repos" && (
          <Card
            id="dora-repos"
            title="Theo repo"
            subtitle="Production = môi trường có tier production trên GitLab, hoặc tên production/prod; admin đặt riêng nếu repo dùng tên khác"
            flush
          >
            {data.repos.length === 0 ? (
              <EmptyState icon="folder" title="Không có repo nào trong phạm vi" />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Repo</th>
                      <th>Môi trường production</th>
                      <th className="num">Deploy</th>
                      <th className="num">Lead time</th>
                      <th className="num">Thay đổi lỗi</th>
                      <th className="num">Khôi phục</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...data.repos]
                      .sort((a, b) => b.deployments - a.deployments || a.path_with_namespace.localeCompare(b.path_with_namespace))
                      .map((r) => (
                        <tr key={r.id} className={r.deployments ? undefined : "muted"}>
                          <td>{r.path_with_namespace}</td>
                          <td>
                            {me?.is_admin ? (
                              <ProductionEnv repo={r} onSaved={() => void mutate()} />
                            ) : (
                              r.production_environment ?? (r.production_detected.length ? r.production_detected.join(", ") : <span className="faint">—</span>)
                            )}
                          </td>
                          <td className="num">{r.deployments}</td>
                          <td className="num nowrap">{hours(r.lead_time_h.median)}</td>
                          <td className="num">{pct(r.change_failure_rate)}</td>
                          <td className="num nowrap">{hours(r.recovery_h.median)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}
      </SubPanel>
    </>
  );
}
