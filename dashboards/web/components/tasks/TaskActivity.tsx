"use client";

import { useEffect, useRef, useState } from "react";

import { SubPanel, SubTabs } from "@/components/AdminTabs";
import { Icon, IconButton } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { Avatar, Message, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { date, dateTimeMin, relative } from "@/lib/format";
import type { TaskAttachment, TaskComment, TaskDetail } from "@/lib/types";

import { FileTextarea, ImageViewer, MAX_FILE_MB, clipboardFiles, inlineUrl, isInlineImage, uploadTaskFile } from "./taskFiles";
import { VIA_LABEL, errorText, fileSize, fmtMinutes, parseDuration, personName, refreshTasks, today, useConfirm } from "./taskParts";

type Tab = "comments" | "history" | "worklog";

/** Bình luận | Lịch sử | Giờ làm of a task. */
export function TaskActivity({ task }: { task: TaskDetail }) {
  const [tab, setTab] = useState<Tab>("comments");
  const path = `/tasks/${encodeURIComponent(task.key)}`;
  return (
    <section className="tk-section">
      <SubTabs<Tab>
        id="tk-activity"
        label="Hoạt động"
        variant="mini"
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "comments", label: "Bình luận", count: task.comments.length },
          { key: "history", label: "Lịch sử", count: task.history.length },
          { key: "worklog", label: "Giờ làm", count: task.worklogs.length },
        ]}
      />
      <SubPanel id="tk-activity" tab={tab}>
        {tab === "comments" ? <Comments task={task} path={path} /> : tab === "history" ? <History task={task} /> : <Worklogs task={task} path={path} />}
      </SubPanel>
    </section>
  );
}

function Comments({ task, path }: { task: TaskDetail; path: string }) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [editUploading, setEditUploading] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: number; body: string } | null>(null);
  const [confirmEl, ask] = useConfirm();

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

  async function remove(c: TaskComment) {
    if (!(await ask({ title: "Xoá bình luận?", body: <p>Không hoàn tác được.</p>, ok: "Xoá", danger: true }))) return;
    await run(() => api.delete(`${path}/comments/${c.id}`));
  }

  return (
    <div className="tk-comments">
      {confirmEl}
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!task.comments.length && <p className="small faint">Chưa có bình luận.</p>}
      <ul className="tk-comment-list">
        {task.comments.map((c) => (
          <li key={c.id} className="tk-comment">
            <Avatar name={c.author?.full_name ?? null} email={c.author?.primary_email} src={c.author?.avatar_url} size="sm" />
            <div className="tk-comment-body">
              <div className="tk-comment-head">
                <strong>{personName(c.author)}</strong>
                <span className="small muted" title={dateTimeMin(c.created_at)}>
                  {relative(c.created_at)}
                  {c.edited_at ? " · đã sửa" : ""}
                </span>
                <span className="spacer" />
                {c.can_edit && <IconButton icon="edit" label="Sửa" onClick={() => setEditing({ id: c.id, body: c.body })} />}
                {c.can_edit && <IconButton icon="remove" tone="danger" label="Xoá" onClick={() => void remove(c)} />}
              </div>
              {editing?.id === c.id ? (
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
                      onClick={() => void run(() => api.patch(`${path}/comments/${c.id}`, { body: editing.body.trim() })).then((ok) => ok && setEditing(null))}
                    >
                      Lưu
                    </button>
                  </div>
                </div>
              ) : (
                <Markdown text={c.body} />
              )}
            </div>
          </li>
        ))}
      </ul>
      {task.can.comment && (
        <form
          className="tk-editor"
          onSubmit={(e) => {
            e.preventDefault();
            if (body.trim() && !uploading) void run(() => api.post(`${path}/comments`, { body: body.trim() })).then((ok) => ok && setBody(""));
          }}
        >
          <FileTextarea
            rows={3}
            value={body}
            taskKey={task.key}
            canAttach={task.can.attach}
            onUploaded={() => void refreshTasks()}
            onChange={setBody}
            onPendingChange={setUploading}
            placeholder="Viết bình luận (markdown, @tên để nhắc)…"
            aria-label="Bình luận mới"
          />
          <div className="adm-form-foot">
            <button type="submit" className="sm" disabled={busy || uploading > 0 || !body.trim()}>
              Gửi bình luận
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function History({ task }: { task: TaskDetail }) {
  if (!task.history.length) return <p className="small faint">Chưa có thay đổi.</p>;
  return (
    <ul className="tk-history">
      {[...task.history]
        .sort((a, b) => b.at.localeCompare(a.at))
        .map((h, i) => (
          <li key={i}>
            <span className="small muted tk-history-at" title={dateTimeMin(h.at)}>
              {relative(h.at)}
            </span>
            <span className="tk-history-text">
              <strong>{h.user ? personName(h.user) : "Tự động"}</strong> đổi <em>{h.field}</em>
              {h.old !== null && h.old !== "" && (
                <>
                  {" "}
                  từ <span className="tk-history-val">{h.old}</span>
                </>
              )}{" "}
              thành <span className="tk-history-val">{h.new ?? "—"}</span>
            </span>
            {h.via !== "web" && <Pill tone="info">{VIA_LABEL[h.via] ?? h.via}</Pill>}
          </li>
        ))}
    </ul>
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
