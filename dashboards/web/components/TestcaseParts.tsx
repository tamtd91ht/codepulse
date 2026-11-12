"use client";

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { FlakyPill } from "@/components/CiImport";
import { BugPills } from "@/components/JiraBug";
import { Modal, ReorderControls, useDragList } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { CodePathsField } from "@/components/TestcaseLinks";
import { SectionOptions } from "@/components/TestcaseSections";
import { Alert, Drawer, Loading, Pill, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import {
  CASE_TYPES,
  type CaseDetail,
  type CaseMilestone,
  type CaseResult,
  type CaseTemplate,
  type CaseType,
  type CaseValues,
  type ImportResult,
  KIND_LABEL,
  PRIORITIES,
  PRIORITY_LABEL,
  PRIORITY_TONE,
  RESULT_LABEL,
  RESULT_TONE,
  type ReadOut,
  type Section,
  type SharedSteps,
  STATUS_LABEL,
  STATUS_TONE,
  STATUSES,
  type StepItem,
  type StepView,
  type Suite,
  type TcProject,
  type TestCase,
  TYPE_LABEL,
  caseKey,
  splitList,
  stepsFromText,
} from "@/lib/testcases";

/*
 * Testcase (docs/specs/30): the parts of the page. A case opens in a side drawer (read, edit,
 * history, results of the runs); a suite is made / renamed / shared in small modals; a Google
 * Sheet or an .xlsx / .csv is imported in two steps (read the tabs, check the columns, import).
 * Cases follow TestRail (§13): a fixed key C123, a section, template Text or Steps (one expected
 * result per step, shared steps), type, estimate, milestone.
 */

export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function PriorityPill({ value }: { value: TestCase["priority"] }) {
  return <Pill tone={PRIORITY_TONE[value]}>{PRIORITY_LABEL[value]}</Pill>;
}

export function StatusPill({ value }: { value: TestCase["status"] }) {
  return <Pill tone={STATUS_TONE[value]}>{STATUS_LABEL[value]}</Pill>;
}

export function ResultPill({ value }: { value: keyof typeof RESULT_LABEL }) {
  return <Pill tone={RESULT_TONE[value]}>{RESULT_LABEL[value]}</Pill>;
}

/** Multi-line text as written (steps keep their lines). */
function Lines({ text }: { text: string | null | undefined }) {
  if (!text) return <span className="muted">—</span>;
  return <div className="tc-lines">{text}</div>;
}

/** "Xuất ▾": Excel, CSV, TestRail CSV of the suite (or of every suite). */
export function ExportMenu({ href }: { href: (format: "xlsx" | "csv" | "testrail") => string }) {
  const box = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent | KeyboardEvent) => {
      const el = box.current;
      if (!el?.open) return;
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !el.contains(e.target as Node)) el.open = false;
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", away);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", away);
    };
  }, []);
  const items: [string, "xlsx" | "csv" | "testrail"][] = [
    ["Excel (.xlsx)", "xlsx"],
    ["CSV", "csv"],
    ["TestRail CSV", "testrail"],
  ];
  return (
    <details className="llm-acc-more docs-more tc-export" ref={box}>
      <summary className="button sm">
        <Icon name="download" size={14} /> Xuất <Icon name="chevronDown" size={12} />
      </summary>
      <div className="llm-acc-menu" role="menu">
        {items.map(([label, f]) => (
          <a
            key={f}
            role="menuitem"
            href={href(f)}
            download
            onClick={() => {
              if (box.current) box.current.open = false;
            }}
          >
            <Icon name="download" size={14} /> {label}
          </a>
        ))}
      </div>
    </details>
  );
}

const VIA_LABEL: Record<string, string> = { editor: "Sửa", import: "Nhập lại", ai: "AI", restore: "Khôi phục", bulk: "Sửa hàng loạt" };

/* ---- the case form ---- */

let uidSeq = 0;
const nextUid = () => ++uidSeq;

/** A row of the steps editor: an own step, or a block of shared steps. */
type StepRow = { uid: number; content: string; expected: string } | { uid: number; shared: number };

type Draft = {
  section_id: string;
  title: string;
  template: CaseTemplate;
  preconditions: string;
  steps: string;
  test_data: string;
  expected: string;
  steps_list: StepRow[];
  notes: string;
  priority: TestCase["priority"];
  type: CaseType;
  kind: TestCase["kind"];
  estimate: string;
  milestone_id: string;
  automation: string;
  automation_id: string;
  tags: string;
  jira_keys: string;
  code_paths: string;
  status: TestCase["status"];
};

const toRows = (items: StepItem[] | undefined): StepRow[] =>
  (items ?? []).map((s) => ("shared" in s ? { uid: nextUid(), shared: s.shared } : { uid: nextUid(), content: s.content ?? "", expected: s.expected ?? "" }));

function toDraft(c?: Partial<TestCase>, sectionId?: number | null): Draft {
  return {
    section_id: c ? (c.section_id ? String(c.section_id) : "") : sectionId ? String(sectionId) : "",
    title: c?.title ?? "",
    template: c?.template ?? "text",
    preconditions: c?.preconditions ?? "",
    steps: c?.steps ?? "",
    test_data: c?.test_data ?? "",
    expected: c?.expected ?? "",
    steps_list: toRows(c?.steps_list),
    notes: c?.notes ?? "",
    priority: c?.priority ?? "medium",
    type: c?.type ?? "functional",
    kind: c?.kind ?? "manual",
    estimate: c?.estimate ?? "",
    milestone_id: c?.milestone_id ? String(c.milestone_id) : "",
    automation: c?.automation ?? "",
    automation_id: c?.automation_id ?? "",
    tags: (c?.tags ?? []).join(", "),
    jira_keys: (c?.jira_keys ?? []).join(", "),
    code_paths: (c?.code_paths ?? []).join("\n"),
    status: c?.status ?? "draft",
  };
}

function fromDraft(d: Draft): CaseValues {
  return {
    section_id: d.section_id ? Number(d.section_id) : null,
    title: d.title,
    template: d.template,
    preconditions: d.preconditions,
    steps: d.steps,
    test_data: d.test_data,
    expected: d.expected,
    steps_list: d.steps_list
      .map((r): StepItem | null => ("shared" in r ? { shared: r.shared } : r.content.trim() || r.expected.trim() ? { content: r.content, expected: r.expected } : null))
      .filter((r): r is StepItem => r !== null),
    notes: d.notes,
    priority: d.priority,
    type: d.type,
    kind: d.kind,
    estimate: d.estimate.trim() || null,
    milestone_id: d.milestone_id ? Number(d.milestone_id) : null,
    automation: d.automation,
    automation_id: d.automation_id.trim() || null,
    tags: splitList(d.tags),
    jira_keys: splitList(d.jira_keys),
    code_paths: splitList(d.code_paths),
    status: d.status,
  };
}

/** The steps table of template "steps": own steps (step + expected) and shared blocks. */
function StepsEditor({ rows, onChange, shared }: { rows: StepRow[]; onChange: (rows: StepRow[]) => void; shared: SharedSteps[] | undefined }) {
  const [pick, setPick] = useState("");
  const byId = useMemo(() => new Map((shared ?? []).map((s) => [s.id, s])), [shared]);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= rows.length) return;
    const next = [...rows];
    const [r] = next.splice(from, 1);
    next.splice(to, 0, r);
    onChange(next);
  };
  const rowProps = useDragList(move);
  const set = (i: number, patch: Partial<{ content: string; expected: string }>) => onChange(rows.map((r, j) => (j === i && !("shared" in r) ? { ...r, ...patch } : r)));
  let n = 0;
  return (
    <div className="tc-steps-edit">
      {rows.length > 0 && (
        <div className="tc-step-head small muted" aria-hidden="true">
          <span />
          <span>Bước</span>
          <span>Kết quả mong đợi</span>
          <span />
        </div>
      )}
      <ol className="tc-step-list">
        {rows.map((r, i) => {
          const first = n + 1;
          if ("shared" in r) {
            const s = byId.get(r.shared);
            n += Math.max(1, s?.steps.length ?? 1);
            const name = s ? `${s.key} · ${s.title}` : `SS${r.shared}`;
            return (
              <li key={r.uid} className="tc-step-row shared" {...rowProps(i)}>
                <div className="tc-step-side">
                  <span className="tc-step-no">{first}</span>
                  <ReorderControls index={i} count={rows.length} name={`bước dùng chung ${name}`} onMove={(to) => move(i, to)} />
                </div>
                <div className="tc-step-shared">
                  <div className="tc-step-shared-head">
                    <Icon name="link" size={12} /> <strong>Bước dùng chung</strong> <span className="tc-codes">{name}</span>
                  </div>
                  {s ? (
                    <ol start={first} className="small">
                      {s.steps.map((st, k) => (
                        <li key={k}>
                          {st.content}
                          {st.expected ? <span className="muted"> → {st.expected}</span> : null}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <span className="small muted">{shared ? "Không tìm thấy bộ bước dùng chung này." : "Đang tải…"}</span>
                  )}
                </div>
                <IconButton icon="remove" tone="danger" label={`Bỏ bước dùng chung ${name}`} className="tip-left" onClick={() => onChange(rows.filter((_, j) => j !== i))} />
              </li>
            );
          }
          n += 1;
          return (
            <li key={r.uid} className="tc-step-row" {...rowProps(i)}>
              <div className="tc-step-side">
                <span className="tc-step-no">{first}</span>
                <ReorderControls index={i} count={rows.length} name={`bước ${first}`} onMove={(to) => move(i, to)} />
              </div>
              <label className="tc-step-cell">
                <span className="tc-step-label small muted">Bước</span>
                <textarea rows={2} value={r.content} aria-label={`Bước ${first}`} onChange={(e) => set(i, { content: e.target.value })} />
              </label>
              <label className="tc-step-cell">
                <span className="tc-step-label small muted">Kết quả mong đợi</span>
                <textarea rows={2} value={r.expected} aria-label={`Kết quả mong đợi của bước ${first}`} onChange={(e) => set(i, { expected: e.target.value })} />
              </label>
              <IconButton icon="remove" tone="danger" label={`Xoá bước ${first}`} className="tip-left" onClick={() => onChange(rows.filter((_, j) => j !== i))} />
            </li>
          );
        })}
      </ol>
      {!rows.length && <p className="small muted">Chưa có bước nào.</p>}
      <div className="tc-step-add">
        <button type="button" className="sm" onClick={() => onChange([...rows, { uid: nextUid(), content: "", expected: "" }])}>
          <Icon name="plus" size={14} /> Thêm bước
        </button>
        {shared && shared.length > 0 ? (
          <span className="tc-inline">
            <label className="filter">
              <span className="sr-only">Bước dùng chung</span>
              <select value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">Chọn bước dùng chung…</option>
                {shared.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.key} · {s.title} ({s.steps.length} bước)
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="ghost sm"
              disabled={!pick}
              onClick={() => {
                onChange([...rows, { uid: nextUid(), shared: Number(pick) }]);
                setPick("");
              }}
            >
              Chèn bước dùng chung
            </button>
          </span>
        ) : shared ? (
          <span className="small muted">Dự án chưa có bước dùng chung.</span>
        ) : null}
      </div>
    </div>
  );
}

function CaseForm({
  draft,
  set,
  sections,
  shared,
  milestones,
  origin,
}: {
  draft: Draft;
  set: (d: Draft) => void;
  sections: Section[] | undefined;
  shared: SharedSteps[] | undefined;
  milestones: CaseMilestone[] | undefined;
  /** The key and "Mã gốc" of a saved case (read only). */
  origin?: { key: string; code: string | null };
}) {
  const field = (k: Exclude<keyof Draft, "steps_list">) => ({
    value: draft[k],
    onChange: (e: { target: { value: string } }) => set({ ...draft, [k]: e.target.value }),
  });
  const split = stepsFromText(draft.steps, draft.expected);
  const toSteps = () => set({ ...draft, template: "steps", steps_list: [...draft.steps_list, ...split.map((s) => ({ uid: nextUid(), ...s }))] });
  const switchTemplate = (t: CaseTemplate) => {
    if (t === "steps" && !draft.steps_list.length) {
      // Start the table from the numbered text when there is one, else with an empty step.
      set({ ...draft, template: "steps", steps_list: split.length ? split.map((s) => ({ uid: nextUid(), ...s })) : [{ uid: nextUid(), content: "", expected: "" }] });
    } else set({ ...draft, template: t });
  };
  const sectionMissing = draft.section_id && sections && !sections.some((s) => String(s.id) === draft.section_id);
  return (
    <div className="adm-form tc-form">
      {origin && (
        <p className="small muted tc-origin">
          Mã <span className="tc-key">{origin.key}</span>
          {origin.code ? (
            <>
              {" "}
              · Mã gốc <span className="tc-codes">{origin.code}</span>
            </>
          ) : null}
        </p>
      )}
      <label className="adm-field">
        <span className="small muted">Tiêu đề *</span>
        <input {...field("title")} maxLength={500} autoFocus />
      </label>
      <div className="tc-form-row">
        <label className="adm-field">
          <span className="small muted">Thư mục</span>
          <select {...field("section_id")} disabled={!sections}>
            <SectionOptions sections={sections ?? []} />
            {sectionMissing && <option value={draft.section_id}>Thư mục hiện tại</option>}
          </select>
        </label>
        <div className="adm-field">
          <span className="small muted">
            Template
          </span>
          <Segmented<CaseTemplate>
            label="Template"
            value={draft.template}
            onChange={switchTemplate}
            options={[
              { value: "text", label: "Văn bản" },
              { value: "steps", label: "Từng bước" },
            ]}
          />
        </div>
      </div>
      <label className="adm-field">
        <span className="small muted">Tiền điều kiện</span>
        <textarea {...field("preconditions")} rows={2} />
      </label>
      {draft.template === "text" ? (
        <>
          <label className="adm-field">
            <span className="small muted">Các bước (mỗi bước một dòng)</span>
            <textarea {...field("steps")} rows={5} placeholder={"1. …\n2. …"} />
          </label>
          <label className="adm-field">
            <span className="small muted">Kết quả mong đợi</span>
            <textarea {...field("expected")} rows={3} />
          </label>
          {split.length > 0 && (
            <div className="tc-inline">
              <button type="button" className="ghost sm" onClick={toSteps}>
                Tách thành {split.length} bước
              </button>
              <span className="small muted">Chuyển sang dạng từng bước, mỗi bước một kết quả mong đợi.</span>
            </div>
          )}
        </>
      ) : (
        <fieldset className="tc-fieldset">
          <legend className="small muted">Các bước</legend>
          <StepsEditor rows={draft.steps_list} onChange={(steps_list) => set({ ...draft, steps_list })} shared={shared} />
        </fieldset>
      )}
      <label className="adm-field">
        <span className="small muted">Dữ liệu test</span>
        <textarea {...field("test_data")} rows={2} />
      </label>
      <div className="tc-form-row three">
        <label className="adm-field">
          <span className="small muted">Type</span>
          <select {...field("type")}>
            {CASE_TYPES.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Ưu tiên</span>
          <select {...field("priority")}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABEL[p]}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Ước tính</span>
          <input {...field("estimate")} maxLength={40} placeholder="30s, 5m, 1h 30m" />
        </label>
      </div>
      <div className="tc-form-row three">
        <label className="adm-field">
          <span className="small muted">Milestone</span>
          <select {...field("milestone_id")} disabled={!milestones}>
            <option value="">Không gắn</option>
            {(milestones ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.key} · {m.name}
                {m.completed_at ? " (đã xong)" : ""}
              </option>
            ))}
            {draft.milestone_id && milestones && !milestones.some((m) => String(m.id) === draft.milestone_id) && <option value={draft.milestone_id}>M{draft.milestone_id}</option>}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Cách chạy</span>
          <select {...field("kind")}>
            <option value="manual">{KIND_LABEL.manual}</option>
            <option value="automated">{KIND_LABEL.automated}</option>
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Trạng thái</span>
          <select {...field("status")}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="tc-form-row">
        <label className="adm-field">
          <span className="small muted">Nhãn (cách nhau dấu phẩy)</span>
          <input {...field("tags")} placeholder="smoke, regression" />
        </label>
        <label className="adm-field">
          <span className="small muted">Jira (References)</span>
          <input {...field("jira_keys")} placeholder="OMX-12, OMX-13" />
        </label>
      </div>
      <label className="adm-field">
        <span className="small muted">
          Vùng code phủ (mỗi dòng một mẫu, dạng repo:glob hoặc glob) — để gợi ý test lại khi MR đổi các file này. Kế thừa thêm vùng của thư mục và bộ; ghi !mẫu để bỏ một vùng kế thừa
        </span>
        <textarea {...field("code_paths")} rows={2} placeholder={"customer-service:src/payment/**\n**/checkout*.ts"} />
      </label>
      <label className="adm-field">
        <span className="small muted">File test tự động (đường dẫn trong repo, có thể kèm ::tên test)</span>
        <input {...field("automation")} maxLength={500} placeholder="tests/test_refund.py::test_partial" />
      </label>
      <label className="adm-field">
        <span className="small muted">Automation ID (tên test trong báo cáo CI, dạng classname.name; duy nhất trong bộ)</span>
        <input {...field("automation_id")} maxLength={500} placeholder="tests.test_refund.test_partial" />
      </label>
      <label className="adm-field">
        <span className="small muted">Ghi chú</span>
        <textarea {...field("notes")} rows={2} />
      </label>
    </div>
  );
}

/** The steps of template "steps" as a numbered table; shared blocks marked. */
export function StepsTable({ steps }: { steps: StepView[] }) {
  if (!steps.length) return <span className="muted">—</span>;
  let lastShared: number | null = null;
  const rows: ReactNode[] = [];
  steps.forEach((s, i) => {
    const sid = s.shared_id ?? null;
    if (sid !== null && sid !== lastShared)
      rows.push(
        <tr key={`h${i}`} className="tc-step-shared-row">
          <td colSpan={3} className="small">
            <Icon name="link" size={12} /> Bước dùng chung: <strong>{s.shared_title ?? `SS${sid}`}</strong>
          </td>
        </tr>,
      );
    lastShared = sid;
    rows.push(
      <tr key={i} className={sid !== null ? "shared" : undefined}>
        <td className="num">{i + 1}</td>
        <td>
          <Lines text={s.content} />
        </td>
        <td>
          <Lines text={s.expected} />
        </td>
      </tr>,
    );
  });
  return (
    <div className="table-wrap">
      <table className="tc-steps-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Bước</th>
            <th>Kết quả mong đợi</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}

/** Results of a case in every run (newest first). */
function CaseResults({ caseId, projectId }: { caseId: number; projectId: number | null }) {
  const results = useApi<CaseResult[]>(`/testcases/${caseId}/results`);
  return (
    <section className="tc-results" aria-labelledby="tc-results-title">
      <h3 className="tc-h3" id="tc-results-title">
        Kết quả chạy
      </h3>
      {!results.data ? (
        <Loading error={results.error} rows={2} />
      ) : !results.data.length ? (
        <p className="small muted">Chưa chạy trong test run nào.</p>
      ) : (
        <ul className="tc-result-list">
          {results.data.map((r) => (
            <li key={r.result.id}>
              <div className="tc-result-head">
                <ResultPill value={r.result.status} />
                {projectId ? (
                  <a className="link" href={`/testcases?project=${projectId}&tab=runs&run=${r.run_id}`}>
                    <span className="tc-key">{r.run_key}</span> {r.run_name}
                  </a>
                ) : (
                  <span>
                    <span className="tc-key">{r.run_key}</span> {r.run_name}
                  </span>
                )}
                {r.config && <span className="small muted">{r.config}</span>}
                {r.completed && <Pill>Đã đóng</Pill>}
              </div>
              <div className="small muted">
                {r.result.created_by?.name ?? "—"} · {dateTimeMin(r.result.created_at)}
                {r.result.version ? ` · bản ${r.result.version}` : ""}
                {r.result.elapsed ? ` · ${r.result.elapsed}` : ""}
                {r.result.defects.length ? " · lỗi " : ""}
                <BugPills keys={r.result.defects} bp={projectId} />
              </div>
              {r.result.comment && <div className="tc-lines small">{r.result.comment}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * One case: read it, edit it, look back at its history and its results. `caseId` null: a new
 * case of `suite` (in `sectionId` when given). `projectId` (the dự án shown) finds the shared
 * steps and milestones; without it the suite's dự án is used.
 */
export function CaseDrawer({
  caseId,
  suite,
  projectId,
  sectionId,
  onClose,
  onSaved,
}: {
  caseId: number | null;
  suite: Suite | null;
  /** Kept for older callers: sections now come from the suite's tree. */
  sections?: string[];
  projectId?: number;
  sectionId?: number | null;
  onClose: () => void;
  onSaved: (c?: TestCase) => void;
}) {
  const detail = useApi<CaseDetail>(caseId ? `/testcases/${caseId}` : null);
  const [editing, setEditing] = useState(caseId === null);
  const [draft, setDraft] = useState<Draft>(() => toDraft(undefined, sectionId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const c = detail.data;
  const pid = projectId ?? suite?.business_project_id ?? null;
  const suiteOf = c?.suite_id ?? suite?.id ?? null;
  const sections = useApi<Section[]>(editing && suiteOf ? `/testcases/sections?suite_id=${suiteOf}` : null);
  const shared = useApi<SharedSteps[]>(editing && pid ? `/testcases/shared-steps?business_project_id=${pid}` : null);
  const milestones = useApi<CaseMilestone[]>(pid ? `/testcases/milestones?business_project_id=${pid}` : null);

  useEffect(() => {
    if (c && !editing) setDraft(toDraft(c));
  }, [c, editing]);

  const save = async () => {
    if (!draft.title.trim()) {
      setError("Testcase cần tiêu đề.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (c) {
        const got = await api.patch<CaseDetail>(`/testcases/${c.id}`, { ...fromDraft(draft), revision: c.revision });
        await detail.mutate(got, false);
        setEditing(false);
        onSaved(got);
      } else if (suite) {
        const got = await api.post<TestCase>("/testcases", { ...fromDraft(draft), suite_id: suite.id });
        onSaved(got);
        onClose();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const restore = async (n: number) => {
    if (!c) return;
    setBusy(true);
    try {
      const got = await api.post<CaseDetail>(`/testcases/${c.id}/restore/${n}`);
      await detail.mutate(got, false);
      onSaved(got);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!c) return;
    setBusy(true);
    try {
      await api.delete(`/testcases/${c.id}`);
      onSaved();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const key = c ? caseKey(c) : "";
  const title = c ? `${key} · ${c.title}` : "Testcase mới";
  const milestone = c?.milestone_id ? milestones.data?.find((m) => m.id === c.milestone_id) : undefined;
  const steps: StepView[] | null =
    c?.template === "steps"
      ? c.steps_view ?? (c.steps_list ?? []).filter((s): s is { content: string; expected?: string | null } => !("shared" in s)).map((s) => ({ content: s.content, expected: s.expected ?? null }))
      : null;
  const actions = editing ? (
    <>
      {c && (
        <button className="ghost sm" onClick={() => setEditing(false)} disabled={busy}>
          Huỷ
        </button>
      )}
      <button className="primary sm" onClick={() => void save()} disabled={busy}>
        {busy ? "Đang lưu…" : "Lưu"}
      </button>
    </>
  ) : c?.can_edit ? (
    <>
      <button className="ghost sm" onClick={() => setConfirming(true)} disabled={busy}>
        Xoá
      </button>
      <button className="sm" onClick={() => setEditing(true)}>
        <Icon name="edit" size={14} /> Sửa
      </button>
    </>
  ) : null;

  return (
    <Drawer title={title} onClose={onClose} width={760} actions={actions}>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {confirming && c && (
        <Alert tone="warning" onClose={() => setConfirming(false)}>
          Xoá testcase {key}? Không khôi phục được — nếu chỉ là không dùng nữa, nên đổi trạng thái sang “Lỗi thời”.{" "}
          <button className="link sm error" onClick={() => void remove()} disabled={busy}>
            Xoá hẳn
          </button>
        </Alert>
      )}
      {caseId && !c ? (
        <Loading error={detail.error} rows={6} />
      ) : editing ? (
        <CaseForm
          draft={draft}
          set={setDraft}
          sections={sections.data}
          shared={pid ? shared.data : []}
          milestones={pid ? milestones.data : []}
          origin={c ? { key, code: c.code } : undefined}
        />
      ) : c ? (
        <div className="tc-view">
          <div className="tc-view-pills">
            <PriorityPill value={c.priority} />
            <StatusPill value={c.status} />
            {c.type && <Pill>{TYPE_LABEL[c.type]}</Pill>}
            <Pill>{KIND_LABEL[c.kind]}</Pill>
            {c.estimate && <Pill icon="history">{c.estimate}</Pill>}
            {c.source === "ai" && <Pill tone="primary" icon="sparkle">AI</Pill>}
            {c.problems.map((p) => (
              <Pill key={p} tone="warn" icon="alert">
                {p}
              </Pill>
            ))}
          </div>
          <dl className="tc-dl">
            <dt>Mã</dt>
            <dd>
              <span className="tc-key">{key}</span>
              {c.code ? (
                <span className="small muted">
                  {" "}
                  · Mã gốc <span className="tc-codes">{c.code}</span>
                </span>
              ) : null}
            </dd>
            <dt>Thư mục</dt>
            <dd>{c.section || <span className="muted">Gốc của bộ</span>}</dd>
            {c.milestone_id ? (
              <>
                <dt>Milestone</dt>
                <dd>{milestone ? `${milestone.key} · ${milestone.name}` : `M${c.milestone_id}`}</dd>
              </>
            ) : null}
            <dt>Tiền điều kiện</dt>
            <dd>
              <Lines text={c.preconditions} />
            </dd>
            {steps ? (
              <>
                <dt>Các bước</dt>
                <dd>
                  <StepsTable steps={steps} />
                </dd>
              </>
            ) : (
              <>
                <dt>Các bước</dt>
                <dd>
                  <Lines text={c.steps} />
                </dd>
                <dt>Kết quả mong đợi</dt>
                <dd>
                  <Lines text={c.expected} />
                </dd>
              </>
            )}
            <dt>Dữ liệu test</dt>
            <dd>
              <Lines text={c.test_data} />
            </dd>
            {c.tags.length > 0 && (
              <>
                <dt>Nhãn</dt>
                <dd className="tc-tags">
                  {c.tags.map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </dd>
              </>
            )}
            {c.jira_keys.length > 0 && (
              <>
                <dt>Jira</dt>
                <dd>{c.jira_keys.join(", ")}</dd>
              </>
            )}
            {c.code_paths.length > 0 && (
              <>
                <dt>Vùng code</dt>
                <dd>
                  {c.code_paths.map((g) => (
                    <code key={g} className="tc-glob">
                      {g}
                    </code>
                  ))}
                </dd>
              </>
            )}
            {c.automation && (
              <>
                <dt>Test tự động</dt>
                <dd>
                  <code>{c.automation}</code>
                </dd>
              </>
            )}
            {(c.automation_id || c.flaky) && (
              <>
                <dt>Automation ID</dt>
                <dd>
                  {c.automation_id && <code>{c.automation_id}</code>} {c.flaky && <FlakyPill />}
                </dd>
              </>
            )}
            {c.notes && (
              <>
                <dt>Ghi chú</dt>
                <dd>
                  <Lines text={c.notes} />
                </dd>
              </>
            )}
          </dl>
          <CaseResults caseId={c.id} projectId={pid} />
          <p className="small muted">
            Phiên bản {c.revision}
            {c.updated_by ? ` · sửa lần cuối bởi ${c.updated_by.name}` : ""}
            {c.updated_at ? ` · ${dateTimeMin(c.updated_at)}` : ""}
          </p>
          {c.revisions.length > 0 && (
            <div className="tc-history">
              <button className="link sm" onClick={() => setShowHistory((v) => !v)} aria-expanded={showHistory}>
                <Icon name="history" size={14} /> Lịch sử ({c.revisions.length})
              </button>
              {showHistory && (
                <ul>
                  {c.revisions.map((r) => (
                    <li key={r.number}>
                      <div>
                        <strong>Bản {r.number}</strong> <span className="small muted">— {VIA_LABEL[r.via] ?? r.via}{r.changed_by ? ` bởi ${r.changed_by.name}` : ""}, {dateTimeMin(r.changed_at)}</span>
                      </div>
                      <div className="small">{String(r.snapshot.title ?? "")}</div>
                      {c.can_edit && (
                        <button className="link sm" onClick={() => void restore(r.number)} disabled={busy}>
                          Khôi phục bản này
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      ) : null}
    </Drawer>
  );
}


/** Make a suite, or rename one. */
export function SuiteModal({
  projectId,
  suite,
  onClose,
  onSaved,
}: {
  projectId: number;
  suite: Suite | null;
  onClose: () => void;
  onSaved: (s: Suite) => void;
}) {
  const [name, setName] = useState(suite?.name ?? "");
  const [description, setDescription] = useState(suite?.description ?? "");
  const [codePaths, setCodePaths] = useState((suite?.code_paths ?? []).join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = suite
        ? await api.patch<Suite>(`/testcases/suites/${suite.id}`, { name, description, code_paths: splitList(codePaths) })
        : await api.post<Suite>("/testcases/suites", { business_project_id: projectId, name, description });
      onSaved(got);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={suite ? "Đổi tên bộ testcase" : "Bộ testcase mới"}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            {suite ? "Lưu" : "Tạo bộ"}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) void save();
        }}
      >
        <label className="adm-field">
          <span className="small muted">Tên bộ (ví dụ: Đăng nhập, Thanh toán)</span>
          <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea value={description} rows={2} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
        </label>
        {suite && <CodePathsField projectId={projectId} value={codePaths} onChange={setCodePaths} />}
      </form>
    </Modal>
  );
}

/** The dự án sharing a suite (docs/specs/30 §3.1). */
export function ShareModal({ suite, projects, onClose, onSaved }: { suite: Suite; projects: TcProject[]; onClose: () => void; onSaved: (s: Suite) => void }) {
  const [chosen, setChosen] = useState<Set<number>>(new Set(suite.projects.filter((p) => p !== suite.business_project_id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    try {
      onSaved(await api.put<Suite>(`/testcases/suites/${suite.id}/projects`, { project_ids: [...chosen] }));
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Dự án dùng bộ testcase này"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy}>
            Lưu
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <p className="small muted">
        Bộ “{suite.name}” thuộc dự án <strong>{suite.home_name}</strong>. Chọn thêm dự án dùng chung (ví dụ BE, FE, Mobile cùng một sản phẩm): testcase hiện và được gợi ý test lại ở mọi dự án đã chọn.
      </p>
      <ul className="docs-share-list">
        {projects
          .filter((p) => p.id !== suite.business_project_id)
          .map((p) => (
            <li key={p.id}>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={chosen.has(p.id)}
                  disabled={!p.can_edit && !chosen.has(p.id)}
                  onChange={(e) => {
                    const next = new Set(chosen);
                    if (e.target.checked) next.add(p.id);
                    else next.delete(p.id);
                    setChosen(next);
                  }}
                />
                {p.name}
                {!p.can_edit && <span className="small muted"> (không có quyền sửa)</span>}
              </label>
            </li>
          ))}
      </ul>
    </Modal>
  );
}

/**
 * Columns of the TestRail model (§14.1) a sheet can map to, with their labels; the server's own
 * list (`ReadOut.fields`) wins when it names them.
 */
const IMPORT_FIELDS: Record<string, string> = {
  type: "Type",
  estimate: "Ước tính (Estimate)",
  template: "Template",
  step_content: "Bước (từng bước)",
  step_expected: "Kết quả mong đợi của bước",
  milestone: "Milestone",
};

/** Import from a Google Sheet link or an .xlsx / .csv: read, check the columns, import. */
export function ImportModal({
  projectId,
  suites,
  initialUrl,
  onClose,
  onDone,
}: {
  projectId: number;
  suites: Suite[];
  initialUrl?: string;
  onClose: () => void;
  onDone: (results: ImportResult[]) => void;
}) {
  const [url, setUrl] = useState(initialUrl ?? "");
  const [read, setRead] = useState<ReadOut | null>(null);
  const [picked, setPicked] = useState<Record<string, { on: boolean; suite: string; header: number; mapping: Record<string, number> }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = (got: ReadOut) => {
    setRead(got);
    const byName = new Map(suites.map((s) => [s.name.toLowerCase(), s.id]));
    setPicked(
      Object.fromEntries(
        got.sheets.map((s) => [
          s.name,
          {
            on: s.total > 0 && "title" in s.mapping,
            suite: String(byName.get(s.name.toLowerCase()) ?? "new"),
            header: s.header_row,
            mapping: s.mapping,
          },
        ]),
      ),
    );
  };

  const readUrl = async () => {
    setBusy(true);
    setError(null);
    try {
      accept(await api.post<ReadOut>("/testcases/import/read", { business_project_id: projectId, url: url.trim() }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const readFile = async (f: File) => {
    setBusy(true);
    setError(null);
    try {
      accept(await api.postRaw<ReadOut>(`/testcases/import/read-file?business_project_id=${projectId}&filename=${encodeURIComponent(f.name)}`, f));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    if (!read) return;
    const chosen = read.sheets.filter((s) => picked[s.name]?.on);
    setBusy(true);
    setError(null);
    try {
      const got = await api.post<{ suites: ImportResult[] }>("/testcases/import", {
        business_project_id: projectId,
        source_url: read.source_url,
        sheets: chosen.map((s) => {
          const p = picked[s.name];
          return {
            name: s.name,
            suite_id: p.suite === "new" ? null : Number(p.suite),
            suite_name: s.name,
            header_row: p.header,
            mapping: p.mapping,
            rows: s.rows,
          };
        }),
      });
      onDone(got.suites);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const ready = read && read.sheets.some((s) => picked[s.name]?.on && "title" in picked[s.name].mapping);
  return (
    <Modal
      title="Nhập testcase từ Google Sheet / Excel"
      onClose={onClose}
      busy={busy}
      width={880}
      footer={
        <>
          <button className="ghost" onClick={read ? () => setRead(null) : onClose} disabled={busy}>
            {read ? "Quay lại" : "Huỷ"}
          </button>
          {read && (
            <button className="primary" onClick={() => void run()} disabled={busy || !ready}>
              {busy ? "Đang nhập…" : "Nhập testcase"}
            </button>
          )}
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      {!read ? (
        <div className="adm-form">
          <label className="adm-field">
            <span className="small muted">Link Google Sheet (chia sẻ “Bất kỳ ai có đường liên kết đều xem được”)</span>
            <div className="tc-inline">
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" autoFocus />
              <button className="primary" onClick={() => void readUrl()} disabled={busy || !url.trim()}>
                {busy ? "Đang đọc…" : "Đọc sheet"}
              </button>
            </div>
          </label>
          <div className="small muted">hoặc tải file lên</div>
          <label className="tc-file">
            <Icon name="upload" size={16} />
            <span>Chọn file .xlsx / .csv (tối đa 10 MB)</span>
            <input
              type="file"
              accept=".xlsx,.csv"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void readFile(f);
                e.target.value = "";
              }}
            />
          </label>
          <p className="small muted">
            Mỗi tab thành một bộ testcase. CodePulse tự nhận cột theo tiêu đề (ID, Section, Tiêu đề, Các bước, Kết quả mong đợi, Type, Ưu tiên, Estimate, Jira…) và bỏ qua cột chạy test (Kết quả thực tế, Người test…). Cột thư mục dạng “A &gt; B” thành cây thư mục; file TestRail CSV nhiều dòng một testcase được nhận thành từng bước. Nhập lại: dòng có ID dạng C123 cập nhật đúng testcase đó, dòng khác ghép theo mã gốc; không xoá testcase nào.
          </p>
          <p className="small tc-templates">
            <Icon name="download" size={14} /> Tải file mẫu:{" "}
            <a className="link" href={`/api/v1/testcases/import/template?format=xlsx&business_project_id=${projectId}`} download>
              Excel (.xlsx)
            </a>{" "}
            ·{" "}
            <a className="link" href={`/api/v1/testcases/import/template?format=testrail&business_project_id=${projectId}`} download>
              TestRail CSV
            </a>
          </p>
        </div>
      ) : (
        <div className="tc-import">
          <p className="small muted">
            <strong>{read.title}</strong> — {read.sheets.length} tab. Kiểm tra cột được nhận, sửa nếu sai.
          </p>
          {read.sheets.map((s) => (
            <SheetPick
              key={s.name}
              sheet={s}
              fields={{ ...read.fields, ...Object.fromEntries(Object.entries(IMPORT_FIELDS).filter(([k]) => !(k in read.fields))) }}
              suites={suites}
              value={picked[s.name]}
              onChange={(v) => setPicked({ ...picked, [s.name]: v })}
            />
          ))}
        </div>
      )}
    </Modal>
  );
}

function SheetPick({
  sheet,
  fields,
  suites,
  value,
  onChange,
}: {
  sheet: ReadOut["sheets"][number];
  fields: Record<string, string>;
  suites: Suite[];
  value: { on: boolean; suite: string; header: number; mapping: Record<string, number> };
  onChange: (v: { on: boolean; suite: string; header: number; mapping: Record<string, number> }) => void;
}) {
  const header = value.header >= 0 ? sheet.rows[value.header] ?? [] : [];
  const width = Math.max(0, ...sheet.rows.slice(0, 12).map((r) => r.length));
  const columns = Array.from({ length: width }, (_, i) => i);
  const byColumn = useMemo(() => Object.fromEntries(Object.entries(value.mapping).map(([f, i]) => [i, f])), [value.mapping]);
  const sample = sheet.rows.slice(value.header + 1, value.header + 4);
  const setColumn = (col: number, field: string) => {
    const mapping = Object.fromEntries(Object.entries(value.mapping).filter(([f, i]) => i !== col && f !== field));
    if (field) mapping[field] = col;
    onChange({ ...value, mapping });
  };
  const rows = Math.max(0, sheet.rows.length - value.header - 1);
  return (
    <section className={`tc-sheet${value.on ? "" : " off"}`}>
      <div className="tc-sheet-head">
        <label className="inline-check">
          <input type="checkbox" checked={value.on} onChange={(e) => onChange({ ...value, on: e.target.checked })} />
          <strong>{sheet.name}</strong>
          <span className="small muted"> · {rows} dòng</span>
        </label>
        {value.on && (
          <>
            <label className="filter">
              <span className="small muted">Vào bộ</span>
              <select value={value.suite} onChange={(e) => onChange({ ...value, suite: e.target.value })}>
                <option value="new">Bộ mới “{sheet.name}”</option>
                {suites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="filter">
              <span className="small muted">Dòng tiêu đề</span>
              <select value={value.header} onChange={(e) => onChange({ ...value, header: Number(e.target.value) })}>
                <option value={-1}>Không có</option>
                {sheet.rows.slice(0, 10).map((_, i) => (
                  <option key={i} value={i}>
                    Dòng {i + 1}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>
      {value.on && (
        <>
          {!("title" in value.mapping) && <Alert tone="warning">Chọn cột nào là “Tiêu đề” để nhập được tab này.</Alert>}
          <div className="table-wrap">
            <table className="tc-map">
              <thead>
                <tr>
                  {columns.map((i) => (
                    <th key={i}>
                      <div className="small muted tc-map-head">{header[i] || `Cột ${i + 1}`}</div>
                      <select value={byColumn[i] ?? ""} onChange={(e) => setColumn(i, e.target.value)} aria-label={`Cột ${header[i] || i + 1} là`}>
                        <option value="">— Bỏ qua —</option>
                        {Object.entries(fields).map(([f, label]) => (
                          <option key={f} value={f}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sample.map((r, ri) => (
                  <tr key={ri}>
                    {columns.map((i) => (
                      <td key={i} className={byColumn[i] ? undefined : "muted"}>
                        <div className="tc-map-cell">{r[i] ?? ""}</div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
