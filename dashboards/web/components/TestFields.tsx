"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

import { Modal, ReorderControls, useDragList } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Alert, Card, EmptyState, Loading, Pill, Segmented } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import {
  type CaseTemplate,
  type CustomValue,
  type CustomValues,
  FIELD_ENTITY_LABEL,
  FIELD_MAX_LEN,
  FIELD_TYPE_LABEL,
  FIELD_TYPES,
  type FieldEntity,
  type FieldType,
  OPTION_TYPES,
  type Person,
  type Suite,
  type TestField,
  TEMPLATE_LABEL,
  fieldText,
  isEmptyValue,
} from "@/lib/testcases";
import { errorText } from "@/lib/testruns";

/*
 * Custom fields (docs/specs/30 §15.8 G5, API §15.11): the "Trường tuỳ biến" card of Thiết lập
 * (tests.manage edits), the inputs of the case editor and of "Ghi kết quả", and the values shown.
 */

/** The fields of a dự án for one entity (live only unless `archived`). Errors leave forms usable. */
export function useFields(projectId: number | null | undefined, entity: FieldEntity, archived = false) {
  return useApi<TestField[]>(projectId ? `/testcases/fields?business_project_id=${projectId}&entity=${entity}${archived ? "&archived=true" : ""}` : null);
}

/** People of a dự án (same cache key as the runs' pickers). */
export function usePeople(projectId: number | null | undefined, wanted = true) {
  return useApi<Person[]>(projectId && wanted ? `/testcases/people?business_project_id=${projectId}` : null);
}

/** Values to start a new case / result with: each field's default. */
export function defaultValues(fields: TestField[]): CustomValues {
  const out: CustomValues = {};
  for (const f of fields) if (!isEmptyValue(f.default)) out[f.key] = f.default;
  return out;
}

/** Only the keys of `fields` that hold a value (false of a checkbox kept). */
export function pickValues(fields: TestField[], values: CustomValues): CustomValues {
  const out: CustomValues = {};
  for (const f of fields) if (!isEmptyValue(values[f.key])) out[f.key] = values[f.key];
  return out;
}

/** One input for one field, by its type. */
export function FieldInput({
  field,
  value,
  onChange,
  people,
  id,
  invalid,
  disabled,
}: {
  field: Pick<TestField, "key" | "name" | "type" | "options" | "required">;
  value: CustomValue | undefined;
  onChange: (v: CustomValue) => void;
  people: Person[] | undefined;
  id?: string;
  invalid?: boolean;
  disabled?: boolean;
}) {
  const common = { id, disabled, "aria-invalid": invalid || undefined, "aria-required": field.required || undefined };
  const text = typeof value === "string" ? value : value == null ? "" : String(value);
  switch (field.type) {
    case "textarea":
      return <textarea {...common} rows={3} maxLength={FIELD_MAX_LEN.textarea} value={text} onChange={(e) => onChange(e.target.value)} />;
    case "number":
      return (
        <input
          {...common}
          type="number"
          inputMode="decimal"
          value={typeof value === "number" ? String(value) : text}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      );
    case "date":
      return <input {...common} type="date" value={text} onChange={(e) => onChange(e.target.value || null)} />;
    case "url":
      return <input {...common} type="url" maxLength={FIELD_MAX_LEN.url} placeholder="https://…" value={text} onChange={(e) => onChange(e.target.value)} />;
    case "checkbox":
      return (
        <label className="inline-check">
          <input type="checkbox" id={id} disabled={disabled} checked={value === true} onChange={(e) => onChange(e.target.checked)} /> Có
        </label>
      );
    case "dropdown":
      return (
        <select {...common} value={text} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">—</option>
          {field.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
          {text && !field.options.includes(text) && <option value={text}>{text}</option>}
        </select>
      );
    case "multiselect": {
      const chosen = Array.isArray(value) ? value : [];
      return (
        <div className="tf-multi" role="group" aria-label={field.name} aria-invalid={invalid || undefined}>
          {field.options.map((o) => (
            <label key={o} className="inline-check">
              <input
                type="checkbox"
                disabled={disabled}
                checked={chosen.includes(o)}
                onChange={(e) => onChange(e.target.checked ? [...chosen, o] : chosen.filter((x) => x !== o))}
              />
              {o}
            </label>
          ))}
          {!field.options.length && <span className="small muted">Trường chưa có lựa chọn nào.</span>}
        </div>
      );
    }
    case "user":
      return (
        <select {...common} value={value == null || value === "" ? "" : String(value)} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
          <option value="">—</option>
          {(people ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
          {value != null && value !== "" && people && !people.some((p) => p.id === Number(value)) && <option value={String(value)}>#{String(value)}</option>}
        </select>
      );
    default:
      return <input {...common} maxLength={FIELD_MAX_LEN.text} value={text} onChange={(e) => onChange(e.target.value)} />;
  }
}

/** The inputs of the fields that apply, two per row; required ones marked with *. */
export function CustomFieldsEditor({
  fields,
  values,
  onChange,
  people,
  invalid,
  legend = "Trường tuỳ biến",
}: {
  fields: TestField[];
  values: CustomValues;
  onChange: (values: CustomValues) => void;
  people: Person[] | undefined;
  /** Keys shown as invalid (after a failed check). */
  invalid?: Set<string>;
  legend?: string;
}) {
  if (!fields.length) return null;
  return (
    <fieldset className="tc-fieldset tf-fields">
      <legend className="small muted">{legend}</legend>
      <div className="tf-grid">
        {fields.map((f) => {
          const id = `tf-${f.entity}-${f.key}`;
          const wide = f.type === "textarea" || f.type === "multiselect";
          return (
            <div key={f.key} className={`adm-field${wide ? " tf-wide" : ""}`}>
              <label htmlFor={f.type === "multiselect" ? undefined : id} className="small muted">
                {f.name}
                {f.required && (
                  <span className="tf-req" aria-label="bắt buộc">
                    {" "}
                    *
                  </span>
                )}
              </label>
              <FieldInput field={f} value={values[f.key]} onChange={(v) => onChange({ ...values, [f.key]: v })} people={people} id={id} invalid={invalid?.has(f.key)} />
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

/** <dt>/<dd> pairs for the values held (archived fields too, marked), inside a <dl>. */
export function CustomFieldsView({ fields, values, people }: { fields: TestField[] | undefined; values: CustomValues | undefined; people: Person[] | undefined }) {
  if (!fields || !values) return null;
  const shown = fields.filter((f) => !isEmptyValue(values[f.key]));
  return (
    <>
      {shown.map((f) => (
        <Fragment key={f.key}>
          <dt>
            {f.name}
            {f.archived_at ? <span className="small muted"> (đã lưu trữ)</span> : null}
          </dt>
          <dd className={f.type === "textarea" ? "tc-lines" : undefined}>
            {f.type === "url" && typeof values[f.key] === "string" && /^https?:\/\//i.test(String(values[f.key])) ? (
              <a className="link" href={String(values[f.key])} target="_blank" rel="noreferrer">
                {String(values[f.key])}
              </a>
            ) : f.type === "multiselect" && Array.isArray(values[f.key]) ? (
              <span className="tc-tags">
                {(values[f.key] as string[]).map((o) => (
                  <span key={o} className="tag">
                    {o}
                  </span>
                ))}
              </span>
            ) : (
              fieldText(f, values[f.key], people)
            )}
          </dd>
        </Fragment>
      ))}
    </>
  );
}

/** "name: value · name: value" for a result in the history. */
export function customSummary(fields: TestField[] | undefined, values: CustomValues | undefined, people: Person[] | undefined): string {
  if (!fields || !values) return "";
  return fields
    .filter((f) => !isEmptyValue(values[f.key]))
    .map((f) => `${f.name}: ${fieldText(f, values[f.key], people)}`)
    .join(" · ");
}

/* ---- Thiết lập › Trường tuỳ biến ---- */

function scopeText(f: TestField, suites: Suite[] | undefined): string {
  const parts: string[] = [];
  if (f.suite_ids.length) {
    const names = f.suite_ids.map((id) => suites?.find((s) => s.id === id)?.name ?? `#${id}`);
    parts.push(names.length > 2 ? `${names.slice(0, 2).join(", ")} +${names.length - 2}` : names.join(", "));
  } else parts.push("Mọi bộ");
  if (f.templates.length) parts.push(f.templates.map((t) => TEMPLATE_LABEL[t] ?? t).join(", "));
  return parts.join(" · ");
}

export function FieldsCard({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const [entity, setEntity] = useState<FieldEntity>("case");
  const [showArchived, setShowArchived] = useState(false);
  const list = useFields(projectId, entity, showArchived);
  const suites = useApi<Suite[]>(`/testcases/suites?business_project_id=${projectId}`);
  const [editing, setEditing] = useState<TestField | "new" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const all = useMemo(() => [...(list.data ?? [])].sort((a, b) => a.position - b.position), [list.data]);
  const live = all.filter((f) => !f.archived_at);
  const archived = all.filter((f) => f.archived_at);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await list.mutate();
    } catch (e) {
      setError(errorText(e));
      await list.mutate();
    } finally {
      setBusy(false);
    }
  };
  const move = (from: number, to: number) => {
    if (to < 0 || to >= live.length || from === to) return;
    const next = [...live];
    const [f] = next.splice(from, 1);
    next.splice(to, 0, f);
    const ids = next.map((x) => x.id);
    // Shown at once; the server's order comes back after.
    void list.mutate(
      [...next.map((x, i) => ({ ...x, position: i })), ...archived],
      { revalidate: false },
    );
    void act(() => api.put("/testcases/fields/order", { business_project_id: projectId, entity, ids }));
  };
  const rowProps = useDragList(move);

  return (
    <Card
      title="Trường tuỳ biến"
      id="tr-fields"
      subtitle="Thêm trường riêng của dự án cho testcase và cho kết quả test; lưu trữ thì giá trị cũ vẫn giữ"
      actions={
        canEdit ? (
          <button type="button" className="sm" onClick={() => setEditing("new")}>
            <Icon name="plus" size={14} /> Thêm trường
          </button>
        ) : undefined
      }
    >
      <div className="tf-head">
        <Segmented<FieldEntity>
          label="Trường của"
          value={entity}
          onChange={setEntity}
          options={[
            { value: "case", label: "Testcase" },
            { value: "result", label: "Kết quả" },
          ]}
        />
        <label className="inline-check small">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Hiện trường đã lưu trữ
        </label>
      </div>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {!list.data ? (
        <Loading error={list.error} rows={3} />
      ) : !all.length ? (
        <EmptyState icon="settings" title={`Chưa có trường tuỳ biến nào cho ${FIELD_ENTITY_LABEL[entity].toLowerCase()}`}>
          {canEdit
            ? entity === "case"
              ? "Ví dụ “Module”, “Môi trường”, “Người review”: thêm ở đây rồi điền khi sửa testcase."
              : "Ví dụ “Thiết bị”, “Build URL”: điền khi ghi kết quả test."
            : undefined}
        </EmptyState>
      ) : (
        <ul className="tf-list" aria-label={`Trường của ${FIELD_ENTITY_LABEL[entity]}`}>
          {live.map((f, i) => (
            <li key={f.id} className="tf-row" {...(canEdit ? rowProps(i) : {})}>
              {canEdit && <ReorderControls index={i} count={live.length} name={`trường ${f.name}`} onMove={(to) => move(i, to)} />}
              <div className="tf-main">
                <strong>{f.name}</strong> <code className="small">{f.key}</code>
                <div className="small muted">
                  {FIELD_TYPE_LABEL[f.type] ?? f.type}
                  {OPTION_TYPES.includes(f.type) && f.options.length ? ` · ${f.options.join(", ")}` : ""} · {scopeText(f, suites.data)}
                  {!isEmptyValue(f.default) ? ` · mặc định: ${fieldText(f, f.default)}` : ""}
                </div>
              </div>
              {f.required && <Pill tone="warn">Bắt buộc</Pill>}
              {canEdit && (
                <span className="tr-row-tools">
                  <IconButton icon="edit" label={`Sửa trường ${f.name}`} onClick={() => setEditing(f)} disabled={busy} />
                  <IconButton icon="folder" label={`Lưu trữ trường ${f.name}`} onClick={() => void act(() => api.delete(`/testcases/fields/${f.id}`))} disabled={busy} />
                </span>
              )}
            </li>
          ))}
          {!live.length && <li className="small muted">Chưa có trường đang dùng.</li>}
          {archived.map((f) => (
            <li key={f.id} className="tf-row archived">
              <div className="tf-main">
                <strong>{f.name}</strong> <code className="small">{f.key}</code>
                <div className="small muted">{FIELD_TYPE_LABEL[f.type] ?? f.type} · đã lưu trữ, giá trị cũ vẫn giữ</div>
              </div>
              <Pill>Đã lưu trữ</Pill>
              {canEdit && (
                <button type="button" className="ghost sm" onClick={() => void act(() => api.post(`/testcases/fields/${f.id}/restore`))} disabled={busy}>
                  Khôi phục
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!canEdit && list.data && <p className="small muted">Cần quyền “Quản lý test” để thêm hoặc sửa trường.</p>}
      {editing && (
        <FieldModal
          projectId={projectId}
          entity={entity}
          field={editing === "new" ? null : editing}
          suites={suites.data}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void list.mutate();
          }}
        />
      )}
    </Card>
  );
}

function FieldModal({
  projectId,
  entity,
  field,
  suites,
  onClose,
  onSaved,
}: {
  projectId: number;
  entity: FieldEntity;
  field: TestField | null;
  suites: Suite[] | undefined;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(field?.name ?? "");
  const [type, setType] = useState<FieldType>(field?.type ?? "text");
  const [options, setOptions] = useState((field?.options ?? []).join("\n"));
  const [required, setRequired] = useState(field?.required ?? false);
  const [dflt, setDflt] = useState<CustomValue>(field?.default ?? null);
  const [suiteIds, setSuiteIds] = useState<Set<number>>(new Set(field?.suite_ids ?? []));
  const [templates, setTemplates] = useState<Set<CaseTemplate>>(new Set(field?.templates ?? []));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const people = usePeople(projectId, type === "user");
  // One option per line (an option may hold a comma).
  const optionList = useMemo(
    () => [
      ...new Set(
        options
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean),
      ),
    ],
    [options],
  );
  const hasOptions = OPTION_TYPES.includes(type);

  useEffect(() => {
    // A new type drops a default of another shape.
    if (!field) setDflt(null);
  }, [type, field]);

  const save = async () => {
    if (hasOptions && !optionList.length) {
      setError("Thêm ít nhất một lựa chọn.");
      return;
    }
    setBusy(true);
    setError(null);
    const body = {
      name: name.trim(),
      ...(hasOptions ? { options: optionList } : {}),
      required,
      default: isEmptyValue(dflt) ? null : dflt,
      suite_ids: [...suiteIds],
      templates: [...templates],
    };
    try {
      if (field) await api.patch(`/testcases/fields/${field.id}`, body);
      else await api.post("/testcases/fields", { business_project_id: projectId, entity, type, ...body });
      onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  const toggle = <T,>(set: Set<T>, v: T, on: boolean) => {
    const next = new Set(set);
    if (on) next.add(v);
    else next.delete(v);
    return next;
  };
  const defaultField = { key: "default", name: "Giá trị mặc định", type, options: optionList, required: false };

  return (
    <Modal
      title={field ? `Sửa trường “${field.name}”` : `Trường mới cho ${FIELD_ENTITY_LABEL[entity].toLowerCase()}`}
      onClose={onClose}
      busy={busy}
      width={620}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            {busy ? "Đang lưu…" : field ? "Lưu" : "Tạo trường"}
          </button>
        </>
      }
    >
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) void save();
        }}
      >
        {error && <Alert tone="error">{error}</Alert>}
        <label className="adm-field">
          <span className="small muted">
            Tên <span className="tf-req">*</span>
          </span>
          <input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} autoFocus placeholder="Module, Môi trường, Người review…" />
        </label>
        {field && (
          <p className="small muted">
            Mã <code>{field.key}</code> (dùng khi lọc <code>f.{field.key}=</code> và qua API) không đổi sau khi tạo.
          </p>
        )}
        <label className="adm-field">
          <span className="small muted">Kiểu{field ? " (không đổi được sau khi tạo)" : ""}</span>
          <select value={type} onChange={(e) => setType(e.target.value as FieldType)} disabled={!!field}>
            {FIELD_TYPES.map((t) => (
              <option key={t} value={t}>
                {FIELD_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </label>
        {hasOptions && (
          <label className="adm-field">
            <span className="small muted">
              Lựa chọn (mỗi dòng một lựa chọn) <span className="tf-req">*</span>
            </span>
            <textarea rows={4} value={options} onChange={(e) => setOptions(e.target.value)} placeholder={"Web\nAndroid\niOS"} />
          </label>
        )}
        <label className="inline-check">
          <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} /> Bắt buộc
          <span className="small muted">
            {" "}
            — {entity === "case" ? "chặn lưu testcase khi để trống" : "chặn ghi kết quả có trạng thái khi để trống"} (nhập Sheet, AI, CI không bị chặn: điền giá trị mặc định)
          </span>
        </label>
        <div className="adm-field">
          <label className="small muted" htmlFor="tf-default">
            Giá trị mặc định (điền sẵn khi tạo)
          </label>
          <FieldInput field={defaultField} value={dflt} onChange={setDflt} people={people.data} id="tf-default" />
        </div>
        <fieldset className="tc-fieldset">
          <legend className="small muted">Áp dụng cho bộ (không chọn = mọi bộ)</legend>
          {!suites ? (
            <Loading rows={2} />
          ) : !suites.length ? (
            <span className="small muted">Dự án chưa có bộ testcase.</span>
          ) : (
            <ul className="tc-doc-pick">
              {suites.map((s) => (
                <li key={s.id}>
                  <label className="inline-check">
                    <input type="checkbox" checked={suiteIds.has(s.id)} onChange={(e) => setSuiteIds(toggle(suiteIds, s.id, e.target.checked))} />
                    {s.name}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
        <fieldset className="tc-fieldset">
          <legend className="small muted">Áp dụng cho template (không chọn = mọi template)</legend>
          <div className="tc-inline">
            {(
              [
                ["text", "Văn bản"],
                ["steps", "Từng bước"],
                ["bdd", "BDD (Gherkin)"],
                ["exploratory", "Khám phá"],
              ] as const
            ).map(([t, label]) => (
              <label key={t} className="inline-check">
                <input type="checkbox" checked={templates.has(t)} onChange={(e) => setTemplates(toggle(templates, t, e.target.checked))} /> {label}
              </label>
            ))}
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
