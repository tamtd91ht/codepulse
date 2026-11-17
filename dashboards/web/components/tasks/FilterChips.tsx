"use client";

import { useEffect, useMemo, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { SavedTaskFilter, SearchCond, SearchFieldInfo, SearchFields, SearchNode, SearchOp, SearchTree, TaskSearchPage, TaskSpace } from "@/lib/types";

import { CATEGORY_LABEL, RESOLUTION_LABEL, RESOLUTIONS, TASK_TYPES, TYPE_LABEL, errorText, personName, priorityList, sortedStatuses, useSprints, useTaskPeople, useVersions } from "./taskParts";

/*
 * Bộ lọc dạng chip (docs/specs/31 §21.4): each chip is "Trường · toán tử · giá trị", "+" adds one.
 * Chips "là" of the same field are ORed (shown with "hoặc"); everything else is ANDed. The tree
 * goes to POST /tasks/search.
 */

export type ChipCond = SearchCond & { id: string };

let seq = 0;
export const chipId = () => `c${Date.now().toString(36)}${(seq++).toString(36)}`;

export const FIELD_LABEL: Record<string, string> = {
  text: "Văn bản",
  status: "Trạng thái",
  category: "Nhóm trạng thái",
  type: "Loại",
  priority: "Ưu tiên",
  resolution: "Lý do đóng",
  assignee: "Người làm",
  reporter: "Người tạo",
  watcher: "Người theo dõi",
  label: "Nhãn",
  component: "Thành phần",
  sprint: "Sprint",
  version: "Phiên bản",
  parent: "Task cha",
  due: "Hạn",
  created: "Ngày tạo",
  updated: "Cập nhật",
  resolved: "Ngày đóng",
  points: "Story point",
  age_in_column_hours: "Giờ trong cột",
  has_mr: "Có MR",
  has_critical: "Có critical",
  test_failed: "Testcase Failed",
  blocked: "Bị chặn",
  dod_met: "DoD",
};

const OP_LABEL: Record<SearchOp, string> = {
  is: "là",
  is_not: "không là",
  empty: "trống",
  not_empty: "có giá trị",
  before: "trước ngày",
  after: "sau ngày",
  within_days: "trong N ngày",
  gte: "≥",
  lte: "≤",
};
const DOD_LABEL: Record<string, string> = { met: "đạt", unmet: "chưa đạt", blocking: "chưa đạt, đang bắt buộc" };
/** Kinds whose value is a list (one picked per chip; OR by adding chips). */
const LIST_KINDS = new Set(["ref", "enum", "person", "sprint", "array", "parent"]);

function opLabel(kind: string, op: SearchOp): string {
  if (kind === "text") return op === "is" ? "chứa" : op === "is_not" ? "không chứa" : OP_LABEL[op];
  if (op === "within_days") return "trong";
  return OP_LABEL[op];
}

/** Chips → the tree: "là" chips of one field in an `any` group, the rest ANDed. */
export function toTree(chips: SearchCond[]): SearchTree {
  const all: SearchNode[] = [];
  const groups = new Map<string, SearchCond[]>();
  for (const c of chips) {
    const cond: SearchCond = { field: c.field, op: c.op, ...(c.value === undefined ? {} : { value: c.value }) };
    if (c.op === "is") {
      const g = groups.get(c.field);
      if (g) g.push(cond);
      else {
        const fresh = [cond];
        groups.set(c.field, fresh);
        all.push({ any: fresh });
      }
    } else all.push(cond);
  }
  return { all: all.map((n) => ("any" in n && n.any && n.any.length === 1 ? n.any[0] : n)) };
}

/** A tree (a saved filter, the sentence filter) → chips; nested groups are flattened. */
export function fromTree(tree: SearchTree | null | undefined): ChipCond[] {
  const out: ChipCond[] = [];
  const walk = (n: SearchNode) => {
    if ("field" in n) {
      const value = Array.isArray(n.value) && n.value.length > 1 ? null : n.value;
      if (value === null) for (const v of n.value as string[]) out.push({ id: chipId(), field: n.field, op: n.op, value: [v] });
      else out.push({ id: chipId(), field: n.field, op: n.op, value: n.value });
      return;
    }
    for (const x of n.all ?? n.any ?? []) walk(x);
  };
  if (tree) walk(tree);
  return out;
}

const first = (v: SearchCond["value"]) => (Array.isArray(v) ? (v[0] ?? "") : v === undefined ? "" : String(v));

export function FilterChips({ space, chips, onChange }: { space: TaskSpace; chips: ChipCond[]; onChange: (next: ChipCond[]) => void }) {
  const bp = space.business_project_id;
  const fields = useApi<SearchFields>(`/tasks/search/fields?space=${bp}`);
  const people = useTaskPeople(bp);
  const sprints = useSprints(bp);
  const versions = useVersions(bp);
  const [editing, setEditing] = useState<ChipCond | null>(null);

  const infos = useMemo(() => {
    const m = new Map<string, SearchFieldInfo>();
    for (const f of fields.data?.fields ?? []) if (f.field !== "space") m.set(f.field, f);
    for (const f of fields.data?.custom ?? []) m.set(f.field, f);
    return m;
  }, [fields.data]);
  const fieldName = (f: string) => FIELD_LABEL[f] ?? infos.get(f)?.name ?? f;

  function valueLabel(c: SearchCond): string {
    const info = infos.get(c.field);
    const v = first(c.value);
    if (c.op === "empty" || c.op === "not_empty") return "";
    if (c.op === "within_days") return `${v} ngày${c.field === "due" ? " tới" : " qua"}`;
    if (c.op === "before" || c.op === "after") return date(v);
    if (info?.kind === "bool") return v === "true" || c.value === true ? "có" : "không";
    switch (c.field) {
      case "status":
        return space.statuses.find((s) => String(s.id) === v)?.name ?? `#${v}`;
      case "category":
        return CATEGORY_LABEL[v as keyof typeof CATEGORY_LABEL] ?? v;
      case "type":
        return TYPE_LABEL[v as keyof typeof TYPE_LABEL] ?? v;
      case "priority":
        return priorityList(space).find((p) => p.key === v)?.name ?? v;
      case "resolution":
        return RESOLUTION_LABEL[v as keyof typeof RESOLUTION_LABEL] ?? v;
      case "sprint":
        return v === "active" ? "đang chạy" : v === "backlog" ? "backlog" : ((sprints.data ?? []).find((s) => String(s.id) === v)?.name ?? `#${v}`);
      case "version":
        return (versions.data ?? []).find((x) => String(x.id) === v)?.name ?? `#${v}`;
      case "due":
        if (v === "overdue") return "quá hạn";
        if (v === "week") return "7 ngày tới";
        return date(v);
      case "dod_met":
        return DOD_LABEL[v] ?? v;
      case "age_in_column_hours":
        return `${v} giờ`;
    }
    if (info?.kind === "person") return v === "me" ? "tôi" : v === "none" ? "chưa có" : personName((people.data ?? []).find((p) => String(p.id) === v) ?? null);
    if (info?.kind === "date") return date(v);
    return v;
  }

  // Which chips sit in an OR group with the one before them.
  const orBefore = (i: number) => chips[i].op === "is" && chips.slice(0, i).some((c) => c.field === chips[i].field && c.op === "is");

  return (
    <div className="fs-chips">
      <ul className="fs-chip-list" aria-label="Điều kiện lọc">
        {chips.map((c, i) => {
          const kind = infos.get(c.field)?.kind ?? "";
          const text = [fieldName(c.field), opLabel(kind, c.op), valueLabel(c)].filter(Boolean).join(" · ");
          return (
            <li key={c.id} className="fs-chip-item">
              {orBefore(i) && <span className="fs-or">hoặc</span>}
              <span className={`fs-chip${editing?.id === c.id ? " editing" : ""}`}>
                <button type="button" className="fs-chip-text" onClick={() => setEditing(c)} aria-label={`Sửa điều kiện ${text}`}>
                  {text}
                </button>
                <IconButton icon="close" size={14} label={`Bỏ điều kiện ${text}`} onClick={() => onChange(chips.filter((x) => x.id !== c.id))} />
              </span>
            </li>
          );
        })}
        <li>
          <button type="button" className="ghost sm fs-add" onClick={() => setEditing({ id: "", field: "type", op: "is", value: [] })} disabled={!fields.data} aria-label="Thêm điều kiện">
            <Icon name="plus" size={14} /> Điều kiện
          </button>
        </li>
      </ul>
      {fields.error && (
        <p className="small fs-error" role="alert">
          Không tải được danh sách trường: {errorText(fields.error)}{" "}
          <button type="button" className="link" onClick={() => void fields.mutate()}>
            Thử lại
          </button>
        </p>
      )}
      {editing && (
        <ChipEditor
          key={editing.id || "new"}
          space={space}
          initial={editing}
          infos={infos}
          fieldName={fieldName}
          lookups={{ people: people.data ?? [], sprints: sprints.data ?? [], versions: versions.data ?? [] }}
          onCancel={() => setEditing(null)}
          onSave={(c) => {
            onChange(editing.id ? chips.map((x) => (x.id === editing.id ? { ...c, id: editing.id } : x)) : [...chips, { ...c, id: chipId() }]);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

interface Lookups {
  people: { id: number; full_name: string | null; primary_email: string | null }[];
  sprints: { id: number; name: string; state: string }[];
  versions: { id: number; name: string }[];
}

function ChipEditor({
  space,
  initial,
  infos,
  fieldName,
  lookups,
  onCancel,
  onSave,
}: {
  space: TaskSpace;
  initial: ChipCond;
  infos: Map<string, SearchFieldInfo>;
  fieldName: (f: string) => string;
  lookups: Lookups;
  onCancel: () => void;
  onSave: (c: SearchCond) => void;
}) {
  const [field, setField] = useState(initial.field);
  const [op, setOp] = useState<SearchOp>(initial.op);
  const [value, setValue] = useState(first(initial.value));
  const info = infos.get(field);
  const kind = info?.kind ?? "";
  const ops = info?.ops ?? ["is"];

  useEffect(() => {
    if (!ops.includes(op)) setOp(ops[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field, info]);

  const needsValue = op !== "empty" && op !== "not_empty";
  const ready = !needsValue || value.trim() !== "";

  function save() {
    if (!ready) return;
    const cond: SearchCond = { field, op };
    if (needsValue) {
      if (op === "within_days" || op === "gte" || op === "lte" || ((kind === "number" || kind === "hours") && op === "is")) cond.value = Number(value);
      else if (kind === "bool") cond.value = value === "true";
      else if (LIST_KINDS.has(kind) && op !== "before" && op !== "after") cond.value = [value.trim()];
      else cond.value = value.trim();
    }
    onSave(cond);
  }

  const options = valueOptions(field, kind, op, space, info, lookups);

  return (
    <form
      className="fs-editor"
      aria-label={initial.id ? "Sửa điều kiện" : "Thêm điều kiện"}
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <label className="rq-field">
        <span className="rq-label">Trường</span>
        <select
          value={field}
          autoFocus
          onChange={(e) => {
            setField(e.target.value);
            setValue("");
          }}
        >
          {[...infos.keys()].map((f) => (
            <option key={f} value={f}>
              {fieldName(f)}
            </option>
          ))}
        </select>
      </label>
      <label className="rq-field">
        <span className="rq-label">Toán tử</span>
        <select value={op} onChange={(e) => setOp(e.target.value as SearchOp)}>
          {ops.map((o) => (
            <option key={o} value={o}>
              {opLabel(kind, o)}
            </option>
          ))}
        </select>
      </label>
      {needsValue && (
        <label className="rq-field">
          <span className="rq-label">{op === "within_days" ? "Số ngày" : "Giá trị"}</span>
          {options ? (
            <select value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">Chọn…</option>
              {options.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          ) : op === "before" || op === "after" || (kind === "date" && op === "is") ? (
            <input type="date" value={value} onChange={(e) => setValue(e.target.value)} />
          ) : op === "within_days" || op === "gte" || op === "lte" || kind === "number" ? (
            <input type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} />
          ) : (
            <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={field === "parent" ? "CRM-12" : field === "text" ? "Từ trong tiêu đề, mô tả, bình luận" : ""} maxLength={200} />
          )}
        </label>
      )}
      <div className="fs-editor-actions">
        <button type="button" className="ghost sm" onClick={onCancel}>
          Huỷ
        </button>
        <button type="submit" className="sm" disabled={!ready}>
          {initial.id ? "Lưu" : "Thêm"}
        </button>
      </div>
    </form>
  );
}

/** Picks for a value, or null for a free input. */
function valueOptions(field: string, kind: string, op: SearchOp, space: TaskSpace, info: SearchFieldInfo | undefined, l: Lookups): [string, string][] | null {
  if (op === "before" || op === "after" || op === "within_days" || op === "gte" || op === "lte") return null;
  if (kind === "bool")
    return [
      ["true", "có"],
      ["false", "không"],
    ];
  if (kind === "dod") return Object.entries(DOD_LABEL);
  if (kind === "person") return [["me", "Tôi"], ["none", "Chưa có"], ...l.people.map((p): [string, string] => [String(p.id), personName(p)])];
  if (field === "due" && op === "is")
    return [
      ["overdue", "Quá hạn"],
      ["week", "7 ngày tới"],
    ];
  switch (field) {
    case "status":
      return sortedStatuses(space).map((s) => [String(s.id), s.name]);
    case "category":
      return Object.entries(CATEGORY_LABEL);
    case "type":
      return TASK_TYPES.filter((t) => space.types.includes(t)).map((t) => [t, TYPE_LABEL[t]]);
    case "priority":
      return priorityList(space).map((p) => [p.key, p.name]);
    case "resolution":
      return RESOLUTIONS.map((r) => [r, RESOLUTION_LABEL[r]]);
    case "sprint":
      return [["active", "Sprint đang chạy"], ["backlog", "Backlog"], ...l.sprints.map((s): [string, string] => [String(s.id), s.name])];
    case "version":
      return l.versions.map((v) => [String(v.id), v.name]);
  }
  if (info?.custom_kind === "select") return (info.options ?? []).map((o) => [o, o]);
  return null;
}

/* Bộ lọc đã ghim (§21.4): quick filters on the Board. Picking one asks the server which tasks
 * match (the Board filters its own rows by those keys). */
export function PinnedFilters({ bp, onKeys }: { bp: number; onKeys: (keys: Set<string> | null) => void }) {
  const pinned = useApi<SavedTaskFilter[]>(`/tasks/search/filters?space=${bp}&pinned=true`);
  const [on, setOn] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setOn(null);
    onKeys(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bp]);
  if (!pinned.data?.length) return null;

  async function pick(id: number) {
    if (on === id) {
      setOn(null);
      onKeys(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<TaskSearchPage>("/tasks/search", { space: [bp], filter_id: id, limit: 500 });
      setOn(id);
      onKeys(new Set(res.items.map((t) => t.key)));
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }

  return (
    <div className="rq-field fs-pinned">
      <span className="rq-label">Lọc nhanh</span>
      <div className="fs-pinned-list" role="group" aria-label="Bộ lọc đã ghim">
        {pinned.data.map((f) => (
          <button key={f.id} type="button" className={`fs-pin${on === f.id ? " on" : ""}`} aria-pressed={on === f.id} disabled={busy} onClick={() => void pick(f.id)}>
            <Icon name="pin" size={12} /> {f.name}
          </button>
        ))}
      </div>
      {error && (
        <span className="small fs-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
