"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { FieldChanges } from "@/components/TestAudit";
import { ConfirmModal } from "@/components/TestRunParts";
import { CreateRunModal } from "@/components/TestRuns";
import { Alert, Card, Drawer, EmptyState, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import {
  type Baseline,
  type BaselineCompare,
  type BaselineDetail,
  type CaseMilestone,
  type CompareCase,
  PRIORITY_LABEL,
  type Priority,
  type Section,
  type Suite,
  TYPE_LABEL,
  type CaseType,
  countOf,
} from "@/lib/testcases";
import { errorText } from "@/lib/testruns";

/*
 * Baselines (docs/specs/30 §15.13 G9, API §15.16): frozen snapshots of a suite (or some of its
 * sections) to run a release against and compare with. Made and deleted by tests.manage holders,
 * never edited; compared with another baseline or with the cases as they are now.
 */

const creatorName = (b: Baseline) => b.created_by_name ?? (b.created_by && typeof b.created_by === "object" ? b.created_by.name : null);

export function BaselinesTab({ projectId, canManage, onOpenRun }: { projectId: number; canManage: boolean; onOpenRun: (id: number) => void }) {
  const suites = useApi<Suite[]>(`/testcases/suites?business_project_id=${projectId}`);
  const [suiteId, setSuiteId] = useState<number | null>(null);
  const list = useApi<Baseline[]>(`/testcases/baselines?business_project_id=${projectId}${suiteId ? `&suite_id=${suiteId}` : ""}`, { keepPreviousData: true });
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [compare, setCompare] = useState<{ a: number | null; b: string } | null>(null);
  const [runFrom, setRunFrom] = useState<Baseline | null>(null);
  const [deleting, setDeleting] = useState<Baseline | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const items = list.data ?? [];

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/testcases/baselines/${deleting.id}`);
      setMessage(`Đã xoá baseline “${deleting.name}”. Test run đã tạo từ nó vẫn giữ nguyên nội dung.`);
      if (open === deleting.id) setOpen(null);
      setDeleting(null);
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
        <label className="filter">
          <span className="small muted">Bộ testcase</span>
          <select value={suiteId ?? ""} onChange={(e) => setSuiteId(e.target.value ? Number(e.target.value) : null)} disabled={!suites.data}>
            <option value="">Mọi bộ</option>
            {(suites.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <span className="spacer" />
        <button type="button" className="sm" onClick={() => setCompare({ a: items[0]?.id ?? null, b: "current" })} disabled={!items.length}>
          <Icon name="scale" size={14} /> So sánh
        </button>
        {canManage && (
          <button type="button" className="sm primary" onClick={() => setCreating(true)}>
            <Icon name="plus" size={14} /> Tạo baseline
          </button>
        )}
      </div>
      {message && (
        <Alert tone="success" onClose={() => setMessage(null)}>
          {message}
        </Alert>
      )}
      <Card title={`Baseline${list.data ? ` (${items.length})` : ""}`} id="tc-baselines" subtitle="Bản chụp cố định của bộ testcase theo phiên bản phát hành: chạy test run trên đúng nội dung đó và so sánh qua các lần">
        {!list.data ? (
          list.error ? (
            <Alert tone="error">
              Lỗi: {list.error.message}{" "}
              <button type="button" className="link sm" onClick={() => void list.mutate()}>
                Thử lại
              </button>
            </Alert>
          ) : (
            <Loading rows={4} />
          )
        ) : !items.length ? (
          <EmptyState icon="pin" title={suiteId ? "Bộ này chưa có baseline" : "Dự án chưa có baseline"}>
            {canManage ? "Tạo baseline để chốt nội dung testcase của một bản phát hành (ví dụ “Release 2.4”)." : "Người có quyền Quản lý test tạo baseline."}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="tc-table">
              <thead>
                <tr>
                  <th>Baseline</th>
                  <th>Bộ</th>
                  <th>Milestone</th>
                  <th className="num">Testcase</th>
                  <th>Tạo</th>
                  <th className="tc-row-actions" aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {items.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <button type="button" className="link tc-row-title" onClick={() => setOpen(b.id)}>
                        {b.name}
                      </button>
                      {b.description && <div className="small muted bl-desc">{b.description}</div>}
                    </td>
                    <td className="small">{b.suite_name ?? `#${b.suite_id}`}</td>
                    <td className="small">{b.milestone_name ?? <span className="muted">—</span>}</td>
                    <td className="num">{b.case_count.toLocaleString("vi-VN")}</td>
                    <td className="small tc-nowrap">
                      {dateTimeMin(b.created_at)}
                      {creatorName(b) ? <div className="muted">{creatorName(b)}</div> : null}
                    </td>
                    <td className="tc-row-actions bl-actions">
                      <IconButton icon="scale" label={`So sánh ${b.name}`} className="tip-left" onClick={() => setCompare({ a: b.id, b: "current" })} />
                      {canManage && <IconButton icon="clipboardCheck" label={`Tạo test run từ ${b.name}`} className="tip-left" onClick={() => setRunFrom(b)} />}
                      {canManage && <IconButton icon="remove" tone="danger" label={`Xoá baseline ${b.name}`} className="tip-left" onClick={() => setDeleting(b)} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {creating && (
        <CreateBaselineModal
          projectId={projectId}
          suites={(suites.data ?? []).filter((s) => !s.archived)}
          initialSuite={suiteId}
          onClose={() => setCreating(false)}
          onCreated={(b) => {
            setCreating(false);
            setMessage(`Đã tạo baseline “${b.name}” với ${b.case_count} testcase.`);
            void list.mutate();
            setOpen(b.id);
          }}
        />
      )}
      {open !== null && (
        <BaselineDrawer
          id={open}
          canManage={canManage}
          onClose={() => setOpen(null)}
          onCompare={(id) => setCompare({ a: id, b: "current" })}
          onRun={(b) => setRunFrom(b)}
          onDelete={(b) => setDeleting(b)}
        />
      )}
      {compare && <CompareModal baselines={items} initial={compare} onClose={() => setCompare(null)} />}
      {runFrom && (
        <CreateRunModal
          projectId={projectId}
          initial={{ baseline_id: runFrom.id }}
          onClose={() => setRunFrom(null)}
          onCreated={(id) => {
            setRunFrom(null);
            onOpenRun(id);
          }}
        />
      )}
      {deleting && (
        <ConfirmModal
          title={`Xoá baseline “${deleting.name}”?`}
          confirmLabel="Xoá baseline"
          danger
          busy={busy}
          error={error}
          onClose={() => {
            setDeleting(null);
            setError(null);
          }}
          onConfirm={() => void remove()}
        >
          Bản chụp {deleting.case_count} testcase bị xoá, không khôi phục được. Test run đã tạo từ baseline này vẫn giữ nguyên nội dung.
        </ConfirmModal>
      )}
    </div>
  );
}

/** Name, description, milestone and (optionally) some sections of a suite. */
function CreateBaselineModal({
  projectId,
  suites,
  initialSuite,
  onClose,
  onCreated,
}: {
  projectId: number;
  suites: Suite[];
  initialSuite: number | null;
  onClose: () => void;
  onCreated: (b: Baseline) => void;
}) {
  const [suite, setSuite] = useState<number | null>(initialSuite ?? suites[0]?.id ?? null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [milestone, setMilestone] = useState("");
  const [some, setSome] = useState(false);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const milestones = useApi<CaseMilestone[]>(`/testcases/milestones?business_project_id=${projectId}`);
  const sections = useApi<Section[]>(some && suite ? `/testcases/sections?suite_id=${suite}` : null);
  const ready = !!suite && !!name.trim() && (!some || chosen.size > 0);

  const save = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      onCreated(
        await api.post<Baseline>("/testcases/baselines", {
          suite_id: suite,
          name: name.trim(),
          description: description.trim() || null,
          milestone_id: milestone ? Number(milestone) : null,
          ...(some ? { section_ids: [...chosen] } : {}),
        }),
      );
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Tạo baseline"
      onClose={onClose}
      busy={busy}
      width={560}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !ready}>
            {busy ? "Đang chụp…" : "Tạo baseline"}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label className="adm-field">
          <span className="small muted">Tên *</span>
          <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Release 2.4" autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Bộ testcase *</span>
          <select
            value={suite ?? ""}
            onChange={(e) => {
              setSuite(e.target.value ? Number(e.target.value) : null);
              setChosen(new Set());
            }}
          >
            {!suites.length && <option value="">Dự án chưa có bộ nào</option>}
            {suites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.total - s.obsolete})
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="adm-field">
          <span className="small muted">Milestone</span>
          <select value={milestone} onChange={(e) => setMilestone(e.target.value)} disabled={!milestones.data}>
            <option value="">Không gắn</option>
            {(milestones.data ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.key} · {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-check small">
          <input type="checkbox" checked={some} onChange={(e) => setSome(e.target.checked)} />
          Chỉ chụp một số thư mục
        </label>
        {some && (
          <div className="tr-tree" role="group" aria-label="Thư mục">
            {!sections.data ? (
              <Loading error={sections.error} rows={3} />
            ) : !sections.data.length ? (
              <p className="small muted">Bộ này chưa có thư mục: bỏ tick để chụp cả bộ.</p>
            ) : (
              sections.data.map((s) => (
                <label key={s.id} className="inline-check tr-tree-row" style={{ paddingLeft: s.depth * 18 }}>
                  <input
                    type="checkbox"
                    checked={chosen.has(s.id)}
                    onChange={(e) => {
                      const next = new Set(chosen);
                      if (e.target.checked) next.add(s.id);
                      else next.delete(s.id);
                      setChosen(next);
                    }}
                  />
                  <span>{s.name}</span>
                  <span className="muted small">({s.total})</span>
                </label>
              ))
            )}
          </div>
        )}
        <p className="small muted">Testcase “Lỗi thời” không vào baseline. Baseline không sửa được: chụp lại bằng một baseline mới.</p>
      </form>
    </Modal>
  );
}

function BaselineDrawer({
  id,
  canManage,
  onClose,
  onCompare,
  onRun,
  onDelete,
}: {
  id: number;
  canManage: boolean;
  onClose: () => void;
  onCompare: (id: number) => void;
  onRun: (b: Baseline) => void;
  onDelete: (b: Baseline) => void;
}) {
  const detail = useApi<BaselineDetail>(`/testcases/baselines/${id}`);
  const [q, setQ] = useState("");
  const b = detail.data;
  const cases = useMemo(() => {
    const term = q.trim().toLowerCase();
    const all = b?.cases ?? [];
    return term ? all.filter((c) => `${c.key} ${c.title} ${c.section ?? ""}`.toLowerCase().includes(term)) : all;
  }, [b, q]);
  return (
    <Drawer
      title={b ? `Baseline · ${b.name}` : "Baseline"}
      onClose={onClose}
      width={880}
      actions={
        b && (
          <>
            <button type="button" className="ghost sm" onClick={() => onCompare(b.id)}>
              <Icon name="scale" size={14} /> So sánh
            </button>
            {canManage && (
              <button type="button" className="ghost sm" onClick={() => onDelete(b)}>
                Xoá
              </button>
            )}
            {canManage && (
              <button type="button" className="sm primary ink" onClick={() => onRun(b)}>
                <Icon name="clipboardCheck" size={14} /> Tạo test run
              </button>
            )}
          </>
        )
      }
    >
      {!b ? (
        <Loading error={detail.error} rows={8} />
      ) : (
        <div className="tc-view">
          <div className="tc-view-pills">
            <Pill icon="folder">{b.suite_name ?? `Bộ #${b.suite_id}`}</Pill>
            {b.milestone_name && <Pill icon="flag">{b.milestone_name}</Pill>}
            <Pill>{b.case_count} testcase</Pill>
            <span className="small muted">
              Chụp {dateTimeMin(b.created_at)}
              {creatorName(b) ? ` bởi ${creatorName(b)}` : ""}
            </span>
          </div>
          {b.description && <p className="tc-lines">{b.description}</p>}
          <label className="filter search">
            <span className="small muted">Tìm trong baseline</span>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="C123, tiêu đề, thư mục…" />
          </label>
          {!cases.length ? (
            <p className="small muted">{q ? "Không có testcase nào khớp." : "Baseline không có testcase nào."}</p>
          ) : (
            <div className="table-wrap">
              <table className="tc-table">
                <thead>
                  <tr>
                    <th>Mã</th>
                    <th>Testcase</th>
                    <th>Ưu tiên</th>
                    <th>Type</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((c) => (
                    <tr key={c.id}>
                      <td className="num tc-code-cell">
                        <span className="tc-key">{c.key}</span>
                      </td>
                      <td>
                        {c.title}
                        <div className="small muted">
                          {c.section ? (
                            <span className="tc-row-section">
                              <Icon name="folder" size={12} /> {c.section}
                            </span>
                          ) : (
                            "Gốc của bộ"
                          )}
                          {c.case_id == null && <span> · testcase đã xoá</span>}
                        </div>
                      </td>
                      <td className="small">{c.priority ? (PRIORITY_LABEL[c.priority as Priority] ?? c.priority) : "—"}</td>
                      <td className="small tc-nowrap">{c.type ? (TYPE_LABEL[c.type as CaseType] ?? c.type) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}

const caseOf = (c: CompareCase | string): CompareCase => (typeof c === "string" ? { key: c } : c);

/** Two baselines, or a baseline and the cases as they are now: added / removed / changed. */
function CompareModal({ baselines, initial, onClose }: { baselines: Baseline[]; initial: { a: number | null; b: string }; onClose: () => void }) {
  const [a, setA] = useState<number | null>(initial.a);
  const [b, setB] = useState<string>(initial.b);
  const left = baselines.find((x) => x.id === a);
  // The other side: "current" or a baseline of the same suite.
  const others = baselines.filter((x) => x.id !== a && (!left || x.suite_id === left.suite_id));
  const ok = a !== null && (b === "current" || others.some((x) => String(x.id) === b));
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [a, b]);
  const cmp = useApi<BaselineCompare>(ok ? `/testcases/baselines/compare?a=${a}&b=${b}&offset=${offset}&limit=200` : null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const d = cmp.data;
  return (
    <Modal title="So sánh baseline" onClose={onClose} width={920} footer={<button type="button" className="ghost" onClick={onClose}>Đóng</button>}>
      <div className="bl-pick">
        <label className="adm-field">
          <span className="small muted">Baseline</span>
          <select value={a ?? ""} onChange={(e) => setA(e.target.value ? Number(e.target.value) : null)}>
            {baselines.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name} · {x.suite_name ?? `bộ #${x.suite_id}`}
              </option>
            ))}
          </select>
        </label>
        <Icon name="chevronRight" size={16} />
        <label className="adm-field">
          <span className="small muted">So với</span>
          <select value={b} onChange={(e) => setB(e.target.value)}>
            <option value="current">Hiện tại (testcase đang có)</option>
            {others.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!ok ? (
        <p className="small muted">Chọn hai bên để so sánh (baseline cùng bộ, hoặc “Hiện tại”).</p>
      ) : !d ? (
        <Loading error={cmp.error} rows={5} />
      ) : (
        <div className="bl-compare">
          <div className="tc-view-pills">
            <Pill tone="good">+{d.total_added ?? d.added.length} thêm</Pill>
            <Pill tone="bad">−{d.total_removed ?? d.removed.length} bỏ</Pill>
            <Pill tone="warn">{d.total_changed ?? d.changed.length} đổi</Pill>
            <Pill>{countOf(d.same)} giữ nguyên</Pill>
          </div>
          {(() => {
            const limit = d.limit ?? 200;
            const most = Math.max(d.total_added ?? 0, d.total_removed ?? 0, d.total_changed ?? 0);
            if (most <= limit) return null;
            return (
              <div className="tc-inline small muted">
                <span>
                  Trang {Math.floor(offset / limit) + 1}/{Math.ceil(most / limit)} (mỗi trang {limit} testcase mỗi loại)
                </span>
                <button type="button" className="ghost sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
                  Trước
                </button>
                <button type="button" className="ghost sm" disabled={offset + limit >= most} onClick={() => setOffset(offset + limit)}>
                  Sau
                </button>
              </div>
            );
          })()}
          {!d.added.length && !d.removed.length && !d.changed.length ? (
            <EmptyState icon="checkCircle" tone="good" title="Hai bên giống nhau" />
          ) : (
            <>
              {d.added.length > 0 && <CaseList title="Thêm" tone="add" items={d.added.map(caseOf)} />}
              {d.removed.length > 0 && <CaseList title="Bỏ" tone="del" items={d.removed.map(caseOf)} />}
              {d.changed.length > 0 && (
                <section>
                  <h3 className="tc-h3">Đổi ({d.total_changed ?? d.changed.length})</h3>
                  <ul className="bl-changed">
                    {d.changed.map((c) => {
                      const isOpen = openKey === c.key;
                      return (
                        <li key={c.key}>
                          <button type="button" className="link bl-changed-head" aria-expanded={isOpen} onClick={() => setOpenKey(isOpen ? null : c.key)}>
                            <Icon name={isOpen ? "chevronDown" : "chevronRight"} size={12} />
                            <span className="tc-key">{c.key}</span> {c.title}
                            <span className="small muted"> · {c.fields.length} trường</span>
                          </button>
                          {isOpen && <FieldChanges changes={c.fields} />}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

function CaseList({ title, tone, items }: { title: string; tone: "add" | "del"; items: CompareCase[] }) {
  return (
    <section>
      <h3 className="tc-h3">
        {title} ({items.length})
      </h3>
      <ul className={`bl-cases ${tone}`}>
        {items.map((c) => (
          <li key={c.key}>
            <span className="tc-key">{c.key}</span> {c.title ?? ""}
            {c.section ? <span className="small muted"> · {c.section}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
