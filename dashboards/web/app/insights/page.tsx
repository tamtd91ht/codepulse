"use client";

/**
 * Engineering Insights (docs/specs/20 §10): team-level flow, review load and knowledge risk.
 * Numbers describe a dự án or repo, never rank people; names show only where the API allows.
 */

import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { DateRangePicker } from "@/components/DateRangePicker";
import { Alert, Card, ChartTip, EmptyState, ExternalLink, Loading, PageHeader, Pager, Pill, Segmented, SplitBar, StatStrip, StripStat } from "@/components/ui";
import { type ApiError, qs, useApi } from "@/lib/api";
import { date, dateTime, localDay, num, periodLabel, rangeLabel, shiftDays, shiftMonths, shiftWeeks, thisMonth, thisWeek, weekLabel, weekOf } from "@/lib/format";
import type {
  InsightConfidence,
  InsightPerson,
  InsightsFlow,
  InsightsKnowledge,
  InsightsMrs,
  InsightsReviewLoad,
  InsightStage,
  Repo,
} from "@/lib/types";

type Period = "week" | "month" | "range";
type Tab = "flow" | "load" | "knowledge";
type Only = "" | "unreviewed" | "xl";
type Query = Record<string, string | number | null | undefined>;

const TABS: { key: Tab; label: string }[] = [
  { key: "flow", label: "Luồng làm việc" },
  { key: "load", label: "Tải review" },
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

function ConfidenceNote({ c }: { c: InsightConfidence | undefined }) {
  if (!c || c.level === "ok") return null;
  return (
    <Alert tone={c.level === "insufficient" ? "warning" : "info"}>
      <strong>{c.level === "insufficient" ? "Chưa đủ dữ liệu" : "Độ tin cậy thấp"}:</strong>{" "}
      {c.reasons.map((r) => r.message + (r.repos?.length ? ` (${r.repos.join(", ")})` : "")).join(" · ")}
    </Alert>
  );
}

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

export default function InsightsPage() {
  const [tab, setTab] = useState<Tab>("flow");
  const [period, setPeriod] = useState<Period>("week");
  const [cursor, setCursor] = useState<string | null>(null);
  const [range, setRange] = useState<{ from: string; to: string }>({ from: "", to: "" });
  const [bpId, setBpId] = useState("");
  const [repoId, setRepoId] = useState("");
  useEffect(() => {
    setCursor(thisWeek());
    try {
      const t = new URLSearchParams(window.location.search).get("tab");
      if (t === "flow" || t === "load" || t === "knowledge") setTab(t);
    } catch {
      /* no URL: keep the default tab */
    }
  }, []);
  const go = (t: Tab) => {
    setTab(t);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", t);
      window.history.replaceState(null, "", url);
    } catch {
      /* the tab just is not kept in the URL */
    }
  };
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ flow: null, load: null, knowledge: null });
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.key === tab);
    const next = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i + TABS.length - 1) % TABS.length : -1;
    if (next < 0) return;
    e.preventDefault();
    go(TABS[next].key);
    tabRefs.current[TABS[next].key]?.focus();
  };

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
      <PageHeader
        eyebrow="Insights"
        title="Luồng làm việc của team"
        subtitle={FOOTER}
        actions={
          tab === "knowledge" ? null : (
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
          )
        }
      />

      <div className="tabs" role="tablist" aria-label="Nội dung Insights" onKeyDown={onTabKey}>
        {TABS.map((t) => (
          <button
            key={t.key}
            ref={(el) => {
              tabRefs.current[t.key] = el;
            }}
            type="button"
            role="tab"
            id={`tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`panel-${t.key}`}
            tabIndex={tab === t.key ? 0 : -1}
            className={tab === t.key ? "active" : undefined}
            onClick={() => go(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="toolbar filters">
        <label className="filter">
          <span className="small muted">Dự án</span>
          <select
            value={bpId}
            onChange={(e) => {
              setBpId(e.target.value);
              setRepoId("");
            }}
            aria-label="Lọc theo dự án"
          >
            <option value="">Mọi dự án</option>
            {projects.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Repo</span>
          <select value={repoId} onChange={(e) => setRepoId(e.target.value)} aria-label="Lọc theo repo">
            <option value="">Mọi repo</option>
            {repoOptions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.path_with_namespace}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab !== "knowledge" && period === "range" && !rangeOk && (
          <Alert tone="info">
            Chọn khoảng từ {date(RANGE_MIN)} đến hôm nay, dài tối đa {RANGE_MAX_DAYS} ngày.
          </Alert>
        )}
        {tab === "flow" && query && <FlowTab query={query} />}
        {tab === "load" && query && <LoadTab query={query} />}
        {tab === "knowledge" && <KnowledgeTab query={scope} />}
      </div>

      <p className="small muted">
        {FOOTER} Thời gian tính theo lịch (cả cuối tuần), dùng trung vị và p75 thay vì trung bình. Bấm các nút &quot;Xem MR&quot; để
        ra danh sách MR tạo nên con số.
      </p>
    </>
  );
}

function FlowTab({ query }: { query: Query }) {
  const { data, error, mutate } = useApi<InsightsFlow>(`/insights/flow${qs(query)}`, { keepPreviousData: true });
  const [drill, setDrill] = useState<{ stage: InsightStage; only: Only }>({ stage: "time_to_merge", only: "" });
  const listRef = useRef<HTMLDivElement>(null);
  const open = (stage: InsightStage, only: Only = "") => {
    setDrill({ stage, only });
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
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

  return (
    <>
      <ConfidenceNote c={data.confidence} />
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
      <div className="toolbar">
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
        {data.data_as_of && <span className="small muted">Dữ liệu tới {dateTime(data.data_as_of)}</span>}
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

      <div ref={listRef}>
        <MrList key={JSON.stringify(query)} query={query} drill={drill} onDrill={setDrill} />
      </div>
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

function LoadTab({ query }: { query: Query }) {
  const { data, error, mutate } = useApi<InsightsReviewLoad>(`/insights/review-load${qs(query)}`, { keepPreviousData: true });
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  return (
    <>
      <ConfidenceNote c={data.confidence} />
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
      <div className="grid two">
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
      </div>
    </>
  );
}

function KnowledgeTab({ query }: { query: Query }) {
  const [flaggedOnly, setFlaggedOnly] = useState(true);
  const { data, error, mutate } = useApi<InsightsKnowledge>(`/insights/knowledge${qs(query)}`, { keepPreviousData: true });
  if (!data) return <Pending error={error} retry={() => void mutate()} />;
  const rows = flaggedOnly ? data.modules.filter((m) => m.single_owner || m.orphaned) : data.modules;
  const threshold = Math.round(data.bus_threshold * 100);
  let body: ReactNode;
  if (data.modules.length === 0)
    body = (
      <EmptyState icon="folder" title="Chưa có dữ liệu file trong cửa sổ">
        Repo chưa có commit trong {data.window_days} ngày qua, hoặc admin cần chạy <code>codepulse insights backfill-files</code>.
      </EmptyState>
    );
  else if (rows.length === 0)
    body = (
      <EmptyState icon="checkCircle" tone="good" title="Không có module nào đáng lo">
        Mọi module có thay đổi đều có nhiều người cùng nắm.
      </EmptyState>
    );
  else
    body = (
      <>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Module</th>
                <th className="num" title={`Số người tối thiểu viết ≥ ${threshold}% thay đổi`}>
                  Bus factor
                </th>
                <th>Người nắm nhiều nhất</th>
                <th className="num">Commit</th>
                <th className="num">Dòng đổi</th>
                <th>Sửa gần nhất</th>
                <th>Cảnh báo</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr key={`${m.project_id}:${m.module}`}>
                  <td>
                    <code>{m.module}</code>
                    <div className="small muted">{m.path_with_namespace}</div>
                  </td>
                  <td className="num">{m.bus_factor}</td>
                  <td>
                    {m.owners.map((o, i) => (
                      <div key={i} className="small">
                        <Person p={o} /> <span className="muted">{num(o.share, 0)}%</span>
                      </div>
                    ))}
                  </td>
                  <td className="num">{m.commits}</td>
                  <td className="num muted">{num(m.churn, 0)}</td>
                  <td className="nowrap muted">{date(m.last_change_at)}</td>
                  <td className="nowrap">
                    {m.orphaned && <Pill tone="bad">Người nắm đã vắng</Pill>} {m.single_owner && <Pill tone="warn">Một người nắm</Pill>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <HiddenNote people={rows.flatMap((m) => m.owners)} />
        {data.totals.shown < data.totals.modules && (
          <p className="small muted" style={{ margin: 0, padding: "8px 18px" }}>
            Hiển thị {data.totals.shown}/{data.totals.modules} module (module có cảnh báo trước). Lọc theo dự án hoặc repo để xem phần
            còn lại.
          </p>
        )}
      </>
    );
  return (
    <>
      <ConfidenceNote c={data.confidence} />
      <StatStrip label={`Module trong ${data.window_days} ngày qua`}>
        <StripStat label="Module có thay đổi" value={data.totals.modules} sub={`${data.module_depth} cấp thư mục`} />
        <StripStat
          label="Một người nắm"
          title={`Một người viết hơn ${threshold}% thay đổi, module có ≥ ${data.min_commits} commit`}
          value={data.totals.single_owner}
          tone={data.totals.single_owner ? "warn" : undefined}
        />
        <StripStat
          label="Người nắm đã vắng"
          title={`Người nắm nhiều nhất đã nghỉ (tắt trong CodePulse) hoặc không commit trong ${data.orphan_after_days} ngày`}
          value={data.totals.orphaned}
          tone={data.totals.orphaned ? "bad" : undefined}
        />
      </StatStrip>
      <Card
        id="insights-modules"
        title="Module"
        subtitle="Rủi ro của team khi kiến thức tập trung ở một người, không phải thành tích hay lỗi của người đó"
        actions={
          <Segmented<"flagged" | "all">
            label="Hiện"
            value={flaggedOnly ? "flagged" : "all"}
            onChange={(v) => setFlaggedOnly(v === "flagged")}
            options={[
              { value: "flagged", label: "Có cảnh báo" },
              { value: "all", label: "Tất cả" },
            ]}
          />
        }
        flush
      >
        {body}
      </Card>
    </>
  );
}
