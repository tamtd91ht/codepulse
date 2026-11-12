"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import {
  ConfirmModal,
  MilestoneSelect,
  PersonSelect,
  RunRow,
  SelectionPicker,
  StatusBar,
  initialPick,
  selectionOf,
  useSetupLists,
  type Pick,
} from "@/components/TestRunParts";
import { Alert, EmptyState, Loading } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import { errorText, type ConfigGroup, type PlanDetail, type PlanEntry, type Person } from "@/lib/testruns";

/*
 * Test plan (docs/specs/30 §13.6, API §14.4): several runs at once, each entry = a choice of cases ×
 * every combination of the ticked configurations (one per group), progress summed over its runs.
 */

interface EntryDraft {
  uid: number;
  name: string;
  pick: Pick;
  configIds: number[];
  assignee: number | null;
}

let nextUid = 1;
const newEntry = (): EntryDraft => ({ uid: nextUid++, name: "", pick: initialPick(null), configIds: [], assignee: null });

/** Runs an entry makes: the product, over groups with something ticked, of the ticked count. */
function runsOf(entry: EntryDraft, groups: ConfigGroup[]): number {
  return groups.reduce((n, g) => {
    const k = g.configs.filter((c) => entry.configIds.includes(c.id)).length;
    return k ? n * k : n;
  }, 1);
}

function toEntries(drafts: EntryDraft[]): PlanEntry[] | null {
  const out: PlanEntry[] = [];
  for (const d of drafts) {
    const selection = selectionOf(d.pick, null);
    if (!selection) return null;
    out.push({
      ...(d.name.trim() ? { name: d.name.trim() } : {}),
      selection,
      ...(d.configIds.length ? { config_ids: d.configIds } : {}),
      ...(d.assignee ? { assigned_to: d.assignee } : {}),
    });
  }
  return out;
}

function EntriesEditor({
  projectId,
  entries,
  onChange,
  groups,
  people,
}: {
  projectId: number;
  entries: EntryDraft[];
  onChange: (e: EntryDraft[]) => void;
  groups: ConfigGroup[];
  people: Person[] | undefined;
}) {
  const set = (uid: number, p: Partial<EntryDraft>) => onChange(entries.map((e) => (e.uid === uid ? { ...e, ...p } : e)));
  const total = entries.reduce((n, e) => n + runsOf(e, groups), 0);
  return (
    <div className="tr-entries">
      {entries.map((e, i) => (
        <div key={e.uid} className="tr-entry">
          <div className="tr-entry-head">
            <strong className="small">Nhóm run {i + 1}</strong>
            <span className="small muted">sẽ tạo {runsOf(e, groups)} run</span>
            <span className="spacer" />
            {entries.length > 1 && <IconButton icon="remove" label={`Bỏ nhóm run ${i + 1}`} onClick={() => onChange(entries.filter((x) => x.uid !== e.uid))} />}
          </div>
          <div className="tr-form-row">
            <label className="adm-field">
              <span className="small muted">Tên run (tuỳ chọn)</span>
              <input value={e.name} onChange={(ev) => set(e.uid, { name: ev.target.value })} placeholder="Theo tên bộ / cấu hình" />
            </label>
            <label className="adm-field">
              <span className="small muted">Giao cho</span>
              <PersonSelect value={e.assignee} onChange={(v) => set(e.uid, { assignee: v })} people={people} />
            </label>
          </div>
          <SelectionPicker projectId={projectId} pick={e.pick} onChange={(p) => set(e.uid, { pick: p })} />
          {groups.length > 0 ? (
            <fieldset className="tr-pick">
              <legend className="small muted">Cấu hình (mỗi tổ hợp một run)</legend>
              {groups.map((g) => (
                <div key={g.id} className="tr-cfg-line">
                  <span className="small tr-cfg-name">{g.name}</span>
                  {g.configs.length ? (
                    g.configs.map((c) => (
                      <label key={c.id} className="inline-check">
                        <input
                          type="checkbox"
                          checked={e.configIds.includes(c.id)}
                          onChange={(ev) => set(e.uid, { configIds: ev.target.checked ? [...e.configIds, c.id] : e.configIds.filter((x) => x !== c.id) })}
                        />
                        {c.name}
                      </label>
                    ))
                  ) : (
                    <span className="small muted">Chưa có cấu hình</span>
                  )}
                </div>
              ))}
            </fieldset>
          ) : (
            <p className="small muted">Chưa có cấu hình nào (tab Thiết lập): mỗi nhóm tạo một run.</p>
          )}
        </div>
      ))}
      <div className="tr-entries-foot">
        <button type="button" className="sm" onClick={() => onChange([...entries, newEntry()])}>
          <Icon name="plus" size={14} /> Thêm nhóm run
        </button>
        <span className="small">
          Tổng cộng sẽ tạo <strong>{total}</strong> run
        </span>
      </div>
    </div>
  );
}

export function CreatePlanModal({ projectId, onClose, onCreated }: { projectId: number; onClose: () => void; onCreated: (planId: number) => void }) {
  const { milestones, people, configs } = useSetupLists(projectId);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [milestone, setMilestone] = useState<number | null>(null);
  const [entries, setEntries] = useState<EntryDraft[]>(() => [newEntry()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = toEntries(entries);

  const submit = async () => {
    if (!body || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const plan = await api.post<PlanDetail>("/testcases/plans", {
        business_project_id: projectId,
        name: name.trim(),
        description: description.trim() || undefined,
        milestone_id: milestone ?? undefined,
        entries: body,
      });
      onCreated(plan.id);
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Tạo test plan"
      onClose={onClose}
      busy={busy}
      width={720}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !body || !name.trim()}>
            {busy ? "Đang tạo…" : "Tạo test plan"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Tên plan</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Release 2.3" required />
          </label>
          <label className="adm-field">
            <span className="small muted">Milestone</span>
            <MilestoneSelect value={milestone} onChange={setMilestone} milestones={milestones.data} />
          </label>
        </div>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <EntriesEditor projectId={projectId} entries={entries} onChange={setEntries} groups={configs.data ?? []} people={people.data} />
      </div>
    </Modal>
  );
}

function AddEntriesModal({ plan, projectId, onClose, onSaved }: { plan: PlanDetail; projectId: number; onClose: () => void; onSaved: () => void }) {
  const { people, configs } = useSetupLists(projectId);
  const [entries, setEntries] = useState<EntryDraft[]>(() => [newEntry()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = toEntries(entries);
  const submit = async () => {
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/testcases/plans/${plan.id}`, { add_entries: body });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Thêm run vào ${plan.key}`}
      onClose={onClose}
      busy={busy}
      width={720}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !body}>
            Thêm run
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <EntriesEditor projectId={projectId} entries={entries} onChange={setEntries} groups={configs.data ?? []} people={people.data} />
      </div>
    </Modal>
  );
}

function EditPlanModal({ plan, projectId, onClose, onSaved }: { plan: PlanDetail; projectId: number; onClose: () => void; onSaved: () => void }) {
  const { milestones } = useSetupLists(projectId);
  const [name, setName] = useState(plan.name);
  const [description, setDescription] = useState(plan.description ?? "");
  const [milestone, setMilestone] = useState<number | null>(plan.milestone_id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/testcases/plans/${plan.id}`, { name: name.trim(), description: description.trim() || null, milestone_id: milestone });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Sửa ${plan.key}`}
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
          <span className="small muted">Tên plan</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="adm-field">
          <span className="small muted">Milestone</span>
          <MilestoneSelect value={milestone} onChange={setMilestone} milestones={milestones.data} />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>
    </Modal>
  );
}

export function PlanView({ planId, projectId, onBack, onOpenRun }: { planId: number; projectId: number; onBack: () => void; onOpenRun: (id: number) => void }) {
  const plan = useApi<PlanDetail>(`/testcases/plans/${planId}`);
  const [dialog, setDialog] = useState<"edit" | "add" | "close" | "delete" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = plan.data;
  if (plan.error?.status === 404)
    return (
      <EmptyState icon="alert" title="Không tìm thấy test plan">
        <button type="button" className="link" onClick={onBack}>
          Về danh sách
        </button>
      </EmptyState>
    );
  if (!p) return <Loading error={plan.error} rows={6} />;
  const completed = !!p.completed_at;
  const editable = p.can_edit && !completed;

  const act = async (fn: () => Promise<unknown>, after: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      after();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const done = () => {
    setDialog(null);
    void plan.mutate();
  };

  return (
    <div className="tr-tab">
      <div className="tr-view-head">
        <button type="button" className="ghost sm" onClick={onBack}>
          <Icon name="chevronLeft" size={14} /> Test run
        </button>
        <div className="tr-view-title">
          <span className="tr-key">{p.key}</span>
          <h2>{p.name}</h2>
          {completed && <span className="pill">Đã đóng {dateTimeMin(p.completed_at)}</span>}
        </div>
        <div className="tr-view-actions">
          {editable && (
            <>
              <IconButton icon="edit" label="Sửa plan" onClick={() => setDialog("edit")} />
              <button type="button" className="sm" onClick={() => setDialog("add")}>
                <Icon name="plus" size={14} /> Thêm run
              </button>
            </>
          )}
          <MoreMenu
            items={[
              ...(editable ? [{ icon: "checkCircle" as const, label: "Đóng plan", run: () => setDialog("close") }] : []),
              ...(p.can_edit ? [{ icon: "remove" as const, label: "Xoá plan", danger: true, run: () => setDialog("delete") }] : []),
            ]}
          />
        </div>
      </div>
      <div className="small muted tr-item-meta tr-view-meta">
        {p.milestone_name && <span>Milestone: {p.milestone_name}</span>}
        <span>{p.runs.length} run</span>
        <span>Tạo {dateTimeMin(p.created_at)}</span>
      </div>
      {p.description && <p className="small tr-desc">{p.description}</p>}
      {error && !dialog && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="card tr-summary">
        <StatusBar counts={p.counts} />
      </div>
      <div className="card">
        {p.runs.length ? (
          <ul className="tr-list">
            {p.runs.map((r) => (
              <RunRow key={r.id} run={r} onOpen={onOpenRun} />
            ))}
          </ul>
        ) : (
          <EmptyState icon="inbox" title="Plan chưa có run nào" />
        )}
      </div>
      {dialog === "edit" && <EditPlanModal plan={p} projectId={projectId} onClose={() => setDialog(null)} onSaved={done} />}
      {dialog === "add" && <AddEntriesModal plan={p} projectId={projectId} onClose={() => setDialog(null)} onSaved={done} />}
      {dialog === "close" && (
        <ConfirmModal
          title={`Đóng ${p.key}?`}
          confirmLabel="Đóng plan"
          busy={busy}
          error={error}
          onClose={() => setDialog(null)}
          onConfirm={() => void act(() => api.post(`/testcases/plans/${p.id}/close`), done)}
        >
          Đóng plan đóng luôn {p.runs.filter((r) => !r.completed_at).length} run đang mở của nó; nội dung testcase của các run được chốt lại.
        </ConfirmModal>
      )}
      {dialog === "delete" && (
        <ConfirmModal
          title={`Xoá ${p.key}?`}
          confirmLabel="Xoá plan"
          danger
          busy={busy}
          error={error}
          onClose={() => setDialog(null)}
          onConfirm={() =>
            void act(
              () => api.delete(`/testcases/plans/${p.id}`),
              () => {
                setDialog(null);
                onBack();
              },
            )
          }
        >
          Xoá plan “{p.name}” cùng {p.runs.length} run và mọi kết quả đã ghi. Không thể hoàn tác.
        </ConfirmModal>
      )}
    </div>
  );
}
