"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { IconButton } from "@/components/icons";
import { MultiSelect } from "@/components/MultiSelect";
import { Card, Loading, Message } from "@/components/ui";
import { api } from "@/lib/api";
import type { TaskCategory, TaskField, TaskFieldKind, TaskRules, TaskSpace, TaskType } from "@/lib/types";

import { CATEGORY_LABEL, TASK_TYPES, TYPE_LABEL, errorText, refreshTasks, sortedStatuses, useTaskFields } from "./taskParts";

/* Cài đặt (§13, tasks.manage): key, types, statuses and their transitions, git rules, custom fields. */

export const KEY_RE = /^[A-Z][A-Z0-9]{1,9}$/;

const DEFAULT_RULES: TaskRules = {
  start_on_commit: true,
  review_on_mr: true,
  done_on_merge: true,
  reopen_on_mr_close: true,
  block_done_on_critical: false,
  mr_comment: false,
  start: null,
  review: null,
  done: null,
};

const RULE_TEXT: { key: keyof TaskRules; label: string; hint: string }[] = [
  { key: "start_on_commit", label: "Bắt đầu khi có commit", hint: "Nhánh / commit đầu tiên có mã task → trạng thái “bắt đầu”" },
  { key: "review_on_mr", label: "Review khi mở MR", hint: "MR có mã task đang mở → trạng thái “review”" },
  { key: "done_on_merge", label: "Xong khi merge", hint: "MR fixes merge vào nhánh mặc định, hoặc commit Fixes: vào nhánh mặc định → “xong”" },
  { key: "reopen_on_mr_close", label: "Mở lại khi đóng MR", hint: "MR đóng không merge, task đang review → về “bắt đầu”" },
  { key: "block_done_on_critical", label: "Chặn Done khi còn critical", hint: "Còn finding critical mở trên code của task thì không cho vào Done (cả người và git)" },
  { key: "mr_comment", label: "Bình luận vào MR", hint: "Ghi link task vào MR khi MR được nối lần đầu (ghi lên GitLab)" },
];

/** First setup of a dự án's Task (shown to its managers while it is off). */
export function EnableSpace({ space, onDone }: { space: TaskSpace; onDone: () => void }) {
  const [key, setKey] = useState(space.key ?? "");
  const [types, setTypes] = useState<TaskType[]>(space.types?.length ? space.types : TASK_TYPES);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = KEY_RE.test(key);
  async function enable() {
    if (!valid || !types.length) return;
    setBusy(true);
    setError(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}`, { key, types });
      await refreshTasks();
      onDone();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Card title={`Bật Task cho ${space.name}`} collapsible={false}>
      <form
        className="adm-form tk-form"
        onSubmit={(e) => {
          e.preventDefault();
          void enable();
        }}
      >
        <p className="muted small">
          Mỗi dự án có một mã (ví dụ <code>CRM</code>): task đánh số <code>CRM-1</code>, <code>CRM-2</code>… và commit, nhánh, MR có mã này tự nối vào task.
        </p>
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="rq-field tk-key-field">
          <span className="rq-label">Mã dự án</span>
          <input value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} maxLength={10} placeholder="CRM" autoFocus aria-invalid={!!key && !valid} />
          {key && !valid && <span className="small error">2–10 ký tự, chữ hoa và số, bắt đầu bằng chữ.</span>}
        </label>
        <TypeChecks value={types} onChange={setTypes} />
        <div className="adm-form-foot">
          <button type="submit" className="primary" disabled={busy || !valid || !types.length}>
            {busy ? "Đang bật…" : "Bật Task"}
          </button>
        </div>
      </form>
    </Card>
  );
}

function TypeChecks({ value, onChange }: { value: TaskType[]; onChange: (v: TaskType[]) => void }) {
  return (
    <fieldset className="rq-field tk-types">
      <legend className="rq-label">Loại task dùng trong dự án</legend>
      <div className="tk-inline">
        {TASK_TYPES.map((t) => (
          <label key={t} className="inline-check">
            <input
              type="checkbox"
              checked={value.includes(t)}
              onChange={(e) => onChange(e.target.checked ? TASK_TYPES.filter((x) => x === t || value.includes(x)) : value.filter((x) => x !== t))}
            />
            {TYPE_LABEL[t]}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function TaskSettings({ space }: { space: TaskSpace }) {
  return (
    <div className="tk-settings">
      <GeneralCard key={`g${space.business_project_id}`} space={space} />
      <StatusesCard key={`s${space.business_project_id}`} space={space} />
      <RulesCard key={`r${space.business_project_id}`} space={space} />
      <FieldsCard key={`f${space.business_project_id}`} space={space} />
    </div>
  );
}

function GeneralCard({ space }: { space: TaskSpace }) {
  const [key, setKey] = useState(space.key ?? "");
  const [types, setTypes] = useState<TaskType[]>(space.types);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const valid = KEY_RE.test(key);
  const dirty = key !== space.key || types.join() !== space.types.join();
  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}`, { key, types });
      await refreshTasks();
      setMessage("Đã lưu.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }
  return (
    <Card title="Mã và loại task" id="tk-set-general">
      <div className="adm-form tk-form">
        {message && <Message text={message} onClose={() => setMessage(null)} />}
        <label className="rq-field tk-key-field">
          <span className="rq-label">Mã dự án</span>
          <input value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} maxLength={10} aria-invalid={!valid} />
          <span className="small muted">Chỉ đổi được khi dự án chưa có task nào.</span>
        </label>
        <TypeChecks value={types} onChange={setTypes} />
        <div className="adm-form-foot">
          <button type="button" disabled={busy || !dirty || !valid || !types.length} onClick={() => void save()}>
            Lưu
          </button>
        </div>
      </div>
    </Card>
  );
}

type StatusDraft = { id?: number; tmp: string; name: string; category: TaskCategory; transitions: number[] | null };

function StatusesCard({ space }: { space: TaskSpace }) {
  const initial = useMemo<StatusDraft[]>(
    () => sortedStatuses(space).map((s) => ({ id: s.id, tmp: `s${s.id}`, name: s.name, category: s.category, transitions: s.transitions ?? null })),
    [space],
  );
  const [items, setItems] = useState<StatusDraft[]>(initial);
  const [move, setMove] = useState<Record<string, number>>({});
  const [removing, setRemoving] = useState<StatusDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    setItems(initial);
    setMove({});
  }, [initial]);
  const named = new Map(initial.map((s) => [s.id, s.name]));
  const dirty = JSON.stringify(items) !== JSON.stringify(initial);

  const update = (tmp: string, patch: Partial<StatusDraft>) => setItems((list) => list.map((s) => (s.tmp === tmp ? { ...s, ...patch } : s)));
  const shift = (i: number, by: number) =>
    setItems((list) => {
      const next = [...list];
      const [x] = next.splice(i, 1);
      next.splice(i + by, 0, x);
      return next;
    });

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}/statuses`, {
        statuses: items.map((s) => ({ ...(s.id ? { id: s.id } : {}), name: s.name.trim(), category: s.category, transitions: s.transitions })),
        ...(Object.keys(move).length ? { move } : {}),
      });
      await refreshTasks();
      setMove({});
      setMessage("Đã lưu trạng thái.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card title="Trạng thái và luồng chuyển" id="tk-set-statuses" subtitle="Thứ tự là thứ tự cột trên board. Để trống “Được chuyển tới” = chuyển tới mọi trạng thái.">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="table-wrap">
        <table className="tk-edit-table">
          <thead>
            <tr>
              <th>Tên</th>
              <th>Nhóm</th>
              <th>Được chuyển tới</th>
              <th className="tk-actions-col" aria-label="Thao tác" />
            </tr>
          </thead>
          <tbody>
            {items.map((s, i) => (
              <tr key={s.tmp}>
                <td>
                  <input value={s.name} onChange={(e) => update(s.tmp, { name: e.target.value })} aria-label="Tên trạng thái" maxLength={60} />
                </td>
                <td>
                  <select value={s.category} onChange={(e) => update(s.tmp, { category: e.target.value as TaskCategory })} aria-label="Nhóm">
                    {(["todo", "doing", "done"] as TaskCategory[]).map((c) => (
                      <option key={c} value={c}>
                        {CATEGORY_LABEL[c]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="tk-trans-cell">
                  <MultiSelect<number>
                    label={`Được chuyển tới từ ${s.name}`}
                    options={items.filter((o) => o.id && o.tmp !== s.tmp).map((o) => ({ value: o.id as number, label: o.name }))}
                    value={s.transitions ?? []}
                    onChange={(v) => update(s.tmp, { transitions: v.length ? v : null })}
                    empty="Mọi trạng thái"
                  />
                </td>
                <td className="tk-actions-col">
                  <span className="actions">
                    <IconButton icon="chevronUp" label="Lên" disabled={i === 0} onClick={() => shift(i, -1)} />
                    <IconButton icon="chevronDown" label="Xuống" disabled={i === items.length - 1} onClick={() => shift(i, 1)} />
                    <IconButton
                      icon="remove"
                      tone="danger"
                      label="Xoá trạng thái"
                      disabled={items.length <= 1}
                      onClick={() => (s.id ? setRemoving(s) : setItems((list) => list.filter((x) => x.tmp !== s.tmp)))}
                    />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(move).length > 0 && (
        <p className="small muted">
          Khi lưu:{" "}
          {Object.entries(move)
            .map(([from, to]) => `task “${named.get(Number(from)) ?? from}” chuyển sang “${named.get(to) ?? to}”`)
            .join("; ")}
          .
        </p>
      )}
      <div className="adm-form-foot">
        <button
          type="button"
          className="ghost"
          onClick={() => setItems((list) => [...list, { tmp: `n${Date.now()}`, name: "", category: "todo", transitions: null }])}
        >
          Thêm trạng thái
        </button>
        <button type="button" className="ghost" disabled={!dirty || busy} onClick={() => (setItems(initial), setMove({}))}>
          Hoàn tác
        </button>
        <button type="button" disabled={!dirty || busy || items.some((s) => !s.name.trim())} onClick={() => void save()}>
          Lưu trạng thái
        </button>
      </div>
      {removing && (
        <RemoveStatusModal
          status={removing}
          targets={items.filter((s) => s.id && s.tmp !== removing.tmp) as (StatusDraft & { id: number })[]}
          onClose={() => setRemoving(null)}
          onPick={(to) => {
            const from = removing.id as number;
            setMove((m) => {
              // A status that already received tasks of a removed one passes them on too.
              const next: Record<string, number> = {};
              for (const [k, v] of Object.entries(m)) next[k] = v === from ? to : v;
              next[String(from)] = to;
              return next;
            });
            setItems((list) =>
              list
                .filter((x) => x.tmp !== removing.tmp)
                .map((x) => (x.transitions ? { ...x, transitions: x.transitions.filter((t) => t !== from) } : x)),
            );
            setRemoving(null);
          }}
        />
      )}
    </Card>
  );
}

function RemoveStatusModal({
  status,
  targets,
  onClose,
  onPick,
}: {
  status: StatusDraft;
  targets: (StatusDraft & { id: number })[];
  onClose: () => void;
  onPick: (to: number) => void;
}) {
  const [to, setTo] = useState<number | null>(targets[0]?.id ?? null);
  return (
    <Modal
      title={`Xoá trạng thái “${status.name}”`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose}>
            Huỷ
          </button>
          <button type="button" className="primary danger" disabled={to === null} onClick={() => to !== null && onPick(to)}>
            Xoá
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        <p>Task đang ở trạng thái này chuyển sang:</p>
        <select value={to ?? ""} onChange={(e) => setTo(Number(e.target.value))} aria-label="Chuyển task sang">
          {targets.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <p className="small muted">Thay đổi chỉ áp dụng khi bấm “Lưu trạng thái”.</p>
      </div>
    </Modal>
  );
}

function RulesCard({ space }: { space: TaskSpace }) {
  const statuses = sortedStatuses(space);
  const initial = useMemo<TaskRules>(() => ({ ...DEFAULT_RULES, ...(space.rules ?? {}) }), [space]);
  const [rules, setRules] = useState<TaskRules>(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => setRules(initial), [initial]);
  const dirty = JSON.stringify(rules) !== JSON.stringify(initial);
  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}`, { key: space.key, rules });
      await refreshTasks();
      setMessage("Đã lưu luật git.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }
  const target = (k: "start" | "review" | "done", label: string) => (
    <label className="rq-field">
      <span className="rq-label">{label}</span>
      <select value={rules[k] ?? ""} onChange={(e) => setRules({ ...rules, [k]: e.target.value ? Number(e.target.value) : null })}>
        <option value="">Không đặt</option>
        {statuses.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <Card title="Luật đồng bộ git" id="tk-set-rules" subtitle="Trạng thái task chạy theo nhánh, commit và MR có mã task.">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="settings-grid">
        {RULE_TEXT.map((r) => (
          <label key={r.key} className="setting-tile tk-rule">
            <span className="inline-check">
              <input
                type="checkbox"
                role="switch"
                checked={!!rules[r.key]}
                onChange={(e) => setRules({ ...rules, [r.key]: e.target.checked })}
              />
              <strong>{r.label}</strong>
            </span>
            <span className="small muted">{r.hint}</span>
          </label>
        ))}
      </div>
      <div className="rq-grid tk-rule-targets">
        {target("start", "Trạng thái “bắt đầu”")}
        {target("review", "Trạng thái “review”")}
        {target("done", "Trạng thái “xong”")}
      </div>
      <div className="adm-form-foot">
        <button type="button" disabled={!dirty || busy} onClick={() => void save()}>
          Lưu luật
        </button>
      </div>
    </Card>
  );
}

const FIELD_KIND_LABEL: Record<TaskFieldKind, string> = { text: "Văn bản", number: "Số", date: "Ngày", select: "Chọn một", user: "Người" };
type FieldDraft = TaskField & { tmp: string; optionsText: string };

function FieldsCard({ space }: { space: TaskSpace }) {
  const fields = useTaskFields(space.business_project_id);
  const initial = useMemo<FieldDraft[]>(
    () =>
      [...(fields.data ?? [])]
        .sort((a, b) => a.position - b.position)
        .map((f, i) => ({ ...f, tmp: `f${f.id ?? i}`, optionsText: (f.options ?? []).join(", ") })),
    [fields.data],
  );
  const [items, setItems] = useState<FieldDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => setItems(initial), [initial]);
  const dirty = JSON.stringify(items) !== JSON.stringify(initial);
  const update = (tmp: string, patch: Partial<FieldDraft>) => setItems((list) => list.map((f) => (f.tmp === tmp ? { ...f, ...patch } : f)));
  const shift = (i: number, by: number) =>
    setItems((list) => {
      const next = [...list];
      const [x] = next.splice(i, 1);
      next.splice(i + by, 0, x);
      return next;
    });

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}/fields`, {
        fields: items.map((f, i) => ({
          ...(f.id ? { id: f.id } : {}),
          name: f.name.trim(),
          kind: f.kind,
          options:
            f.kind === "select"
              ? f.optionsText
                  .split(",")
                  .map((o) => o.trim())
                  .filter(Boolean)
              : null,
          required: f.required,
          position: i,
          archived: f.archived,
        })),
      });
      await refreshTasks();
      setMessage("Đã lưu trường tuỳ biến.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card title="Trường tuỳ biến" id="tk-set-fields" subtitle="Thêm trường riêng của dự án; trường đã dùng nên ẩn (lưu trữ) thay vì xoá.">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!fields.data ? (
        <Loading error={fields.error} rows={3} />
      ) : (
        <>
          {!items.length ? (
            <p className="small faint">Chưa có trường tuỳ biến.</p>
          ) : (
            <div className="table-wrap">
              <table className="tk-edit-table">
                <thead>
                  <tr>
                    <th>Tên</th>
                    <th>Kiểu</th>
                    <th>Lựa chọn</th>
                    <th>Bắt buộc</th>
                    <th>Lưu trữ</th>
                    <th className="tk-actions-col" aria-label="Thao tác" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((f, i) => (
                    <tr key={f.tmp}>
                      <td>
                        <input value={f.name} onChange={(e) => update(f.tmp, { name: e.target.value })} aria-label="Tên trường" maxLength={60} />
                      </td>
                      <td>
                        <select value={f.kind} onChange={(e) => update(f.tmp, { kind: e.target.value as TaskFieldKind })} aria-label="Kiểu" disabled={!!f.id}>
                          {(Object.keys(FIELD_KIND_LABEL) as TaskFieldKind[]).map((k) => (
                            <option key={k} value={k}>
                              {FIELD_KIND_LABEL[k]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {f.kind === "select" ? (
                          <input value={f.optionsText} onChange={(e) => update(f.tmp, { optionsText: e.target.value })} placeholder="A, B, C" aria-label="Lựa chọn" />
                        ) : (
                          <span className="faint">—</span>
                        )}
                      </td>
                      <td>
                        <input type="checkbox" checked={f.required} onChange={(e) => update(f.tmp, { required: e.target.checked })} aria-label="Bắt buộc" />
                      </td>
                      <td>
                        <input type="checkbox" checked={f.archived} onChange={(e) => update(f.tmp, { archived: e.target.checked })} aria-label="Lưu trữ" />
                      </td>
                      <td className="tk-actions-col">
                        <span className="actions">
                          <IconButton icon="chevronUp" label="Lên" disabled={i === 0} onClick={() => shift(i, -1)} />
                          <IconButton icon="chevronDown" label="Xuống" disabled={i === items.length - 1} onClick={() => shift(i, 1)} />
                          {!f.id && <IconButton icon="remove" tone="danger" label="Bỏ trường mới" onClick={() => setItems((list) => list.filter((x) => x.tmp !== f.tmp))} />}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="adm-form-foot">
            <button
              type="button"
              className="ghost"
              onClick={() =>
                setItems((list) => [...list, { tmp: `n${Date.now()}`, name: "", kind: "text", options: null, optionsText: "", required: false, position: list.length, archived: false }])
              }
            >
              Thêm trường
            </button>
            <button type="button" disabled={!dirty || busy || items.some((f) => !f.name.trim())} onClick={() => void save()}>
              Lưu trường
            </button>
          </div>
        </>
      )}
    </Card>
  );
}
