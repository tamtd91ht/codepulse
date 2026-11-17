"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Alert, Card, ExternalLink, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TaskPerson, TaskSpace } from "@/lib/types";
import {
  BUG_STATE_LABEL,
  type BugDefaults,
  type BugIn,
  type BugOut,
  type BugState,
  type RunOut,
  type TestDetail,
  type TestOut,
  type TestProjectSettings,
  errorText,
  splitNames,
} from "@/lib/testruns";

/* Jira bugs from test results (docs/specs/30a G3, docs/specs/30 §15): bug states next to defects,
 * "Cần retest", creating / linking a bug from the test drawer, and the dự án's test settings. */

/* ---------------- Bug states: one request per render pass ---------------- */

const KEY_RE = /^[A-Z][A-Z0-9_]{1,9}-\d{1,7}$/;
const TTL_MS = 5 * 60 * 1000;
const known = new Map<string, { at: number; state: BugState }>();
const pending = new Map<number, Set<string>>(); // dự án -> keys to ask
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

function notify() {
  listeners.forEach((l) => l());
}

async function flush() {
  timer = null;
  const groups = [...pending].map(([bp, set]) => [bp, [...set]] as const);
  pending.clear();
  for (const [bp, keys] of groups) {
    for (let i = 0; i < keys.length; i += 100) {
      const chunk = keys.slice(i, i + 100);
      try {
        const got = await api.get<BugState[]>(`/testcases/bugs/status?business_project_id=${bp}&keys=${encodeURIComponent(chunk.join(","))}`);
        const now = Date.now();
        for (const s of got) known.set(`${bp}:${s.key}`, { at: now, state: s });
      } catch {
        /* states are a hint: unknown keys stay plain */
      }
    }
  }
  notify();
}

function want(bp: number, keys: string[]) {
  const now = Date.now();
  let added = false;
  for (const k of keys) {
    if (!KEY_RE.test(k)) continue;
    const hit = known.get(`${bp}:${k}`);
    if (hit && now - hit.at < TTL_MS) continue;
    const set = pending.get(bp) ?? new Set<string>();
    if (!set.has(k)) {
      set.add(k);
      pending.set(bp, set);
      added = true;
    }
  }
  if (added && timer === null) timer = setTimeout(() => void flush(), 30);
}

/** Forget cached states (after a bug was made or linked) so the next render asks again. */
export function forgetBugStates(keys?: string[]) {
  if (!keys) return known.clear();
  for (const id of [...known.keys()]) if (keys.includes(id.slice(id.indexOf(":") + 1))) known.delete(id);
}

/** key -> its state, for the keys asked in one dự án; the requests of every component are batched. */
export function useBugStates(bp: number | null | undefined, keys: string[]): Record<string, BugState> {
  const [, setTick] = useState(0);
  const joined = keys.join(",");
  useEffect(() => {
    const listener = () => setTick((n) => n + 1);
    listeners.add(listener);
    if (bp) want(bp, joined ? joined.split(",") : []);
    return () => {
      listeners.delete(listener);
    };
  }, [bp, joined]);
  const out: Record<string, BugState> = {};
  for (const k of keys) {
    const hit = bp ? known.get(`${bp}:${k}`) : undefined;
    if (hit) out[k] = hit.state;
  }
  return out;
}

function tone(state: BugState | undefined): "good" | "warn" | undefined {
  if (!state) return undefined;
  return state.state === "closed" ? "good" : state.state === "opened" ? "warn" : undefined;
}

function stateTitle(key: string, s: BugState | undefined): string {
  if (!s) return key;
  const what = s.status ? `${BUG_STATE_LABEL[s.state]} (${s.status})` : BUG_STATE_LABEL[s.state];
  return `${key}: ${what}${s.title ? ` · ${s.title}` : ""}`;
}

/** Each bug as a pill coloured by its state (open: amber, closed: green), linking to Jira. */
export function BugPills({ keys, bp }: { keys: string[]; bp: number | null | undefined }) {
  const states = useBugStates(bp, keys);
  if (!keys.length) return null;
  return (
    <span className="jb-pills">
      {keys.map((k) => {
        const s = states[k];
        const label = (
          <Pill tone={tone(s)} title={stateTitle(k, s)}>
            {k}
            {s && s.state !== "unknown" ? ` · ${BUG_STATE_LABEL[s.state]}` : ""}
          </Pill>
        );
        return s?.web_url ? (
          <ExternalLink key={k} href={s.web_url}>
            {label}
          </ExternalLink>
        ) : (
          <span key={k}>{label}</span>
        );
      })}
    </span>
  );
}

/** "Bug: 2 (1 mở, 1 đóng)" for a row of the run table. */
export function BugCount({ keys, bp }: { keys: string[]; bp: number | null | undefined }) {
  const states = useBugStates(bp, keys);
  if (!keys.length) return null;
  const open = keys.filter((k) => states[k]?.state === "opened").length;
  const closed = keys.filter((k) => states[k]?.state === "closed").length;
  const parts = [open ? `${open} mở` : "", closed ? `${closed} đóng` : ""].filter(Boolean);
  return (
    <span title={keys.map((k) => stateTitle(k, states[k])).join("\n")}>
      {` · Bug: ${keys.length}`}
      {parts.length ? ` (${parts.join(", ")})` : ""}
    </span>
  );
}

/** A failed test of an open run whose bugs are all closed: run it again. */
export function RetestPill({ test, spaced }: { test: Pick<TestOut, "id"> & { needs_retest?: boolean }; spaced?: boolean }) {
  if (!test.needs_retest) return null;
  return (
    <>
      {spaced && " "}
      <Pill tone="info" icon="sync" title="Mọi bug của test này đã đóng: chạy lại để xác nhận">
        Cần retest
      </Pill>
    </>
  );
}

/* ---------------- The test drawer: create / link a bug ---------------- */

/** A step's text for a bug title: the step bug's "[C12] <step> – Failed" (docs/specs/31 §20.3). */
function stepText(detail: TestDetail, step: number): string {
  const s = (detail.content.steps_list ?? [])[step];
  return (s?.content ?? "").replace(/\s+/g, " ").trim() || `Bước ${step + 1}`;
}

/** A bug as a CodePulse task (docs/specs/31 §19.2): title, note, priority, who fixes it. With
 * `step`, a bug from that failed step of result `fromResult` (§20.3). */
function CreateTaskBugModal({
  detail,
  bp,
  prefs,
  onClose,
  onDone,
  step,
  fromResult,
}: {
  detail: TestDetail;
  bp: number;
  prefs: TestProjectSettings;
  onClose: () => void;
  onDone: (key: string) => void;
  step?: number;
  fromResult?: number;
}) {
  const failed = detail.results.filter((r) => r.status === "failed");
  const [resultId, setResultId] = useState<number | null>(fromResult ?? failed[0]?.id ?? null);
  const caseKey = detail.content.case_key ?? detail.case_key;
  const [summary, setSummary] = useState(`${caseKey ? `[${caseKey}] ` : ""}${step !== undefined ? stepText(detail, step) : detail.content.title || detail.title} – Failed`);
  const [extra, setExtra] = useState("");
  const spaces = useApi<TaskSpace[]>("/tasks/spaces");
  const space = spaces.data?.find((s) => s.business_project_id === bp) ?? null;
  const people = useApi<TaskPerson[]>(`/tasks/people?space=${bp}`);
  const defaults = useApi<BugDefaults>(`/testcases/tests/${detail.id}/bug/defaults`);
  const [priority, setPriority] = useState<string>("");
  // null: not chosen yet, so the covered task's assignee (§20.3) is preselected.
  const [picked, setPicked] = useState<string | null>(null);
  const preset = defaults.data?.assignee_id ? String(defaults.data.assignee_id) : "";
  const assignee = picked ?? preset;
  const [labels, setLabels] = useState(prefs.jira_labels.join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const priorities = [...(space?.priorities ?? [])].sort((a, b) => a.position - b.position);
  const submit = async () => {
    setBusy(true);
    setError(null);
    const body: BugIn = {
      summary: summary.trim() || undefined,
      description: extra.trim() || undefined,
      labels: splitNames(labels),
      priority: priority || undefined,
      // Untouched before the default loaded: left out, so the server picks the same default.
      ...(picked === null && !defaults.data ? {} : { assignee_id: assignee ? Number(assignee) : null }),
      result_id: resultId ?? undefined,
      step,
    };
    try {
      const out = await api.post<BugOut>(`/testcases/tests/${detail.id}/bug`, body);
      onDone(out.key);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={step !== undefined ? `Tạo bug từ bước ${step + 1} của ${detail.key}` : `Tạo bug từ ${detail.key}`}
      onClose={onClose}
      busy={busy}
      width={600}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !failed.length || !summary.trim()}>
            {busy ? "Đang tạo…" : "Tạo bug"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        {!failed.length && <Alert tone="info">Test chưa có kết quả Failed: ghi kết quả Failed trước khi tạo bug.</Alert>}
        <p className="small muted">
          Bug thành task <strong>{prefs.task_space_key ?? ""}</strong> trong mục Task. Mô tả tự gồm các bước (mong đợi / thực tế), môi trường, log, đính kèm và link về testcase; bug đóng thì test được gợi ý chạy lại.
        </p>
        {step !== undefined && <p className="small muted qa-step-note">Mô tả gồm các bước 1–{step + 1} (mong đợi / thực tế), dừng ở bước lỗi.</p>}
        {failed.length > 1 && step === undefined && (
          <label className="adm-field">
            <span className="small muted">Kết quả Failed</span>
            <select value={resultId ?? ""} onChange={(e) => setResultId(Number(e.target.value))}>
              {failed.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.created_at ? r.created_at.slice(0, 16).replace("T", " ") : `#${r.id}`}
                  {r.created_by ? ` · ${r.created_by.name}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="adm-field">
          <span className="small muted">Tiêu đề</span>
          <input value={summary} maxLength={250} onChange={(e) => setSummary(e.target.value)} />
        </label>
        <label className="adm-field">
          <span className="small muted">Ghi chú thêm (đầu mô tả)</span>
          <textarea rows={3} value={extra} onChange={(e) => setExtra(e.target.value)} />
        </label>
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Ưu tiên</span>
            <select value={priority} onChange={(e) => setPriority(e.target.value)}>
              <option value="">Mặc định của dự án</option>
              {priorities.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="adm-field">
            <span className="small muted">Người xử lý</span>
            <select value={assignee} onChange={(e) => setPicked(e.target.value)} disabled={!people.data}>
              <option value="">Chưa giao</option>
              {(people.data ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name ?? u.primary_email ?? `#${u.id}`}
                </option>
              ))}
            </select>
            {defaults.data?.task_key && preset && assignee === preset && <span className="small muted">Người làm {defaults.data.task_key} (task testcase phủ)</span>}
          </label>
        </div>
        <label className="adm-field">
          <span className="small muted">Nhãn</span>
          <input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="qa, regression" />
        </label>
      </div>
    </Modal>
  );
}

function CreateBugModal({ detail, prefs, onClose, onDone }: { detail: TestDetail; prefs: TestProjectSettings; onClose: () => void; onDone: (key: string) => void }) {
  const failed = detail.results.filter((r) => r.status === "failed");
  const [resultId, setResultId] = useState<number | null>(failed[0]?.id ?? null);
  const caseKey = detail.content.case_key ?? detail.case_key;
  const [summary, setSummary] = useState(`${caseKey ? `[${caseKey}] ` : ""}${detail.content.title || detail.title} – Failed`);
  const [extra, setExtra] = useState("");
  const [issueType, setIssueType] = useState(prefs.jira_issue_type || "Bug");
  const [components, setComponents] = useState(prefs.jira_components.join(", "));
  const [labels, setLabels] = useState(prefs.jira_labels.join(", "));
  const [assignee, setAssignee] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    const body: BugIn = {
      summary: summary.trim() || undefined,
      description: extra.trim() || undefined,
      issue_type: issueType.trim() || undefined,
      components: splitNames(components),
      labels: splitNames(labels),
      assignee: assignee.trim() || undefined,
      result_id: resultId ?? undefined,
    };
    try {
      const out = await api.post<BugOut>(`/testcases/tests/${detail.id}/bug`, body);
      onDone(out.key);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Tạo bug Jira từ ${detail.key}`}
      onClose={onClose}
      busy={busy}
      width={640}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !failed.length || !summary.trim()}>
            {busy ? "Đang tạo…" : "Tạo bug"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        {!failed.length && <Alert tone="info">Test chưa có kết quả Failed: ghi kết quả Failed trước khi tạo bug.</Alert>}
        <p className="small muted">
          Bug vào project <strong>{prefs.jira_project_key}</strong>. Mô tả tự gồm các bước (mong đợi / thực tế), môi trường, bình luận, đính kèm và link về CodePulse.
        </p>
        {failed.length > 1 && (
          <label className="adm-field">
            <span className="small muted">Kết quả Failed</span>
            <select value={resultId ?? ""} onChange={(e) => setResultId(Number(e.target.value))}>
              {failed.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.created_at ? r.created_at.slice(0, 16).replace("T", " ") : `#${r.id}`}
                  {r.created_by ? ` · ${r.created_by.name}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="adm-field">
          <span className="small muted">Tiêu đề</span>
          <input value={summary} maxLength={250} onChange={(e) => setSummary(e.target.value)} />
        </label>
        <label className="adm-field">
          <span className="small muted">Ghi chú thêm (đầu mô tả)</span>
          <textarea rows={3} value={extra} onChange={(e) => setExtra(e.target.value)} />
        </label>
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Loại issue</span>
            <input value={issueType} onChange={(e) => setIssueType(e.target.value)} />
          </label>
          <label className="adm-field">
            <span className="small muted">Người xử lý (username Jira)</span>
            <input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="Để trống: theo Jira" />
          </label>
        </div>
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Components</span>
            <input value={components} onChange={(e) => setComponents(e.target.value)} placeholder="Backend, CRM" />
          </label>
          <label className="adm-field">
            <span className="small muted">Labels</span>
            <input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="qa, regression" />
          </label>
        </div>
      </div>
    </Modal>
  );
}

function LinkBugModal({ detail, onClose, onDone }: { detail: TestDetail; onClose: () => void; onDone: (key: string) => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clean = key.trim().toUpperCase();
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<BugOut>(`/testcases/tests/${detail.id}/bug/link`, { key: clean });
      onDone(out.key);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Gắn bug có sẵn vào ${detail.key}`}
      onClose={onClose}
      busy={busy}
      width={440}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !KEY_RE.test(clean)}>
            Gắn bug
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <label className="adm-field">
          <span className="small muted">Mã task (CRM-12) hoặc mã issue Jira</span>
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="ABC-123"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && KEY_RE.test(clean) && !busy) void submit();
            }}
          />
        </label>
        <p className="small muted">Mã được thêm vào ô Bug của kết quả gần nhất. Task CodePulse được kiểm tra ngay; issue Jira được kiểm tra khi kết nối được.</p>
      </div>
    </Modal>
  );
}

/** "Tạo bug" next to a failed step of a result in the test drawer (docs/specs/31 §20.3): only when
 * bugs go to CodePulse tasks, the person may record and the result has no bug yet. */
export function StepBugButton({ detail, resultId, step, onChanged }: { detail: TestDetail; resultId: number; step: number; onChanged: () => void }) {
  const run = useApi<RunOut>(`/testcases/runs/${detail.run_id}`);
  const bpId = run.data?.business_project_id;
  const prefs = useApi<TestProjectSettings>(bpId ? `/testcases/project-settings?business_project_id=${bpId}` : null);
  const [open, setOpen] = useState(false);
  const p = prefs.data;
  const result = detail.results.find((r) => r.id === resultId);
  if (!p || !bpId || p.bug_target_effective !== "codepulse" || !p.task_space_key) return null;
  if (!detail.can_record || detail.run.completed || result?.status !== "failed" || result.defects.length) return null;
  return (
    <>
      <button type="button" className="sm ghost qa-step-bug" onClick={() => setOpen(true)} title={`Tạo task Bug từ bước ${step + 1}`}>
        <Icon name="plus" size={12} /> Tạo bug
      </button>
      {open && (
        <CreateTaskBugModal
          detail={detail}
          bp={bpId}
          prefs={p}
          step={step}
          fromResult={resultId}
          onClose={() => setOpen(false)}
          onDone={(key) => {
            forgetBugStates([key]);
            setOpen(false);
            onChanged();
          }}
        />
      )}
    </>
  );
}

/** In the test drawer: the test's bugs with their states, "Cần retest", create / link. */
export function JiraBugPanel({ detail, onChanged }: { detail: TestDetail; onChanged: () => void }) {
  const run = useApi<RunOut>(`/testcases/runs/${detail.run_id}`);
  const bpId = run.data?.business_project_id;
  const prefs = useApi<TestProjectSettings>(bpId ? `/testcases/project-settings?business_project_id=${bpId}` : null);
  const [dialog, setDialog] = useState<"create" | "link" | null>(null);
  const [made, setMade] = useState<string | null>(null);
  const canAct = detail.can_record && !detail.run.completed;
  const p = prefs.data;
  const toTask = p?.bug_target_effective === "codepulse";
  const createBlocked = !p
    ? "Đang tải cài đặt dự án…"
    : toTask
      ? !p.task_space_key
        ? "Dự án chưa bật Task (Task › Cài đặt), hoặc chọn gửi bug lên Jira ở Cài đặt dự án"
        : !detail.results.some((r) => r.status === "failed")
          ? "Cần một kết quả Failed để tạo bug"
          : null
    : !p.jira_write_enabled
      ? "Chưa bật tạo bug lên Jira (Admin › Jira › Kết nối)"
      : !p.jira_configured
        ? "Chưa cấu hình kết nối Jira"
        : !p.jira_project_key
          ? "Chưa chọn project Jira nhận bug (Thiết lập › Cài đặt dự án)"
          : !detail.results.some((r) => r.status === "failed")
            ? "Cần một kết quả Failed để tạo bug"
            : null;
  const done = (key: string) => {
    forgetBugStates([key]);
    setDialog(null);
    setMade(key);
    onChanged();
  };
  if (!detail.defects.length && !canAct) return null;
  return (
    <div className="tr-block jb-panel">
      <h4>{toTask ? "Bug" : "Bug Jira"}</h4>
      {made && <Alert tone="success">Đã gắn {made} vào kết quả.</Alert>}
      <div className="jb-row">
        {detail.defects.length ? <BugPills keys={detail.defects} bp={bpId} /> : <span className="small muted">Chưa có bug.</span>}
        <RetestPill test={detail} />
      </div>
      {canAct && (
        <div className="jb-row">
          <button
            type="button"
            className="sm"
            onClick={() => setDialog("create")}
            disabled={createBlocked !== null}
            title={createBlocked ?? (toTask ? "Tạo task Bug từ kết quả Failed" : "Tạo issue trên Jira từ kết quả Failed")}
          >
            <Icon name="plus" size={14} /> {toTask ? "Tạo bug" : "Tạo bug Jira"}
          </button>
          <button type="button" className="sm ghost" onClick={() => setDialog("link")}>
            Gắn bug có sẵn
          </button>
          {createBlocked && p && <span className="small muted">{createBlocked}</span>}
        </div>
      )}
      {dialog === "create" && p && toTask && bpId && <CreateTaskBugModal detail={detail} bp={bpId} prefs={p} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "create" && p && !toTask && <CreateBugModal detail={detail} prefs={p} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "link" && <LinkBugModal detail={detail} onClose={() => setDialog(null)} onDone={done} />}
    </div>
  );
}

/* ---------------- Thiết lập › Cài đặt dự án ---------------- */

export function ProjectSettingsCard({ projectId }: { projectId: number }) {
  const got = useApi<TestProjectSettings>(`/testcases/project-settings?business_project_id=${projectId}`);
  const [draft, setDraft] = useState<{ key: string; type: string; components: string; labels: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const s = got.data;
  const save = async (body: Partial<TestProjectSettings>) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await got.mutate(await api.put<TestProjectSettings>("/testcases/project-settings", { business_project_id: projectId, ...body }), { revalidate: false });
      setSaved(true);
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const edit = () =>
    s && setDraft({ key: s.jira_project_key ?? "", type: s.jira_issue_type, components: s.jira_components.join(", "), labels: s.jira_labels.join(", ") });
  return (
    <Card title="Cài đặt dự án" id="tr-project-settings" subtitle="Ai ghi kết quả, quy trình duyệt testcase và nơi nhận bug">
      {!s ? (
        <Loading error={got.error} rows={3} />
      ) : (
        <div className="adm-form">
          {error && <Alert tone="error">{error}</Alert>}
          {saved && !error && <span className="small muted" role="status">Đã lưu.</span>}
          <label className="jira-switch">
            <input type="checkbox" role="switch" checked={s.assigned_only} disabled={!s.can_edit || busy} onChange={(e) => void save({ assigned_only: e.target.checked })} /> Chỉ người được giao mới ghi kết quả
          </label>
          <label className="jira-switch">
            <input type="checkbox" role="switch" checked={s.require_approval} disabled={!s.can_edit || busy} onChange={(e) => void save({ require_approval: e.target.checked })} /> Bắt buộc duyệt testcase
          </label>
          <h4>Nơi nhận bug</h4>
          <div className="jb-row" role="radiogroup" aria-label="Nơi nhận bug">
            <label className="inline-check">
              <input
                type="radio"
                name={`bug-target-${projectId}`}
                checked={(s.bug_target ?? "codepulse") === "codepulse"}
                disabled={!s.can_edit || busy}
                onChange={() => void save({ bug_target: "codepulse" })}
              />
              Task CodePulse{s.task_space_key ? ` (${s.task_space_key})` : " (dự án chưa bật Task)"}
            </label>
            <label className="inline-check">
              <input type="radio" name={`bug-target-${projectId}`} checked={s.bug_target === "jira"} disabled={!s.can_edit || busy || !!s.jira_retired} onChange={() => void save({ bug_target: "jira" })} />
              Issue Jira
            </label>
          </div>
          {s.jira_retired && <p className="small muted">Dự án đã ngừng dùng Jira (Task › Cài đặt › Chuyển đổi khỏi Jira): bug luôn tạo thành task CodePulse.</p>}
          {s.bug_target_effective && s.bug_target_effective !== (s.bug_target ?? "codepulse") && (
            <p className="small muted">Dự án chưa bật Task nên bug vẫn tạo trên Jira (project đã chọn bên dưới).</p>
          )}
          <h4>Bug Jira</h4>
          <p className="small muted">
            {s.jira_write_enabled ? (
              <Pill tone="good">Đã bật tạo bug lên Jira</Pill>
            ) : (
              <Pill tone="warn">Chưa bật tạo bug lên Jira</Pill>
            )}{" "}
            {!s.jira_configured
              ? "Chưa cấu hình kết nối Jira (Admin › Jira › Kết nối)."
              : !s.jira_write_enabled
                ? "Admin bật ở Admin › Jira › Kết nối (hoặc JIRA_WRITE_ENABLED); token cần quyền tạo issue."
                : s.jira_base_url ?? ""}
          </p>
          {draft ? (
            <>
              <div className="tr-form-row">
                <label className="adm-field">
                  <span className="small muted">Project Jira (mã)</span>
                  <input value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value.toUpperCase() })} placeholder="OMX" />
                </label>
                <label className="adm-field">
                  <span className="small muted">Loại issue</span>
                  <input value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })} placeholder="Bug" />
                </label>
              </div>
              <div className="tr-form-row">
                <label className="adm-field">
                  <span className="small muted">Components</span>
                  <input value={draft.components} onChange={(e) => setDraft({ ...draft, components: e.target.value })} placeholder="Backend, CRM" />
                </label>
                <label className="adm-field">
                  <span className="small muted">Labels</span>
                  <input value={draft.labels} onChange={(e) => setDraft({ ...draft, labels: e.target.value })} placeholder="qa" />
                </label>
              </div>
              <div className="jb-row">
                <button type="button" onClick={() => setDraft(null)} disabled={busy}>
                  Huỷ
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    void save({
                      jira_project_key: draft.key.trim() || null,
                      jira_issue_type: draft.type.trim() || "Bug",
                      jira_components: splitNames(draft.components),
                      jira_labels: splitNames(draft.labels),
                    }).then((ok) => ok && setDraft(null))
                  }
                >
                  Lưu
                </button>
              </div>
            </>
          ) : (
            <dl className="jira-kvs">
              <div className="jira-kv">
                <dt>Project</dt>
                <dd>{s.jira_project_key ?? <span className="muted">Chưa chọn</span>}</dd>
              </div>
              <div className="jira-kv">
                <dt>Loại issue</dt>
                <dd>{s.jira_issue_type}</dd>
              </div>
              <div className="jira-kv">
                <dt>Components</dt>
                <dd>{s.jira_components.join(", ") || <span className="muted">—</span>}</dd>
              </div>
              <div className="jira-kv">
                <dt>Labels</dt>
                <dd>{s.jira_labels.join(", ") || <span className="muted">—</span>}</dd>
              </div>
              {s.can_edit && (
                <button type="button" className="sm" onClick={edit}>
                  Sửa
                </button>
              )}
            </dl>
          )}
        </div>
      )}
    </Card>
  );
}
