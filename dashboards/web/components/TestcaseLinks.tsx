"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { CaseDrawer, StatusPill, errorText } from "@/components/TestcaseParts";
import { Alert, Card, EmptyState, Loading, Pagination, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import {
  type AiRun,
  type IssueDoc,
  type LinkSuggestion,
  RESULT_LABEL,
  RESULT_TONE,
  type RepoRef,
  type Suite,
  type Trace,
  splitList,
} from "@/lib/testcases";

/*
 * Links of test cases to Jira and code (docs/specs/30 §15.2 G2): the "Vùng code" field of a
 * suite / section (one "{repo}:{glob}" or bare glob per line), the review of AI link
 * suggestions, writing cases from an uncovered Jira issue, and the traceability matrix.
 */

/** "Vùng code": one entry per line; the dự án's repos are offered as "{repo}:" prefixes. */
export function CodePathsField({
  projectId,
  value,
  onChange,
  negate = false,
}: {
  projectId: number;
  value: string;
  onChange: (v: string) => void;
  /** A case may write "!glob" to drop an inherited one. */
  negate?: boolean;
}) {
  const repos = useApi<RepoRef[]>(`/testcases/repos?business_project_id=${projectId}`);
  const add = (prefix: string) => onChange(value.trim() ? `${value.trimEnd()}\n${prefix}` : prefix);
  return (
    <label className="adm-field">
      <span className="small muted">Vùng code (mỗi dòng một mẫu)</span>
      <textarea value={value} rows={3} spellCheck={false} placeholder="customer-service:src/customer/**" onChange={(e) => onChange(e.target.value)} />
      <span className="small muted">
        Dạng <code>repo:glob</code> (repo là tên ngắn hoặc đường dẫn GitLab) hoặc chỉ <code>glob</code> (mọi repo của dự án). Testcase bên trong kế thừa vùng này
        {negate ? "; ghi !mẫu để bỏ một vùng kế thừa" : ""}.
      </span>
      {(repos.data ?? []).length > 0 && (
        <span className="tc-tags">
          {repos.data?.map((r) => (
            <button key={r.id} type="button" className="tag" title={r.path} onClick={() => add(`${r.name}:`)}>
              {r.name}:
            </button>
          ))}
        </span>
      )}
    </label>
  );
}

/** Review AI link suggestions: accept / reject / edit each, or accept them all at once. */
export function LinksReview({ run, onApplied }: { run: AiRun; onApplied: () => void }) {
  const items: LinkSuggestion[] = useMemo(() => run.result?.links ?? [], [run]);
  const applied = useMemo(() => new Set(run.result?.applied ?? []), [run]);
  const [rejected, setRejected] = useState<Set<number>>(new Set());
  const [edits, setEdits] = useState<Record<number, { jira: string; code: string }>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const valueOf = (s: LinkSuggestion) => edits[s.id] ?? { jira: s.jira_keys.join(", "), code: s.code_paths.join("\n") };
  const pending = items.filter((s) => !applied.has(s.id) && !rejected.has(s.id));

  const apply = async (list: LinkSuggestion[]) => {
    if (!list.length) return;
    setBusy(true);
    setMessage(null);
    try {
      const got = await api.post<{ changed: number }>(`/testcases/ai/${run.id}/apply-links`, {
        items: list.map((s) => ({ id: s.id, jira_keys: splitList(valueOf(s).jira), code_paths: splitList(valueOf(s).code) })),
      });
      setMessage({ tone: "success", text: `Đã gắn liên kết cho ${got.changed} testcase (mỗi testcase có một bản lịch sử).` });
      onApplied();
    } catch (e) {
      setMessage({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!items.length) return <p className="small muted">AI không gợi ý thêm liên kết nào.</p>;
  return (
    <section>
      <div className="tc-proposals-head">
        <h3 className="tc-h3">Gợi ý liên kết ({items.length})</h3>
        <span className="spacer" />
        <button className="primary sm" onClick={() => void apply(pending)} disabled={busy || !pending.length}>
          Chấp nhận tất cả ({pending.length})
        </button>
      </div>
      {message && (
        <Alert tone={message.tone} onClose={() => setMessage(null)}>
          {message.text}
        </Alert>
      )}
      <div className="table-wrap">
        <table className="tc-table">
          <thead>
            <tr>
              <th>Testcase</th>
              <th>Hiện có</th>
              <th>Gợi ý thêm</th>
              <th>Lý do</th>
              <th aria-label="Thao tác" />
            </tr>
          </thead>
          <tbody>
            {items.map((s) => {
              const done = applied.has(s.id);
              const off = rejected.has(s.id);
              const v = valueOf(s);
              return (
                <tr key={s.id} className={done || off ? "muted" : undefined}>
                  <td>
                    <button className="link" onClick={() => setOpen(s.id)}>
                      {s.key}
                    </button>{" "}
                    {s.title}
                  </td>
                  <td className="small">
                    {[...s.current_jira, ...s.current_code].join(", ") || <span className="muted">—</span>}
                  </td>
                  <td>
                    {done || off ? (
                      <span className="small">{[...s.jira_keys, ...s.code_paths].join(", ")}</span>
                    ) : (
                      <div className="tc-link-edit">
                        <input aria-label={`Jira cho ${s.key}`} value={v.jira} placeholder="Jira" onChange={(e) => setEdits({ ...edits, [s.id]: { ...v, jira: e.target.value } })} />
                        <textarea aria-label={`Vùng code cho ${s.key}`} rows={2} spellCheck={false} value={v.code} placeholder="repo:glob" onChange={(e) => setEdits({ ...edits, [s.id]: { ...v, code: e.target.value } })} />
                      </div>
                    )}
                  </td>
                  <td className="small">{s.reason}</td>
                  <td>
                    {done ? (
                      <Pill tone="good">Đã gắn</Pill>
                    ) : off ? (
                      <button className="link sm" onClick={() => setRejected(new Set([...rejected].filter((i) => i !== s.id)))}>
                        Hoàn tác
                      </button>
                    ) : (
                      <span className="tc-row-actions">
                        <button className="ghost sm" onClick={() => void apply([s])} disabled={busy}>
                          Chấp nhận
                        </button>
                        <button className="ghost sm" onClick={() => setRejected(new Set([...rejected, s.id]))} disabled={busy}>
                          Bỏ qua
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {open !== null && <CaseDrawer caseId={open} suite={null} sections={[]} onClose={() => setOpen(null)} onSaved={onApplied} />}
    </section>
  );
}

/** Ask the AI for link suggestions over a suite (or one of its sections). */
export async function askLinks(projectId: number, suiteId: number, sectionId: number | null): Promise<AiRun> {
  return api.post<AiRun>("/testcases/ai", { kind: "links", business_project_id: projectId, suite_id: suiteId, section_id: sectionId });
}

/** "Sinh testcase" from a Jira issue without cases: the key and the documents naming it. */
export function GenerateFromIssue({
  projectId,
  issueKey,
  issueTitle,
  suites,
  initialSuite,
  onClose,
  onAsked,
}: {
  projectId: number;
  issueKey: string;
  issueTitle?: string | null;
  suites: Suite[];
  initialSuite?: number | null;
  onClose: () => void;
  onAsked: (run: AiRun) => void;
}) {
  const docs = useApi<IssueDoc[]>(`/testcases/issue-docs?business_project_id=${projectId}&key=${encodeURIComponent(issueKey)}`);
  const [suiteId, setSuiteId] = useState(String(initialSuite ?? suites[0]?.id ?? ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!suiteId && suites[0]) setSuiteId(String(suites[0].id));
  }, [suiteId, suites]);
  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      const run = await api.post<AiRun>("/testcases/ai", {
        kind: "generate",
        business_project_id: projectId,
        suite_id: Number(suiteId),
        jira_keys: [issueKey],
        doc_ids: (docs.data ?? []).slice(0, 4).map((d) => d.id),
        auto_docs: true,
      });
      onAsked(run);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Sinh testcase cho ${issueKey}`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void ask()} disabled={busy || !suiteId}>
            <Icon name="sparkle" size={14} /> Sinh testcase
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <div className="adm-form">
        {issueTitle && <p className="small">{issueTitle}</p>}
        <label className="adm-field">
          <span className="small muted">Thêm vào bộ</span>
          <select value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
            {suites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <div className="small">
          <span className="muted">Nguồn: </span>
          {issueKey}
          {docs.data ? (
            docs.data.length ? (
              <>, {docs.data.map((d) => d.title).join(", ")} (tài liệu nhắc {issueKey}, tự chọn)</>
            ) : (
              <span className="muted"> (không có tài liệu nào nhắc mã này)</span>
            )
          ) : (
            <span className="muted"> · đang tìm tài liệu…</span>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Issues per page of the matrix (the API caps a page at 500). */
const TRACE_PAGE = 100;

/** "Ma trận truy vết": Jira issue → testcase → latest result → bug, with filters and CSV. */
export function TraceView({ projectId, canEdit, onAsked }: { projectId: number; canEdit: boolean; onAsked?: (run: AiRun) => void }) {
  const suiteList = useApi<Suite[]>(canEdit ? `/testcases/suites?business_project_id=${projectId}` : null);
  const suites = (suiteList.data ?? []).filter((s) => s.can_edit);
  const [q, setQ] = useState("");
  const [sprint, setSprint] = useState("");
  const [label, setLabel] = useState("");
  const [milestone, setMilestone] = useState("");
  const [uncovered, setUncovered] = useState(false);
  const [generate, setGenerate] = useState<{ key: string; title: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const milestones = useApi<{ id: number; name: string }[]>(`/testcases/milestones?business_project_id=${projectId}`);
  const params = new URLSearchParams({ business_project_id: String(projectId) });
  if (q.trim()) params.set("q", q.trim());
  if (sprint) params.set("sprint", sprint);
  if (label) params.set("label", label);
  if (milestone) params.set("milestone_id", milestone);
  if (uncovered) params.set("uncovered", "true");
  const filters = params.toString();
  // A new filter starts again from the first page.
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [filters]);
  const paged = new URLSearchParams(params);
  paged.set("limit", String(TRACE_PAGE));
  paged.set("offset", String((page - 1) * TRACE_PAGE));
  const data = useApi<Trace>(`/testcases/traceability?${paged}`, { keepPreviousData: true });
  const d = data.data;
  const pct = d && d.issues ? `${Math.round((d.covered * 100) / d.issues)}%` : "—";

  return (
    <div className="tc-trace">
      <div className="toolbar filters">
        <label className="filter">
          <span className="small muted">Tìm</span>
          <input value={q} placeholder="Mã / tiêu đề issue" onChange={(e) => setQ(e.target.value)} />
        </label>
        <label className="filter">
          <span className="small muted">Sprint / version</span>
          <select value={sprint} onChange={(e) => setSprint(e.target.value)}>
            <option value="">Tất cả</option>
            {(d?.sprints ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Nhãn / epic</span>
          <select value={label} onChange={(e) => setLabel(e.target.value)}>
            <option value="">Tất cả</option>
            {(d?.labels ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Kết quả theo milestone</span>
          <select value={milestone} onChange={(e) => setMilestone(e.target.value)}>
            <option value="">Mọi run</option>
            {(milestones.data ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-check small">
          <input type="checkbox" checked={uncovered} onChange={(e) => setUncovered(e.target.checked)} /> Chỉ issue chưa có testcase
        </label>
        <span className="spacer" />
        <a className="button sm" href={`/api/v1/testcases/traceability/export?${params}`} download>
          <Icon name="download" size={14} /> Xuất CSV
        </a>
      </div>
      {message && (
        <Alert tone="success" onClose={() => setMessage(null)}>
          {message}
        </Alert>
      )}
      {!d ? (
        <Loading error={data.error} rows={6} />
      ) : (
        <Card
          title={`Ma trận truy vết (${d.total})`}
          id="tc-trace"
          subtitle={`${pct} issue có testcase (${d.covered}/${d.issues}) · ${d.without_requirement} testcase chưa gắn yêu cầu`}
        >
          {d.rows.length ? (
            <div className="table-wrap">
              <table className="tc-table">
                <thead>
                  <tr>
                    <th>Issue Jira</th>
                    <th>Testcase</th>
                    <th>Kết quả gần nhất</th>
                    <th>Bug</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={r.key}>
                      <td>
                        {r.url ? (
                          <a className="link" href={r.url} target="_blank" rel="noreferrer">
                            {r.key}
                          </a>
                        ) : (
                          <strong>{r.key}</strong>
                        )}{" "}
                        {r.title}
                        <div className="small muted">{[r.type, r.state, r.sprint].filter(Boolean).join(" · ")}</div>
                      </td>
                      <td>
                        {r.cases.length ? (
                          <ul className="tc-trace-list">
                            {r.cases.map((c) => (
                              <li key={c.id}>
                                <span className="tc-codes">{c.key}</span> {c.title} <StatusPill value={c.status} />
                              </li>
                            ))}
                          </ul>
                        ) : canEdit && suites.length ? (
                          <button className="link sm" onClick={() => setGenerate({ key: r.key, title: r.title })}>
                            <Icon name="sparkle" size={12} /> Sinh testcase
                          </button>
                        ) : (
                          <span className="muted small">Chưa có testcase</span>
                        )}
                      </td>
                      <td>
                        <ul className="tc-trace-list">
                          {r.cases.map((c) => (
                            <li key={c.id}>
                              {c.test_status ? (
                                <>
                                  <Pill tone={RESULT_TONE[c.test_status]}>{RESULT_LABEL[c.test_status]}</Pill> <span className="small muted">{c.run_key}</span>
                                </>
                              ) : (
                                <span className="small muted">chưa chạy</span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </td>
                      <td className="small">{[...new Set(r.cases.flatMap((c) => c.defects))].join(", ") || <span className="muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState icon="inbox" title="Không có issue Jira nào khớp bộ lọc">
              Issue Jira của dự án đồng bộ từ Jira; gắn mã Jira cho testcase để hiện ở đây.
            </EmptyState>
          )}
          {d.total > TRACE_PAGE && (
            <Pagination page={page} pageSize={TRACE_PAGE} total={d.total} shown={d.rows.length} onPage={setPage} label="issue" />
          )}
        </Card>
      )}
      {generate && (
        <GenerateFromIssue
          projectId={projectId}
          issueKey={generate.key}
          issueTitle={generate.title}
          suites={suites}
          onClose={() => setGenerate(null)}
          onAsked={(run) => {
            setMessage(`Đã gửi yêu cầu sinh testcase cho ${generate.key}; xem kết quả trong “Lượt AI” của tab Testcase.`);
            onAsked?.(run);
          }}
        />
      )}
    </div>
  );
}
