"use client";

import { use, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import {
  Card,
  ChartTip,
  EmptyState,
  Kpi,
  Loading,
  PageHeader,
  Pager,
  PersonLink,
  Segmented,
  SortTh,
  Sparkline,
  useSort,
} from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import {
  CATEGORY_LABEL,
  SEVERITIES,
  SEVERITY_LABEL,
  num,
  ratio,
  shiftMonths,
  shiftWeeks,
  thisMonth,
  thisWeek,
  weekLabel,
  weekOf,
} from "@/lib/format";
import type { Hotspots, Leaderboard, LeaderboardRow, MetricsSeries, Team } from "@/lib/types";

type Period = "week" | "month";

function RankList({ items, warm }: { items: { label: string; count: number; mono?: boolean }[]; warm?: boolean }) {
  if (items.length === 0) return <p className="muted small">Không có finding trong kỳ.</p>;
  const max = Math.max(...items.map((i) => i.count), 1);
  return (
    <ol className="rank-list">
      {items.map((i) => (
        <li key={i.label}>
          <span className="name">{i.mono ? <code>{i.label}</code> : i.label}</span>
          <span className="count">{i.count}</span>
          <span className={`bar${warm ? " warm" : ""}`}>
            <span style={{ width: `${(100 * i.count) / max}%` }} />
          </span>
        </li>
      ))}
    </ol>
  );
}

const cell = (m: LeaderboardRow, key: string): number | string | null => {
  switch (key) {
    case "name":
      return m.user.full_name ?? m.user.primary_email ?? "";
    case "reviews":
      return m.reviews_count ?? 0;
    case "critical":
      return m.critical ?? 0;
    case "high":
      return m.high ?? 0;
    case "score":
      return m.avg_score === null ? null : Number(m.avg_score);
    case "density":
      return m.defect_density === null ? null : Number(m.defect_density);
    case "fix":
      return m.fix_rate === null ? null : Number(m.fix_rate);
    default:
      return m.quality_index === null ? null : Number(m.quality_index);
  }
};

function MembersTable({ members }: { members: LeaderboardRow[] }) {
  const { sorted, sort, toggle } = useSort(members, { key: "qi", dir: "desc" }, cell);
  if (members.length === 0)
    return (
      <EmptyState icon="members" title="Chưa có dữ liệu thành viên trong kỳ này">
        Số liệu xuất hiện sau khi có review trong kỳ.
      </EmptyState>
    );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortTh id="name" sort={sort} onSort={toggle}>
              Thành viên
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
            <SortTh id="density" sort={sort} onSort={toggle} num>
              Mật độ
            </SortTh>
            <SortTh id="fix" sort={sort} onSort={toggle} num>
              Fix rate
            </SortTh>
            <SortTh id="qi" sort={sort} onSort={toggle} num>
              Quality Index
            </SortTh>
            <th>8 tuần</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => (
            <tr key={m.user.id}>
              <td>
                <PersonLink id={m.user.id} name={m.user.full_name} email={m.user.primary_email} />
              </td>
              <td className="num">{m.reviews_count ?? 0}</td>
              <td className="num">{num(m.avg_score)}</td>
              <td className={`num${m.critical ? " bad" : ""}`}>{m.critical ?? 0}</td>
              <td className="num">{m.high ?? 0}</td>
              <td className="num">{num(m.defect_density, 2)}</td>
              <td className="num">{ratio(m.fix_rate)}</td>
              <td className="num">
                {m.quality_index === null ? (
                  <span className="muted" aria-label="Chưa đủ số review tối thiểu" title="Chưa đủ số review tối thiểu">
                    —
                  </span>
                ) : (
                  <strong>{num(m.quality_index)}</strong>
                )}
              </td>
              <td>
                <Sparkline values={m.trend} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function TeamOverview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [period, setPeriod] = useState<Period>("week");
  // One cursor: the Monday of a week, or the first day of a month, depending on `period`.
  // Set after mount: "this week" is the viewer's, and the server may sit in another timezone.
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
  const step = (by: number) =>
    setCursor((c) => (c === null ? c : period === "week" ? shiftWeeks(c, by) : shiftMonths(c, by)));

  const { data: teams } = useApi<Team[]>("/teams/visible");
  const team = teams?.find((t) => String(t.id) === id);
  const keep = { keepPreviousData: true };
  const { data: series, error } = useApi<MetricsSeries>(start && `/metrics/teams/${id}${qs({ period, to: start })}`, keep);
  const { data: board, error: boardError } = useApi<Leaderboard>(start && `/leaderboard${qs({ team_id: id, period, start })}`, keep);
  const { data: hot, error: hotError } = useApi<Hotspots>(start && `/metrics/teams/${id}/hotspots${qs({ period, start })}`, keep);

  const periodLabel =
    start === null ? "…" : period === "week" ? `Tuần từ ${weekLabel(start)}` : `Tháng ${start.slice(5, 7)}/${start.slice(0, 4)}`;
  const header = (
    <PageHeader
      eyebrow="Team overview"
      title={team?.name ?? "Team"}
      subtitle="Chỉ số của kỳ so với kỳ trước, finding theo mức độ, điểm nóng và bảng thành viên."
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
            label={periodLabel}
            onPrev={() => step(-1)}
            onNext={() => step(1)}
            nextDisabled={start === null || start >= latest}
            prevDisabled={start === null}
          />
        </>
      }
    />
  );

  if (!series)
    return (
      <>
        {header}
        <Loading error={error} rows={4} />
      </>
    );

  const current = series.series.find((m) => m.period_start === start) ?? null;
  const d = series.delta;
  const chart = series.series.map((m) => ({
    period: period === "week" ? weekLabel(m.period_start) : m.period_start.slice(0, 7),
    ...Object.fromEntries(SEVERITIES.filter((s) => s !== "info").map((s) => [s, m[s] ?? 0])),
  }));

  return (
    <>
      {header}

      <div className="grid kpis">
        <Kpi label="Quality Index" value={current?.quality_index} change={d.quality_index} hint="50% điểm TB + 20% mật độ lỗi thấp + 15% build + 15% fix rate" />
        <Kpi
          label="Critical + High"
          value={(current?.critical ?? 0) + (current?.high ?? 0)}
          digits={0}
          change={d.critical === null || d.high === null ? null : (d.critical ?? 0) + (d.high ?? 0)}
          higherIsBetter={false}
        />
        <Kpi label="Mật độ lỗi" unit="/ 100 LOC" value={current?.defect_density} change={d.defect_density} higherIsBetter={false} digits={2} />
        <Kpi label="Build success" unit="%" value={current?.build_success_rate} change={d.build_success_rate} digits={0} />
      </div>

      <div className="grid two">
        <Card title="Finding theo mức độ" subtitle={period === "week" ? "12 tuần gần nhất" : "Các tháng gần nhất"}>
          <div className="chart-box">
            <ResponsiveContainer>
              <BarChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barCategoryGap="28%">
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="period" tickLine={false} axisLine={false} fontSize={12} />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                <Tooltip content={<ChartTip digits={0} />} cursor={{ fill: "var(--surface-2)" }} />
                <Legend iconType="circle" iconSize={8} />
                {SEVERITIES.filter((s) => s !== "info").map((s, i, all) => (
                  <Bar
                    key={s}
                    dataKey={s}
                    name={SEVERITY_LABEL[s]}
                    stackId="sev"
                    fill={`var(--sev-${s})`}
                    radius={i === all.length - 1 ? [4, 4, 0, 0] : 0}
                    maxBarSize={36}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Điểm nóng của kỳ" subtitle="Loại lỗi và file / module có nhiều finding nhất">
          {hot ? (
            <div className="grid hotspots" style={{ gap: 20 }}>
              <div>
                <h3 className="muted" style={{ marginTop: 0 }}>
                  Loại lỗi
                </h3>
                <RankList items={hot.categories.map((c) => ({ label: CATEGORY_LABEL[c.category] ?? c.category, count: c.count }))} />
              </div>
              <div style={{ minWidth: 0 }}>
                <h3 className="muted" style={{ marginTop: 0 }}>
                  File / module
                </h3>
                <RankList items={hot.files.map((f) => ({ label: f.file_path, count: f.count, mono: true }))} warm />
              </div>
            </div>
          ) : (
            <Loading error={hotError} rows={4} />
          )}
        </Card>
      </div>

      <Card
        title="Thành viên"
        subtitle="Bấm tiêu đề cột để sắp xếp. Quality Index chỉ hiện khi đủ số review tối thiểu."
        flush
      >
        {board?.members ? (
          <MembersTable members={board.members} />
        ) : board || boardError?.status === 403 ? (
          <EmptyState icon="user" title="Điểm từng người chỉ hiển thị cho lead của team">
            Quyết định Phase 0: manager xem số liệu tổng hợp, không xem điểm cá nhân.
          </EmptyState>
        ) : (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={boardError} rows={4} />
          </div>
        )}
      </Card>
    </>
  );
}
