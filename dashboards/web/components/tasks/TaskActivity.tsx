"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Icon, IconButton, type IconName } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { Avatar, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date, dateTimeMin, relative } from "@/lib/format";
import type { TaskAttachment, TaskDetail, TaskPage, TaskTimelineItem, TaskTimelineType } from "@/lib/types";

import { FileTextarea, ImageViewer, MAX_FILE_MB, clipboardFiles, inlineUrl, isInlineImage, uploadTaskFile } from "./taskFiles";
import { VIA_LABEL, errorText, fileSize, fmtMinutes, parseDuration, personName, refreshTasks, today, useConfirm, useTaskPeople } from "./taskParts";

type Chip = "all" | "comment" | "code" | "test" | "history" | "worklog";

const CHIPS: { key: Chip; label: string; types: TaskTimelineType[] }[] = [
  { key: "all", label: "Tất cả", types: ["comment", "history", "worklog", "git", "review", "test", "deploy"] },
  { key: "comment", label: "Bình luận", types: ["comment"] },
  { key: "code", label: "Code", types: ["git", "review", "deploy"] },
  { key: "test", label: "Test", types: ["test"] },
  { key: "history", label: "Thay đổi", types: ["history"] },
  { key: "worklog", label: "Giờ làm", types: ["worklog"] },
];
const PAGE = 50;
const TYPE_ICON: Record<TaskTimelineType, IconName> = {
  comment: "edit",
  history: "history",
  worklog: "calendar",
  git: "code",
  review: "review",
  test: "check",
  deploy: "flag",
};
const TEST_TONE: Record<string, "good" | "bad" | "warn" | undefined> = { passed: "good", failed: "bad", blocked: "warn", retest: "warn" };

/**
 * One timeline of the task (docs/specs/31 §20.1): comments, field changes, code, reviews, tests,
 * deploy and worklogs, newest first, with filter chips. "Giờ làm" shows the worklog table and its
 * form instead. The comment box is `TaskCommentBox`, fixed at the bottom of the main column.
 */
export function TaskActivity({ task }: { task: TaskDetail }) {
  const [chip, setChip] = useState<Chip>("all");
  const path = `/tasks/${encodeURIComponent(task.key)}`;
  return (
    <section className="tk-section" id="tl-timeline" aria-labelledby="tl-timeline-title">
      <div className="tk-section-head">
        <h3 id="tl-timeline-title">
          <Icon name="activity" size={16} /> Dòng thời gian
        </h3>
      </div>
      <div className="tl-chips" role="group" aria-label="Lọc dòng thời gian">
        {CHIPS.map((c) => (
          <button key={c.key} type="button" className={`tl-chip${chip === c.key ? " on" : ""}`} aria-pressed={chip === c.key} onClick={() => setChip(c.key)}>
            {c.label}
            {c.key === "comment" && task.comments.length > 0 && <span className="tl-chip-count">{task.comments.length}</span>}
            {c.key === "worklog" && task.worklogs.length > 0 && <span className="tl-chip-count">{task.worklogs.length}</span>}
          </button>
        ))}
      </div>
      {chip === "worklog" ? <Worklogs task={task} path={path} /> : <Timeline key={chip} task={task} path={path} types={CHIPS.find((c) => c.key === chip)?.types ?? []} />}
    </section>
  );
}

function Timeline({ task, path, types }: { task: TaskDetail; path: string; types: TaskTimelineType[] }) {
  const query = `${path}/timeline?types=${types.join(",")}&limit=${PAGE}`;
  const first = useApi<TaskTimelineItem[]>(query, { keepPreviousData: true });
  const [older, setOlder] = useState<TaskTimelineItem[]>([]);
  const [olderDone, setOlderDone] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: number; body: string } | null>(null);
  const [editUploading, setEditUploading] = useState(0);
  const [busy, setBusy] = useState(false);
  const [confirmEl, ask] = useConfirm();

  const items = first.data ?? [];
  const all = [...items, ...older.filter((o) => !items.some((i) => i.id === o.id))];
  const hasMore = !olderDone && (older.length ? true : items.length >= PAGE);

  async function more() {
    const last = all[all.length - 1];
    if (!last) return;
    setLoading(true);
    try {
      const got = await api.get<TaskTimelineItem[]>(`${query}&before=${encodeURIComponent(last.at)}`);
      // The cursor is inclusive (items sharing the last time come again): keep only new ones.
      const seen = new Set(all.map((i) => i.id));
      const fresh = got.filter((i) => !seen.has(i.id));
      setOlder((cur) => [...cur, ...fresh]);
      if (got.length < PAGE || !fresh.length) setOlderDone(true);
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setLoading(false);
  }

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      await refreshTasks();
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!(await ask({ title: "Xoá bình luận?", body: <p>Không hoàn tác được.</p>, ok: "Xoá", danger: true }))) return;
    if (await run(() => api.delete(`${path}/comments/${id}`))) setOlder((cur) => cur.filter((o) => o.id !== `comment:${id}`));
  }

  if (!first.data) return <Loading error={first.error} rows={3} />;
  return (
    <div className="tl-list-wrap">
      {confirmEl}
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!all.length && <p className="small faint">Chưa có hoạt động nào{types.length === 1 && types[0] === "comment" ? " – hãy viết bình luận đầu tiên ở ô bên dưới" : ""}.</p>}
      <ol className="tl-list">
        {all.map((it) => {
          if (it.type === "comment") {
            const id = Number(it.data.id);
            const canEdit = !!it.data.can_edit;
            return (
              <li key={it.id} className="tl-item tl-comment">
                <Avatar name={it.actor?.full_name ?? null} email={it.actor?.primary_email} src={it.actor?.avatar_url} size="sm" />
                <div className="tk-comment-body">
                  <div className="tk-comment-head">
                    <strong>{personName(it.actor)}</strong>
                    <span className="small muted" title={dateTimeMin(it.at)}>
                      {relative(it.at)}
                      {it.data.edited_at ? " · đã sửa" : ""}
                    </span>
                    <span className="spacer" />
                    {canEdit && <IconButton icon="edit" label="Sửa" onClick={() => setEditing({ id, body: String(it.data.body ?? "") })} />}
                    {canEdit && <IconButton icon="remove" tone="danger" label="Xoá" onClick={() => void remove(id)} />}
                  </div>
                  {editing?.id === id ? (
                    <div className="tk-editor">
                      <FileTextarea
                        rows={3}
                        value={editing.body}
                        taskKey={task.key}
                        canAttach={task.can.attach}
                        onUploaded={() => void refreshTasks()}
                        onPendingChange={setEditUploading}
                        onChange={(next) => setEditing((cur) => (cur ? { id: cur.id, body: typeof next === "function" ? next(cur.body) : next } : cur))}
                        aria-label="Sửa bình luận"
                      />
                      <div className="adm-form-foot">
                        <button type="button" className="ghost sm" onClick={() => setEditing(null)}>
                          Huỷ
                        </button>
                        <button
                          type="button"
                          className="sm"
                          disabled={busy || editUploading > 0 || !editing.body.trim()}
                          onClick={() =>
                            void run(() => api.patch(`${path}/comments/${id}`, { body: editing.body.trim() })).then((ok) => {
                              if (!ok) return;
                              setOlder((cur) => cur.map((o) => (o.id === it.id ? { ...o, text: editing.body.trim(), data: { ...o.data, body: editing.body.trim() } } : o)));
                              setEditing(null);
                            })
                          }
                        >
                          Lưu
                        </button>
                      </div>
                    </div>
                  ) : (
                    <Markdown text={String(it.data.body ?? it.text)} />
                  )}
                </div>
              </li>
            );
          }
          return <EventRow key={it.id} item={it} />;
        })}
      </ol>
      {hasMore && all.length > 0 && (
        <button type="button" className="ghost sm tl-more" disabled={loading} onClick={() => void more()}>
          {loading ? "Đang tải…" : "Xem cũ hơn"}
        </button>
      )}
    </div>
  );
}

/** A line of the timeline that is not a comment. */
function EventRow({ item }: { item: TaskTimelineItem }) {
  const d = item.data;
  const via = typeof d.via === "string" && d.via !== "web" ? d.via : null;
  const tone =
    item.type === "test"
      ? TEST_TONE[String(d.status)]
      : item.type === "review"
        ? Number(d.critical) > 0
          ? "bad"
          : "good"
        : item.type === "git" && d.state === "closed"
          ? "warn"
          : item.type === "deploy"
            ? "good"
            : undefined;
  const external = !!item.link && /^https?:/.test(item.link);
  return (
    <li className={`tl-item tl-event tl-${item.type}${tone ? ` tl-tone-${tone}` : ""}`}>
      <span className="tl-dot" aria-hidden="true">
        <Icon name={TYPE_ICON[item.type] ?? "info"} size={14} />
      </span>
      <span className="tl-text">
        {item.actor ? <strong>{personName(item.actor)}</strong> : item.type === "history" ? <strong>Tự động</strong> : null} {item.text}
        {via && (
          <>
            {" "}
            <Pill tone="info">{VIA_LABEL[via] ?? via}</Pill>
          </>
        )}
        {item.link && (
          <>
            {" "}
            <a className="link small" href={item.link} target={external ? "_blank" : undefined} rel={external ? "noreferrer" : undefined}>
              Mở{external ? " ↗" : ""}
            </a>
          </>
        )}
      </span>
      <span className="small muted tl-at" title={dateTimeMin(item.at)}>
        {relative(item.at)}
      </span>
    </li>
  );
}

type Suggest = { kind: "@" | "#"; start: number; query: string };
type Option = { value: string; label: string; sub?: string };

/** The token being typed before the caret: `@tam` or `#CRM-1` / `#otp`. */
function tokenAt(text: string, caret: number): Suggest | null {
  const m = /(^|[\s(])([@#])([\p{L}\p{N}._-]{0,40})$/u.exec(text.slice(0, caret));
  if (!m) return null;
  return { kind: m[2] as "@" | "#", start: caret - m[3].length - 1, query: m[3] };
}

/**
 * The comment box (docs/specs/31 §20.1), fixed at the bottom of the task's main column: `@` names
 * a person of the dự án (inserts `@emailname`), `#` a task (inserts its key). Files paste or drop
 * as before.
 */
export function TaskCommentBox({ task, bp }: { task: TaskDetail; bp: number | null }) {
  const path = `/tasks/${encodeURIComponent(task.key)}`;
  const area = useRef<HTMLTextAreaElement>(null);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [closed, setClosed] = useState<number | null>(null);
  const people = useTaskPeople(task.can.comment ? bp : null);
  const token = tokenAt(body, caret);
  const open = token && closed !== token.start ? token : null;
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(open?.kind === "#" ? open.query : ""), 200);
    return () => clearTimeout(t);
  }, [open?.kind, open?.query]);
  const tasks = useApi<TaskPage>(open?.kind === "#" && bp ? `/tasks?q=${encodeURIComponent(debounced)}&space=${bp}&limit=8` : null, { keepPreviousData: true });

  const options: Option[] = useMemo(() => {
    if (!open) return [];
    const q = open.query.toLowerCase();
    if (open.kind === "@")
      return (people.data ?? [])
        .map((p) => ({ name: (p.primary_email ?? "").split("@")[0], p }))
        .filter(({ name, p }) => name && (name.toLowerCase().includes(q) || (p.full_name ?? "").toLowerCase().includes(q)))
        .slice(0, 8)
        .map(({ name, p }) => ({ value: `@${name}`, label: personName(p), sub: `@${name}` }));
    return (tasks.data?.items ?? []).filter((t) => t.key !== task.key).map((t) => ({ value: t.key, label: t.key, sub: t.title }));
  }, [open, people.data, tasks.data, task.key]);

  useEffect(() => setActive(0), [open?.kind, open?.query]);

  function pick(o: Option) {
    if (!open) return;
    const before = body.slice(0, open.start);
    const after = body.slice(caret);
    const insert = `${o.value} `;
    const next = `${before}${insert}${after.replace(/^\s/, "")}`;
    const at = before.length + insert.length;
    setBody(next);
    setCaret(at);
    requestAnimationFrame(() => {
      area.current?.focus();
      area.current?.setSelectionRange(at, at);
    });
  }

  async function send() {
    if (!body.trim() || uploading || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post(`${path}/comments`, { body: body.trim() });
      setBody("");
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  if (!task.can.comment) return null;
  const listId = `tl-suggest-${task.key}`;
  return (
    <form
      className="tl-composer"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="tl-composer-area">
        {open && options.length > 0 && (
          <ul className="tl-suggest" id={listId} role="listbox" aria-label={open.kind === "@" ? "Nhắc tên" : "Chèn mã task"}>
            {options.map((o, i) => (
              <li key={o.value} role="option" aria-selected={i === active}>
                <button
                  type="button"
                  className={i === active ? "on" : undefined}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(o);
                  }}
                >
                  <strong>{o.label}</strong>
                  {o.sub && <span className="small muted tk-ellipsis">{o.sub}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
        <FileTextarea
          ref={area}
          rows={2}
          value={body}
          taskKey={task.key}
          canAttach={task.can.attach}
          onUploaded={() => void refreshTasks()}
          onChange={setBody}
          onPendingChange={setUploading}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onKeyDown={(e) => {
            if (open && options.length) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => (a + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                pick(options[Math.min(active, options.length - 1)]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                setClosed(open.start);
                return;
              }
            }
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Viết bình luận… @ để nhắc tên, # để chèn mã task, Ctrl+Enter để gửi"
          aria-label="Bình luận mới"
          aria-autocomplete="list"
          aria-controls={open && options.length ? listId : undefined}
          data-shortcut="comment"
        />
      </div>
      <div className="tl-composer-foot">
        <button type="submit" className="sm" disabled={busy || uploading > 0 || !body.trim()}>
          Gửi bình luận
        </button>
      </div>
    </form>
  );
}

function Worklogs({ task, path }: { task: TaskDetail; path: string }) {
  const [time, setTime] = useState("");
  const [day, setDay] = useState(today());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const minutes = parseDuration(time);
  const total = task.worklogs.reduce((n, w) => n + w.minutes, 0);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      await refreshTasks();
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tk-worklogs">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!task.worklogs.length ? (
        <p className="small faint">Chưa ai ghi giờ.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Người</th>
                <th>Ngày</th>
                <th className="num">Thời gian</th>
                <th>Ghi chú</th>
                {(task.can.worklog || task.can.moderate) && <th className="tk-actions-col" aria-label="Thao tác" />}
              </tr>
            </thead>
            <tbody>
              {task.worklogs.map((w) => (
                <tr key={w.id}>
                  <td className="nowrap">{personName(w.user)}</td>
                  <td className="nowrap">{w.day ? date(w.day) : "—"}</td>
                  <td className="num">{fmtMinutes(w.minutes)}</td>
                  <td>
                    {w.note}
                    {w.via !== "web" && (
                      <>
                        {" "}
                        <Pill tone="info">{VIA_LABEL[w.via] ?? w.via}</Pill>
                      </>
                    )}
                  </td>
                  {(task.can.worklog || task.can.moderate) && (
                    <td className="tk-actions-col">
                      <IconButton icon="remove" tone="danger" label="Xoá" disabled={busy} onClick={() => void run(() => api.delete(`${path}/worklogs/${w.id}`))} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={2}>
                  <strong>Tổng</strong>
                </td>
                <td className="num">
                  <strong>{fmtMinutes(total)}</strong>
                </td>
                <td colSpan={task.can.worklog || task.can.moderate ? 2 : 1} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {task.can.worklog && (
        <form
          className="tk-worklog-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (minutes) void run(() => api.post(`${path}/worklogs`, { minutes, day, note: note.trim() || undefined })).then((ok) => ok && (setTime(""), setNote("")));
          }}
        >
          <label className="rq-field">
            <span className="rq-label">Thời gian</span>
            <input value={time} onChange={(e) => setTime(e.target.value)} placeholder="1h30m" aria-invalid={!!time && !minutes} />
          </label>
          <label className="rq-field">
            <span className="rq-label">Ngày</span>
            <input type="date" value={day} max={today()} onChange={(e) => setDay(e.target.value)} />
          </label>
          <label className="rq-field tk-grow">
            <span className="rq-label">Ghi chú</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
          </label>
          <button type="submit" className="sm" disabled={busy || !minutes}>
            Ghi giờ
          </button>
        </form>
      )}
    </div>
  );
}

/**
 * Files of a task (≤ 20 MB each, docs/specs/31 §17): pick from disk, drop onto the section,
 * or paste (Ctrl+V) a copied screenshot anywhere on the task outside a text field. Images show
 * as thumbnails and open full size; other files download.
 */
export function TaskAttachments({ task }: { task: TaskDetail }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(0);
  const [over, setOver] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [viewing, setViewing] = useState<TaskAttachment | null>(null);
  const [confirmEl, ask] = useConfirm();
  const canAttach = task.can.attach;
  const images = task.attachments.filter((a) => isInlineImage(a.content_type));
  const others = task.attachments.filter((a) => !isInlineImage(a.content_type));

  async function upload(files: File[]) {
    if (!files.length || !canAttach) return;
    setBusy((n) => n + files.length);
    setMessage(null);
    const failed: string[] = [];
    await Promise.all(
      files.map(async (f, i) => {
        try {
          await uploadTaskFile(task.key, f, i);
        } catch (e) {
          failed.push(`${f.name || "ảnh"}: ${errorText(e)}`);
        } finally {
          setBusy((n) => n - 1);
        }
      }),
    );
    await refreshTasks();
    if (failed.length) setMessage(`Lỗi: ${failed.join("; ")}`);
  }

  // A screenshot pasted on the task (not inside a text field or a dialog) becomes an attachment.
  const uploadRef = useRef(upload);
  uploadRef.current = upload;
  useEffect(() => {
    if (!canAttach) return;
    const onPaste = (e: ClipboardEvent) => {
      const t = e.target instanceof HTMLElement ? e.target : null;
      if (t?.closest("input, textarea, select, [contenteditable='true'], .modal-backdrop")) return;
      const files = clipboardFiles(e);
      if (!files.length) return;
      e.preventDefault();
      void uploadRef.current(files);
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [canAttach]);

  async function remove(a: TaskAttachment) {
    if (!(await ask({ title: `Xoá “${a.name}”?`, body: <p>Không hoàn tác được.</p>, ok: "Xoá", danger: true }))) return;
    try {
      await api.delete(`/tasks/attachments/${a.id}`);
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  return (
    <section
      className={`tk-section tk-files${over ? " is-over" : ""}`}
      aria-labelledby="tk-files-title"
      onDragOver={(e) => {
        if (!canAttach || ![...e.dataTransfer.types].includes("Files")) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
      }}
      onDrop={(e) => {
        setOver(false);
        if (!canAttach || !e.dataTransfer.files.length) return;
        e.preventDefault();
        void upload([...e.dataTransfer.files]);
      }}
    >
      {confirmEl}
      {viewing && <ImageViewer file={viewing} onClose={() => setViewing(null)} />}
      <div className="tk-section-head">
        <h3 id="tk-files-title">
          <Icon name="file" size={16} /> Đính kèm{task.attachments.length ? ` (${task.attachments.length})` : ""}
        </h3>
        {canAttach && (
          <span className="actions">
            <input
              ref={input}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                void upload([...(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
            <button type="button" className="sm ghost" disabled={busy > 0} onClick={() => input.current?.click()}>
              <Icon name="upload" size={14} /> {busy ? `Đang tải ${busy} file…` : "Tải file lên"}
            </button>
          </span>
        )}
      </div>
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {canAttach && (
        <p className="small muted tk-files-hint">
          Kéo thả file vào đây, hoặc chụp màn hình rồi dán (Ctrl+V) khi đang xem task. Mỗi file tối đa {MAX_FILE_MB} MB. Ảnh dán vào mô tả, bình luận cũng nằm ở đây.
        </p>
      )}
      {!task.attachments.length ? (
        <p className="small faint">Chưa có file.</p>
      ) : (
        <>
          {images.length > 0 && (
            <ul className="tk-thumbs">
              {images.map((a) => (
                <li key={a.id} className="tk-thumb">
                  <button type="button" className="tk-thumb-img" onClick={() => setViewing(a)} aria-label={`Xem ảnh ${a.name}`}>
                    <img src={inlineUrl(a.id)} alt={a.name} loading="lazy" />
                  </button>
                  <div className="tk-thumb-meta">
                    <span className="tk-ellipsis small" title={a.name}>
                      {a.name}
                    </span>
                    <span className="tk-thumb-actions">
                      <a href={a.url} className="icon-btn" download={a.name} aria-label={`Tải ${a.name}`} data-tip="Tải về">
                        <Icon name="download" size={14} />
                      </a>
                      {a.can_delete && <IconButton icon="remove" tone="danger" label="Xoá ảnh" onClick={() => void remove(a)} />}
                    </span>
                  </div>
                  <span className="tk-thumb-sub small muted">
                    {fileSize(a.size)} · {personName(a.uploaded_by)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {others.length > 0 && (
            <ul className="tk-git-list">
              {others.map((a) => (
                <li key={a.id}>
                  <Icon name="file" size={14} />
                  <a href={a.url} className="tk-ellipsis" download={a.name}>
                    {a.name}
                  </a>
                  <span className="small muted">
                    {fileSize(a.size)} · {personName(a.uploaded_by)} · {date(a.created_at)}
                  </span>
                  <span className="spacer" />
                  <a href={a.url} className="icon-btn" download={a.name} aria-label={`Tải ${a.name}`} data-tip="Tải về">
                    <Icon name="download" size={16} />
                  </a>
                  {a.can_delete && <IconButton icon="remove" tone="danger" label="Xoá file" onClick={() => void remove(a)} />}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
