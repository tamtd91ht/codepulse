"use client";

import Link from "next/link";
import { type ReactNode, useMemo, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import { GitBadge, PointsChip, RowPriority, StatusPill, TypeIcon, spaceOf, useTaskSpaces } from "@/components/tasks/taskParts";
import { Loading, Segmented } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Me, TaskRow } from "@/lib/types";
import { useDrawerList } from "./drawerNav";
import { Popover, ValueList } from "./FilterBar";
import { useViewOptions } from "./filterState";

/*
 * "Việc của tôi" (docs/specs/31 §19.3, layout §22.7): one screen answering "what do I do now" —
 * what needs me right away, then my tasks by state, then how my sprints stand. Two columns from
 * 1280px (left: now + doing; right: waiting, to do, done this week, my sprints); an empty group is
 * one 32px line, a task one 36px row. Data: GET /tasks/my-work.
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

// §22.6: opens in the right drawer on this page (?task=KEY); the list stays.
const taskHref = (key: string) => `/tasks/me?task=${encodeURIComponent(key)}`;
const isHttp = (url: string | null | undefined): url is string => !!url && /^https?:\/\//i.test(url);
/** An in-app path ("/tasks/X") or an http(s) URL; anything else is dropped. */
function safeLink(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  return isHttp(url) ? url : null;
}

type MyView = { density: "dense" | "comfy"; doneWeek: boolean; sprints: boolean };
const MY_VIEW: MyView = { density: "dense", doneWeek: true, sprints: true };

export function MyWork() {
  const { data, error, mutate } = useApi<MyWorkData>("/tasks/my-work", { refreshInterval: 60_000 });
  const { data: me } = useApi<Me>("/auth/me");
  const spaces = useTaskSpaces();
  // The reduced FilterBar (UI/UX §4.4, docs/specs/31 §22.9): search, the dự án chip, Hiển thị. The
  // shared FilterBar is per dự án (its fields, people and counts come from one dự án); this page
  // spans all of them, so it keeps the same row and pieces (fb- styles, Popover, ValueList) with
  // a filter applied in the browser.
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const [view, setView] = useViewOptions<MyView>("me", me?.id, MY_VIEW);

  const projects = useMemo(() => {
    if (!data) return [];
    const ids = new Set<number>();
    for (const row of [...data.doing, ...data.waiting, ...data.todo, ...data.done_week]) {
      const id = spaceOf(row);
      if (id !== null) ids.add(id);
    }
    const name = (id: number) => spaces.data?.find((s) => s.business_project_id === id)?.name ?? `Dự án ${id}`;
    return [...ids].map((id) => ({ id, name: name(id) })).sort((a, b) => a.name.localeCompare(b.name, "vi"));
  }, [data, spaces.data]);

  const needle = q.trim().toLowerCase();
  const inProject = (id: number | null) => !picked.length || (id !== null && picked.includes(id));
  const keep = <T extends TaskRow>(rows: T[]) =>
    rows.filter((r) => inProject(spaceOf(r)) && (!needle || r.key.toLowerCase().includes(needle) || r.title.toLowerCase().includes(needle)));
  const filtered = needle !== "" || picked.length > 0;
  // §22.6: J/K in the drawer walk the tasks in the order the page lists them.
  useDrawerList(data ? [...data.doing, ...data.waiting, ...data.todo, ...data.done_week].map((r) => r.key) : []);

  if (!data)
    return error ? (
      <div className="mw-error">
        <Loading error={error} />
        <button type="button" onClick={() => mutate()}>
          <Icon name="sync" size={16} /> Thử lại
        </button>
      </div>
    ) : (
      <div className="mw-cols" aria-busy="true">
        <section className="mw-block" aria-label="Đang tải">
          <Loading rows={4} />
        </section>
        <section className="mw-block">
          <Loading rows={6} />
        </section>
      </div>
    );

  const attention = data.attention.filter((item) => {
    if (item.task && !inProject(spaceOf(item.task))) return false;
    if (!needle) return true;
    const hay = `${item.task?.key ?? item.task_key ?? ""} ${item.task?.title ?? item.mr?.title ?? ""} ${item.text}`.toLowerCase();
    return hay.includes(needle);
  });

  const shownTasks = [data.doing, data.waiting, data.todo, ...(view.doneWeek ? [data.done_week] : [])].reduce((n, rows) => n + keep(rows).length, 0);

  return (
    <div className={`mw${view.density === "comfy" ? " nw-mw-comfy" : ""}`}>
      <div className="fb-bar mw-fb" role="toolbar" aria-label="Lọc việc của tôi">
        <div className={`fb-search${q ? " wide" : ""}`} role="search">
          <Icon name="search" size={14} />
          <label className="sr-only" htmlFor="mw-q">
            Tìm trong việc của tôi
          </label>
          <input id="mw-q" type="text" value={q} maxLength={200} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Escape" && q && (e.preventDefault(), setQ(""))} placeholder="Tìm mã hoặc tiêu đề…" />
          {q && (
            <button type="button" className="fb-x" aria-label="Xoá chữ tìm" onClick={() => setQ("")}>
              <Icon name="close" size={12} />
            </button>
          )}
        </div>
        {projects.length > 1 && <ProjectChip projects={projects} picked={picked} onChange={setPicked} />}
        {filtered && (
          <button type="button" className="link small fb-clear" onClick={() => (setQ(""), setPicked([]))}>
            Xoá lọc
          </button>
        )}
        <div className="fb-right">
          <span className="mw-refresh small muted" title="Tự làm mới mỗi phút">
            <Icon name="sync" size={12} /> <span className="fb-btn-text">tự làm mới mỗi phút</span>
          </span>
          <span className={`fb-count${filtered ? " filtered" : ""}`} aria-live="polite">
            {shownTasks} task
            {filtered && <span className="fb-count-tag"> · đã lọc</span>}
          </span>
          <MyDisplay view={view} onChange={setView} />
        </div>
      </div>

      <div className="mw-cols">
        <div className="mw-col">
          {attention.length === 0 ? (
            <p className="mw-none mw-none-good" role="status">
              <Icon name="checkCircle" size={14} /> Không có gì cần xử lý ngay
            </p>
          ) : (
            <section className="mw-block mw-attention" aria-labelledby="mw-attention-h">
              <h2 id="mw-attention-h" className="mw-h">
                Cần xử lý ngay <span className="mw-count">{attention.length}</span>
              </h2>
              <ul className="mw-list">
                {attention.map((item, i) => (
                  <AttentionRow key={`${item.kind}-${item.task?.key ?? item.task_key ?? item.mr?.iid ?? item.notification_id ?? i}-${i}`} item={item} />
                ))}
              </ul>
            </section>
          )}
          <TaskSection title="Đang làm" rows={keep(data.doing)} empty="chưa có task nào đang làm" />
        </div>

        <div className="mw-col">
          <TaskSection
            title="Chờ người khác"
            rows={keep(data.waiting)}
            empty="không có task nào đang bị chặn"
            extra={(row) => {
              const by = (row as TaskRow & { blocked_by?: string[] }).blocked_by ?? [];
              return by.length ? (
                <span className="mw-blocked small">
                  chặn bởi{" "}
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
          <TaskSection title="Cần làm" rows={keep(data.todo)} empty="hết việc · xem Backlog của dự án để nhận thêm" />
          {view.doneWeek && <TaskSection title="Xong tuần này" rows={keep(data.done_week)} empty="tuần này chưa xong task nào" collapsed />}

          {!view.sprints ? null : data.sprints.length === 0 ? (
            <p className="mw-none muted">
              <span className="mw-none-title">Sprint của tôi</span> · không có task trong sprint đang chạy
            </p>
          ) : (
            <section className="mw-block" aria-labelledby="mw-sprint-h">
              <h2 id="mw-sprint-h" className="mw-h">
                Sprint của tôi <span className="mw-count">{data.sprints.length}</span>
              </h2>
              <ul className="mw-sprints">
                {data.sprints.map((s) => (
                  <SprintStrip key={s.id} sprint={s} />
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

/** "Dự án ▾" (several picked: "Dự án: A, B ✕"): a FilterBar-style chip with a searchable list. */
function ProjectChip({ projects, picked, onChange }: { projects: { id: number; name: string }[]; picked: number[]; onChange: (next: number[]) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const names = projects.filter((p) => picked.includes(p.id)).map((p) => p.name);
  const text = names.join(", ");
  return (
    <span className={`fb-chip fb-pinned${picked.length ? " fb-active" : ""}`} ref={ref} data-narrow-tip={picked.length ? `Dự án: ${text}` : "Dự án"}>
      <button
        type="button"
        className="fb-chip-text"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={picked.length ? `Sửa lọc Dự án: ${text}` : "Lọc theo dự án"}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="folder" size={14} className="fb-pin-icon" />
        {picked.length ? (
          <>
            <span className="fb-pin-label">
              <span className="fb-chip-field">Dự án:</span> {text}
            </span>
            <span className="fb-pin-n" aria-hidden="true">
              {picked.length}
            </span>
          </>
        ) : (
          <span className="fb-pin-label">
            Dự án <Icon name="more" size={12} />
          </span>
        )}
      </button>
      {picked.length > 0 && (
        <button type="button" className="fb-x" aria-label={`Bỏ lọc Dự án: ${text}`} onClick={() => onChange([])}>
          <Icon name="close" size={12} />
        </button>
      )}
      {open && (
        <Popover anchor={ref} onClose={() => setOpen(false)} label="Lọc dự án">
          <ValueList
            title="Dự án"
            options={projects.map((p) => ({ value: String(p.id), label: p.name }))}
            picked={picked.map(String)}
            onToggle={(v) => {
              const id = Number(v);
              onChange(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
            }}
            onClear={() => onChange([])}
          />
        </Popover>
      )}
    </span>
  );
}

/** "⧉ Hiển thị" of Việc của tôi: density and the two optional blocks, per person (useViewOptions). */
function MyDisplay({ view, onChange }: { view: MyView; onChange: (patch: Partial<MyView>) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={ref}
        type="button"
        className="fb-chip fb-display"
        aria-haspopup="dialog"
        aria-expanded={open}
        data-narrow-tip="Hiển thị"
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="settings" size={14} /> <span className="fb-btn-text">Hiển thị</span>
      </button>
      {open && (
        <Popover anchor={ref} onClose={() => setOpen(false)} label="Hiển thị" width={300} align="end">
          <div className="fb-display-pop">
            <div className="fb-display-row">
              <span className="fb-display-label">Mật độ</span>
              <Segmented<MyView["density"]>
                label="Mật độ hiển thị"
                value={view.density}
                onChange={(density) => onChange({ density })}
                options={[
                  { value: "dense", label: "Gọn" },
                  { value: "comfy", label: "Thoáng" },
                ]}
              />
            </div>
            <label className="fb-display-row">
              <span className="fb-display-label">Hiện “Xong tuần này”</span>
              <input type="checkbox" checked={view.doneWeek} onChange={(e) => onChange({ doneWeek: e.target.checked })} />
            </label>
            <label className="fb-display-row">
              <span className="fb-display-label">Hiện “Sprint của tôi”</span>
              <input type="checkbox" checked={view.sprints} onChange={(e) => onChange({ sprints: e.target.checked })} />
            </label>
            <button type="button" className="link small" onClick={() => onChange(MY_VIEW)}>
              Về mặc định
            </button>
          </div>
        </Popover>
      )}
    </>
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
        <span className="mw-key">{item.task.key}</span> <span className="mw-title">{item.task.title}</span>
      </Link>
    );
  } else if (item.mr) {
    const title = item.mr.title ?? `!${item.mr.iid}`;
    subject = mrUrl ? (
      <a href={mrUrl} target="_blank" rel="noreferrer" className="mw-subject">
        <span className="mw-key">
          {item.mr.project} !{item.mr.iid}
        </span>{" "}
        <span className="mw-title">{title}</span>
        <Icon name="external" size={12} />
      </a>
    ) : (
      <span className="mw-subject">
        <span className="mw-key">
          {item.mr.project} !{item.mr.iid}
        </span>{" "}
        <span className="mw-title">{title}</span>
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
        <Icon name={kind.icon} size={14} />
        <span className="visually-hidden">{kind.label}</span>
      </span>
      <div className="mw-main">
        {subject}
        <span className="mw-text small muted" title={item.text}>
          {item.text}
        </span>
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
  // An empty group is one 32px line, not a block (§22.7).
  if (rows.length === 0)
    return (
      <p className="mw-none muted">
        <span className="mw-none-title">{title}</span> · {empty}
      </p>
    );
  return (
    <section className="mw-block" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`} className="mw-h">
        <button type="button" className="section-toggle mw-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
          <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
          {title} <span className="mw-count">{rows.length}</span>
        </button>
      </h2>
      <div id={id} hidden={!open}>
        <ul className="mw-list">
          {rows.map((row) => (
            <li key={row.key} className="mw-row mw-task">
              <TypeIcon type={row.type} />
              <Link href={taskHref(row.key)} className="mw-subject">
                <span className="mw-key">{row.key}</span> <span className="mw-title">{row.title}</span>
              </Link>
              <span className="mw-meta">
                {extra?.(row)}
                {row.due_date && <span className="small muted">hạn {fmtDay(row.due_date)}</span>}
                <GitBadge row={row} />
                <StatusPill status={row.status} resolution={row.resolution} />
                <RowPriority row={row} />
                <PointsChip points={row.story_points} />
              </span>
              <Link href={taskHref(row.key)} className="button sm mw-hover-act" aria-label={`Mở ${row.key}`}>
                Mở
              </Link>
            </li>
          ))}
        </ul>
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
