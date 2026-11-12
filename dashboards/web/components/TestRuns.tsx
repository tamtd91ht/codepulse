"use client";

import { useDeferredValue, useEffect, useMemo, useState } from "react";

import { CiBadge, CiImportModal, CiResult, FlakyPill } from "@/components/CiImport";
import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import { CreatePlanModal } from "@/components/TestPlans";
import {
  ConfirmModal,
  MilestoneSelect,
  PersonSelect,
  RunRow,
  SelectionPicker,
  StatusBar,
  StatusButtons,
  StatusPill,
  initialPick,
  selectionOf,
  splitKeys,
  useSetupLists,
} from "@/components/TestRunParts";
import { Alert, Card, Drawer, EmptyState, Loading, Segmented } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import { type CaseType, PRIORITY_LABEL, type Priority, TYPE_LABEL } from "@/lib/testcases";
import {
  RESULT_STATUSES,
  SOURCE_LABEL,
  TEST_STATUSES,
  TEST_STATUS_LABEL,
  durationText,
  errorText,
  refsText,
  type PlanOut,
  type ResultStatus,
  type RunOut,
  type Selection,
  type StepView,
  type TestDetail,
  type TestList,
  type TestOut,
  type TestStatus,
} from "@/lib/testruns";
import type { Me } from "@/lib/types";

/*
 * Test run (docs/specs/30 §13.6, API §14.3): runs and plans of a dự án, one run's tests grouped by
 * thư mục with quick and bulk results, and a drawer to execute one test (steps, result per step,
 * history), moving to the next test without closing.
 */

const priorityText = (p: string | null) => (p ? (PRIORITY_LABEL[p as Priority] ?? p) : "—");

function two(x: number) {
  return String(x).padStart(2, "0");
}

function defaultRunName(initial?: CreateRunProps["initial"]): string {
  const d = new Date();
  if (initial?.regression) return `Regression ${initial.regression.days} ngày (${two(d.getDate())}/${two(d.getMonth() + 1)})`;
  return `Test run ${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()}`;
}

/* ---------------- Create / edit a run ---------------- */

type CreateRunProps = {
  projectId: number;
  initial?: { suite_id?: number; case_ids?: number[]; section_ids?: number[]; regression?: { days: number; target_branch?: string } };
  onClose: () => void;
  onCreated: (runId: number) => void;
};

export function CreateRunModal({ projectId, initial, onClose, onCreated }: CreateRunProps) {
  const { milestones, people } = useSetupLists(projectId);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [milestone, setMilestone] = useState<number | null>(null);
  const [assignee, setAssignee] = useState<number | null>(null);
  const [refs, setRefs] = useState("");
  const [pick, setPick] = useState(() => initialPick(initial as Selection | undefined));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selection = selectionOf(pick, initial as Selection | undefined);

  const submit = async () => {
    if (!selection) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.post<RunOut>("/testcases/runs", {
        business_project_id: projectId,
        name: name.trim() || undefined,
        description: description.trim() || undefined,
        milestone_id: milestone ?? undefined,
        assigned_to: assignee ?? undefined,
        refs: splitKeys(refs),
        selection,
      });
      onCreated(run.id);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Tạo test run"
      onClose={onClose}
      busy={busy}
      width={640}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !selection}>
            {busy ? "Đang tạo…" : "Tạo test run"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <label className="adm-field">
          <span className="small muted">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={defaultRunName(initial)} />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Milestone</span>
            <MilestoneSelect value={milestone} onChange={setMilestone} milestones={milestones.data} />
          </label>
          <label className="adm-field">
            <span className="small muted">Giao cho</span>
            <PersonSelect value={assignee} onChange={setAssignee} people={people.data} />
          </label>
        </div>
        <label className="adm-field">
          <span className="small muted">Mã Jira (References)</span>
          <input value={refs} onChange={(e) => setRefs(e.target.value)} placeholder="ABC-12, ABC-15" />
        </label>
        <SelectionPicker projectId={projectId} pick={pick} onChange={setPick} initial={initial as Selection | undefined} />
      </div>
    </Modal>
  );
}

function EditRunModal({ run, onClose, onSaved }: { run: RunOut; onClose: () => void; onSaved: () => void }) {
  const { milestones, people } = useSetupLists(run.business_project_id);
  const [name, setName] = useState(run.name);
  const [description, setDescription] = useState(run.description ?? "");
  const [milestone, setMilestone] = useState<number | null>(run.milestone_id);
  const [assignee, setAssignee] = useState<number | null>(run.assigned_to?.id ?? null);
  const [refs, setRefs] = useState(refsText(run.refs));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/testcases/runs/${run.id}`, {
        name: name.trim() || run.name,
        description: description.trim() || null,
        milestone_id: milestone,
        assigned_to: assignee,
        refs: splitKeys(refs),
      });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Sửa ${run.key}`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !name.trim()}>
            Lưu
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <label className="adm-field">
          <span className="small muted">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Milestone</span>
            <MilestoneSelect value={milestone} onChange={setMilestone} milestones={milestones.data} />
          </label>
          <label className="adm-field">
            <span className="small muted">Giao cho</span>
            <PersonSelect value={assignee} onChange={setAssignee} people={people.data} />
          </label>
        </div>
        <label className="adm-field">
          <span className="small muted">Mã Jira (References)</span>
          <input value={refs} onChange={(e) => setRefs(e.target.value)} />
        </label>
      </div>
    </Modal>
  );
}

function AddCasesModal({ run, onClose, onSaved }: { run: RunOut; onClose: () => void; onSaved: () => void }) {
  const [pick, setPick] = useState(() => initialPick(null));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selection = selectionOf(pick, null);
  const submit = async () => {
    if (!selection) return;
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/testcases/runs/${run.id}`, { add: selection });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Thêm testcase vào ${run.key}`}
      onClose={onClose}
      busy={busy}
      width={600}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !selection}>
            Thêm
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <p className="small muted">Testcase đã có trong run được bỏ qua.</p>
        <SelectionPicker projectId={run.business_project_id} pick={pick} onChange={setPick} />
      </div>
    </Modal>
  );
}

/* ---------------- Tab "Test run": runs and plans ---------------- */

export function RunsTab({
  projectId,
  canEdit,
  onOpenRun,
  onOpenPlan,
}: {
  projectId: number;
  canEdit: boolean;
  onOpenRun: (id: number) => void;
  onOpenPlan: (id: number) => void;
}) {
  const [state, setState] = useState<"open" | "completed">("open");
  const runs = useApi<RunOut[]>(`/testcases/runs?business_project_id=${projectId}&state=${state}`, { keepPreviousData: true });
  const plans = useApi<PlanOut[]>(`/testcases/plans?business_project_id=${projectId}&state=${state}`, { keepPreviousData: true });
  const [creating, setCreating] = useState<"run" | "plan" | "ci" | null>(null);
  const single = (runs.data ?? []).filter((r) => r.plan_id == null);

  return (
    <div className="tr-tab">
      <div className="toolbar">
        <Segmented
          label="Trạng thái"
          value={state}
          onChange={setState}
          options={[
            { value: "open", label: "Đang mở" },
            { value: "completed", label: "Đã đóng" },
          ]}
        />
        <span className="spacer" />
        {canEdit && (
          <>
            <button type="button" className="sm" onClick={() => setCreating("ci")}>
              <Icon name="upload" size={14} /> Nhập kết quả CI
            </button>
            <button type="button" className="sm" onClick={() => setCreating("plan")}>
              <Icon name="folder" size={14} /> Tạo test plan
            </button>
            <button type="button" className="sm primary" onClick={() => setCreating("run")}>
              <Icon name="plus" size={14} /> Tạo test run
            </button>
          </>
        )}
      </div>
      {!runs.data || !plans.data ? (
        <Loading error={runs.error ?? plans.error} rows={5} />
      ) : !single.length && !plans.data.length ? (
        <EmptyState icon="clipboardCheck" title={state === "open" ? "Chưa có test run nào đang mở" : "Chưa có test run nào đã đóng"}>
          {state === "open" && canEdit
            ? "Tạo test run từ một bộ, thư mục, bộ lọc, hoặc từ danh sách Cần test lại; test plan gom nhiều run theo cấu hình."
            : undefined}
        </EmptyState>
      ) : (
        <>
          {plans.data.length > 0 && (
            <Card title={`Test plan (${plans.data.length})`} id="tr-plans">
              <ul className="tr-list">
                {plans.data.map((p) => (
                  <li key={p.id} className="tr-item">
                    <div className="tr-item-main">
                      <div className="tr-item-title">
                        <span className="tr-key">{p.key}</span>
                        <button type="button" className="link" onClick={() => onOpenPlan(p.id)}>
                          {p.name}
                        </button>
                        {p.completed_at && <span className="pill">Đã đóng</span>}
                      </div>
                      <div className="small muted tr-item-meta">
                        <span>{Array.isArray(p.runs) ? p.runs.length : p.runs} run</span>
                        {p.milestone_name && <span>Milestone: {p.milestone_name}</span>}
                        <span>Tạo {dateTimeMin(p.created_at)}</span>
                      </div>
                    </div>
                    <StatusBar counts={p.counts} compact />
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card title={`Test run (${single.length})`} id="tr-runs" subtitle="Run thuộc plan nằm trong trang của plan">
            {single.length ? (
              <ul className="tr-list">
                {single.map((r) => (
                  <RunRow key={r.id} run={r} onOpen={onOpenRun} />
                ))}
              </ul>
            ) : (
              <p className="small muted">Không có run lẻ nào.</p>
            )}
          </Card>
        </>
      )}
      {creating === "run" && (
        <CreateRunModal
          projectId={projectId}
          onClose={() => setCreating(null)}
          onCreated={(id) => {
            setCreating(null);
            void runs.mutate();
            onOpenRun(id);
          }}
        />
      )}
      {creating === "ci" && (
        <CiImportModal
          projectId={projectId}
          onClose={() => {
            setCreating(null);
            void runs.mutate();
          }}
          onOpenRun={(id) => {
            setCreating(null);
            void runs.mutate();
            onOpenRun(id);
          }}
        />
      )}
      {creating === "plan" && (
        <CreatePlanModal
          projectId={projectId}
          onClose={() => setCreating(null)}
          onCreated={(id) => {
            setCreating(null);
            void plans.mutate();
            onOpenPlan(id);
          }}
        />
      )}
    </div>
  );
}

/* ---------------- One run ---------------- */

type Dialog = "edit" | "add" | "close" | "delete" | "remove" | null;

export function RunView({ runId, projectId, onBack, onOpenPlan }: { runId: number; projectId: number; onBack: () => void; onOpenPlan?: (id: number) => void }) {
  const { data: me } = useApi<Me>("/auth/me");
  const run = useApi<RunOut>(`/testcases/runs/${runId}`);
  const { people } = useSetupLists(projectId);
  const [status, setStatus] = useState<TestStatus | "">("");
  const [assignee, setAssignee] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const q = useDeferredValue(search.trim());
  const tests = useApi<TestList>(`/testcases/runs/${runId}/tests${qs({ status, assigned_to: assignee, q })}`, { keepPreviousData: true });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<number | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const r = run.data;
  const completed = !!r?.completed_at;

  // Grouped by thư mục, in the order the server lists them; the drawer walks the same order.
  const groups = useMemo(() => {
    const out: { section: string; items: TestOut[] }[] = [];
    for (const t of tests.data?.items ?? []) {
      const section = t.section || "Không thư mục";
      const last = out[out.length - 1];
      const found = last?.section === section ? last : out.find((g) => g.section === section);
      if (found) found.items.push(t);
      else out.push({ section, items: [t] });
    }
    return out;
  }, [tests.data]);
  const order = useMemo(() => groups.flatMap((g) => g.items.map((t) => t.id)), [groups]);

  useEffect(() => {
    // Drop selections that the filters hid.
    setSelected((s) => new Set([...s].filter((id) => order.includes(id))));
  }, [order]);

  const refresh = () => {
    void tests.mutate();
    void run.mutate();
  };

  if (run.error?.status === 404)
    return (
      <EmptyState icon="alert" title="Không tìm thấy test run">
        <button type="button" className="link" onClick={onBack}>
          Về danh sách test run
        </button>
      </EmptyState>
    );
  if (!r) return <Loading error={run.error} rows={6} />;

  const canRecord = (t: TestOut) => !completed && (r.can_edit || (!!me && (t.assigned_to?.id === me.id || r.assigned_to?.id === me.id)));
  const anyRecord = !completed && (r.can_edit || (tests.data?.items ?? []).some(canRecord));
  const editable = r.can_edit && !completed;

  const act = async (fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      after?.();
      refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const quick = (t: TestOut, s: ResultStatus) => void act(() => api.post(`/testcases/tests/${t.id}/results`, { status: s }));
  const bulk = (body: { status?: ResultStatus; assigned_to?: number | null }) =>
    void act(() => api.post(`/testcases/runs/${r.id}/results`, { test_ids: [...selected], ...body }), () => setSelected(new Set()));

  const startHere = () => {
    const first = (tests.data?.items ?? []).find((t) => t.status === "untested" && canRecord(t)) ?? tests.data?.items[0];
    if (first) setOpen(first.id);
  };

  const allIds = order;
  const allChecked = allIds.length > 0 && allIds.every((id) => selected.has(id));

  return (
    <div className="tr-tab">
      <div className="tr-view-head">
        <button type="button" className="ghost sm" onClick={onBack}>
          <Icon name="chevronLeft" size={14} /> Test run
        </button>
        <div className="tr-view-title">
          <span className="tr-key">{r.key}</span>
          <h2>{r.name}</h2>
          {completed && <span className="pill">Đã đóng {dateTimeMin(r.completed_at)}</span>}
        </div>
        <div className="tr-view-actions">
          {editable && (
            <>
              <IconButton icon="edit" label="Sửa run" onClick={() => setDialog("edit")} />
              <button type="button" className="sm" onClick={() => setDialog("add")}>
                <Icon name="plus" size={14} /> Thêm testcase
              </button>
            </>
          )}
          {!completed && anyRecord && (
            <button type="button" className="sm primary ink" onClick={startHere} disabled={!tests.data?.items.length}>
              <Icon name="clipboardCheck" size={14} /> Chạy test
            </button>
          )}
          <MoreMenu
            items={[
              {
                icon: "download",
                label: "Xuất Excel",
                run: () => {
                  window.location.href = `/api/v1/testcases/runs/${r.id}/export`;
                },
              },
              ...(editable ? [{ icon: "checkCircle" as const, label: "Đóng run", run: () => setDialog("close") }] : []),
              ...(r.can_edit ? [{ icon: "remove" as const, label: "Xoá run", danger: true, run: () => setDialog("delete") }] : []),
            ]}
          />
        </div>
      </div>
      <div className="small muted tr-item-meta tr-view-meta">
        {r.plan_id && (
          <span>
            Plan:{" "}
            {onOpenPlan ? (
              <button type="button" className="link" onClick={() => onOpenPlan(r.plan_id!)}>
                {r.plan_name}
              </button>
            ) : (
              r.plan_name
            )}
          </span>
        )}
        {r.config && <span>Cấu hình: {r.config}</span>}
        {r.milestone_name && <span>Milestone: {r.milestone_name}</span>}
        <span>{r.assigned_to ? `Giao: ${r.assigned_to.name}` : "Chưa giao"}</span>
        {refsText(r.refs) && <span>Jira: {refsText(r.refs)}</span>}
        <span>Nguồn: {SOURCE_LABEL[r.source] ?? r.source}</span>
        <span>
          Tạo {dateTimeMin(r.created_at)}
          {r.created_by ? ` bởi ${r.created_by.name}` : ""}
        </span>
      </div>
      {r.description && <p className="small tr-desc">{r.description}</p>}
      <div className="card tr-summary">
        <StatusBar counts={r.counts} />
      </div>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="toolbar filters">
        <label className="filter">
          <span className="small muted">Trạng thái</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as TestStatus | "")}>
            <option value="">Tất cả</option>
            {TEST_STATUSES.map((s) => (
              <option key={s} value={s}>
                {TEST_STATUS_LABEL[s]} ({r.counts[s] ?? 0})
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Người được giao</span>
          <PersonSelect value={assignee} onChange={setAssignee} people={people.data} empty="Mọi người" />
        </label>
        <label className="filter tr-search">
          <span className="small muted">Tìm</span>
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Tên, mã T / C…" />
        </label>
      </div>
      {selected.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Thao tác hàng loạt">
          <strong className="small">Đã chọn {selected.size}</strong>
          <span className="small muted">Đặt trạng thái:</span>
          <StatusButtons value={null} onChange={(s) => bulk({ status: s })} disabled={busy} size="sm" />
          {r.can_edit && (
            <label className="tr-inline small">
              <span className="muted">Giao cho</span>
              <select
                value=""
                disabled={busy}
                onChange={(e) => {
                  if (e.target.value === "") return;
                  bulk({ assigned_to: e.target.value === "none" ? null : Number(e.target.value) });
                }}
              >
                <option value="">Chọn…</option>
                <option value="none">Bỏ giao</option>
                {(people.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className="spacer" />
          {editable && (
            <button type="button" className="sm ghost tc-danger" onClick={() => setDialog("remove")} disabled={busy}>
              <Icon name="remove" size={14} /> Bỏ khỏi run
            </button>
          )}
          <button type="button" className="sm ghost" onClick={() => setSelected(new Set())}>
            Bỏ chọn
          </button>
        </div>
      )}
      <div className="card tr-tests-card">
        {!tests.data ? (
          <Loading error={tests.error} rows={6} />
        ) : !tests.data.items.length ? (
          <EmptyState icon="search" title={status || assignee || q ? "Không có test nào khớp bộ lọc" : "Run chưa có test nào"}>
            {editable && !status && !assignee && !q ? "Bấm “Thêm testcase” để đưa testcase vào run." : undefined}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="tc-table tr-tests">
              <thead>
                <tr>
                  {anyRecord && (
                    <th className="tc-check">
                      <input
                        type="checkbox"
                        aria-label="Chọn tất cả test"
                        checked={allChecked}
                        onChange={(e) => setSelected(e.target.checked ? new Set(allIds) : new Set())}
                      />
                    </th>
                  )}
                  <th>Test</th>
                  <th>Testcase</th>
                  <th>Ưu tiên</th>
                  <th>Type</th>
                  <th>Giao cho</th>
                  <th>Trạng thái</th>
                  <th>Test lần cuối</th>
                  {anyRecord && <th aria-label="Ghi nhanh" />}
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <GroupRows
                    key={g.section}
                    section={g.section}
                    items={g.items}
                    cols={anyRecord ? 9 : 7}
                    anyRecord={anyRecord}
                    selected={selected}
                    onSelect={(id, on) => {
                      const next = new Set(selected);
                      if (on) next.add(id);
                      else next.delete(id);
                      setSelected(next);
                    }}
                    canRecord={canRecord}
                    onOpen={setOpen}
                    onQuick={quick}
                    busy={busy}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tests.data && tests.data.total > tests.data.items.length && (
          <p className="small muted">
            Hiện {tests.data.items.length} / {tests.data.total} test.
          </p>
        )}
      </div>
      {open !== null && (
        <TestDrawer
          testId={open}
          order={order}
          people={people.data}
          onNavigate={setOpen}
          onClose={() => setOpen(null)}
          onChanged={refresh}
        />
      )}
      {dialog === "edit" && (
        <EditRunModal
          run={r}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            refresh();
          }}
        />
      )}
      {dialog === "add" && (
        <AddCasesModal
          run={r}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            refresh();
          }}
        />
      )}
      {dialog === "close" && (
        <ConfirmModal
          title={`Đóng ${r.key}?`}
          confirmLabel="Đóng run"
          busy={busy}
          error={error}
          onClose={() => setDialog(null)}
          onConfirm={() => void act(() => api.post(`/testcases/runs/${r.id}/close`), () => setDialog(null))}
        >
          Run đóng thì không ghi thêm kết quả, không thêm / bớt testcase. Nội dung testcase được chốt lại như lúc đóng: sửa testcase sau này không đổi run này.
          {r.counts.untested > 0 && (
            <p>
              <strong>Còn {r.counts.untested} test chưa test.</strong>
            </p>
          )}
        </ConfirmModal>
      )}
      {dialog === "delete" && (
        <ConfirmModal
          title={`Xoá ${r.key}?`}
          confirmLabel="Xoá run"
          danger
          busy={busy}
          error={error}
          onClose={() => setDialog(null)}
          onConfirm={() =>
            void act(
              () => api.delete(`/testcases/runs/${r.id}`),
              () => {
                setDialog(null);
                onBack();
              },
            )
          }
        >
          Xoá run “{r.name}” cùng {r.counts.total} test và mọi kết quả đã ghi. Không thể hoàn tác.
        </ConfirmModal>
      )}
      {dialog === "remove" && (
        <ConfirmModal
          title={`Bỏ ${selected.size} test khỏi run?`}
          confirmLabel="Bỏ khỏi run"
          danger
          busy={busy}
          error={error}
          onClose={() => setDialog(null)}
          onConfirm={() =>
            void act(
              // The dialog says results go with the tests: confirming it is the "force" the API asks.
              () => api.patch(`/testcases/runs/${r.id}`, { remove_test_ids: [...selected], force: true }),
              () => {
                setDialog(null);
                setSelected(new Set());
              },
            )
          }
        >
          Kết quả đã ghi của các test này bị xoá theo. Testcase gốc không đổi.
        </ConfirmModal>
      )}
    </div>
  );
}

function GroupRows({
  section,
  items,
  cols,
  anyRecord,
  selected,
  onSelect,
  canRecord,
  onOpen,
  onQuick,
  busy,
}: {
  section: string;
  items: TestOut[];
  cols: number;
  anyRecord: boolean;
  selected: Set<number>;
  onSelect: (id: number, on: boolean) => void;
  canRecord: (t: TestOut) => boolean;
  onOpen: (id: number) => void;
  onQuick: (t: TestOut, s: ResultStatus) => void;
  busy: boolean;
}) {
  return (
    <>
      <tr className="tr-group">
        <th colSpan={cols} scope="colgroup">
          <Icon name="folder" size={13} /> {section} <span className="muted">({items.length})</span>
        </th>
      </tr>
      {items.map((t) => {
        const rec = canRecord(t);
        return (
          <tr key={t.id}>
            {anyRecord && (
              <td className="tc-check">
                <input type="checkbox" aria-label={`Chọn ${t.key}`} checked={selected.has(t.id)} disabled={!rec} onChange={(e) => onSelect(t.id, e.target.checked)} />
              </td>
            )}
            <td className="num tc-code-cell">{t.key}</td>
            <td>
              <button type="button" className="link tc-row-title" onClick={() => onOpen(t.id)}>
                {t.title}
              </button>
              {t.flaky && <> <FlakyPill /></>}
              <div className="small muted">
                {t.case_key ?? "Testcase đã xoá"}
                {t.defects.length ? ` · Bug: ${t.defects.join(", ")}` : ""}
              </div>
            </td>
            <td className="small">{priorityText(t.priority)}</td>
            <td className="small tr-nowrap">{t.type ? (TYPE_LABEL[t.type as CaseType] ?? t.type) : "—"}</td>
            <td className="small">{t.assigned_to?.name ?? <span className="muted">—</span>}</td>
            <td>
              <StatusPill status={t.status} />
            </td>
            <td className="small tc-nowrap">{t.tested_at ? dateTimeMin(t.tested_at) : <span className="muted">—</span>}</td>
            {anyRecord && (
              <td>
                {rec && (
                  <div className="tr-quick" role="group" aria-label={`Ghi nhanh ${t.key}`}>
                    {RESULT_STATUSES.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`tr-quick-btn st-${s}`}
                        aria-pressed={t.status === s}
                        aria-label={`${TEST_STATUS_LABEL[s]} cho ${t.key}`}
                        title={TEST_STATUS_LABEL[s]}
                        disabled={busy}
                        onClick={() => onQuick(t, s)}
                      >
                        {TEST_STATUS_LABEL[s][0]}
                      </button>
                    ))}
                  </div>
                )}
              </td>
            )}
          </tr>
        );
      })}
    </>
  );
}

/* ---------------- Executing one test ---------------- */

function stepsOf(d: TestDetail): StepView[] {
  return d.content.steps_view ?? d.content.steps_list ?? [];
}

function TextBlock({ label, text }: { label: string; text: string | null | undefined }) {
  if (!text) return null;
  return (
    <div className="tr-block">
      <h4>{label}</h4>
      <div className="tr-pre">{text}</div>
    </div>
  );
}

export function TestDrawer({
  testId,
  order,
  people,
  onNavigate,
  onClose,
  onChanged,
}: {
  testId: number;
  order: number[];
  people: { id: number; name: string }[] | undefined;
  onNavigate: (id: number) => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const detail = useApi<TestDetail>(`/testcases/tests/${testId}`);
  const at = order.indexOf(testId);
  const prev = at > 0 ? order[at - 1] : null;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return;
      if (e.key === "ArrowRight" && next !== null) onNavigate(next);
      if (e.key === "ArrowLeft" && prev !== null) onNavigate(prev);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [next, prev, onNavigate]);

  const d = detail.data?.id === testId ? detail.data : undefined;
  const steps = d ? stepsOf(d) : [];
  const stepMode = d?.content.template === "steps" && steps.length > 0;
  const readOnly = !d || d.run.completed || !d.can_record;

  return (
    <Drawer
      title={d ? `${d.key} · ${d.content.title || d.title}` : "Test"}
      onClose={onClose}
      width={820}
      actions={
        <>
          <span className="small muted tr-pos">{at >= 0 ? `${at + 1}/${order.length}` : ""}</span>
          <IconButton icon="chevronLeft" label="Test trước (←)" onClick={() => prev !== null && onNavigate(prev)} disabled={prev === null} />
          <IconButton icon="chevronRight" label="Test sau (→)" onClick={() => next !== null && onNavigate(next)} disabled={next === null} />
        </>
      }
    >
      {!d ? (
        <Loading error={detail.error} rows={8} />
      ) : (
        <div className="tr-exec">
          <div className="tc-view-pills">
            <StatusPill status={d.status} />
            {d.content.case_key && <span className="pill">{d.content.case_key}</span>}
            {d.content.priority && <span className="pill">{priorityText(d.content.priority)}</span>}
            {d.content.type && <span className="pill">{TYPE_LABEL[d.content.type as CaseType] ?? d.content.type}</span>}
            {d.content.estimate_s ? <span className="pill">Ước tính {durationText(d.content.estimate_s)}</span> : null}
            {refsText(d.content.refs) && <span className="pill">{refsText(d.content.refs)}</span>}
            <span className="pill">{d.assigned_to ? `Giao: ${d.assigned_to.name}` : "Chưa giao"}</span>
          </div>
          {d.content.section && <p className="small muted">{d.content.section}</p>}
          {d.run.completed && <Alert tone="info">Run {d.run.key} đã đóng: chỉ xem, nội dung là bản chốt lúc đóng.</Alert>}
          {!d.run.completed && !d.can_record && <Alert tone="info">Bạn chỉ xem được test này (ghi kết quả cần quyền sửa hoặc được giao test).</Alert>}
          <TextBlock label="Tiền điều kiện" text={d.content.preconditions} />
          {stepMode ? null : (
            <>
              <TextBlock label="Các bước" text={d.content.steps} />
              <TextBlock label="Kết quả mong đợi" text={d.content.expected} />
            </>
          )}
          <TextBlock label="Dữ liệu test" text={d.content.test_data} />
          <TextBlock label="Ghi chú" text={d.content.notes} />
          <RecordForm key={d.id} detail={d} steps={stepMode ? steps : []} readOnly={readOnly} people={people} onSaved={(goNext) => {
            void detail.mutate();
            onChanged();
            if (goNext && next !== null) onNavigate(next);
          }} hasNext={next !== null} />
          <History detail={d} />
        </div>
      )}
    </Drawer>
  );
}

function RecordForm({
  detail,
  steps,
  readOnly,
  people,
  onSaved,
  hasNext,
}: {
  detail: TestDetail;
  steps: StepView[];
  readOnly: boolean;
  people: { id: number; name: string }[] | undefined;
  onSaved: (goNext: boolean) => void;
  hasNext: boolean;
}) {
  const [status, setStatus] = useState<ResultStatus | null>(null);
  const [comment, setComment] = useState("");
  const [version, setVersion] = useState(() => detail.results.find((x) => x.version)?.version ?? "");
  const [elapsed, setElapsed] = useState("");
  const [defects, setDefects] = useState("");
  const [assign, setAssign] = useState<number | null>(detail.assigned_to?.id ?? null);
  const [stepStatus, setStepStatus] = useState<(TestStatus | "")[]>(() => steps.map(() => ""));
  const [actual, setActual] = useState<string[]>(() => steps.map(() => ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const assignChanged = assign !== (detail.assigned_to?.id ?? null);
  const stepsTouched = stepStatus.some(Boolean) || actual.some((a) => a.trim());
  const ready = !!status || !!comment.trim() || assignChanged || stepsTouched;

  // A failed step usually fails the test: suggest it when nothing is chosen yet.
  const setStep = (i: number, s: TestStatus | "") => {
    const nextSteps = stepStatus.map((x, j) => (j === i ? s : x));
    setStepStatus(nextSteps);
    if (!status) {
      if (nextSteps.includes("failed")) setStatus("failed");
      else if (nextSteps.includes("blocked")) setStatus("blocked");
    }
    // Step n recorded: go straight on to step n+1 (user 2026-11-12).
    if (s) {
      const following = document.querySelector<HTMLSelectElement>(`select[data-step="${i + 1}"]`);
      following?.focus();
    }
  };

  const save = async (goNext: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api.post<TestDetail>(`/testcases/tests/${detail.id}/results`, {
        ...(status ? { status } : {}),
        ...(comment.trim() ? { comment: comment.trim() } : {}),
        ...(version.trim() ? { version: version.trim() } : {}),
        ...(elapsed.trim() ? { elapsed: elapsed.trim() } : {}),
        ...(defects.trim() ? { defects: splitKeys(defects) } : {}),
        ...(assignChanged ? { assigned_to: assign } : {}),
        ...(steps.length && stepsTouched ? { step_results: steps.map((_, i) => ({ status: stepStatus[i] || "untested", actual: actual[i].trim() || null })) } : {}),
      });
      setStatus(null);
      setComment("");
      setElapsed("");
      setDefects("");
      setStepStatus(steps.map(() => ""));
      setActual(steps.map(() => ""));
      onSaved(goNext);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {steps.length > 0 && (
        <div className="tr-block">
          <h4>Các bước</h4>
          <div className="table-wrap">
            <table className="tc-table tr-steps">
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Bước</th>
                  <th>Kết quả mong đợi</th>
                  {!readOnly && <th>Kết quả thực tế</th>}
                </tr>
              </thead>
              <tbody>
                {steps.map((s, i) => (
                  <tr key={i}>
                    <td className="num">{i + 1}</td>
                    <td>
                      {s.shared_title && <div className="small muted">Bước dùng chung: {s.shared_title}</div>}
                      {s.shared != null && s.content == null ? <span className="muted">Bước dùng chung SS{s.shared}</span> : <div className="tr-pre">{s.content}</div>}
                    </td>
                    <td>
                      <div className="tr-pre">{s.expected || <span className="muted">—</span>}</div>
                    </td>
                    {!readOnly && (
                      <td className="tr-step-rec">
                        <select data-step={i} aria-label={`Trạng thái bước ${i + 1}`} className={stepStatus[i] ? `tr-sel st-${stepStatus[i]}` : undefined} value={stepStatus[i]} onChange={(e) => setStep(i, e.target.value as TestStatus | "")}>
                          <option value="">—</option>
                          {RESULT_STATUSES.map((st) => (
                            <option key={st} value={st}>
                              {TEST_STATUS_LABEL[st]}
                            </option>
                          ))}
                        </select>
                        <textarea
                          rows={1}
                          aria-label={`Kết quả thực tế bước ${i + 1}`}
                          placeholder="Thực tế…"
                          value={actual[i]}
                          onChange={(e) => setActual(actual.map((x, j) => (j === i ? e.target.value : x)))}
                        />
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {!readOnly && (
        <section
          className="tr-record"
          aria-label="Ghi kết quả"
          onKeyDown={(e) => {
            // Ctrl/⌘ + Enter: save and go on to the next test.
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && ready && !busy) {
              e.preventDefault();
              void save(hasNext);
            }
          }}
        >
          <h4>Ghi kết quả</h4>
          {error && (
            <Alert tone="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
          <StatusButtons value={status} onChange={(s) => setStatus(status === s ? null : s)} disabled={busy} />
          <label className="adm-field">
            <span className="small muted">Bình luận</span>
            <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Mô tả lỗi, bước tái hiện, ghi chú…" />
          </label>
          <div className="tr-form-row four">
            <label className="adm-field">
              <span className="small muted">Phiên bản / build</span>
              <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="1.4.2" />
            </label>
            <label className="adm-field">
              <span className="small muted">Thời gian chạy</span>
              <input value={elapsed} onChange={(e) => setElapsed(e.target.value)} placeholder="1m 30s" />
            </label>
            <label className="adm-field">
              <span className="small muted">Bug (mã Jira)</span>
              <input value={defects} onChange={(e) => setDefects(e.target.value)} placeholder="ABC-123" />
            </label>
            <label className="adm-field">
              <span className="small muted">Giao cho</span>
              <PersonSelect value={assign} onChange={setAssign} people={people} />
            </label>
          </div>
          <div className="adm-form-foot">
            <span className="small muted tr-save-hint">{hasNext ? "Lưu xong tự mở test kế tiếp · Ctrl+Enter" : "Test cuối của danh sách · Ctrl+Enter"}</span>
            {hasNext && (
              <button type="button" className="ghost" onClick={() => void save(false)} disabled={busy || !ready}>
                Chỉ lưu
              </button>
            )}
            {/* Saving moves on to the next test (user 2026-11-12): no closing and reopening. */}
            <button type="button" className="primary" onClick={() => void save(hasNext)} disabled={busy || !ready}>
              {busy ? "Đang lưu…" : hasNext ? "Lưu & sang test sau" : "Lưu kết quả"}
            </button>
          </div>
        </section>
      )}
    </>
  );
}

function History({ detail }: { detail: TestDetail }) {
  const results = [...detail.results].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  return (
    <div className="tr-block">
      <h4>Lịch sử kết quả ({results.length})</h4>
      {!results.length ? (
        <p className="small muted">Chưa có kết quả nào.</p>
      ) : (
        <ol className="tr-history">
          {results.map((x) => (
            <li key={x.id}>
              <span className={`tr-dot st-${x.status ?? "untested"}`} aria-hidden="true" />
              <div>
                <div className="small">
                  {x.status ? <StatusPill status={x.status} /> : <span className="pill">Cập nhật</span>} {x.source === "ci" && <CiBadge />}{" "}
                  <strong>{x.created_by?.name ?? "—"}</strong>{" "}
                  <span className="muted">{dateTimeMin(x.created_at)}</span>
                  {x.version && <span className="muted"> · bản {x.version}</span>}
                  {(x.elapsed || x.elapsed_s) && <span className="muted"> · {x.elapsed ?? durationText(x.elapsed_s)}</span>}
                  {x.assigned_to && <span className="muted"> · giao {x.assigned_to.name}</span>}
                </div>
                {x.comment && <div className="tr-pre small">{x.comment}</div>}
                <CiResult result={x} />
                {x.defects.length > 0 && <div className="small">Bug: {x.defects.join(", ")}</div>}
                {x.step_results && x.step_results.some((s) => s.status !== "untested" || s.actual) && (
                  <ul className="tr-step-hist small">
                    {x.step_results.map((s, i) =>
                      s.status !== "untested" || s.actual ? (
                        <li key={i}>
                          Bước {i + 1}: <StatusPill status={s.status} /> {s.actual}
                        </li>
                      ) : null,
                    )}
                  </ul>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
