"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Markdown } from "@/components/Markdown";
import { MoreMenu } from "@/components/MoreMenu";
import { Drawer, EmptyState, Loading, Message, Pill, Segmented, copyText } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { TaskSpace, TaskVersion } from "@/lib/types";

import { errorText, refreshTasks, useConfirm, useVersions } from "./taskParts";
import { ReleaseModal } from "./TaskQuality";

/*
 * Phiên bản (§13, layout §22.7): a one-row toolbar (search, status chips, "+ Phiên bản"), a compact
 * table whose "Sẵn sàng" column is a progress bar of the release checks plus a chip that opens a
 * side panel listing what is missing; release notes, edit and delete sit in a ⋯ menu, and the
 * "Phát hành" button shows only once the version is ready (forcing stays in the menu, §19.4).
 */

/** GET /tasks/versions/{id}/readiness (same shape as TaskQuality's ReleaseModal reads). */
interface Readiness {
  ready: boolean;
  tasks: number;
  checks: { key: string; label: string; ok: boolean; items: string[]; count: number }[];
}
const useReadiness = (id: number | null) => useApi<Readiness>(id ? `/tasks/versions/${id}/readiness` : null);

type StatusFilter = "open" | "released" | "all";

export function TaskVersions({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const versions = useVersions(bp);
  const [edit, setEdit] = useState<TaskVersion | "new" | null>(null);
  const [notes, setNotes] = useState<TaskVersion | null>(null);
  const [gaps, setGaps] = useState<TaskVersion | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("open");
  const [confirmEl, ask] = useConfirm();
  const canManage = space.can_manage;

  // Releasing goes through the readiness check (docs/specs/31 §19.4).
  const [releasing, setReleasing] = useState<TaskVersion | null>(null);

  async function remove(v: TaskVersion) {
    if (!(await ask({ title: `Xoá phiên bản “${v.name}”?`, body: <p>Task gắn phiên bản này sẽ bỏ phiên bản.</p>, ok: "Xoá", danger: true }))) return;
    try {
      await api.delete(`/tasks/versions/${v.id}`);
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  const all = versions.data ?? [];
  const counts = { open: all.filter((v) => !v.released_at).length, released: all.filter((v) => v.released_at).length, all: all.length };
  const needle = q.trim().toLowerCase();
  const rows = all.filter(
    (v) =>
      (status === "all" || (status === "open" ? !v.released_at : !!v.released_at)) &&
      (!needle || v.name.toLowerCase().includes(needle) || (v.description ?? "").toLowerCase().includes(needle)),
  );

  return (
    <div className="vs">
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

      <div className="vs-toolbar">
        <label className="vs-search">
          <Icon name="search" size={14} />
          <span className="visually-hidden">Tìm phiên bản</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm phiên bản…" />
        </label>
        <Segmented<StatusFilter>
          label="Trạng thái phiên bản"
          value={status}
          onChange={setStatus}
          options={[
            { value: "open", label: `Chưa phát hành · ${counts.open}` },
            { value: "released", label: `Đã phát hành · ${counts.released}` },
            { value: "all", label: `Tất cả · ${counts.all}` },
          ]}
        />
        <span className="vs-spacer" />
        {canManage && (
          <button type="button" className="primary sm" onClick={() => setEdit("new")}>
            <Icon name="plus" size={14} /> Phiên bản
          </button>
        )}
      </div>

      <section className="card flush vs-card">
        {!versions.data ? (
          <Loading error={versions.error} rows={4} />
        ) : !all.length ? (
          <p className="vs-none muted">
            <Icon name="flag" size={14} /> Chưa có phiên bản nào
            {canManage ? " · tạo phiên bản để gom task theo đợt phát hành và sinh release notes" : ""}
          </p>
        ) : !rows.length ? (
          <p className="vs-none muted">
            Không có phiên bản khớp ·{" "}
            <button type="button" className="ghost sm" onClick={() => (setQ(""), setStatus("all"))}>
              Xoá lọc
            </button>
          </p>
        ) : (
          <div className="table-wrap vs-scroll">
            <table className="vs-table">
              <thead>
                <tr>
                  <th>Phiên bản</th>
                  <th>Trạng thái</th>
                  <th className="vs-ready-col">Sẵn sàng</th>
                  <th>Ngày dự kiến</th>
                  <th className="num">Task xong</th>
                  <th className="tk-actions-col" aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {rows.map((v) => (
                  <VersionRow
                    key={v.id}
                    version={v}
                    canManage={canManage}
                    onNotes={() => setNotes(v)}
                    onRelease={() => setReleasing(v)}
                    onEdit={() => setEdit(v)}
                    onRemove={() => void remove(v)}
                    onGaps={() => setGaps(v)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {edit && <VersionModal bp={bp} version={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {notes && <NotesModal version={notes} onClose={() => setNotes(null)} />}
      {gaps && (
        <GapsPanel
          version={gaps}
          canManage={canManage}
          onClose={() => setGaps(null)}
          onRelease={() => {
            setReleasing(gaps);
            setGaps(null);
          }}
        />
      )}
    </div>
  );
}

function VersionRow({
  version: v,
  canManage,
  onNotes,
  onRelease,
  onEdit,
  onRemove,
  onGaps,
}: {
  version: TaskVersion;
  canManage: boolean;
  onNotes: () => void;
  onRelease: () => void;
  onEdit: () => void;
  onRemove: () => void;
  onGaps: () => void;
}) {
  const released = !!v.released_at;
  const got = useReadiness(released ? null : v.id);
  const r = got.data;
  const passed = r ? r.checks.filter((c) => c.ok).length : 0;
  const total = r ? r.checks.length : 0;
  const missing = total - passed;
  const pct = total ? Math.round((passed / total) * 100) : 0;
  const menu = [
    { icon: "file" as const, label: "Release notes", run: onNotes },
    ...(canManage && !released && !r?.ready ? [{ icon: "checkCircle" as const, label: r ? "Vẫn phát hành…" : "Phát hành…", run: onRelease }] : []),
    ...(canManage ? [{ icon: "edit" as const, label: "Sửa", run: onEdit }] : []),
    ...(canManage ? [{ icon: "remove" as const, label: "Xoá", danger: true, run: onRemove }] : []),
  ];
  return (
    <tr>
      <td>
        <strong>{v.name}</strong>
        {v.description && (
          <div className="small muted vs-desc" title={v.description}>
            {v.description}
          </div>
        )}
      </td>
      <td>{released ? <Pill tone="good">Đã phát hành {date(v.released_at as string)}</Pill> : <Pill>Chưa phát hành</Pill>}</td>
      <td className="vs-ready-col">
        {released ? (
          <span className="faint">—</span>
        ) : got.error ? (
          <span className="small error" title={got.error.message}>
            Không kiểm tra được
          </span>
        ) : !r ? (
          <span className="vs-bar vs-bar-loading" aria-label="Đang kiểm tra" />
        ) : (
          <span className="vs-ready">
            <span
              className={`vs-bar${r.ready ? " ok" : ""}`}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={total}
              aria-valuenow={passed}
              aria-label={`${passed}/${total} điều kiện phát hành đạt`}
            >
              <span style={{ width: `${pct}%` }} />
            </span>
            {r.ready ? (
              <Pill tone="good">Sẵn sàng</Pill>
            ) : (
              <button type="button" className="vs-gap-chip" onClick={onGaps} aria-haspopup="dialog">
                Chưa sẵn sàng ({missing}) <Icon name="chevronRight" size={12} />
              </button>
            )}
          </span>
        )}
      </td>
      <td className="nowrap">{v.release_date ? date(v.release_date) : "—"}</td>
      <td className="num">{v.tasks === undefined ? "—" : `${v.done ?? 0}/${v.tasks}`}</td>
      <td className="tk-actions-col">
        <span className="actions">
          {canManage && !released && r?.ready && (
            <button type="button" className="sm" onClick={onRelease}>
              Phát hành
            </button>
          )}
          <MoreMenu items={menu} />
        </span>
      </td>
    </tr>
  );
}

/** The side panel behind "Chưa sẵn sàng (n) ▸": each release check and what fails it. */
function GapsPanel({ version, canManage, onClose, onRelease }: { version: TaskVersion; canManage: boolean; onClose: () => void; onRelease: () => void }) {
  const got = useReadiness(version.id);
  const r = got.data;
  return (
    <Drawer
      title={`Sẵn sàng phát hành · ${version.name}`}
      onClose={onClose}
      width={440}
      actions={
        canManage && r ? (
          <button type="button" className={r.ready ? "sm primary ink" : "sm"} onClick={onRelease}>
            {r.ready ? "Phát hành" : "Vẫn phát hành…"}
          </button>
        ) : undefined
      }
    >
      {!r ? (
        <Loading error={got.error} rows={4} />
      ) : (
        <div className="vs-gaps">
          <p className="small">
            {r.ready ? <Pill tone="good">Sẵn sàng</Pill> : <Pill tone="bad">Chưa sẵn sàng</Pill>} {r.tasks} task trong phiên bản.
          </p>
          <ul className="vs-gap-list">
            {r.checks.map((c) => (
              <li key={c.key} className={c.ok ? "ok" : "miss"}>
                <Icon name={c.ok ? "checkCircle" : "alertCircle"} size={14} />
                <span className="vs-gap-body">
                  <span>
                    {c.label}
                    {!c.ok && <strong> · {c.count}</strong>}
                  </span>
                  {!c.ok && c.items.length > 0 && (
                    <span className="small muted">
                      {c.items.join(", ")}
                      {c.count > c.items.length ? "…" : ""}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Drawer>
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
