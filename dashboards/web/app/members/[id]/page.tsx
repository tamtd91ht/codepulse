"use client";

import Link from "next/link";
import { use } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Kpi, Loading, SeverityBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, date, num, ratio, weekLabel } from "@/lib/format";
import type { BoardFinding, CategoryWeek, MetricsSeries, Page, Person, ReviewListItem } from "@/lib/types";

function Heatmap({ cells }: { cells: CategoryWeek[] }) {
  const weeks = [...new Set(cells.map((c) => c.week))].sort();
  const categories = [...new Set(cells.map((c) => c.category))].sort();
  if (!weeks.length) return <p className="muted">Chưa có finding nào trong 12 tuần.</p>;
  const max = Math.max(...cells.map((c) => c.count));
  const count = (w: string, c: string) => cells.find((x) => x.week === w && x.category === c)?.count ?? 0;
  return (
    <div className="table-wrap">
      <div className="heatmap" style={{ gridTemplateColumns: `110px repeat(${weeks.length}, minmax(34px, 1fr))` }}>
        <span />
        {weeks.map((w) => (
          <span key={w} className="muted" style={{ textAlign: "center" }}>{weekLabel(w)}</span>
        ))}
        {categories.map((c) => (
          <div key={c} style={{ display: "contents" }}>
            <span>{CATEGORY_LABEL[c] ?? c}</span>
            {weeks.map((w) => {
              const n = count(w, c);
              const alpha = n ? 0.15 + (0.85 * n) / max : 0;
              return (
                <span key={w} className="cell" title={`${n} finding`}
                  style={{ background: n ? `color-mix(in srgb, var(--accent) ${Math.round(alpha * 100)}%, transparent)` : "var(--bg)" }}>
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

export default function MemberDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: person, error } = useApi<Person>(`/people/${id}`);
  const { data: series } = useApi<MetricsSeries>(`/metrics/users/${id}${qs({ period: "week" })}`);
  const { data: heat } = useApi<CategoryWeek[]>(`/metrics/users/${id}/categories?weeks=12`);
  const { data: reviews } = useApi<Page<ReviewListItem>>(`/reviews${qs({ user_id: id, limit: 15 })}`);
  const { data: open } = useApi<Page<BoardFinding>>(`/findings${qs({ user_id: id, limit: 50 })}`);

  if (!person) return <Loading error={error} />;
  const latest = series?.series.at(-1) ?? null;
  const rank = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const findings = [...(open?.items ?? [])].sort((a, b) => rank[a.severity] - rank[b.severity]);

  return (
    <>
      <h1>{person.full_name ?? person.primary_email}</h1>
      <p className="muted">{person.primary_email}</p>

      {series && (
        <>
          <div className="grid kpis">
            <Kpi label="Quality Index (tuần gần nhất)" value={latest?.quality_index} change={series.delta.quality_index} />
            <Kpi label="Điểm TB" value={latest?.avg_score} change={series.delta.avg_score} />
            <Kpi label="Fix rate" value={latest?.fix_rate === null || !latest ? null : Number(latest.fix_rate) * 100} digits={0} />
            <Kpi label="QI trung bình team" value={series.team_quality_index} />
          </div>
          <div className="card">
            <h2>12 tuần gần nhất</h2>
            <div style={{ height: 240 }}>
              <ResponsiveContainer>
                <LineChart data={series.series.map((m) => ({
                  week: weekLabel(m.period_start),
                  score: m.avg_score === null ? null : Number(m.avg_score),
                  qi: m.quality_index === null ? null : Number(m.quality_index),
                }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="week" stroke="var(--muted)" fontSize={12} />
                  <YAxis domain={[0, 100]} stroke="var(--muted)" fontSize={12} />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="score" name="Điểm TB" stroke="var(--accent)" strokeWidth={2} connectNulls />
                  <Line dataKey="qi" name="Quality Index" stroke="var(--good)" strokeWidth={2} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}

      <div className="card">
        <h2>Loại lỗi theo tuần</h2>
        {heat ? <Heatmap cells={heat} /> : <Loading />}
      </div>

      <div className="grid two">
        <div className="card">
          <h2>MR / commit gần đây</h2>
          {reviews ? (
            <div className="table-wrap">
              <table>
                <tbody>
                  {reviews.items.map((r) => (
                    <tr key={r.id}>
                      <td><Link href={`/reviews/${r.id}`}>{r.target}</Link></td>
                      <td className="muted">{r.project}</td>
                      <td className="num">{r.status === "done" ? num(r.score, 0) : r.status}</td>
                      <td className="muted">{date(r.finished_at)}</td>
                      <td>{r.web_url && <a href={r.web_url} target="_blank" rel="noreferrer">GitLab ↗</a>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Loading />
          )}
        </div>
        <div className="card">
          <h2>Finding đang mở</h2>
          {open ? (
            findings.length ? (
              <ul style={{ paddingLeft: 18 }}>
                {findings.map((f) => (
                  <li key={f.id} style={{ marginBottom: 6 }}>
                    <SeverityBadge severity={f.severity} />{" "}
                    <Link href={`/reviews/${f.review_id}`}>{f.title}</Link>{" "}
                    <span className="muted">{f.file_path}:{f.line_start} · {f.age_days} ngày</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Không có finding đang mở.</p>
            )
          ) : (
            <Loading />
          )}
        </div>
      </div>
      <p className="muted">Fix rate {ratio(latest?.fix_rate ?? null)} = tỉ lệ lỗi critical/high đã sửa ở lần review sau.</p>
    </>
  );
}
