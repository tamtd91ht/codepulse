"use client";

import Link from "next/link";
import { useState } from "react";

import { MultiSelect } from "@/components/MultiSelect";
import { HealthBadge, type HealthColor } from "@/components/tasks/TaskHealth";
import { TimelineMany } from "@/components/tasks/TaskTimeline";
import { Icon, type IconName } from "@/components/icons";
import { Card, EmptyState, Loading, PageHeader, Segmented } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import type { Me, OverviewTrends, TrendDirection } from "@/lib/types";

/*
 * Tổng quan Task cho Lead / Manager (docs/specs/31 §19.5, roadmap P4): one row per dự án the person
 * can see with Task on — active sprint, points done, projection, highest bugs, open critical
 * findings, median lead time, next release — each with a health label. Read-only.
 */

interface OverviewRow {
  business_project_id: number;
  name: string;
  key: string;
  sprint: { id: number; name: string; start_date: string | null; end_date: string | null } | null;
  done_pct: number | null;
  projected_pct: number | null;
  highest_bugs: number;
  open_critical: number;
  lead_time_h: number | null;
  next_version: { id: number; name: string; release_date: string | null; total: number; done: number; done_pct: number | null } | null;
  health: HealthColor;
  /** §20.6: the last 4 closed sprints, oldest first; null with none closed. */
  trends?: OverviewTrends | null;
}

const TREND_ARROW: Record<NonNullable<TrendDirection>, IconName> = { up: "trendUp", down: "trendDown", flat: "minus" };
const TREND_WORD: Record<NonNullable<TrendDirection>, string> = { up: "tăng", down: "giảm", flat: "đứng yên" };

/** One metric's arrow; every metric here is better lower, so up reads as worse. */
function Trend({ label, values, direction, names, fmt }: { label: string; values: (number | null)[]; direction: TrendDirection; names: string[]; fmt: (v: number | null) => string }) {
  const tip = `${label} 4 sprint gần nhất: ${values.map((v, i) => `${names[i] ?? ""} ${fmt(v)}`).join(" → ")}`;
  return (
    <span className={`sr-trend${direction ? ` sr-trend-${direction}` : ""}`} title={tip}>
      <span className="sr-trend-label">{label}</span>
      {direction ? <Icon name={TREND_ARROW[direction]} size={12} /> : <span className="th-muted">—</span>}
      <span className="visually-hidden">{direction ? TREND_WORD[direction] : "chưa đủ dữ liệu"}. {tip}</span>
    </span>
  );
}

function pct(v: number | null): string {
  return v === null ? "—" : `${v}%`;
}

function hours(v: number | null): string {
  if (v === null) return "—";
  return v >= 48 ? `${Math.round(v / 2.4) / 10} ngày` : `${v} giờ`;
}

export default function TaskOverviewPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const allowed = !me || can(me, "tasks.view");
  const rows = useApi<OverviewRow[]>(allowed ? "/tasks/overview" : null);
  // §21.3: a Timeline mode with the epics of several dự án (read-only), narrowed by a pick.
  const [mode, setMode] = useState<"table" | "timeline">("table");
  const [picked, setPicked] = useState<number[]>([]);

  if (!allowed)
    return (
      <>
        <PageHeader title="Tổng quan Task" />
        <EmptyState icon="alert" title="Bạn chưa có quyền xem task" />
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow={<Link href="/tasks">Task</Link>}
        title="Tổng quan dự án"
        subtitle="Sprint đang chạy, dự phóng hoàn thành, bug và lỗi nghiêm trọng của mọi dự án bạn xem được. Bấm một dòng để xem sức khoẻ sprint."
        actions={
          <Segmented
            label="Chế độ xem"
            value={mode}
            onChange={setMode}
            options={[
              { value: "table", label: "Bảng" },
              { value: "timeline", label: "Timeline" },
            ]}
          />
        }
      />
      {mode === "timeline" ? (
        <Card title="Timeline theo Epic" subtitle="Epic của các dự án bạn xem được, chỉ đọc. Mở một dự án để kéo đổi hạn hoặc sprint.">
          <div className="rm-many-filter">
            <MultiSelect
              label="Dự án"
              options={(rows.data ?? []).map((r) => ({ value: r.business_project_id, label: r.name, hint: r.key }))}
              value={picked}
              onChange={setPicked}
              empty="Mọi dự án"
              disabled={!rows.data}
            />
          </div>
          <TimelineMany spaces={picked} />
        </Card>
      ) : (
      <Card flush title="Dự án" subtitle="Rủi ro: dự phóng dưới 70%, hoặc có bug mức cao nhất / lỗi critical đang mở. Cần chú ý: dự phóng dưới 90%.">
        {rows.error ? (
          <div className="th-error">
            <Loading error={rows.error} />
            <button type="button" className="ghost sm" onClick={() => void rows.mutate()}>
              Thử lại
            </button>
          </div>
        ) : !rows.data ? (
          <Loading rows={5} />
        ) : !rows.data.length ? (
          <EmptyState icon="inbox" title="Chưa có dự án nào dùng Task">
            Dự án bật Task và bạn được xem sẽ hiện ở đây.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="th-table">
              <thead>
                <tr>
                  <th>Dự án</th>
                  <th>Sức khoẻ</th>
                  <th>Sprint đang chạy</th>
                  <th className="num">Point xong</th>
                  <th className="num">Dự phóng</th>
                  <th className="num">Bug cao nhất</th>
                  <th className="num">Lỗi critical</th>
                  <th className="num" title="Từ lúc tạo tới lúc xong, trung vị 30 ngày qua">Lead time</th>
                  <th>Phiên bản kế tiếp</th>
                  <th title="4 sprint đã đóng gần nhất: lead time trung vị, bug mức cao nhất và finding critical mở ra trong sprint. Mũi tên lên là xấu đi">Xu hướng 4 sprint</th>
                </tr>
              </thead>
              <tbody>
                {rows.data.map((r) => (
                  <tr key={r.business_project_id}>
                    <td>
                      <Link href={`/tasks?space=${r.business_project_id}&tab=health`} className="th-row-link">
                        {r.name} <span className="mono th-muted">{r.key}</span>
                      </Link>
                    </td>
                    <td>
                      <HealthBadge value={r.health} />
                    </td>
                    <td>{r.sprint ? r.sprint.name : <span className="th-muted">Không có</span>}</td>
                    <td className="num">{pct(r.done_pct)}</td>
                    <td className="num">{pct(r.projected_pct)}</td>
                    <td className={`num${r.highest_bugs ? " th-bad" : ""}`}>{r.highest_bugs}</td>
                    <td className={`num${r.open_critical ? " th-bad" : ""}`}>{r.open_critical}</td>
                    <td className="num">{hours(r.lead_time_h)}</td>
                    <td>
                      {r.next_version ? (
                        <span>
                          {r.next_version.name}
                          {r.next_version.release_date && <span className="th-muted"> · {dayLabel(r.next_version.release_date)}</span>}
                          <span className="th-muted"> · {pct(r.next_version.done_pct)} xong</span>
                        </span>
                      ) : (
                        <span className="th-muted">—</span>
                      )}
                    </td>
                    <td>
                      {r.trends ? (
                        <span className="sr-trends">
                          <Trend label="Lead" {...r.trends.lead_time_h} names={r.trends.sprints} fmt={hours} />
                          <Trend label="Bug" {...r.trends.highest_bugs} names={r.trends.sprints} fmt={(v) => String(v ?? 0)} />
                          <Trend label="Critical" {...r.trends.critical_findings} names={r.trends.sprints} fmt={(v) => String(v ?? 0)} />
                        </span>
                      ) : (
                        <span className="th-muted" title="Chưa có sprint nào đã đóng">
                          —
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      )}
    </>
  );
}
