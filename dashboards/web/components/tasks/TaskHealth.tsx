"use client";

import Link from "next/link";
import { useMemo } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, ChartTip, EmptyState, Loading, StatStrip, StripStat } from "@/components/ui";
import { useApi } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import type { TaskPerson, TaskSpace } from "@/lib/types";

import { PersonAvatar } from "./taskParts";

/*
 * Sức khoẻ sprint (docs/specs/31 §19.5, roadmap P4): the active sprint's forecast, what is stuck,
 * the load per person (by name, not a ranking) and quality signals. Read-only.
 */

export type HealthColor = "red" | "amber" | "green";

type Brief = { key: string; title: string; assignee_id: number | null };

export interface SprintHealth {
  sprint: { id: number; name: string; goal: string | null; start_date: string | null; end_date: string | null } | null;
  forecast?: {
    days: { day: string; ideal_points: number; remaining_points: number | null; remaining_tasks: number | null }[];
    committed_points: number;
    scope_points: number;
    done_points: number;
    done_pct: number | null;
    ideal_remaining: number | null;
    elapsed_days: number;
    total_days: number;
    projected_done_points: number | null;
    projected_pct: number | null;
  };
  stuck?: {
    aged: (Brief & { assignee: TaskPerson | null; status: string; hours: number; limit_hours: number })[];
    mrs_waiting: { task: Brief; project_id: number; iid: number; title: string | null; hours: number }[];
    blocked: (Brief & { blocked_by: string[] })[];
    count: number;
    doing_warning_hours: number;
    mr_review_hours: number;
  };
  load?: { user: TaskPerson | null; name: string; open: number; open_points: number }[];
  quality?: { open_critical: number; bugs_created: number; reopened: number; highest_bugs: number };
  health?: HealthColor;
}

export const HEALTH_LABEL: Record<HealthColor, string> = { red: "Rủi ro", amber: "Cần chú ý", green: "Ổn" };

export function HealthBadge({ value }: { value: HealthColor }) {
  return (
    <span className={`th-badge th-${value}`}>
      <span className="th-dot" aria-hidden="true" />
      {HEALTH_LABEL[value]}
    </span>
  );
}

function age(hours: number): string {
  return hours >= 48 ? `${Math.round(hours / 24)} ngày` : `${Math.round(hours)} giờ`;
}

function TaskRef({ k, title }: { k: string; title: string }) {
  return (
    <Link href={`/tasks/${encodeURIComponent(k)}`} className="th-task">
      <span className="mono">{k}</span> <span className="th-task-title">{title}</span>
    </Link>
  );
}

export function TaskHealth({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const res = useApi<SprintHealth>(`/tasks/spaces/${bp}/health`);
  const h = res.data;
  const retry = () => void res.mutate();

  const errorBox = res.error ? (
    <div className="th-error">
      <Loading error={res.error} />
      <button type="button" className="ghost sm" onClick={retry}>
        Thử lại
      </button>
    </div>
  ) : null;

  const chart = useMemo(() => {
    const fc = h?.forecast;
    if (!fc) return [];
    const pace = fc.elapsed_days >= 2 ? fc.done_points / fc.elapsed_days : null;
    return fc.days.map((d, i) => ({
      ...d,
      label: dayLabel(d.day),
      projected: pace !== null && i >= fc.elapsed_days ? Math.max(Math.round((fc.scope_points - pace * i) * 10) / 10, 0) : null,
    }));
  }, [h]);

  if (h && !h.sprint) {
    return (
      <div className="th-health">
        <EmptyState icon="calendar" title="Dự án chưa có sprint đang chạy">
          Bắt đầu một sprint ở tab Backlog để xem dự báo, việc kẹt, tải và chất lượng.
        </EmptyState>
        <p className="th-foot">
          <Link href="/tasks/overview">Xem tổng quan mọi dự án</Link>
        </p>
      </div>
    );
  }

  const fc = h?.forecast;
  const st = h?.stuck;
  const load = h?.load ?? [];
  const qa = h?.quality;
  const maxPoints = Math.max(...load.map((l) => l.open_points), 1);
  const maxOpen = Math.max(...load.map((l) => l.open), 1);

  return (
    <div className="th-health">
      {h?.sprint && (
        <div className="th-head">
          <div>
            <strong>{h.sprint.name}</strong>
            {h.sprint.start_date && h.sprint.end_date && (
              <span className="th-muted">
                {" "}
                · {dayLabel(h.sprint.start_date)} – {dayLabel(h.sprint.end_date)}
              </span>
            )}
          </div>
          <div className="th-head-right">
            {h.health && <HealthBadge value={h.health} />}
            <Link href="/tasks/overview" className="button sm">
              Tổng quan mọi dự án
            </Link>
          </div>
        </div>
      )}

      <Card title="Dự báo" id="th-forecast" subtitle="Burndown và đường dự phóng theo tốc độ đã làm">
        {errorBox ?? (!fc ? (
          <Loading rows={4} />
        ) : (
          <>
            <StatStrip label="Dự báo sprint" inline>
              <StripStat label="Point cam kết" value={fc.committed_points} digits={1} />
              <StripStat label="Point xong" value={fc.done_points} digits={1} sub={fc.done_pct === null ? undefined : `${fc.done_pct}% phạm vi`} />
              <StripStat label="Lý tưởng còn lại" value={fc.ideal_remaining ?? undefined} digits={1} title="Point còn lại hôm nay theo đường lý tưởng" />
              <StripStat
                label="Dự phóng hoàn thành"
                value={fc.projected_pct ?? undefined}
                digits={1}
                unit="%"
                tone={fc.projected_pct === null ? undefined : fc.projected_pct < 70 ? "bad" : fc.projected_pct < 90 ? "warn" : "good"}
                title="Theo tốc độ trung bình từ đầu sprint; cần ít nhất 2 ngày"
                sub={fc.projected_done_points === null ? "Cần ≥ 2 ngày dữ liệu" : `${fc.projected_done_points}/${fc.scope_points} point`}
              />
            </StatStrip>
            {!fc.days.length ? (
              <EmptyState icon="calendar" title="Sprint chưa có ngày bắt đầu / kết thúc" />
            ) : (
              <div className="chart-box th-chart" role="img" aria-label={`Burndown ${h?.sprint?.name ?? ""}: đường lý tưởng, còn lại và dự phóng`}>
                <ResponsiveContainer>
                  <LineChart data={chart} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={12} minTickGap={12} />
                    <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />
                    <Tooltip content={<ChartTip />} />
                    <Legend iconType="circle" iconSize={8} />
                    <Line dataKey="ideal_points" name="Lý tưởng" stroke="var(--faint)" strokeDasharray="4 4" strokeWidth={2} dot={false} />
                    <Line dataKey="remaining_points" name="Còn lại (SP)" stroke="var(--chart-1)" strokeWidth={2.5} dot={false} connectNulls />
                    <Line dataKey="projected" name="Dự phóng" stroke="var(--chart-3)" strokeDasharray="2 3" strokeWidth={2} dot={false} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </>
        ))}
      </Card>

      <div className="grid two">
        <Card title="Kẹt" id="th-stuck" subtitle={st ? `Quá ${st.doing_warning_hours} giờ trong cột Đang làm (hoặc ngưỡng của trạng thái), MR chờ review quá ${st.mr_review_hours} giờ, task bị chặn` : undefined}>
          {errorBox ?? (!st ? (
            <Loading rows={4} />
          ) : !st.count ? (
            <EmptyState icon="checkCircle" tone="good" title="Không có việc nào đang kẹt" />
          ) : (
            <div className="th-stuck">
              {st.aged.length > 0 && (
                <section aria-label="Lâu trong một trạng thái">
                  <h4 className="th-sub">Lâu trong một trạng thái ({st.aged.length})</h4>
                  <ul className="th-list">
                    {st.aged.map((a) => (
                      <li key={a.key}>
                        <TaskRef k={a.key} title={a.title} />
                        <span className="th-meta">
                          {a.status} · {age(a.hours)} (ngưỡng {a.limit_hours} giờ)
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {st.mrs_waiting.length > 0 && (
                <section aria-label="MR chờ review">
                  <h4 className="th-sub">MR chờ review ({st.mrs_waiting.length})</h4>
                  <ul className="th-list">
                    {st.mrs_waiting.map((m) => (
                      <li key={`${m.project_id}!${m.iid}`}>
                        <TaskRef k={m.task.key} title={m.title ?? m.task.title} />
                        <span className="th-meta">
                          !{m.iid} · mở {age(m.hours)}, chưa ai review
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {st.blocked.length > 0 && (
                <section aria-label="Bị chặn">
                  <h4 className="th-sub">Bị chặn ({st.blocked.length})</h4>
                  <ul className="th-list">
                    {st.blocked.map((b) => (
                      <li key={b.key}>
                        <TaskRef k={b.key} title={b.title} />
                        <span className="th-meta">chặn bởi {b.blocked_by.join(", ")}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          ))}
        </Card>

        <Card title="Tải" id="th-load" subtitle="Point (thanh trên) và task (thanh dưới) đang mở của mỗi người trong sprint, xếp theo tên">
          {errorBox ?? (!h ? (
            <Loading rows={4} />
          ) : !load.length ? (
            <EmptyState icon="checkCircle" tone="good" title="Không còn task mở trong sprint" />
          ) : (
            <ul className="th-load">
              {load.map((l) => (
                <li key={l.user?.id ?? "none"}>
                  <span className="th-load-name">
                    <PersonAvatar person={l.user} />
                    <span>{l.name}</span>
                  </span>
                  <span className="th-load-bars">
                    <span className="th-bar" aria-hidden="true">
                      <span className="th-bar-fill th-bar-points" style={{ width: `${(l.open_points / maxPoints) * 100}%` }} />
                    </span>
                    <span className="th-bar" aria-hidden="true">
                      <span className="th-bar-fill th-bar-tasks" style={{ width: `${(l.open / maxOpen) * 100}%` }} />
                    </span>
                  </span>
                  <span className="th-load-num">
                    {l.open_points} point · {l.open} task
                  </span>
                </li>
              ))}
            </ul>
          ))}
        </Card>
      </div>

      <Card title="Chất lượng" id="th-quality" subtitle="Tín hiệu từ review code và bug trong thời gian sprint">
        {errorBox ?? (!qa ? (
          <Loading rows={2} />
        ) : (
          <StatStrip label="Chất lượng sprint" inline>
            <StripStat label="Lỗi critical đang mở" value={qa.open_critical} tone={qa.open_critical ? "bad" : "good"} title="Finding critical còn mở trên review mới nhất của commit / MR nối với task trong sprint" />
            <StripStat label="Bug cao nhất đang mở" value={qa.highest_bugs} tone={qa.highest_bugs ? "bad" : "good"} title="Bug trong sprint có ưu tiên mức cao nhất, chưa xong" />
            <StripStat label="Bug tạo trong sprint" value={qa.bugs_created} title="Task loại Bug của dự án tạo trong khoảng ngày của sprint" />
            <StripStat label="Task mở lại" value={qa.reopened} tone={qa.reopened ? "warn" : undefined} title="Task trong sprint rời trạng thái Xong về trạng thái mở trong thời gian sprint" />
          </StatStrip>
        ))}
      </Card>
    </div>
  );
}
