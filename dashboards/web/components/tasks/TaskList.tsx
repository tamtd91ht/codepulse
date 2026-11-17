"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import useSWR from "swr";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { EmptyState, Loading, Message, Pagination, SortTh } from "@/components/ui";
import { type ApiError, api, qs, useApi } from "@/lib/api";
import { date, relative } from "@/lib/format";
import type { FilterScope, Me, SavedTaskFilter, SearchCond, SearchTree, TaskSearchPage, TaskSpace } from "@/lib/types";

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
import { type ChipCond, FilterChips, chipId, fromTree, toTree } from "./FilterChips";
import { SmartFilter } from "./SmartFilter";

/*
 * Danh sách (§13): every field as a filter, saved filters, sortable columns, bulk changes, paging.
 * Since §21.4 it reads POST /tasks/search: the grid's filters ANDed with the chip bar's tree.
 */

// category and updated_from have no control of their own: only "Lọc nhanh" (§19.8) sets them.
const FILTER_KEYS = ["q", "status", "type", "assignee", "priority", "sprint", "version", "label", "due", "category", "updated_from", "resolution"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
type Filters = Record<FilterKey, string>;
const NO_FILTERS: Filters = { q: "", status: "", type: "", assignee: "", priority: "", sprint: "", version: "", label: "", due: "", category: "", updated_from: "", resolution: "" };

/** The grid's filters as conditions of the search tree. */
function gridConds(f: Filters): SearchCond[] {
  const out: SearchCond[] = [];
  for (const k of FILTER_KEYS) {
    const v = f[k].trim();
    if (!v) continue;
    if (k === "q") out.push({ field: "text", op: "is", value: v });
    else if (k === "due") out.push({ field: "due", op: "is", value: v });
    else if (k === "updated_from") {
      const d = new Date(`${v}T00:00:00Z`);
      d.setUTCDate(d.getUTCDate() - 1); // "from D" = after the day before
      out.push({ field: "updated", op: "after", value: d.toISOString().slice(0, 10) });
    } else out.push({ field: k, op: "is", value: [v] });
  }
  return out;
}

export function TaskList({ space, me, onOpen }: { space: TaskSpace; me: Me | undefined; onOpen: (key: string) => void }) {
  const bp = space.business_project_id;
  const statuses = useMemo(() => sortedStatuses(space), [space]);
  const people = useTaskPeople(bp);
  const sprints = useSprints(bp);
  const versions = useVersions(bp);
  const saved = useApi<SavedTaskFilter[]>(`/tasks/search/filters?space=${bp}`);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [chips, setChipsState] = useState<ChipCond[]>([]);
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
  }, [filters, chips, sort, size, bp]);

  // The grid ANDed with the chip bar (its "là" chips of one field ORed).
  const tree: SearchTree = useMemo(() => ({ all: [...gridConds(filters), ...(toTree(chips).all ?? [])] }), [filters, chips]);
  const treeJson = JSON.stringify(tree);
  const body = { space: [bp], tree, sort: sort.key, order: sort.dir, limit: size, offset: (page - 1) * size };
  // A string key under /tasks: refreshTasks() refreshes it with the rest of the module.
  const list = useSWR<TaskSearchPage, ApiError>(`/tasks/search#${JSON.stringify(body)}`, () => api.post<TaskSearchPage>("/tasks/search", body), {
    keepPreviousData: true,
    shouldRetryOnError: (err: ApiError) => !err.status || err.status >= 500,
  });
  const rows = list.data?.items ?? [];
  const filtersOn = FILTER_KEYS.some((k) => filters[k]) || chips.length > 0;
  const exportHref = `/api/v1/tasks/search/export.csv${qs({ space: bp, tree: treeJson, sort: sort.key, order: sort.dir })}`;
  const set = (k: FilterKey, v: string) => {
    setSavedId("");
    setFilters((f) => ({ ...f, [k]: v }));
  };
  const setChips = (next: ChipCond[]) => {
    setSavedId("");
    setChipsState(next);
  };
  const toggleSort = (key: string) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "key" ? "asc" : "desc" }));
  const mySaved = (saved.data ?? []).filter((f) => f.space === null || f.space === undefined || f.space === bp);
  const current = mySaved.find((f) => String(f.id) === savedId) ?? null;
  const canDeleteSaved = !!current && (current.can_delete ?? current.user_id === me?.id);
  const [editingSaved, setEditingSaved] = useState(false);

  // A saved filter opens as chips (its whole tree), so one sees and edits what it does.
  function applySaved(id: string) {
    setSavedId(id);
    const f = mySaved.find((x) => String(x.id) === id);
    if (!f) return;
    setQ("");
    setFilters(NO_FILTERS);
    setChipsState(fromTree(f.tree));
  }

  // A saved filter opened from Ctrl+K (`?filter=ID`, docs/specs/31 §20.2): applied once loaded.
  const [pendingSaved, setPendingSaved] = useState<string | null>(() => {
    try {
      return new URLSearchParams(window.location.search).get("filter");
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (!pendingSaved || !saved.data) return;
    applySaved(pendingSaved);
    setPendingSaved(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingSaved, saved.data]);

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
          onApply={(conds) => {
            // The sentence's chips replace every filter, shown in the chip bar to edit (§21.4).
            setQ("");
            setFilters(NO_FILTERS);
            setChips(conds.map((c) => ({ ...c, id: chipId() })));
          }}
        />
        <FilterChips space={space} chips={chips} onChange={setChips} />
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
                  {f.shared_scope === "team" ? " (team)" : f.shared_scope === "project" ? " (dự án)" : ""}
                  {f.pinned ? " · ghim" : ""}
                </option>
              ))}
            </select>
            {saved.error && (
              <button type="button" className="link small" onClick={() => void saved.mutate()}>
                Tải lại bộ lọc
              </button>
            )}
            {current?.can_edit && <IconButton icon="settings" label="Chia sẻ, ghim, đăng ký bộ lọc này" onClick={() => setEditingSaved(true)} />}
            {canDeleteSaved && <IconButton icon="remove" tone="danger" label="Xoá bộ lọc này" onClick={() => void deleteSaved()} />}
          </div>
          <div className="tk-inline">
            <a className="button ghost sm" href={exportHref} download aria-label="Xuất CSV theo bộ lọc hiện tại">
              <Icon name="download" size={14} /> CSV
            </a>
            {filtersOn && (
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
                  setQ("");
                  setSavedId("");
                  setFilters(NO_FILTERS);
                  setChipsState([]);
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
      {saving && <SaveFilterModal bp={bp} query={filters} tree={tree} onClose={() => setSaving(false)} onSaved={(id) => void saved.mutate().then(() => setSavedId(String(id)))} />}
      {editingSaved && current && <SaveFilterModal bp={bp} query={filters} tree={tree} existing={current} onClose={() => setEditingSaved(false)} onSaved={() => void saved.mutate()} />}
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

const SCOPE_LABEL: Record<FilterScope, string> = {
  me: "Chỉ mình tôi",
  team: "Team của tôi",
  project: "Mọi người xem dự án",
};

/* Lưu bộ lọc (§21.4): its tree, who sees it, a Board pin, a daily / weekly digest. With
 * `existing`: change those settings of one's own filter (its conditions stay). */
function SaveFilterModal({
  bp,
  query,
  tree,
  existing,
  onClose,
  onSaved,
}: {
  bp: number;
  query: Filters;
  tree: SearchTree;
  existing?: SavedTaskFilter;
  onClose: () => void;
  onSaved: (id: number) => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [scope, setScope] = useState<FilterScope>(existing?.shared_scope ?? "me");
  const [pinned, setPinned] = useState(existing?.pinned ?? false);
  const [cadence, setCadence] = useState<"" | "daily" | "weekly">(existing?.subscription?.cadence ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      let id: number;
      if (existing) {
        const body = { name: name.trim(), shared_scope: scope, pinned, ...(cadence ? { subscription: { cadence } } : { unsubscribe: true }) };
        id = (await api.patch<SavedTaskFilter>(`/tasks/search/filters/${existing.id}`, body)).id;
      } else {
        const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => v));
        const body = { name: name.trim(), space: bp, query: clean, tree, shared_scope: scope, pinned, subscription: cadence ? { cadence } : null };
        id = (await api.post<SavedTaskFilter>("/tasks/search/filters", body)).id;
      }
      onSaved(id);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={existing ? `Bộ lọc “${existing.name}”` : "Lưu bộ lọc"}
      onClose={onClose}
      busy={busy}
      width={460}
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
        <label className="rq-field">
          <span className="rq-label">Ai thấy</span>
          <select value={scope} onChange={(e) => setScope(e.target.value as FilterScope)}>
            {(Object.keys(SCOPE_LABEL) as FilterScope[]).map((k) => (
              <option key={k} value={k}>
                {SCOPE_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-check">
          <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Ghim lên Board làm lọc nhanh
        </label>
        <label className="rq-field">
          <span className="rq-label">Nhận tóm tắt</span>
          <select value={cadence} onChange={(e) => setCadence(e.target.value as "" | "daily" | "weekly")}>
            <option value="">Không</option>
            <option value="daily">Hằng ngày (8:00)</option>
            <option value="weekly">Hằng tuần (thứ Hai 8:00)</option>
          </select>
          <span className="small muted">Gửi qua Telegram nếu bạn đã liên kết, không thì vào hộp thông báo.</span>
        </label>
      </form>
    </Modal>
  );
}
