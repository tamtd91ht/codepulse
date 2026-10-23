"use client";

import Link from "next/link";
import { use } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Avatar, Card, ChartTip, EmptyState, ExternalLink, Kpi, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, date, num, ratio, weekLabel } from "@/lib/format";
import type { BoardFinding, CategoryWeek, MetricsSeries, Page, Person, ReviewListItem } from "@/lib/types";

function Heatmap({ cells }: { cells: CategoryWeek[] }) {
  const weeks = [...new Set(cells.map((c) => c.week))].sort();
  const categories = [...new Set(cells.map((c) => c.category))].sort();
  if (!weeks.length)
    return (
      <EmptyState icon="checkCircle" tone="good" title="Chưa có finding nào trong 12 tuần">
        Loại lỗi theo tuần xuất hiện ở đây khi có review.
      </EmptyState>
    );
  const max = Math.max(...cells.map((c) => c.count));
  const count = (w: string, c: string) => cells.find((x) => x.week === w && x.category === c)?.count ?? 0;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Loại lỗi theo tuần (cuộn ngang)">
      <div
        className="heatmap"
        role="table"
        aria-label="Số finding theo loại lỗi và tuần"
        style={{ gridTemplateColumns: `120px repeat(${weeks.length}, minmax(34px, 1fr))` }}
      >
        <div role="row" style={{ display: "contents" }}>
          <span role="columnheader" aria-label="Loại lỗi" />
          {weeks.map((w) => (
            <span key={w} className="col-label" role="columnheader">
              {weekLabel(w)}
            </span>
          ))}
        </div>
        {categories.map((c) => (
          <div key={c} role="row" style={{ display: "contents" }}>
            <span className="row-label" role="rowheader">
              {CATEGORY_LABEL[c] ?? c}
            </span>
            {weeks.map((w) => {
              const n = count(w, c);
              // A narrow tint range keeps the default text colour readable in both themes.
              const alpha = n ? 0.15 + (0.35 * n) / max : 0;
              return (
                <span
                  key={w}
                  className="cell"
                  role="cell"
                  aria-label={`${n} finding`}
                  title={`${CATEGORY_LABEL[c] ?? c}, tuần ${weekLabel(w)}: ${n} finding`}
                  style={n ? { background: `color-mix(in srgb, var(--primary) ${Math.round(alpha * 100)}%, var(--surface-2))` } : undefined}
                >
                  {n || ""}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

const REVIEW_STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  done: { label: "Xong", tone: "good" },
  failed: { label: "Lỗi", tone: "bad" },
  skipped: { label: "Bỏ qua", tone: "warn" },
  pending: { label: "Đang chờ", tone: "info" },
  running: { label: "Đang chạy", tone: "info" },
};

export default function MemberDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: person, error } = useApi<Person>(`/people/${id}`);
  const { data: series, error: seriesError } = useApi<MetricsSeries>(`/metrics/users/${id}${qs({ period: "week" })}`);
  const { data: heat, error: heatError } = useApi<CategoryWeek[]>(`/metrics/users/${id}/categories?weeks=12`);
  const { data: reviews, error: reviewsError } = useApi<Page<ReviewListItem>>(`/reviews${qs({ user_id: id, limit: 15 })}`);
  const { data: open, error: openError } = useApi<Page<BoardFinding>>(`/findings${qs({ user_id: id, limit: 50 })}`);

  if (!person) return <Loading error={error} rows={4} />;
  const latest = series?.series.at(-1) ?? null;
  const rank = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const findings = [...(open?.items ?? [])].sort((a, b) => rank[a.severity] - rank[b.severity]);
  const name = person.full_name ?? person.primary_email ?? `#${person.id}`;

  return (
    <>
      <PageHeader
        eyebrow="Member detail"
        title={name}
        subtitle={person.primary_email}
        avatar={<Avatar name={person.full_name} email={person.primary_email} size="lg" />}
      />

      {!series && <Loading error={seriesError} rows={3} />}
      {series && (
        <>
          <div className="grid kpis">
            <Kpi label="Quality Index (tuần gần nhất)" value={latest?.quality_index} change={series.delta.quality_index} />
            <Kpi label="Điểm TB" value={latest?.avg_score} change={series.delta.avg_score} />
            <Kpi
              label="Fix rate"
              unit="%"
              value={latest?.fix_rate === null || !latest ? null : Number(latest.fix_rate) * 100}
              digits={0}
              hint="Tỉ lệ lỗi critical/high đã sửa ở lần review sau"
            />
            <Kpi label="QI trung bình team" value={series.team_quality_index} />
          </div>
          <Card title="12 tuần gần nhất" subtitle="Điểm trung bình và Quality Index theo tuần">
            <div className="chart-box">
              <ResponsiveContainer>
                <LineChart
                  data={series.series.map((m) => ({
                    week: weekLabel(m.period_start),
                    score: m.avg_score === null ? null : Number(m.avg_score),
                    qi: m.quality_index === null ? null : Number(m.quality_index),
                  }))}
                  margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
                >
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip content={<ChartTip />} cursor={{ stroke: "var(--border-strong)" }} />
                  <Legend iconType="circle" iconSize={8} />
                  <Line dataKey="score" name="Điểm TB" stroke="var(--chart-1)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} connectNulls />
                  <Line
                    dataKey="qi"
                    name="Quality Index"
                    stroke="var(--chart-2)"
                    strokeWidth={2.5}
                    strokeDasharray="6 4"
                    dot={false}
                    activeDot={{ r: 4 }}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </>
      )}

      <Card title="Loại lỗi theo tuần" subtitle="Số finding của mỗi loại, 12 tuần gần nhất">
        {heat ? <Heatmap cells={heat} /> : <Loading error={heatError} rows={4} />}
      </Card>

      <div className="grid two">
        <Card title="MR / commit gần đây" flush>
          {reviews ? (
            reviews.items.length === 0 ? (
              <EmptyState title="Chưa có review nào">Review xuất hiện khi MR hoặc commit được thu thập và chấm.</EmptyState>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Thay đổi</th>
                      <th>Repo</th>
                      <th className="num">Điểm</th>
                      <th>Ngày</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {reviews.items.map((r) => {
                      const st = REVIEW_STATUS[r.status];
                      return (
                        <tr key={r.id}>
                          <td>
                            <Link href={`/reviews/${r.id}`}>{r.target}</Link>
                          </td>
                          <td className="muted">{r.project}</td>
                          <td className="num">
                            {r.status === "done" ? <strong>{num(r.score, 0)}</strong> : <Pill tone={st?.tone}>{st?.label ?? r.status}</Pill>}
                          </td>
                          <td className="muted">{date(r.finished_at)}</td>
                          <td>{r.web_url && <ExternalLink href={r.web_url}>GitLab</ExternalLink>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <div style={{ padding: "0 18px 16px" }}>
              <Loading error={reviewsError} rows={4} />
            </div>
          )}
        </Card>

        <Card title="Finding đang mở" subtitle="Critical và high xếp trước">
          {open ? (
            findings.length ? (
              <ul className="finding-list">
                {findings.map((f) => (
                  <li key={f.id}>
                    <SeverityBadge severity={f.severity} />
                    <div className="body">
                      <Link href={`/reviews/${f.review_id}`}>{f.title}</Link>
                      <div className="meta">
                        <code>
                          {f.file_path}:{f.line_start}
                        </code>{" "}
                        · {f.age_days} ngày
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon="checkCircle" tone="good" title="Không có finding đang mở" />
            )
          ) : (
            <Loading error={openError} rows={4} />
          )}
        </Card>
      </div>
      <p className="muted small" style={{ marginTop: 12 }}>
        Fix rate {ratio(latest?.fix_rate ?? null)} = tỉ lệ lỗi critical/high đã sửa ở lần review sau.
      </p>
    </>
  );
}
