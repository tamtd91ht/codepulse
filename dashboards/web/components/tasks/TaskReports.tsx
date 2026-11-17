"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { DateRangePicker, type DayRange } from "@/components/DateRangePicker";
import { Icon, type IconName } from "@/components/icons";
import { Card, ChartTip, Loading, StatStrip, StripStat } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { dayLabel, localDay, shiftDays } from "@/lib/format";
import type { TaskBurndown, TaskReports as Reports, TaskSpace } from "@/lib/types";

import { dayBefore, listHref } from "./filterState";
import { PersonAvatar, PriorityIcon, RESOLUTION_LABEL, RESOLUTIONS, fmtMinutes, personName, useSprints } from "./taskParts";

/*
 * Báo cáo (§13, layout §22.7): a one-row toolbar (date range chip with quick picks, "Hiển thị"),
 * a 64px strip of 6 numbers that open the Danh sách filtered by URL, then the charts on a
 * 2-column grid. A chart with nothing to draw is an 80px card saying what to do next; each chart
 * can be hidden (remembered per browser).
 */

type ChartKey = "burndown" | "velocity" | "flow" | "status" | "worklog" | "resolution" | "priority" | "assignee";
const CHARTS: { key: ChartKey; label: string }[] = [
  { key: "burndown", label: "Burndown" },
  { key: "velocity", label: "Velocity" },
  { key: "flow", label: "Tạo và hoàn thành" },
  { key: "status", label: "Theo trạng thái" },
  { key: "worklog", label: "Giờ làm theo người" },
  { key: "resolution", label: "Đóng theo lý do" },
  { key: "priority", label: "Theo ưu tiên" },
  { key: "assignee", label: "Theo người làm" },
];
const HIDDEN_KEY = "cp:tasks:reports:hidden";

function readHidden(): ChartKey[] {
  try {
    const raw = window.localStorage.getItem(HIDDEN_KEY);
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(list) ? (list.filter((k) => CHARTS.some((c) => c.key === k)) as ChartKey[]) : [];
  } catch {
    return [];
  }
}
function writeHidden(list: ChartKey[]) {
  try {
    window.localStorage.setItem(HIDDEN_KEY, JSON.stringify(list));
  } catch {
    /* per-viewer convenience only */
  }
}

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

  const [hidden, setHidden] = useState<ChartKey[]>([]);
  useEffect(() => setHidden(readHidden()), []);
  const toggle = (key: ChartKey) =>
    setHidden((list) => {
      const next = list.includes(key) ? list.filter((k) => k !== key) : [...list, key];
      writeHidden(next);
      return next;
    });
  const shown = (key: ChartKey) => !hidden.includes(key);

  // Quick picks: 7 / 30 / 90 days, this sprint, the sprint before (§22.7).
  const today = localDay();
  const quickPicks = useMemo(() => {
    const picks: { label: string; range: DayRange }[] = [
      { label: "7 ngày", range: { from: shiftDays(today, -6), to: today } },
      { label: "30 ngày", range: { from: shiftDays(today, -29), to: today } },
      { label: "90 ngày", range: { from: shiftDays(today, -89), to: today } },
    ];
    const active = started.find((s) => s.state === "active" && s.start_date);
    if (active?.start_date) picks.push({ label: "Sprint này", range: { from: active.start_date, to: today } });
    const prev = started.find((s) => s.state === "closed" && s.start_date);
    if (prev?.start_date) {
      const end = prev.end_date ?? prev.closed_at?.slice(0, 10) ?? today;
      picks.push({ label: "Sprint trước", range: { from: prev.start_date, to: end > today ? today : end } });
    }
    return picks;
  }, [started, today]);

  /** The Danh sách tab filtered by GET /tasks parameters (read by the shared FilterBar). */
  // §22.9: links carry the List's own filter (`f`), built from GET /tasks names; sort / order set its first sort.
  const toList = (params: Record<string, string>, sort?: { sort: string; order?: "asc" | "desc" }) => listHref(bp, params, sort);
  const createdSince = { text: "", chips: [{ id: "created", field: "created", op: "after" as const, value: dayBefore(range.from) ?? range.from }] };
  const doneSince = { category: "done", updated_from: range.from };
  const backlogHref = `/tasks?space=${bp}&tab=backlog`;

  return (
    <div className="rp">
      <div className="rp-toolbar">
        <DateRangePicker value={range} max={today} onChange={setRange} showLabel={false} label="Khoảng ngày báo cáo" quickPicks={quickPicks} />
        <span className="rp-spacer" />
        <ViewMenu hidden={hidden} onToggle={toggle} />
      </div>

      {!r ? (
        <div className="rp-kpis-skel">
          <Loading error={data.error} rows={1} />
        </div>
      ) : (
        <div className="rp-kpis">
          <StatStrip label="Chỉ số báo cáo">
            <StripStat
              label="Cycle time trung vị"
              value={r.cycle_time.median_h === null ? null : String(r.cycle_time.median_h)}
              digits={1}
              unit="giờ"
              title="Từ lúc bắt đầu làm đến khi xong, trung vị · bấm để xem task đã xong"
              href={toList({ ...doneSince, resolution: "done" })}
            />
            <StripStat
              label="Cycle time p85"
              value={r.cycle_time.p85_h === null ? null : String(r.cycle_time.p85_h)}
              digits={1}
              unit="giờ"
              title="85% task xong trong khoảng này · bấm để xem task đã xong"
              href={toList({ ...doneSince, resolution: "done" }, { sort: "updated", order: "desc" })}
            />
            <StripStat
              label="Task xong (có bắt đầu)"
              value={r.cycle_time.count}
              title="Task Đã xong từng qua nhóm Đang làm: tính cycle time"
              href={toList({ ...doneSince, resolution: "done" })}
            />
            <StripStat
              label="Xong không qua Đang làm"
              value={r.cycle_time.skipped ?? 0}
              title="Nhảy thẳng vào Xong: không tính vào cycle time (docs/specs/31 §19.1)"
              href={toList(doneSince)}
            />
            <StripStat
              label="Task tạo"
              value={r.flow.reduce((n, d) => n + d.created, 0)}
              title="Task tạo trong khoảng · bấm để xem task mới nhất"
              href={listHref(bp, createdSince, { sort: "created", order: "desc" })}
            />
            <StripStat
              label="Giờ làm"
              value={String(Math.round(r.worklog.reduce((n, w) => n + w.minutes, 0) / 6) / 10)}
              digits={1}
              unit="giờ"
              title="Giờ ghi trong khoảng · bấm để xem task có thay đổi"
              href={toList({ updated_from: range.from }, { sort: "updated", order: "desc" })}
            />
          </StatStrip>
        </div>
      )}

      <div className="rp-grid">
        {shown("burndown") && (
          <Block
            id="tk-burndown"
            title="Burndown"
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
            loading={!sprints.data || (started.length > 0 && !burndown.data)}
            error={sprints.error ?? burndown.error}
            empty={
              !started.length
                ? { icon: "calendar", text: "Chưa có sprint nào bắt đầu", next: <Link href={backlogHref}>Bắt đầu sprint ở Backlog →</Link> }
                : !burndown.data?.days.length
                  ? { icon: "calendar", text: "Sprint chưa có dữ liệu ngày", next: <span>Đặt ngày bắt đầu và kết thúc cho sprint</span> }
                  : null
            }
          >
            <div className="chart-box">
              <ResponsiveContainer>
                <LineChart data={(burndown.data?.days ?? []).map((d) => ({ ...d, label: dayLabel(d.day) }))} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
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
          </Block>
        )}

        {shown("velocity") && (
          <Block
            id="tk-velocity"
            title="Velocity"
            subtitle="Story point cam kết và hoàn thành mỗi sprint"
            loading={!r}
            error={data.error}
            empty={r && !r.velocity.length ? { icon: "trendUp", text: "Chưa có sprint hoàn tất trong khoảng này", next: <Link href={backlogHref}>Đóng sprint ở Backlog →</Link> } : null}
          >
            <div className="chart-box">
              <ResponsiveContainer>
                <BarChart data={r?.velocity ?? []} margin={{ top: 8, right: 0, bottom: 0, left: -16 }}>
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
          </Block>
        )}

        {shown("flow") && (
          <Block
            id="tk-flow"
            title="Tạo và hoàn thành"
            subtitle="Số task tạo mới và xong mỗi ngày"
            loading={!r}
            error={data.error}
            empty={r && !r.flow.length ? { icon: "activity", text: "Không có task tạo hay xong trong khoảng này", next: <span>Chọn khoảng ngày dài hơn</span> } : null}
          >
            <div className="chart-box">
              <ResponsiveContainer>
                <LineChart data={(r?.flow ?? []).map((d) => ({ ...d, label: dayLabel(d.day) }))} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
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
          </Block>
        )}

        {shown("status") && (
          <Block id="tk-by-status" title="Theo trạng thái" loading={!r} error={data.error} empty={r && !r.by_status.length ? { icon: "inbox", text: "Chưa có task", next: <span>Tạo task đầu tiên bằng nút Tạo task</span> } : null}>
            <ul className="tk-bars">
              {(r?.by_status ?? []).map((s, i, all) => {
                const max = Math.max(...all.map((x) => x.count), 1);
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
          </Block>
        )}

        {shown("worklog") && (
          <Block
            id="tk-worklog"
            title="Giờ làm theo người"
            loading={!r}
            error={data.error}
            empty={r && !r.worklog.length ? { icon: "calendar", text: "Chưa ai ghi giờ trong khoảng này", next: <span>Ghi giờ ở chi tiết task</span> } : null}
          >
            <div className="table-wrap">
              <table className="rp-table">
                <thead>
                  <tr>
                    <th>Người</th>
                    <th className="num">Giờ làm</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(r?.worklog ?? [])]
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
          </Block>
        )}

        {shown("resolution") && r && r.by_resolution && (
          <Block
            id="tk-by-resolution"
            title="Đóng theo lý do"
            subtitle="Task đóng trong khoảng đã chọn. Chỉ “Đã xong” tính vào velocity và cycle time."
            empty={RESOLUTIONS.every((k) => !r.by_resolution?.[k]) ? { icon: "checkCircle", text: "Chưa đóng task nào trong khoảng này", next: <span>Chọn khoảng ngày dài hơn</span> } : null}
          >
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
          </Block>
        )}

        {shown("priority") && (
          <Block
            id="tk-by-priority"
            title="Theo ưu tiên"
            subtitle="Task đang mở theo mức ưu tiên; tạo / xong trong khoảng đã chọn"
            flush
            wide
            loading={!r}
            error={data.error}
            empty={r && !r.by_priority?.length ? { icon: "flag", text: "Dự án chưa có mức ưu tiên", next: <Link href={`/tasks?space=${bp}&tab=settings`}>Đặt ở Cài đặt →</Link> } : null}
          >
            <div className="table-wrap">
              <table className="rp-table">
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
                    const rows = r?.by_priority ?? [];
                    const max = Math.max(...rows.map((x) => x.open), 1);
                    return rows.map((x) => (
                      <tr key={x.priority.key}>
                        <td>
                          <Link href={toList({ priority: x.priority.key })} className="rp-cell-link">
                            <PriorityIcon name={x.priority.name} level={x.priority.level} color={x.priority.color} withLabel />
                          </Link>
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
          </Block>
        )}

        {shown("assignee") && (
          <Block
            id="tk-by-assignee"
            title="Theo người làm"
            flush
            wide
            loading={!r}
            error={data.error}
            empty={r && !r.by_assignee.length ? { icon: "user", text: "Chưa có task được giao", next: <span>Giao người làm ở Board hoặc Danh sách</span> } : null}
          >
            <div className="table-wrap">
              <table className="rp-table">
                <thead>
                  <tr>
                    <th>Người làm</th>
                    <th className="num">Đang mở</th>
                    <th className="num">Đã xong</th>
                    <th className="num">Story point xong</th>
                  </tr>
                </thead>
                <tbody>
                  {(r?.by_assignee ?? []).map((a, i) => (
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
          </Block>
        )}

        {hidden.length === CHARTS.length && (
          <p className="rp-none muted">
            Đang ẩn mọi biểu đồ · mở <strong>Hiển thị</strong> để bật lại.
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * One chart of the grid: a Card while loading, failed or with data; an 80px card with a
 * next step when there is nothing to draw (§5: an empty state inside a block is at most 80px).
 */
function Block({
  id,
  title,
  subtitle,
  actions,
  flush,
  wide,
  loading,
  error,
  empty,
  children,
}: {
  id: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  flush?: boolean;
  wide?: boolean;
  loading?: boolean;
  error?: Error;
  empty?: { icon: IconName; text: string; next?: ReactNode } | null;
  children: ReactNode;
}) {
  if (!error && !loading && empty)
    return (
      <section className={`rp-empty${wide ? " rp-wide" : ""}`} aria-label={title}>
        <span className="rp-empty-icon" aria-hidden="true">
          <Icon name={empty.icon} size={16} />
        </span>
        <span className="rp-empty-body">
          <strong>{title}</strong>
          <span className="small muted">
            {empty.text}
            {empty.next && <> · {empty.next}</>}
          </span>
        </span>
        {actions && <span className="rp-empty-actions">{actions}</span>}
      </section>
    );
  return (
    <Card title={title} id={id} subtitle={subtitle} actions={actions} flush={flush} className={`rp-card${wide ? " rp-wide" : ""}`}>
      {error || loading ? <Loading error={error} rows={4} /> : children}
    </Card>
  );
}

/** "Hiển thị": which charts show; remembered per browser (§3.5). */
function ViewMenu({ hidden, onToggle }: { hidden: ChartKey[]; onToggle: (key: ChartKey) => void }) {
  const box = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent | KeyboardEvent) => {
      const el = box.current;
      if (!el?.open) return;
      if (e instanceof KeyboardEvent) {
        if (e.key === "Escape") {
          el.open = false;
          el.querySelector("summary")?.focus();
        }
      } else if (!el.contains(e.target as Node)) el.open = false;
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", away);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", away);
    };
  }, []);
  const count = CHARTS.length - hidden.length;
  return (
    <details className="rp-view" ref={box}>
      <summary className="button sm ghost" aria-label={`Hiển thị: ${count}/${CHARTS.length} biểu đồ`}>
        <Icon name="board" size={14} /> Hiển thị
        {hidden.length > 0 && <span className="rp-view-count">{count}/{CHARTS.length}</span>}
      </summary>
      <div className="rp-view-pop" role="group" aria-label="Biểu đồ hiển thị">
        <span className="rp-view-title small muted">Biểu đồ hiển thị</span>
        {CHARTS.map((c) => (
          <label key={c.key} className="inline-check">
            <input type="checkbox" checked={!hidden.includes(c.key)} onChange={() => onToggle(c.key)} />
            {c.label}
          </label>
        ))}
      </div>
    </details>
  );
}
