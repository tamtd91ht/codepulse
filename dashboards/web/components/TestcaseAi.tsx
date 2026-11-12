"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { PriorityPill, errorText } from "@/components/TestcaseParts";
import { Alert, Card, Drawer, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { DocList, ExtractedFile } from "@/lib/docs";
import { relative } from "@/lib/format";
import {
  AI_ACTIVE,
  AI_KIND_LABEL,
  AI_STATUS_LABEL,
  type AiRun,
  ISSUE_LABEL,
  type Proposal,
  type RunList,
  type Suite,
  splitList,
} from "@/lib/testcases";

/*
 * AI on test cases (docs/specs/30 §6–§8.2): ask (cases from sources, a review of a suite), follow
 * the runs, and look at what one proposes: tick the cases worth keeping, adjust a title, add them
 * to a suite as drafts.
 */

type Paste = { name: string; kind: string; size: number; text: string; truncated: boolean };

/** Ask the AI to write cases for a suite, or to review it. */
export function AiModal({
  kind,
  projectId,
  suite,
  limits,
  onClose,
  onAsked,
}: {
  kind: "generate" | "audit";
  projectId: number;
  suite: Suite;
  limits: RunList | undefined;
  onClose: () => void;
  onAsked: (run: AiRun) => void;
}) {
  const docs = useApi<DocList>(kind === "generate" ? `/docs?business_project_id=${projectId}` : null);
  const [docIds, setDocIds] = useState<Set<number>>(new Set());
  const [jira, setJira] = useState("");
  const [links, setLinks] = useState("");
  const [paste, setPaste] = useState("");
  const [files, setFiles] = useState<Paste[]>([]);
  const [instruction, setInstruction] = useState("");
  const [count, setCount] = useState(15);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const max = limits?.max_sources ?? 5;
  const used = docIds.size + splitList(links).length + files.length + (paste.trim() ? 1 : 0) + (splitList(jira).length ? 1 : 0);

  const addFile = async (f: File) => {
    setBusy(true);
    setError(null);
    try {
      const got = await api.postRaw<ExtractedFile>(`/docs/drafts/extract?business_project_id=${projectId}&filename=${encodeURIComponent(f.name)}`, f);
      setFiles((list) => [...list, { name: got.name, kind: got.kind, size: got.size, text: got.text, truncated: got.truncated }]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      const pasted: Paste[] = paste.trim() ? [{ name: "Văn bản dán", kind: "paste", size: paste.length, text: paste.trim(), truncated: false }] : [];
      const run = await api.post<AiRun>("/testcases/ai", {
        kind,
        business_project_id: projectId,
        suite_id: suite.id,
        doc_ids: [...docIds],
        jira_keys: splitList(jira),
        urls: splitList(links),
        files: [...files, ...pasted],
        instruction: instruction.trim() || null,
        count,
      });
      onAsked(run);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const ready = kind === "audit" || (used > 0 && used <= max);
  return (
    <Modal
      title={kind === "generate" ? `Sinh testcase bằng AI — ${suite.name}` : `Rà soát bộ “${suite.name}” bằng AI`}
      onClose={onClose}
      busy={busy}
      width={720}
      footer={
        <>
          <span className="small muted tc-foot-note">
            {limits ? `Đã dùng ${limits.used_today}/${limits.per_day} lượt trong 24 giờ` : ""}
          </span>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void ask()} disabled={busy || !ready}>
            <Icon name="sparkle" size={14} /> {kind === "generate" ? "Sinh testcase" : "Rà soát"}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <div className="adm-form">
        {kind === "generate" ? (
          <>
            <p className="small muted">
              Chọn tối đa {max} nguồn (đang chọn {used}). AI viết testcase theo nguồn, không trùng testcase đã có trong bộ; kết quả là đề xuất, bạn chọn cái nào thêm vào bộ.
            </p>
            <fieldset className="tc-fieldset">
              <legend className="small muted">Tài liệu dự án</legend>
              {docs.data ? (
                docs.data.docs.length ? (
                  <ul className="tc-doc-pick">
                    {docs.data.docs.map((d) => (
                      <li key={d.id}>
                        <label className="inline-check">
                          <input
                            type="checkbox"
                            checked={docIds.has(d.id)}
                            onChange={(e) => {
                              const next = new Set(docIds);
                              if (e.target.checked) next.add(d.id);
                              else next.delete(d.id);
                              setDocIds(next);
                            }}
                          />
                          {d.title}
                        </label>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="small muted">Dự án chưa có tài liệu.</span>
                )
              ) : (
                <Loading error={docs.error} rows={2} />
              )}
            </fieldset>
            <label className="adm-field">
              <span className="small muted">Mã Jira (story, task) — tính là một nguồn</span>
              <input value={jira} onChange={(e) => setJira(e.target.value)} placeholder="OMX-12, OMX-15" />
            </label>
            <label className="adm-field">
              <span className="small muted">Link (mỗi dòng một link: trang web, Google Docs, file GitLab của dự án)</span>
              <textarea value={links} onChange={(e) => setLinks(e.target.value)} rows={2} />
            </label>
            <label className="adm-field">
              <span className="small muted">Dán văn bản (đặc tả, user story, tiêu chí chấp nhận…)</span>
              <textarea value={paste} onChange={(e) => setPaste(e.target.value)} rows={4} />
            </label>
            <div className="tc-inline">
              <label className="tc-file sm">
                <Icon name="upload" size={14} />
                <span>Tải file (.docx, .pdf, .xlsx, .md)</span>
                <input
                  type="file"
                  accept=".md,.markdown,.txt,.doc,.docx,.xlsx,.pdf"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void addFile(f);
                    e.target.value = "";
                  }}
                />
              </label>
              {files.map((f, i) => (
                <span key={`${f.name}-${i}`} className="tag">
                  {f.name}
                  <button className="link sm" aria-label={`Bỏ ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </span>
              ))}
            </div>
            <label className="adm-field tc-count">
              <span className="small muted">Số testcase mong muốn</span>
              <input type="number" min={1} max={limits?.max_count ?? 40} value={count} onChange={(e) => setCount(Math.max(1, Math.min(limits?.max_count ?? 40, Number(e.target.value) || 1)))} />
            </label>
          </>
        ) : (
          <p className="small muted">
            AI đọc {suite.total - suite.obsolete} testcase của bộ, chỉ ra testcase trùng, mơ hồ, thiếu bước / kết quả mong đợi, ưu tiên chưa hợp lý, và đề xuất testcase còn thiếu (trường hợp biên, lỗi, phân quyền…).
          </p>
        )}
        <label className="adm-field">
          <span className="small muted">Yêu cầu thêm (tuỳ chọn)</span>
          <textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={2} maxLength={1000} placeholder="Ví dụ: tập trung vào luồng hoàn tiền và phân quyền" />
        </label>
      </div>
    </Modal>
  );
}

/** The dự án's AI runs (being written, ready to look at, failed). */
export function RunsCard({ runs, onOpen }: { runs: AiRun[]; onOpen: (id: number) => void }) {
  if (!runs.length) return null;
  return (
    <Card title="Lượt AI" id="tc-ai-runs" subtitle="Kết quả là đề xuất: mở để chọn testcase thêm vào bộ">
      <ul className="tc-runs">
        {runs.map((r) => (
          <li key={r.id}>
            <button className="link" onClick={() => onOpen(r.id)}>
              {AI_KIND_LABEL[r.kind]} · {r.kind === "impact" ? r.mr_label : r.suite_name}
            </button>
            <span className="small muted">{r.created_at ? relative(r.created_at) : ""}</span>
            <Pill tone={r.status === "failed" ? "bad" : r.status === "ready" ? "good" : r.status === "applied" ? undefined : "info"}>
              {r.running && <span className="spinner inline" aria-hidden="true" />}
              {AI_STATUS_LABEL[r.status]}
            </Pill>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** What a run read and proposes; tick and add the cases. */
export function RunDrawer({
  runId,
  suites,
  onClose,
  onChanged,
}: {
  runId: number;
  suites: Suite[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const run = useApi<AiRun>(`/testcases/ai/${runId}`, {
    refreshInterval: (latest) => (latest && AI_ACTIVE.includes(latest.status) ? 3000 : 0),
  });
  const r = run.data;
  const proposals: Proposal[] = useMemo(() => {
    if (!r?.result) return [];
    return ((r.kind === "impact" ? r.result.missing : r.result.cases) ?? []) as Proposal[];
  }, [r]);
  const applied = useMemo(() => new Set(r?.result?.applied ?? []), [r]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [titles, setTitles] = useState<Record<number, string>>({});
  const [suiteId, setSuiteId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    setPicked(new Set(proposals.map((_, i) => i).filter((i) => !applied.has(i))));
  }, [proposals, applied]);
  useEffect(() => {
    if (r && !suiteId) setSuiteId(String(r.suite_id ?? suites[0]?.id ?? ""));
  }, [r, suiteId, suites]);

  const add = async () => {
    if (!r) return;
    setBusy(true);
    setMessage(null);
    try {
      const got = await api.post<{ added: unknown[] }>(`/testcases/ai/${r.id}/apply`, {
        suite_id: suiteId ? Number(suiteId) : null,
        items: [...picked].sort((a, b) => a - b).map((i) => ({ index: i, ...(titles[i] ? { title: titles[i] } : {}) })),
      });
      setMessage({ tone: "success", text: `Đã thêm ${got.added.length} testcase (trạng thái Nháp) vào bộ.` });
      await run.mutate();
      onChanged();
    } catch (e) {
      setMessage({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  const retry = async () => {
    if (!r) return;
    setBusy(true);
    try {
      await run.mutate(await api.post<AiRun>(`/testcases/ai/${r.id}/retry`), false);
      onChanged();
    } catch (e) {
      setMessage({ tone: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  const discard = async () => {
    if (!r) return;
    setBusy(true);
    try {
      await api.delete(`/testcases/ai/${r.id}`);
      onChanged();
      onClose();
    } catch (e) {
      setMessage({ tone: "error", text: errorText(e) });
      setBusy(false);
    }
  };

  const open = proposals.filter((_, i) => !applied.has(i)).length;
  const actions = r && (
    <>
      {(r.status === "failed" || r.status === "ready") && (
        <button className="ghost sm" onClick={() => void retry()} disabled={busy}>
          Chạy lại
        </button>
      )}
      {r.status !== "discarded" && (
        <button className="ghost sm" onClick={() => void discard()} disabled={busy}>
          Bỏ
        </button>
      )}
      {open > 0 && (r.status === "ready" || r.status === "applied") && (
        <button className="primary sm" onClick={() => void add()} disabled={busy || !picked.size || !suiteId}>
          Thêm {picked.size} testcase
        </button>
      )}
    </>
  );

  return (
    <Drawer title={r ? `${AI_KIND_LABEL[r.kind]} · ${r.kind === "impact" ? r.mr_label ?? "" : r.suite_name ?? ""}` : "Lượt AI"} onClose={onClose} width="wide" actions={actions}>
      {!r ? (
        <Loading error={run.error} rows={6} />
      ) : (
        <div className="tc-run">
          {message && (
            <Alert tone={message.tone} onClose={() => setMessage(null)}>
              {message.text}
            </Alert>
          )}
          {AI_ACTIVE.includes(r.status) && (
            <Loading>
              {AI_STATUS_LABEL[r.status]}… thường mất 1–3 phút, có thể đóng cửa sổ này và quay lại sau.
            </Loading>
          )}
          {r.status === "failed" && <Alert tone="error">{r.error ?? "Lỗi"}</Alert>}
          {r.result?.summary && <p className="tc-summary">{r.result.summary}</p>}
          {(r.result?.warnings ?? []).map((w) => (
            <Alert key={w} tone="warning">
              {w}
            </Alert>
          ))}
          {r.sources && r.sources.length > 0 && r.kind === "generate" && (
            <p className="small muted">
              Nguồn:{" "}
              {r.sources.map((s, i) => (
                <span key={i} className={s.error ? "bad" : undefined}>
                  {i ? ", " : ""}
                  {s.title || s.name || s.url}
                  {s.error ? ` (${s.error})` : ""}
                </span>
              ))}
            </p>
          )}
          {r.kind === "audit" && (r.result?.issues ?? []).length > 0 && (
            <section>
              <h3 className="tc-h3">Vấn đề của bộ ({r.result?.issues?.length})</h3>
              <ul className="tc-issues">
                {r.result?.issues?.map((it, i) => (
                  <li key={i}>
                    <Pill tone={it.kind === "duplicate" || it.kind === "missing_expected" ? "warn" : undefined}>{ISSUE_LABEL[it.kind]}</Pill>
                    <span className="tc-codes">{it.codes.join(", ")}</span>
                    <span>{it.message}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {r.kind === "generate" && (r.result?.gaps ?? []).length > 0 && (
            <Alert tone="info">
              <strong>Cần hỏi lại BA/PO:</strong>
              <ul className="tc-gaps">
                {r.result?.gaps?.map((g) => (
                  <li key={g}>{g}</li>
                ))}
              </ul>
            </Alert>
          )}
          {r.kind === "impact" && r.result && (
            <p className="small muted">Testcase cần test lại được hiển thị ở thẻ “Testcase liên quan” của review và tab “Cần test lại”. Dưới đây là testcase AI đề xuất thêm.</p>
          )}
          {proposals.length > 0 && (
            <section>
              <div className="tc-proposals-head">
                <h3 className="tc-h3">{r.kind === "generate" ? "Testcase đề xuất" : "Testcase nên có thêm"} ({proposals.length})</h3>
                <label className="filter">
                  <span className="small muted">Thêm vào bộ</span>
                  <select value={suiteId} onChange={(e) => setSuiteId(e.target.value)}>
                    {suites.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button className="link sm" onClick={() => setPicked(picked.size ? new Set() : new Set(proposals.map((_, i) => i).filter((i) => !applied.has(i))))}>
                  {picked.size ? "Bỏ chọn tất cả" : "Chọn tất cả"}
                </button>
              </div>
              <ol className="tc-proposals">
                {proposals.map((p, i) => (
                  <li key={i} className={applied.has(i) ? "done" : undefined}>
                    <label className="inline-check">
                      <input
                        type="checkbox"
                        disabled={applied.has(i)}
                        checked={applied.has(i) || picked.has(i)}
                        onChange={(e) => {
                          const next = new Set(picked);
                          if (e.target.checked) next.add(i);
                          else next.delete(i);
                          setPicked(next);
                        }}
                        aria-label={`Chọn ${p.title}`}
                      />
                    </label>
                    <div className="tc-proposal">
                      <div className="tc-proposal-head">
                        <PriorityPill value={p.priority} />
                        {p.section && <span className="small muted">{p.section}</span>}
                        {applied.has(i) && <Pill tone="good">Đã thêm</Pill>}
                      </div>
                      {applied.has(i) ? (
                        <strong>{p.title}</strong>
                      ) : (
                        <input className="tc-proposal-title" value={titles[i] ?? p.title} onChange={(e) => setTitles({ ...titles, [i]: e.target.value })} aria-label="Tiêu đề" />
                      )}
                      {p.preconditions && (
                        <div className="small">
                          <span className="muted">Tiền điều kiện:</span> {p.preconditions}
                        </div>
                      )}
                      {p.steps && <div className="tc-lines small">{p.steps}</div>}
                      {p.expected && (
                        <div className="small">
                          <span className="muted">Kết quả mong đợi:</span> {p.expected}
                        </div>
                      )}
                      {(p.tags?.length || p.jira_keys?.length) ? (
                        <div className="tc-tags">
                          {[...(p.tags ?? []), ...(p.jira_keys ?? [])].map((t) => (
                            <span key={t} className="tag">
                              {t}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {r.cost_usd ? <p className="small muted">Chi phí AI: ${r.cost_usd.toFixed(3)}{r.model ? ` · ${r.model}` : ""}</p> : null}
        </div>
      )}
    </Drawer>
  );
}
