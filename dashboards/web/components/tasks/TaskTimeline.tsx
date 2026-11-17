"use client";

import Link from "next/link";
import { type DragEvent, type PointerEvent, type ReactNode, useMemo, useRef, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Alert, EmptyState, Loading, Message } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import type { TaskSpace, TaskTimelineData, TaskTimelineMany, TimelineBar, TimelineSprint, TimelineVersion } from "@/lib/types";

import { TypeIcon, addDays, errorText, refreshTasks, today, useSprints } from "./taskParts";

/*
 * Timeline by epic (docs/specs/31 §21.3): weeks across, epics down (expandable to their Story /
 * Task / Bug), sprint bands and version milestones on top, thin arrows for "blocks". Not a Gantt:
 * the only edits are the due date (drag the bar's right edge) and the sprint (drag a child bar onto
 * a sprint band), each also reachable from the keyboard through the bar's "Đổi lịch" dialog.
 */

const DAY = 18;
const ROW = 34;
const SHIFT_WEEKS = 4;
const WEEKS = 12;
const DRAG_TYPE = "text/x-task-key";

function dayNum(day: string): number {
  return Math.round(Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10))) / 86_400_000);
}

function short(day: string): string {
  return `${day.slice(8, 10)}/${day.slice(5, 7)}`;
}

/** ISO week number of a day. */
function weekNo(day: string): number {
  const d = new Date(`${day}T00:00:00Z`);
  const dow = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dow + 3);
  const firstThursday = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  return 1 + Math.round(((d.getTime() - firstThursday.getTime()) / 86_400_000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7);
}

function mondayOf(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  return addDays(day, -((d.getUTCDay() + 6) % 7));
}

/** The default window: from the Monday two weeks back, twelve weeks; `shift` moves it by 4 weeks. */
function windowOf(shift: number): { from: string; to: string } {
  const from = addDays(mondayOf(today()), -14 + shift * SHIFT_WEEKS * 7);
  return { from, to: addDays(from, WEEKS * 7 - 1) };
}

interface Row {
  id: string;
  kind: "group" | "epic" | "child";
  label: ReactNode;
  bar?: TimelineBar;
  milestones?: TimelineVersion[];
}

interface Edits {
  /** New due date for a bar (drag of its right edge). */
  onDue: (bar: TimelineBar, due: string) => void;
  /** Drop of a bar on a sprint band. */
  onSprint: (key: string, sprintId: number) => void;
}

/** The chart shared by one dự án (editable) and several (read-only). */
function Chart({
  from,
  to,
  todayDay,
  sprints,
  versions,
  rows,
  links,
  onOpen,
  edits,
  label,
}: {
  from: string;
  to: string;
  todayDay: string;
  sprints?: TimelineSprint[];
  versions?: TimelineVersion[];
  rows: Row[];
  links?: { from: string; to: string; late: boolean }[];
  onOpen?: (key: string) => void;
  edits?: Edits;
  label: string;
}) {
  const base = dayNum(from);
  const days = dayNum(to) - base + 1;
  const width = days * DAY;
  const x = (day: string) => (dayNum(day) - base) * DAY;
  const todayX = x(todayDay);
  const [drag, setDrag] = useState<string | null>(null);
  const [band, setBand] = useState<number | null>(null);
  const [resize, setResize] = useState<{ key: string; startX: number; delta: number } | null>(null);
  const justResized = useRef(false);

  const weeks = useMemo(() => {
    const out: string[] = [];
    for (let d = mondayOf(from); dayNum(d) <= dayNum(to); d = addDays(d, 7)) out.push(d);
    return out;
  }, [from, to]);

  /** Left / width of a bar inside the window, or null when it is all outside. */
  function geom(b: TimelineBar, extra = 0) {
    const s = dayNum(b.start) - base;
    const endDay = b.end ?? (b.start > todayDay ? b.start : todayDay);
    const e = Math.max(s, dayNum(endDay) - base + extra);
    if (e < 0 || s >= days) return null;
    const l = Math.max(0, s);
    const r = Math.min(days - 1, e);
    return { left: l * DAY, width: (r - l + 1) * DAY, cutL: s < 0, cutR: e >= days };
  }

  const rowOf = new Map<string, number>();
  rows.forEach((r, i) => {
    if (r.bar) rowOf.set(r.bar.key, i);
  });

  function startResize(e: PointerEvent<HTMLSpanElement>, b: TimelineBar) {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setResize({ key: b.key, startX: e.clientX, delta: 0 });
  }
  function moveResize(e: PointerEvent<HTMLSpanElement>) {
    if (!resize) return;
    const delta = Math.round((e.clientX - resize.startX) / DAY);
    if (delta !== resize.delta) setResize({ ...resize, delta });
  }
  function endResize(b: TimelineBar) {
    if (!resize) return;
    const { delta } = resize;
    setResize(null);
    justResized.current = true;
    window.setTimeout(() => (justResized.current = false), 0);
    if (!delta || !edits) return;
    const end = b.end ?? (b.start > todayDay ? b.start : todayDay);
    let due = addDays(end, delta);
    if (due < b.start) due = b.start;
    if (due !== b.end) edits.onDue(b, due);
  }

  const bandProps = (s: TimelineSprint) =>
    edits
      ? {
          onDragOver: (e: DragEvent<HTMLElement>) => {
            if (!drag) return;
            e.preventDefault();
            if (band !== s.id) setBand(s.id);
          },
          onDragLeave: () => setBand(null),
          onDrop: (e: DragEvent<HTMLElement>) => {
            e.preventDefault();
            const key = drag ?? e.dataTransfer.getData(DRAG_TYPE);
            setDrag(null);
            setBand(null);
            if (key) edits.onSprint(key, s.id);
          },
        }
      : {};

  function bar(b: TimelineBar, kind: Row["kind"]) {
    const extra = resize?.key === b.key ? resize.delta : 0;
    const g = geom(b, extra);
    if (!g) return null;
    const pct = Math.round(b.progress * 100);
    const movable = !!edits && b.can_edit && kind === "child";
    const sizable = !!edits && b.can_edit;
    const overdue =
      b.overdue_from && !extra
        ? (() => {
            const s = Math.max(0, dayNum(b.overdue_from) - base + 1);
            const e = Math.min(days - 1, dayNum(todayDay) - base);
            return e >= s ? { left: s * DAY, width: (e - s + 1) * DAY } : null;
          })()
        : null;
    const range = `${dayLabel(b.start)} – ${b.end ? dayLabel(b.end) : "chưa có hạn"}`;
    return (
      <>
        {/* A div, not a button: Firefox does not drag buttons. */}
        <div
          className={`rm-bar rm-bar-${kind}${b.done ? " done" : ""}${b.end ? "" : " open-end"}${g.cutL ? " cut-l" : ""}${g.cutR ? " cut-r" : ""}${drag === b.key ? " dragging" : ""}`}
          style={{ left: g.left, width: g.width }}
          aria-label={`${b.key} ${b.title}: ${range}, ${pct}% xong${b.overdue ? ", quá hạn" : ""}${b.blocked_late ? ", bị chặn quá hạn" : ""}`}
          title={`${b.key} · ${b.title}\n${range} · ${pct}% xong${b.assignee ? ` · ${b.assignee}` : ""}${b.blockers.length ? `\nBị chặn bởi ${b.blockers.join(", ")}` : ""}`}
          draggable={movable}
          onDragStart={(e) => {
            if ((e.target as HTMLElement).closest(".rm-handle")) {
              e.preventDefault();
              return;
            }
            e.dataTransfer.setData(DRAG_TYPE, b.key);
            e.dataTransfer.effectAllowed = "move";
            setDrag(b.key);
          }}
          onDragEnd={() => {
            setDrag(null);
            setBand(null);
          }}
          onClick={() => {
            if (!justResized.current) onOpen?.(b.key);
          }}
          onKeyDown={(e) => {
            if (onOpen && (e.key === "Enter" || e.key === " ")) {
              e.preventDefault();
              onOpen(b.key);
            }
          }}
          role={onOpen ? "button" : "img"}
          tabIndex={onOpen ? 0 : -1}
        >
          <span className="rm-bar-done" style={{ width: `${pct}%` }} />
          {kind !== "child" && g.width > 40 && <span className="rm-bar-text">{pct}%</span>}
          {sizable && (
            <span
              className="rm-handle"
              aria-hidden="true"
              onPointerDown={(e) => startResize(e, b)}
              onPointerMove={moveResize}
              onPointerUp={() => endResize(b)}
              onPointerCancel={() => setResize(null)}
              onClick={(e) => e.stopPropagation()}
            />
          )}
        </div>
        {overdue &&<span className="rm-overdue" style={overdue} aria-hidden="true" />}
        {resize?.key === b.key && (
          <span className="rm-resize-tip" style={{ left: g.left + g.width + 6 }}>
            Hạn {short(addDays(b.end ?? todayDay, resize.delta))}
          </span>
        )}
      </>
    );
  }

  const inWindow = (d: string | null) => d !== null && d >= from && d <= to;

  return (
    <div className="rm-chart" role="region" aria-label={label} tabIndex={0}>
      <div className="rm-grid" style={{ width: width + 280 }}>
        <div className="rm-row rm-head">
          <div className="rm-label small muted">{label}</div>
          <div className="rm-track" style={{ width }}>
            {weeks.map((w) => (
              <span key={w} className="rm-week" style={{ left: Math.max(0, x(w)), width: 7 * DAY }}>
                <strong>Tuần {weekNo(w)}</strong> <span className="faint">{short(w)}</span>
              </span>
            ))}
          </div>
        </div>
        {versions && (
          <div className="rm-row rm-head rm-head-thin">
            <div className="rm-label small muted">Phiên bản</div>
            <div className="rm-track" style={{ width }}>
              {versions.filter((v) => inWindow(v.date)).map((v) => (
                <span key={v.id} className={`rm-milestone${v.released ? " released" : ""}`} style={{ left: x(v.date as string) + DAY / 2 }} title={`${v.name} · ${dayLabel(v.date as string)}${v.released ? " · đã phát hành" : ""}`}>
                  <span aria-hidden="true">◆</span> {v.name} <span className="faint">({short(v.date as string)})</span>
                </span>
              ))}
              {!versions.length && <span className="small faint rm-none">Không có mốc trong khoảng này</span>}
            </div>
          </div>
        )}
        {sprints && (
          <div className="rm-row rm-head rm-head-thin">
            <div className="rm-label small muted">Sprint{edits ? <span className="faint"> · kéo task lên dải</span> : null}</div>
            <div className="rm-track" style={{ width }}>
              {sprints.map((s) => {
                const l = Math.max(0, x(s.start));
                const r = Math.min(width, x(s.end) + DAY);
                if (r <= l) return null;
                return (
                  <span
                    key={s.id}
                    className={`rm-band rm-band-${s.state}${band === s.id ? " over" : ""}`}
                    style={{ left: l, width: r - l }}
                    title={`${s.name}: ${dayLabel(s.start)} – ${dayLabel(s.end)}`}
                    {...bandProps(s)}
                  >
                    {s.name}
                  </span>
                );
              })}
              {!sprints.length && <span className="small faint rm-none">Không có sprint có ngày trong khoảng này</span>}
            </div>
          </div>
        )}
        <div className="rm-body" style={{ height: rows.length * ROW }}>
          {rows.map((r) => (
            <div key={r.id} className={`rm-row rm-row-${r.kind}`} style={{ height: ROW }}>
              <div className="rm-label">{r.label}</div>
              <div className="rm-track" style={{ width }}>
                {r.bar && bar(r.bar, r.kind)}
                {r.milestones
                  ?.filter((v) => inWindow(v.date))
                  .map((v) => (
                    <span key={v.id} className={`rm-milestone${v.released ? " released" : ""}`} style={{ left: x(v.date as string) + DAY / 2 }} title={`${v.name} · ${dayLabel(v.date as string)}`}>
                      <span aria-hidden="true">◆</span> {v.name}
                    </span>
                  ))}
              </div>
            </div>
          ))}
          {todayX >= 0 && todayX < width && <span className="rm-today" style={{ left: 280 + todayX + DAY / 2 }} title={`Hôm nay ${dayLabel(todayDay)}`} aria-hidden="true" />}
          {links && links.length > 0 && (
            <svg className="rm-links" width={width} height={rows.length * ROW} style={{ left: 280 }} aria-hidden="true">
              <defs>
                <marker id="rm-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L6,3 L0,6 z" className="rm-arrow-head" />
                </marker>
                <marker id="rm-arrow-late" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L6,3 L0,6 z" className="rm-arrow-head late" />
                </marker>
              </defs>
              {links.map((l) => {
                const fi = rowOf.get(l.from);
                const ti = rowOf.get(l.to);
                if (fi === undefined || ti === undefined) return null;
                const fb = rows[fi].bar as TimelineBar;
                const tb = rows[ti].bar as TimelineBar;
                const x1 = (dayNum(fb.end ?? todayDay) - base + 1) * DAY;
                const x2 = (dayNum(tb.start) - base) * DAY;
                const y1 = fi * ROW + ROW / 2;
                const y2 = ti * ROW + ROW / 2;
                return (
                  <path
                    key={`${l.from}-${l.to}`}
                    d={`M${x1},${y1} C${x1 + 24},${y1} ${x2 - 24},${y2} ${x2},${y2}`}
                    className={`rm-link${l.late ? " late" : ""}`}
                    markerEnd={`url(#${l.late ? "rm-arrow-late" : "rm-arrow"})`}
                  />
                );
              })}
            </svg>
          )}
        </div>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <span className="rm-legend small muted" aria-hidden="true">
      <span className="rm-sw rm-sw-done" /> đã xong <span className="rm-sw rm-sw-left" /> còn lại <span className="rm-sw rm-sw-late" /> quá hạn <span className="rm-sw-ms">◆</span> phiên bản
    </span>
  );
}

function WindowNav({ shift, setShift, from, to }: { shift: number; setShift: (n: number) => void; from: string; to: string }) {
  return (
    <div className="rm-nav">
      <IconButton icon="chevronLeft" label={`Lùi ${SHIFT_WEEKS} tuần`} onClick={() => setShift(shift - 1)} />
      <button type="button" className="ghost sm" onClick={() => setShift(0)} disabled={shift === 0}>
        Hôm nay
      </button>
      <IconButton icon="chevronRight" label={`Tới ${SHIFT_WEEKS} tuần`} onClick={() => setShift(shift + 1)} />
      <span className="small muted">
        {dayLabel(from)} – {dayLabel(to)}
      </span>
    </div>
  );
}

/** Timeline tab of one dự án. */
export function TaskTimeline({ space, onOpen }: { space: TaskSpace; onOpen: (key: string) => void }) {
  const bp = space.business_project_id;
  const [shift, setShift] = useState(0);
  const win = windowOf(shift);
  const data = useApi<TaskTimelineData>(`/tasks/spaces/${bp}/timeline?from=${win.from}&to=${win.to}`);
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<TimelineBar | null>(null);

  const t = data.data;
  // First load: open the epics that hold a warning.
  const open = useMemo(() => {
    if (expanded) return expanded;
    const late = new Set((t?.warnings ?? []).map((w) => w.key));
    return new Set((t?.epics ?? []).filter((e) => e.children?.some((c) => late.has(c.key))).map((e) => e.key));
  }, [expanded, t]);
  const toggle = (key: string) => {
    const next = new Set(open);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setExpanded(next);
  };

  async function run(work: () => Promise<unknown>, done: string) {
    setMessage(null);
    try {
      await work();
      setMessage(done);
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    await refreshTasks();
  }
  const edits: Edits = {
    onDue: (b, due) => void run(() => api.patch(`/tasks/${encodeURIComponent(b.key)}`, { due_date: due }), `${b.key}: hạn mới ${dayLabel(due)}`),
    onSprint: (key, sprintId) => {
      const all = [...(t?.epics.flatMap((e) => e.children ?? []) ?? []), ...(t?.loose ?? [])];
      const b = all.find((x) => x.key === key);
      if (!b || b.sprint_id === sprintId) return;
      const name = t?.sprints.find((s) => s.id === sprintId)?.name ?? "sprint";
      void run(() => api.post(`/tasks/${encodeURIComponent(key)}/move`, { sprint_id: sprintId }), `${key}: chuyển sang ${name}`);
    },
  };

  const rows: Row[] = [];
  const labelOf = (b: TimelineBar, kind: Row["kind"]) => (
    <>
      {kind === "epic" ? (
        <IconButton
          icon={open.has(b.key) ? "chevronDown" : "chevronRight"}
          label={`${open.has(b.key) ? "Thu" : "Mở"} ${b.key} (${b.children_count ?? 0} task)`}
          aria-expanded={open.has(b.key)}
          onClick={() => toggle(b.key)}
          size={14}
          disabled={!b.children_count}
        />
      ) : (
        <span className="rm-indent" />
      )}
      <TypeIcon type={b.type} />
      <button type="button" className="link sm rm-key" onClick={() => onOpen(b.key)}>
        {b.key}
      </button>
      <span className="rm-title" title={b.title}>
        {b.title}
      </span>
      {kind === "epic" && <span className="small faint">{b.done_count ?? 0}/{b.children_count ?? 0}</span>}
      {b.blocked_late && (
        <span className="rm-warn" title={`Bị chặn quá hạn bởi ${b.blockers.join(", ")}`}>
          <Icon name="alert" size={14} />
          <span className="visually-hidden">Bị chặn quá hạn</span>
        </span>
      )}
      {b.can_edit && <IconButton icon="calendar" label={`Đổi lịch ${b.key}`} size={14} className="rm-menu" onClick={() => setEditing(b)} />}
    </>
  );
  for (const e of t?.epics ?? []) {
    rows.push({ id: e.key, kind: "epic", bar: e, label: labelOf(e, "epic") });
    if (open.has(e.key)) for (const c of e.children ?? []) rows.push({ id: c.key, kind: "child", bar: c, label: labelOf(c, "child") });
  }
  if (t?.loose.length) {
    const key = "__loose";
    rows.push({
      id: key,
      kind: "group",
      label: (
        <>
          <IconButton icon={open.has(key) ? "chevronDown" : "chevronRight"} label={`${open.has(key) ? "Thu" : "Mở"} task không thuộc epic`} aria-expanded={open.has(key)} onClick={() => toggle(key)} size={14} />
          <span className="rm-title">Không thuộc epic</span>
          <span className="small faint">{t.loose.length}</span>
        </>
      ),
    });
    if (open.has(key)) for (const c of t.loose) rows.push({ id: c.key, kind: "child", bar: c, label: labelOf(c, "child") });
  }

  return (
    <div className="rm-wrap">
      <div className="toolbar rm-toolbar">
        <WindowNav shift={shift} setShift={setShift} from={win.from} to={win.to} />
        <span className="spacer" />
        <Legend />
      </div>
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {t && t.warnings.length > 0 && (
        <Alert tone="warning">
          <strong>Bị chặn quá hạn</strong>
          <ul className="rm-warnings">
            {t.warnings.map((w) => (
              <li key={`${w.blocker}-${w.key}`}>{w.text}</li>
            ))}
          </ul>
        </Alert>
      )}
      {data.error && !t ? (
        <div className="th-error">
          <Loading error={data.error} />
          <button type="button" className="ghost sm" onClick={() => void data.mutate()}>
            Thử lại
          </button>
        </div>
      ) : !t ? (
        <Loading rows={6} />
      ) : !rows.length ? (
        <EmptyState icon="calendar" title="Chưa có epic hay task có lịch">
          Tạo task loại Epic, đặt nó làm task cha của Story / Task / Bug, rồi đặt ngày hạn hoặc đưa vào sprint.
        </EmptyState>
      ) : (
        <Chart
          from={t.from}
          to={t.to}
          todayDay={t.today}
          sprints={t.sprints}
          versions={t.versions}
          rows={rows}
          links={t.links}
          onOpen={onOpen}
          edits={edits}
          label="Epic"
        />
      )}
      {editing && <ScheduleModal bp={bp} bar={editing} onClose={() => setEditing(null)} onSaved={(text) => setMessage(text)} />}
    </div>
  );
}

/** The keyboard way to what the drags do: the due date and (Story / Task / Bug) the sprint. */
function ScheduleModal({ bp, bar, onClose, onSaved }: { bp: number; bar: TimelineBar; onClose: () => void; onSaved: (text: string) => void }) {
  const sprints = useSprints(bar.type === "epic" ? null : bp);
  const [due, setDue] = useState(bar.due_date ?? "");
  const [sprint, setSprint] = useState(bar.sprint_id ? String(bar.sprint_id) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = encodeURIComponent(bar.key);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      if ((due || null) !== bar.due_date) await api.patch(`/tasks/${key}`, { due_date: due || null });
      const next = sprint ? Number(sprint) : null;
      if (bar.type !== "epic" && next !== bar.sprint_id) await api.post(`/tasks/${key}/move`, { sprint_id: next });
      await refreshTasks();
      onSaved(`${bar.key}: đã lưu lịch`);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`Lịch của ${bar.key}`}
      onClose={onClose}
      busy={busy}
      width={440}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy}>
            Lưu
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <p className="small muted">{bar.title}</p>
        <label className="rq-field">
          <span className="rq-label">Ngày hạn</span>
          <input type="date" value={due} onChange={(e) => setDue(e.target.value)} autoFocus />
        </label>
        {bar.type !== "epic" && (
          <label className="rq-field">
            <span className="rq-label">Sprint</span>
            <select value={sprint} onChange={(e) => setSprint(e.target.value)} disabled={!sprints.data}>
              <option value="">Backlog (không thuộc sprint)</option>
              {(sprints.data ?? [])
                .filter((s) => s.state !== "closed" || s.id === bar.sprint_id)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                    {s.state === "active" ? " · đang chạy" : ""}
                  </option>
                ))}
            </select>
          </label>
        )}
      </div>
    </Modal>
  );
}

/** Manager view of /tasks/overview: epics of several dự án, read-only. */
export function TimelineMany({ spaces }: { spaces: number[] }) {
  const [shift, setShift] = useState(0);
  const win = windowOf(shift);
  const query = new URLSearchParams({ from: win.from, to: win.to });
  for (const s of spaces) query.append("space", String(s));
  const data = useApi<TaskTimelineMany>(`/tasks/timeline?${query}`);
  const t = data.data;
  const rows: Row[] = [];
  for (const s of t?.spaces ?? []) {
    rows.push({
      id: `bp-${s.business_project_id}`,
      kind: "group",
      milestones: s.versions,
      label: (
        <Link className="rm-title rm-space" href={`/tasks?space=${s.business_project_id}&tab=timeline`}>
          {s.name} <span className="mono faint">{s.key}</span>
        </Link>
      ),
    });
    for (const e of s.epics)
      rows.push({
        id: `${s.business_project_id}-${e.key}`,
        kind: "epic",
        bar: e,
        label: (
          <>
            <span className="rm-indent" />
            <TypeIcon type="epic" />
            <Link className="rm-key mono small" href={`/tasks?space=${s.business_project_id}&tab=timeline&task=${e.key}`}>
              {e.key}
            </Link>
            <span className="rm-title" title={e.title}>
              {e.title}
            </span>
            <span className="small faint">
              {e.done_count ?? 0}/{e.children_count ?? 0}
            </span>
          </>
        ),
      });
    if (!s.epics.length)
      rows.push({ id: `bp-${s.business_project_id}-none`, kind: "child", label: <span className="small faint rm-indent-text">Chưa có epic</span> });
  }
  return (
    <div className="rm-wrap">
      <div className="toolbar rm-toolbar">
        <WindowNav shift={shift} setShift={setShift} from={win.from} to={win.to} />
        <span className="spacer" />
        <Legend />
      </div>
      {data.error && !t ? (
        <div className="th-error">
          <Loading error={data.error} />
          <button type="button" className="ghost sm" onClick={() => void data.mutate()}>
            Thử lại
          </button>
        </div>
      ) : !t ? (
        <Loading rows={6} />
      ) : !t.spaces.length ? (
        <EmptyState icon="calendar" title="Không có dự án nào để xem" />
      ) : (
        <Chart from={t.from} to={t.to} todayDay={t.today} rows={rows} label="Dự án / Epic" />
      )}
    </div>
  );
}
