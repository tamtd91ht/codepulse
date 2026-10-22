"use client";

import Link from "next/link";
import { use, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Kpi, Loading, Sparkline } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, SEVERITIES, SEVERITY_LABEL, num, ratio, shiftWeeks, thisWeek, weekLabel } from "@/lib/format";
import type { Hotspots, Leaderboard, MetricsSeries, Team } from "@/lib/types";

export default function TeamOverview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [period, setPeriod] = useState<"week" | "month">("week");
  const [week, setWeek] = useState(thisWeek());
  const start = period === "week" ? week : `${week.slice(0, 7)}-01`;

  const { data: teams } = useApi<Team[]>("/teams/visible");
  const team = teams?.find((t) => String(t.id) === id);
  const { data: series, error } = useApi<MetricsSeries>(
    `/metrics/teams/${id}${qs({ period, to: start })}`,
  );
  const { data: board, error: boardError } = useApi<Leaderboard>(
    `/leaderboard${qs({ team_id: id, period, start })}`,
  );
  const { data: hot } = useApi<Hotspots>(`/metrics/teams/${id}/hotspots${qs({ period, start })}`);

  if (!series) return <Loading error={error} />;
  const current = series.series.find((m) => m.period_start === start) ?? null;
  const d = series.delta;
  const chart = series.series.map((m) => ({
    period: period === "week" ? weekLabel(m.period_start) : m.period_start.slice(0, 7),
    ...Object.fromEntries(SEVERITIES.filter((s) => s !== "info").map((s) => [s, m[s] ?? 0])),
  }));

  return (
    <>
      <h1>{team?.name ?? "Team"}</h1>
      <div className="toolbar">
        <select value={period} onChange={(e) => setPeriod(e.target.value as "week" | "month")}>
          <option value="week">Theo tuần</option>
          <option value="month">Theo tháng</option>
        </select>
        <button onClick={() => setWeek(shiftWeeks(week, period === "week" ? -1 : -4))}>‹</button>
        <span>{period === "week" ? `Tuần bắt đầu ${weekLabel(start)}` : `Tháng ${start.slice(0, 7)}`}</span>
        <button onClick={() => setWeek(shiftWeeks(week, period === "week" ? 1 : 4))} disabled={week >= thisWeek()}>
          ›
        </button>
      </div>

      <div className="grid kpis">
        <Kpi label="Quality Index" value={current?.quality_index} change={d.quality_index} />
        <Kpi label="Critical + High" value={(current?.critical ?? 0) + (current?.high ?? 0)} digits={0}
          change={d.critical === null || d.high === null ? null : (d.critical ?? 0) + (d.high ?? 0)} higherIsBetter={false} />
        <Kpi label="Mật độ lỗi / 100 LOC" value={current?.defect_density} change={d.defect_density} higherIsBetter={false} digits={2} />
        <Kpi label="Build success" value={current?.build_success_rate} change={d.build_success_rate} digits={0} />
      </div>

      <div className="grid two">
        <div className="card">
          <h2>Finding theo mức độ</h2>
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="period" stroke="var(--muted)" fontSize={12} />
                <YAxis allowDecimals={false} stroke="var(--muted)" fontSize={12} />
                <Tooltip />
                <Legend />
                {SEVERITIES.filter((s) => s !== "info").map((s) => (
                  <Bar key={s} dataKey={s} name={SEVERITY_LABEL[s]} stackId="sev" fill={`var(--sev-${s})`} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="card">
          <h2>Điểm nóng của kỳ</h2>
          {hot ? (
            <div className="grid two" style={{ gap: 8 }}>
              <div>
                <div className="muted">Loại lỗi</div>
                <ol>
                  {hot.categories.map((c) => (
                    <li key={c.category}>{CATEGORY_LABEL[c.category] ?? c.category}: {c.count}</li>
                  ))}
                </ol>
              </div>
              <div style={{ minWidth: 0 }}>
                <div className="muted">File / module</div>
                <ol>
                  {hot.files.map((f) => (
                    <li key={f.file_path} style={{ overflowWrap: "anywhere" }}>
                      <code>{f.file_path}</code>: {f.count}
                    </li>
                  ))}
                </ol>
              </div>
            </div>
          ) : (
            <Loading />
          )}
        </div>
      </div>

      <div className="card">
        <h2>Thành viên</h2>
        {board?.members ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Thành viên</th>
                  <th className="num">Reviews</th>
                  <th className="num">Điểm TB</th>
                  <th className="num">Critical</th>
                  <th className="num">High</th>
                  <th className="num">Mật độ</th>
                  <th className="num">Fix rate</th>
                  <th className="num">Quality Index</th>
                  <th>8 tuần</th>
                </tr>
              </thead>
              <tbody>
                {board.members.map((m) => (
                  <tr key={m.user.id}>
                    <td>
                      <Link href={`/members/${m.user.id}`}>{m.user.full_name ?? m.user.primary_email}</Link>
                    </td>
                    <td className="num">{m.reviews_count ?? 0}</td>
                    <td className="num">{num(m.avg_score)}</td>
                    <td className="num">{m.critical ?? 0}</td>
                    <td className="num">{m.high ?? 0}</td>
                    <td className="num">{num(m.defect_density, 2)}</td>
                    <td className="num">{ratio(m.fix_rate)}</td>
                    <td className="num">{m.quality_index === null ? <span className="muted" title="Chưa đủ số review tối thiểu">—</span> : num(m.quality_index)}</td>
                    <td>
                      <Sparkline values={m.trend} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : board ? (
          <p className="muted">Điểm từng người chỉ hiển thị cho lead của team (quyết định Phase 0).</p>
        ) : boardError?.status === 403 ? (
          <p className="muted">Điểm từng người chỉ hiển thị cho lead của team.</p>
        ) : (
          <Loading error={boardError} />
        )}
      </div>
    </>
  );
}
