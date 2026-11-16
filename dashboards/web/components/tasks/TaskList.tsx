"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { EmptyState, Loading, Message, Pagination, SortTh } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { date, relative } from "@/lib/format";
import type { Me, TaskFilter, TaskPage, TaskSpace } from "@/lib/types";

import {
  GitBadge,
  priorityList,
  RESOLUTION_LABEL,
  RESOLUTIONS,
  PersonAvatar,
  RowPriority,
  StatusPill,
  TASK_TYPES,
  TYPE_LABEL,
  TypeIcon,
  errorText,
  personName,
  refreshTasks,
  sortedStatuses,
  useConfirm,
  useSprints,
  useTaskPeople,
  useVersions,
} from "./taskParts";
import { DensityToggle } from "./DensityToggle";
import { SmartFilter } from "./SmartFilter";

/* Danh sách (§13): every field as a filter, saved filters, sortable columns, bulk changes, paging. */

// category and updated_from have no control of their own: only "Lọc nhanh" (§19.8) sets them.
const FILTER_KEYS = ["q", "status", "type", "assignee", "priority", "sprint", "version", "label", "due", "category", "updated_from", "resolution"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
type Filters = Record<FilterKey, string>;
const NO_FILTERS: Filters = { q: "", status: "", type: "", assignee: "", priority: "", sprint: "", version: "", label: "", due: "", category: "", updated_from: "", resolution: "" };

export function TaskList({ space, me, onOpen }: { space: TaskSpace; me: Me | undefined; onOpen: (key: string) => void }) {
  const bp = space.business_project_id;
  const statuses = useMemo(() => sortedStatuses(space), [space]);
  const people = useTaskPeople(bp);
  const sprints = useSprints(bp);
  const versions = useVersions(bp);
  const saved = useApi<TaskFilter[]>("/tasks/filters");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({ key: "updated", dir: "desc" });
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(50);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [savedId, setSavedId] = useState("");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [labelText, setLabelText] = useState("");
  const [confirmEl, ask] = useConfirm();

  // The search box waits for a pause in typing.
  useEffect(() => {
    const t = window.setTimeout(() => setFilters((f) => (f.q === q ? f : { ...f, q })), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => {
    setPage(1);
    setSelected(new Set());
  }, [filters, sort, size, bp]);

  const path = `/tasks${qs({ space: bp, ...filters, sort: sort.key, order: sort.dir, limit: size, offset: (page - 1) * size })}`;
  const list = useApi<TaskPage>(path, { keepPreviousData: true });
  const rows = list.data?.items ?? [];
  const filtersOn = FILTER_KEYS.some((k) => filters[k]);
  const set = (k: FilterKey, v: string) => {
    setSavedId("");
    setFilters((f) => ({ ...f, [k]: v }));
  };
  const toggleSort = (key: string) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "key" ? "asc" : "desc" }));
  const mySaved = (saved.data ?? []).filter((f) => f.space === null || f.space === undefined || f.space === bp);
  const current = mySaved.find((f) => String(f.id) === savedId) ?? null;
  const canDeleteSaved = !!current && (current.can_delete ?? current.user_id === me?.id);

  function applySaved(id: string) {
    setSavedId(id);
    const f = mySaved.find((x) => String(x.id) === id);
    if (!f) return;
    const next = { ...NO_FILTERS };
    for (const k of FILTER_KEYS) if (typeof f.query?.[k] === "string") next[k] = f.query[k];
    setQ(next.q);
    setFilters(next);
  }

  async function deleteSaved() {
    if (!current) return;
    if (!(await ask({ title: `Xoá bộ lọc “${current.name}”?`, body: <p>Người khác đang dùng bộ lọc chia sẻ này cũng mất nó.</p>, ok: "Xoá", danger: true }))) return;
    try {
      await api.delete(`/tasks/filters/${current.id}`);
      setSavedId("");
      await saved.mutate();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  async function bulk(body: Record<string, unknown>, what: string) {
    if (!selected.size) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.post("/tasks/bulk", { keys: [...selected], ...body });
      setMessage(`Đã ${what} ${selected.size} task.`);
      setSelected(new Set());
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    await refreshTasks();
    setBusy(false);
  }

  async function bulkDelete() {
    const n = selected.size;
    if (!(await ask({ title: `Xoá ${n} task?`, body: <p>Sub-task của chúng bị xoá theo. Không hoàn tác được.</p>, ok: `Xoá ${n} task`, danger: true }))) return;
    await bulk({ delete: true }, "xoá");
  }

  const allChecked = rows.length > 0 && rows.every((r) => selected.has(r.key));
  const sprintOptions = (sprints.data ?? []).filter((s) => s.state !== "closed");

  return (
    <div className="tk-list">
      {confirmEl}
      <section className="card">
        <SmartFilter
          bp={bp}
          active={filters}
          onApply={(query) => {
            const next = { ...NO_FILTERS };
            for (const k of FILTER_KEYS) if (typeof query[k] === "string") next[k] = query[k];
            setSavedId("");
            setQ(next.q);
            setFilters(next);
          }}
          onRemove={(k) => {
            if (k === "q") setQ("");
            else if ((FILTER_KEYS as readonly string[]).includes(k)) set(k as FilterKey, "");
          }}
        />
        <div className="rq-grid">
          <label className="rq-field">
            <span className="rq-label">Tìm</span>
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Mã, tiêu đề, mô tả…" />
          </label>
          <label className="rq-field">
            <span className="rq-label">Trạng thái</span>
            <select value={filters.status} onChange={(e) => set("status", e.target.value)}>
              <option value="">Mọi trạng thái</option>
              {statuses.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Loại</span>
            <select value={filters.type} onChange={(e) => set("type", e.target.value)}>
              <option value="">Mọi loại</option>
              {TASK_TYPES.filter((t) => space.types.includes(t)).map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABEL[t]}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Người làm</span>
            <select value={filters.assignee} onChange={(e) => set("assignee", e.target.value)}>
              <option value="">Mọi người</option>
              <option value="me">Của tôi</option>
              <option value="none">Chưa giao</option>
              {(people.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {personName(p)}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Ưu tiên</span>
            <select value={filters.priority} onChange={(e) => set("priority", e.target.value)}>
              <option value="">Mọi mức</option>
              {priorityList(space).map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Lý do đóng</span>
            <select value={filters.resolution} onChange={(e) => set("resolution", e.target.value)}>
              <option value="">Mọi lý do</option>
              {RESOLUTIONS.map((r) => (
                <option key={r} value={r}>
                  {RESOLUTION_LABEL[r]}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Sprint</span>
            <select value={filters.sprint} onChange={(e) => set("sprint", e.target.value)}>
              <option value="">Mọi sprint</option>
              <option value="active">Sprint đang chạy</option>
              <option value="backlog">Backlog (chưa vào sprint)</option>
              {(sprints.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Phiên bản</span>
            <select value={filters.version} onChange={(e) => set("version", e.target.value)}>
              <option value="">Mọi phiên bản</option>
              {(versions.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Nhãn</span>
            <input value={filters.label} onChange={(e) => set("label", e.target.value.trim())} placeholder="backend" />
          </label>
          <label className="rq-field">
            <span className="rq-label">Hạn</span>
            <select value={filters.due} onChange={(e) => set("due", e.target.value)}>
              <option value="">Mọi hạn</option>
              <option value="overdue">Quá hạn</option>
              <option value="week">Đến hạn trong 7 ngày</option>
            </select>
          </label>
          <DensityToggle view="list" />
        </div>
        <div className="rq-foot tk-saved">
          <div className="tk-inline">
            <select value={savedId} onChange={(e) => applySaved(e.target.value)} aria-label="Bộ lọc đã lưu" disabled={!saved.data}>
              <option value="">Bộ lọc đã lưu…</option>
              {mySaved.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                  {f.shared ? " (chia sẻ)" : ""}
                </option>
              ))}
            </select>
            {canDeleteSaved && <IconButton icon="remove" tone="danger" label="Xoá bộ lọc này" onClick={() => void deleteSaved()} />}
          </div>
          <div className="tk-inline">
            {filtersOn && (
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
                  setQ("");
                  setSavedId("");
                  setFilters(NO_FILTERS);
                }}
              >
                Xoá lọc
              </button>
            )}
            <button type="button" className="sm" onClick={() => setSaving(true)} disabled={!filtersOn}>
              Lưu bộ lọc
            </button>
          </div>
        </div>
      </section>

      {message && <Message text={message} onClose={() => setMessage(null)} />}

      {selected.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Thao tác hàng loạt">
          <strong className="small">Đã chọn {selected.size}</strong>
          <BulkSelect label="Trạng thái" disabled={busy} onPick={(v) => void bulk({ set: { status_id: Number(v) } }, "chuyển trạng thái")}>
            {statuses.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </BulkSelect>
          <BulkSelect label="Người làm" disabled={busy} onPick={(v) => void bulk({ set: { assignee_id: v === "none" ? null : Number(v) } }, "giao")}>
            <option value="none">Bỏ giao</option>
            {(people.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {personName(p)}
              </option>
            ))}
          </BulkSelect>
          <BulkSelect label="Ưu tiên" disabled={busy} onPick={(v) => void bulk({ set: { priority: v } }, "đổi ưu tiên")}>
            {priorityList(space).map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </BulkSelect>
          <BulkSelect label="Sprint" disabled={busy} onPick={(v) => void bulk({ set: { sprint_id: v === "none" ? null : Number(v) } }, "chuyển sprint")}>
            <option value="none">Backlog</option>
            {sprintOptions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </BulkSelect>
          <BulkSelect label="Phiên bản" disabled={busy} onPick={(v) => void bulk({ set: { version_id: v === "none" ? null : Number(v) } }, "đổi phiên bản")}>
            <option value="none">Bỏ phiên bản</option>
            {(versions.data ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </BulkSelect>
          <span className="tk-inline">
            <input className="tk-bulk-label" value={labelText} onChange={(e) => setLabelText(e.target.value)} placeholder="Nhãn" aria-label="Nhãn" />
            <IconButton
              icon="plus"
              label="Thêm nhãn"
              disabled={busy || !labelText.trim()}
              onClick={() => void bulk({ set: { labels_add: [labelText.trim()] } }, "thêm nhãn cho").then(() => setLabelText(""))}
            />
            <IconButton
              icon="minus"
              label="Bỏ nhãn"
              disabled={busy || !labelText.trim()}
              onClick={() => void bulk({ set: { labels_remove: [labelText.trim()] } }, "bỏ nhãn của").then(() => setLabelText(""))}
            />
          </span>
          <span className="spacer" />
          {space.can_manage && (
            <button type="button" className="sm ghost tc-danger" onClick={() => void bulkDelete()} disabled={busy}>
              <Icon name="remove" size={14} /> Xoá
            </button>
          )}
          <button type="button" className="sm ghost" onClick={() => setSelected(new Set())}>
            Bỏ chọn
          </button>
        </div>
      )}

      <section className="card flush">
        {!list.data ? (
          <Loading error={list.error} rows={8} />
        ) : !rows.length ? (
          <EmptyState icon="search" title={filtersOn ? "Không có task nào khớp bộ lọc" : "Dự án chưa có task nào"} />
        ) : (
          <>
            <div className="table-wrap">
              <table className="tk-table">
                <thead>
                  <tr>
                    <th className="check-col">
                      <input
                        type="checkbox"
                        aria-label="Chọn mọi task trên trang"
                        checked={allChecked}
                        onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((r) => r.key)) : new Set())}
                      />
                    </th>
                    <SortTh id="key" sort={sort} onSort={toggleSort}>
                      Mã
                    </SortTh>
                    <th>Tiêu đề</th>
                    <th>Trạng thái</th>
                    <SortTh id="priority" sort={sort} onSort={toggleSort}>
                      Ưu tiên
                    </SortTh>
                    <th>Người làm</th>
                    <th className="num">SP</th>
                    <SortTh id="due" sort={sort} onSort={toggleSort}>
                      Hạn
                    </SortTh>
                    <SortTh id="updated" sort={sort} onSort={toggleSort}>
                      Cập nhật
                    </SortTh>
                    <th className="tk-actions-col" aria-label="Thao tác" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => {
                    const overdue = t.due_date && t.status.category !== "done" && t.due_date < new Date().toISOString().slice(0, 10);
                    return (
                      <tr key={t.key} className={selected.has(t.key) ? "selected" : undefined}>
                        <td className="check-col">
                          <input
                            type="checkbox"
                            aria-label={`Chọn ${t.key}`}
                            checked={selected.has(t.key)}
                            onChange={(e) => {
                              const next = new Set(selected);
                              if (e.target.checked) next.add(t.key);
                              else next.delete(t.key);
                              setSelected(next);
                            }}
                          />
                        </td>
                        <td className="nowrap">
                          <span className="tk-keycell">
                            <TypeIcon type={t.type} />
                            <span className={`tk-key${t.status.category === "done" ? " done" : ""}`}>{t.key}</span>
                          </span>
                        </td>
                        <td>
                          <button type="button" className="link tk-row-title" onClick={() => onOpen(t.key)}>
                            {t.title}
                          </button>
                          <span className="tk-row-badges">
                            <GitBadge row={t} />
                            {(t.labels ?? []).map((l) => (
                              <span key={l} className="tk-label">
                                {l}
                              </span>
                            ))}
                          </span>
                        </td>
                        <td>
                          <StatusPill status={t.status} resolution={t.resolution} />
                        </td>
                        <td className="nowrap">
                          <RowPriority row={t} withLabel />
                        </td>
                        <td className="nowrap">
                          <span className="tk-person">
                            <PersonAvatar person={t.assignee} />
                            <span className={t.assignee ? undefined : "faint"}>{personName(t.assignee)}</span>
                          </span>
                        </td>
                        <td className="num">{t.story_points ?? "—"}</td>
                        <td className={`nowrap${overdue ? " tk-overdue" : ""}`}>{t.due_date ? date(t.due_date) : "—"}</td>
                        <td className="nowrap muted small" title={t.updated_at}>
                          {relative(t.updated_at)}
                        </td>
                        <td className="tk-actions-col">
                          <Link href={`/tasks/${encodeURIComponent(t.key)}`} className="icon-btn" aria-label={`Mở ${t.key} trong trang riêng`} data-tip="Mở trang riêng">
                            <Icon name="external" size={16} />
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="tk-pad">
              <Pagination page={page} pageSize={size} total={list.data.total} shown={rows.length} onPage={setPage} onPageSize={setSize} label="task" />
            </div>
          </>
        )}
      </section>
      {saving && <SaveFilterModal bp={bp} query={filters} onClose={() => setSaving(false)} onSaved={(id) => void saved.mutate().then(() => setSavedId(String(id)))} />}
    </div>
  );
}

function BulkSelect({ label, disabled, onPick, children }: { label: string; disabled?: boolean; onPick: (v: string) => void; children: ReactNode }) {
  return (
    <select
      className="compact"
      value=""
      disabled={disabled}
      aria-label={label}
      onChange={(e) => {
        if (e.target.value) onPick(e.target.value);
      }}
    >
      <option value="">{label}…</option>
      {children}
    </select>
  );
}

function SaveFilterModal({ bp, query, onClose, onSaved }: { bp: number; query: Filters; onClose: () => void; onSaved: (id: number) => void }) {
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => v));
      const created = await api.post<TaskFilter>("/tasks/filters", { name: name.trim(), space: bp, query: clean, shared });
      onSaved(created.id);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Lưu bộ lọc"
      onClose={onClose}
      busy={busy}
      width={420}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            Lưu
          </button>
        </>
      }
    >
      <form
        className="adm-form tk-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="rq-field">
          <span className="rq-label">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Bug quá hạn của tôi" autoFocus />
        </label>
        <label className="inline-check">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Chia sẻ cho mọi người xem dự án
        </label>
      </form>
    </Modal>
  );
}
