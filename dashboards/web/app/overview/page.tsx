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

import { Icon } from "@/components/icons";
import { Card, ChartTip, CountLink, EmptyState, Kpi, Loading, PageHeader, Pager, Pill, Segmented, Sparkline } from "@/components/ui";
import { boardLink, qs, useApi } from "@/lib/api";
import { num, periodLabel, shiftMonths, shiftWeeks, thisMonth, thisWeek, weekLabel, weekOf } from "@/lib/format";
import type { ProjectEntry, ProjectsOverview } from "@/lib/types";

type Period = "week" | "month";
const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];

function scoreTone(score: number | null): "good" | "warn" | "bad" | undefined {
  if (score === null) return undefined;
  return score >= 80 ? "good" : score >= 65 ? "warn" : "bad";
}

function ProjectCard({ p }: { p: ProjectEntry }) {
  const body = (
    <>
      <div className="head">
        <div style={{ minWidth: 0 }}>
          <div className="name">{p.name}</div>
          <div className="small muted">
            {p.repos} repo · {p.reviews} review
          </div>
        </div>
        {p.id !== null && (
          <span className="faint">
            <Icon name="chevronRight" size={16} />
          </span>
        )}
      </div>
      <div className="big">
        {p.avg_score === null ? "—" : num(p.avg_score, 1)}
        <span className="unit">điểm TB</span>
        {p.delta.avg_score !== null && (
          <span className={`pill ${p.delta.avg_score >= 0 ? "good" : "bad"}`} style={{ marginLeft: 6 }}>
            {p.delta.avg_score > 0 ? "+" : ""}
            {num(p.delta.avg_score, 1)}
          </span>
        )}
      </div>
      <div className="spark">
        <Sparkline values={p.trend.map((v) => (v === null ? null : String(v)))} />
      </div>
      <div className="stats">
        {p.pass_rate !== null && <Pill tone={p.pass_rate >= 80 ? "good" : p.pass_rate >= 50 ? "warn" : "bad"}>{num(p.pass_rate, 0)}% approve</Pill>}
        <Pill tone={p.critical ? "bad" : "good"}>{p.critical} critical mới</Pill>
        <Pill tone={p.high ? "warn" : "good"}>{p.high} high mới</Pill>
        <Pill tone={p.open_critical ? "bad" : undefined}>{p.open_critical} critical đang mở</Pill>
        <Pill tone={p.open_high ? "warn" : undefined}>{p.open_high} high đang mở</Pill>
        {p.delta.reviews !== 0 && (
          <Pill>
            {p.delta.reviews > 0 ? "+" : ""}
            {p.delta.reviews} review
          </Pill>
        )}
      </div>
    </>
  );
  return p.id === null ? (
    <div className="card project-card">{body}</div>
  ) : (
    <Link href={`/projects/${p.id}`} className="card project-card">
      {body}
    </Link>
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

  const header = (
    <PageHeader
      eyebrow="Tổng quan"
      title="Chất lượng code theo dự án"
      subtitle="Số liệu tổng hợp của mọi dự án trong kỳ, so với kỳ trước."
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
      <div className="grid kpis">
        <Kpi label="Reviews trong kỳ" value={t.reviews} digits={0} change={t.delta.reviews} />
        <Kpi label="Điểm trung bình" value={t.avg_score} change={t.delta.avg_score} hint="Trung bình điểm review (0–100) của mọi dự án" />
        <Kpi label="Critical mới" value={t.critical} digits={0} change={t.delta.critical} higherIsBetter={false} href={boardLink("critical", { period: inPeriod })} />
        <Kpi label="High mới" value={t.high} digits={0} change={t.delta.high} higherIsBetter={false} href={boardLink("high", { period: inPeriod })} />
        <Kpi label="Critical đang mở" value={t.open_critical} digits={0} hint="Chưa xử lý, mọi thời kỳ" href={boardLink("critical")} />
        <Kpi label="High đang mở" value={t.open_high} digits={0} hint="Chưa xử lý, mọi thời kỳ" href={boardLink("high")} />
        <Kpi label="Tỉ lệ APPROVE" value={t.pass_rate} unit="%" hint={`${t.approve} approve · ${t.fix_required} fix · ${t.block} block`} />
        <Kpi label="Dự án · Repo" value={t.projects} digits={0} unit={`· ${t.repos} repo`} />
      </div>

      {data.projects.length === 0 ? (
        <Card>
          <EmptyState icon="folder" title="Chưa có dự án hay repo nào">
            Admin thêm repo và gom vào dự án ở Admin → Dự án &amp; Repo.
          </EmptyState>
        </Card>
      ) : (
        <>
          <div className="grid three">
            {data.projects.map((p) => (
              <ProjectCard key={p.id ?? "none"} p={p} />
            ))}
          </div>

          <div className="grid two">
            <Card title="Reviews và lỗi nghiêm trọng theo dự án" subtitle="Trong kỳ đang xem">
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
                    <Bar dataKey="reviews" name="Reviews" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                    <Bar dataKey="critical" name="Critical mới" fill="var(--sev-critical)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                    <Bar dataKey="high" name="High mới" fill="var(--sev-high)" radius={[4, 4, 0, 0]} maxBarSize={40} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="Điểm trung bình theo kỳ" subtitle={`${data.trend_starts.length} kỳ gần nhất, tối đa 6 dự án`}>
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

          <Card title="Bảng dự án" flush>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Dự án</th>
                    <th className="num">Repo</th>
                    <th className="num">Reviews</th>
                    <th className="num">Điểm TB</th>
                    <th className="num">Critical</th>
                    <th className="num">High</th>
                    <th className="num">Medium</th>
                    <th className="num">Low</th>
                    <th className="num">Critical đang mở</th>
                    <th className="num">High đang mở</th>
                    <th className="num">LOC</th>
                  </tr>
                </thead>
                <tbody>
                  {data.projects.map((p) => (
                    <tr key={p.id ?? "none"}>
                      <td>{p.id === null ? <span className="muted">{p.name}</span> : <Link href={`/projects/${p.id}`}>{p.name}</Link>}</td>
                      <td className="num">{p.repos}</td>
                      <td className="num">{p.reviews}</td>
                      <td className="num">
                        {p.avg_score === null ? <span className="faint">—</span> : <Pill tone={scoreTone(p.avg_score)}>{num(p.avg_score, 1)}</Pill>}
                      </td>
                      <td className={`num${p.critical ? " bad" : ""}`}>
                        <CountLink n={p.critical} href={p.id === null ? null : boardLink("critical", { project: { id: p.id, name: p.name }, period: inPeriod })} />
                      </td>
                      <td className="num">
                        <CountLink n={p.high} href={p.id === null ? null : boardLink("high", { project: { id: p.id, name: p.name }, period: inPeriod })} />
                      </td>
                      <td className="num">{p.medium}</td>
                      <td className="num">{p.low}</td>
                      <td className={`num${p.open_critical ? " bad" : ""}`}>
                        <CountLink n={p.open_critical} href={p.id === null ? null : boardLink("critical", { project: { id: p.id, name: p.name } })} />
                      </td>
                      <td className="num">
                        <CountLink n={p.open_high} href={p.id === null ? null : boardLink("high", { project: { id: p.id, name: p.name } })} />
                      </td>
                      <td className="num muted">{p.loc.toLocaleString("vi-VN")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
