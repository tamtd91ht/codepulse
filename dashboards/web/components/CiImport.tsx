"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { MilestoneSelect, useSetupLists } from "@/components/TestRunParts";
import { Alert } from "@/components/ui";
import { ApiError, useApi } from "@/lib/api";
import type { ResultOut } from "@/lib/testruns";

/*
 * CI results (docs/specs/30a G1, docs/specs/30 §15.4): upload a JUnit / TestNG / xUnit /
 * Playwright / Cucumber report into a test run, follow the background import, read the summary;
 * CI facts, failure log and stack trace of a CI result; the "Flaky" pill.
 */

export interface CiImportOut {
  id: number;
  business_project_id: number;
  suite_id: number | null;
  run_id: number | null;
  run_key: string | null;
  status: "queued" | "running" | "done" | "failed";
  format: string | null;
  file_name: string | null;
  total: number;
  done: number;
  mapped: number;
  created_cases: number;
  skipped: number;
  unmapped: string[];
  error: string | null;
  commit_sha: string | null;
  branch: string | null;
  mr_iid: number | null;
  close_run: boolean;
  auto_create: boolean;
  created_at: string | null;
  finished_at: string | null;
}

const MAX_BYTES = 20 * 1024 * 1024;
const STATUS_TEXT: Record<CiImportOut["status"], string> = {
  queued: "Đang chờ xử lý",
  running: "Đang xử lý",
  done: "Đã xong",
  failed: "Lỗi",
};

export function FlakyPill() {
  return (
    <span className="pill warn" title="Kết quả CI không ổn định: pass và fail trên cùng commit, hoặc đổi trạng thái nhiều lần">
      Flaky
    </span>
  );
}

export function CiBadge() {
  return (
    <span className="pill info" title="Kết quả từ CI">
      CI
    </span>
  );
}

/** What a CI result adds to the history: commit, branch, MR, failure log, stack, attachments. */
export function CiResult({ result }: { result: ResultOut }) {
  if (result.source !== "ci") return null;
  const ci = result.ci ?? {};
  const facts = [
    ci.branch ? `nhánh ${ci.branch}` : null,
    ci.commit_sha ? `commit ${String(ci.commit_sha).slice(0, 8)}` : null,
    ci.mr_iid ? `MR !${ci.mr_iid}` : null,
  ].filter(Boolean);
  return (
    <div className="ci-result small">
      {(facts.length > 0 || ci.test_name) && (
        <div className="muted">
          {facts.join(" · ")}
          {ci.test_name && (
            <>
              {facts.length > 0 && " · "}
              <code>{String(ci.test_name)}</code>
            </>
          )}
        </div>
      )}
      {result.log && <div className="tr-pre ci-log">{result.log}</div>}
      {result.stack && (
        <details className="ci-stack">
          <summary>Stack trace</summary>
          <pre>{result.stack}</pre>
        </details>
      )}
      {(result.attachments ?? []).length > 0 && (
        <div>
          Đính kèm:{" "}
          {(result.attachments ?? []).map((a, i) => (
            <code key={i} className="ci-attachment">
              {a.name || a.path}
            </code>
          ))}
        </div>
      )}
    </div>
  );
}

async function upload(form: FormData): Promise<{ import_id: number }> {
  const response = await fetch("/api/v1/testcases/runs/import-results", {
    method: "POST",
    body: form,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as { import_id: number };
}

export function CiImportModal({
  projectId,
  runId,
  onClose,
  onOpenRun,
}: {
  projectId: number;
  /** Record into this open run instead of making a new one. */
  runId?: number;
  onClose: () => void;
  onOpenRun?: (id: number) => void;
}) {
  const suites = useApi<{ id: number; name: string }[]>(`/testcases/suites?business_project_id=${projectId}`);
  const { milestones } = useSetupLists(projectId);
  const [file, setFile] = useState<File | null>(null);
  const [suite, setSuite] = useState<number | null>(null);
  const [runName, setRunName] = useState("");
  const [milestone, setMilestone] = useState<number | null>(null);
  const [branch, setBranch] = useState("");
  const [commit, setCommit] = useState("");
  const [mr, setMr] = useState("");
  const [autoCreate, setAutoCreate] = useState(false);
  const [closeRun, setCloseRun] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importId, setImportId] = useState<number | null>(null);
  const progress = useApi<CiImportOut>(importId ? `/testcases/ci-imports/${importId}` : null, {
    refreshInterval: (latest) => (latest && (latest.status === "done" || latest.status === "failed") ? 0 : 1500),
  });
  const imp = progress.data;
  const finished = imp?.status === "done" || imp?.status === "failed";

  const submit = async () => {
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setError("File lớn hơn 20 MB.");
      return;
    }
    if (autoCreate && !suite) {
      setError("Tự tạo testcase cần chọn bộ testcase.");
      return;
    }
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("business_project_id", String(projectId));
    if (suite) form.append("suite_id", String(suite));
    if (runId) form.append("run_id", String(runId));
    if (!runId && runName.trim()) form.append("run_name", runName.trim());
    if (!runId && milestone) form.append("milestone_id", String(milestone));
    if (branch.trim()) form.append("branch", branch.trim());
    if (commit.trim()) form.append("commit_sha", commit.trim());
    if (/^\d+$/.test(mr.trim())) form.append("mr_iid", mr.trim());
    if (autoCreate) form.append("auto_create", "true");
    if (closeRun) form.append("close_run", "true");
    try {
      const started = await upload(form);
      setImportId(started.import_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const pct = imp && imp.total ? Math.round((imp.done / imp.total) * 100) : 0;

  return (
    <Modal
      title="Nhập kết quả CI"
      onClose={onClose}
      busy={busy}
      width={640}
      footer={
        importId ? (
          <>
            {imp?.run_id && imp.status === "done" && onOpenRun && (
              <button type="button" className="primary" onClick={() => onOpenRun(imp.run_id as number)}>
                Mở {imp.run_key}
              </button>
            )}
            <button type="button" onClick={onClose}>
              Đóng
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onClose} disabled={busy}>
              Huỷ
            </button>
            <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !file}>
              {busy ? "Đang gửi…" : "Nhập kết quả"}
            </button>
          </>
        )
      }
    >
      {!importId ? (
        <div className="adm-form">
          {error && <Alert tone="error">{error}</Alert>}
          <p className="small muted">
            JUnit / TestNG / xUnit XML, Playwright JSON hoặc Cucumber JSON (tối đa 20 MB). Test được ghép với testcase qua mã <code>C123</code> trong tên
            hoặc tag (@C123, [C123]), rồi qua Automation ID (<code>classname.name</code>).
          </p>
          <label className="adm-field">
            <span className="small muted">File báo cáo</span>
            <input type="file" accept=".xml,.json,application/xml,application/json,text/xml" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label className="adm-field">
            <span className="small muted">Bộ testcase (để ghép Automation ID và tự tạo case)</span>
            <select value={suite ?? ""} onChange={(e) => setSuite(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Mọi bộ của dự án</option>
              {(suites.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          {!runId && (
            <div className="tr-form-row">
              <label className="adm-field">
                <span className="small muted">Tên run</span>
                <input value={runName} onChange={(e) => setRunName(e.target.value)} placeholder="CI <nhánh> <commit>" />
              </label>
              <label className="adm-field">
                <span className="small muted">Milestone</span>
                <MilestoneSelect value={milestone} onChange={setMilestone} milestones={milestones.data} />
              </label>
            </div>
          )}
          <div className="tr-form-row">
            <label className="adm-field">
              <span className="small muted">Nhánh</span>
              <input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
            </label>
            <label className="adm-field">
              <span className="small muted">Commit</span>
              <input value={commit} onChange={(e) => setCommit(e.target.value)} placeholder="SHA" />
            </label>
            <label className="adm-field">
              <span className="small muted">MR (IID)</span>
              <input value={mr} inputMode="numeric" onChange={(e) => setMr(e.target.value)} placeholder="123" />
            </label>
          </div>
          <label className="inline-check">
            <input type="checkbox" checked={autoCreate} onChange={(e) => setAutoCreate(e.target.checked)} /> Tự tạo testcase cho test chưa ghép được (vào
            thư mục Automation › Unmapped)
          </label>
          <label className="inline-check">
            <input type="checkbox" checked={closeRun} onChange={(e) => setCloseRun(e.target.checked)} /> Đóng run sau khi nhập
          </label>
        </div>
      ) : !imp ? (
        <p className="small muted" role="status">
          Đang gửi…
        </p>
      ) : (
        <div className="adm-form" aria-live="polite">
          <p>
            <strong>{STATUS_TEXT[imp.status]}</strong> · {imp.file_name} {imp.format && <span className="muted">({imp.format})</span>}
          </p>
          {!finished && (
            <div className="ci-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Tiến độ nhập">
              <span style={{ width: `${pct}%` }} />
            </div>
          )}
          {imp.status === "failed" && <Alert tone="error">{imp.error ?? "Nhập lỗi."}</Alert>}
          {imp.status === "done" && (
            <>
              <ul className="ci-summary small">
                <li>
                  <Icon name="check" size={14} /> {imp.mapped}/{imp.total} test đã ghép testcase
                </li>
                {imp.created_cases > 0 && <li>{imp.created_cases} testcase tạo mới (nháp)</li>}
                {imp.skipped > 0 && <li>{imp.skipped} test bị bỏ qua (skipped): không ghi kết quả</li>}
                <li>{imp.unmapped.length} test chưa ghép được</li>
              </ul>
              {imp.unmapped.length > 0 && (
                <details>
                  <summary className="small">Danh sách chưa ghép ({imp.unmapped.length})</summary>
                  <ul className="ci-unmapped small">
                    {imp.unmapped.map((u) => (
                      <li key={u}>
                        <code>{u}</code>
                      </li>
                    ))}
                  </ul>
                  <p className="small muted">Thêm mã C… vào tên test, hoặc điền Automation ID cho testcase rồi nhập lại.</p>
                </details>
              )}
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
