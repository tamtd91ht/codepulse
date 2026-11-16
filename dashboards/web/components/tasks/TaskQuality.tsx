"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Card, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TaskCoverCase, TaskCriterion, TaskDetail, TaskSpace, TaskVersion } from "@/lib/types";

import { errorText, refreshTasks } from "./taskParts";

/* Task ↔ test quality (docs/specs/31 §19.4): the testcases covering a task, its Definition of
 * Done, the dự án's DoD settings, a version's release readiness. */

interface Coverage {
  total: number;
  passed: number;
  failed: number;
  untested: number;
  hidden?: boolean;
  cases: TaskCoverCase[];
  criteria?: TaskCriterion[];
}

const STATUS_TONE: Record<string, "good" | "bad" | "warn" | undefined> = { passed: "good", failed: "bad", blocked: "warn", retest: "warn" };
const STATUS_LABEL: Record<string, string> = { passed: "Passed", failed: "Failed", blocked: "Blocked", retest: "Retest", untested: "Chưa chạy" };

/** Acceptance criteria with stable ids (docs/specs/31 §20.3): add, edit, tick, move, delete; each
 * with the testcases linked to it and their combined status. */
function TaskCriteria({ task, onChanged }: { task: TaskDetail; onChanged: () => void }) {
  const path = `/tasks/${encodeURIComponent(task.key)}/criteria`;
  const got = useApi<TaskCriterion[]>(path);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const items = got.data;
  const canEdit = task.can.edit;

  async function run(work: () => Promise<TaskCriterion[] | void>): Promise<boolean> {
    setBusy(true);
    setMessage(null);
    let ok = false;
    try {
      const out = await work();
      if (out) await got.mutate(out, { revalidate: false });
      else await got.mutate();
      onChanged();
      ok = true;
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
    return ok;
  }

  const add = () =>
    run(async () => {
      const out = await api.post<TaskCriterion[]>(path, { text: text.trim() });
      setText("");
      return out;
    });
  const patch = (id: number, body: Partial<Pick<TaskCriterion, "text" | "done" | "position">>) => run(() => api.patch<TaskCriterion[]>(`${path}/${id}`, body));
  const remove = (c: TaskCriterion) => {
    if (!window.confirm(`Xoá tiêu chí “${c.text}”? Testcase gắn với nó vẫn phủ task.`)) return;
    void run(async () => {
      await api.delete(`${path}/${c.id}`);
    });
  };

  const done = items?.filter((c) => c.done).length ?? 0;
  return (
    <section className="tk-section qa-criteria" aria-labelledby="qa-criteria-title">
      <div className="tk-section-head">
        <h3 id="qa-criteria-title">
          <Icon name="clipboardCheck" size={16} /> Tiêu chí chấp nhận
          {items && items.length > 0 && (
            <span className="small muted">
              {" "}
              · {done}/{items.length} đạt
            </span>
          )}
        </h3>
      </div>
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!items ? (
        <Loading error={got.error} rows={2} />
      ) : !items.length ? (
        <p className="small faint">Chưa có tiêu chí chấp nhận. Mỗi tiêu chí có mã riêng để gắn testcase và xem độ phủ theo từng tiêu chí.</p>
      ) : (
        <ol className="qa-crit-list">
          {items.map((c, i) => (
            <li key={c.id} className={c.done ? "qa-crit done" : "qa-crit"}>
              <input type="checkbox" checked={c.done} disabled={!canEdit || busy} onChange={(e) => void patch(c.id, { done: e.target.checked })} aria-label={`Đạt: ${c.text}`} />
              <div className="qa-crit-main">
                {editing?.id === c.id ? (
                  <form
                    className="tk-inline-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (!editing.text.trim()) return;
                      void patch(c.id, { text: editing.text.trim() }).then((ok) => ok && setEditing(null));
                    }}
                  >
                    <input value={editing.text} maxLength={500} autoFocus onChange={(e) => setEditing({ id: c.id, text: e.target.value })} aria-label="Nội dung tiêu chí" />
                    <button type="submit" className="sm" disabled={busy || !editing.text.trim()}>
                      Lưu
                    </button>
                    <button type="button" className="sm ghost" onClick={() => setEditing(null)}>
                      Huỷ
                    </button>
                  </form>
                ) : (
                  <span className="qa-crit-text">
                    <span className="qa-crit-no">AC{i + 1}</span> {c.text}
                  </span>
                )}
                {c.status !== null && (
                  <div className="qa-crit-cases small">
                    {c.cases.length ? (
                      c.cases.map((tc) => (
                        <span key={tc.case_id} className="qa-crit-case" title={tc.title}>
                          <span className="tk-key">{tc.key}</span>
                          <Pill tone={STATUS_TONE[tc.status]}>{STATUS_LABEL[tc.status] ?? tc.status}</Pill>
                        </span>
                      ))
                    ) : (
                      <span className="faint">chưa có testcase</span>
                    )}
                  </div>
                )}
              </div>
              {c.status && c.status !== "none" && <Pill tone={STATUS_TONE[c.status]}>{STATUS_LABEL[c.status] ?? c.status}</Pill>}
              {canEdit && editing?.id !== c.id && (
                <span className="qa-crit-actions">
                  <IconButton icon="chevronUp" label="Lên trên" disabled={busy || i === 0} onClick={() => void patch(c.id, { position: i - 1 })} />
                  <IconButton icon="chevronDown" label="Xuống dưới" disabled={busy || i === items.length - 1} onClick={() => void patch(c.id, { position: i + 1 })} />
                  <IconButton icon="edit" label="Sửa tiêu chí" disabled={busy} onClick={() => setEditing({ id: c.id, text: c.text })} />
                  <IconButton icon="remove" label="Xoá tiêu chí" tone="danger" disabled={busy} onClick={() => remove(c)} />
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      {canEdit && (
        <form
          className="tk-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void add();
          }}
        >
          <input value={text} maxLength={500} onChange={(e) => setText(e.target.value)} placeholder="Ví dụ: Lưu được khách hàng khi đủ trường bắt buộc" aria-label="Tiêu chí mới" />
          <button type="submit" className="sm" disabled={busy || !text.trim()}>
            Thêm tiêu chí
          </button>
        </form>
      )}
    </section>
  );
}

/** The task's acceptance criteria, then the testcases that cover it (a Story's acceptance), with
 * their latest result; a testcase can be linked to one criterion. */
export function TaskTests({ task }: { task: TaskDetail }) {
  const path = `/tasks/${encodeURIComponent(task.key)}/tests`;
  const got = useApi<Coverage>(path);
  const criteria = useApi<TaskCriterion[]>(`/tasks/${encodeURIComponent(task.key)}/criteria`);
  const [ref, setRef] = useState("");
  const [criterion, setCriterion] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const c = got.data;
  const crit = criteria.data ?? [];
  const label = (id: number | null | undefined) => {
    const i = crit.findIndex((x) => x.id === id);
    return i >= 0 ? `AC${i + 1}` : null;
  };
  const criteriaSection = <TaskCriteria task={task} onChanged={() => void got.mutate()} />;
  if (c?.hidden) return criteriaSection;

  async function add() {
    if (!ref.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      await got.mutate(await api.post<Coverage>(path, { case: ref.trim(), ...(criterion ? { criterion_id: Number(criterion) } : {}) }), { revalidate: false });
      setRef("");
      await criteria.mutate();
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  async function relink(tc: TaskCoverCase, to: string) {
    try {
      await got.mutate(await api.post<Coverage>(path, { case: String(tc.case_id), criterion_id: to ? Number(to) : null }), { revalidate: false });
      await criteria.mutate();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  async function remove(caseId: number) {
    try {
      await api.delete(`${path}/${caseId}`);
      await got.mutate();
      await criteria.mutate();
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  return (
    <>
    {criteriaSection}
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
              {task.can.link && crit.length > 0 ? (
                <select className="qa-case-crit" value={tc.criterion_id ?? ""} onChange={(e) => void relink(tc, e.target.value)} aria-label={`Tiêu chí của ${tc.key}`}>
                  <option value="">Cả task</option>
                  {crit.map((x, i) => (
                    <option key={x.id} value={x.id}>
                      AC{i + 1}
                    </option>
                  ))}
                </select>
              ) : (
                label(tc.criterion_id) && <span className="pill">{label(tc.criterion_id)}</span>
              )}
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
          {crit.length > 0 && (
            <select className="qa-case-crit" value={criterion} onChange={(e) => setCriterion(e.target.value)} aria-label="Gắn vào tiêu chí">
              <option value="">Cả task</option>
              {crit.map((x, i) => (
                <option key={x.id} value={x.id}>
                  AC{i + 1}: {x.text.length > 40 ? `${x.text.slice(0, 40)}…` : x.text}
                </option>
              ))}
            </select>
          )}
          <button type="submit" className="sm" disabled={busy || !ref.trim()}>
            Gắn testcase
          </button>
        </form>
      )}
    </section>
    </>
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

/** On the versions list: ready to release, or what holds it back (§20.0). */
export function ReadinessPill({ versionId }: { versionId: number }) {
  const got = useApi<Readiness>(`/tasks/versions/${versionId}/readiness`);
  const r = got.data;
  if (!r) return null;
  if (r.ready) return <Pill tone="good">Sẵn sàng</Pill>;
  const missing = r.checks.filter((c) => !c.ok);
  return (
    <Pill tone="warn">
      <span title={missing.map((c) => `${c.label}: ${c.count}`).join(" · ")}>Chưa sẵn sàng ({missing.length} mục)</span>
    </Pill>
  );
}
