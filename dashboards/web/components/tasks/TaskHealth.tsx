"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Alert, Card, ChartTip, EmptyState, Expander, Loading, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import type { ForecastRange, LatestSprintReview, SprintReview, SprintScopeChange, SprintTaskGroup, TaskPerson, TaskSpace } from "@/lib/types";

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
    /** §20.6: a band from the daily pace / velocity spread; null with too little data. */
    range?: ForecastRange | null;
    few_data?: boolean;
  };
  scope_change?: SprintScopeChange;
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

  if (h && !h.sprint) return <NoActiveSprint space={space} />;

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
                title="Theo tốc độ trung bình từ đầu sprint; cần ít nhất 2 ngày. Khoảng: độ dao động của tốc độ từng ngày (từ 4 ngày) và velocity các sprint đã đóng (từ 3 sprint)"
                sub={<ForecastSub fc={fc} />}
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

      <Card title="Thay đổi phạm vi" id="th-scope" subtitle="Task thêm vào hoặc bỏ ra sau khi sprint bắt đầu">
        {errorBox ?? (!h ? (
          <Loading rows={2} />
        ) : !h.scope_change || (!h.scope_change.added_after_start.count && !h.scope_change.removed_after_start.count) ? (
          <EmptyState icon="checkCircle" tone="good" title="Phạm vi giữ nguyên từ lúc bắt đầu" />
        ) : (
          <div className="sr-scope">
            <ScopeList title="Thêm sau khi bắt đầu" sign="+" group={h.scope_change.added_after_start} />
            <ScopeList title="Bỏ ra sau khi bắt đầu" sign="−" group={h.scope_change.removed_after_start} />
          </div>
        ))}
      </Card>
    </div>
  );
}

/* -- §20.6: forecast range, scope change, sprint review, empty state ------------------------------ */

function ForecastSub({ fc }: { fc: NonNullable<SprintHealth["forecast"]> }) {
  if (fc.range) {
    const r = fc.range;
    const basis = r.basis.map((b) => (b === "daily" ? "tốc độ từng ngày" : "velocity sprint trước")).join(" + ");
    return (
      <span title={`${r.low_points}–${r.high_points}/${fc.scope_points} point, theo ${basis}`}>
        Dự kiến xong {Math.round(r.low_pct)}–{Math.round(r.high_pct)}% SP
      </span>
    );
  }
  if (fc.projected_done_points === null) return <>Cần ≥ 2 ngày dữ liệu</>;
  return (
    <>
      {fc.projected_done_points}/{fc.scope_points} point · <span className="sr-few">ít dữ liệu</span>
    </>
  );
}

function ScopeList({ title, sign, group }: { title: string; sign: string; group: SprintTaskGroup }) {
  if (!group.count) return null;
  return (
    <section aria-label={title}>
      <h4 className="th-sub">
        {title}: {sign}
        {group.count} task · {group.points ?? 0} SP
      </h4>
      <ul className="th-list">
        {group.tasks.map((t) => (
          <li key={t.key}>
            <TaskRef k={t.key} title={t.title} />
            <span className="th-meta">{t.points} SP</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** A duration in hours as a stat: days from 48 hours on. */
function cycleStat(h: number | null): { value: number | undefined; unit: string; digits: number } {
  if (h === null) return { value: undefined, unit: "", digits: 1 };
  return h >= 48 ? { value: Math.round(h / 2.4) / 10, unit: " ngày", digits: 1 } : { value: h, unit: " giờ", digits: 1 };
}

/** The sprint review (§20.6): done, carried over, added mid-sprint, bugs, findings, cycle time. */
export function SprintReviewView({ review }: { review: SprintReview }) {
  const r = review;
  const carried = r.carried_over;
  return (
    <div className="sr-review">
      <StatStrip label={`Đánh giá ${r.sprint.name}`} inline>
        <StripStat
          label="Đã xong"
          value={r.done.points ?? 0}
          digits={1}
          unit=" SP"
          sub={`${r.done.count ?? 0} task${r.committed_points !== null ? ` · cam kết ${r.committed_points} SP` : ""}`}
          title="Task lý do Xong (không tính Lỗi thời / Không làm)"
        />
        <StripStat
          label="Chuyển sang sau"
          value={carried.count ?? undefined}
          sub={carried.points !== null ? `${carried.points} SP` : carried.count === null ? "không rõ" : "đã chuyển khi đóng"}
          tone={carried.count ? "warn" : undefined}
          title="Task chưa xong của sprint"
        />
        <StripStat
          label="Thêm sau khi bắt đầu"
          value={r.added_after_start.count ?? 0}
          sub={`${r.added_after_start.points ?? 0} SP`}
          tone={r.added_after_start.count ? "warn" : undefined}
        />
        <StripStat label="Bug tạo trong sprint" value={r.bugs_created.count} sub={`mức cao nhất: ${r.bugs_created.highest}`} tone={r.bugs_created.highest ? "bad" : undefined} />
        <StripStat
          label="Finding mới"
          value={r.findings.critical}
          sub={`critical · ${r.findings.high} high`}
          tone={r.findings.critical ? "bad" : undefined}
          title="Finding critical / high mở ra trên code nối với task của sprint, trong thời gian sprint"
        />
        <StripStat label="Cycle time trung vị" {...cycleStat(r.cycle_time_h)} title="Từ lúc bắt đầu làm tới lúc xong, các task đã xong của sprint" />
      </StatStrip>
      {(carried.tasks.length > 0 || (r.added_after_start.count ?? 0) > 0) && (
        <Expander summary="Xem danh sách task">
          <div className="sr-scope">
            <ScopeList title="Chuyển sang sau" sign="" group={carried} />
            <ScopeList title="Thêm sau khi bắt đầu" sign="+" group={r.added_after_start} />
          </div>
        </Expander>
      )}
    </div>
  );
}

/** "Tạo trang retro": the review as a document of Tài liệu dự án, through POST /docs (docs.edit). */
export function RetroButton({ review }: { review: SprintReview }) {
  const [busy, setBusy] = useState(false);
  const [doc, setDoc] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!review.can_create_retro) return null;
  const bp = review.sprint.business_project_id;
  async function go() {
    setBusy(true);
    setError(null);
    try {
      const made = await api.post<{ id: number }>("/docs", {
        business_project_id: bp,
        title: `Retro ${review.sprint.name}`.slice(0, 200),
        category: "other",
        summary: `Đánh giá sprint ${review.sprint.name}`.slice(0, 300),
        body: review.markdown,
        note: "Tạo từ đánh giá sprint",
      });
      setDoc(made.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  if (doc !== null)
    return (
      <Link href={`/docs?project=${bp}&doc=${doc}`} className="button sm">
        Mở trang retro
      </Link>
    );
  return (
    <span className="sr-retro">
      <button type="button" className="sm" onClick={() => void go()} disabled={busy}>
        {busy ? "Đang tạo…" : "Tạo trang retro"}
      </button>
      {error && <Alert tone="error">{error}</Alert>}
    </span>
  );
}

/** The Backlog sprint header's note (§20.6): "+3 task sau khi bắt đầu"; nothing while unchanged. */
export function ScopeNote({ sprintId }: { sprintId: number }) {
  const res = useApi<SprintScopeChange>(`/tasks/sprints/${sprintId}/scope-change`);
  const added = res.data?.added_after_start;
  const removed = res.data?.removed_after_start;
  if (!added?.count && !removed?.count) return null;
  const parts = [added?.count ? `+${added.count} task` : null, removed?.count ? `−${removed.count} task` : null].filter(Boolean).join(", ");
  const tip = [
    added?.count ? `Thêm: ${added.keys.join(", ")} (${added.points ?? 0} SP)` : null,
    removed?.count ? `Bỏ ra: ${removed.keys.join(", ")} (${removed.points ?? 0} SP)` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="small sr-scope-note" title={tip}>
      · {parts} sau khi bắt đầu
    </span>
  );
}

/** No sprint running: the last closed sprint's review, or the Kanban flow of 14 days. */
function NoActiveSprint({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const res = useApi<LatestSprintReview>(`/tasks/spaces/${bp}/review/latest`);
  const kanban = space.board_mode === "kanban";
  const startLink = kanban ? null : (
    <Link href={`/tasks?space=${bp}&tab=backlog`} className="button sm">
      Bắt đầu sprint
    </Link>
  );
  const data = res.data;
  return (
    <div className="th-health">
      <div className="th-head">
        <div>
          <strong>{kanban ? "Dòng chảy 14 ngày qua" : "Chưa có sprint đang chạy"}</strong>
          {data?.review && <span className="th-muted"> · sprint gần nhất: {data.review.sprint.name}</span>}
        </div>
        <div className="th-head-right">
          {startLink}
          <Link href="/tasks/overview" className="button sm">
            Tổng quan mọi dự án
          </Link>
        </div>
      </div>
      <Card
        title={data?.flow ? "Kanban: 14 ngày qua" : "Sprint gần nhất"}
        id="sr-latest"
        subtitle={data?.flow ? "Số task xong, cycle time và task mới, không theo sprint" : "Đánh giá của sprint đã đóng gần nhất"}
        actions={data?.review ? <RetroButton review={data.review} /> : undefined}
      >
        {res.error ? (
          <div className="th-error">
            <Loading error={res.error} />
            <button type="button" className="ghost sm" onClick={() => void res.mutate()}>
              Thử lại
            </button>
          </div>
        ) : !data ? (
          <Loading rows={3} />
        ) : data.flow ? (
          <StatStrip label="Kanban 14 ngày" inline>
            <StripStat label="Task xong" value={data.flow.throughput} sub={`${data.flow.throughput_points} SP · ${data.flow.per_week}/tuần`} />
            <StripStat label="Cycle time trung vị" {...cycleStat(data.flow.cycle_time_h)} />
            <StripStat label="Task mới" value={data.flow.created} />
          </StatStrip>
        ) : data.review ? (
          <SprintReviewView review={data.review} />
        ) : (
          <EmptyState icon="calendar" title="Dự án chưa có sprint nào đã đóng">
            Bắt đầu một sprint ở tab Backlog để xem dự báo, việc kẹt, tải và chất lượng.
          </EmptyState>
        )}
      </Card>
    </div>
  );
}
