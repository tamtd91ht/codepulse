"use client";

import Link from "next/link";
import { useState } from "react";

import { Icon } from "@/components/icons";
import { RunDrawer } from "@/components/TestcaseAi";
import { CaseDrawer, PriorityPill, errorText } from "@/components/TestcaseParts";
import { CreateRunModal } from "@/components/TestRuns";
import { Alert, Card, EmptyState, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import { AI_ACTIVE, AI_STATUS_LABEL, type AiBrief, type AiRun, type ImpactCase, type MrImpact, type Regression, type Suite } from "@/lib/testcases";

/*
 * Testcase cần test lại (docs/specs/30 §8): the cases a merge request calls for (same Jira key,
 * a changed file in a case's code globs, its automated test changed; and the AI's pick when asked),
 * on the review of the MR and, over every MR merged in a period, as the release's list.
 */

const CONFIDENCE_LABEL: Record<string, string> = { high: "chắc", medium: "có thể", low: "ít khả năng" };

function CaseRows({ cases, onOpen }: { cases: ImpactCase[]; onOpen: (id: number) => void }) {
  return (
    <div className="table-wrap">
      <table className="tc-table">
        <thead>
          <tr>
            <th>Mã</th>
            <th>Testcase</th>
            <th>Ưu tiên</th>
            <th>Vì sao</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.case.id}>
              <td className="num tc-code-cell">{c.case.code}</td>
              <td>
                <button className="link tc-row-title" onClick={() => onOpen(c.case.id)}>
                  {c.case.title}
                </button>
                <div className="small muted">
                  {c.suite}
                  {c.case.section ? ` · ${c.case.section}` : ""}
                </div>
              </td>
              <td>
                <PriorityPill value={c.case.priority} />
              </td>
              <td className="small">
                {c.reasons.map((r) => (
                  <div key={r}>{r}</div>
                ))}
                {c.ai_reason && (
                  <div>
                    <Icon name="sparkle" size={12} /> {c.ai_reason}
                    {c.confidence ? <span className="muted"> ({CONFIDENCE_LABEL[c.confidence] ?? c.confidence})</span> : null}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AiLine({ ai }: { ai: AiBrief | null }) {
  if (!ai) return null;
  return (
    <Pill tone={ai.status === "failed" ? "bad" : AI_ACTIVE.includes(ai.status) ? "info" : "good"} icon="sparkle">
      {AI_ACTIVE.includes(ai.status) && <span className="spinner inline" aria-hidden="true" />}
      AI: {AI_STATUS_LABEL[ai.status]}
    </Pill>
  );
}

/** On a merge request's review: the cases to test again, and the AI analysis on demand. */
export function MrImpactCard({ mrId }: { mrId: number }) {
  const impact = useApi<MrImpact>(`/testcases/impact/mr/${mrId}`, {
    refreshInterval: (latest) => (latest?.ai && AI_ACTIVE.includes(latest.ai.status) ? 4000 : 0),
  });
  const [open, setOpen] = useState<number | null>(null);
  const [run, setRun] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const suites = useApi<Suite[]>(run && impact.data?.business_project_id ? `/testcases/suites?business_project_id=${impact.data.business_project_id}` : null);
  const d = impact.data;
  if (impact.error?.status === 404) return null;

  const analyse = async () => {
    if (!d?.business_project_id) return;
    setBusy(true);
    setError(null);
    try {
      await api.post<AiRun>("/testcases/ai", { kind: "impact", business_project_id: d.business_project_id, merge_request_id: d.mr_id });
      await impact.mutate();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const running = d?.ai ? AI_ACTIVE.includes(d.ai.status) : false;
  return (
    <Card
      title="Testcase liên quan"
      id="review-testcases"
      subtitle="Testcase nên test lại khi merge thay đổi này (theo Jira, vùng code, file test tự động; thêm AI khi cần)"
      actions={
        d?.can_edit && d.ai_allowed ? (
          <button className="sm" onClick={() => void analyse()} disabled={busy || running}>
            <Icon name="sparkle" size={14} /> {d.ai ? "Phân tích lại bằng AI" : "Phân tích bằng AI"}
          </button>
        ) : undefined
      }
    >
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!d ? (
        <Loading error={impact.error} rows={3} />
      ) : (
        <>
          <div className="tc-impact-head small muted">
            <span>{d.files} file thay đổi</span>
            <AiLine ai={d.ai} />
            {d.ai?.status === "failed" && <span className="bad">{d.ai.error}</span>}
            {d.ai && d.ai.missing > 0 && d.can_edit && (
              <button className="link sm" onClick={() => setRun(d.ai!.id)}>
                AI đề xuất {d.ai.missing} testcase còn thiếu
              </button>
            )}
            <Link className="link sm" href={`/testcases?project=${d.business_project_id}`}>
              Mở Testcase
            </Link>
          </div>
          {d.ai?.summary && <p className="small">{d.ai.summary}</p>}
          {d.cases.length ? (
            <CaseRows cases={d.cases} onOpen={setOpen} />
          ) : (
            <EmptyState icon="checkCircle" title="Chưa thấy testcase nào liên quan">
              Gắn mã Jira hoặc vùng code cho testcase để CodePulse tự ghép, hoặc bấm “Phân tích bằng AI”.
            </EmptyState>
          )}
        </>
      )}
      {open !== null && <CaseDrawer caseId={open} suite={null} sections={[]} onClose={() => setOpen(null)} onSaved={() => void impact.mutate()} />}
      {run !== null && <RunDrawer runId={run} suites={suites.data ?? []} onClose={() => setRun(null)} onChanged={() => void impact.mutate()} />}
    </Card>
  );
}

/** Tab "Cần test lại": the MRs merged in a period and every case they call for. */
export function RegressionView({ projectId, canEdit, onRunCreated }: { projectId: number; canEdit: boolean; onRunCreated?: (runId: number) => void }) {
  const [days, setDays] = useState(14);
  const [branch, setBranch] = useState("");
  const params = `business_project_id=${projectId}&days=${days}${branch ? `&target_branch=${encodeURIComponent(branch)}` : ""}`;
  const data = useApi<Regression>(`/testcases/regression?${params}`, {
    keepPreviousData: true,
    refreshInterval: (latest) => (latest?.mrs.some((m) => m.ai && AI_ACTIVE.includes(m.ai.status)) ? 5000 : 0),
  });
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const d = data.data;

  const analyse = async (mrId: number) => {
    setBusy(mrId);
    setError(null);
    try {
      await api.post<AiRun>("/testcases/ai", { kind: "impact", business_project_id: projectId, merge_request_id: mrId });
      await data.mutate();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="tc-regression">
      <div className="toolbar filters">
        <label className="filter">
          <span className="small muted">MR merge trong</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[3, 7, 14, 30, 60, 90].map((n) => (
              <option key={n} value={n}>
                {n} ngày
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Nhánh đích</span>
          <select value={branch} onChange={(e) => setBranch(e.target.value)}>
            <option value="">Mọi nhánh</option>
            {(d?.branches ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <span className="spacer" />
        <a className="button sm" href={`/api/v1/testcases/regression/export?${params}`} download>
          <Icon name="download" size={14} /> Xuất Excel
        </a>
        {canEdit && (
          <button type="button" className="sm primary" onClick={() => setCreating(true)} disabled={!d?.cases.length}>
            <Icon name="plus" size={14} /> Tạo test run
          </button>
        )}
      </div>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!d ? (
        <Loading error={data.error} rows={5} />
      ) : (
        <>
          <Card title={`Cần test lại (${d.cases.length})`} id="tc-regression-cases" subtitle="Gộp từ mọi MR đã merge trong khoảng đã chọn, sắp theo ưu tiên">
            {d.cases.length ? (
              <CaseRows cases={d.cases} onOpen={setOpen} />
            ) : (
              <EmptyState icon="checkCircle" title="Không có testcase nào cần test lại">
                {d.mrs.length ? "Các MR chưa ghép được testcase nào: gắn Jira / vùng code cho testcase, hoặc phân tích MR bằng AI." : "Không có MR nào được merge trong khoảng này."}
              </EmptyState>
            )}
          </Card>
          <Card title={`Merge request (${d.mrs.length})`} id="tc-regression-mrs">
            {d.mrs.length ? (
              <div className="table-wrap">
                <table className="tc-table">
                  <thead>
                    <tr>
                      <th>MR</th>
                      <th>Merge</th>
                      <th className="num">File</th>
                      <th className="num">Testcase</th>
                      <th>AI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.mrs.map((m) => (
                      <tr key={m.mr_id}>
                        <td>
                          {m.review_id ? (
                            <Link className="link" href={`/reviews/${m.review_id}`}>
                              !{m.iid} {m.title}
                            </Link>
                          ) : (
                            <span>
                              !{m.iid} {m.title}
                            </span>
                          )}
                          <div className="small muted">
                            {m.repo}
                            {m.target_branch ? ` → ${m.target_branch}` : ""}
                          </div>
                        </td>
                        <td className="small">{dateTimeMin(m.merged_at)}</td>
                        <td className="num">{m.files}</td>
                        <td className="num">{m.matched}</td>
                        <td>
                          {m.ai ? (
                            <AiLine ai={m.ai} />
                          ) : canEdit ? (
                            <button className="link sm" onClick={() => void analyse(m.mr_id)} disabled={busy === m.mr_id}>
                              <Icon name="sparkle" size={12} /> Phân tích
                            </button>
                          ) : (
                            <span className="muted small">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="small muted">Không có MR nào được merge trong khoảng này.</p>
            )}
          </Card>
        </>
      )}
      {open !== null && <CaseDrawer caseId={open} suite={null} sections={[]} onClose={() => setOpen(null)} onSaved={() => void data.mutate()} />}
      {creating && (
        <CreateRunModal
          projectId={projectId}
          initial={{ regression: { days, ...(branch ? { target_branch: branch } : {}) } }}
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            onRunCreated?.(id);
          }}
        />
      )}
    </div>
  );
}
