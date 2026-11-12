"use client";

import { type DragEvent, type ReactNode, useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton, type IconName } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import { AiModal, RunDrawer, RunsCard, askDuplicates } from "@/components/TestcaseAi";
import {
  BULK_EDIT_LABEL,
  type BulkEditKind,
  BulkEditModal,
  BulkOpsModal,
  ColumnChooser,
  FieldFilter,
  ReplaceModal,
  type UndoNote,
  UndoNotice,
  bulkNote,
  useFieldColumns,
} from "@/components/TestcaseBulk";
import { GenerateFromIssue, askLinks } from "@/components/TestcaseLinks";
import { useFields, usePeople } from "@/components/TestFields";
import { CaseDrawer, ExportMenu, ImportModal, PriorityPill, ShareModal, StatusPill, SuiteModal, errorText } from "@/components/TestcaseParts";
import { CASE_DRAG_TYPE, SectionOptions, SectionTree } from "@/components/TestcaseSections";
import { CreateRunModal } from "@/components/TestRuns";
import { Alert, EmptyState, Loading, Message, Pill, Skeleton } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TestProjectSettings } from "@/lib/testruns";
import {
  AI_ACTIVE,
  type AiRun,
  type BulkOut,
  CASE_TYPES,
  type CaseList,
  type CaseMilestone,
  type ImportResult,
  KIND_LABEL,
  PRIORITIES,
  PRIORITY_LABEL,
  type RunList,
  STATUS_LABEL,
  STATUSES,
  type Section,
  type Stats,
  type Suite,
  type TcProject,
  type TestCase,
  TYPE_LABEL,
  caseKey,
  fieldText,
} from "@/lib/testcases";

/*
 * Tab "Testcase" of /testcases (docs/specs/30, TestRail model §13): suites and the chosen suite's
 * section tree on the left; on the right the cases grouped by section (flat while searching or
 * filtering), coverage, bulk changes, a test run from the ticked cases.
 */

type Filters = { q: string; priority: string; status: string; kind: string; type: string; milestone_id: string; tag: string; problem: boolean };
const NO_FILTERS: Filters = { q: "", priority: "", status: "", kind: "", type: "", milestone_id: "", tag: "", problem: false };
type RunInitial = { suite_id?: number; case_ids?: number[]; section_ids?: number[] };

export function CasesTab({
  project,
  projects,
  suiteId,
  onSuite,
  onChanged,
}: {
  project: TcProject;
  projects: TcProject[];
  suiteId: number | null;
  onSuite: (id: number | null) => void;
  onChanged: () => void;
}) {
  const [archived, setArchived] = useState(false);
  const suites = useApi<Suite[]>(`/testcases/suites?business_project_id=${project.id}${archived ? "&archived=true" : ""}`, { keepPreviousData: true });
  const suite = suites.data?.find((s) => s.id === suiteId) ?? null;
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [term, setTerm] = useState("");
  const [limit, setLimit] = useState(200);
  const [sectionId, setSectionId] = useState<number | null>(null);
  // §15.8: one custom field filter (f.<key>=<value>) and the custom columns shown.
  const [fieldFilter, setFieldFilter] = useState<{ key: string; value: string }>({ key: "", value: "" });
  const [columnKeys, setColumnKeys] = useFieldColumns(project.id);
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === term ? f : { ...f, q: term })), 300);
    return () => clearTimeout(t);
  }, [term]);
  useEffect(() => {
    setFilters(NO_FILTERS);
    setTerm("");
    setPicked(new Set());
    setLimit(200);
    setSectionId(null);
    setFieldFilter({ key: "", value: "" });
  }, [project.id, suiteId]);

  const query = new URLSearchParams({ business_project_id: String(project.id), limit: String(limit) });
  if (suiteId) query.set("suite_id", String(suiteId));
  if (suiteId && sectionId) query.set("section_id", String(sectionId));
  for (const [k, v] of Object.entries(filters)) if (v) query.set(k, String(v));
  if (fieldFilter.key && fieldFilter.value !== "") query.set(`f.${fieldFilter.key}`, fieldFilter.value);
  const cases = useApi<CaseList>(`/testcases?${query}`, { keepPreviousData: true });
  const sections = useApi<Section[]>(suite ? `/testcases/sections?suite_id=${suite.id}` : null);
  const milestones = useApi<CaseMilestone[]>(`/testcases/milestones?business_project_id=${project.id}`);
  const stats = useApi<Stats>(`/testcases/stats?business_project_id=${project.id}`);
  const runs = useApi<RunList>(project.can_edit ? `/testcases/ai?business_project_id=${project.id}` : null, {
    refreshInterval: (latest) => (latest?.runs.some((r) => AI_ACTIVE.includes(r.status)) ? 4000 : 0),
  });

  const fields = useFields(project.id, "case");
  const liveFields = useMemo(() => [...(fields.data ?? [])].filter((f) => !f.archived_at).sort((a, b) => a.position - b.position), [fields.data]);
  // Fields of this suite only, when one is chosen.
  const suiteFields = useMemo(() => liveFields.filter((f) => !suiteId || !f.suite_ids.length || f.suite_ids.includes(suiteId)), [liveFields, suiteId]);
  const shownFields = suiteFields.filter((f) => columnKeys.includes(f.key));
  const filterField = liveFields.find((f) => f.key === fieldFilter.key);
  const people = usePeople(project.id, shownFields.some((f) => f.type === "user") || filterField?.type === "user");
  const settings = useApi<TestProjectSettings>(`/testcases/project-settings?business_project_id=${project.id}`);
  const approvalOn = !!settings.data?.require_approval;
  // §15.9: tests.delete / tests.approve (older servers: tests.edit / tests.manage).
  const canDelete = project.can_delete ?? project.can_edit;
  const canApprove = project.can_approve ?? project.can_manage;

  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<number | "new" | null>(null);
  const [modal, setModal] = useState<null | "suite" | "rename" | "share" | "import" | "generate" | "audit" | "delete" | "replace" | "bulkops">(null);
  const [bulkEdit, setBulkEdit] = useState<BulkEditKind | null>(null);
  const [note, setNote] = useState<UndoNote | null>(null);
  const [run, setRun] = useState<number | null>(null);
  const [testRun, setTestRun] = useState<RunInitial | null>(null);
  // Cases asked to be deleted (one from its row, or the ticked ones): confirmed in a modal.
  const [deleting, setDeleting] = useState<TestCase[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dropOver, setDropOver] = useState<number | "root" | null>(null);

  const refresh = async () => {
    await Promise.all([cases.mutate(), suites.mutate(), stats.mutate(), sections.mutate()]);
    onChanged();
  };

  // After a bulk change: "Đã sửa N testcase · Hoàn tác" when the server gives a batch (§15.10).
  const bulkDone = (got: BulkOut, text: string) => {
    if (got.batch_id != null) {
      setMessage(null);
      setNote(bulkNote(got, text));
    } else {
      setNote(null);
      setMessage(`${text}.`);
    }
    setPicked(new Set());
    void refresh();
  };

  const bulk = async (body: Record<string, unknown>, ids: number[] = [...picked], done?: (n: number) => string) => {
    if (!ids.length) return;
    setBusy(true);
    try {
      const got = await api.post<BulkOut>("/testcases/bulk", { ids, ...body });
      bulkDone(got, done ? done(got.changed).replace(/\.$/, "") : `Đã sửa ${got.changed} testcase`);
    } catch (e) {
      setNote(null);
      setMessage(`Lỗi: ${errorText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const sectionName = (id: number | null) => (id === null ? "gốc của bộ" : `“${sections.data?.find((s) => s.id === id)?.path ?? ""}”`);
  const moveCases = (ids: number[], target: number | null) =>
    void bulk({ action: "section", section_id: target }, ids, (n) => `Đã chuyển ${n} testcase vào ${sectionName(target)}.`);

  const archive = async (s: Suite, value: boolean) => {
    try {
      await api.patch(`/testcases/suites/${s.id}`, { archived: value });
      setMessage(value ? `Đã lưu trữ bộ “${s.name}”.` : `Đã đưa bộ “${s.name}” trở lại.`);
      onSuite(null);
      await refresh();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  };

  const imported = (results: ImportResult[]) => {
    const parts = results.map(
      (r) =>
        `“${r.name}”: thêm ${r.created}, cập nhật ${r.updated}${r.unchanged ? `, giữ nguyên ${r.unchanged}` : ""}${r.skipped ? `, bỏ qua ${r.skipped} dòng không có tiêu đề` : ""}${r.missing ? `, ${r.missing} testcase không còn trong sheet (vẫn giữ)` : ""}`,
    );
    setMessage(`Đã nhập. ${parts.join("; ")}.`);
    if (results.length === 1) onSuite(results[0].suite_id);
    void refresh();
  };

  const asked = (r: AiRun) => {
    void runs.mutate();
    setRun(r.id);
  };
  // §15.2 G2: AI link suggestions over the suite (or the chosen section), reviewed in the run.
  const suggestLinks = async (id: number) => {
    try {
      asked(await askLinks(project.id, id, sectionId ?? null));
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  };
  // §15.10: the AI groups duplicate cases of the suite; the run drawer applies them.
  const findDuplicates = async (id: number) => {
    try {
      asked(await askDuplicates(project.id, id));
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  };
  const [fromIssue, setFromIssue] = useState<{ key: string; title: string | null } | null>(null);

  const list = cases.data;
  const all = useMemo(() => list?.items ?? [], [list]);
  const canEdit = project.can_edit;
  const suiteList = suites.data ?? [];
  const sectionList = sections.data;
  const filtering = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS) || term.trim() !== "" || (!!fieldFilter.key && fieldFilter.value !== "");
  const exportHref = (fmt: string) => `/api/v1/testcases/export?business_project_id=${project.id}${suite ? `&suite_id=${suite.id}` : ""}&format=${fmt}`;
  const chosenSection = sectionId !== null ? sectionList?.find((s) => s.id === sectionId) : undefined;
  const milestoneName = (id: number | null | undefined) => {
    const m = id ? milestones.data?.find((x) => x.id === id) : undefined;
    return m ? m.name : id ? `M${id}` : null;
  };

  // TestRail's view: one heading per section (tree order) followed by its cases; flat when filtering.
  const groups = useMemo(() => {
    if (!suite || !sectionList || filtering) return null;
    const bySection = new Map<number | null, TestCase[]>();
    for (const c of all) {
      const k = c.section_id ?? null;
      bySection.set(k, [...(bySection.get(k) ?? []), c]);
    }
    const out: { id: number | null; label: string; cases: TestCase[] }[] = [];
    if (bySection.has(null)) out.push({ id: null, label: "Gốc của bộ", cases: bySection.get(null) ?? [] });
    for (const s of sectionList) if (bySection.has(s.id)) out.push({ id: s.id, label: s.path, cases: bySection.get(s.id) ?? [] });
    const known = new Set([null, ...sectionList.map((s) => s.id)]);
    for (const [k, cs] of bySection) if (!known.has(k)) out.push({ id: k, label: cs[0]?.section ?? "Thư mục khác", cases: cs });
    return out;
  }, [suite, sectionList, filtering, all]);

  const dragCases = (c: TestCase) => (e: DragEvent) => {
    const ids = picked.has(c.id) ? [...picked] : [c.id];
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(CASE_DRAG_TYPE, JSON.stringify(ids));
    e.dataTransfer.setData("text/plain", ids.length > 1 ? `${ids.length} testcase` : `${caseKey(c)} ${c.title}`);
  };
  const dropOn = (target: number | null) => {
    const key = target ?? "root";
    return {
      onDragOver: (e: DragEvent) => {
        if (!canEdit || ![...e.dataTransfer.types].includes(CASE_DRAG_TYPE)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setDropOver(key);
      },
      onDragLeave: () => setDropOver((o) => (o === key ? null : o)),
      onDrop: (e: DragEvent) => {
        e.preventDefault();
        setDropOver(null);
        const raw = e.dataTransfer.getData(CASE_DRAG_TYPE);
        try {
          const ids = (JSON.parse(raw) as number[]).filter((n) => typeof n === "number");
          if (ids.length) moveCases(ids, target);
        } catch {
          /* not a drag of case rows */
        }
      },
      "data-over": dropOver === key ? "" : undefined,
    };
  };

  const menu: { icon: IconName; label: string; danger?: boolean; run: () => void }[] = suite
    ? [
        ...(suite.total && project.can_manage ? [{ icon: "clipboardCheck" as const, label: "Tạo test run cho bộ", run: () => setTestRun({ suite_id: suite.id }) }] : []),
        ...(suite.can_edit ? [{ icon: "edit" as const, label: "Đổi tên", run: () => setModal("rename") }] : []),
        ...(suite.can_edit ? [{ icon: "link" as const, label: "Dự án dùng chung", run: () => setModal("share") }] : []),
        ...(suite.can_edit && suite.source_url ? [{ icon: "sync" as const, label: "Nhập lại từ Google Sheet", run: () => setModal("import") }] : []),
        ...(suite.can_edit && suite.total ? [{ icon: "sparkle" as const, label: "Rà soát bằng AI", run: () => setModal("audit") }] : []),
        ...(suite.can_edit && suite.total ? [{ icon: "link" as const, label: "Gợi ý Jira / vùng code bằng AI", run: () => void suggestLinks(suite.id) }] : []),
        ...(suite.can_edit && suite.total > 1 ? [{ icon: "sparkle" as const, label: "Tìm trùng lặp bằng AI", run: () => void findDuplicates(suite.id) }] : []),
        ...(suite.can_edit && !suite.archived && suite.total ? [{ icon: "search" as const, label: "Tìm & thay", run: () => setModal("replace") }] : []),
        ...(suite.can_edit ? [{ icon: "folder" as const, label: suite.archived ? "Đưa trở lại" : "Lưu trữ", run: () => void archive(suite, !suite.archived) }] : []),
        ...(suite.can_delete ? [{ icon: "remove" as const, label: "Xoá bộ", danger: true, run: () => setModal("delete") }] : []),
      ]
    : [];
  if (canEdit) menu.push({ icon: "history" as const, label: "Lịch sử thao tác hàng loạt", run: () => setModal("bulkops") });

  const showDelete = canEdit && canDelete;
  const cols = 7 + (canEdit ? 1 : 0) + (showDelete ? 1 : 0) + shownFields.length;
  const row = (c: TestCase) => (
    <tr
      key={c.id}
      className={`tc-case-row${c.status === "obsolete" ? " muted" : ""}${picked.has(c.id) ? " picked" : ""}`}
      draggable={canEdit && !!suite}
      onDragStart={dragCases(c)}
    >
      {canEdit && (
        <td className="tc-check">
          <input
            type="checkbox"
            aria-label={`Chọn ${caseKey(c)}`}
            checked={picked.has(c.id)}
            onChange={(e) => {
              const next = new Set(picked);
              if (e.target.checked) next.add(c.id);
              else next.delete(c.id);
              setPicked(next);
            }}
          />
        </td>
      )}
      <td className="num tc-code-cell">
        <span className="tc-key">{caseKey(c)}</span>
      </td>
      <td>
        <button className="link tc-row-title" onClick={() => setOpen(c.id)}>
          {c.title}
        </button>
        <div className="small muted tc-row-sub">
          {!suiteId && <span>{suiteList.find((s) => s.id === c.suite_id)?.name}</span>}
          {(!groups || !suite) && c.section && (
            <span className="tc-row-section">
              <Icon name="folder" size={12} /> {c.section}
            </span>
          )}
          {c.milestone_id ? <span>{milestoneName(c.milestone_id)}</span> : null}
          {c.tags.map((t) => (
            <span key={t} className="tag">
              {t}
            </span>
          ))}
          {c.source === "ai" && (
            <span className="tc-ai-mark">
              <Icon name="sparkle" size={12} /> AI
            </span>
          )}
          {c.problems.length > 0 && (
            <span className="warn" title={c.problems.join(", ")}>
              <Icon name="alert" size={12} /> {c.problems.join(", ")}
            </span>
          )}
        </div>
      </td>
      <td className="small tc-nowrap">{c.type ? TYPE_LABEL[c.type] : "—"}</td>
      <td>
        <PriorityPill value={c.priority} />
      </td>
      <td className="small tc-nowrap">{c.estimate ?? <span className="muted">—</span>}</td>
      <td>
        <StatusPill value={c.status} />
      </td>
      <td className="small tc-nowrap">{c.jira_keys.join(", ")}</td>
      {shownFields.map((f) => (
        <td key={f.key} className="small tc-field-cell">
          {fieldText(f, c.custom?.[f.key], people.data)}
        </td>
      ))}
      {showDelete && (
        <td className="tc-row-actions">
          <IconButton icon="remove" tone="danger" label={`Xoá testcase ${caseKey(c)}`} className="tip-left" onClick={() => setDeleting([c])} />
        </td>
      )}
    </tr>
  );

  return (
    <div className="tc-shell">
      <aside className="card tc-nav" aria-label="Bộ testcase">
        <div className="tc-nav-head">
          <strong>Bộ testcase</strong>
          {canEdit && (
            <span className="tc-nav-actions">
              <button className="icon-btn" aria-label="Nhập từ Google Sheet / Excel" data-tip="Nhập từ Google Sheet / Excel" onClick={() => setModal("import")}>
                <Icon name="upload" size={16} />
              </button>
              <button className="icon-btn" aria-label="Thêm bộ testcase" data-tip="Thêm bộ testcase" onClick={() => setModal("suite")}>
                <Icon name="plus" size={16} />
              </button>
            </span>
          )}
        </div>
        {!suites.data ? (
          <Loading error={suites.error} rows={4} />
        ) : (
          <ul className="tc-suites">
            {!archived && (
              <li>
                <button className={suiteId === null ? "active" : undefined} aria-current={suiteId === null ? "true" : undefined} onClick={() => onSuite(null)}>
                  <span className="tc-suite-name">Tất cả</span>
                  <span className="small muted">{suiteList.reduce((n, s) => n + s.total - s.obsolete, 0)}</span>
                </button>
              </li>
            )}
            {suiteList.map((s) => (
              <li key={s.id}>
                <button className={s.id === suiteId ? "active" : undefined} aria-current={s.id === suiteId ? "true" : undefined} onClick={() => onSuite(s.id)} title={s.description ?? undefined}>
                  <span className="tc-suite-name">{s.name}</span>
                  {s.shared && <Icon name="link" size={12} />}
                  <span className="small muted">{s.total - s.obsolete}</span>
                </button>
              </li>
            ))}
            {!suiteList.length && (
              <li className="small muted tc-empty-nav">
                {archived ? "Không có bộ nào đã lưu trữ." : canEdit ? "Chưa có bộ nào: nhập từ Google Sheet hoặc tạo bộ mới." : "Chưa có bộ testcase nào."}
              </li>
            )}
          </ul>
        )}
        <label className="inline-check small tc-nav-archived">
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => {
              setArchived(e.target.checked);
              onSuite(null);
            }}
          />
          Bộ đã lưu trữ
        </label>
        {suite && (
          <SectionTree
            suite={suite}
            sections={sectionList}
            error={sections.error}
            selected={sectionId}
            onSelect={setSectionId}
            canEdit={suite.can_edit && !suite.archived}
            onChanged={() => refresh()}
            onMoveCases={moveCases}
            onCreateRun={project.can_manage ? (id) => setTestRun({ suite_id: suite.id, section_ids: [id] }) : undefined}
            onMessage={setMessage}
          />
        )}
      </aside>

      <section className="tc-main">
        <Message text={message} onClose={() => setMessage(null)} />
        <UndoNotice note={note} onClose={() => setNote(null)} onUndone={() => void refresh()} />
        <CoverageStrip stats={stats.data} error={stats.error} onGenerate={canEdit && (suites.data ?? []).some((s) => s.can_edit) ? setFromIssue : undefined} />

        <div className="tc-head">
          <div className="tc-title">
            <h2>{suite ? suite.name : "Tất cả testcase"}</h2>
            {chosenSection && (
              <Pill icon="folder" title="Đang xem thư mục (gồm thư mục con)">
                {chosenSection.path}
              </Pill>
            )}
            {suite?.shared && <Pill icon="link">Dùng chung {suite.projects.length} dự án</Pill>}
            {suite && suite.business_project_id !== project.id && <span className="small muted">từ dự án {suite.home_name}</span>}
            {suite?.source_url && (
              <a className="link sm" href={suite.source_url} target="_blank" rel="noreferrer">
                Google Sheet{suite.source_sheet ? ` · ${suite.source_sheet}` : ""}
              </a>
            )}
          </div>
          <div className="tc-actions">
            <ColumnChooser fields={suiteFields} value={columnKeys} onChange={setColumnKeys} />
            <ExportMenu href={exportHref} />
            {suite?.can_edit && (
              <button className="sm" onClick={() => setModal("generate")}>
                <Icon name="sparkle" size={14} /> Sinh bằng AI
              </button>
            )}
            {suite?.can_edit && !suite.archived && (
              <button className="primary sm" onClick={() => setOpen("new")}>
                <Icon name="plus" size={14} /> Thêm testcase
              </button>
            )}
            <MoreMenu items={menu} />
          </div>
        </div>
        {!suite && canEdit && suiteList.length > 0 && <p className="small muted">Chọn một bộ bên trái để xem cây thư mục, thêm testcase, sinh bằng AI hoặc rà soát.</p>}

        <div className="toolbar filters">
          <label className="filter search">
            <span className="small muted">Tìm</span>
            <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="C123, tiêu đề, bước, Jira… (không dấu cũng được)" />
          </label>
          <Select label="Type" value={filters.type} onChange={(v) => setFilters({ ...filters, type: v })} options={CASE_TYPES.map((t) => [t, TYPE_LABEL[t]])} all="Mọi type" />
          <Select label="Ưu tiên" value={filters.priority} onChange={(v) => setFilters({ ...filters, priority: v })} options={PRIORITIES.map((p) => [p, PRIORITY_LABEL[p]])} all="Mọi mức" />
          <Select label="Trạng thái" value={filters.status} onChange={(v) => setFilters({ ...filters, status: v })} options={STATUSES.map((s) => [s, STATUS_LABEL[s]])} all="Còn dùng" />
          <Select label="Cách chạy" value={filters.kind} onChange={(v) => setFilters({ ...filters, kind: v })} options={[["manual", KIND_LABEL.manual], ["automated", KIND_LABEL.automated]]} all="Mọi cách" />
          {(milestones.data?.length ?? 0) > 0 && (
            <Select
              label="Milestone"
              value={filters.milestone_id}
              onChange={(v) => setFilters({ ...filters, milestone_id: v })}
              options={(milestones.data ?? []).map((m) => [String(m.id), `${m.key} · ${m.name}`])}
              all="Mọi milestone"
            />
          )}
          {(list?.tags.length ?? 0) > 0 && <Select label="Nhãn" value={filters.tag} onChange={(v) => setFilters({ ...filters, tag: v })} options={(list?.tags ?? []).map((t) => [t, t])} all="Mọi nhãn" />}
          <FieldFilter fields={suiteFields} people={people.data} value={fieldFilter} onChange={setFieldFilter} />
          <label className="inline-check small tc-problem">
            <input type="checkbox" checked={filters.problem} onChange={(e) => setFilters({ ...filters, problem: e.target.checked })} />
            Thiếu bước / kết quả
          </label>
          {filtering && (
            <button
              className="ghost sm tc-clear"
              onClick={() => {
                setFilters(NO_FILTERS);
                setTerm("");
                setFieldFilter({ key: "", value: "" });
              }}
            >
              Xoá lọc
            </button>
          )}
        </div>

        {picked.size > 0 && (
          <div className="tc-bulk" role="region" aria-label="Thao tác hàng loạt">
            <strong>{picked.size} đã chọn</strong>
            {project.can_manage && (
              <button className="sm" disabled={busy} onClick={() => setTestRun({ suite_id: suite?.id, case_ids: [...picked] })}>
                <Icon name="clipboardCheck" size={14} /> Tạo test run
              </button>
            )}
            {(canApprove || !approvalOn) && (
              <button className="ghost sm" disabled={busy} onClick={() => void bulk({ action: "status", status: "approved" }, undefined, (n) => `Đã duyệt ${n} testcase`)}>
                <Icon name="check" size={14} /> Duyệt
              </button>
            )}
            <BulkSelect label="Đổi trạng thái" busy={busy} onPick={(v) => void bulk({ action: "status", status: v }, undefined, (n) => `Đã chuyển ${n} testcase sang “${STATUS_LABEL[v as keyof typeof STATUS_LABEL]}”`)}>
              {STATUSES.filter((s) => s !== "approved" || canApprove || !approvalOn).map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </BulkSelect>
            <BulkSelect label="Đổi cách chạy" busy={busy} onPick={(v) => void bulk({ action: "kind", kind: v }, undefined, (n) => `Đã đổi cách chạy của ${n} testcase`)}>
              <option value="manual">{KIND_LABEL.manual}</option>
              <option value="automated">{KIND_LABEL.automated}</option>
            </BulkSelect>
            <BulkSelect label="Sửa thêm" busy={busy} onPick={(v) => setBulkEdit(v as BulkEditKind)}>
              {(Object.keys(BULK_EDIT_LABEL) as BulkEditKind[]).map((k) => (
                <option key={k} value={k}>
                  {BULK_EDIT_LABEL[k]}
                </option>
              ))}
            </BulkSelect>
            {suite && sectionList && (
              <BulkSelect label="Chuyển vào thư mục" busy={busy} onPick={(v) => moveCases([...picked], v === "root" ? null : Number(v))}>
                <option value="root">Gốc của bộ</option>
                <SectionOptions sections={sectionList} root={null} />
              </BulkSelect>
            )}
            <BulkSelect label="Đổi type" busy={busy} onPick={(v) => void bulk({ action: "type", type: v })}>
              {CASE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABEL[t]}
                </option>
              ))}
            </BulkSelect>
            <BulkSelect label="Đổi ưu tiên" busy={busy} onPick={(v) => void bulk({ action: "priority", priority: v })}>
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {PRIORITY_LABEL[p]}
                </option>
              ))}
            </BulkSelect>
            {milestones.data && (
              <BulkSelect label="Gắn milestone" busy={busy} onPick={(v) => void bulk({ action: "milestone", milestone_id: v === "none" ? null : Number(v) })}>
                <option value="none">Bỏ milestone</option>
                {milestones.data
                  .filter((m) => !m.completed_at)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.key} · {m.name}
                    </option>
                  ))}
              </BulkSelect>
            )}
            <BulkSelect label="Chuyển sang bộ" busy={busy} onPick={(v) => void bulk({ action: "move", suite_id: Number(v) })}>
              {suiteList
                .filter((s) => s.can_edit)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
            </BulkSelect>
            {canDelete && (
              <button className="ghost sm tc-danger" disabled={busy} onClick={() => setDeleting(all.filter((c) => picked.has(c.id)))}>
                <Icon name="remove" size={14} /> Xoá
              </button>
            )}
            <button className="ghost sm" disabled={busy} onClick={() => setPicked(new Set())}>
              Bỏ chọn
            </button>
          </div>
        )}

        {!list ? (
          <Loading error={cases.error} rows={8} />
        ) : !all.length ? (
          <EmptyState icon="clipboardCheck" title={filtering ? "Không có testcase nào khớp bộ lọc" : chosenSection ? "Thư mục chưa có testcase" : "Chưa có testcase nào"}>
            {canEdit && !filtering ? (suite ? "Thêm testcase, sinh bằng AI từ tài liệu / Jira, hoặc nhập từ Google Sheet." : "Nhập từ Google Sheet (nút ⇪ bên trái) hoặc tạo một bộ testcase.") : undefined}
          </EmptyState>
        ) : (
          <>
            <div className="table-wrap">
              <table className="tc-table">
                <thead>
                  <tr>
                    {canEdit && (
                      <th className="tc-check">
                        <input
                          type="checkbox"
                          aria-label="Chọn tất cả"
                          checked={all.every((c) => picked.has(c.id))}
                          onChange={(e) => setPicked(e.target.checked ? new Set(all.map((c) => c.id)) : new Set())}
                        />
                      </th>
                    )}
                    <th>Mã</th>
                    <th>Testcase</th>
                    <th>Type</th>
                    <th>Ưu tiên</th>
                    <th>Ước tính</th>
                    <th>Trạng thái</th>
                    <th>Jira</th>
                    {shownFields.map((f) => (
                      <th key={f.key}>{f.name}</th>
                    ))}
                    {showDelete && <th className="tc-row-actions" aria-label="Thao tác" />}
                  </tr>
                </thead>
                {groups ? (
                  groups.map((g) => (
                    <tbody key={g.id ?? "root"} className="tc-group">
                      <tr className="tc-group-row" {...(suite?.can_edit ? dropOn(g.id) : {})}>
                        <th colSpan={cols} scope="colgroup">
                          <span className="tc-group-label">
                            <Icon name="folder" size={14} /> {g.label}
                          </span>
                          <span className="small muted">{g.cases.length}</span>
                        </th>
                      </tr>
                      {g.cases.map(row)}
                    </tbody>
                  ))
                ) : (
                  <tbody>{all.map(row)}</tbody>
                )}
              </table>
            </div>
            <p className="small muted tc-foot">
              {all.length < list.total ? `Hiển thị ${all.length}/${list.total} testcase. ` : `${list.total} testcase. `}
              {all.length < list.total && (
                <button className="link sm" onClick={() => setLimit(500)}>
                  Xem thêm
                </button>
              )}
              {canEdit && suite && groups && " Kéo một dòng (hoặc các dòng đã chọn) thả vào thư mục bên trái hoặc tiêu đề thư mục để chuyển."}
            </p>
          </>
        )}

        {canEdit && runs.data && <RunsCard runs={runs.data.runs} onOpen={setRun} />}
      </section>

      {open !== null && (
        <CaseDrawer
          caseId={open === "new" ? null : open}
          suite={suite}
          projectId={project.id}
          sectionId={sectionId}
          approvalOn={approvalOn}
          canApprove={canApprove}
          onClose={() => setOpen(null)}
          onSaved={() => void refresh()}
        />
      )}
      {bulkEdit && (
        <BulkEditModal
          kind={bulkEdit}
          ids={[...picked]}
          suites={suiteList.filter((s) => s.can_edit && !s.archived)}
          fields={suiteFields}
          people={people.data}
          currentSuite={suite?.id ?? null}
          onClose={() => setBulkEdit(null)}
          onDone={bulkDone}
        />
      )}
      {modal === "replace" && suite && (
        <ReplaceModal
          suite={suite}
          sections={sectionList}
          initialSection={sectionId}
          onClose={() => setModal(null)}
          onApplied={(got) => bulkDone(got, `Đã thay trong ${got.changed} testcase`)}
        />
      )}
      {modal === "bulkops" && <BulkOpsModal projectId={project.id} onClose={() => setModal(null)} onUndone={() => void refresh()} />}
      {testRun && (
        <CreateRunModal
          projectId={project.id}
          initial={testRun}
          onClose={() => setTestRun(null)}
          onCreated={(runId) => window.location.assign(`/testcases?project=${project.id}&tab=runs&run=${runId}`)}
        />
      )}
      {run !== null && <RunDrawer runId={run} suites={suiteList.filter((s) => s.can_edit)} onClose={() => setRun(null)} onChanged={() => void Promise.all([runs.mutate(), refresh()])} />}
      {(modal === "suite" || modal === "rename") && (
        <SuiteModal
          projectId={project.id}
          suite={modal === "rename" ? suite : null}
          onClose={() => setModal(null)}
          onSaved={(s) => {
            onSuite(s.id);
            void refresh();
          }}
        />
      )}
      {modal === "share" && suite && <ShareModal suite={suite} projects={projects} onClose={() => setModal(null)} onSaved={() => void refresh()} />}
      {modal === "import" && (
        <ImportModal projectId={project.id} suites={suiteList.filter((s) => s.can_edit)} initialUrl={suite?.source_url ?? undefined} onClose={() => setModal(null)} onDone={imported} />
      )}
      {(modal === "generate" || modal === "audit") && suite && (
        <AiModal kind={modal} projectId={project.id} suite={suite} limits={runs.data} onClose={() => setModal(null)} onAsked={asked} />
      )}
      {fromIssue && (
        <GenerateFromIssue
          projectId={project.id}
          issueKey={fromIssue.key}
          issueTitle={fromIssue.title}
          suites={suiteList.filter((s) => s.can_edit)}
          initialSuite={suite?.can_edit ? suite.id : null}
          onClose={() => setFromIssue(null)}
          onAsked={asked}
        />
      )}
      {deleting && (
        <DeleteCases
          cases={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={(n) => {
            setMessage(`Đã xoá ${n} testcase.`);
            setPicked(new Set());
            if (typeof open === "number" && deleting.some((c) => c.id === open)) setOpen(null);
            void refresh();
          }}
        />
      )}
      {modal === "delete" && suite && (
        <DeleteSuite
          suite={suite}
          onClose={() => setModal(null)}
          onDeleted={() => {
            setMessage(`Đã xoá bộ “${suite.name}”.`);
            onSuite(null);
            void refresh();
          }}
        />
      )}
    </div>
  );
}

/** A "pick to apply" select of the bulk bar (resets to its prompt after each pick). */
function BulkSelect({ label, busy, onPick, children }: { label: string; busy: boolean; onPick: (value: string) => void; children: ReactNode }) {
  return (
    <label className="filter">
      <span className="sr-only">{label}</span>
      <select disabled={busy} value="" onChange={(e) => e.target.value && onPick(e.target.value)}>
        <option value="">{label}…</option>
        {children}
      </select>
    </label>
  );
}


function Select({ label, value, onChange, options, all }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][]; all: string }) {
  return (
    <label className="filter">
      <span className="small muted">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{all}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function CoverageStrip({
  stats,
  error,
  onGenerate,
}: {
  stats: Stats | undefined;
  error: Error | undefined;
  onGenerate?: (issue: { key: string; title: string | null }) => void;
}) {
  if (error) return null;
  if (!stats)
    return (
      <div className="tc-kpis">
        {[0, 1, 2, 3].map((i) => (
          <div key={i}>
            <Skeleton width={60} height={22} />
            <Skeleton width={110} />
          </div>
        ))}
      </div>
    );
  const pct = (n: number, d: number) => (d ? `${Math.round((n * 100) / d)}%` : "—");
  return (
    <>
      <div className="tc-kpis">
        <div>
          <span className="docs-kpi-value">{stats.total}</span>
          <span className="small muted">testcase đang dùng · {stats.by_status.approved} đã duyệt{stats.by_status.review ? ` · ${stats.by_status.review} chờ duyệt` : ""}</span>
        </div>
        <div>
          <span className="docs-kpi-value">{pct(stats.automated, stats.total)}</span>
          <span className="small muted">đã tự động ({stats.automated})</span>
        </div>
        <div>
          <span className={`docs-kpi-value${stats.problems ? " warn" : ""}`}>{stats.problems}</span>
          <span className="small muted">thiếu bước / kết quả mong đợi</span>
        </div>
        <div>
          <span className="docs-kpi-value">{stats.jira_issues ? pct(stats.jira_covered, stats.jira_issues) : "—"}</span>
          <span className="small muted">{stats.jira_issues ? `issue Jira 90 ngày có testcase (${stats.jira_covered}/${stats.jira_issues})` : "chưa có issue Jira của dự án"}</span>
        </div>
      </div>
      {stats.uncovered.length > 0 && (
        <details className="tc-uncovered small">
          <summary>{stats.jira_issues - stats.jira_covered} issue Jira chưa có testcase</summary>
          <ul>
            {stats.uncovered.map((u) => (
              <li key={u.key}>
                {u.url ? (
                  <a className="link" href={u.url} target="_blank" rel="noreferrer">
                    {u.key}
                  </a>
                ) : (
                  <strong>{u.key}</strong>
                )}{" "}
                {u.title}
                {onGenerate && (
                  <>
                    {" "}
                    <button type="button" className="link sm" onClick={() => onGenerate({ key: u.key, title: u.title })}>
                      <Icon name="sparkle" size={12} /> Sinh testcase
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/** Delete one case or the ticked ones, for good (their history too). */
function DeleteCases({ cases, onClose, onDeleted }: { cases: TestCase[]; onClose: () => void; onDeleted: (n: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    setBusy(true);
    try {
      const got = await api.post<{ changed: number }>("/testcases/bulk", { ids: cases.map((c) => c.id), action: "delete" });
      onDeleted(got.changed);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  const one = cases.length === 1 ? cases[0] : null;
  return (
    <Modal
      title={one ? `Xoá testcase ${caseKey(one)}?` : `Xoá ${cases.length} testcase?`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="danger" onClick={() => void remove()} disabled={busy}>
            {busy ? "Đang xoá…" : one ? "Xoá testcase" : `Xoá ${cases.length} testcase`}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      {one ? (
        <p>
          <strong>{one.title}</strong> cùng lịch sử sửa sẽ bị xoá hẳn, không khôi phục được.
        </p>
      ) : (
        <>
          <p>Các testcase sau cùng lịch sử sửa sẽ bị xoá hẳn, không khôi phục được:</p>
          <ul className="tc-delete-list small">
            {cases.slice(0, 8).map((c) => (
              <li key={c.id}>
                <span className="tc-codes">{caseKey(c)}</span> {c.title}
              </li>
            ))}
            {cases.length > 8 && <li className="muted">… và {cases.length - 8} testcase khác</li>}
          </ul>
        </>
      )}
      <p className="small muted">Nếu testcase chỉ không còn dùng, có thể đổi trạng thái sang “Lỗi thời” thay vì xoá: vẫn giữ để tra cứu và không bị gợi ý test lại.</p>
    </Modal>
  );
}

function DeleteSuite({ suite, onClose, onDeleted }: { suite: Suite; onClose: () => void; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    setBusy(true);
    try {
      await api.delete(`/testcases/suites/${suite.id}`);
      onDeleted();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Xoá bộ “${suite.name}”?`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="danger" onClick={() => void remove()} disabled={busy}>
            Xoá {suite.total} testcase
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <p>
        Bộ và <strong>{suite.total}</strong> testcase cùng lịch sử sửa sẽ bị xoá hẳn{suite.shared ? `, ở cả ${suite.projects.length} dự án dùng chung` : ""}. Không khôi phục được — nếu chỉ không dùng nữa, hãy chọn “Lưu trữ”.
      </p>
    </Modal>
  );
}
