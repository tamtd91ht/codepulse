"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Card, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TaskDetail, TaskSpace, TaskVersion } from "@/lib/types";

import { errorText, refreshTasks } from "./taskParts";

/* Task ↔ test quality (docs/specs/31 §19.4): the testcases covering a task, its Definition of
 * Done, the dự án's DoD settings, a version's release readiness. */

interface Coverage {
  total: number;
  passed: number;
  failed: number;
  untested: number;
  hidden?: boolean;
  cases: { case_id: number; key: string; title: string; status: string; run_id: number | null; test_id: number | null }[];
}

const STATUS_TONE: Record<string, "good" | "bad" | "warn" | undefined> = { passed: "good", failed: "bad", blocked: "warn", retest: "warn" };
const STATUS_LABEL: Record<string, string> = { passed: "Passed", failed: "Failed", blocked: "Blocked", retest: "Retest", untested: "Chưa chạy" };

/** The testcases that cover this task (a Story's acceptance), with their latest result. */
export function TaskTests({ task }: { task: TaskDetail }) {
  const path = `/tasks/${encodeURIComponent(task.key)}/tests`;
  const got = useApi<Coverage>(path);
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const c = got.data;
  if (c?.hidden) return null;

  async function add() {
    if (!ref.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      await got.mutate(await api.post<Coverage>(path, { case: ref.trim() }), { revalidate: false });
      setRef("");
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  async function remove(caseId: number) {
    try {
      await api.delete(`${path}/${caseId}`);
      await got.mutate();
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  return (
    <section className="tk-section" aria-labelledby="tk-tests-title">
      <div className="tk-section-head">
        <h3 id="tk-tests-title">
          <Icon name="check" size={16} /> Testcase
          {c && c.total > 0 && (
            <span className="small muted">
              {" "}
              · {c.passed}/{c.total} Passed{c.failed ? ` · ${c.failed} Failed` : ""}
            </span>
          )}
        </h3>
      </div>
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!c ? (
        <Loading error={got.error} rows={2} />
      ) : !c.cases.length ? (
        <p className="small faint">Chưa có testcase nào phủ task này. Gắn testcase để thấy độ phủ và tự nhắc khi test Failed.</p>
      ) : (
        <ul className="tk-git-list">
          {c.cases.map((tc) => (
            <li key={tc.case_id}>
              <span className="tk-key">{tc.key}</span>
              <span className="tk-ellipsis">{tc.title}</span>
              <span className="spacer" />
              <Pill tone={STATUS_TONE[tc.status]}>{STATUS_LABEL[tc.status] ?? tc.status}</Pill>
              {task.can.link && <IconButton icon="close" label={`Bỏ ${tc.key}`} onClick={() => void remove(tc.case_id)} />}
            </li>
          ))}
        </ul>
      )}
      {task.can.link && (
        <form
          className="tk-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Mã testcase, ví dụ C123" aria-label="Mã testcase" />
          <button type="submit" className="sm" disabled={busy || !ref.trim()}>
            Gắn testcase
          </button>
        </form>
      )}
    </section>
  );
}

/** The dự án's Definition of Done on this task, ticked from real data. */
export function TaskDod({ task }: { task: TaskDetail }) {
  const items = task.dod ?? [];
  if (!items.length) return null;
  const done = items.filter((i) => i.ok).length;
  return (
    <section className="tk-section" aria-labelledby="tk-dod-title">
      <div className="tk-section-head">
        <h3 id="tk-dod-title">
          Definition of Done{" "}
          <span className="small muted">
            {done}/{items.length}
            {task.dod_enforced ? " · bắt buộc trước khi Xong" : ""}
          </span>
        </h3>
      </div>
      <ul className="tk-dod">
        {items.map((i) => (
          <li key={i.key} className={i.ok ? "ok" : "miss"}>
            <Icon name={i.ok ? "checkCircle" : "alertCircle"} size={14} />
            <span>{i.label}</span>
            <span className="small muted">{i.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const DOD_CHOICES: { key: string; label: string; hint: string }[] = [
  { key: "has_mr", label: "Có MR", hint: "Task có ít nhất một MR nối vào" },
  { key: "mr_merged", label: "MR đã merge", hint: "MR fixes (hoặc mọi MR) đã merge" },
  { key: "review_score", label: "Điểm review đạt", hint: "Điểm review trung bình ≥ ngưỡng" },
  { key: "no_critical", label: "Không còn critical", hint: "Không còn finding critical mở trên code" },
  { key: "pipeline_ok", label: "Pipeline không lỗi", hint: "Không pipeline nào của task đang lỗi" },
  { key: "tests_pass", label: "Testcase đều Passed", hint: "Mọi testcase phủ task Passed ở lần chạy gần nhất" },
];

/** Task › Cài đặt: which DoD checks the dự án uses, and whether Done waits for them. */
export function DodCard({ space }: { space: TaskSpace }) {
  const current = ((space.rules ?? {}) as { dod?: { checks?: string[]; min_score?: number; enforce?: boolean } }).dod ?? {};
  const [checks, setChecks] = useState<string[]>(current.checks ?? []);
  const [minScore, setMinScore] = useState<number>(current.min_score ?? 7);
  const [enforce, setEnforce] = useState<boolean>(!!current.enforce);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    setChecks(current.checks ?? []);
    setMinScore(current.min_score ?? 7);
    setEnforce(!!current.enforce);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [space.business_project_id, JSON.stringify(current)]);

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}`, { key: space.key, rules: { dod: { checks, min_score: minScore, enforce } } });
      await refreshTasks();
      setMessage("Đã lưu Definition of Done.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card title="Definition of Done" id="tk-set-dod" subtitle="Danh sách tự tích từ dữ liệu thật (MR, review, pipeline, testcase) hiện trên mỗi task.">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="adm-form tk-form">
        <div className="tk-dod-choices">
          {DOD_CHOICES.map((c) => (
            <label key={c.key} className="inline-check" title={c.hint}>
              <input type="checkbox" checked={checks.includes(c.key)} onChange={(e) => setChecks(e.target.checked ? [...checks, c.key] : checks.filter((x) => x !== c.key))} />
              {c.label}
            </label>
          ))}
        </div>
        {checks.includes("review_score") && (
          <label className="rq-field" style={{ maxWidth: 220 }}>
            <span className="rq-label">Điểm review tối thiểu</span>
            <input type="number" min={0} max={10} step={0.5} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
          </label>
        )}
        <label className="inline-check">
          <input type="checkbox" checked={enforce} onChange={(e) => setEnforce(e.target.checked)} />
          Bắt buộc: chưa đạt thì không chuyển được sang nhóm Xong (git tự chuyển thì ghi lại, chưa chuyển)
        </label>
        <div className="adm-form-foot">
          <button type="button" disabled={busy} onClick={() => void save()}>
            Lưu DoD
          </button>
        </div>
      </div>
    </Card>
  );
}

interface Readiness {
  ready: boolean;
  tasks: number;
  checks: { key: string; label: string; ok: boolean; items: string[]; count: number }[];
}

/** Releasing a version: what holds it back; release anyway only with a reason. */
export function ReleaseModal({ version, onClose, onDone }: { version: TaskVersion; onClose: () => void; onDone: (msg: string) => void }) {
  const got = useApi<Readiness>(`/tasks/versions/${version.id}/readiness`);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const r = got.data;
  const forced = !!r && !r.ready;

  async function release() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/tasks/versions/${version.id}/release`, forced ? { force: true, reason: reason.trim() } : {});
      await refreshTasks();
      onDone(`Đã phát hành ${version.name}${forced ? " (bỏ qua kiểm tra, đã ghi lý do)" : ""}.`);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Phát hành “${version.name}”`}
      onClose={onClose}
      busy={busy}
      width={560}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className={forced ? "primary danger" : "primary"} disabled={busy || !r || (forced && !reason.trim())} onClick={() => void release()}>
            {forced ? "Vẫn phát hành" : "Phát hành"}
          </button>
        </>
      }
    >
      {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
      {!r ? (
        <Loading error={got.error} rows={4} />
      ) : (
        <div className="adm-form tk-form">
          <p className="small" style={{ margin: 0 }}>
            {r.ready ? <Pill tone="good">Sẵn sàng</Pill> : <Pill tone="bad">Chưa sẵn sàng</Pill>} {r.tasks} task trong phiên bản.
          </p>
          <ul className="tk-dod">
            {r.checks.map((c) => (
              <li key={c.key} className={c.ok ? "ok" : "miss"}>
                <Icon name={c.ok ? "checkCircle" : "alertCircle"} size={14} />
                <span>{c.label}</span>
                {!c.ok && (
                  <span className="small muted">
                    {c.count}: {c.items.join(", ")}
                    {c.count > c.items.length ? "…" : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
          {forced && (
            <label className="rq-field">
              <span className="rq-label">Lý do phát hành khi chưa đủ điều kiện (ghi vào nhật ký)</span>
              <textarea rows={2} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
            </label>
          )}
        </div>
      )}
    </Modal>
  );
}
