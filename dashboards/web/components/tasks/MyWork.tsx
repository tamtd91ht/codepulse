"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import { GitBadge, PointsChip, RowPriority, StatusPill, TypeIcon } from "@/components/tasks/taskParts";
import { EmptyState, Loading } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { TaskRow } from "@/lib/types";

/*
 * "Việc của tôi" (docs/specs/31 §19.3): one screen answering "what do I do now" — what needs me
 * right away, then my tasks by state, then how my sprints stand. Data: GET /tasks/my-work.
 */

type AttentionKind = "overdue" | "critical" | "review" | "mention" | "test_failed";
type AttentionAction = "start" | "open" | "findings" | "review" | "reply";

interface MyWorkMr {
  project: string;
  iid: number;
  title: string | null;
  web_url: string | null;
  age_hours: number | null;
  first_review_at: string | null;
}

interface AttentionItem {
  kind: AttentionKind;
  text: string;
  action: AttentionAction;
  task?: TaskRow;
  mr?: MyWorkMr;
  task_key?: string | null;
  link?: string | null;
  notification_id?: number;
}

interface MySprint {
  id: number;
  name: string;
  space: number;
  end_date: string | null;
  days_left: number | null;
  points: number;
  done_points: number;
  tasks: number;
  done_tasks: number;
}

interface MyWorkData {
  attention: AttentionItem[];
  doing: TaskRow[];
  waiting: (TaskRow & { blocked_by: string[] })[];
  todo: TaskRow[];
  done_week: TaskRow[];
  sprints: MySprint[];
}

const KIND: Record<AttentionKind, { icon: IconName; label: string }> = {
  overdue: { icon: "calendar", label: "Quá hạn" },
  critical: { icon: "alertCircle", label: "Finding critical" },
  review: { icon: "review", label: "Chờ review" },
  mention: { icon: "bell", label: "Nhắc đến bạn" },
  test_failed: { icon: "clipboardCheck", label: "Test thất bại" },
};

const taskHref = (key: string) => `/tasks/${encodeURIComponent(key)}`;
const isHttp = (url: string | null | undefined): url is string => !!url && /^https?:\/\//i.test(url);
/** An in-app path ("/tasks/X") or an http(s) URL; anything else is dropped. */
function safeLink(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  return isHttp(url) ? url : null;
}

export function MyWork() {
  const { data, error, mutate } = useApi<MyWorkData>("/tasks/my-work", { refreshInterval: 60_000 });

  if (!data)
    return error ? (
      <div className="mw-error">
        <Loading error={error} />
        <button type="button" onClick={() => mutate()}>
          <Icon name="sync" size={16} /> Thử lại
        </button>
      </div>
    ) : (
      <div className="mw">
        <section className="mw-block" aria-label="Đang tải">
          <Loading rows={4} />
        </section>
        <section className="mw-block">
          <Loading rows={6} />
        </section>
      </div>
    );

  return (
    <div className="mw">
      <section className="mw-block mw-attention" aria-labelledby="mw-attention-h">
        <h2 id="mw-attention-h" className="mw-h">
          Cần xử lý ngay <span className="mw-count">{data.attention.length}</span>
        </h2>
        {data.attention.length === 0 ? (
          <EmptyState icon="checkCircle" tone="good" title="Không có gì cần xử lý ngay">
            Không có task quá hạn, finding critical, MR chờ bạn review hay lời nhắc nào.
          </EmptyState>
        ) : (
          <ul className="mw-list">
            {data.attention.map((item, i) => (
              <AttentionRow key={`${item.kind}-${item.task?.key ?? item.task_key ?? item.mr?.iid ?? item.notification_id ?? i}-${i}`} item={item} />
            ))}
          </ul>
        )}
      </section>

      <TaskSection title="Đang làm" rows={data.doing} empty="Bạn chưa có task nào đang làm. Chọn một task ở “Cần làm” và chuyển sang trạng thái đang làm." />
      <TaskSection
        title="Chờ người khác"
        rows={data.waiting}
        empty="Không có task nào của bạn đang bị chặn."
        extra={(row) => {
          const by = (row as TaskRow & { blocked_by?: string[] }).blocked_by ?? [];
          return by.length ? (
            <span className="mw-blocked small">
              bị chặn bởi{" "}
              {by.map((k, i) => (
                <span key={k}>
                  {i > 0 && ", "}
                  <Link href={taskHref(k)}>{k}</Link>
                </span>
              ))}
            </span>
          ) : null;
        }}
      />
      <TaskSection title="Cần làm" rows={data.todo} empty="Hết việc cần làm. Xem Backlog của dự án để nhận thêm task." />
      <TaskSection title="Xong tuần này" rows={data.done_week} empty="Tuần này bạn chưa hoàn thành task nào." collapsed />

      <section className="mw-block" aria-labelledby="mw-sprint-h">
        <h2 id="mw-sprint-h" className="mw-h">
          Sprint của tôi <span className="mw-count">{data.sprints.length}</span>
        </h2>
        {data.sprints.length === 0 ? (
          <p className="muted small mw-empty">Bạn không có task trong sprint đang chạy nào.</p>
        ) : (
          <ul className="mw-sprints">
            {data.sprints.map((s) => (
              <SprintStrip key={s.id} sprint={s} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const kind = KIND[item.kind] ?? { icon: "info" as IconName, label: item.kind };
  const key = item.task?.key ?? item.task_key ?? null;
  const mrUrl = isHttp(item.mr?.web_url) ? item.mr?.web_url ?? null : null;
  const link = safeLink(item.link);

  let subject: ReactNode;
  if (item.task) {
    subject = (
      <Link href={taskHref(item.task.key)} className="mw-subject">
        <span className="mw-key">{item.task.key}</span> {item.task.title}
      </Link>
    );
  } else if (item.mr) {
    const title = item.mr.title ?? `!${item.mr.iid}`;
    subject = mrUrl ? (
      <a href={mrUrl} target="_blank" rel="noreferrer" className="mw-subject">
        <span className="mw-key">
          {item.mr.project} !{item.mr.iid}
        </span>{" "}
        {title}
        <Icon name="external" size={12} />
      </a>
    ) : (
      <span className="mw-subject">
        <span className="mw-key">
          {item.mr.project} !{item.mr.iid}
        </span>{" "}
        {title}
      </span>
    );
  } else if (key) {
    subject = (
      <Link href={taskHref(key)} className="mw-subject">
        <span className="mw-key">{key}</span>
      </Link>
    );
  } else subject = null;

  // One action per row; "start" needs a status id, so it opens the task instead.
  let action: ReactNode = null;
  const external = (href: string, label: string) => (
    <a className="button sm" href={href} target="_blank" rel="noreferrer">
      {label}
      <Icon name="external" size={12} />
    </a>
  );
  if (item.action === "review") {
    if (mrUrl) action = external(mrUrl, "Review");
  } else if (item.action === "reply") {
    if (link) action = link.startsWith("/") ? <Link className="button sm" href={link}>Trả lời</Link> : external(link, "Trả lời");
    else if (key) action = <Link className="button sm" href={taskHref(key)}>Trả lời</Link>;
  } else if (key) {
    action = (
      <Link className="button sm" href={taskHref(key)}>
        {item.action === "findings" ? "Xem finding" : "Mở"}
      </Link>
    );
  } else if (link) {
    action = link.startsWith("/") ? <Link className="button sm" href={link}>Mở</Link> : external(link, "Mở");
  }

  return (
    <li className={`mw-row mw-kind-${item.kind}`}>
      <span className="mw-kind" title={kind.label}>
        <Icon name={kind.icon} size={16} />
        <span className="visually-hidden">{kind.label}</span>
      </span>
      <div className="mw-main">
        {subject}
        <span className="mw-text small muted">{item.text}</span>
      </div>
      {action && <div className="mw-action">{action}</div>}
    </li>
  );
}

function TaskSection({
  title,
  rows,
  empty,
  extra,
  collapsed,
}: {
  title: string;
  rows: TaskRow[];
  empty: string;
  extra?: (row: TaskRow) => ReactNode;
  collapsed?: boolean;
}) {
  const [open, setOpen] = useState(!collapsed);
  const id = `mw-sec-${title.replace(/\s+/g, "-")}`;
  return (
    <section className="mw-block" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="mw-h">
        <button type="button" className="section-toggle mw-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          <Icon name={open ? "chevronDown" : "chevronRight"} size={16} />
          {title} <span className="mw-count">{rows.length}</span>
        </button>
      </h2>
      <div id={id} hidden={!open}>
        {rows.length === 0 ? (
          <p className="muted small mw-empty">{empty}</p>
        ) : (
          <ul className="mw-list">
            {rows.map((row) => (
              <li key={row.key} className="mw-row mw-task">
                <TypeIcon type={row.type} />
                <div className="mw-main">
                  <Link href={taskHref(row.key)} className="mw-subject">
                    <span className="mw-key">{row.key}</span> {row.title}
                  </Link>
                  <span className="mw-meta">
                    <StatusPill status={row.status} resolution={row.resolution} />
                    <RowPriority row={row} />
                    <PointsChip points={row.story_points} />
                    {row.due_date && <span className="small muted">hạn {fmtDay(row.due_date)}</span>}
                    <GitBadge row={row} />
                    {extra?.(row)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function SprintStrip({ sprint }: { sprint: MySprint }) {
  const pct = sprint.points > 0 ? Math.min(100, Math.round((sprint.done_points / sprint.points) * 100)) : 0;
  const left =
    sprint.days_left === null ? null : sprint.days_left < 0 ? `quá ${-sprint.days_left} ngày` : sprint.days_left === 0 ? "kết thúc hôm nay" : `còn ${sprint.days_left} ngày`;
  return (
    <li className="mw-sprint">
      <div className="mw-sprint-head">
        <strong>{sprint.name}</strong>
        {left && <span className={`small${sprint.days_left !== null && sprint.days_left < 0 ? " mw-late" : " muted"}`}>{left}</span>}
        <span className="mw-sprint-sp small">
          tôi xong {sprint.done_points}/{sprint.points} SP · {sprint.done_tasks}/{sprint.tasks} task
        </span>
      </div>
      <div
        className="mw-bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={`${sprint.name}: xong ${pct}% story point`}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
    </li>
  );
}

function fmtDay(day: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  return m ? `${m[3]}/${m[2]}` : day;
}
