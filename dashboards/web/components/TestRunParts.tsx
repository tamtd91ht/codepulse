"use client";

import type { ReactNode } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Alert, Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { CASE_TYPES, PRIORITIES, PRIORITY_LABEL, type Suite, TYPE_LABEL } from "@/lib/testcases";
import {
  RESULT_STATUSES,
  TEST_STATUS_LABEL,
  TEST_STATUSES,
  passedPct,
  type ConfigGroup,
  type Counts,
  type Milestone,
  type Person,
  type ResultStatus,
  type RunOut,
  type SectionNode,
  type Selection,
  type TestStatus,
} from "@/lib/testruns";
import { dateTimeMin } from "@/lib/format";

/* Shared pieces of the test run screens (docs/specs/30 §13.6): status colours, progress bars, the case picker. */

export function StatusPill({ status }: { status: TestStatus }) {
  return <span className={`tr-pill st-${status}`}>{TEST_STATUS_LABEL[status]}</span>;
}

/** Stacked bar passed / failed / blocked / retest / untested, with a legend unless `compact`. */
export function StatusBar({ counts, compact }: { counts: Counts; compact?: boolean }) {
  const total = counts.total || 0;
  const label = `${passedPct(counts)}% passed; ${TEST_STATUSES.map((s) => `${TEST_STATUS_LABEL[s]} ${counts[s] ?? 0}`).join(", ")}`;
  return (
    <div className={`tr-progress${compact ? " compact" : ""}`}>
      <div className="tr-bar" role="img" aria-label={label} title={label}>
        {total > 0 &&
          TEST_STATUSES.map((s) =>
            counts[s] ? <span key={s} className={`st-${s}`} style={{ width: `${(counts[s] / total) * 100}%` }} /> : null,
          )}
      </div>
      {compact ? (
        <span className="tr-pct small">
          <strong>{passedPct(counts)}%</strong> <span className="muted">passed · {total} test</span>
        </span>
      ) : (
        <ul className="tr-legend small">
          {TEST_STATUSES.map((s) => (
            <li key={s}>
              <span className={`tr-dot st-${s}`} aria-hidden="true" />
              {TEST_STATUS_LABEL[s]} <strong>{counts[s] ?? 0}</strong>
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

/** Passed / Failed / Blocked / Retest as toggle buttons. */
export function StatusButtons({
  value,
  onChange,
  disabled,
  size,
}: {
  value: ResultStatus | null;
  onChange: (s: ResultStatus) => void;
  disabled?: boolean;
  size?: "sm";
}) {
  return (
    <div className="tr-st-group" role="group" aria-label="Kết quả">
      {RESULT_STATUSES.map((s) => (
        <button
          key={s}
          type="button"
          className={`tr-st-btn st-${s}${size ? " sm" : ""}`}
          aria-pressed={value === s}
          disabled={disabled}
          onClick={() => onChange(s)}
        >
          {TEST_STATUS_LABEL[s]}
        </button>
      ))}
    </div>
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
