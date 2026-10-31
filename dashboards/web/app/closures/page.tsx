"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import useSWRInfinite from "swr/infinite";

import { CLOSED_AS, shortShas } from "@/components/FindingClose";
import { Icon, IconButton } from "@/components/icons";
import { Card, EmptyState, Loading, Message, PageHeader, Pill, Segmented, SeverityBadge } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, date } from "@/lib/format";
import type { ClosureFacets, ClosureListItem } from "@/lib/types";

/**
 * "Đóng finding": critical/high findings closed because a later change fixed them.
 * - Chờ duyệt (managers, admins): requests of their teams' members waiting for a decision —
 *   "Chạy review" checks the commits (closes the finding if they fix it), "Từ chối" with a note.
 * - Lịch sử: every request and closing this person may see (their own work, their teams', all
 *   for an admin), filtered by status, way, severity, dự án, repo, member, period and text.
 * Each row opens to the whole story: the request, the decision, the result.
 */

interface Page {
  items: ClosureListItem[];
  next_cursor: string | null;
}

type Tab = "todo" | "history";

const STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  pending: { label: "Chờ duyệt", tone: "info" },
  checking: { label: "Đang review", tone: "info" },
  proposed: { label: "Đề xuất chấp nhận rủi ro", tone: "warn" },
  closed: { label: "Đã đóng", tone: "good" },
  rejected: { label: "Chưa đủ điều kiện", tone: "bad" },
  failed: { label: "Chưa kiểm được", tone: "warn" },
  cancelled: { label: "Đã rút" },
  reopened: { label: "Mở lại", tone: "warn" },
};
const KIND: Record<string, string> = {
  request: "Thành viên yêu cầu",
  auto: "Tự động sau review",
  admin: "Admin đóng tay",
  reopen: "Admin mở lại",
};

const EMPTY = { status: "", kind: "", severity: "", bp: "", repo: "", author: "", from: "", to: "", q: "" };
type Filters = typeof EMPTY;

export default function ClosuresPage() {
  const facets = useApi<ClosureFacets>("/close-requests/facets");
  const [tab, setTab] = useState<Tab | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Start where there is something to do: a decider with requests waiting lands on them.
  useEffect(() => {
    if (tab === null && facets.data) setTab(facets.data.can_decide && facets.data.todo > 0 ? "todo" : "history");
  }, [facets.data, tab]);
  // Typing in the search box filters after a short pause.
  useEffect(() => {
    const id = setTimeout(() => setFilters((f) => (f.q === query.trim() ? f : { ...f, q: query.trim() })), 350);
    return () => clearTimeout(id);
  }, [query]);

  const view = tab ?? "history";
  const base =
    tab === null
      ? null
      : `/close-requests${qs({
          view,
          kind: filters.kind,
          severity: filters.severity,
          business_project_id: filters.bp,
          project_id: filters.repo,
          author_id: filters.author,
          from: filters.from,
          to: filters.to,
          q: filters.q,
        })}${statusParams(filters.status)}`;
  const pages = useSWRInfinite<Page, ApiError>(
    (index, previous) => {
      if (!base) return null;
      if (index === 0) return base;
      return previous?.next_cursor ? `${base}${base.includes("?") ? "&" : "?"}cursor=${encodeURIComponent(previous.next_cursor)}` : null;
    },
    (path: string) => api.get<Page>(path),
    {
      keepPreviousData: true,
      shouldRetryOnError: (e: ApiError) => !e.status || e.status >= 500,
      // Follow checks until they end.
      refreshInterval: (loaded) => (loaded?.some((p) => p.items.some((c) => c.status === "checking")) ? 4000 : 0),
    },
  );
  const items = pages.data?.flatMap((p) => p.items) ?? [];
  const more = !!pages.data?.at(-1)?.next_cursor;
  const loadingMore = pages.isValidating && pages.size > (pages.data?.length ?? 0);
  const filtered = Object.values(filters).some(Boolean);
  const set = (key: keyof Filters, value: string) =>
    setFilters((f) => ({ ...f, [key]: value, ...(key === "bp" ? { repo: "" } : {}) }));
  const refresh = async () => {
    await Promise.all([pages.mutate(), facets.mutate()]);
  };

  const f = facets.data;
  const repos = f ? (filters.bp ? f.repos.filter((r) => String(r.business_project_id) === filters.bp) : f.repos) : [];

  return (
    <>
      <PageHeader
        eyebrow="Findings"
        title="Đóng finding"
        subtitle="Critical/high đã được sửa ở commit sau: thành viên gửi yêu cầu kèm commit, manager hoặc admin chạy review để kiểm; đạt thì đóng (vẫn giữ mức và vẫn tính điểm), không đạt thì ghi lý do. Finding sai thì dùng Khiếu nại."
        actions={
          f?.can_decide ? (
            <Segmented
              label="Xem"
              value={view}
              onChange={(v) => {
                setTab(v);
                setOpen(null);
              }}
              options={[
                { value: "todo", label: `Chờ duyệt${f.todo ? ` · ${f.todo}` : ""}` },
                { value: "history", label: "Lịch sử" },
              ]}
            />
          ) : undefined
        }
      />
      <Message text={message} onClose={() => setMessage(null)} />

      <ol className="closure-flow small muted" aria-label="Các bước">
        <li>
          <strong>1. Thành viên</strong> mở finding critical/high của mình ở trang review, bấm “Yêu cầu đóng”, dán mã hoặc
          link các commit đã sửa.
        </li>
        <li>
          <strong>2. Manager hoặc admin</strong> xem yêu cầu ở tab “Chờ duyệt”, bấm “Chạy review” (hoặc từ chối kèm lý do).
        </li>
        <li>
          <strong>3. Kết quả</strong>: đạt thì finding được đóng; chưa đạt thì lý do hiện ở đây và dưới finding.
        </li>
      </ol>

      {view === "history" && (
        <div className="toolbar filters">
          <label className="filter search">
            <span className="small muted">Tìm</span>
            <input
              type="search"
              value={query}
              placeholder="Tiêu đề, file, mã commit"
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Tìm theo tiêu đề finding, file hoặc mã commit"
            />
          </label>
          <label className="filter">
            <span className="small muted">Trạng thái</span>
            <select value={filters.status} onChange={(e) => set("status", e.target.value)}>
              <option value="">Mọi trạng thái</option>
              <option value="waiting">Đang chờ (chờ duyệt, đang review, chờ xác nhận)</option>
              <option value="closed">Đã đóng</option>
              <option value="rejected">Chưa đủ điều kiện / bị từ chối</option>
              <option value="failed">Chưa kiểm được</option>
              <option value="cancelled">Đã rút</option>
              <option value="reopened">Mở lại</option>
            </select>
          </label>
          <label className="filter">
            <span className="small muted">Cách đóng</span>
            <select value={filters.kind} onChange={(e) => set("kind", e.target.value)}>
              <option value="">Mọi cách</option>
              {Object.entries(KIND).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="filter">
            <span className="small muted">Mức</span>
            <select value={filters.severity} onChange={(e) => set("severity", e.target.value)}>
              <option value="">Critical và high</option>
              <option value="critical">Chỉ critical</option>
              <option value="high">Chỉ high</option>
            </select>
          </label>
          {f && f.business_projects.length > 1 && (
            <label className="filter">
              <span className="small muted">Dự án</span>
              <select value={filters.bp} onChange={(e) => set("bp", e.target.value)}>
                <option value="">Mọi dự án</option>
                {f.business_projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {repos.length > 1 && (
            <label className="filter">
              <span className="small muted">Repo</span>
              <select value={filters.repo} onChange={(e) => set("repo", e.target.value)}>
                <option value="">Mọi repo</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.path}
                  </option>
                ))}
              </select>
            </label>
          )}
          {f && f.authors.length > 1 && (
            <label className="filter">
              <span className="small muted">Thành viên</span>
              <select value={filters.author} onChange={(e) => set("author", e.target.value)}>
                <option value="">Mọi thành viên</option>
                {f.authors.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name ?? `#${a.id}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="filter">
            <span className="small muted">Từ ngày</span>
            <input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set("from", e.target.value)} />
          </label>
          <label className="filter">
            <span className="small muted">Đến ngày</span>
            <input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set("to", e.target.value)} />
          </label>
          {filtered && (
            <button
              className="ghost"
              onClick={() => {
                setFilters(EMPTY);
                setQuery("");
              }}
            >
              Bỏ lọc
            </button>
          )}
        </div>
      )}

      {!pages.data ? (
        <Loading error={pages.error ?? facets.error} rows={5} />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState icon="checkCircle" tone={view === "todo" ? "good" : undefined} title={emptyTitle(view, filtered)}>
            {view === "history" && !filtered
              ? "Thành viên yêu cầu đóng finding critical/high của mình ở trang review, kèm các commit đã sửa."
              : undefined}
          </EmptyState>
        </Card>
      ) : (
        <Card flush>
          <div className="table-wrap">
            <table className="closures">
              <thead>
                <tr>
                  <th>Finding</th>
                  <th>Thành viên</th>
                  <th>Cách đóng</th>
                  <th>Trạng thái</th>
                  <th aria-label="Chi tiết" />
                </tr>
              </thead>
              <tbody>
                {items.map((c) => {
                  const st = STATUS[c.status] ?? { label: c.status };
                  const expanded = open === c.id;
                  return (
                    <Fragment key={c.id}>
                      <tr className={expanded ? "open" : undefined}>
                        <td style={{ whiteSpace: "normal", minWidth: 260 }}>
                          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                            <SeverityBadge severity={c.finding.severity} />
                            {c.can_open_review ? (
                              <Link href={`/reviews/${c.finding_review_id}`}>{c.finding.title ?? `Finding #${c.finding.id}`}</Link>
                            ) : (
                              <strong>{c.finding.title ?? `Finding #${c.finding.id}`}</strong>
                            )}
                          </div>
                          <div className="muted small">
                            {c.repo?.path ?? "—"}
                            {c.finding.file_path && (
                              <>
                                {" · "}
                                <code>
                                  {c.finding.file_path}
                                  {c.finding.line_start ? `:${c.finding.line_start}` : ""}
                                </code>
                              </>
                            )}
                          </div>
                        </td>
                        <td>{c.author?.name ?? "—"}</td>
                        <td>
                          {KIND[c.kind] ?? c.kind}
                          {c.shas.length > 0 && (
                            <div className="small">
                              <code>{shortShas(c.shas)}</code>
                            </div>
                          )}
                          {c.argument && <div className="small muted">có phản biện</div>}
                        </td>
                        <td>
                          <Pill tone={st.tone}>
                            {c.status === "checking" && <span className="spinner inline" aria-hidden="true" />} {st.label}
                            {c.status === "closed" && c.closed_as ? ` · ${CLOSED_AS[c.closed_as]}` : ""}
                          </Pill>
                          <div className="muted small">{date(c.decided_at ?? c.created_at)}</div>
                        </td>
                        <td style={{ width: 44 }}>
                          <IconButton
                            icon={expanded ? "chevronUp" : "chevronDown"}
                            label={expanded ? "Thu gọn" : "Xem chi tiết"}
                            aria-expanded={expanded}
                            onClick={() => setOpen(expanded ? null : c.id)}
                          />
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="closure-detail">
                          <td colSpan={5}>
                            <Detail closure={c} onDone={refresh} onMessage={setMessage} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {more && (
            <div style={{ padding: "12px 18px" }}>
              <button onClick={() => void pages.setSize((pages.data?.length ?? 0) + 1)} disabled={loadingMore}>
                {loadingMore ? "Đang tải…" : "Tải thêm"}
              </button>
            </div>
          )}
        </Card>
      )}
    </>
  );
}

/** "waiting" groups the two in-flight states. */
function statusParams(status: string): string {
  if (!status) return "";
  const list = status === "waiting" ? ["pending", "checking", "proposed"] : [status];
  return list.map((s) => `&status=${s}`).join("");
}

function emptyTitle(view: Tab, filtered: boolean): string {
  if (view === "todo") return "Không có yêu cầu nào chờ bạn duyệt";
  return filtered ? "Không có mục nào khớp bộ lọc" : "Chưa có finding nào được yêu cầu đóng hay được đóng";
}

/** The story of one row: request, decision, result; and the decider's buttons. */
function Detail({
  closure: c,
  onDone,
  onMessage,
}: {
  closure: ClosureListItem;
  onDone: () => Promise<void>;
  onMessage: (text: string) => void;
}) {
  const [refusing, setRefusing] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const decidable = c.can_decide && (c.status === "pending" || c.status === "failed");
  const confirmable = c.can_decide && c.status === "proposed";
  const refusedByPerson = c.status === "rejected" && (c.reason ?? "").startsWith("Từ chối:");

  async function act(path: string, body: unknown, done: string) {
    setBusy(true);
    setError(null);
    try {
      await api.post(path, body);
      onMessage(done);
      await onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const st = STATUS[c.status] ?? { label: c.status };
  return (
    <div className="closure-story">
      <div className="closure-finding small">
        <span className="muted">{CATEGORY_LABEL[c.finding.category] ?? c.finding.category}</span>
        {c.business_project && <span className="muted"> · dự án {c.business_project.name}</span>}
        {c.finding.message && <p style={{ margin: "4px 0 0" }}>{c.finding.message}</p>}
      </div>
      <ol className="closure-timeline">
        <li>
          <span className="step-dot" aria-hidden="true">
            <Icon name={c.kind === "auto" ? "sync" : "user"} size={14} />
          </span>
          <div>
            <div className="step-title">{firstStep(c)}</div>
            <div className="muted small">{date(c.created_at)}</div>
            {c.shas.length > 0 && (
              <div className="small">
                Commit: <code>{c.shas.join(", ")}</code>
              </div>
            )}
            {c.kind === "auto" && c.review_id && (
              <div className="small">
                Từ <Link href={`/reviews/${c.review_id}`}>review #{c.review_id}</Link>
              </div>
            )}
            {c.argument && (
              <blockquote className="closure-argument small">
                <strong>Phản biện:</strong> {c.argument}
              </blockquote>
            )}
            {c.note && <div className="small">Ghi chú: {c.note}</div>}
          </div>
        </li>
        {c.kind === "request" && (
          <li>
            <span className="step-dot" aria-hidden="true">
              <Icon name="scale" size={14} />
            </span>
            <div>
              <div className="step-title">
                {c.status === "pending"
                  ? "Chờ manager hoặc admin duyệt"
                  : c.status === "cancelled"
                    ? "Người gửi đã rút yêu cầu"
                    : refusedByPerson
                      ? `${c.decided_by_name ?? "Người duyệt"} từ chối, không chạy review`
                      : `${c.decided_by_name ?? "Người duyệt"} chạy review ${[c.shas.length ? "các commit" : "", c.argument ? "phản biện" : ""].filter(Boolean).join(" và ")}`}
              </div>
              {c.decided_at && c.status !== "pending" && <div className="muted small">{date(c.decided_at)}</div>}
            </div>
          </li>
        )}
        <li>
          <span className="step-dot" aria-hidden="true">
            <Icon name={c.status === "closed" ? "checkCircle" : c.status === "checking" ? "sync" : "info"} size={14} />
          </span>
          <div>
            <div className="step-title">
              Kết quả:{" "}
              <Pill tone={st.tone}>
                {st.label}
                {c.status === "closed" && c.closed_as ? ` · ${CLOSED_AS[c.closed_as]}` : ""}
              </Pill>
            </div>
            {c.status === "checking" && <div className="muted small">Đang lấy diff và hỏi model, thường dưới 1 phút…</div>}
            {c.status === "proposed" && (
              <div className="small">
                Model thấy phản biện được code hiện tại chứng minh. Finding chỉ được đóng (“Chấp nhận rủi ro”) khi manager
                hoặc admin xác nhận: hãy mở file bằng chứng trên GitLab và chắc đó là cấu hình đang chạy thật.
              </div>
            )}
            {c.reason && <div className="small">{c.reason}</div>}
            {c.evidence && (
              <div className="small">
                Chỗ sửa: <code>{c.evidence}</code>
              </div>
            )}
            {c.cost_usd != null && <div className="muted small">Chi phí kiểm: ${c.cost_usd.toFixed(4)}</div>}
          </div>
        </li>
      </ol>

      {(decidable || confirmable || c.can_cancel) && (
        <div className="toolbar" style={{ marginTop: 12 }}>
          {confirmable && (
            <button
              className="primary"
              disabled={busy}
              onClick={() => act(`/close-requests/${c.id}/confirm`, undefined, "Đã đóng finding: chấp nhận rủi ro.")}
            >
              Xác nhận chấp nhận rủi ro
            </button>
          )}
          {decidable && (
            <button
              className="primary"
              disabled={busy}
              onClick={() => act(`/close-requests/${c.id}/run`, undefined, "Đã gửi đi review; kết quả hiện ở đây sau ít phút.")}
            >
              {c.status === "failed" ? "Chạy review lại" : "Chạy review"}
            </button>
          )}
          {(decidable || confirmable) && !refusing && (
            <button disabled={busy} onClick={() => setRefusing(true)}>
              Từ chối
            </button>
          )}
          {c.can_cancel && (
            <button className="ghost" disabled={busy} onClick={() => act(`/close-requests/${c.id}/cancel`, undefined, "Đã rút yêu cầu.")}>
              Rút yêu cầu
            </button>
          )}
        </div>
      )}
      {refusing && (
        <form
          style={{ display: "grid", gap: 8, maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            void act(`/close-requests/${c.id}/reject`, { note: note.trim() }, "Đã từ chối yêu cầu.");
          }}
        >
          <label htmlFor={`refuse-${c.id}`} style={{ fontWeight: 600 }}>
            Lý do từ chối (người gửi sẽ thấy)
          </label>
          <textarea id={`refuse-${c.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
          <div style={{ display: "flex", gap: 8 }}>
            <button className="primary sm" type="submit" disabled={busy || note.trim().length < 5}>
              Từ chối
            </button>
            <button className="sm" type="button" onClick={() => setRefusing(false)} disabled={busy}>
              Huỷ
            </button>
          </div>
        </form>
      )}
      {error && (
        <p className="error small" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function firstStep(c: ClosureListItem): string {
  if (c.kind === "auto") return "Tự động: review của một commit sau thấy lỗi đã được sửa";
  if (c.kind === "admin") return `${c.requested_by_name ?? "Admin"} đóng thủ công`;
  if (c.kind === "reopen") return `${c.requested_by_name ?? "Admin"} mở lại finding`;
  return `${c.requested_by_name ?? "Thành viên"} gửi yêu cầu đóng`;
}
