"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Score, scoreTone } from "@/components/Evaluation";
import { Icon } from "@/components/icons";
import { Card, ChartTip, CountLink, EmptyState, Loading, PageHeader, Pager, Segmented, Sparkline, SplitBar, StatStrip, StripStat } from "@/components/ui";
import { type BoardScope, boardLink, qs, useApi } from "@/lib/api";
import { delta, localDay, num, periodLabel, shiftMonths, shiftWeeks, thisMonth, thisWeek, weekLabel, weekOf } from "@/lib/format";
import type { Me, ProjectEntry, ProjectsOverview } from "@/lib/types";

type Period = "week" | "month";
type View = "grid" | "table";
const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];
const VIEW_STORE = "cp:project-view";

/** The period (its first day) holding a dự án's latest review, when the shown one has none. */
const lastPeriod = (p: ProjectEntry, period: Period): string | null => {
  if (p.reviews > 0 || !p.last_review_at) return null;
  const day = localDay(new Date(p.last_review_at));
  return period === "week" ? weekOf(day) : `${day.slice(0, 7)}-01`;
};

const health = (p: ProjectEntry) => (p.open_critical ? "critical" : p.open_high ? "high" : p.reviews ? "ok" : "idle");

/** Critical / high as two small chips (zeros fade); each opens its findings when it has any. */
function SevPair({ critical, high, hrefC, hrefH, what }: { critical: number; high: number; hrefC?: string | null; hrefH?: string | null; what: string }) {
  const label = `${critical} critical, ${high} high ${what}`;
  return (
    <span className="sev-row" title={label}>
      <span className={`sev-count critical${critical ? "" : " zero"}`}>
        <CountLink n={critical} href={hrefC ?? null} />
      </span>
      <span className={`sev-count high${high ? "" : " zero"}`}>
        <CountLink n={high} href={hrefH ?? null} />
      </span>
      <span className="visually-hidden">{label}</span>
    </span>
  );
}

/** One dự án at a glance: score and its change, the trend, open critical / high; opens the dự án. */
function ProjectTile({ p, period }: { p: ProjectEntry; period: Period }) {
  const change = p.delta.avg_score;
  const last = lastPeriod(p, period);
  const body = (
    <>
      <span className="p-name">{p.name}</span>
      <span className="p-meta">
        {p.repos} repo · {p.reviews} review
        {last && <> · gần nhất: {periodLabel(period, last).toLowerCase()}</>}
      </span>
      <span className="p-score">
        {p.avg_score === null ? (
          <span className="faint" title={last ? "Chưa có review trong kỳ này; điểm tính theo thời gian commit" : undefined}>
            —
          </span>
        ) : <span className={`score ${scoreTone(p.avg_score) ?? ""}`}>{num(p.avg_score, 1)}</span>}
        {change !== null && change !== 0 && (
          <span className={`kstat-delta ${change > 0 ? "up" : "down"}`} title="Điểm TB so với kỳ trước">
            <Icon name={change > 0 ? "trendUp" : "trendDown"} size={11} />
            {delta(change, 1)}
          </span>
        )}
      </span>
      <span className="p-spark">
        <Sparkline values={p.trend.map((v) => (v === null ? null : String(v)))} />
      </span>
      <span className="p-sev">
        <SevPair critical={p.open_critical} high={p.open_high} what="đang mở" />
      </span>
    </>
  );
  return p.id === null ? (
    <div className={`proj-tile h-${health(p)}`} title="Repo chưa gom vào dự án nào">
      {body}
    </div>
  ) : (
    <Link href={`/projects/${p.id}`} className={`proj-tile h-${health(p)}`}>
      {body}
    </Link>
  );
}

/** "Xem tuần từ 19/10": moves the page to the period of the dự án's latest review. */
function JumpToLast({ p, period, onJump }: { p: ProjectEntry; period: Period; onJump: (start: string) => void }) {
  const last = lastPeriod(p, period);
  if (!last) return null;
  return (
    <button type="button" className="link-btn small" onClick={() => onJump(last)} title="Chuyển tới kỳ có review gần nhất">
      xem {periodLabel(period, last).toLowerCase()}
    </button>
  );
}

function ProjectTable({
  projects,
  inPeriod,
  period,
  onJump,
}: {
  projects: ProjectEntry[];
  inPeriod: BoardScope["period"];
  period: Period;
  onJump: (start: string) => void;
}) {
  const link = (severity: "critical" | "high", p: ProjectEntry, period: boolean) =>
    p.id === null ? null : boardLink(severity, { project: { id: p.id, name: p.name }, period: period ? inPeriod : undefined });
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Dự án</th>
            <th className="num">Repo</th>
            <th className="num">Review</th>
            <th className="num">Điểm TB</th>
            <th>Mới C/H</th>
            <th>Mở C/H</th>
            <th className="num">Medium</th>
            <th className="num">Low</th>
            <th className="num">LOC</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.id ?? "none"}>
              <td>
                <span className={`health-dot h-${health(p)}`} aria-hidden="true" />{" "}
                {p.id === null ? <span className="muted">{p.name}</span> : <Link href={`/projects/${p.id}`}>{p.name}</Link>}
              </td>
              <td className="num">{p.repos}</td>
              <td className="num">{p.reviews}</td>
              <td className="num">
                <Score value={p.avg_score} strong />
                <JumpToLast p={p} period={period} onJump={onJump} />
              </td>
              <td className="nowrap">
                <SevPair critical={p.critical} high={p.high} what="mới trong kỳ" hrefC={link("critical", p, true)} hrefH={link("high", p, true)} />
              </td>
              <td className="nowrap">
                <SevPair critical={p.open_critical} high={p.open_high} what="đang mở" hrefC={link("critical", p, false)} hrefH={link("high", p, false)} />
              </td>
              <td className="num">{p.medium || <span className="faint">0</span>}</td>
              <td className="num">{p.low || <span className="faint">0</span>}</td>
              <td className="num muted">{p.loc.toLocaleString("vi-VN")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function OverviewPage() {
  const [period, setPeriod] = useState<Period>("week");
  const [cursor, setCursor] = useState<string | null>(null);
  useEffect(() => {
    setCursor(thisWeek());
  }, []);
  const start = cursor === null ? null : period === "week" ? cursor : `${cursor.slice(0, 7)}-01`;
  const latest = period === "week" ? thisWeek() : thisMonth();
  const changePeriod = (p: Period) => {
    setPeriod(p);
    setCursor((c) => (c === null ? c : p === "month" ? `${c.slice(0, 7)}-01` : c >= thisMonth() ? thisWeek() : weekOf(c)));
  };
  const step = (by: number) => setCursor((c) => (c === null ? c : period === "week" ? shiftWeeks(c, by) : shiftMonths(c, by)));

  const { data, error } = useApi<ProjectsOverview>(start && `/metrics/projects${qs({ period, start })}`, { keepPreviousData: true });
  const { data: me } = useApi<Me>("/auth/me");
  const [view, setView] = useState<View>("grid");
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(VIEW_STORE);
      if (v === "grid" || v === "table") setView(v);
    } catch {
      /* blocked storage: keep the default */
    }
  }, []);
  const changeView = (v: View) => {
    setView(v);
    try {
      window.localStorage.setItem(VIEW_STORE, v);
    } catch {
      /* blocked storage: the choice just does not persist */
    }
  };

  const header = (
    <PageHeader
      eyebrow="Tổng quan"
      title="Chất lượng code theo dự án"
      actions={
        <>
          <Segmented<Period>
            label="Chu kỳ"
            value={period}
            onChange={changePeriod}
            options={[
              { value: "week", label: "Tuần" },
              { value: "month", label: "Tháng" },
            ]}
          />
          <Pager
            label={periodLabel(period, start)}
            onPrev={() => step(-1)}
            onNext={() => step(1)}
            nextDisabled={start === null || start >= latest}
            prevDisabled={start === null}
          />
        </>
      }
    />
  );
  if (!data)
    return (
      <>
        {header}
        <Loading error={error} rows={4} />
      </>
    );

  const t = data.totals;
  // Dự án with reviews, none of them in this period: they show "—", so say where to look.
  const idle = data.projects.filter((p) => lastPeriod(p, period) !== null);
  const named = data.projects.filter((p) => p.id !== null);
  const bars = data.projects.map((p) => ({
    name: p.name,
    reviews: p.reviews,
    critical: p.critical,
    high: p.high,
  }));
  // From the response, not the cursor: while the next period loads, links keep the shown one.
  const inPeriod = { range: data.period_range, label: periodLabel(data.period_type, data.period_start) };
  const trendRows = data.trend_starts.map((s, i) => ({
    period: period === "week" ? weekLabel(s) : s.slice(5, 7),
    ...Object.fromEntries(named.slice(0, 6).map((p) => [p.name, p.trend[i]])),
  }));

  return (
    <>
      {header}
      <StatStrip label="Kết quả của mọi dự án trong kỳ">
        <StripStat label="Review" value={t.reviews} change={t.delta.reviews} />
        <StripStat label="Điểm TB" title="Trung bình điểm review (0–100) của mọi dự án" value={t.avg_score} digits={1} change={t.delta.avg_score} tone={scoreTone(t.avg_score) ?? undefined} />
        <StripStat label="Critical mới" value={t.critical} change={t.delta.critical} higherIsBetter={false} tone={t.critical ? "bad" : undefined} href={boardLink("critical", { period: inPeriod })} />
        <StripStat label="High mới" value={t.high} change={t.delta.high} higherIsBetter={false} tone={t.high ? "warn" : undefined} href={boardLink("high", { period: inPeriod })} />
        <StripStat label="Critical mở" title="Chưa xử lý, mọi thời kỳ" value={t.open_critical} tone={t.open_critical ? "bad" : undefined} href={boardLink("critical")} />
        <StripStat label="High mở" title="Chưa xử lý, mọi thời kỳ" value={t.open_high} tone={t.open_high ? "warn" : undefined} href={boardLink("high")} />
        <StripStat
          label="Tỉ lệ APPROVE"
          value={t.pass_rate}
          unit="%"
          sub={
            <SplitBar
              label="Kết luận"
              parts={[
                { name: "Approve", value: t.approve, color: "var(--good)" },
                { name: "Fix required", value: t.fix_required, color: "var(--sev-high)" },
                { name: "Block", value: t.block, color: "var(--sev-critical)" },
              ]}
            />
          }
        />
        <StripStat label="Dự án" value={t.projects} sub={`${t.repos} repo`} />
      </StatStrip>

      {data.projects.length === 0 ? (
        <Card>
          {me && !me.is_admin ? (
            <EmptyState icon="members" title="Bạn chưa được cấp quyền vào dự án nào">
              Liên hệ admin để được cấp quyền Xem hoặc Quản lý dự án (Admin → Dự án &amp; Repo → chọn dự án → Quyền truy cập).
            </EmptyState>
          ) : (
            <EmptyState icon="folder" title="Chưa có dự án hay repo nào">
              Admin thêm repo và gom vào dự án ở Admin → Dự án &amp; Repo.
            </EmptyState>
          )}
        </Card>
      ) : (
        <>
          <Card
            id="overview-projects"
            title={`Dự án · ${named.length}`}
            actions={
              <Segmented<View>
                label="Kiểu xem"
                value={view}
                onChange={changeView}
                options={[
                  { value: "grid", label: "Lưới" },
                  { value: "table", label: "Bảng" },
                ]}
              />
            }
            flush
          >
            {view === "grid" ? (
              <ul className="proj-grid">
                {data.projects.map((p) => (
                  <li key={p.id ?? "none"}>
                    <ProjectTile p={p} period={period} />
                  </li>
                ))}
              </ul>
            ) : null}
            {view === "grid" && idle.length > 0 && (
              <p className="small muted" style={{ margin: 0, padding: "0 18px 14px" }}>
                Chưa có review trong kỳ (điểm tính theo thời gian commit):{" "}
                {idle.map((p, i) => (
                  <span key={p.id ?? "none"}>
                    {i > 0 && " · "}
                    {p.name} <JumpToLast p={p} period={period} onJump={setCursor} />
                  </span>
                ))}
              </p>
            )}
            {view === "grid" ? null : (
              <ProjectTable projects={data.projects} inPeriod={inPeriod} period={period} onJump={setCursor} />
            )}
          </Card>

          <div className="grid two">
            <Card title="Review và lỗi theo dự án" actions={<span className="muted small">trong kỳ</span>}>
              <div className="chart-box">
                <ResponsiveContainer>
                  <BarChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="30%">
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis
                      dataKey="name"
                      tickLine={false}
                      axisLine={false}
                      fontSize={12}
                      interval={0}
                      tickFormatter={(v: string) => (v.length > 11 ? `${v.slice(0, 10)}…` : v)}
                    />
                    <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                    <Tooltip content={<ChartTip digits={0} />} cursor={{ fill: "var(--surface-2)" }} />
                    <Legend iconType="circle" iconSize={8} />
                    <Bar dataKey="reviews" name="Review" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                    <Bar dataKey="critical" name="Critical mới" fill="var(--sev-critical)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                    <Bar dataKey="high" name="High mới" fill="var(--sev-high)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="Điểm TB theo kỳ" actions={<span className="muted small" title="Tối đa 6 dự án">{data.trend_starts.length} kỳ</span>}>
              <div className="chart-box">
                <ResponsiveContainer>
                  <LineChart data={trendRows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="period" tickLine={false} axisLine={false} fontSize={12} />
                    <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                    <Tooltip content={<ChartTip />} cursor={{ stroke: "var(--border-strong)" }} />
                    <Legend iconType="circle" iconSize={8} />
                    {named.slice(0, 6).map((p, i) => (
                      <Line key={p.name} dataKey={p.name} stroke={COLORS[i % COLORS.length]} strokeWidth={2.5} dot={false} connectNulls />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

        </>
      )}
    </>
  );
}
