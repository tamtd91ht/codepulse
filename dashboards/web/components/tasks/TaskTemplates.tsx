"use client";

import Link from "next/link";
import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Loading, Message } from "@/components/ui";
import { api, can, useApi } from "@/lib/api";
import type { Me, TaskSpace, TaskSpaceMembers, TaskSpaceTemplate, TaskTemplateApplied } from "@/lib/types";

import { TYPE_LABEL, errorText, refreshTasks } from "./taskParts";

/* Project templates and board modes (docs/specs/31 §19.6, §20.4). The built-in templates (Scrum,
 * Kanban, QA, Mobile) live in app/tasks/templates.py and come with the saved ones from
 * GET /tasks/templates; a manager applies one when turning Task on, or later from Cài đặt. */

export type BoardMode = "scrum" | "kanban";
/** Built-in templates PUT /tasks/spaces accepts directly; the others go through apply-template. */
export const DIRECT_TEMPLATES = ["scrum", "kanban", "qa"];

export const BOARD_MODE_TEXT: Record<BoardMode, { label: string; hint: string }> = {
  scrum: { label: "Scrum", hint: "Board là sprint đang chạy; có tab Backlog để lập sprint." },
  kanban: { label: "Kanban", hint: "Board hiện mọi task chưa xong và task xong 14 ngày qua; ẩn tab Backlog." },
};

export function useTaskTemplates() {
  return useApi<TaskSpaceTemplate[]>("/tasks/templates");
}

/** The workflow of a template as a row of status chips. */
export function StatusFlow({ statuses }: { statuses: TaskSpaceTemplate["statuses"] }) {
  return (
    <ol className="tt-flow" aria-label="Trạng thái">
      {statuses.map((s) => (
        <li key={s.name} className={`tt-chip tt-chip-${s.category}`}>
          {s.name}
        </li>
      ))}
    </ol>
  );
}

function TemplateCard({ t, selected, onPick, name }: { t: TaskSpaceTemplate; selected: boolean; onPick: () => void; name: string }) {
  const extras = [
    t.fields.length ? `Trường: ${t.fields.join(", ")}` : null,
    t.has_dod ? "Có DoD" : null,
    t.has_automation ? "Có quy tắc tự động" : null,
  ].filter(Boolean);
  return (
    <label className={`tt-card${selected ? " selected" : ""}`}>
      <input type="radio" name={name} value={t.key} checked={selected} onChange={onPick} className="tt-radio" />
      <span className="tt-card-name">
        {t.name}
        <span className="small muted"> · {BOARD_MODE_TEXT[t.board_mode]?.label ?? t.board_mode}</span>
      </span>
      {t.description && <span className="small muted">{t.description}</span>}
      <StatusFlow statuses={t.statuses} />
      <span className="small muted">Loại: {t.types.map((x) => TYPE_LABEL[x] ?? x).join(", ")}</span>
      {extras.length > 0 && <span className="small muted">{extras.join(" · ")}</span>}
    </label>
  );
}

/** Template cards as a radio group (arrow keys move between them, as native radios do): built-in
 * ones first, then the ones saved from a dự án. */
export function TemplatePicker({
  templates,
  value,
  onChange,
  onDelete,
}: {
  templates: TaskSpaceTemplate[];
  value: string;
  onChange: (key: string) => void;
  onDelete?: (t: TaskSpaceTemplate) => void;
}) {
  const builtin = templates.filter((t) => t.builtin);
  const saved = templates.filter((t) => !t.builtin);
  return (
    <fieldset className="rq-field tt-picker">
      <legend className="rq-label">Chọn mẫu dự án</legend>
      <div className="tt-cards">
        {builtin.map((t) => (
          <TemplateCard key={t.key} t={t} name="tt-template" selected={value === t.key} onPick={() => onChange(t.key)} />
        ))}
      </div>
      <h3 className="tp-group">Mẫu đã lưu</h3>
      {saved.length ? (
        <div className="tt-cards">
          {saved.map((t) => (
            <div key={t.key} className="tp-saved">
              <TemplateCard t={t} name="tt-template" selected={value === t.key} onPick={() => onChange(t.key)} />
              {onDelete && t.can_delete && (
                <IconButton icon="remove" label={`Xoá mẫu ${t.name}`} tone="danger" className="tp-delete" onClick={() => onDelete(t)} />
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="small faint">Chưa có mẫu nào được lưu. Vào Cài đặt của một dự án, chọn “Lưu cấu hình thành mẫu”.</p>
      )}
    </fieldset>
  );
}

/** Steps of the enable wizard; the current one is marked for screen readers. */
/**
 * A one-line horizontal stepper (§22.7): ① Mã → ② Mẫu → ③ Thành viên → ④ Xác nhận → ⑤ Jira.
 * The number lives only in the badge (the list's own numbering is off, so it never shows twice);
 * a finished step shows a tick, and screen readers hear "Bước n/N".
 */
export function WizardSteps({ step, labels }: { step: number; labels: string[] }) {
  return (
    <ol className="tt-steps st-stepper" aria-label="Các bước">
      {labels.map((label, i) => (
        <li key={label} className={i === step ? "current" : i < step ? "done" : undefined} aria-current={i === step ? "step" : undefined}>
          {i > 0 && (
            <span className="st-step-sep" aria-hidden="true">
              →
            </span>
          )}
          <span className="tt-step-no" aria-hidden="true">
            {i < step ? <Icon name="check" size={12} /> : i + 1}
          </span>
          <span className="visually-hidden">
            Bước {i + 1}/{labels.length}
            {i < step ? ", đã xong" : ""}:{" "}
          </span>
          <span className="st-step-label">{label}</span>
        </li>
      ))}
    </ol>
  );
}

/** Scrum or Kanban, editable later in Cài đặt. */
export function BoardModeField({ value, onChange }: { value: BoardMode; onChange: (mode: BoardMode) => void }) {
  return (
    <fieldset className="rq-field tt-mode">
      <legend className="rq-label">Chế độ bảng</legend>
      {(Object.keys(BOARD_MODE_TEXT) as BoardMode[]).map((mode) => (
        <label key={mode} className="inline-check tt-mode-option">
          <input type="radio" name="tt-board-mode" value={mode} checked={value === mode} onChange={() => onChange(mode)} />
          <span>
            <strong>{BOARD_MODE_TEXT[mode].label}</strong>
            <span className="small muted"> · {BOARD_MODE_TEXT[mode].hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

/** "Áp mẫu" in Cài đặt: everything while the dự án has no task, the safe parts once it has. */
export function ApplyTemplateModal({
  space,
  onClose,
  onApplied,
}: {
  space: TaskSpace & { template_key?: string | null };
  onClose: () => void;
  /** Called instead of onClose when the dialog closes after a template was applied. */
  onApplied?: () => void;
}) {
  const templates = useTaskTemplates();
  const [key, setKey] = useState(space.template_key ?? "scrum");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TaskTemplateApplied | null>(null);
  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<TaskTemplateApplied>(`/tasks/spaces/${space.business_project_id}/apply-template`, { key });
      setResult(out);
      await refreshTasks();
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }
  async function remove(t: TaskSpaceTemplate) {
    if (t.id == null || !window.confirm(`Xoá mẫu “${t.name}”? Dự án đã áp mẫu này không bị ảnh hưởng.`)) return;
    setError(null);
    try {
      await api.delete(`/tasks/templates/${t.id}`);
      if (key === t.key) setKey("scrum");
      await templates.mutate();
    } catch (e) {
      setError(errorText(e));
    }
  }
  const close = () => (result && onApplied ? onApplied() : onClose());
  return (
    <Modal
      title={`Áp mẫu cho ${space.name}`}
      onClose={close}
      busy={busy}
      width={820}
      footer={
        result ? (
          <button type="button" className="primary" onClick={close}>
            Xong
          </button>
        ) : (
          <>
            <button type="button" className="ghost" disabled={busy} onClick={onClose}>
              Huỷ
            </button>
            <button type="button" className="primary" disabled={busy || !templates.data} onClick={() => void apply()}>
              {busy ? "Đang áp…" : "Áp mẫu"}
            </button>
          </>
        )
      }
    >
      {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
      {result ? (
        <div className="tp-result" role="status">
          <p className="small">
            {result.mode === "full" ? "Dự án chưa có task: đã áp toàn bộ mẫu." : "Dự án đã có task: chỉ áp những phần không làm mất dữ liệu."}
          </p>
          {result.applied.length > 0 && (
            <>
              <h3 className="tp-group">Đã áp</h3>
              <ul className="tp-list">
                {result.applied.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </>
          )}
          {result.skipped.length > 0 && (
            <>
              <h3 className="tp-group">Bỏ qua</h3>
              <ul className="tp-list tp-skipped">
                {result.skipped.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : (
        <>
          <p className="small muted tp-note">
            Dự án chưa có task: thay toàn bộ trạng thái, ưu tiên, luật, trường. Đã có task: giữ nguyên trạng thái và luồng chuyển; chỉ thêm ưu tiên, loại task, trường còn thiếu và áp luật git, DoD,
            quy tắc tự động, chế độ bảng.
          </p>
          {!templates.data ? (
            <Loading error={templates.error} rows={3} />
          ) : (
            <TemplatePicker templates={templates.data} value={key} onChange={setKey} onDelete={(t) => void remove(t)} />
          )}
        </>
      )}
    </Modal>
  );
}

/** "Lưu cấu hình thành mẫu": the dự án's current setup becomes a template for other dự án. */
export function SaveTemplateModal({ space, onClose }: { space: TaskSpace; onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<TaskSpaceTemplate | null>(null);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<TaskSpaceTemplate>(`/tasks/spaces/${space.business_project_id}/save-template`, {
        name: name.trim(),
        description: description.trim() || null,
      });
      setSaved(out);
      await refreshTasks();
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }
  return (
    <Modal
      title="Lưu cấu hình thành mẫu"
      onClose={onClose}
      busy={busy}
      footer={
        saved ? (
          <button type="button" className="primary" onClick={onClose}>
            Xong
          </button>
        ) : (
          <>
            <button type="button" className="ghost" disabled={busy} onClick={onClose}>
              Huỷ
            </button>
            <button type="submit" form="tp-save-form" className="primary" disabled={busy || !name.trim()}>
              {busy ? "Đang lưu…" : "Lưu mẫu"}
            </button>
          </>
        )
      }
    >
      {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
      {saved ? (
        <p className="small" role="status">
          Đã lưu mẫu <strong>{saved.name}</strong>. Mẫu hiện khi bật Task cho dự án khác và trong “Áp mẫu”.
        </p>
      ) : (
        <form
          id="tp-save-form"
          className="adm-form tk-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) void save();
          }}
        >
          <p className="small muted tp-note">
            Lưu loại task, trạng thái (WIP, cảnh báo tuổi, luồng chuyển), ưu tiên, luật git, DoD, quy tắc tự động, trường tuỳ chỉnh và chế độ bảng của {space.name}. Không lưu task, sprint hay người.
          </p>
          <label className="rq-field">
            <span className="rq-label">Tên mẫu</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder={`Cấu hình ${space.key ?? space.name}`} autoFocus />
          </label>
          <label className="rq-field">
            <span className="rq-label">Mô tả (tuỳ chọn)</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} rows={3} />
          </label>
        </form>
      )}
    </Modal>
  );
}

type Grant = { user_id: number; role: "view" | "manage"; admin_role: "lead" | "manager" | null };

/** Step 3 of the enable wizard: who already has access to the dự án, and who committed to its
 * repos in the last 90 days without access. A Manager dự án (or admin) grants them "view" through
 * the dự án's grant list (PUT /business-projects/{id}/grants, docs/specs/21 §10). */
export function MembersStep({ bpId }: { bpId: number }) {
  const got = useApi<TaskSpaceMembers>(`/tasks/spaces/${bpId}/members`);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function grantView(ids: number[]) {
    if (!ids.length) return;
    setBusy(true);
    setMessage(null);
    try {
      const current = await api.get<Grant[]>(`/business-projects/${bpId}/grants`);
      const have = new Set(current.map((g) => g.user_id));
      const grants = [
        ...current.map((g) => ({ user_id: g.user_id, role: g.role, admin_role: g.admin_role })),
        ...ids.filter((id) => !have.has(id)).map((id) => ({ user_id: id, role: "view" as const, admin_role: null })),
      ];
      await api.put(`/business-projects/${bpId}/grants`, { grants });
      await got.mutate();
      setMessage(`Đã cấp quyền xem cho ${ids.length} người.`);
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }
  if (!got.data)
    return (
      <div className="tp-members">
        <Loading error={got.error} rows={3} />
        {got.error && (
          <button type="button" className="ghost sm" onClick={() => void got.mutate()}>
            Thử lại
          </button>
        )}
      </div>
    );
  const { members, suggestions, can_grant } = got.data;
  return (
    <div className="tp-members">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <p className="small muted tp-note">Người có quyền vào dự án thấy Task nếu bộ quyền của họ có “Xem task”. Có thể bỏ qua bước này và cấp quyền sau ở Quản trị dự án.</p>
      <h3 className="tp-group">Đang có quyền ({members.length})</h3>
      {members.length ? (
        <ul className="tp-people">
          {members.map((m) => (
            <li key={m.user_id}>
              <span className="tp-person">{m.full_name ?? m.email ?? `#${m.user_id}`}</span>
              <span className="small muted">{m.admin_role === "manager" ? "Manager dự án" : m.admin_role === "lead" ? "Lead dự án" : m.role === "manage" ? "Quản lý" : "Xem"}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small faint">Chưa ai được cấp quyền vào dự án.</p>
      )}
      <div className="tp-group-head">
        <h3 className="tp-group">Gợi ý từ GitLab ({suggestions.length})</h3>
        {can_grant && suggestions.length > 1 && (
          <button type="button" className="ghost sm" disabled={busy} onClick={() => void grantView(suggestions.map((s) => s.user_id))}>
            Cấp quyền xem cho tất cả
          </button>
        )}
      </div>
      {suggestions.length ? (
        <ul className="tp-people">
          {suggestions.map((s) => (
            <li key={s.user_id}>
              <span className="tp-person">{s.full_name ?? s.email ?? `#${s.user_id}`}</span>
              <span className="small muted">{s.commits} commit trong 90 ngày</span>
              {can_grant && (
                <button type="button" className="ghost sm" disabled={busy} onClick={() => void grantView([s.user_id])}>
                  Cấp quyền xem
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="small faint">Không có ai commit vào repo của dự án trong 90 ngày qua mà chưa có quyền.</p>
      )}
      {!can_grant && suggestions.length > 0 && <p className="small muted">Chỉ Manager dự án hoặc admin cấp được quyền; nhờ họ thêm những người này.</p>}
    </div>
  );
}

/** Link to Tình trạng bật Task for admins and those who manage Task. */
export function AdoptionLink({ me }: { me: Me | undefined }) {
  if (!can(me, "tasks.manage") && !can(me, "admin.projects")) return null;
  return (
    <Link href="/tasks/adoption" className="button">
      <Icon name="pulse" size={16} /> Tình trạng bật Task
    </Link>
  );
}
