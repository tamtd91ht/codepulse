"use client";

import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { DateRangePicker, type DayRange } from "@/components/DateRangePicker";
import { Card, ChartTip, EmptyState, Loading, StatStrip, StripStat } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { dayLabel, localDay, shiftDays } from "@/lib/format";
import type { TaskBurndown, TaskReports as Reports, TaskSpace } from "@/lib/types";

import { PersonAvatar, PriorityIcon, RESOLUTION_LABEL, RESOLUTIONS, fmtMinutes, personName, useSprints } from "./taskParts";

/** Báo cáo (§13): burndown of a sprint, velocity, created vs resolved, cycle time, by status / person, worklog. */
export function TaskReports({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const [range, setRange] = useState<DayRange>(() => ({ from: shiftDays(localDay(), -89), to: localDay() }));
  const data = useApi<Reports>(`/tasks/spaces/${bp}/reports${qs({ from: range.from, to: range.to })}`, { keepPreviousData: true });
  const sprints = useSprints(bp);
  const started = useMemo(
    () => (sprints.data ?? []).filter((s) => s.state !== "planned").sort((a, b) => (b.start_date ?? "").localeCompare(a.start_date ?? "") || b.id - a.id),
    [sprints.data],
  );
  const [sprintId, setSprintId] = useState<number | null>(null);
  useEffect(() => {
    if (sprintId === null && started.length) setSprintId((started.find((s) => s.state === "active") ?? started[0]).id);
  }, [started, sprintId]);
  const burndown = useApi<TaskBurndown>(sprintId ? `/tasks/sprints/${sprintId}/burndown` : null);
  const r = data.data;

  return (
    <div className="tk-reports">
      <div className="rq-grid tk-filters">
        <div className="rq-field">
          <span className="rq-label">Khoảng ngày</span>
          <DateRangePicker value={range} max={localDay()} onChange={setRange} showLabel={false} label="Khoảng ngày báo cáo" />
        </div>
      </div>

      {!r ? (
        <Loading error={data.error} rows={4} />
      ) : (
        <StatStrip label="Cycle time">
          <StripStat label="Cycle time trung vị" value={r.cycle_time.median_h === null ? null : String(r.cycle_time.median_h)} digits={1} unit="giờ" title="Từ lúc bắt đầu làm đến khi xong, trung vị" />
          <StripStat label="Cycle time p85" value={r.cycle_time.p85_h === null ? null : String(r.cycle_time.p85_h)} digits={1} unit="giờ" title="85% task xong trong khoảng này" />
          <StripStat label="Task xong (có bắt đầu)" value={r.cycle_time.count} title="Task Đã xong từng qua nhóm Đang làm: tính cycle time" />
          <StripStat label="Xong không qua Đang làm" value={r.cycle_time.skipped ?? 0} title="Nhảy thẳng vào Xong: không tính vào cycle time (docs/specs/31 §19.1)" />
          <StripStat label="Task tạo" value={r.flow.reduce((n, d) => n + d.created, 0)} />
          <StripStat label="Giờ làm" value={String(Math.round(r.worklog.reduce((n, w) => n + w.minutes, 0) / 6) / 10)} digits={1} unit="giờ" />
        </StatStrip>
      )}

      <div className="grid two">
        <Card
          title="Burndown"
          id="tk-burndown"
          actions={
            started.length > 0 && (
              <select className="compact" value={sprintId ?? ""} onChange={(e) => setSprintId(Number(e.target.value))} aria-label="Sprint">
                {started.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.state === "active" ? " (đang chạy)" : ""}
                  </option>
                ))}
              </select>
            )
          }
        >
          {!sprints.data ? (
            <Loading error={sprints.error} rows={4} />
          ) : !started.length ? (
            <EmptyState icon="calendar" title="Chưa có sprint nào bắt đầu" />
          ) : !burndown.data ? (
            <Loading error={burndown.error} rows={4} />
          ) : !burndown.data.days.length ? (
            <EmptyState icon="calendar" title="Sprint chưa có dữ liệu ngày" />
          ) : (
            <div className="chart-box">
              <ResponsiveContainer>
                <LineChart data={burndown.data.days.map((d) => ({ ...d, label: dayLabel(d.day) }))} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip content={<ChartTip />} />
                  <Legend iconType="circle" iconSize={8} />
                  <Line dataKey="ideal_points" name="Lý tưởng" stroke="var(--faint)" strokeDasharray="4 4" strokeWidth={2} dot={false} />
                  <Line dataKey="remaining_points" name="Còn lại (SP)" stroke="var(--chart-1)" strokeWidth={2.5} dot={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
        <Card title="Velocity" id="tk-velocity" subtitle="Story point cam kết và hoàn thành mỗi sprint">
          {!r ? (
            <Loading error={data.error} rows={4} />
          ) : !r.velocity.length ? (
            <EmptyState icon="trendUp" title="Chưa có sprint hoàn tất trong khoảng này" />
          ) : (
            <div className="chart-box">
              <ResponsiveContainer>
                <BarChart data={r.velocity} margin={{ top: 8, right: 0, bottom: 0, left: -16 }}>
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="name" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
                  <Legend iconType="circle" iconSize={8} />
                  <Bar dataKey="committed" name="Cam kết" fill="var(--chart-4)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                  <Bar dataKey="completed" name="Hoàn thành" fill="var(--chart-2)" radius={[4, 4, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <Card title="Tạo và hoàn thành" id="tk-flow" subtitle="Số task tạo mới và xong mỗi ngày">
        {!r ? (
          <Loading error={data.error} rows={4} />
        ) : !r.flow.length ? (
          <EmptyState icon="activity" title="Không có task tạo hay xong trong khoảng này" />
        ) : (
          <div className="chart-box">
            <ResponsiveContainer>
              <LineChart data={r.flow.map((d) => ({ ...d, label: dayLabel(d.day) }))} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={12} minTickGap={16} />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                <Tooltip content={<ChartTip digits={0} />} />
                <Legend iconType="circle" iconSize={8} />
                <Line dataKey="created" name="Tạo" stroke="var(--chart-3)" strokeWidth={2} dot={false} />
                <Line dataKey="resolved" name="Xong" stroke="var(--chart-2)" strokeWidth={2.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <div className="grid two">
        <Card title="Theo trạng thái" id="tk-by-status">
          {!r ? (
            <Loading error={data.error} rows={4} />
          ) : !r.by_status.length ? (
            <EmptyState title="Chưa có task" />
          ) : (
            <ul className="tk-bars">
              {r.by_status.map((s, i) => {
                const max = Math.max(...r.by_status.map((x) => x.count), 1);
                const name = typeof s.status === "string" ? s.status : s.status.name;
                return (
                  <li key={i}>
                    <span className="tk-bar-label">{name}</span>
                    <span className="tk-bar-track">
                      <span className="tk-bar-fill" style={{ width: `${(s.count / max) * 100}%` }} />
                    </span>
                    <span className="num">{s.count}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <Card title="Giờ làm theo người" id="tk-worklog">
          {!r ? (
            <Loading error={data.error} rows={4} />
          ) : !r.worklog.length ? (
            <EmptyState icon="calendar" title="Chưa ai ghi giờ trong khoảng này" />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Người</th>
                    <th className="num">Giờ làm</th>
                  </tr>
                </thead>
                <tbody>
                  {[...r.worklog]
                    .sort((a, b) => b.minutes - a.minutes)
                    .map((w, i) => (
                      <tr key={w.user?.id ?? `n${i}`}>
                        <td>
                          <span className="tk-person">
                            <PersonAvatar person={w.user} />
                            {personName(w.user)}
                          </span>
                        </td>
                        <td className="num">{fmtMinutes(w.minutes)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {r && r.by_resolution && (
        <Card title="Đóng theo lý do" id="tk-by-resolution" subtitle="Task đóng trong khoảng đã chọn. Chỉ “Đã xong” tính vào velocity và cycle time.">
          <ul className="tk-bars">
            {RESOLUTIONS.map((k) => {
              const n = r.by_resolution?.[k] ?? 0;
              const max = Math.max(...RESOLUTIONS.map((x) => r.by_resolution?.[x] ?? 0), 1);
              return (
                <li key={k}>
                  <span className="tk-bar-label">{RESOLUTION_LABEL[k]}</span>
                  <span className="tk-bar-track">
                    <span className="tk-bar-fill" style={{ width: `${(n / max) * 100}%` }} />
                  </span>
                  <span className="num">{n}</span>
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <Card title="Theo ưu tiên" id="tk-by-priority" subtitle="Task đang mở theo mức ưu tiên của dự án; tạo / xong trong khoảng đã chọn" flush>
        {!r ? (
          <Loading error={data.error} rows={4} />
        ) : !r.by_priority?.length ? (
          <EmptyState title="Dự án chưa có mức ưu tiên" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Ưu tiên</th>
                  <th className="tk-prio-bar-col" aria-label="Tỉ lệ đang mở" />
                  <th className="num">Đang mở</th>
                  <th className="num">Quá hạn</th>
                  <th className="num">Point đang mở</th>
                  <th className="num">Tạo mới</th>
                  <th className="num">Đã xong</th>
                </tr>
              </thead>
              <tbody>
                {(() => {
                  const rows = r.by_priority ?? [];
                  const max = Math.max(...rows.map((x) => x.open), 1);
                  return rows.map((x) => (
                    <tr key={x.priority.key}>
                      <td>
                        <PriorityIcon name={x.priority.name} level={x.priority.level} color={x.priority.color} withLabel />
                      </td>
                      <td className="tk-prio-bar-col">
                        <span className={`tk-bar-track tk-prio-c-${x.priority.color}`}>
                          <span className="tk-bar-fill tk-prio-fill" style={{ width: `${(x.open / max) * 100}%` }} />
                        </span>
                      </td>
                      <td className="num">{x.open}</td>
                      <td className={`num${x.overdue ? " tk-overdue" : ""}`}>{x.overdue}</td>
                      <td className="num">{x.open_points}</td>
                      <td className="num">{x.created}</td>
                      <td className="num">{x.resolved}</td>
                    </tr>
                  ));
                })()}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Theo người làm" id="tk-by-assignee" flush>
        {!r ? (
          <Loading error={data.error} rows={4} />
        ) : !r.by_assignee.length ? (
          <EmptyState title="Chưa có task được giao" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Người làm</th>
                  <th className="num">Đang mở</th>
                  <th className="num">Đã xong</th>
                  <th className="num">Story point xong</th>
                </tr>
              </thead>
              <tbody>
                {r.by_assignee.map((a, i) => (
                  <tr key={a.user?.id ?? `n${i}`}>
                    <td>
                      <span className="tk-person">
                        <PersonAvatar person={a.user} />
                        {personName(a.user)}
                      </span>
                    </td>
                    <td className="num">{a.open}</td>
                    <td className="num">{a.done}</td>
                    <td className="num">{a.points}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
