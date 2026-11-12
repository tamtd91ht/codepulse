"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import { AiModal, RunDrawer, RunsCard } from "@/components/TestcaseAi";
import { RegressionView } from "@/components/TestcaseImpact";
import { CaseDrawer, ImportModal, PriorityPill, ShareModal, StatusPill, SuiteModal, errorText } from "@/components/TestcaseParts";
import { Alert, EmptyState, Loading, Message, PageHeader, Pill, Skeleton } from "@/components/ui";
import { api, can, useApi } from "@/lib/api";
import {
  AI_ACTIVE,
  type AiRun,
  type CaseList,
  type ImportResult,
  KIND_LABEL,
  PRIORITIES,
  PRIORITY_LABEL,
  type RunList,
  STATUS_LABEL,
  STATUSES,
  type Stats,
  type Suite,
  type TcProject,
  type TestCase,
} from "@/lib/testcases";
import type { Me } from "@/lib/types";

/*
 * Testcase (docs/specs/30, user 2026-11-12). Tab "Testcase": the dự án's suites on the left (one
 * per tab of the team's Google Sheet), the cases of the chosen one on the right with coverage,
 * filters and bulk changes; a case opens in a side drawer. Cases come from the editor, a Google
 * Sheet / Excel import, or the AI (written from documents / Jira / links / text, or proposed by a
 * review of the suite), always as drafts a person approves. Tab "Cần test lại": what the merge
 * requests of a period call for, exported to run the tests on Google Sheet.
 * The URL keeps the dự án, the suite and the tab (?project=&suite=&tab=).
 */

type Tab = "cases" | "regression";
type Where = { project: number | null; suite: number | null; tab: Tab };
const LAST_PROJECT = "cp:testcases:project";

function readWhere(): Where {
  if (typeof window === "undefined") return { project: null, suite: null, tab: "cases" };
  const p = new URLSearchParams(window.location.search);
  const num = (k: string) => (p.get(k) && /^\d+$/.test(p.get(k)!) ? Number(p.get(k)) : null);
  return { project: num("project"), suite: num("suite"), tab: p.get("tab") === "regression" ? "regression" : "cases" };
}

function writeWhere(w: Where) {
  const p = new URLSearchParams();
  if (w.project) p.set("project", String(w.project));
  if (w.suite) p.set("suite", String(w.suite));
  if (w.tab !== "cases") p.set("tab", w.tab);
  window.history.replaceState(null, "", `${window.location.pathname}${p.size ? `?${p}` : ""}`);
}

function remembered(): number | null {
  try {
    const v = window.localStorage.getItem(LAST_PROJECT);
    return v && /^\d+$/.test(v) ? Number(v) : null;
  } catch {
    return null;
  }
}

type Filters = { q: string; priority: string; status: string; kind: string; section: string; tag: string; problem: boolean };
const NO_FILTERS: Filters = { q: "", priority: "", status: "", kind: "", section: "", tag: "", problem: false };

export default function TestcasesPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const allowed = me ? can(me, "docs.view") : false;
  const projects = useApi<TcProject[]>(allowed ? "/testcases/projects" : null);
  const [where, setWhere] = useState<Where>({ project: null, suite: null, tab: "cases" });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setWhere(readWhere());
    setReady(true);
  }, []);

  const projectId = useMemo(() => {
    const list = projects.data ?? [];
    if (!list.length) return null;
    const ok = (id: number | null) => (id && list.some((p) => p.id === id) ? id : null);
    return ok(where.project) ?? ok(ready ? remembered() : null) ?? (list.find((p) => p.cases > 0) ?? list[0]).id;
  }, [projects.data, where.project, ready]);

  useEffect(() => {
    if (!projectId) return;
    try {
      window.localStorage.setItem(LAST_PROJECT, String(projectId));
    } catch {
      /* per-viewer convenience only */
    }
  }, [projectId]);

  const go = useCallback((next: Partial<Where>) => {
    setWhere((w) => {
      const merged = { ...w, ...next };
      writeWhere(merged);
      return merged;
    });
  }, []);

  if (me && !allowed)
    return (
      <>
        <PageHeader title="Testcase" foldable={false} />
        <EmptyState icon="alert" title="Bạn chưa có quyền xem testcase">
          Testcase dùng quyền “Tài liệu dự án”: nhờ admin cấp quyền xem.
        </EmptyState>
      </>
    );

  const project = projects.data?.find((p) => p.id === projectId) ?? null;
  return (
    <>
      <PageHeader
        title="Testcase"
        subtitle="Bộ testcase của dự án, nhập từ Google Sheet, AI sinh và rà soát testcase, gợi ý testcase cần test lại theo merge request"
        foldable={false}
        actions={
          <label className="adm-field tc-project">
            <span className="small muted">Dự án</span>
            <select value={projectId ?? ""} onChange={(e) => go({ project: Number(e.target.value), suite: null })} disabled={!projects.data}>
              {(projects.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.cases})
                </option>
              ))}
            </select>
          </label>
        }
      />
      <nav className="tabs page-tabs" aria-label="Testcase">
        {(
          [
            ["cases", "Testcase", "clipboardCheck"],
            ["regression", "Cần test lại", "history"],
          ] as const
        ).map(([key, label, icon]) => (
          <button key={key} type="button" className={where.tab === key ? "active" : undefined} aria-current={where.tab === key ? "page" : undefined} onClick={() => go({ tab: key })}>
            <Icon name={icon} size={16} />
            {label}
          </button>
        ))}
      </nav>
      {!projects.data ? (
        <Loading error={projects.error} rows={6} />
      ) : !project ? (
        <EmptyState icon="inbox" title="Bạn chưa xem được dự án nào" />
      ) : where.tab === "regression" ? (
        <RegressionView projectId={project.id} canEdit={project.can_edit} />
      ) : (
        <CasesTab project={project} projects={projects.data} suiteId={where.suite} onSuite={(id) => go({ suite: id })} onChanged={() => void projects.mutate()} />
      )}
    </>
  );
}

function CasesTab({
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
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === term ? f : { ...f, q: term })), 300);
    return () => clearTimeout(t);
  }, [term]);
  useEffect(() => {
    setFilters(NO_FILTERS);
    setTerm("");
    setPicked(new Set());
    setLimit(200);
  }, [project.id, suiteId]);

  const query = new URLSearchParams({ business_project_id: String(project.id), limit: String(limit) });
  if (suiteId) query.set("suite_id", String(suiteId));
  for (const [k, v] of Object.entries(filters)) if (v) query.set(k, String(v));
  const cases = useApi<CaseList>(`/testcases?${query}`, { keepPreviousData: true });
  const stats = useApi<Stats>(`/testcases/stats?business_project_id=${project.id}`);
  const runs = useApi<RunList>(project.can_edit ? `/testcases/ai?business_project_id=${project.id}` : null, {
    refreshInterval: (latest) => (latest?.runs.some((r) => AI_ACTIVE.includes(r.status)) ? 4000 : 0),
  });

  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<number | "new" | null>(null);
  const [modal, setModal] = useState<null | "suite" | "rename" | "share" | "import" | "generate" | "audit" | "delete">(null);
  const [run, setRun] = useState<number | null>(null);
  // Cases asked to be deleted (one from its row, or the ticked ones): confirmed in a modal.
  const [deleting, setDeleting] = useState<TestCase[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    await Promise.all([cases.mutate(), suites.mutate(), stats.mutate()]);
    onChanged();
  };

  const bulk = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const got = await api.post<{ changed: number }>("/testcases/bulk", { ids: [...picked], ...body });
      setMessage(`Đã cập nhật ${got.changed} testcase.`);
      setPicked(new Set());
      await refresh();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    } finally {
      setBusy(false);
    }
  };

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

  const list = cases.data;
  const all = list?.items ?? [];
  const canEdit = project.can_edit;
  const suiteList = suites.data ?? [];
  const exportHref = (fmt: string) => `/api/v1/testcases/export?business_project_id=${project.id}${suite ? `&suite_id=${suite.id}` : ""}&format=${fmt}`;
  const menu = suite
    ? [
        ...(suite.can_edit ? [{ icon: "edit" as const, label: "Đổi tên", run: () => setModal("rename") }] : []),
        ...(suite.can_edit ? [{ icon: "link" as const, label: "Dự án dùng chung", run: () => setModal("share") }] : []),
        ...(suite.can_edit && suite.source_url ? [{ icon: "sync" as const, label: "Nhập lại từ Google Sheet", run: () => setModal("import") }] : []),
        ...(suite.can_edit && suite.total ? [{ icon: "sparkle" as const, label: "Rà soát bằng AI", run: () => setModal("audit") }] : []),
        { icon: "download" as const, label: "Xuất CSV", run: () => window.location.assign(exportHref("csv")) },
        ...(suite.can_edit ? [{ icon: "folder" as const, label: suite.archived ? "Đưa trở lại" : "Lưu trữ", run: () => void archive(suite, !suite.archived) }] : []),
        ...(suite.can_delete ? [{ icon: "remove" as const, label: "Xoá bộ", danger: true, run: () => setModal("delete") }] : []),
      ]
    : [];

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
                <button className={suiteId === null ? "active" : undefined} onClick={() => onSuite(null)}>
                  <span className="tc-suite-name">Tất cả</span>
                  <span className="small muted">{suiteList.reduce((n, s) => n + s.total - s.obsolete, 0)}</span>
                </button>
              </li>
            )}
            {suiteList.map((s) => (
              <li key={s.id}>
                <button className={s.id === suiteId ? "active" : undefined} onClick={() => onSuite(s.id)} title={s.description ?? undefined}>
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
          <input type="checkbox" checked={archived} onChange={(e) => {
            setArchived(e.target.checked);
            onSuite(null);
          }} />
          Bộ đã lưu trữ
        </label>
      </aside>

      <section className="tc-main">
        <Message text={message} onClose={() => setMessage(null)} />
        <CoverageStrip stats={stats.data} error={stats.error} />

        <div className="tc-head">
          <div className="tc-title">
            <h2>{suite ? suite.name : "Tất cả testcase"}</h2>
            {suite?.shared && <Pill icon="link">Dùng chung {suite.projects.length} dự án</Pill>}
            {suite && suite.business_project_id !== project.id && <span className="small muted">từ dự án {suite.home_name}</span>}
            {suite?.source_url && (
              <a className="link sm" href={suite.source_url} target="_blank" rel="noreferrer">
                Google Sheet{suite.source_sheet ? ` · ${suite.source_sheet}` : ""}
              </a>
            )}
          </div>
          <div className="tc-actions">
            <a className="button sm" href={exportHref("xlsx")} download>
              <Icon name="download" size={14} /> Xuất Excel
            </a>
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
        {!suite && canEdit && suiteList.length > 0 && <p className="small muted">Chọn một bộ bên trái để thêm testcase, sinh bằng AI hoặc rà soát.</p>}

        <div className="toolbar filters">
          <label className="filter search">
            <span className="small muted">Tìm</span>
            <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Mã, tiêu đề, bước, Jira… (không dấu cũng được)" />
          </label>
          <Select label="Chức năng" value={filters.section} onChange={(v) => setFilters({ ...filters, section: v })} options={(list?.sections ?? []).map((s) => [s, s])} all="Mọi chức năng" />
          <Select label="Ưu tiên" value={filters.priority} onChange={(v) => setFilters({ ...filters, priority: v })} options={PRIORITIES.map((p) => [p, PRIORITY_LABEL[p]])} all="Mọi mức" />
          <Select label="Trạng thái" value={filters.status} onChange={(v) => setFilters({ ...filters, status: v })} options={STATUSES.map((s) => [s, STATUS_LABEL[s]])} all="Còn dùng" />
          <Select label="Loại" value={filters.kind} onChange={(v) => setFilters({ ...filters, kind: v })} options={[["manual", KIND_LABEL.manual], ["automated", KIND_LABEL.automated]]} all="Mọi loại" />
          {(list?.tags.length ?? 0) > 0 && <Select label="Nhãn" value={filters.tag} onChange={(v) => setFilters({ ...filters, tag: v })} options={(list?.tags ?? []).map((t) => [t, t])} all="Mọi nhãn" />}
          <label className="inline-check small tc-problem">
            <input type="checkbox" checked={filters.problem} onChange={(e) => setFilters({ ...filters, problem: e.target.checked })} />
            Thiếu bước / kết quả
          </label>
        </div>

        {picked.size > 0 && (
          <div className="tc-bulk" role="region" aria-label="Thao tác hàng loạt">
            <strong>{picked.size} đã chọn</strong>
            <button className="sm" disabled={busy} onClick={() => void bulk({ action: "status", status: "approved" })}>
              <Icon name="check" size={14} /> Duyệt
            </button>
            <label className="filter">
              <span className="sr-only">Đổi ưu tiên</span>
              <select disabled={busy} value="" onChange={(e) => e.target.value && void bulk({ action: "priority", priority: e.target.value })}>
                <option value="">Đổi ưu tiên…</option>
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {PRIORITY_LABEL[p]}
                  </option>
                ))}
              </select>
            </label>
            <label className="filter">
              <span className="sr-only">Chuyển bộ</span>
              <select disabled={busy} value="" onChange={(e) => e.target.value && void bulk({ action: "move", suite_id: Number(e.target.value) })}>
                <option value="">Chuyển sang bộ…</option>
                {suiteList.filter((s) => s.can_edit).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <button className="ghost sm" disabled={busy} onClick={() => void bulk({ action: "status", status: "obsolete" })}>
              Đánh dấu lỗi thời
            </button>
            <button className="ghost sm tc-danger" disabled={busy} onClick={() => setDeleting(all.filter((c) => picked.has(c.id)))}>
              <Icon name="remove" size={14} /> Xoá
            </button>
            <button className="ghost sm" disabled={busy} onClick={() => setPicked(new Set())}>
              Bỏ chọn
            </button>
          </div>
        )}

        {!list ? (
          <Loading error={cases.error} rows={8} />
        ) : !all.length ? (
          <EmptyState icon="clipboardCheck" title={JSON.stringify(filters) !== JSON.stringify(NO_FILTERS) ? "Không có testcase nào khớp bộ lọc" : "Chưa có testcase nào"}>
            {canEdit ? (suite ? "Thêm testcase, sinh bằng AI từ tài liệu / Jira, hoặc nhập từ Google Sheet." : "Nhập từ Google Sheet (nút ⇪ bên trái) hoặc tạo một bộ testcase.") : undefined}
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
                    <th>Ưu tiên</th>
                    <th>Loại</th>
                    <th>Trạng thái</th>
                    <th>Jira</th>
                    {canEdit && <th className="tc-row-actions" aria-label="Thao tác" />}
                  </tr>
                </thead>
                <tbody>
                  {all.map((c) => (
                    <tr key={c.id} className={c.status === "obsolete" ? "muted" : undefined}>
                      {canEdit && (
                        <td className="tc-check">
                          <input
                            type="checkbox"
                            aria-label={`Chọn ${c.code}`}
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
                      <td className="num tc-code-cell">{c.code}</td>
                      <td>
                        <button className="link tc-row-title" onClick={() => setOpen(c.id)}>
                          {c.title}
                        </button>
                        <div className="small muted tc-row-sub">
                          {!suiteId && <span>{suiteList.find((s) => s.id === c.suite_id)?.name}</span>}
                          {c.section && <span>{c.section}</span>}
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
                      <td>
                        <PriorityPill value={c.priority} />
                      </td>
                      <td className="small tc-nowrap">{KIND_LABEL[c.kind]}</td>
                      <td>
                        <StatusPill value={c.status} />
                      </td>
                      <td className="small tc-nowrap">{c.jira_keys.join(", ")}</td>
                      {canEdit && (
                        <td className="tc-row-actions">
                          <IconButton icon="remove" tone="danger" label="Xoá testcase" className="tip-left" onClick={() => setDeleting([c])} />
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="small muted tc-foot">
              {all.length < list.total ? `Hiển thị ${all.length}/${list.total} testcase. ` : `${list.total} testcase. `}
              {all.length < list.total && (
                <button className="link sm" onClick={() => setLimit(500)}>
                  Xem thêm
                </button>
              )}
            </p>
          </>
        )}

        {canEdit && runs.data && <RunsCard runs={runs.data.runs} onOpen={setRun} />}
      </section>

      {open !== null && (
        <CaseDrawer
          caseId={open === "new" ? null : open}
          suite={suite}
          sections={list?.sections ?? []}
          onClose={() => setOpen(null)}
          onSaved={() => void refresh()}
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

function CoverageStrip({ stats, error }: { stats: Stats | undefined; error: Error | undefined }) {
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
          <span className="small muted">testcase đang dùng · {stats.by_status.approved} đã duyệt</span>
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
      title={one ? `Xoá testcase ${one.code}?` : `Xoá ${cases.length} testcase?`}
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
                <span className="tc-codes">{c.code}</span> {c.title}
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
