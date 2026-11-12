"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { DiffText, textDiff } from "@/components/TestcaseBulk";
import { Alert, Card, EmptyState, Loading, Skeleton } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import {
  AUDIT_ACTION_LABEL,
  AUDIT_ENTITY_LABEL,
  type AuditChange,
  type AuditFacets,
  type AuditRow,
  REPLACE_FIELD_LABEL,
  facetOption,
  valueText,
} from "@/lib/testcases";
import { errorText } from "@/lib/testruns";

/*
 * "Nhật ký thay đổi" (docs/specs/30 §15.14 G12, API §15.16): who changed what in the dự án's tests,
 * with the before / after of each changed column, filters, "Tải thêm" (before_id) and an export.
 * For tests.manage holders.
 */

const PAGE = 50;

/** Names of the columns a change can touch. */
export const FIELD_LABEL: Record<string, string> = {
  ...REPLACE_FIELD_LABEL,
  name: "Tên",
  description: "Mô tả",
  status: "Trạng thái",
  priority: "Ưu tiên",
  type: "Type",
  kind: "Cách chạy",
  template: "Template",
  steps_list: "Các bước (bảng)",
  bdd: "Kịch bản BDD",
  charter: "Sứ mệnh",
  params: "Tham số",
  estimate_s: "Ước tính",
  estimate: "Ước tính",
  milestone_id: "Milestone",
  section_id: "Thư mục",
  section: "Thư mục",
  suite_id: "Bộ",
  tags: "Nhãn",
  jira_keys: "Jira",
  code_paths: "Vùng code",
  automation: "Test tự động",
  automation_id: "Automation ID",
  custom: "Trường tuỳ biến",
  position: "Vị trí",
  archived: "Lưu trữ",
  completed_at: "Đóng lúc",
  assigned_to: "Giao cho",
};

const entityText = (e: string | null) => (e ? (AUDIT_ENTITY_LABEL[e] ?? e) : "—");
const actionText = (a: string) => AUDIT_ACTION_LABEL[a] ?? a;

/** Before / after of each changed field; texts get a word diff. */
export function FieldChanges({ changes }: { changes: AuditChange[] }) {
  if (!changes.length) return <p className="small muted">Không có cột nào đổi.</p>;
  return (
    <div className="table-wrap">
      <table className="au-diff">
        <thead>
          <tr>
            <th>Trường</th>
            <th>Trước</th>
            <th>Sau</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c, i) => {
            const a = valueText(c.before);
            const b = valueText(c.after);
            const both = typeof c.before === "string" && typeof c.after === "string";
            const d = both ? textDiff(a, b) : null;
            return (
              <tr key={`${c.field}-${i}`}>
                <th scope="row" className="small">
                  {FIELD_LABEL[c.field] ?? c.field}
                </th>
                <td>{d ? <DiffText segs={d.before} mark="del" /> : <div className="tc-lines au-val">{a}</div>}</td>
                <td>{d ? <DiffText segs={d.after} mark="ins" /> : <div className="tc-lines au-val">{b}</div>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The list answer: the rows, or {items | rows} around them. */
function rowsOf(got: unknown): AuditRow[] {
  if (Array.isArray(got)) return got as AuditRow[];
  if (got && typeof got === "object") {
    const o = got as { items?: AuditRow[]; rows?: AuditRow[] };
    return o.items ?? o.rows ?? [];
  }
  return [];
}

type Filters = { actor_id: string; entity: string; action: string; q: string; from: string; to: string };
const NO_FILTERS: Filters = { actor_id: "", entity: "", action: "", q: "", from: "", to: "" };

export function AuditView({ projectId }: { projectId: number }) {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === term.trim() ? f : { ...f, q: term.trim() })), 350);
    return () => clearTimeout(t);
  }, [term]);
  const facets = useApi<AuditFacets>(`/testcases/audit/facets?business_project_id=${projectId}`);
  const params = { business_project_id: projectId, ...filters };
  const first = useApi<unknown>(`/testcases/audit${qs({ ...params, limit: PAGE })}`, { keepPreviousData: true });
  const key = JSON.stringify(params);
  const [more, setMore] = useState<{ key: string; rows: AuditRow[]; end: boolean }>({ key, rows: [], end: false });
  const [loading, setLoading] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set());

  const extra = more.key === key ? more : { key, rows: [], end: false };
  const firstRows = useMemo(() => rowsOf(first.data), [first.data]);
  const rows = useMemo(() => [...firstRows, ...extra.rows], [firstRows, extra.rows]);
  const end = extra.rows.length ? extra.end : firstRows.length < PAGE;

  const loadMore = async () => {
    const last = rows[rows.length - 1];
    if (!last) return;
    setLoading(true);
    setMoreError(null);
    try {
      const got = rowsOf(await api.get<unknown>(`/testcases/audit${qs({ ...params, limit: PAGE, before_id: last.id })}`));
      setMore({ key, rows: [...extra.rows, ...got], end: got.length < PAGE });
    } catch (e) {
      setMoreError(errorText(e));
    } finally {
      setLoading(false);
    }
  };

  const exportHref = (format: "xlsx" | "csv") => `/api/v1/testcases/audit/export${qs({ ...params, format })}`;
  const filtering = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS) || term.trim() !== "";
  const toggle = (id: number) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const select = (label: string, k: keyof Filters, list: AuditFacets[keyof AuditFacets] | undefined, all: string, labelOf?: (v: string) => string) => (
    <label className="filter">
      <span className="small muted">{label}</span>
      <select value={filters[k]} onChange={(e) => setFilters({ ...filters, [k]: e.target.value })} disabled={!facets.data && !facets.error}>
        <option value="">{all}</option>
        {(list ?? []).map((f) => {
          const [v, l] = facetOption(f);
          const named = typeof f === "object" && !!(f.label || f.name);
          return (
            <option key={v} value={v}>
              {labelOf && !named ? l.replace(v, labelOf(v)) : l}
            </option>
          );
        })}
      </select>
    </label>
  );

  return (
    <div className="tr-tab">
      <Card
        title="Nhật ký thay đổi"
        id="tc-audit"
        subtitle="Mọi lần tạo / sửa / xoá bộ, thư mục, testcase, run, plan, milestone, cấu hình, trường, trạng thái, baseline… của dự án; giữ ít nhất 1 năm"
      >
        <div className="toolbar filters">
          <label className="filter search">
            <span className="small muted">Tìm</span>
            <input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="C123, tên đối tượng, giá trị…" />
          </label>
          {select("Người", "actor_id", facets.data?.actors, "Mọi người")}
          {select("Loại đối tượng", "entity", facets.data?.entities, "Mọi loại", entityText)}
          {select("Hành động", "action", facets.data?.actions, "Mọi hành động", actionText)}
          <label className="filter">
            <span className="small muted">Từ ngày</span>
            <input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
          </label>
          <label className="filter">
            <span className="small muted">Đến ngày</span>
            <input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
          </label>
          {filtering && (
            <button
              type="button"
              className="ghost sm"
              onClick={() => {
                setFilters(NO_FILTERS);
                setTerm("");
              }}
            >
              Xoá lọc
            </button>
          )}
          <span className="spacer" />
          <a className="button sm" href={exportHref("xlsx")} download>
            <Icon name="download" size={14} /> Excel
          </a>
          <a className="button sm" href={exportHref("csv")} download>
            <Icon name="download" size={14} /> CSV
          </a>
        </div>
        {facets.error && <Alert tone="warning">Không tải được danh sách bộ lọc ({facets.error.message}).</Alert>}
        {!first.data ? (
          first.error ? (
            <Alert tone="error">
              Lỗi: {first.error.message}{" "}
              <button type="button" className="link sm" onClick={() => void first.mutate()}>
                Thử lại
              </button>
            </Alert>
          ) : (
            <Loading rows={8} />
          )
        ) : !rows.length ? (
          <EmptyState icon="history" title={filtering ? "Không có thay đổi nào khớp bộ lọc" : "Chưa có thay đổi nào được ghi"} />
        ) : (
          <>
            <div className="table-wrap">
              <table className="tc-table au-table">
                <thead>
                  <tr>
                    <th className="au-toggle" aria-label="Mở chi tiết" />
                    <th>Thời gian</th>
                    <th>Người</th>
                    <th>Hành động</th>
                    <th>Đối tượng</th>
                    <th>Thay đổi</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const isOpen = open.has(r.id);
                    const changes = r.changes ?? [];
                    const fields = changes.map((c) => FIELD_LABEL[c.field] ?? c.field);
                    return (
                      <Fragment key={r.id}>
                        <tr className={isOpen ? "au-open" : undefined}>
                          <td className="au-toggle">
                            <button
                              type="button"
                              className="icon-btn"
                              aria-expanded={isOpen}
                              aria-controls={`au-row-${r.id}`}
                              aria-label={isOpen ? "Ẩn chi tiết" : "Xem trước / sau"}
                              data-tip={isOpen ? "Ẩn chi tiết" : "Xem trước / sau"}
                              onClick={() => toggle(r.id)}
                            >
                              <Icon name={isOpen ? "chevronDown" : "chevronRight"} size={14} />
                            </button>
                          </td>
                          <td className="small tc-nowrap">{dateTimeMin(r.at)}</td>
                          <td className="small">{r.actor_name ?? <span className="muted">Hệ thống</span>}</td>
                          <td>
                            <span className={`pill au-${actionText(r.action) === "Xoá" ? "del" : actionText(r.action) === "Tạo" ? "add" : "upd"}`}>{actionText(r.action)}</span>
                          </td>
                          <td>
                            <div className="small muted">{entityText(r.entity)}</div>
                            <div>{r.target ?? (r.entity_id != null ? `#${r.entity_id}` : "—")}</div>
                          </td>
                          <td className="small">
                            {fields.length ? (
                              <>
                                {fields.slice(0, 4).join(", ")}
                                {fields.length > 4 ? ` +${fields.length - 4}` : ""}
                              </>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr id={`au-row-${r.id}`} className="au-detail">
                            <td />
                            <td colSpan={5}>
                              <FieldChanges changes={changes} />
                              {r.details != null && r.details !== "" && (
                                <details className="small au-extra">
                                  <summary>Chi tiết thêm</summary>
                                  <pre>{valueText(r.details)}</pre>
                                </details>
                              )}
                              {r.request_id && (
                                <p className="small muted">
                                  Mã yêu cầu <code>{r.request_id}</code>
                                </p>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {moreError && (
              <Alert tone="error" onClose={() => setMoreError(null)}>
                {moreError}
              </Alert>
            )}
            <div className="tc-foot small muted">
              {rows.length.toLocaleString("vi-VN")} dòng{end ? " · hết" : ""}{" "}
              {!end &&
                (loading ? (
                  <Skeleton width={80} />
                ) : (
                  <button type="button" className="link sm" onClick={() => void loadMore()}>
                    Tải thêm
                  </button>
                ))}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
