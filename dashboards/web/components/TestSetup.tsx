"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { ConfirmModal } from "@/components/TestRunParts";
import { Alert, Card, EmptyState, Loading } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import { errorText, type ConfigGroup, type SharedStep } from "@/lib/testruns";

/* Thiết lập (docs/specs/30 §13.4–§13.5, API §14.2): shared steps and configurations of a dự án. */

type StepDraft = { content: string; expected: string };

function SharedStepModal({ projectId, item, onClose, onSaved }: { projectId: number; item: SharedStep | null; onClose: () => void; onSaved: () => void }) {
  const [title, setTitle] = useState(item?.title ?? "");
  const [steps, setSteps] = useState<StepDraft[]>(() =>
    item?.steps.length ? item.steps.map((s) => ({ content: s.content, expected: s.expected ?? "" })) : [{ content: "", expected: "" }],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clean = steps.filter((s) => s.content.trim()).map((s) => ({ content: s.content.trim(), expected: s.expected.trim() || null }));
  const setStep = (i: number, p: Partial<StepDraft>) => setSteps(steps.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= steps.length) return;
    const next = [...steps];
    [next[i], next[j]] = [next[j], next[i]];
    setSteps(next);
  };
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (item) await api.patch(`/testcases/shared-steps/${item.id}`, { title: title.trim(), steps: clean });
      else await api.post("/testcases/shared-steps", { business_project_id: projectId, title: title.trim(), steps: clean });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={item ? `Sửa ${item.key}` : "Tạo bước dùng chung"}
      onClose={onClose}
      busy={busy}
      width={760}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !title.trim() || !clean.length}>
            {item ? "Lưu" : "Tạo"}
          </button>
        </>
      }
    >
      <div className="adm-form">
        {error && <Alert tone="error">{error}</Alert>}
        {item && item.used_by > 0 && <Alert tone="info">Đang dùng trong {item.used_by} testcase: sửa ở đây thì mọi testcase đó đổi theo.</Alert>}
        <label className="adm-field">
          <span className="small muted">Tên</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Đăng nhập bằng tài khoản quản trị" />
        </label>
        <div className="table-wrap">
          <table className="tc-table tr-step-editor">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Bước</th>
                <th>Kết quả mong đợi</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {steps.map((s, i) => (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  <td>
                    <textarea rows={2} aria-label={`Bước ${i + 1}`} value={s.content} onChange={(e) => setStep(i, { content: e.target.value })} />
                  </td>
                  <td>
                    <textarea rows={2} aria-label={`Kết quả mong đợi bước ${i + 1}`} value={s.expected} onChange={(e) => setStep(i, { expected: e.target.value })} />
                  </td>
                  <td className="tr-step-tools">
                    <IconButton icon="chevronUp" label={`Đưa bước ${i + 1} lên`} onClick={() => move(i, -1)} disabled={i === 0} />
                    <IconButton icon="chevronDown" label={`Đưa bước ${i + 1} xuống`} onClick={() => move(i, 1)} disabled={i === steps.length - 1} />
                    <IconButton icon="remove" label={`Xoá bước ${i + 1}`} onClick={() => setSteps(steps.filter((_, j) => j !== i))} disabled={steps.length === 1} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <button type="button" className="sm" onClick={() => setSteps([...steps, { content: "", expected: "" }])}>
            <Icon name="plus" size={14} /> Thêm bước
          </button>
        </div>
      </div>
    </Modal>
  );
}

function SharedSteps({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const list = useApi<SharedStep[]>(`/testcases/shared-steps?business_project_id=${projectId}`);
  const [editing, setEditing] = useState<SharedStep | "new" | null>(null);
  const [removing, setRemoving] = useState<SharedStep | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/testcases/shared-steps/${removing.id}`);
      setRemoving(null);
      void list.mutate();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      title="Bước dùng chung"
      id="tr-shared-steps"
      subtitle="Chuỗi bước dùng lại trong nhiều testcase dạng bước; sửa một chỗ, mọi testcase đổi theo"
      actions={
        canEdit ? (
          <button type="button" className="sm" onClick={() => setEditing("new")}>
            <Icon name="plus" size={14} /> Tạo bước dùng chung
          </button>
        ) : undefined
      }
    >
      {!list.data ? (
        <Loading error={list.error} rows={3} />
      ) : !list.data.length ? (
        <EmptyState icon="copy" title="Chưa có bước dùng chung nào">
          Ví dụ “Đăng nhập bằng tài khoản quản trị”: tạo ở đây rồi chèn vào bảng bước của testcase.
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="tc-table">
            <thead>
              <tr>
                <th>Mã</th>
                <th>Tên</th>
                <th className="num">Số bước</th>
                <th className="num">Testcase dùng</th>
                <th>Cập nhật</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {list.data.map((s) => (
                <tr key={s.id}>
                  <td className="num tc-code-cell">{s.key}</td>
                  <td>
                    {s.can_edit ? (
                      <button type="button" className="link tc-row-title" onClick={() => setEditing(s)}>
                        {s.title}
                      </button>
                    ) : (
                      s.title
                    )}
                    {s.steps[0] && <div className="small muted tr-clip">1. {s.steps[0].content}</div>}
                  </td>
                  <td className="num">{s.steps.length}</td>
                  <td className="num">{s.used_by}</td>
                  <td className="small tc-nowrap">{dateTimeMin(s.updated_at)}</td>
                  <td>
                    {s.can_edit && (
                      <div className="tr-row-tools">
                        <IconButton icon="edit" label={`Sửa ${s.key}`} onClick={() => setEditing(s)} />
                        <IconButton icon="remove" label={`Xoá ${s.key}`} onClick={() => setRemoving(s)} />
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <SharedStepModal
          projectId={projectId}
          item={editing === "new" ? null : editing}
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
          confirmLabel="Xoá"
          danger
          busy={busy}
          error={error}
          onClose={() => {
            setRemoving(null);
            setError(null);
          }}
          onConfirm={() => void remove()}
        >
          {removing.used_by > 0
            ? `“${removing.title}” đang dùng trong ${removing.used_by} testcase: bỏ nó khỏi các testcase đó trước khi xoá.`
            : `Xoá “${removing.title}”. Không thể hoàn tác.`}
        </ConfirmModal>
      )}
    </Card>
  );
}

/** A name shown as text, or an input while being renamed (Enter saves, Escape cancels). */
function InlineName({ value, editing, onSave, onCancel, label }: { value: string; editing: boolean; onSave: (v: string) => void; onCancel: () => void; label: string }) {
  const [text, setText] = useState(value);
  if (!editing) return <span>{value}</span>;
  return (
    <input
      className="tr-inline-input"
      aria-label={label}
      autoFocus
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && text.trim()) onSave(text.trim());
        if (e.key === "Escape") {
          e.stopPropagation();
          onCancel();
        }
      }}
      onBlur={() => (text.trim() && text.trim() !== value ? onSave(text.trim()) : onCancel())}
    />
  );
}

function AddInline({ placeholder, label, onAdd, disabled }: { placeholder: string; label: string; onAdd: (name: string) => Promise<boolean>; disabled?: boolean }) {
  const [text, setText] = useState("");
  const submit = async () => {
    if (!text.trim()) return;
    if (await onAdd(text.trim())) setText("");
  };
  return (
    <form
      className="tr-add-inline"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <input aria-label={label} placeholder={placeholder} value={text} onChange={(e) => setText(e.target.value)} disabled={disabled} />
      <button type="submit" className="sm" disabled={disabled || !text.trim()}>
        <Icon name="plus" size={14} /> Thêm
      </button>
    </form>
  );
}

function Configurations({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const list = useApi<ConfigGroup[]>(`/testcases/configs?business_project_id=${projectId}`);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [removing, setRemoving] = useState<{ kind: "group" | "config"; id: number; name: string; count?: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await list.mutate();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Cấu hình" id="tr-configs" subtitle="Nhóm cấu hình (Trình duyệt, Nền tảng…); test plan tạo một run cho mỗi tổ hợp đã chọn">
      {error && !removing && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!list.data ? (
        <Loading error={list.error} rows={3} />
      ) : (
        <>
          {!list.data.length && (
            <EmptyState icon="settings" title="Chưa có nhóm cấu hình nào">
              {canEdit ? "Thêm nhóm như “Trình duyệt” rồi các cấu hình Chrome, Safari…" : undefined}
            </EmptyState>
          )}
          <ul className="tr-cfg-groups">
            {list.data.map((g) => (
              <li key={g.id} className="tr-cfg-group">
                <div className="tr-cfg-head">
                  <strong>
                    <InlineName
                      value={g.name}
                      label={`Tên nhóm ${g.name}`}
                      editing={renaming === `g${g.id}`}
                      onCancel={() => setRenaming(null)}
                      onSave={(name) => void act(() => api.patch(`/testcases/config-groups/${g.id}`, { name })).then(() => setRenaming(null))}
                    />
                  </strong>
                  <span className="small muted">{g.configs.length} cấu hình</span>
                  <span className="spacer" />
                  {canEdit && (
                    <>
                      <IconButton icon="edit" label={`Đổi tên nhóm ${g.name}`} onClick={() => setRenaming(`g${g.id}`)} />
                      <IconButton icon="remove" label={`Xoá nhóm ${g.name}`} onClick={() => setRemoving({ kind: "group", id: g.id, name: g.name, count: g.configs.length })} />
                    </>
                  )}
                </div>
                <ul className="tr-cfg-items">
                  {g.configs.map((c) => (
                    <li key={c.id} className="tr-cfg-item">
                      <InlineName
                        value={c.name}
                        label={`Tên cấu hình ${c.name}`}
                        editing={renaming === `c${c.id}`}
                        onCancel={() => setRenaming(null)}
                        onSave={(name) => void act(() => api.patch(`/testcases/configs/${c.id}`, { name })).then(() => setRenaming(null))}
                      />
                      {canEdit && renaming !== `c${c.id}` && (
                        <>
                          <IconButton icon="edit" size={13} label={`Đổi tên ${c.name}`} onClick={() => setRenaming(`c${c.id}`)} />
                          <IconButton icon="close" size={13} label={`Xoá ${c.name}`} onClick={() => setRemoving({ kind: "config", id: c.id, name: c.name })} />
                        </>
                      )}
                    </li>
                  ))}
                  {!g.configs.length && <li className="small muted">Chưa có cấu hình</li>}
                </ul>
                {canEdit && (
                  <AddInline
                    label={`Cấu hình mới trong ${g.name}`}
                    placeholder="Cấu hình mới, ví dụ Chrome"
                    disabled={busy}
                    onAdd={(name) => act(() => api.post("/testcases/configs", { group_id: g.id, name }))}
                  />
                )}
              </li>
            ))}
          </ul>
          {canEdit && (
            <div className="tr-cfg-new">
              <AddInline
                label="Nhóm cấu hình mới"
                placeholder="Nhóm mới, ví dụ Trình duyệt"
                disabled={busy}
                onAdd={(name) => act(() => api.post("/testcases/config-groups", { business_project_id: projectId, name }))}
              />
            </div>
          )}
        </>
      )}
      {removing && (
        <ConfirmModal
          title={`Xoá ${removing.kind === "group" ? "nhóm" : "cấu hình"} “${removing.name}”?`}
          confirmLabel="Xoá"
          danger
          busy={busy}
          error={error}
          onClose={() => {
            setRemoving(null);
            setError(null);
          }}
          onConfirm={() =>
            void act(() => api.delete(removing.kind === "group" ? `/testcases/config-groups/${removing.id}` : `/testcases/configs/${removing.id}`)).then(
              (ok) => ok && setRemoving(null),
            )
          }
        >
          {removing.kind === "group"
            ? `Xoá nhóm cùng ${removing.count ?? 0} cấu hình bên trong. Run đã tạo giữ tên cấu hình cũ.`
            : "Run đã tạo giữ tên cấu hình cũ."}
        </ConfirmModal>
      )}
    </Card>
  );
}

export function SetupTab({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  return (
    <div className="tr-tab">
      <SharedSteps projectId={projectId} canEdit={canEdit} />
      <Configurations projectId={projectId} canEdit={canEdit} />
    </div>
  );
}
