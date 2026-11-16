"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { IconButton } from "@/components/icons";
import { MultiSelect } from "@/components/MultiSelect";
import { Card, Loading, Message } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { TaskCategory, TaskField, TaskFieldKind, TaskPriorityColor, TaskPriorityLevel, TaskRules, TaskSpace, TaskType } from "@/lib/types";

import {
  CATEGORY_LABEL,
  LEVEL_LABEL,
  PRIORITY_COLORS,
  PRIORITY_LEVELS,
  PriorityIcon,
  TASK_TYPES,
  TYPE_LABEL,
  errorText,
  priorityList,
  refreshTasks,
  sortedStatuses,
  useTaskFields,
} from "./taskParts";
import { BOARD_MODE_TEXT, type BoardMode, BoardModeField, StatusFlow, type TemplateKey, TemplatePicker, WizardSteps, templateOf } from "./TaskTemplates";

/* Cài đặt (§13, tasks.manage): key, types, statuses and their transitions, priorities (§18), git rules, custom fields. */

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

/** First setup of a dự án's Task (shown to its managers while it is off): a 3-step wizard,
 * mã + loại task → mẫu dự án → xác nhận (§19.6). */
export function EnableSpace({ space, onDone }: { space: TaskSpace; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [key, setKey] = useState(space.key ?? "");
  const [types, setTypes] = useState<TaskType[]>(space.types?.length ? space.types : TASK_TYPES);
  // Types follow the chosen template until the manager picks them by hand.
  const [typesTouched, setTypesTouched] = useState(false);
  const [template, setTemplate] = useState<TemplateKey>("scrum");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = KEY_RE.test(key);
  const tpl = templateOf(template);
  const stepOk = step !== 0 || (valid && types.length > 0);
  function pickTemplate(next: TemplateKey) {
    setTemplate(next);
    if (!typesTouched) setTypes(templateOf(next).types);
  }
  async function enable() {
    if (!valid || !types.length) return;
    setBusy(true);
    setError(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}`, { key, types, template });
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
          if (step < 2) {
            if (stepOk) setStep(step + 1);
          } else void enable();
        }}
      >
        <WizardSteps step={step} labels={["Mã và loại task", "Chọn mẫu", "Xác nhận"]} />
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        {step === 0 && (
          <>
            <p className="muted small">
              Mỗi dự án có một mã (ví dụ <code>CRM</code>): task đánh số <code>CRM-1</code>, <code>CRM-2</code>… và commit, nhánh, MR có mã này tự nối vào task.
            </p>
            <label className="rq-field tk-key-field">
              <span className="rq-label">Mã dự án</span>
              <input value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} maxLength={10} placeholder="CRM" autoFocus aria-invalid={!!key && !valid} />
              {key && !valid && <span className="small error">2–10 ký tự, chữ hoa và số, bắt đầu bằng chữ.</span>}
            </label>
            <TypeChecks
              value={types}
              onChange={(v) => {
                setTypes(v);
                setTypesTouched(true);
              }}
            />
          </>
        )}
        {step === 1 && <TemplatePicker value={template} onChange={pickTemplate} />}
        {step === 2 && (
          <dl className="tt-summary">
            <dt>Mã dự án</dt>
            <dd>
              <code>{key}</code> (task đầu tiên: <code>{key}-1</code>)
            </dd>
            <dt>Mẫu</dt>
            <dd>{tpl.name}</dd>
            <dt>Trạng thái</dt>
            <dd>
              <StatusFlow statuses={tpl.statuses} />
            </dd>
            <dt>Loại task</dt>
            <dd>{types.map((t) => TYPE_LABEL[t]).join(", ")}</dd>
            <dt>Chế độ bảng</dt>
            <dd>{BOARD_MODE_TEXT[tpl.board_mode].label}</dd>
            <dd className="small muted tt-summary-note">Trạng thái, loại, luật git và chế độ bảng đều sửa được sau trong Cài đặt.</dd>
          </dl>
        )}
        <div className="adm-form-foot">
          {step > 0 && (
            <button type="button" disabled={busy} onClick={() => setStep(step - 1)}>
              Quay lại
            </button>
          )}
          {step < 2 ? (
            <button type="submit" className="primary" disabled={!stepOk}>
              Tiếp
            </button>
          ) : (
            <button type="submit" className="primary" disabled={busy || !valid || !types.length}>
              {busy ? "Đang bật…" : "Bật Task"}
            </button>
          )}
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
      <PrioritiesCard key={`p${space.business_project_id}`} space={space} />
      <RulesCard key={`r${space.business_project_id}`} space={space} />
      <FieldsCard key={`f${space.business_project_id}`} space={space} />
      <JiraImportCard key={`j${space.business_project_id}`} space={space} />
    </div>
  );
}

function GeneralCard({ space }: { space: TaskSpace }) {
  const [key, setKey] = useState(space.key ?? "");
  const [types, setTypes] = useState<TaskType[]>(space.types);
  const [mode, setMode] = useState<BoardMode>(space.board_mode ?? "scrum");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const valid = KEY_RE.test(key);
  const dirty = key !== space.key || types.join() !== space.types.join() || mode !== (space.board_mode ?? "scrum");
  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      await api.put(`/tasks/spaces/${space.business_project_id}`, { key, types, board_mode: mode });
      await refreshTasks();
      setMessage("Đã lưu.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }
  return (
    <Card title="Mã, loại task và chế độ bảng" id="tk-set-general">
      <div className="adm-form tk-form">
        {message && <Message text={message} onClose={() => setMessage(null)} />}
        <label className="rq-field tk-key-field">
          <span className="rq-label">Mã dự án</span>
          <input value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} maxLength={10} aria-invalid={!valid} />
          <span className="small muted">Chỉ đổi được khi dự án chưa có task nào.</span>
        </label>
        <TypeChecks value={types} onChange={setTypes} />
        <BoardModeField value={mode} onChange={setMode} />
        <div className="adm-form-foot">
          <button type="button" disabled={busy || !dirty || !valid || !types.length} onClick={() => void save()}>
            Lưu
          </button>
        </div>
      </div>
    </Card>
  );
}

type StatusDraft = {
  id?: number;
  tmp: string;
  name: string;
  category: TaskCategory;
  transitions: number[] | null;
  wip_limit: number | null;
  age_warning_hours: number | null;
};

function StatusesCard({ space }: { space: TaskSpace }) {
  const initial = useMemo<StatusDraft[]>(
    () =>
      sortedStatuses(space).map((s) => ({
        id: s.id,
        tmp: `s${s.id}`,
        name: s.name,
        category: s.category,
        transitions: s.transitions ?? null,
        wip_limit: s.wip_limit ?? null,
        age_warning_hours: s.age_warning_hours ?? null,
      })),
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
        statuses: items.map((s) => ({
          ...(s.id ? { id: s.id } : {}),
          name: s.name.trim(),
          category: s.category,
          transitions: s.transitions,
          wip_limit: s.wip_limit,
          age_warning_hours: s.age_warning_hours,
        })),
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
              <th title="Số task tối đa nên có trong cột (chỉ cảnh báo, không chặn)">WIP</th>
              <th title="Task ở trạng thái này quá số giờ này thì card hiện cảnh báo">Cảnh báo sau (giờ)</th>
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
                <td>
                  <input
                    type="number"
                    min={1}
                    className="tk-num-input"
                    value={s.wip_limit ?? ""}
                    aria-label={`Giới hạn WIP của ${s.name}`}
                    placeholder="—"
                    onChange={(e) => update(s.tmp, { wip_limit: e.target.value ? Number(e.target.value) : null })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min={1}
                    className="tk-num-input"
                    value={s.age_warning_hours ?? ""}
                    aria-label={`Số giờ cảnh báo của ${s.name}`}
                    placeholder="—"
                    onChange={(e) => update(s.tmp, { age_warning_hours: e.target.value ? Number(e.target.value) : null })}
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
          onClick={() => setItems((list) => [...list, { tmp: `n${Date.now()}`, name: "", category: "todo", transitions: null, wip_limit: null, age_warning_hours: null }])}
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

type PriorityDraft = { id?: number; tmp: string; name: string; level: TaskPriorityLevel; color: TaskPriorityColor; is_default: boolean };

/** The dự án's priorities, as Jira's priority scheme (docs/specs/31 §18). */
function PrioritiesCard({ space }: { space: TaskSpace }) {
  const initial = useMemo<PriorityDraft[]>(
    () => priorityList(space).map((p) => ({ id: p.id > 0 ? p.id : undefined, tmp: `p${p.id}`, name: p.name, level: p.level, color: p.color, is_default: p.is_default })),
    [space],
  );
  const [items, setItems] = useState<PriorityDraft[]>(initial);
  const [move, setMove] = useState<Record<string, number>>({});
  const [removing, setRemoving] = useState<PriorityDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    setItems(initial);
    setMove({});
  }, [initial]);
  const named = new Map(initial.map((p) => [p.id, p.name]));
  const dirty = JSON.stringify(items) !== JSON.stringify(initial);
  const names = items.map((p) => p.name.trim().toLowerCase());
  const invalid = names.some((n) => !n) || new Set(names).size !== names.length;

  const update = (tmp: string, patch: Partial<PriorityDraft>) => setItems((list) => list.map((p) => (p.tmp === tmp ? { ...p, ...patch } : p)));
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
      await api.put(`/tasks/spaces/${space.business_project_id}/priorities`, {
        priorities: items.map((p) => ({ ...(p.id ? { id: p.id } : {}), name: p.name.trim(), level: p.level, color: p.color, is_default: p.is_default })),
        ...(Object.keys(move).length ? { move } : {}),
      });
      await refreshTasks();
      setMove({});
      setMessage("Đã lưu mức ưu tiên.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card
      title="Mức ưu tiên"
      id="tk-set-priorities"
      subtitle="Như priority scheme của Jira: tên, màu và thứ tự riêng của dự án (trên cùng = gấp nhất). Mũi tên cho biết mức độ; Bug production mức Cao nhất / Cao tính P1 / P2 trong DPI."
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="table-wrap">
        <table className="tk-edit-table">
          <thead>
            <tr>
              <th>Hiển thị</th>
              <th>Tên</th>
              <th>Mũi tên (mức độ)</th>
              <th>Màu</th>
              <th>Mặc định</th>
              <th className="tk-actions-col" aria-label="Thao tác" />
            </tr>
          </thead>
          <tbody>
            {items.map((p, i) => (
              <tr key={p.tmp}>
                <td>
                  <PriorityIcon name={p.name || "…"} level={p.level} color={p.color} withLabel />
                </td>
                <td>
                  <input value={p.name} onChange={(e) => update(p.tmp, { name: e.target.value })} aria-label="Tên mức ưu tiên" maxLength={40} />
                </td>
                <td>
                  <select value={p.level} onChange={(e) => update(p.tmp, { level: e.target.value as TaskPriorityLevel })} aria-label="Mũi tên">
                    {PRIORITY_LEVELS.map((l) => (
                      <option key={l} value={l}>
                        {LEVEL_LABEL[l]}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select value={p.color} onChange={(e) => update(p.tmp, { color: e.target.value as TaskPriorityColor })} aria-label="Màu">
                    {PRIORITY_COLORS.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <label className="inline-check">
                    <input type="radio" name={`prio-default-${space.business_project_id}`} checked={p.is_default} onChange={() => setItems((list) => list.map((x) => ({ ...x, is_default: x.tmp === p.tmp })))} />
                    <span className="visually-hidden">Mặc định khi tạo task</span>
                  </label>
                </td>
                <td className="tk-actions-col">
                  <span className="actions">
                    <IconButton icon="chevronUp" label="Lên" disabled={i === 0} onClick={() => shift(i, -1)} />
                    <IconButton icon="chevronDown" label="Xuống" disabled={i === items.length - 1} onClick={() => shift(i, 1)} />
                    <IconButton
                      icon="remove"
                      tone="danger"
                      label="Xoá mức ưu tiên"
                      disabled={items.length <= 1}
                      onClick={() => (p.id ? setRemoving(p) : setItems((list) => list.filter((x) => x.tmp !== p.tmp)))}
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
      {invalid && <p className="small error">Tên mức ưu tiên không được trống hay trùng nhau.</p>}
      <div className="adm-form-foot">
        <button
          type="button"
          className="ghost"
          disabled={items.length >= 12}
          onClick={() => setItems((list) => [...list, { tmp: `n${Date.now()}`, name: "", level: "medium", color: "blue", is_default: false }])}
        >
          Thêm mức
        </button>
        <button type="button" className="ghost" disabled={!dirty || busy} onClick={() => (setItems(initial), setMove({}))}>
          Hoàn tác
        </button>
        <button type="button" disabled={!dirty || busy || invalid} onClick={() => void save()}>
          Lưu mức ưu tiên
        </button>
      </div>
      {removing && (
        <Modal
          title={`Xoá mức ưu tiên “${removing.name}”`}
          onClose={() => setRemoving(null)}
          width={440}
          footer={
            <>
              <button type="button" className="ghost" onClick={() => setRemoving(null)}>
                Huỷ
              </button>
              <RemovePriorityPick
                targets={items.filter((x) => x.id && x.tmp !== removing.tmp) as (PriorityDraft & { id: number })[]}
                onPick={(to) => {
                  const from = removing.id as number;
                  setMove((m) => {
                    const next: Record<string, number> = {};
                    for (const [k, v] of Object.entries(m)) next[k] = v === from ? to : v;
                    next[String(from)] = to;
                    return next;
                  });
                  setItems((list) => {
                    const left = list.filter((x) => x.tmp !== removing.tmp);
                    return removing.is_default ? left.map((x) => ({ ...x, is_default: x.id === to })) : left;
                  });
                  setRemoving(null);
                }}
              />
            </>
          }
        >
          <p className="small muted">Task đang ở mức này chuyển sang mức bạn chọn. Thay đổi chỉ áp dụng khi bấm “Lưu mức ưu tiên”.</p>
        </Modal>
      )}
    </Card>
  );
}

function RemovePriorityPick({ targets, onPick }: { targets: (PriorityDraft & { id: number })[]; onPick: (to: number) => void }) {
  const [to, setTo] = useState<number | null>(targets[0]?.id ?? null);
  return (
    <>
      <select value={to ?? ""} onChange={(e) => setTo(Number(e.target.value))} aria-label="Chuyển task sang">
        {targets.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <button type="button" className="primary danger" disabled={to === null} onClick={() => to !== null && onPick(to)}>
        Xoá
      </button>
    </>
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

type JiraSource = { project_key: string; issues: number; imported: number };
type JiraPreview = { project_key: string; to_create: number; skipped: number; preview: { jira_key: string; title: string | null; type: string; state: string }[] };
type JiraDone = { project_key: string; created: number; skipped: number; defects_rewritten: number; mapping: { jira_key: string; task_key: string }[] };

/** Import the issues CodePulse synced from Jira into this dự án's tasks (docs/specs/31 §19.2). */
export function JiraImportCard({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const got = useApi<{ sources: JiraSource[]; imported: number }>(`/tasks/spaces/${bp}/import/jira`);
  const [project, setProject] = useState("");
  const [rewrite, setRewrite] = useState(true);
  const [preview, setPreview] = useState<JiraPreview | null>(null);
  const [done, setDone] = useState<JiraDone | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const sources = got.data?.sources ?? [];
  const chosen = project || sources[0]?.project_key || "";

  async function call(dry: boolean) {
    setBusy(true);
    setMessage(null);
    try {
      const out = await api.post<JiraPreview & JiraDone>(`/tasks/spaces/${bp}/import/jira`, { project_key: chosen, dry_run: dry, rewrite_defects: rewrite });
      if (dry) {
        setPreview(out);
        setDone(null);
      } else {
        setDone(out);
        setPreview(null);
        await refreshTasks();
        await got.mutate();
      }
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card
      title="Nhập từ Jira"
      id="tk-set-jira-import"
      subtitle="Chuyển issue Jira đã đồng bộ về CodePulse thành task (không gọi Jira). Mã Jira cũ vẫn trỏ về task mới: commit, MR, bug của testcase."
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!got.data ? (
        <Loading error={got.error} rows={2} />
      ) : !sources.length ? (
        <p className="small faint">Chưa có issue Jira nào đồng bộ cho dự án này.</p>
      ) : (
        <div className="adm-form tk-form">
          <div className="rq-grid">
            <label className="rq-field">
              <span className="rq-label">Project Jira</span>
              <select value={chosen} onChange={(e) => (setProject(e.target.value), setPreview(null), setDone(null))}>
                {sources.map((s) => (
                  <option key={s.project_key} value={s.project_key}>
                    {s.project_key} · {s.issues} issue{s.imported ? ` · đã nhập ${s.imported}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="inline-check" style={{ alignSelf: "end" }}>
              <input type="checkbox" checked={rewrite} onChange={(e) => setRewrite(e.target.checked)} />
              Đổi mã bug Jira trong kết quả test sang mã task
            </label>
          </div>
          {preview && (
            <div className="small">
              <p style={{ margin: "0 0 6px" }}>
                Sẽ tạo <strong>{preview.to_create}</strong> task{preview.skipped ? `, bỏ qua ${preview.skipped} issue đã nhập` : ""}.
              </p>
              {preview.preview.length > 0 && (
                <ul className="tk-git-list">
                  {preview.preview.map((r) => (
                    <li key={r.jira_key}>
                      <span className="tk-key">{r.jira_key}</span>
                      <span className="tk-ellipsis">{r.title}</span>
                      <span className="spacer" />
                      <span className="small muted">
                        {TYPE_LABEL[r.type as TaskType] ?? r.type} · {r.state === "closed" ? "Đã đóng" : "Đang mở"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {done && (
            <p className="small" role="status">
              Đã tạo <strong>{done.created}</strong> task từ {done.project_key}
              {done.skipped ? `, bỏ qua ${done.skipped} issue đã nhập` : ""}
              {done.defects_rewritten ? `, đổi mã bug ở ${done.defects_rewritten} kết quả test` : ""}.
            </p>
          )}
          <div className="adm-form-foot">
            <button type="button" className="ghost" disabled={busy || !chosen} onClick={() => void call(true)}>
              Xem trước
            </button>
            <button type="button" disabled={busy || !chosen || !preview || preview.to_create === 0} onClick={() => void call(false)}>
              {busy ? "Đang nhập…" : `Nhập ${preview?.to_create ?? ""} task`}
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
