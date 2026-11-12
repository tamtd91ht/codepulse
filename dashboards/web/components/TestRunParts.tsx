"use client";

import { type ReactNode, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Alert, Card, Loading, Skeleton } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { CASE_TYPES, PRIORITIES, PRIORITY_LABEL, type Suite, TYPE_LABEL } from "@/lib/testcases";
import {
  BUILTIN_STATUSES,
  STATUS_COLORS,
  STATUS_KEYS,
  errorText,
  passedPct,
  statusClass,
  statusName,
  type ConfigGroup,
  type Counts,
  type Milestone,
  type Person,
  type ResultStatus,
  type RunOut,
  type SectionNode,
  type Selection,
  type StatusDef,
  type StatusList,
  type TestStatus,
} from "@/lib/testruns";
import { dateTimeMin } from "@/lib/format";

/* Shared pieces of the test run screens (docs/specs/30 §13.6): status colours, progress bars, the case picker. */

/** The result statuses of a dự án (§15 G6), the built-in ones while they load. */
export function useStatuses(projectId: number | null | undefined) {
  const list = useApi<StatusList>(projectId ? `/testcases/statuses?business_project_id=${projectId}` : null);
  return { defs: list.data?.items ?? BUILTIN_STATUSES, list };
}

export function StatusPill({ status, statuses, title }: { status: TestStatus; statuses?: StatusDef[] | null; title?: string }) {
  return (
    <span className={`tr-pill ${statusClass(status, statuses)}`} title={title}>
      {statusName(status, statuses)}
    </span>
  );
}

/** The statuses a bar shows, in order: the configured ones (Untested last), then any key the
 * counts carry that no definition names (a custom status of another dự án in a plan). */
function barStatuses(counts: Counts, statuses?: StatusDef[] | null): StatusDef[] {
  const defs = statuses?.length ? statuses : BUILTIN_STATUSES;
  const known = new Set(defs.map((d) => d.key));
  const extra = Object.keys(counts)
    .filter((k) => k !== "total" && !known.has(k) && (counts[k] ?? 0) > 0)
    .map((k): StatusDef => ({ id: null, key: k, name: k, color: "muted", done: true, builtin: false, position: 999_999, archived: false }));
  const all = [...defs, ...extra].filter((d) => d.key !== "untested");
  const untested = defs.find((d) => d.key === "untested") ?? BUILTIN_STATUSES[BUILTIN_STATUSES.length - 1];
  // Archived or unused custom statuses only take room when tests carry them.
  return [...all.filter((d) => d.builtin || (counts[d.key] ?? 0) > 0 || !d.archived), untested];
}

/** Stacked bar of every status of the dự án in its order and colours, with a legend unless `compact`. */
export function StatusBar({ counts, compact, statuses }: { counts: Counts; compact?: boolean; statuses?: StatusDef[] | null }) {
  const total = counts.total || 0;
  const shown = barStatuses(counts, statuses);
  const label = `${passedPct(counts)}% passed; ${shown.map((s) => `${statusName(s.key, shown)} ${counts[s.key] ?? 0}`).join(", ")}`;
  return (
    <div className={`tr-progress${compact ? " compact" : ""}`}>
      <div className="tr-bar" role="img" aria-label={label} title={label}>
        {total > 0 &&
          shown.map((s) => {
            const n = counts[s.key] ?? 0;
            return n ? <span key={s.key} className={statusClass(s.key, shown)} style={{ width: `${(n / total) * 100}%` }} /> : null;
          })}
      </div>
      {compact ? (
        <span className="tr-pct small">
          <strong>{passedPct(counts)}%</strong> <span className="muted">passed · {total} test</span>
        </span>
      ) : (
        <ul className="tr-legend small">
          {shown
            .filter((s) => s.builtin || (counts[s.key] ?? 0) > 0 || !s.archived)
            .filter((s) => s.key !== "skipped" || (counts.skipped ?? 0) > 0)
            .map((s) => (
              <li key={s.key}>
                <span className={`tr-dot ${statusClass(s.key, shown)}`} aria-hidden="true" />
                {statusName(s.key, shown)} <strong>{counts[s.key] ?? 0}</strong>
              </li>
            ))}
          <li className="muted">
            <strong>{passedPct(counts)}%</strong> passed / {total} test
          </li>
        </ul>
      )}
    </div>
  );
}

/** The statuses a result may be recorded with: built-in ones, then live custom ones. */
export function resultStatuses(statuses?: StatusDef[] | null): StatusDef[] {
  const defs = statuses?.length ? statuses : BUILTIN_STATUSES;
  return defs.filter((d) => d.key !== "untested" && !d.archived);
}

/** The result statuses as toggle buttons (with their shortcut keys in the test drawer). */
export function StatusButtons({
  value,
  onChange,
  disabled,
  size,
  statuses,
  shortcuts,
}: {
  value: ResultStatus | null;
  onChange: (s: ResultStatus) => void;
  disabled?: boolean;
  size?: "sm";
  statuses?: StatusDef[] | null;
  shortcuts?: boolean;
}) {
  return (
    <div className="tr-st-group" role="group" aria-label="Kết quả">
      {resultStatuses(statuses).map((s) => {
        const key = shortcuts ? STATUS_KEYS[s.key] : undefined;
        return (
          <button
            key={s.key}
            type="button"
            className={`tr-st-btn ${statusClass(s.key, statuses)}${size ? " sm" : ""}`}
            aria-pressed={value === s.key}
            aria-keyshortcuts={key}
            title={key ? `${s.name} (phím ${key})` : undefined}
            disabled={disabled}
            onClick={() => onChange(s.key)}
          >
            {s.name}
            {key && <kbd className="tr-kbd" aria-hidden="true">{key}</kbd>}
          </button>
        );
      })}
    </div>
  );
}

/* ---- Thiết lập › Trạng thái kết quả (§15 G6) ---- */

function StatusModal({ projectId, item, onClose, onSaved }: { projectId: number; item: StatusDef | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(item?.name ?? "");
  const [color, setColor] = useState<string>(item?.color ?? "info");
  const [done, setDone] = useState(item?.done ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (item?.id) await api.patch(`/testcases/statuses/${item.id}`, { name: name.trim(), color, done });
      else await api.post("/testcases/statuses", { business_project_id: projectId, name: name.trim(), color, done });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={item ? `Sửa trạng thái “${item.name}”` : "Thêm trạng thái kết quả"}
      onClose={onClose}
      busy={busy}
      width={480}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !name.trim()}>
            {item ? "Lưu" : "Thêm"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <label className="adm-field">
          <span className="small muted">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Cần xác nhận" maxLength={60} autoFocus />
        </label>
        {item && <p className="small muted">Mã: {item.key} (không đổi được)</p>}
        <div className="adm-field">
          <span className="small muted">Màu</span>
          <div className="tr-chips" role="radiogroup" aria-label="Màu">
            {STATUS_COLORS.map((c) => (
              <button key={c.value} type="button" role="radio" aria-checked={color === c.value} className={`tr-st-btn sm st-c-${c.value}`} aria-pressed={color === c.value} onClick={() => setColor(c.value)}>
                {c.label}
              </button>
            ))}
          </div>
        </div>
        <label className="inline-check">
          <input type="checkbox" checked={done} onChange={(e) => setDone(e.target.checked)} />
          <span>Tính là đã chạy xong (không cộng vào thời gian còn lại của run)</span>
        </label>
      </div>
    </Modal>
  );
}

export function ResultStatusesCard({ projectId }: { projectId: number }) {
  const { list } = useStatuses(projectId);
  const [editing, setEditing] = useState<StatusDef | "new" | null>(null);
  const [removing, setRemoving] = useState<StatusDef | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const items = list.data?.items ?? [];
  const canEdit = !!list.data?.can_edit;
  const custom = items.filter((s) => !s.builtin);

  const act = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await list.mutate();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const move = (s: StatusDef, by: number) => {
    const i = custom.indexOf(s);
    const other = custom[i + by];
    if (!other?.id || !s.id) return;
    // Positions shown are 100 + the stored one; swap the stored ones.
    void act(async () => {
      await api.patch(`/testcases/statuses/${s.id}`, { position: other.position - 100 });
      await api.patch(`/testcases/statuses/${other.id}`, { position: s.position - 100 });
    });
  };

  return (
    <Card
      title="Trạng thái kết quả"
      id="tr-statuses"
      subtitle="Passed, Failed, Blocked, Retest, Skipped có sẵn; thêm trạng thái riêng của dự án (đang dùng thì lưu trữ, không xoá)"
      actions={
        canEdit ? (
          <button type="button" className="sm" onClick={() => setEditing("new")} disabled={custom.length >= 20}>
            <Icon name="plus" size={14} /> Thêm trạng thái
          </button>
        ) : undefined
      }
    >
      {error && !removing && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!list.data ? (
        <Loading error={list.error} rows={3} />
      ) : (
        <div className="table-wrap">
          <table className="tc-table">
            <thead>
              <tr>
                <th>Trạng thái</th>
                <th>Mã</th>
                <th>Tính là xong</th>
                <th>Loại</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.key}>
                  <td>
                    <StatusPill status={s.key} statuses={items} />
                  </td>
                  <td className="small tc-code-cell">{s.key}</td>
                  <td className="small">{s.done ? "Có" : "Không"}</td>
                  <td className="small muted">{s.builtin ? "Có sẵn" : s.archived ? "Đã lưu trữ" : "Tuỳ biến"}</td>
                  <td>
                    {canEdit && !s.builtin && (
                      <div className="tr-row-tools">
                        <IconButton icon="chevronUp" label={`Đưa ${s.name} lên`} onClick={() => move(s, -1)} disabled={busy || custom.indexOf(s) === 0} />
                        <IconButton icon="chevronDown" label={`Đưa ${s.name} xuống`} onClick={() => move(s, 1)} disabled={busy || custom.indexOf(s) === custom.length - 1} />
                        <IconButton icon="edit" label={`Sửa ${s.name}`} onClick={() => setEditing(s)} />
                        <button type="button" className="sm ghost" disabled={busy} onClick={() => void act(() => api.patch(`/testcases/statuses/${s.id}`, { archived: !s.archived }))}>
                          {s.archived ? "Dùng lại" : "Lưu trữ"}
                        </button>
                        <IconButton icon="remove" label={`Xoá ${s.name}`} onClick={() => setRemoving(s)} />
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!custom.length && <p className="small muted">Chưa có trạng thái tuỳ biến nào.</p>}
        </div>
      )}
      {editing && (
        <StatusModal
          projectId={projectId}
          item={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void list.mutate();
          }}
        />
      )}
      {removing && (
        <ConfirmModal
          title={`Xoá trạng thái “${removing.name}”?`}
          confirmLabel="Xoá"
          danger
          busy={busy}
          error={error}
          onClose={() => {
            setRemoving(null);
            setError(null);
          }}
          onConfirm={() => void act(() => api.delete(`/testcases/statuses/${removing.id}`)).then((ok) => ok && setRemoving(null))}
        >
          Chỉ xoá được trạng thái chưa dùng trong kết quả nào; đã dùng thì chọn “Lưu trữ”.
        </ConfirmModal>
      )}
    </Card>
  );
}

/** Milestones, people and configurations of a dự án (shared SWR cache keys). */
export function useSetupLists(projectId: number) {
  const milestones = useApi<Milestone[]>(`/testcases/milestones?business_project_id=${projectId}`);
  const people = useApi<Person[]>(`/testcases/people?business_project_id=${projectId}`);
  const configs = useApi<ConfigGroup[]>(`/testcases/configs?business_project_id=${projectId}`);
  return { milestones, people, configs };
}

export function MilestoneSelect({ value, onChange, milestones, id }: { value: number | null; onChange: (v: number | null) => void; milestones: Milestone[] | undefined; id?: string }) {
  return (
    <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">Không gắn milestone</option>
      {(milestones ?? [])
        .filter((m) => !m.completed_at || m.id === value)
        .map((m) => (
          <option key={m.id} value={m.id}>
            {m.key} · {m.name}
          </option>
        ))}
    </select>
  );
}

export function PersonSelect({ value, onChange, people, empty = "Chưa giao", id }: { value: number | null; onChange: (v: number | null) => void; people: Person[] | undefined; empty?: string; id?: string }) {
  return (
    <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{empty}</option>
      {(people ?? []).map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}

/** A yes / no dialog for closing and deleting. */
export function ConfirmModal({
  title,
  children,
  confirmLabel,
  danger,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      busy={busy}
      width={460}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className={danger ? "danger" : "primary ink"} onClick={onConfirm} disabled={busy}>
            {busy ? "Đang xử lý…" : confirmLabel}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <div className="small">{children}</div>
    </Modal>
  );
}

/** One run in a list: key, name, where it belongs, progress. */
export function RunRow({ run, onOpen }: { run: RunOut; onOpen: (id: number) => void }) {
  return (
    <li className="tr-item">
      <div className="tr-item-main">
        <div className="tr-item-title">
          <span className="tr-key">{run.key}</span>
          <button type="button" className="link" onClick={() => onOpen(run.id)}>
            {run.name}
          </button>
          {run.completed_at && <span className="pill">Đã đóng</span>}
        </div>
        <div className="small muted tr-item-meta">
          {run.config && <span>{run.config}</span>}
          {run.milestone_name && <span>Milestone: {run.milestone_name}</span>}
          {run.plan_name && <span>Plan: {run.plan_name}</span>}
          <span>{run.assigned_to ? `Giao: ${run.assigned_to.name}` : "Chưa giao"}</span>
          <span>
            Tạo {dateTimeMin(run.created_at)}
            {run.created_by ? ` bởi ${run.created_by.name}` : ""}
          </span>
        </div>
      </div>
      <StatusBar counts={run.counts} compact />
    </li>
  );
}

/* ---- Choosing the cases of a run (Selection, §14.3) ---- */

export type PickMode = "preset" | "all" | "sections" | "filter";
export interface Pick {
  mode: PickMode;
  suite: number | null;
  sections: number[];
  priority: string[];
  type: string[];
  tags: string;
}

export function initialPick(initial?: Selection | null): Pick {
  const preset = !!initial && (!!initial.case_ids?.length || !!initial.regression);
  return {
    mode: preset ? "preset" : initial?.section_ids?.length ? "sections" : "all",
    suite: initial?.suite_id ?? null,
    sections: initial?.section_ids ?? [],
    priority: initial?.priority ?? [],
    type: initial?.type ?? [],
    tags: (initial?.tags ?? []).join(", "),
  };
}

/** The Selection the picker stands for; null while incomplete (sections mode with none ticked). */
export function selectionOf(pick: Pick, initial?: Selection | null): Selection | null {
  const suite = pick.suite ?? undefined;
  if (pick.mode === "preset" && initial) {
    if (initial.regression) return { regression: initial.regression };
    return { case_ids: initial.case_ids ?? [], ...(initial.suite_id ? { suite_id: initial.suite_id } : {}) };
  }
  if (pick.mode === "sections") return pick.suite && pick.sections.length ? { suite_id: pick.suite, section_ids: pick.sections } : null;
  if (pick.mode === "filter") {
    const tags = pick.tags
      .split(/[,;\n]/)
      .map((t) => t.trim())
      .filter(Boolean);
    if (!pick.priority.length && !pick.type.length && !tags.length) return null;
    return {
      ...(suite ? { suite_id: suite } : { all: true }),
      ...(pick.priority.length ? { priority: pick.priority } : {}),
      ...(pick.type.length ? { type: pick.type } : {}),
      ...(tags.length ? { tags } : {}),
    };
  }
  return suite ? { suite_id: suite, all: true } : { all: true };
}

function toggle(list: string[], v: string): string[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function SelectionPicker({
  projectId,
  pick,
  onChange,
  initial,
}: {
  projectId: number;
  pick: Pick;
  onChange: (p: Pick) => void;
  initial?: Selection | null;
}) {
  const suites = useApi<Suite[]>(`/testcases/suites?business_project_id=${projectId}`);
  const sections = useApi<SectionNode[]>(pick.mode === "sections" && pick.suite ? `/testcases/sections?suite_id=${pick.suite}` : null);
  const live = (suites.data ?? []).filter((s) => !s.archived);
  const hasPreset = !!initial && (!!initial.case_ids?.length || !!initial.regression);
  const set = (p: Partial<Pick>) => onChange({ ...pick, ...p });

  const modes: [PickMode, string][] = [
    ...(hasPreset ? ([["preset", initial?.regression ? "Danh sách Cần test lại" : "Testcase đã chọn"]] as [PickMode, string][]) : []),
    ["all", "Cả bộ"],
    ["sections", "Theo thư mục"],
    ["filter", "Theo bộ lọc"],
  ];

  // Sections whose ancestor is ticked are included already: shown ticked and locked.
  const byId = new Map((sections.data ?? []).map((s) => [s.id, s]));
  const chosen = new Set(pick.sections);
  const ancestorChosen = (s: SectionNode) => {
    let p = s.parent_id;
    while (p != null) {
      if (chosen.has(p)) return true;
      p = byId.get(p)?.parent_id ?? null;
    }
    return false;
  };

  let summary: string;
  if (pick.mode === "preset" && initial) {
    summary = initial.regression
      ? `Testcase cần test lại theo MR merge trong ${initial.regression.days} ngày${initial.regression.target_branch ? ` vào ${initial.regression.target_branch}` : ""}`
      : `${initial.case_ids?.length ?? 0} testcase đã chọn`;
  } else if (pick.mode === "all") {
    const pool = pick.suite ? live.filter((s) => s.id === pick.suite) : live;
    const n = pool.reduce((a, s) => a + s.total - s.obsolete, 0);
    summary = suites.data ? `Khoảng ${n} testcase (không tính testcase lỗi thời)` : "";
  } else if (pick.mode === "sections") {
    const n = (sections.data ?? []).filter((s) => chosen.has(s.id) && !ancestorChosen(s)).reduce((a, s) => a + s.total, 0);
    summary = pick.sections.length ? `${n} testcase trong ${pick.sections.length} thư mục (gồm thư mục con)` : "Tick ít nhất một thư mục";
  } else {
    summary = "Chọn ít nhất một ưu tiên, type hoặc nhãn; số testcase tính khi tạo";
  }

  return (
    <fieldset className="tr-pick">
      <legend className="small muted">Chọn testcase</legend>
      <div className="tr-chips" role="group" aria-label="Cách chọn testcase">
        {modes.map(([m, label]) => (
          <button key={m} type="button" className="tr-chip" aria-pressed={pick.mode === m} onClick={() => set({ mode: m })}>
            {label}
          </button>
        ))}
      </div>
      {pick.mode !== "preset" && (
        <label className="adm-field">
          <span className="small muted">Bộ testcase</span>
          <select
            value={pick.suite ?? ""}
            onChange={(e) => set({ suite: e.target.value ? Number(e.target.value) : null, sections: [] })}
            disabled={!suites.data}
          >
            {pick.mode !== "sections" && <option value="">Mọi bộ của dự án</option>}
            {pick.mode === "sections" && <option value="">Chọn bộ…</option>}
            {live.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.total - s.obsolete})
              </option>
            ))}
          </select>
        </label>
      )}
      {pick.mode === "sections" && pick.suite && (
        <div className="tr-tree" role="group" aria-label="Thư mục">
          {!sections.data ? (
            sections.error ? (
              <Alert tone="error">Lỗi: {sections.error.message}</Alert>
            ) : (
              <Skeleton height={60} />
            )
          ) : !sections.data.length ? (
            <p className="small muted">Bộ này chưa có thư mục: chọn “Cả bộ”.</p>
          ) : (
            sections.data.map((s) => {
              const locked = ancestorChosen(s);
              return (
                <label key={s.id} className="inline-check tr-tree-row" style={{ paddingLeft: s.depth * 18 }}>
                  <input
                    type="checkbox"
                    checked={locked || chosen.has(s.id)}
                    disabled={locked}
                    onChange={(e) => set({ sections: e.target.checked ? [...pick.sections, s.id] : pick.sections.filter((x) => x !== s.id) })}
                  />
                  <span>{s.name}</span>
                  <span className="muted small">({s.total})</span>
                </label>
              );
            })
          )}
        </div>
      )}
      {pick.mode === "filter" && (
        <>
          <div className="adm-field">
            <span className="small muted">Ưu tiên</span>
            <div className="tr-chips" role="group" aria-label="Ưu tiên">
              {PRIORITIES.map((p) => (
                <button key={p} type="button" className="tr-chip" aria-pressed={pick.priority.includes(p)} onClick={() => set({ priority: toggle(pick.priority, p) })}>
                  {PRIORITY_LABEL[p]}
                </button>
              ))}
            </div>
          </div>
          <div className="adm-field">
            <span className="small muted">Type</span>
            <div className="tr-chips" role="group" aria-label="Type">
              {CASE_TYPES.map((t) => (
                <button key={t} type="button" className="tr-chip" aria-pressed={pick.type.includes(t)} onClick={() => set({ type: toggle(pick.type, t) })}>
                  {TYPE_LABEL[t]}
                </button>
              ))}
            </div>
          </div>
          <label className="adm-field">
            <span className="small muted">Nhãn (cách nhau bằng dấu phẩy)</span>
            <input value={pick.tags} onChange={(e) => set({ tags: e.target.value })} placeholder="smoke, thanh-toan" />
          </label>
        </>
      )}
      {summary && <p className="small tr-pick-sum">{summary}</p>}
    </fieldset>
  );
}

/** Jira keys typed one per line / comma separated, upper-cased. */
export function splitKeys(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
}
