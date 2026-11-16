"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { IconButton } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { EmptyState, Loading, Message, Pill, copyText } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { TaskSpace, TaskVersion } from "@/lib/types";

import { errorText, refreshTasks, useConfirm, useVersions } from "./taskParts";
import { ReadinessPill, ReleaseModal } from "./TaskQuality";

/** Phiên bản (§13): versions of the dự án, release, release notes. */
export function TaskVersions({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const versions = useVersions(bp);
  const [edit, setEdit] = useState<TaskVersion | "new" | null>(null);
  const [notes, setNotes] = useState<TaskVersion | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const canManage = space.can_manage;

  // Releasing goes through the readiness check (docs/specs/31 §19.4).
  const [releasing, setReleasing] = useState<TaskVersion | null>(null);
  function release(v: TaskVersion) {
    setReleasing(v);
  }

  async function remove(v: TaskVersion) {
    if (!(await ask({ title: `Xoá phiên bản “${v.name}”?`, body: <p>Task gắn phiên bản này sẽ bỏ phiên bản.</p>, ok: "Xoá", danger: true }))) return;
    try {
      await api.delete(`/tasks/versions/${v.id}`);
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  return (
    <div className="tk-versions">
      {confirmEl}
      {releasing && (
        <ReleaseModal
          version={releasing}
          onClose={() => setReleasing(null)}
          onDone={(m) => {
            setReleasing(null);
            setMessage(m);
          }}
        />
      )}
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <section className="card flush">
        <div className="card-head tk-pad">
          <div className="card-title">
            <h2>Phiên bản</h2>
          </div>
          {canManage && (
            <div className="actions">
              <button type="button" className="sm" onClick={() => setEdit("new")}>
                Tạo phiên bản
              </button>
            </div>
          )}
        </div>
        {!versions.data ? (
          <Loading error={versions.error} rows={4} />
        ) : !versions.data.length ? (
          <EmptyState icon="flag" title="Chưa có phiên bản nào">
            {canManage ? "Tạo phiên bản để gom task theo đợt phát hành và sinh release notes." : undefined}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Phiên bản</th>
                  <th>Trạng thái</th>
                  <th>Ngày dự kiến</th>
                  <th className="num">Task</th>
                  <th className="tk-actions-col" aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {versions.data.map((v) => (
                  <tr key={v.id}>
                    <td>
                      <strong>{v.name}</strong>
                      {v.description && <div className="small muted">{v.description}</div>}
                    </td>
                    <td>
                      {v.released_at ? (
                        <Pill tone="good">Đã phát hành {date(v.released_at)}</Pill>
                      ) : (
                        <>
                          <Pill>Chưa phát hành</Pill> <ReadinessPill versionId={v.id} />
                        </>
                      )}
                    </td>
                    <td className="nowrap">{v.release_date ? date(v.release_date) : "—"}</td>
                    <td className="num">{v.tasks === undefined ? "—" : `${v.done ?? 0}/${v.tasks}`}</td>
                    <td className="tk-actions-col">
                      <span className="actions">
                        <IconButton icon="file" label="Release notes" onClick={() => setNotes(v)} />
                        {canManage && !v.released_at && <IconButton icon="checkCircle" label="Phát hành" onClick={() => void release(v)} />}
                        {canManage && <IconButton icon="edit" label="Sửa" onClick={() => setEdit(v)} />}
                        {canManage && <IconButton icon="remove" tone="danger" label="Xoá" onClick={() => void remove(v)} />}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {edit && <VersionModal bp={bp} version={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {notes && <NotesModal version={notes} onClose={() => setNotes(null)} />}
    </div>
  );
}

function VersionModal({ bp, version, onClose }: { bp: number; version: TaskVersion | null; onClose: () => void }) {
  const [name, setName] = useState(version?.name ?? "");
  const [description, setDescription] = useState(version?.description ?? "");
  const [releaseDate, setReleaseDate] = useState(version?.release_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!name.trim()) return;
    setBusy(true);
    const body = { name: name.trim(), description: description.trim() || null, release_date: releaseDate || null };
    try {
      if (version) await api.patch(`/tasks/versions/${version.id}`, body);
      else await api.post(`/tasks/spaces/${bp}/versions`, body);
      await refreshTasks();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={version ? `Sửa “${version.name}”` : "Tạo phiên bản"}
      onClose={onClose}
      busy={busy}
      width={480}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            {version ? "Lưu" : "Tạo phiên bản"}
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="rq-field">
          <span className="rq-label">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="v2.4.0" autoFocus />
        </label>
        <label className="rq-field">
          <span className="rq-label">Mô tả</span>
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="rq-field">
          <span className="rq-label">Ngày phát hành dự kiến</span>
          <input type="date" value={releaseDate} onChange={(e) => setReleaseDate(e.target.value)} />
        </label>
      </div>
    </Modal>
  );
}

function NotesModal({ version, onClose }: { version: TaskVersion; onClose: () => void }) {
  const notes = useApi<{ markdown: string }>(`/tasks/versions/${version.id}/notes`);
  const [copied, setCopied] = useState<string | null>(null);
  return (
    <Modal
      title={`Release notes · ${version.name}`}
      onClose={onClose}
      width={760}
      footer={
        <>
          {copied && <span className="small muted">{copied}</span>}
          <button
            type="button"
            className="primary ink"
            disabled={!notes.data?.markdown}
            onClick={() => void copyText(notes.data?.markdown ?? "").then((ok) => setCopied(ok ? "Đã chép markdown" : "Không chép được"))}
          >
            Chép markdown
          </button>
        </>
      }
    >
      {!notes.data ? (
        <Loading error={notes.error} rows={6} />
      ) : !notes.data.markdown.trim() ? (
        <EmptyState title="Chưa có task xong trong phiên bản này" />
      ) : (
        <div className="tk-notes">
          <Markdown text={notes.data.markdown} />
        </div>
      )}
    </Modal>
  );
}
