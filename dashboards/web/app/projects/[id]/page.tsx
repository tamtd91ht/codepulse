"use client";

import Link from "next/link";
import { use, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PeriodPicker } from "@/components/PeriodPicker";
import { CriteriaSummaryBlock } from "@/components/CriteriaScores";
import { ProjectCriteria } from "@/components/ProjectCriteria";
import { ProjectRepos } from "@/components/ProjectRepos";
import { ReviewResults } from "@/components/ReviewResults";
import { Card, ChartTip, Drawer, EmptyState, Loading, PageHeader, SplitBar, StatStrip, StripStat } from "@/components/ui";
import { scoreTone } from "@/components/Evaluation";
import { type BoardScope, boardLink, can, canSetupProject, qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, periodLabel, rangeLabel, shiftDays, weekLabel } from "@/lib/format";
import { scopeNote, useCycleScope } from "@/lib/timeScope";
import type { Me, ProjectDetail } from "@/lib/types";

type Period = "week" | "month" | "range";
/** "range": whole days (e.g. "Hôm nay", 00:00 to now), from RANGE_MIN on, as on Tổng quan. */
const RANGE_MIN = "2026-04-01";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  // The dashboard-wide period (lib/timeScope): a week, a month or a range of days.
  const time = useCycleScope({ range: true, rangeMin: RANGE_MIN });
  const period: Period = time?.period ?? "week";
  const start = time && time.period !== "range" ? time.start : null;
  const range = time?.range ?? { from: "", to: "" };
  const today = time?.today ?? "";
  const rangeOk = time !== null && range.from >= RANGE_MIN && range.from <= range.to && range.to <= today;
  const [criteriaOpen, setCriteriaOpen] = useState(false);
  const { data: me } = useApi<Me>("/auth/me");
  const url =
    period === "range"
      ? rangeOk && `/metrics/projects/${id}${qs({ period, start: range.from, end: range.to })}`
      : start && `/metrics/projects/${id}${qs({ period, start })}`;
  const { data, error } = useApi<ProjectDetail>(url || null, { keepPreviousData: true });

  const note = scopeNote(time);
  const header = (
    <>
    <PageHeader
      eyebrow={
        <Link href="/overview" className="muted">
          ← Tổng quan
        </Link>
      }
      title={data?.project.name ?? "Dự án"}
      subtitle={data?.project.description ?? undefined}
      actions={
        <>
          {/* docs/specs/21 §10: the dự án's Lead / Manager (admins: Admin › Dự án & Repo). */}
          {me && !me.is_admin && data && canSetupProject(me, data.project.id) && (
            <Link className="button" href={`/project-admin?open=${data.project.id}`}>
              Cấu hình dự án
            </Link>
          )}
          <PeriodPicker time={time} rangeMin={RANGE_MIN} />
        </>
      }
    />
    {note && <p className="muted small scope-note">{note}</p>}
    </>
  );
  if (!data)
    return (
      <>
        {header}
        <Loading error={error} rows={4} />
      </>
    );

  const t = data.totals;
  // The response's own period (not the picker's, while the next one loads).
  const shownLabel =
    data.period_type === "range"
      ? rangeLabel(data.period_start, data.period_end ?? data.period_start)
      : periodLabel(data.period_type, data.period_start);
  const seriesPeriod = data.series_period ?? (data.period_type === "month" ? "month" : "week");
  const series = data.series.map((s) => ({
    period: seriesPeriod === "week" ? weekLabel(s.period_start) : s.period_start.slice(5, 7),
    reviews: s.reviews,
    critical: s.critical,
    high: s.high,
    score: s.avg_score,
  }));
  const scope: BoardScope = {
    project: data.project,
    period: { range: data.period_range, label: shownLabel },
  };
  const maxCat = Math.max(...data.categories.map((c) => c.count), 1);
  const days = {
    from: data.period_start,
    // The period's last day: the day before the next one starts.
    to: shiftDays(data.next_period_start, -1),
    label: shownLabel,
  };

  return (
    <>
      {header}
      <StatStrip label="Kết quả của dự án trong kỳ">
        <StripStat label="Review" value={t.reviews} change={t.delta.reviews} />
        <StripStat label="Điểm TB" value={t.avg_score} digits={1} change={t.delta.avg_score} tone={scoreTone(t.avg_score) ?? undefined} />
        <StripStat label="Critical mới" value={t.critical} change={t.delta.critical} higherIsBetter={false} tone={t.critical ? "bad" : undefined} href={boardLink("critical", scope)} />
        <StripStat label="High mới" value={t.high} change={t.delta.high} higherIsBetter={false} tone={t.high ? "warn" : undefined} href={boardLink("high", scope)} />
        <StripStat label="Critical mở" title="Chưa xử lý, mọi thời kỳ" value={t.open_critical} tone={t.open_critical ? "bad" : undefined} href={boardLink("critical", { project: scope.project })} />
        <StripStat label="High mở" title="Chưa xử lý, mọi thời kỳ" value={t.open_high} tone={t.open_high ? "warn" : undefined} href={boardLink("high", { project: scope.project })} />
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
        <StripStat label="Repo" value={t.repos} sub={`${t.loc.toLocaleString("vi-VN")} LOC`} />
      </StatStrip>

      <div className="grid wide-left">
        <Card title="Xu hướng" actions={<span className="muted small">{data.series.length} {seriesPeriod === "week" ? "tuần" : "tháng"}</span>}>
          <div className="chart-box">
            <ResponsiveContainer>
              <ComposedChart data={series} margin={{ top: 8, right: 0, bottom: 0, left: -16 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="period" tickLine={false} axisLine={false} fontSize={12} />
                <YAxis yAxisId="n" allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                <YAxis yAxisId="score" orientation="right" domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} width={36} />
                <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
                <Legend iconType="circle" iconSize={8} />
                <Bar yAxisId="n" dataKey="reviews" name="Review" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                <Bar yAxisId="n" dataKey="critical" name="Critical" fill="var(--sev-critical)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                <Bar yAxisId="n" dataKey="high" name="High" fill="var(--sev-high)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                <Line yAxisId="score" dataKey="score" name="Điểm TB" stroke="var(--chart-2)" strokeWidth={2.5} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Loại lỗi" actions={<span className="muted small" title="Số finding theo loại trong kỳ, mọi mức độ">{data.categories.reduce((n, c) => n + c.count, 0)} finding</span>}>
          {data.categories.length === 0 ? (
            <EmptyState icon="checkCircle" tone="good" title="Không có finding trong kỳ" />
          ) : (
            <ol className="rank-list">
              {data.categories.map((c) => (
                <li key={c.category}>
                  <span className="name">{CATEGORY_LABEL[c.category] ?? c.category}</span>
                  <span className="count">{c.count}</span>
                  <span className="bar">
                    <span style={{ width: `${(100 * c.count) / maxCat}%` }} />
                  </span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <CriteriaSummaryBlock
        scope={{ business_project_id: data.project.id }}
        to={days.to}
        onConfigure={can(me, "criteria.assign") ? () => setCriteriaOpen(true) : undefined}
      />
      {criteriaOpen && (
        <Drawer title={`Bộ tiêu chí · ${data.project.name}`} width={640} onClose={() => setCriteriaOpen(false)}>
          <ProjectCriteria projectId={data.project.id} projectName={data.project.name} />
        </Drawer>
      )}

      <ProjectRepos repos={data.repos} businessProjectId={data.project.id} scope={scope} period={days} />

      <ReviewResults businessProjectId={data.project.id} repos={data.repos} period={days} id="review-results-project" defaultCollapsed />
    </>
  );
}
