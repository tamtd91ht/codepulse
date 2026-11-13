"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { CasesTab } from "@/components/TestcaseCases";
import { RegressionView } from "@/components/TestcaseImpact";
import { TraceView } from "@/components/TestcaseLinks";
import { TestcaseReports } from "@/components/TestcaseReports";
import { MilestonesTab } from "@/components/TestMilestones";
import { PlanView } from "@/components/TestPlans";
import { RunView, RunsTab } from "@/components/TestRuns";
import { SetupTab } from "@/components/TestSetup";
import { AuditView } from "@/components/TestAudit";
import { BaselinesTab } from "@/components/TestBaselines";
import { EmptyState, Loading, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { TcProject } from "@/lib/testcases";
import type { Me } from "@/lib/types";

/*
 * Testcase (docs/specs/30, user 2026-11-12). Tab "Testcase": the dự án's suites on the left (one
 * per tab of the team's Google Sheet), the cases of the chosen one on the right with coverage,
 * filters and bulk changes; a case opens in a side drawer. Cases come from the editor, a Google
 * Sheet / Excel import, or the AI (written from documents / Jira / links / text, or proposed by a
 * review of the suite), always as drafts a person approves. Tab "Cần test lại": what the merge
 * requests of a period call for, exported to run the tests on Google Sheet.
 * Tabs "Test run", "Milestone", "Thiết lập" (§13, TestRail model): runs and plans with execution,
 * releases with their progress, shared steps and configurations.
 * The URL keeps the dự án, the suite, the tab and the open run / plan (?project=&suite=&tab=&run=&plan=).
 */

type Tab = "cases" | "runs" | "milestones" | "baselines" | "regression" | "trace" | "reports" | "audit" | "setup";
const TABS: Tab[] = ["cases", "runs", "milestones", "baselines", "regression", "trace", "reports", "audit", "setup"];
type Where = { project: number | null; suite: number | null; tab: Tab; run: number | null; plan: number | null };
const HOME: Where = { project: null, suite: null, tab: "cases", run: null, plan: null };
const LAST_PROJECT = "cp:testcases:project";

function readWhere(): Where {
  if (typeof window === "undefined") return HOME;
  const p = new URLSearchParams(window.location.search);
  const num = (k: string) => (p.get(k) && /^\d+$/.test(p.get(k)!) ? Number(p.get(k)) : null);
  const tab = TABS.find((t) => t === p.get("tab")) ?? (num("run") || num("plan") ? "runs" : "cases");
  return { project: num("project"), suite: num("suite"), tab, run: num("run"), plan: num("plan") };
}

function writeWhere(w: Where) {
  const p = new URLSearchParams();
  if (w.project) p.set("project", String(w.project));
  if (w.suite) p.set("suite", String(w.suite));
  if (w.tab !== "cases") p.set("tab", w.tab);
  if (w.run) p.set("run", String(w.run));
  else if (w.plan) p.set("plan", String(w.plan));
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


export default function TestcasesPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const allowed = me ? can(me, "tests.view") : false;
  const projects = useApi<TcProject[]>(allowed ? "/testcases/projects" : null);
  const [where, setWhere] = useState<Where>(HOME);
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
        <PageHeader title="Testcase" />
        <EmptyState icon="alert" title="Bạn chưa có quyền xem testcase">
          Nhờ admin cấp quyền “Testcase › Xem testcase” trong bộ quyền.
        </EmptyState>
      </>
    );

  const project = projects.data?.find((p) => p.id === projectId) ?? null;
  return (
    <>
      <PageHeader
        title="Testcase"
        subtitle="Bộ testcase của dự án, test run và test plan, milestone, AI sinh và rà soát testcase, gợi ý testcase cần test lại theo merge request"
        actions={
          <label className="adm-field tc-project">
            <span className="small muted">Dự án</span>
            <select value={projectId ?? ""} onChange={(e) => go({ project: Number(e.target.value), suite: null, run: null, plan: null })} disabled={!projects.data}>
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
            ["runs", "Test run", "activity"],
            ["milestones", "Milestone", "flag"],
            ["baselines", "Baseline", "pin"],
            ["regression", "Cần test lại", "history"],
            ["trace", "Ma trận truy vết", "link"],
            ["reports", "Báo cáo", "pulse"],
            ["audit", "Nhật ký thay đổi", "file"],
            ["setup", "Thiết lập", "settings"],
          ] as const
        )
          // §15.14: the audit log is for tests.manage holders of the dự án.
          .filter(([key]) => key !== "audit" || !!project?.can_manage)
          .map(([key, label, icon]) => (
          <button key={key} type="button" className={where.tab === key ? "active" : undefined} aria-current={where.tab === key ? "page" : undefined} onClick={() => go({ tab: key, run: null, plan: null })}>
            <Icon name={icon} size={16} />
            {label}
          </button>
        ))}
      </nav>
      {!projects.data ? (
        <Loading error={projects.error} rows={6} />
      ) : !project ? (
        <EmptyState icon="inbox" title="Bạn chưa xem được dự án nào" />
      ) : where.tab === "runs" ? (
        where.run ? (
          <RunView key={where.run} runId={where.run} projectId={project.id} onBack={() => go({ run: null })} onOpenPlan={(id) => go({ run: null, plan: id })} />
        ) : where.plan ? (
          <PlanView key={where.plan} planId={where.plan} projectId={project.id} onBack={() => go({ plan: null })} onOpenRun={(id) => go({ run: id })} />
        ) : (
          <RunsTab projectId={project.id} canEdit={project.can_manage} onOpenRun={(id) => go({ run: id, plan: null })} onOpenPlan={(id) => go({ plan: id, run: null })} />
        )
      ) : where.tab === "milestones" ? (
        <MilestonesTab projectId={project.id} canEdit={project.can_manage} onOpenRun={(id) => go({ tab: "runs", run: id, plan: null })} />
      ) : where.tab === "baselines" ? (
        <BaselinesTab key={project.id} projectId={project.id} canManage={project.can_manage} onOpenRun={(id) => go({ tab: "runs", run: id, plan: null })} />
      ) : where.tab === "audit" ? (
        project.can_manage ? (
          <AuditView key={project.id} projectId={project.id} />
        ) : (
          <EmptyState icon="alert" title="Nhật ký thay đổi cần quyền Quản lý test của dự án" />
        )
      ) : where.tab === "reports" ? (
        <TestcaseReports key={project.id} projectId={project.id} />
      ) : where.tab === "setup" ? (
        <SetupTab projectId={project.id} canEdit={project.can_edit} canManage={project.can_manage} />
      ) : where.tab === "trace" ? (
        <TraceView projectId={project.id} canEdit={project.can_edit} />
      ) : where.tab === "regression" ? (
        <RegressionView projectId={project.id} canEdit={project.can_manage} onRunCreated={(id) => go({ tab: "runs", run: id, plan: null })} />
      ) : (
        <CasesTab project={project} projects={projects.data} suiteId={where.suite} onSuite={(id) => go({ suite: id })} onChanged={() => void projects.mutate()} />
      )}
    </>
  );
}

