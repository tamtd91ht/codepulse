"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Alert, Drawer, Loading, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import {
  type CaseDetail,
  type CaseValues,
  type ImportResult,
  KIND_LABEL,
  PRIORITIES,
  PRIORITY_LABEL,
  PRIORITY_TONE,
  type ReadOut,
  STATUS_LABEL,
  STATUS_TONE,
  STATUSES,
  type Suite,
  type TcProject,
  type TestCase,
  splitList,
} from "@/lib/testcases";

/*
 * Testcase (docs/specs/30): the parts of the page. A case opens in a side drawer (read, edit,
 * history); a suite is made / renamed / shared in small modals; a Google Sheet or an .xlsx / .csv
 * is imported in two steps (read the tabs, check the columns, import).
 */

export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

export function PriorityPill({ value }: { value: TestCase["priority"] }) {
  return <Pill tone={PRIORITY_TONE[value]}>{PRIORITY_LABEL[value]}</Pill>;
}

export function StatusPill({ value }: { value: TestCase["status"] }) {
  return <Pill tone={STATUS_TONE[value]}>{STATUS_LABEL[value]}</Pill>;
}

/** Multi-line text as written (steps keep their lines). */
function Lines({ text }: { text: string | null }) {
  if (!text) return <span className="muted">—</span>;
  return <div className="tc-lines">{text}</div>;
}

const VIA_LABEL: Record<string, string> = { editor: "Sửa", import: "Nhập lại", ai: "AI", restore: "Khôi phục", bulk: "Sửa hàng loạt" };

type Draft = {
  code: string;
  section: string;
  title: string;
  preconditions: string;
  steps: string;
  test_data: string;
  expected: string;
  notes: string;
  priority: TestCase["priority"];
  kind: TestCase["kind"];
  automation: string;
  tags: string;
  jira_keys: string;
  code_paths: string;
  status: TestCase["status"];
};

function toDraft(c?: Partial<TestCase>): Draft {
  return {
    code: c?.code ?? "",
    section: c?.section ?? "",
    title: c?.title ?? "",
    preconditions: c?.preconditions ?? "",
    steps: c?.steps ?? "",
    test_data: c?.test_data ?? "",
    expected: c?.expected ?? "",
    notes: c?.notes ?? "",
    priority: c?.priority ?? "medium",
    kind: c?.kind ?? "manual",
    automation: c?.automation ?? "",
    tags: (c?.tags ?? []).join(", "),
    jira_keys: (c?.jira_keys ?? []).join(", "),
    code_paths: (c?.code_paths ?? []).join("\n"),
    status: c?.status ?? "draft",
  };
}

function fromDraft(d: Draft): CaseValues {
  return {
    code: d.code.trim() || undefined,
    section: d.section,
    title: d.title,
    preconditions: d.preconditions,
    steps: d.steps,
    test_data: d.test_data,
    expected: d.expected,
    notes: d.notes,
    priority: d.priority,
    kind: d.kind,
    automation: d.automation,
    tags: splitList(d.tags),
    jira_keys: splitList(d.jira_keys),
    code_paths: splitList(d.code_paths),
    status: d.status,
  };
}

function CaseForm({ draft, set, sections }: { draft: Draft; set: (d: Draft) => void; sections: string[] }) {
  const field = (k: keyof Draft) => ({
    value: draft[k],
    onChange: (e: { target: { value: string } }) => set({ ...draft, [k]: e.target.value }),
  });
  return (
    <div className="adm-form tc-form">
      <div className="tc-form-row">
        <label className="adm-field tc-code">
          <span className="small muted">Mã</span>
          <input {...field("code")} maxLength={40} placeholder="Tự cấp" />
        </label>
        <label className="adm-field">
          <span className="small muted">Chức năng / màn hình</span>
          <input {...field("section")} maxLength={200} list="tc-sections" />
          <datalist id="tc-sections">
            {sections.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </label>
      </div>
      <label className="adm-field">
        <span className="small muted">Tiêu đề *</span>
        <input {...field("title")} maxLength={500} autoFocus />
      </label>
      <label className="adm-field">
        <span className="small muted">Tiền điều kiện</span>
        <textarea {...field("preconditions")} rows={2} />
      </label>
      <label className="adm-field">
        <span className="small muted">Các bước (mỗi bước một dòng)</span>
        <textarea {...field("steps")} rows={5} placeholder={"1. …\n2. …"} />
      </label>
      <label className="adm-field">
        <span className="small muted">Dữ liệu test</span>
        <textarea {...field("test_data")} rows={2} />
      </label>
      <label className="adm-field">
        <span className="small muted">Kết quả mong đợi</span>
        <textarea {...field("expected")} rows={3} />
      </label>
      <div className="tc-form-row three">
        <label className="adm-field">
          <span className="small muted">Ưu tiên</span>
          <select {...field("priority")}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABEL[p]}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Loại</span>
          <select {...field("kind")}>
            <option value="manual">{KIND_LABEL.manual}</option>
            <option value="automated">{KIND_LABEL.automated}</option>
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Trạng thái</span>
          <select {...field("status")}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="tc-form-row">
        <label className="adm-field">
          <span className="small muted">Nhãn (cách nhau dấu phẩy)</span>
          <input {...field("tags")} placeholder="smoke, regression" />
        </label>
        <label className="adm-field">
          <span className="small muted">Jira</span>
          <input {...field("jira_keys")} placeholder="OMX-12, OMX-13" />
        </label>
      </div>
      <label className="adm-field">
        <span className="small muted">Vùng code phủ (glob, mỗi dòng một mẫu) — để gợi ý test lại khi MR đổi các file này</span>
        <textarea {...field("code_paths")} rows={2} placeholder={"src/payment/**\n**/checkout*.ts"} />
      </label>
      <label className="adm-field">
        <span className="small muted">File test tự động (đường dẫn trong repo, có thể kèm ::tên test)</span>
        <input {...field("automation")} maxLength={500} placeholder="tests/test_refund.py::test_partial" />
      </label>
      <label className="adm-field">
        <span className="small muted">Ghi chú</span>
        <textarea {...field("notes")} rows={2} />
      </label>
    </div>
  );
}

/** One case: read it, edit it, look back at its history. `caseId` null: a new case of `suite`. */
export function CaseDrawer({
  caseId,
  suite,
  sections,
  onClose,
  onSaved,
}: {
  caseId: number | null;
  suite: Suite | null;
  sections: string[];
  onClose: () => void;
  onSaved: (c?: TestCase) => void;
}) {
  const detail = useApi<CaseDetail>(caseId ? `/testcases/${caseId}` : null);
  const [editing, setEditing] = useState(caseId === null);
  const [draft, setDraft] = useState<Draft>(toDraft());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const c = detail.data;

  useEffect(() => {
    if (c && !editing) setDraft(toDraft(c));
  }, [c, editing]);

  const save = async () => {
    if (!draft.title.trim()) {
      setError("Testcase cần tiêu đề.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (c) {
        const got = await api.patch<CaseDetail>(`/testcases/${c.id}`, { ...fromDraft(draft), code: draft.code.trim() || c.code, revision: c.revision });
        await detail.mutate(got, false);
        setEditing(false);
        onSaved(got);
      } else if (suite) {
        const got = await api.post<TestCase>("/testcases", { ...fromDraft(draft), suite_id: suite.id });
        onSaved(got);
        onClose();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const restore = async (n: number) => {
    if (!c) return;
    setBusy(true);
    try {
      const got = await api.post<CaseDetail>(`/testcases/${c.id}/restore/${n}`);
      await detail.mutate(got, false);
      onSaved(got);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!c) return;
    setBusy(true);
    try {
      await api.delete(`/testcases/${c.id}`);
      onSaved();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const title = c ? `${c.code} · ${c.title}` : "Testcase mới";
  const actions = editing ? (
    <>
      {c && (
        <button className="ghost sm" onClick={() => setEditing(false)} disabled={busy}>
          Huỷ
        </button>
      )}
      <button className="primary sm" onClick={() => void save()} disabled={busy}>
        {busy ? "Đang lưu…" : "Lưu"}
      </button>
    </>
  ) : c?.can_edit ? (
    <>
      <button className="ghost sm" onClick={() => setConfirming(true)} disabled={busy}>
        Xoá
      </button>
      <button className="sm" onClick={() => setEditing(true)}>
        <Icon name="edit" size={14} /> Sửa
      </button>
    </>
  ) : null;

  return (
    <Drawer title={title} onClose={onClose} width={680} actions={actions}>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {confirming && c && (
        <Alert tone="warning" onClose={() => setConfirming(false)}>
          Xoá testcase {c.code}? Không khôi phục được — nếu chỉ là không dùng nữa, nên đổi trạng thái sang “Lỗi thời”.{" "}
          <button className="link sm error" onClick={() => void remove()} disabled={busy}>
            Xoá hẳn
          </button>
        </Alert>
      )}
      {caseId && !c ? (
        <Loading error={detail.error} rows={6} />
      ) : editing ? (
        <CaseForm draft={draft} set={setDraft} sections={sections} />
      ) : c ? (
        <div className="tc-view">
          <div className="tc-view-pills">
            <PriorityPill value={c.priority} />
            <StatusPill value={c.status} />
            <Pill>{KIND_LABEL[c.kind]}</Pill>
            {c.source === "ai" && <Pill tone="primary" icon="sparkle">AI</Pill>}
            {c.problems.map((p) => (
              <Pill key={p} tone="warn" icon="alert">
                {p}
              </Pill>
            ))}
          </div>
          <dl className="tc-dl">
            <dt>Chức năng</dt>
            <dd>{c.section || <span className="muted">—</span>}</dd>
            <dt>Tiền điều kiện</dt>
            <dd>
              <Lines text={c.preconditions} />
            </dd>
            <dt>Các bước</dt>
            <dd>
              <Lines text={c.steps} />
            </dd>
            <dt>Dữ liệu test</dt>
            <dd>
              <Lines text={c.test_data} />
            </dd>
            <dt>Kết quả mong đợi</dt>
            <dd>
              <Lines text={c.expected} />
            </dd>
            {c.tags.length > 0 && (
              <>
                <dt>Nhãn</dt>
                <dd className="tc-tags">
                  {c.tags.map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </dd>
              </>
            )}
            {c.jira_keys.length > 0 && (
              <>
                <dt>Jira</dt>
                <dd>{c.jira_keys.join(", ")}</dd>
              </>
            )}
            {c.code_paths.length > 0 && (
              <>
                <dt>Vùng code</dt>
                <dd>
                  {c.code_paths.map((g) => (
                    <code key={g} className="tc-glob">
                      {g}
                    </code>
                  ))}
                </dd>
              </>
            )}
            {c.automation && (
              <>
                <dt>Test tự động</dt>
                <dd>
                  <code>{c.automation}</code>
                </dd>
              </>
            )}
            {c.notes && (
              <>
                <dt>Ghi chú</dt>
                <dd>
                  <Lines text={c.notes} />
                </dd>
              </>
            )}
          </dl>
          <p className="small muted">
            Phiên bản {c.revision}
            {c.updated_by ? ` · sửa lần cuối bởi ${c.updated_by.name}` : ""}
            {c.updated_at ? ` · ${dateTimeMin(c.updated_at)}` : ""}
          </p>
          {c.revisions.length > 0 && (
            <div className="tc-history">
              <button className="link sm" onClick={() => setShowHistory((v) => !v)} aria-expanded={showHistory}>
                <Icon name="history" size={14} /> Lịch sử ({c.revisions.length})
              </button>
              {showHistory && (
                <ul>
                  {c.revisions.map((r) => (
                    <li key={r.number}>
                      <div>
                        <strong>Bản {r.number}</strong> <span className="small muted">— {VIA_LABEL[r.via] ?? r.via}{r.changed_by ? ` bởi ${r.changed_by.name}` : ""}, {dateTimeMin(r.changed_at)}</span>
                      </div>
                      <div className="small">{String(r.snapshot.title ?? "")}</div>
                      {c.can_edit && (
                        <button className="link sm" onClick={() => void restore(r.number)} disabled={busy}>
                          Khôi phục bản này
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      ) : null}
    </Drawer>
  );
}

/** Make a suite, or rename one. */
export function SuiteModal({
  projectId,
  suite,
  onClose,
  onSaved,
}: {
  projectId: number;
  suite: Suite | null;
  onClose: () => void;
  onSaved: (s: Suite) => void;
}) {
  const [name, setName] = useState(suite?.name ?? "");
  const [description, setDescription] = useState(suite?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = suite
        ? await api.patch<Suite>(`/testcases/suites/${suite.id}`, { name, description })
        : await api.post<Suite>("/testcases/suites", { business_project_id: projectId, name, description });
      onSaved(got);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={suite ? "Đổi tên bộ testcase" : "Bộ testcase mới"}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            {suite ? "Lưu" : "Tạo bộ"}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) void save();
        }}
      >
        <label className="adm-field">
          <span className="small muted">Tên bộ (ví dụ: Đăng nhập, Thanh toán)</span>
          <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea value={description} rows={2} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </form>
    </Modal>
  );
}

/** The dự án sharing a suite (docs/specs/30 §3.1). */
export function ShareModal({ suite, projects, onClose, onSaved }: { suite: Suite; projects: TcProject[]; onClose: () => void; onSaved: (s: Suite) => void }) {
  const [chosen, setChosen] = useState<Set<number>>(new Set(suite.projects.filter((p) => p !== suite.business_project_id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    try {
      onSaved(await api.put<Suite>(`/testcases/suites/${suite.id}/projects`, { project_ids: [...chosen] }));
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Dự án dùng bộ testcase này"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy}>
            Lưu
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <p className="small muted">
        Bộ “{suite.name}” thuộc dự án <strong>{suite.home_name}</strong>. Chọn thêm dự án dùng chung (ví dụ BE, FE, Mobile cùng một sản phẩm): testcase hiện và được gợi ý test lại ở mọi dự án đã chọn.
      </p>
      <ul className="docs-share-list">
        {projects
          .filter((p) => p.id !== suite.business_project_id)
          .map((p) => (
            <li key={p.id}>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={chosen.has(p.id)}
                  disabled={!p.can_edit && !chosen.has(p.id)}
                  onChange={(e) => {
                    const next = new Set(chosen);
                    if (e.target.checked) next.add(p.id);
                    else next.delete(p.id);
                    setChosen(next);
                  }}
                />
                {p.name}
                {!p.can_edit && <span className="small muted"> (không có quyền sửa)</span>}
              </label>
            </li>
          ))}
      </ul>
    </Modal>
  );
}

/** Import from a Google Sheet link or an .xlsx / .csv: read, check the columns, import. */
export function ImportModal({
  projectId,
  suites,
  initialUrl,
  onClose,
  onDone,
}: {
  projectId: number;
  suites: Suite[];
  initialUrl?: string;
  onClose: () => void;
  onDone: (results: ImportResult[]) => void;
}) {
  const [url, setUrl] = useState(initialUrl ?? "");
  const [read, setRead] = useState<ReadOut | null>(null);
  const [picked, setPicked] = useState<Record<string, { on: boolean; suite: string; header: number; mapping: Record<string, number> }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = (got: ReadOut) => {
    setRead(got);
    const byName = new Map(suites.map((s) => [s.name.toLowerCase(), s.id]));
    setPicked(
      Object.fromEntries(
        got.sheets.map((s) => [
          s.name,
          {
            on: s.total > 0 && "title" in s.mapping,
            suite: String(byName.get(s.name.toLowerCase()) ?? "new"),
            header: s.header_row,
            mapping: s.mapping,
          },
        ]),
      ),
    );
  };

  const readUrl = async () => {
    setBusy(true);
    setError(null);
    try {
      accept(await api.post<ReadOut>("/testcases/import/read", { business_project_id: projectId, url: url.trim() }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const readFile = async (f: File) => {
    setBusy(true);
    setError(null);
    try {
      accept(await api.postRaw<ReadOut>(`/testcases/import/read-file?business_project_id=${projectId}&filename=${encodeURIComponent(f.name)}`, f));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    if (!read) return;
    const chosen = read.sheets.filter((s) => picked[s.name]?.on);
    setBusy(true);
    setError(null);
    try {
      const got = await api.post<{ suites: ImportResult[] }>("/testcases/import", {
        business_project_id: projectId,
        source_url: read.source_url,
        sheets: chosen.map((s) => {
          const p = picked[s.name];
          return {
            name: s.name,
            suite_id: p.suite === "new" ? null : Number(p.suite),
            suite_name: s.name,
            header_row: p.header,
            mapping: p.mapping,
            rows: s.rows,
          };
        }),
      });
      onDone(got.suites);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  const ready = read && read.sheets.some((s) => picked[s.name]?.on && "title" in picked[s.name].mapping);
  return (
    <Modal
      title="Nhập testcase từ Google Sheet / Excel"
      onClose={onClose}
      busy={busy}
      width={880}
      footer={
        <>
          <button className="ghost" onClick={read ? () => setRead(null) : onClose} disabled={busy}>
            {read ? "Quay lại" : "Huỷ"}
          </button>
          {read && (
            <button className="primary" onClick={() => void run()} disabled={busy || !ready}>
              {busy ? "Đang nhập…" : "Nhập testcase"}
            </button>
          )}
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      {!read ? (
        <div className="adm-form">
          <label className="adm-field">
            <span className="small muted">Link Google Sheet (chia sẻ “Bất kỳ ai có đường liên kết đều xem được”)</span>
            <div className="tc-inline">
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" autoFocus />
              <button className="primary" onClick={() => void readUrl()} disabled={busy || !url.trim()}>
                {busy ? "Đang đọc…" : "Đọc sheet"}
              </button>
            </div>
          </label>
          <div className="small muted">hoặc tải file lên</div>
          <label className="tc-file">
            <Icon name="upload" size={16} />
            <span>Chọn file .xlsx / .csv (tối đa 10 MB)</span>
            <input
              type="file"
              accept=".xlsx,.csv"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void readFile(f);
                e.target.value = "";
              }}
            />
          </label>
          <p className="small muted">
            Mỗi tab thành một bộ testcase. CodePulse tự nhận cột theo tiêu đề (Mã, Chức năng, Tiêu đề, Các bước, Kết quả mong đợi, Ưu tiên, Jira…) và bỏ qua cột chạy test (Kết quả thực tế, Người test…). Nhập lại cùng sheet sẽ cập nhật testcase theo mã, không xoá testcase nào.
          </p>
        </div>
      ) : (
        <div className="tc-import">
          <p className="small muted">
            <strong>{read.title}</strong> — {read.sheets.length} tab. Kiểm tra cột được nhận, sửa nếu sai.
          </p>
          {read.sheets.map((s) => (
            <SheetPick
              key={s.name}
              sheet={s}
              fields={read.fields}
              suites={suites}
              value={picked[s.name]}
              onChange={(v) => setPicked({ ...picked, [s.name]: v })}
            />
          ))}
        </div>
      )}
    </Modal>
  );
}

function SheetPick({
  sheet,
  fields,
  suites,
  value,
  onChange,
}: {
  sheet: ReadOut["sheets"][number];
  fields: Record<string, string>;
  suites: Suite[];
  value: { on: boolean; suite: string; header: number; mapping: Record<string, number> };
  onChange: (v: { on: boolean; suite: string; header: number; mapping: Record<string, number> }) => void;
}) {
  const header = value.header >= 0 ? sheet.rows[value.header] ?? [] : [];
  const width = Math.max(0, ...sheet.rows.slice(0, 12).map((r) => r.length));
  const columns = Array.from({ length: width }, (_, i) => i);
  const byColumn = useMemo(() => Object.fromEntries(Object.entries(value.mapping).map(([f, i]) => [i, f])), [value.mapping]);
  const sample = sheet.rows.slice(value.header + 1, value.header + 4);
  const setColumn = (col: number, field: string) => {
    const mapping = Object.fromEntries(Object.entries(value.mapping).filter(([f, i]) => i !== col && f !== field));
    if (field) mapping[field] = col;
    onChange({ ...value, mapping });
  };
  const rows = Math.max(0, sheet.rows.length - value.header - 1);
  return (
    <section className={`tc-sheet${value.on ? "" : " off"}`}>
      <div className="tc-sheet-head">
        <label className="inline-check">
          <input type="checkbox" checked={value.on} onChange={(e) => onChange({ ...value, on: e.target.checked })} />
          <strong>{sheet.name}</strong>
          <span className="small muted"> · {rows} dòng</span>
        </label>
        {value.on && (
          <>
            <label className="filter">
              <span className="small muted">Vào bộ</span>
              <select value={value.suite} onChange={(e) => onChange({ ...value, suite: e.target.value })}>
                <option value="new">Bộ mới “{sheet.name}”</option>
                {suites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="filter">
              <span className="small muted">Dòng tiêu đề</span>
              <select value={value.header} onChange={(e) => onChange({ ...value, header: Number(e.target.value) })}>
                <option value={-1}>Không có</option>
                {sheet.rows.slice(0, 10).map((_, i) => (
                  <option key={i} value={i}>
                    Dòng {i + 1}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>
      {value.on && (
        <>
          {!("title" in value.mapping) && <Alert tone="warning">Chọn cột nào là “Tiêu đề” để nhập được tab này.</Alert>}
          <div className="table-wrap">
            <table className="tc-map">
              <thead>
                <tr>
                  {columns.map((i) => (
                    <th key={i}>
                      <div className="small muted tc-map-head">{header[i] || `Cột ${i + 1}`}</div>
                      <select value={byColumn[i] ?? ""} onChange={(e) => setColumn(i, e.target.value)} aria-label={`Cột ${header[i] || i + 1} là`}>
                        <option value="">— Bỏ qua —</option>
                        {Object.entries(fields).map(([f, label]) => (
                          <option key={f} value={f}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sample.map((r, ri) => (
                  <tr key={ri}>
                    {columns.map((i) => (
                      <td key={i} className={byColumn[i] ? undefined : "muted"}>
                        <div className="tc-map-cell">{r[i] ?? ""}</div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
