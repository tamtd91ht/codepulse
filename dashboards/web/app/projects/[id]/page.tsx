"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
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

import {
  Card,
  ChartTip,
  EmptyState,
  ExternalLink,
  Kpi,
  Loading,
  PageHeader,
  Pager,
  Pill,
  Segmented,
  SortTh,
  useSort,
} from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, VERDICT_LABEL, date, num, shiftMonths, shiftWeeks, thisMonth, thisWeek, weekLabel, weekOf } from "@/lib/format";
import type { ProjectDetail, ProjectRepoRow } from "@/lib/types";

type Period = "week" | "month";

const repoCell = (r: ProjectRepoRow, key: string): number | string | null => {
  switch (key) {
    case "path":
      return r.path_with_namespace;
    case "reviews":
      return r.reviews;
    case "score":
      return r.avg_score;
    case "critical":
      return r.critical;
    case "high":
      return r.high;
    case "open":
      return r.open_severe;
    default:
      return r.last_review_at;
  }
};

function RepoTable({ repos }: { repos: ProjectRepoRow[] }) {
  const { sorted, sort, toggle } = useSort(repos, { key: "reviews", dir: "desc" }, repoCell);
  if (repos.length === 0) return <EmptyState icon="folder" title="Dự án chưa có repo" />;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortTh id="path" sort={sort} onSort={toggle}>
              Repo
            </SortTh>
            <SortTh id="reviews" sort={sort} onSort={toggle} num>
              Reviews
            </SortTh>
            <SortTh id="score" sort={sort} onSort={toggle} num>
              Điểm TB
            </SortTh>
            <SortTh id="critical" sort={sort} onSort={toggle} num>
              Critical
            </SortTh>
            <SortTh id="high" sort={sort} onSort={toggle} num>
              High
            </SortTh>
            <SortTh id="open" sort={sort} onSort={toggle} num>
              Đang mở C/H
            </SortTh>
            <SortTh id="last" sort={sort} onSort={toggle}>
              Review gần nhất
            </SortTh>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id} className={r.tracked ? undefined : "muted"}>
              <td>
                <code>{r.path_with_namespace}</code>
                {!r.tracked && <span className="muted small"> · đã bỏ theo dõi</span>}
              </td>
              <td className="num">{r.reviews}</td>
              <td className="num">{r.avg_score === null ? <span className="faint">—</span> : <strong>{num(r.avg_score, 1)}</strong>}</td>
              <td className={`num${r.critical ? " bad" : ""}`}>{r.critical}</td>
              <td className="num">{r.high}</td>
              <td className={`num${r.open_severe ? " bad" : ""}`}>{r.open_severe}</td>
              <td className="muted">{date(r.last_review_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
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
  const { data, error } = useApi<ProjectDetail>(start && `/metrics/projects/${id}${qs({ period, start })}`, { keepPreviousData: true });

  const header = (
    <PageHeader
      eyebrow={
        <Link href="/overview" className="muted">
          ← Tổng quan
        </Link>
      }
      title={data?.project.name ?? "Dự án"}
      subtitle={data?.project.description ?? "Số liệu tổng hợp của dự án trong kỳ, theo repo và theo thời gian."}
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
            label={start === null ? "…" : period === "week" ? `Tuần từ ${weekLabel(start)}` : `Tháng ${start.slice(5, 7)}/${start.slice(0, 4)}`}
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
  const series = data.series.map((s) => ({
    period: period === "week" ? weekLabel(s.period_start) : s.period_start.slice(5, 7),
    reviews: s.reviews,
    severe: s.severe,
    score: s.avg_score,
  }));
  const maxCat = Math.max(...data.categories.map((c) => c.count), 1);

  return (
    <>
      {header}
      <div className="grid kpis">
        <Kpi label="Reviews trong kỳ" value={t.reviews} digits={0} change={t.delta.reviews} />
        <Kpi label="Điểm trung bình" value={t.avg_score} change={t.delta.avg_score} />
        <Kpi label="Critical + High mới" value={t.severe} digits={0} change={t.delta.severe} higherIsBetter={false} />
        <Kpi label="Critical/High đang mở" value={t.open_severe} digits={0} hint="Chưa xử lý, mọi thời kỳ" />
        <Kpi label="Tỉ lệ APPROVE" value={t.pass_rate} unit="%" hint={`${t.approve} approve · ${t.fix_required} fix required · ${t.block} block`} />
        <Kpi label="Repo" value={t.repos} digits={0} unit={`· ${t.loc.toLocaleString("vi-VN")} LOC`} />
      </div>

      <div className="grid two">
        <Card title="Theo thời gian" subtitle={`${data.series.length} kỳ gần nhất: số review, lỗi nghiêm trọng và điểm trung bình`}>
          <div className="chart-box">
            <ResponsiveContainer>
              <ComposedChart data={series} margin={{ top: 8, right: 0, bottom: 0, left: -16 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="period" tickLine={false} axisLine={false} fontSize={12} />
                <YAxis yAxisId="n" allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                <YAxis yAxisId="score" orientation="right" domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} width={36} />
                <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
                <Legend iconType="circle" iconSize={8} />
                <Bar yAxisId="n" dataKey="reviews" name="Reviews" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                <Bar yAxisId="n" dataKey="severe" name="Critical + High" fill="var(--sev-high)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                <Line yAxisId="score" dataKey="score" name="Điểm TB" stroke="var(--chart-2)" strokeWidth={2.5} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Loại lỗi trong kỳ" subtitle="Số finding theo loại, mọi mức độ">
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

      <Card title="Theo repo" subtitle="Bấm tiêu đề cột để sắp xếp" flush>
        <RepoTable repos={data.repos} />
      </Card>

      <Card title="Review gần đây" subtitle="Những review bạn được xem trong dự án này" flush>
        {data.recent.length === 0 ? (
          <EmptyState title="Chưa có review nào bạn được xem" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thay đổi</th>
                  <th>Repo</th>
                  <th>Kết luận</th>
                  <th className="num">Điểm</th>
                  <th>Ngày</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.recent.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link href={`/reviews/${r.id}`}>{r.target}</Link>
                    </td>
                    <td className="muted">{r.project}</td>
                    <td>{r.verdict ? <span className={`verdict ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span> : <span className="muted">—</span>}</td>
                    <td className="num">{r.status === "done" ? <strong>{num(r.score, 0)}</strong> : <Pill tone={r.status === "failed" ? "bad" : "warn"}>{r.status}</Pill>}</td>
                    <td className="muted">{date(r.finished_at)}</td>
                    <td>{r.web_url && <ExternalLink href={r.web_url}>GitLab</ExternalLink>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
