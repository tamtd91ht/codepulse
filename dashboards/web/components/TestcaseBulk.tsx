"use client";

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { SectionOptions } from "@/components/TestcaseSections";
import { FieldInput } from "@/components/TestFields";
import { Alert, EmptyState, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin, relative } from "@/lib/format";
import {
  BULK_KIND_LABEL,
  type BulkOp,
  type BulkOut,
  type CustomValue,
  EXACT_FILTER_TYPES,
  type Person,
  REPLACE_FIELD_LABEL,
  REPLACE_FIELDS,
  type ReplaceField,
  type ReplacePreview,
  type Section,
  type Suite,
  type TestField,
  type UndoOut,
  isEmptyValue,
  splitList,
} from "@/lib/testcases";
import { errorText } from "@/lib/testruns";

/*
 * Bulk changes of cases (docs/specs/30 §15.10 G10, API §15.11): the extra bulk actions, the
 * "Đã sửa N testcase · Hoàn tác" notice, find & replace with a preview, the list of one's recent
 * bulk changes, and the case list's custom-field columns and filter (§15.8).
 */

/* ---- undo ---- */

export type UndoNote = { text: string; batchId: number | string | null | undefined };

function skippedCount(s: BulkOut["skipped"]): number {
  return typeof s === "number" ? s : Array.isArray(s) ? s.length : 0;
}

/** The notice after a bulk change: what changed, and an Undo for 24 hours. */
export function bulkNote(got: BulkOut, text?: string): UndoNote {
  const skipped = skippedCount(got.skipped);
  return {
    text: `${text ?? `Đã sửa ${got.changed} testcase`}${skipped ? ` · bỏ qua ${skipped}` : ""}`,
    batchId: got.batch_id,
  };
}

function undoText(got: UndoOut): string {
  const parts = [`khôi phục ${got.restored} testcase`];
  if (got.deleted) parts.push(`xoá ${got.deleted} bản sao`);
  return `Đã hoàn tác: ${parts.join(", ")}.`;
}

async function undo(batchId: number | string): Promise<UndoOut> {
  return api.post<UndoOut>(`/testcases/bulk-ops/${encodeURIComponent(String(batchId))}/undo`);
}

function SkippedList({ items }: { items: UndoOut["skipped"] }) {
  if (!items.length) return null;
  return (
    <details className="small tc-undo-skipped">
      <summary>{items.length} testcase không hoàn tác được</summary>
      <ul>
        {items.map((s) => (
          <li key={s.case_id}>
            <span className="tc-key">{s.key ?? `#${s.case_id}`}</span> {s.reason ?? "đã bị sửa sau lần này"}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** "Đã sửa N testcase · Hoàn tác", then what the undo restored or skipped. */
export function UndoNotice({ note, onClose, onUndone }: { note: UndoNote | null; onClose: () => void; onUndone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<UndoOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDone(null);
    setError(null);
  }, [note]);
  if (!note) return null;
  const run = async () => {
    if (note.batchId == null) return;
    setBusy(true);
    setError(null);
    try {
      setDone(await undo(note.batchId));
      onUndone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  if (done)
    return (
      <Alert tone={done.skipped.length ? "warning" : "success"} onClose={onClose}>
        {undoText(done)}
        <SkippedList items={done.skipped} />
      </Alert>
    );
  return (
    <Alert tone={error ? "error" : "success"} onClose={onClose}>
      <span aria-live="polite">{note.text}</span>
      {note.batchId != null && (
        <>
          {" · "}
          <button type="button" className="link sm" onClick={() => void run()} disabled={busy}>
            {busy ? "Đang hoàn tác…" : "Hoàn tác"}
          </button>
        </>
      )}
      {error && <div className="small">Không hoàn tác được: {error}</div>}
    </Alert>
  );
}

/** "Lịch sử thao tác hàng loạt": one's last 20 bulk changes in the dự án, each with Undo while allowed. */
export function BulkOpsModal({ projectId, onClose, onUndone }: { projectId: number; onClose: () => void; onUndone: () => void }) {
  const list = useApi<BulkOp[]>(`/testcases/bulk-ops?business_project_id=${projectId}`);
  const [busy, setBusy] = useState<number | string | null>(null);
  const [result, setResult] = useState<{ id: number | string; out?: UndoOut; error?: string } | null>(null);
  const run = async (op: BulkOp) => {
    setBusy(op.id);
    setResult(null);
    try {
      const out = await undo(op.id);
      setResult({ id: op.id, out });
      await list.mutate();
      onUndone();
    } catch (e) {
      setResult({ id: op.id, error: errorText(e) });
    } finally {
      setBusy(null);
    }
  };
  return (
    <Modal
      title="Lịch sử thao tác hàng loạt"
      onClose={onClose}
      busy={busy !== null}
      width={680}
      footer={
        <button type="button" className="ghost" onClick={onClose}>
          Đóng
        </button>
      }
    >
      <p className="small muted">20 lần sửa hàng loạt gần nhất của bạn. Hoàn tác được trong 24 giờ; testcase đã bị sửa sau đó được giữ nguyên. Xoá hàng loạt không hoàn tác được.</p>
      {!list.data ? (
        <Loading error={list.error} rows={4} />
      ) : !list.data.length ? (
        <EmptyState icon="history" title="Chưa có thao tác hàng loạt nào" />
      ) : (
        <ul className="tc-bulk-ops">
          {list.data.map((op) => (
            <li key={op.id}>
              <div className="tc-bulk-op-main">
                <strong>{BULK_KIND_LABEL[op.kind] ?? op.kind}</strong>
                <span className="small">{op.summary ?? ""}</span>
                <span className="small muted">
                  {op.count} testcase · <span title={dateTimeMin(op.created_at)}>{op.created_at ? relative(op.created_at) : "—"}</span>
                </span>
              </div>
              {op.undone_at ? (
                <Pill>Đã hoàn tác</Pill>
              ) : op.can_undo ? (
                <button type="button" className="sm" onClick={() => void run(op)} disabled={busy !== null}>
                  <Icon name="history" size={14} /> {busy === op.id ? "Đang hoàn tác…" : "Hoàn tác"}
                </button>
              ) : (
                <span className="small muted">Hết hạn hoàn tác</span>
              )}
              {result?.id === op.id && (
                <div className="tc-bulk-op-result">
                  {result.error ? (
                    <Alert tone="error">{result.error}</Alert>
                  ) : result.out ? (
                    <Alert tone={result.out.skipped.length ? "warning" : "success"}>
                      {undoText(result.out)}
                      <SkippedList items={result.out.skipped} />
                    </Alert>
                  ) : null}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

/* ---- the extra bulk actions ---- */

export type BulkEditKind = "tags" | "estimate" | "jira" | "custom" | "copy";
export const BULK_EDIT_LABEL: Record<BulkEditKind, string> = {
  tags: "Thêm / bỏ nhãn",
  estimate: "Đặt ước tính",
  jira: "Thêm mã Jira",
  custom: "Đặt trường tuỳ biến",
  copy: "Sao chép sang bộ / thư mục",
};

/** One extra bulk action on the ticked cases; `onDone` gets the server's answer. */
export function BulkEditModal({
  kind,
  ids,
  suites,
  fields,
  people,
  currentSuite,
  onClose,
  onDone,
}: {
  kind: BulkEditKind;
  ids: number[];
  suites: Suite[];
  fields: TestField[];
  people: Person[] | undefined;
  currentSuite: number | null;
  onClose: () => void;
  onDone: (got: BulkOut, text: string) => void;
}) {
  const [tagsAdd, setTagsAdd] = useState("");
  const [tagsRemove, setTagsRemove] = useState("");
  const [estimate, setEstimate] = useState("");
  const [jira, setJira] = useState("");
  const [fieldKey, setFieldKey] = useState(fields[0]?.key ?? "");
  const [value, setValue] = useState<CustomValue>(null);
  const [suiteId, setSuiteId] = useState<string>(String(currentSuite ?? suites[0]?.id ?? ""));
  const [sectionId, setSectionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sections = useApi<Section[]>(kind === "copy" && suiteId ? `/testcases/sections?suite_id=${suiteId}` : null);
  const field = fields.find((f) => f.key === fieldKey);
  const n = ids.length;

  const body = (): [Record<string, unknown>, string] | null => {
    switch (kind) {
      case "tags": {
        const add = splitList(tagsAdd);
        const remove = splitList(tagsRemove);
        if (!add.length && !remove.length) return null;
        return [{ action: "tags", tags_add: add, tags_remove: remove }, "Đã sửa nhãn"];
      }
      case "estimate":
        return [{ action: "estimate", estimate: estimate.trim() || null }, estimate.trim() ? `Đã đặt ước tính ${estimate.trim()}` : "Đã bỏ ước tính"];
      case "jira": {
        const keys = splitList(jira).map((k) => k.toUpperCase());
        return keys.length ? [{ action: "jira", jira_add: keys }, `Đã thêm ${keys.join(", ")}`] : null;
      }
      case "custom":
        if (!field) return null;
        if (field.required && isEmptyValue(value) && field.type !== "checkbox") return null;
        return [{ action: "custom", field_key: field.key, value: isEmptyValue(value) ? null : value }, `Đã đặt “${field.name}”`];
      case "copy":
        if (!suiteId) return null;
        return [{ action: "copy", suite_id: Number(suiteId), section_id: sectionId ? Number(sectionId) : null }, `Đã sao chép`];
    }
  };
  const ready = body() !== null;

  const run = async () => {
    const b = body();
    if (!b) return;
    setBusy(true);
    setError(null);
    try {
      const got = await api.post<BulkOut>("/testcases/bulk", { ids, ...b[0] });
      onDone(got, `${b[1]} cho ${got.changed} testcase`);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  let content: ReactNode = null;
  if (kind === "tags")
    content = (
      <>
        <label className="adm-field">
          <span className="small muted">Thêm nhãn (cách nhau dấu phẩy)</span>
          <input value={tagsAdd} onChange={(e) => setTagsAdd(e.target.value)} placeholder="smoke, regression" autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Bỏ nhãn</span>
          <input value={tagsRemove} onChange={(e) => setTagsRemove(e.target.value)} placeholder="cu, tam" />
        </label>
      </>
    );
  else if (kind === "estimate")
    content = (
      <label className="adm-field">
        <span className="small muted">Ước tính (để trống để bỏ)</span>
        <input value={estimate} maxLength={40} onChange={(e) => setEstimate(e.target.value)} placeholder="30s, 5m, 1h 30m" autoFocus />
      </label>
    );
  else if (kind === "jira")
    content = (
      <label className="adm-field">
        <span className="small muted">Mã Jira thêm vào (giữ mã đã có)</span>
        <input value={jira} onChange={(e) => setJira(e.target.value)} placeholder="OMX-12, OMX-13" autoFocus />
      </label>
    );
  else if (kind === "custom")
    content = !fields.length ? (
      <EmptyState icon="settings" title="Dự án chưa có trường tuỳ biến cho testcase">
        Thêm ở tab Thiết lập › Trường tuỳ biến.
      </EmptyState>
    ) : (
      <>
        <label className="adm-field">
          <span className="small muted">Trường</span>
          <select
            value={fieldKey}
            onChange={(e) => {
              setFieldKey(e.target.value);
              setValue(null);
            }}
            autoFocus
          >
            {fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        {field && (
          <div className="adm-field">
            <label className="small muted" htmlFor="tc-bulk-value">
              Giá trị{field.required ? " (bắt buộc, không để trống)" : " (để trống để xoá giá trị)"}
            </label>
            <FieldInput key={field.key} field={field} value={value} onChange={setValue} people={people} id="tc-bulk-value" />
          </div>
        )}
      </>
    );
  else if (kind === "copy")
    content = (
      <>
        <label className="adm-field">
          <span className="small muted">Sang bộ</span>
          <select
            value={suiteId}
            onChange={(e) => {
              setSuiteId(e.target.value);
              setSectionId("");
            }}
            autoFocus
          >
            {suites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Thư mục</span>
          <select value={sectionId} onChange={(e) => setSectionId(e.target.value)} disabled={!sections.data}>
            <SectionOptions sections={sections.data ?? []} />
          </select>
        </label>
        <p className="small muted">Bản sao có mã mới, trạng thái Nháp; bản gốc giữ nguyên.</p>
      </>
    );

  return (
    <Modal
      title={`${BULK_EDIT_LABEL[kind]} · ${n} testcase`}
      onClose={onClose}
      busy={busy}
      width={520}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void run()} disabled={busy || !ready}>
            {busy ? "Đang áp dụng…" : kind === "copy" ? `Sao chép ${n} testcase` : `Áp dụng cho ${n} testcase`}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        {content}
      </form>
    </Modal>
  );
}

/* ---- find & replace ---- */

type Seg = { text: string; changed: boolean };

/** Word-level diff of two texts: the parts only in `a` and only in `b` are marked. */
export function textDiff(a: string, b: string): { before: Seg[]; after: Seg[] } {
  const tok = (s: string) => s.match(/\s+|[^\s]+/g) ?? [];
  const x = tok(a);
  const y = tok(b);
  let start = 0;
  while (start < x.length && start < y.length && x[start] === y[start]) start++;
  let endX = x.length;
  let endY = y.length;
  while (endX > start && endY > start && x[endX - 1] === y[endY - 1]) {
    endX--;
    endY--;
  }
  const midX = x.slice(start, endX);
  const midY = y.slice(start, endY);
  const keepX = new Array<boolean>(midX.length).fill(false);
  const keepY = new Array<boolean>(midY.length).fill(false);
  if (midX.length * midY.length <= 250_000) {
    // LCS of the middle part.
    const m = midX.length;
    const n = midY.length;
    const dp: Uint16Array[] = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
    for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) dp[i][j] = midX[i] === midY[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    let i = 0;
    let j = 0;
    while (i < m && j < n) {
      if (midX[i] === midY[j]) {
        keepX[i++] = true;
        keepY[j++] = true;
      } else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
      else j++;
    }
  }
  const segs = (all: string[], mid: string[], keep: boolean[], end: number): Seg[] => {
    const out: Seg[] = [];
    const push = (text: string, changed: boolean) => {
      const last = out[out.length - 1];
      if (last && last.changed === changed) last.text += text;
      else out.push({ text, changed });
    };
    all.slice(0, start).forEach((t) => push(t, false));
    mid.forEach((t, k) => push(t, !keep[k] && t.trim() !== ""));
    all.slice(end).forEach((t) => push(t, false));
    return out;
  };
  return { before: segs(x, midX, keepX, endX), after: segs(y, midY, keepY, endY) };
}

export function DiffText({ segs, mark }: { segs: Seg[]; mark: "del" | "ins" }) {
  return (
    <div className="tc-lines tc-diff">
      {segs.map((s, i) => (s.changed ? mark === "del" ? <del key={i}>{s.text}</del> : <ins key={i}>{s.text}</ins> : <span key={i}>{s.text}</span>))}
    </div>
  );
}

/** "Tìm & thay" in a suite (or one of its sections): preview the matches, then apply with undo. */
export function ReplaceModal({
  suite,
  sections,
  initialSection,
  onClose,
  onApplied,
}: {
  suite: Suite;
  sections: Section[] | undefined;
  initialSection: number | null;
  onClose: () => void;
  onApplied: (got: BulkOut) => void;
}) {
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [fields, setFields] = useState<Set<ReplaceField>>(new Set(REPLACE_FIELDS));
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [regex, setRegex] = useState(false);
  const [sectionId, setSectionId] = useState(initialSection ? String(initialSection) : "");
  const [preview, setPreview] = useState<ReplacePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A change of the query makes the preview stale.
  const asked = useRef("");
  const query = JSON.stringify([find, replace, [...fields].sort(), caseSensitive, regex, sectionId]);
  const stale = preview !== null && asked.current !== query;

  const regexError = useMemo(() => {
    if (!regex || !find) return null;
    if (find.length > 200) return "Biểu thức tối đa 200 ký tự.";
    try {
      new RegExp(find);
      return null;
    } catch (e) {
      return `Biểu thức không hợp lệ: ${(e as Error).message}`;
    }
  }, [regex, find]);

  const call = async (isPreview: boolean) => {
    setBusy(true);
    setError(null);
    const body = {
      suite_id: suite.id,
      section_id: sectionId ? Number(sectionId) : null,
      find,
      replace,
      fields: [...fields],
      case_sensitive: caseSensitive,
      regex,
      preview: isPreview,
    };
    try {
      if (isPreview) {
        setPreview(await api.post<ReplacePreview>("/testcases/replace", body));
        asked.current = query;
      } else {
        onApplied(await api.post<BulkOut>("/testcases/replace", body));
        onClose();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const ready = !!find && fields.size > 0 && !regexError;

  return (
    <Modal
      title={`Tìm & thay · ${suite.name}`}
      onClose={onClose}
      busy={busy}
      width={880}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className={preview && !stale ? undefined : "primary"} onClick={() => void call(true)} disabled={busy || !ready}>
            <Icon name="search" size={14} /> Xem trước
          </button>
          {preview && !stale && (
            <button type="button" className="primary" onClick={() => void call(false)} disabled={busy || !ready || !preview.total}>
              {busy ? "Đang áp dụng…" : `Áp dụng cho ${preview.total} testcase`}
            </button>
          )}
        </>
      }
    >
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) void call(true);
        }}
      >
        {error && <Alert tone="error">{error}</Alert>}
        <div className="tc-form-row">
          <label className="adm-field">
            <span className="small muted">Tìm</span>
            <input value={find} onChange={(e) => setFind(e.target.value)} maxLength={regex ? 200 : 1000} autoFocus aria-invalid={!!regexError || undefined} />
          </label>
          <label className="adm-field">
            <span className="small muted">Thay bằng{regex ? " (dùng $1, $2… cho nhóm)" : ""}</span>
            <input value={replace} onChange={(e) => setReplace(e.target.value)} maxLength={1000} />
          </label>
        </div>
        {regexError && <p className="small bad">{regexError}</p>}
        <fieldset className="tc-fieldset">
          <legend className="small muted">Trong</legend>
          <div className="tc-inline tc-wrap">
            {REPLACE_FIELDS.map((f) => (
              <label key={f} className="inline-check">
                <input
                  type="checkbox"
                  checked={fields.has(f)}
                  onChange={(e) => {
                    const next = new Set(fields);
                    if (e.target.checked) next.add(f);
                    else next.delete(f);
                    setFields(next);
                  }}
                />
                {REPLACE_FIELD_LABEL[f]}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="tc-inline tc-wrap">
          <label className="inline-check">
            <input type="checkbox" checked={caseSensitive} onChange={(e) => setCaseSensitive(e.target.checked)} /> Phân biệt hoa thường
          </label>
          <label className="inline-check">
            <input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} /> Biểu thức chính quy (regex)
          </label>
          <label className="filter">
            <span className="small muted">Thư mục</span>
            <select value={sectionId} onChange={(e) => setSectionId(e.target.value)} disabled={!sections}>
              <SectionOptions sections={sections ?? []} root="Cả bộ" />
            </select>
          </label>
        </div>
      </form>
      {preview && (
        <section className={`tc-replace-preview${stale ? " stale" : ""}`} aria-label="Xem trước" aria-live="polite">
          <p className="small">
            <strong>{preview.total}</strong> testcase khớp
            {preview.matches.length < preview.total ? ` (hiện ${preview.matches.length})` : ""}
            {preview.truncated ? " · xem trước đã rút gọn, áp dụng vẫn thay toàn bộ" : ""}
            {stale ? " · đã đổi điều kiện, bấm Xem trước lại" : ""}
          </p>
          {!preview.matches.length ? (
            <p className="small muted">Không có testcase nào khớp.</p>
          ) : (
            <ul className="tc-replace-list">
              {preview.matches.map((m) => (
                <li key={m.case_id}>
                  <div>
                    <span className="tc-key">{m.key}</span> <strong>{m.title}</strong>
                  </div>
                  {m.changes.map((c, i) => {
                    const d = textDiff(c.before ?? "", c.after ?? "");
                    return (
                      <div key={i} className="tc-replace-change">
                        <span className="small muted">{REPLACE_FIELD_LABEL[c.field] ?? c.field}</span>
                        <div className="tc-replace-pair">
                          <DiffText segs={d.before} mark="del" />
                          <DiffText segs={d.after} mark="ins" />
                        </div>
                      </div>
                    );
                  })}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </Modal>
  );
}

/* ---- custom-field columns and filter of the case list (§15.8) ---- */

const COLUMNS_KEY = (projectId: number) => `cp:testcases:columns:${projectId}`;

/** The custom fields shown as columns, remembered per dự án in this browser. */
export function useFieldColumns(projectId: number): [string[], (keys: string[]) => void] {
  const [keys, setKeys] = useState<string[]>([]);
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(COLUMNS_KEY(projectId));
      const got = raw ? (JSON.parse(raw) as unknown) : [];
      setKeys(Array.isArray(got) ? got.filter((k): k is string => typeof k === "string") : []);
    } catch {
      setKeys([]);
    }
  }, [projectId]);
  const save = (next: string[]) => {
    setKeys(next);
    try {
      window.localStorage.setItem(COLUMNS_KEY(projectId), JSON.stringify(next));
    } catch {
      /* per-viewer convenience only */
    }
  };
  return [keys, save];
}

/** "Cột ▾": tick the custom fields to show as columns. */
export function ColumnChooser({ fields, value, onChange }: { fields: TestField[]; value: string[]; onChange: (keys: string[]) => void }) {
  const box = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const away = (e: MouseEvent | KeyboardEvent) => {
      const el = box.current;
      if (!el?.open) return;
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !el.contains(e.target as Node)) el.open = false;
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", away);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", away);
    };
  }, []);
  if (!fields.length) return null;
  const shown = value.filter((k) => fields.some((f) => f.key === k)).length;
  return (
    <details className="llm-acc-more docs-more tc-columns" ref={box}>
      <summary className="button sm" aria-label="Chọn cột trường tuỳ biến">
        <Icon name="settings" size={14} /> Cột{shown ? ` (${shown})` : ""} <Icon name="chevronDown" size={12} />
      </summary>
      <div className="llm-acc-menu tc-columns-menu" role="group" aria-label="Cột trường tuỳ biến">
        <span className="small muted">Trường tuỳ biến hiện trên bảng</span>
        {fields.map((f) => (
          <label key={f.key} className="inline-check">
            <input
              type="checkbox"
              checked={value.includes(f.key)}
              onChange={(e) => onChange(e.target.checked ? [...value.filter((k) => k !== f.key), f.key] : value.filter((k) => k !== f.key))}
            />
            {f.name}
          </label>
        ))}
      </div>
    </details>
  );
}

/** Filter by one custom field: `f.<key>=<value>` (select for dropdown / multi / checkbox / user, "contains" text otherwise). */
export function FieldFilter({
  fields,
  people,
  value,
  onChange,
}: {
  fields: TestField[];
  people: Person[] | undefined;
  value: { key: string; value: string };
  onChange: (v: { key: string; value: string }) => void;
}) {
  const [text, setText] = useState(value.value);
  const field = fields.find((f) => f.key === value.key);
  const exact = field ? EXACT_FILTER_TYPES.includes(field.type) : false;
  useEffect(() => setText(value.value), [value.key, value.value]);
  useEffect(() => {
    if (exact || !field) return;
    const t = setTimeout(() => {
      if (text !== value.value) onChange({ key: value.key, value: text });
    }, 300);
    return () => clearTimeout(t);
  }, [text, exact, field, value, onChange]);
  if (!fields.length) return null;
  return (
    <span className="tc-field-filter">
      <label className="filter">
        <span className="small muted">Trường</span>
        <select value={value.key} onChange={(e) => onChange({ key: e.target.value, value: "" })}>
          <option value="">Mọi trường</option>
          {fields.map((f) => (
            <option key={f.key} value={f.key}>
              {f.name}
            </option>
          ))}
        </select>
      </label>
      {field &&
        (exact ? (
          <label className="filter">
            <span className="sr-only">Giá trị của {field.name}</span>
            <select value={value.value} onChange={(e) => onChange({ key: value.key, value: e.target.value })}>
              <option value="">Mọi giá trị</option>
              {field.type === "checkbox" ? (
                <>
                  <option value="true">Có</option>
                  <option value="false">Không</option>
                </>
              ) : field.type === "user" ? (
                (people ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))
              ) : (
                field.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))
              )}
            </select>
          </label>
        ) : (
          <label className="filter">
            <span className="sr-only">{field.name} chứa</span>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder={`${field.name} chứa…`} />
          </label>
        ))}
    </span>
  );
}
