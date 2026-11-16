"use client";

import { use } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { PeriodPicker } from "@/components/PeriodPicker";
import { Score, scoreTone } from "@/components/Evaluation";
import {
  Card,
  ChartTip,
  CountLink,
  EmptyState,
  Loading,
  PageHeader,
  PersonLink,
  SortTh,
  Sparkline,
  StatStrip,
  StripStat,
  useSort,
} from "@/components/ui";
import { type BoardScope, boardLink, qs, useApi } from "@/lib/api";
import {
  CATEGORY_LABEL,
  SEVERITIES,
  SEVERITY_LABEL,
  num,
  periodLabel,
  rangeLabel,
  ratio,
  shiftDays,
  weekLabel,
} from "@/lib/format";
import { scopeNote, useCycleScope } from "@/lib/timeScope";
import type { Hotspots, Leaderboard, LeaderboardRow, MetricsSeries, Team } from "@/lib/types";

type Period = "week" | "month" | "range";
/** Longest range of days the Team API computes (app/api/routers/metrics.py RANGE_MAX_DAYS). */
const RANGE_MAX_DAYS = 366;

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

const person = (m: LeaderboardRow) => ({ id: m.user.id, name: m.user.full_name ?? m.user.primary_email ?? "" });

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

function MembersTable({ members, scope }: { members: LeaderboardRow[]; scope: BoardScope | null }) {
  const { sorted, sort, toggle } = useSort(members, { key: "qi", dir: "desc" }, cell);
  // A person's numbers are not split by team; someone who left is in them but not viewable.
  const link = (severity: "critical" | "high", m: LeaderboardRow) =>
    scope && m.viewable !== false ? boardLink(severity, { ...scope, team: undefined, user: person(m) }) : null;
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
              <span title="Chỉ hiện khi đủ số review tối thiểu">Quality Index</span>
            </SortTh>
            <th>8 tuần</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => (
            <tr key={m.user.id}>
              <td>
                <PersonLink id={m.user.id} name={m.user.full_name} email={m.user.primary_email} avatar={m.user.avatar_url} />
              </td>
              <td className="num">{m.reviews_count ?? 0}</td>
              <td className="num">
                <Score value={m.avg_score === null ? null : Number(m.avg_score)} />
              </td>
              <td className={`num${m.critical ? " bad" : ""}`}>
                <CountLink n={m.critical ?? 0} href={link("critical", m)} />
              </td>
              <td className="num">
                <CountLink n={m.high ?? 0} href={link("high", m)} />
              </td>
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
  // The dashboard-wide period (lib/timeScope): a week, a month or a range of days (e.g. "Hôm
  // nay", 00:00 to now), the latter computed by the API from the reviews.
  const time = useCycleScope({ range: true, rangeMaxDays: RANGE_MAX_DAYS });
  const period: Period = time?.period ?? "week";
  const start = time && time.period !== "range" ? time.start : null;
  const range = time?.range ?? { from: "", to: "" };
  const today = time?.today ?? "";
  const rangeOk = time !== null && range.from !== "" && range.from <= range.to && range.to <= today;
  const { data: teams } = useApi<Team[]>("/teams/visible");
  const team = teams?.find((t) => String(t.id) === id);
  const keep = { keepPreviousData: true };
  const days = { period, start: range.from, end: range.to };
  const ask = (path: string, cycle: Record<string, string | number>) =>
    period === "range" ? (rangeOk ? `${path}${qs({ ...cycle, ...days })}` : null) : start ? `${path}${qs(cycle)}` : null;
  const { data: series, error } = useApi<MetricsSeries>(ask(`/metrics/teams/${id}`, { period, to: start ?? "" }), keep);
  const { data: board, error: boardError } = useApi<Leaderboard>(ask("/leaderboard", { team_id: id, period, start: start ?? "" }), keep);
  const { data: hot, error: hotError } = useApi<Hotspots>(ask(`/metrics/teams/${id}/hotspots`, { period, start: start ?? "" }), keep);

  const note = scopeNote(time);
  const header = (
    <>
    <PageHeader
      eyebrow="Team"
      title={team?.name ?? "Team"}
      actions={
        <>
          <PeriodPicker time={time} rangeMaxDays={RANGE_MAX_DAYS} />
        </>
      }
    />
    {note && <p className="muted small scope-note">{note}</p>}
    </>
  );

  if (!series)
    return (
      <>
        {header}
        <Loading error={error} rows={4} />
      </>
    );

  // The period of this response: while the next one loads, numbers and links stay together.
  const shown = series.period_range?.[0].slice(0, 10) ?? start;
  // A range is computed now (series.current); a week / month is its stored row. (The API sends
  // `current` as null for weeks and months too, so the chosen period tells them apart.)
  const isRange = period === "range";
  const current = isRange ? (series.current ?? null) : (series.series.find((m) => m.period_start === shown) ?? null);
  const shownLabel = (s: string) =>
    isRange && series.period_range
      ? rangeLabel(s, shiftDays(series.period_range[1].slice(0, 10), -1))
      : periodLabel(period === "month" ? "month" : "week", s);
  const chartPeriod = isRange || period === "week" ? "week" : "month";
  // Team and person numbers count as status=counted does. Linked only where the list can match
  // the number: a stored row (metrics are built after the period ends; the running week has
  // none), and for the team tiles a viewer who sees the members' findings (lead, admin: the
  // leaderboard lists members only for them).
  const scope: BoardScope | null =
    series.period_range && team && current && shown
      ? {
          team: { id: team.id, name: team.name },
          period: { range: series.period_range, label: shownLabel(shown), counted: true },
        }
      : null;
  const teamScope = board?.members ? scope : null;
  const d = series.delta;
  const chart = series.series.map((m) => ({
    period: chartPeriod === "week" ? weekLabel(m.period_start) : m.period_start.slice(0, 7),
    ...Object.fromEntries(SEVERITIES.filter((s) => s !== "info").map((s) => [s, m[s] ?? 0])),
  }));

  return (
    <>
      {header}

      <StatStrip label="Chỉ số của team trong kỳ">
        <StripStat
          label="Quality Index"
          title="50% điểm TB + 20% mật độ lỗi thấp + 15% build + 15% fix rate"
          value={current?.quality_index}
          digits={1}
          change={d.quality_index ?? null}
          tone={scoreTone(current?.quality_index == null ? null : Number(current.quality_index))}
        />
        <StripStat
          label="Critical"
          value={current?.critical ?? 0}
          change={d.critical ?? null}
          higherIsBetter={false}
          tone={current?.critical ? "bad" : undefined}
          href={teamScope ? boardLink("critical", teamScope) : undefined}
        />
        <StripStat
          label="High"
          value={current?.high ?? 0}
          change={d.high ?? null}
          higherIsBetter={false}
          tone={current?.high ? "warn" : undefined}
          href={teamScope ? boardLink("high", teamScope) : undefined}
        />
        <StripStat
          label="Mật độ lỗi"
          title="Finding trên 100 LOC"
          unit="/100 LOC"
          value={current?.defect_density}
          digits={2}
          change={d.defect_density ?? null}
          higherIsBetter={false}
        />
        <StripStat
          label="Build success"
          unit="%"
          value={current?.build_success_rate}
          change={d.build_success_rate ?? null}
        />
        <StripStat label="Thành viên" value={board?.members?.length} />
      </StatStrip>

      <div className="grid two">
        <Card title="Finding theo mức độ" actions={<span className="muted small">{chartPeriod === "week" ? "12 tuần" : "các tháng gần nhất"}</span>}>
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

        <Card title="Điểm nóng" actions={<span className="muted small">trong kỳ</span>}>
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

      <Card title={board?.members ? `Thành viên · ${board.members.length}` : "Thành viên"} flush>
        {board?.members ? (
          <MembersTable members={board.members} scope={scope} />
        ) : board || boardError?.status === 403 ? (
          <EmptyState icon="user" title="Điểm từng người chỉ hiển thị cho lead của team">
            Manager xem số liệu tổng hợp, không xem điểm cá nhân.
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
