"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import { ConfirmModal, RunRow, StatusBar } from "@/components/TestRunParts";
import { Alert, EmptyState, Loading, Segmented } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import { dayText, errorText, today, type Milestone, type RunOut } from "@/lib/testruns";

/* Milestone (docs/specs/30 §13.5, API §14.2): a release or sprint, its runs and their summed progress. */

function MilestoneModal({ projectId, milestone, onClose, onSaved }: { projectId: number; milestone: Milestone | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(milestone?.name ?? "");
  const [description, setDescription] = useState(milestone?.description ?? "");
  const [start, setStart] = useState(milestone?.start_on ?? "");
  const [due, setDue] = useState(milestone?.due_on ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const badRange = !!start && !!due && due < start;
  const submit = async () => {
    setBusy(true);
    setError(null);
    const body = { name: name.trim(), description: description.trim() || null, start_on: start || null, due_on: due || null };
    try {
      if (milestone) await api.patch(`/testcases/milestones/${milestone.id}`, body);
      else await api.post("/testcases/milestones", { business_project_id: projectId, ...body });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={milestone ? `Sửa ${milestone.key}` : "Tạo milestone"}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !name.trim() || badRange}>
            {milestone ? "Lưu" : "Tạo milestone"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        <label className="adm-field">
          <span className="small muted">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Release 2.3 / Sprint 14" />
        </label>
        <div className="tr-form-row">
          <label className="adm-field">
            <span className="small muted">Bắt đầu</span>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="adm-field">
            <span className="small muted">Hạn</span>
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-invalid={badRange} />
          </label>
        </div>
        {badRange && <p className="small bad">Hạn phải sau ngày bắt đầu.</p>}
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>
    </Modal>
  );
}

function MilestoneRuns({ projectId, milestoneId, onOpenRun }: { projectId: number; milestoneId: number; onOpenRun: (id: number) => void }) {
  const runs = useApi<RunOut[]>(`/testcases/runs?business_project_id=${projectId}&state=all&milestone_id=${milestoneId}`);
  if (!runs.data) return <Loading error={runs.error} rows={2} />;
  if (!runs.data.length) return <p className="small muted">Chưa có run nào gắn milestone này.</p>;
  return (
    <ul className="tr-list">
      {runs.data.map((r) => (
        <RunRow key={r.id} run={r} onOpen={onOpenRun} />
      ))}
    </ul>
  );
}

export function MilestonesTab({ projectId, canEdit, onOpenRun }: { projectId: number; canEdit: boolean; onOpenRun: (id: number) => void }) {
  const list = useApi<Milestone[]>(`/testcases/milestones?business_project_id=${projectId}`);
  const [state, setState] = useState<"open" | "done">("open");
  const [editing, setEditing] = useState<Milestone | "new" | null>(null);
  const [removing, setRemoving] = useState<Milestone | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = today();
  const shown = (list.data ?? []).filter((m) => (state === "open" ? !m.completed_at : !!m.completed_at));

  const act = async (fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      after?.();
      void list.mutate();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tr-tab">
      <div className="toolbar">
        <Segmented
          label="Trạng thái milestone"
          value={state}
          onChange={setState}
          options={[
            { value: "open", label: "Đang chạy" },
            { value: "done", label: "Đã xong" },
          ]}
        />
        <span className="spacer" />
        {canEdit && (
          <button type="button" className="sm primary" onClick={() => setEditing("new")}>
            <Icon name="plus" size={14} /> Tạo milestone
          </button>
        )}
      </div>
      {error && !removing && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!list.data ? (
        <Loading error={list.error} rows={4} />
      ) : !shown.length ? (
        <EmptyState icon="flag" title={state === "open" ? "Chưa có milestone nào đang chạy" : "Chưa có milestone nào đã xong"}>
          {state === "open" && canEdit ? "Milestone là một đợt release / sprint: gắn run, plan và testcase vào để theo dõi tiến độ chung." : undefined}
        </EmptyState>
      ) : (
        <ul className="tr-list tr-ms-list">
          {shown.map((m) => {
            const overdue = !m.completed_at && !!m.due_on && m.due_on < now;
            return (
              <li key={m.id} className={`card tr-ms${overdue ? " overdue" : ""}`}>
                <div className="tr-item">
                  <div className="tr-item-main">
                    <div className="tr-item-title">
                      <span className="tr-key">{m.key}</span>
                      <strong>{m.name}</strong>
                      {overdue && (
                        <span className="pill bad">
                          <Icon name="alert" size={12} /> Quá hạn
                        </span>
                      )}
                      {m.completed_at && <span className="pill good">Xong {dateTimeMin(m.completed_at)}</span>}
                    </div>
                    <div className="small muted tr-item-meta">
                      {(m.start_on || m.due_on) && (
                        <span>
                          <Icon name="calendar" size={12} /> {m.start_on ? dayText(m.start_on) : "…"} → {m.due_on ? <span className={overdue ? "bad" : undefined}>{dayText(m.due_on)}</span> : "…"}
                        </span>
                      )}
                      <button type="button" className="link small" aria-expanded={expanded === m.id} onClick={() => setExpanded(expanded === m.id ? null : m.id)}>
                        {m.runs} run
                      </button>
                    </div>
                    {m.description && <p className="small tr-desc">{m.description}</p>}
                  </div>
                  <StatusBar counts={m.counts} compact />
                  {m.can_edit && (
                    <div className="tr-row-tools">
                      <IconButton icon="edit" label={`Sửa ${m.key}`} onClick={() => setEditing(m)} />
                      <MoreMenu
                        disabled={busy}
                        items={[
                          m.completed_at
                            ? { icon: "history", label: "Mở lại", run: () => void act(() => api.patch(`/testcases/milestones/${m.id}`, { completed: false })) }
                            : { icon: "checkCircle", label: "Đánh dấu xong", run: () => void act(() => api.patch(`/testcases/milestones/${m.id}`, { completed: true })) },
                          { icon: "remove", label: "Xoá", danger: true, run: () => setRemoving(m) },
                        ]}
                      />
                    </div>
                  )}
                </div>
                {expanded === m.id && <MilestoneRuns projectId={projectId} milestoneId={m.id} onOpenRun={onOpenRun} />}
              </li>
            );
          })}
        </ul>
      )}
      {editing && (
        <MilestoneModal
          projectId={projectId}
          milestone={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void list.mutate();
          }}
        />
      )}
      {removing && (
        <ConfirmModal
          title={`Xoá ${removing.key}?`}
          confirmLabel="Xoá milestone"
          danger
          busy={busy}
          error={error}
          onClose={() => {
            setRemoving(null);
            setError(null);
          }}
          onConfirm={() => void act(() => api.delete(`/testcases/milestones/${removing.id}`), () => setRemoving(null))}
        >
          Milestone “{removing.name}” bị xoá; {removing.runs} run, các plan và testcase đang gắn nó chỉ mất liên kết, không bị xoá.
        </ConfirmModal>
      )}
    </div>
  );
}
