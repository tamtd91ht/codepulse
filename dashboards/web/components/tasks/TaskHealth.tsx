"use client";

import Link from "next/link";
import { type ReactNode, useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Icon } from "@/components/icons";
import { Alert, Card, ChartTip, Expander, Loading, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import type { ForecastRange, LatestSprintReview, SprintReview, SprintScopeChange, SprintTaskGroup, TaskPerson, TaskSpace } from "@/lib/types";

import { PersonAvatar, useSprints } from "./taskParts";

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

/** "" = the running sprint (GET …/health); a number = a closed sprint's review. */
type SprintPick = "" | number;

export function TaskHealth({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const res = useApi<SprintHealth>(`/tasks/spaces/${bp}/health`);
  const sprints = useSprints(bp);
  const closed = useMemo(
    () => (sprints.data ?? []).filter((s) => s.state === "closed").sort((a, b) => (b.closed_at ?? b.end_date ?? "").localeCompare(a.closed_at ?? a.end_date ?? "") || b.id - a.id),
    [sprints.data],
  );
  const [pick, setPick] = useState<SprintPick>("");
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

  const picker = (
    <SprintChip
      running={h?.sprint ? h.sprint.name : null}
      closed={closed.map((s) => ({ id: s.id, name: s.name }))}
      value={pick}
      onChange={setPick}
    />
  );

  if (pick !== "") return <ClosedSprint id={pick} picker={picker} />;
  if (h && !h.sprint) return <NoActiveSprint space={space} picker={closed.length ? picker : null} onPick={setPick} lastClosed={closed[0]?.id ?? null} />;

  const fc = h?.forecast;
  const st = h?.stuck;
  const load = h?.load ?? [];
  const qa = h?.quality;
  const maxPoints = Math.max(...load.map((l) => l.open_points), 1);
  const maxOpen = Math.max(...load.map((l) => l.open), 1);
  const scope = h?.scope_change;
  const scopeChanged = !!scope && (!!scope.added_after_start.count || !!scope.removed_after_start.count);

  return (
    <div className="sh">
      <div className="sh-toolbar">
        {picker}
        {h?.sprint?.start_date && h.sprint.end_date && (
          <span className="small muted">
            {dayLabel(h.sprint.start_date)} – {dayLabel(h.sprint.end_date)}
          </span>
        )}
        {h?.health && <HealthBadge value={h.health} />}
        {h?.sprint?.goal && (
          <span className="sh-goal small muted" title={h.sprint.goal}>
            Mục tiêu: {h.sprint.goal}
          </span>
        )}
        <span className="sh-spacer" />
        <Link href="/tasks/overview" className="button sm ghost">
          Tổng quan mọi dự án
        </Link>
      </div>

      <div className="sh-grid">
        <Card title="Dự báo" id="th-forecast" className="sh-cell sh-forecast" collapsible={false}>
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
                <p className="sh-none muted">
                  <Icon name="calendar" size={14} /> Sprint chưa có ngày bắt đầu / kết thúc
                </p>
              ) : (
                <div className="chart-box th-chart sh-chart" role="img" aria-label={`Burndown ${h?.sprint?.name ?? ""}: đường lý tưởng, còn lại và dự phóng`}>
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

        <Card
          title={
            <span title={st ? `Quá ${st.doing_warning_hours} giờ trong cột Đang làm (hoặc ngưỡng của trạng thái), MR chờ review quá ${st.mr_review_hours} giờ, task bị chặn` : undefined}>
              Kẹt{st?.count ? <span className="sh-count">{st.count}</span> : null}
            </span>
          }
          id="th-stuck"
          className="sh-cell"
          collapsible={false}
        >
          {errorBox ?? (!st ? (
            <Loading rows={4} />
          ) : !st.count ? (
            <p className="sh-none sh-good">
              <Icon name="checkCircle" size={14} /> Không có việc nào đang kẹt
            </p>
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

        <Card
          title={<span title="Point (thanh trên) và task (thanh dưới) đang mở của mỗi người trong sprint, xếp theo tên">Tải</span>}
          id="th-load"
          className="sh-cell"
          collapsible={false}
        >
          {errorBox ?? (!h ? (
            <Loading rows={4} />
          ) : !load.length ? (
            <p className="sh-none sh-good">
              <Icon name="checkCircle" size={14} /> Không còn task mở trong sprint
            </p>
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

        <Card
          title={<span title="Tín hiệu từ review code và bug trong thời gian sprint; task thêm hoặc bỏ sau khi sprint bắt đầu">Chất lượng</span>}
          id="th-quality"
          className="sh-cell"
          collapsible={false}
        >
          {errorBox ?? (!qa || !h ? (
            <Loading rows={2} />
          ) : (
            <>
              <StatStrip label="Chất lượng sprint" inline>
                <StripStat label="Critical đang mở" value={qa.open_critical} tone={qa.open_critical ? "bad" : "good"} title="Finding critical còn mở trên review mới nhất của commit / MR nối với task trong sprint" />
                <StripStat label="Bug cao nhất mở" value={qa.highest_bugs} tone={qa.highest_bugs ? "bad" : "good"} title="Bug trong sprint có ưu tiên mức cao nhất, chưa xong" />
                <StripStat label="Bug tạo" value={qa.bugs_created} title="Task loại Bug của dự án tạo trong khoảng ngày của sprint" />
                <StripStat label="Task mở lại" value={qa.reopened} tone={qa.reopened ? "warn" : undefined} title="Task trong sprint rời trạng thái Xong về trạng thái mở trong thời gian sprint" />
              </StatStrip>
              <h4 className="th-sub sh-scope-h">Thay đổi phạm vi</h4>
              {!scopeChanged || !scope ? (
                <p className="sh-none sh-good">
                  <Icon name="checkCircle" size={14} /> Phạm vi giữ nguyên từ lúc bắt đầu
                </p>
              ) : (
                <div className="sr-scope">
                  <ScopeList title="Thêm sau khi bắt đầu" sign="+" group={scope.added_after_start} />
                  <ScopeList title="Bỏ ra sau khi bắt đầu" sign="−" group={scope.removed_after_start} />
                </div>
              )}
            </>
          ))}
        </Card>
      </div>
    </div>
  );
}

/** "Sprint: Sprint demo ▾" on the toolbar: the running sprint or a closed one's review. */
function SprintChip({
  running,
  closed,
  value,
  onChange,
}: {
  running: string | null;
  closed: { id: number; name: string }[];
  value: SprintPick;
  onChange: (v: SprintPick) => void;
}) {
  return (
    <label className="sh-chip">
      <span className="sh-chip-label">Sprint:</span>
      <select value={value === "" ? "" : String(value)} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : "")} aria-label="Chọn sprint">
        <option value="">{running ? `${running} (đang chạy)` : "Đang chạy: không có"}</option>
        {closed.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} (đã đóng)
          </option>
        ))}
      </select>
    </label>
  );
}

/** A closed sprint picked on the chip: its review (§20.6). */
function ClosedSprint({ id, picker }: { id: number; picker: ReactNode }) {
  const res = useApi<SprintReview>(`/tasks/sprints/${id}/review`);
  const r = res.data;
  return (
    <div className="sh">
      <div className="sh-toolbar">
        {picker}
        {r?.sprint.start_date && r.sprint.end_date && (
          <span className="small muted">
            {dayLabel(r.sprint.start_date)} – {dayLabel(r.sprint.end_date)}
          </span>
        )}
        <span className="sh-spacer" />
        {r && <RetroButton review={r} />}
      </div>
      <Card title="Đánh giá sprint" id="sr-picked" collapsible={false}>
        {res.error ? (
          <div className="th-error">
            <Loading error={res.error} />
            <button type="button" className="ghost sm" onClick={() => void res.mutate()}>
              Thử lại
            </button>
          </div>
        ) : !r ? (
          <Loading rows={3} />
        ) : (
          <SprintReviewView review={r} />
        )}
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


/**
 * No sprint running (§22.7): one compact card (icon, a sentence, "Bắt đầu sprint" and "Tổng quan
 * mọi dự án"), then the last closed sprint's numbers, or the Kanban flow of 14 days.
 */
function NoActiveSprint({
  space,
  picker,
  onPick,
  lastClosed,
}: {
  space: TaskSpace;
  picker: ReactNode;
  onPick: (id: number) => void;
  lastClosed: number | null;
}) {
  const bp = space.business_project_id;
  const res = useApi<LatestSprintReview>(`/tasks/spaces/${bp}/review/latest`);
  const kanban = space.board_mode === "kanban";
  const data = res.data;
  return (
    <div className="sh">
      {picker && <div className="sh-toolbar">{picker}</div>}
      <section className="sh-empty" aria-label={kanban ? "Kanban" : "Chưa có sprint đang chạy"}>
        <span className="sh-empty-icon" aria-hidden="true">
          <Icon name={kanban ? "activity" : "calendar"} size={18} />
        </span>
        <p className="sh-empty-text">
          {kanban ? (
            <>
              <strong>Dự án chạy Kanban</strong> · không theo sprint; dưới đây là dòng chảy 14 ngày qua.
            </>
          ) : (
            <>
              <strong>Chưa có sprint đang chạy</strong> · bắt đầu một sprint để xem dự báo, việc kẹt, tải và chất lượng.
            </>
          )}
        </p>
        <span className="sh-empty-actions">
          {!kanban && (
            <Link href={`/tasks?space=${bp}&tab=backlog`} className="button sm primary ink">
              Bắt đầu sprint
            </Link>
          )}
          <Link href="/tasks/overview" className="button sm">
            Tổng quan mọi dự án
          </Link>
        </span>
      </section>

      {res.error ? (
        <div className="th-error">
          <Loading error={res.error} />
          <button type="button" className="ghost sm" onClick={() => void res.mutate()}>
            Thử lại
          </button>
        </div>
      ) : !data ? (
        <Loading rows={2} />
      ) : data.flow ? (
        <Card title="Kanban: 14 ngày qua" id="sr-latest" subtitle="Số task xong, cycle time và task mới, không theo sprint" collapsible={false}>
          <StatStrip label="Kanban 14 ngày" inline>
            <StripStat label="Task xong" value={data.flow.throughput} sub={`${data.flow.throughput_points} SP · ${data.flow.per_week}/tuần`} />
            <StripStat label="Cycle time trung vị" {...cycleStat(data.flow.cycle_time_h)} />
            <StripStat label="Task mới" value={data.flow.created} />
          </StatStrip>
        </Card>
      ) : data.review ? (
        <Card
          title={`Sprint gần nhất · ${data.review.sprint.name}`}
          id="sr-latest"
          collapsible={false}
          actions={
            <span className="sh-latest-actions">
              {lastClosed !== null && (
                <button type="button" className="ghost sm" onClick={() => onPick(lastClosed)}>
                  Xem chi tiết
                </button>
              )}
              <RetroButton review={data.review} />
            </span>
          }
        >
          <SprintReviewView review={data.review} />
        </Card>
      ) : (
        <p className="sh-none muted">
          <Icon name="info" size={14} /> Dự án chưa có sprint nào đã đóng.
        </p>
      )}
    </div>
  );
}
