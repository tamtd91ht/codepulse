"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { type SubTab, SubTabs } from "@/components/AdminTabs";
import { RangePicker, Score, useUrlRange } from "@/components/Evaluation";
import { Icon } from "@/components/icons";
import { Card, Drawer, EmptyState, ExternalLink, Loading, PageHeader, Pagination, Pill, StatStrip, StripStat } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, date, relative } from "@/lib/format";
import type { JiraTask, JiraTaskDetail, JiraTaskList, JiraTaskMr, JiraTaskOptions, JiraUnlinkedMrs, Me, Person } from "@/lib/types";

/*
 * Task Jira (docs/specs/27 §18, user 2026-11-09): the Jira issues of the dự án one sees, each
 * with the MRs that name it and their current review, an issue's detail in a drawer, and the
 * MRs that name no issue. Read only: Jira itself is opened through the links. The period is the
 * dashboard-wide one (lib/timeScope).
 */

type View = "issues" | "unlinked";

const PAGE_SIZE = 25;
const MR_STATE: Record<string, string> = { merged: "Đã merge", opened: "Đang mở", closed: "Đã đóng", locked: "Khoá" };

/** One URL query parameter as state (kept on reload and in shared links). */
function useParam(name: string): [string, (v: string) => void] {
  const [value, setValue] = useState("");
  useEffect(() => {
    const read = () => setValue(new URLSearchParams(window.location.search).get(name) ?? "");
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [name]);
  const update = useCallback(
    (next: string) => {
      setValue(next);
      const url = new URL(window.location.href);
      if (next) url.searchParams.set(name, next);
      else url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    },
    [name],
  );
  return [value, update];
}

const pct = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 100)}%` : "—");

function StateChip({ state }: { state: string | null }) {
  return state === "closed" ? <Pill tone="good">Đã xong</Pill> : <Pill tone="info">Đang mở</Pill>;
}

function Severe({ critical, high }: { critical: number; high: number }) {
  if (!critical && !high) return <span className="muted">—</span>;
  return (
    <span className="sev-row" title={`${critical} critical, ${high} high đang mở`}>
      <span className={`sev-count critical${critical ? "" : " zero"}`}>{critical}</span>
      <span className={`sev-count high${high ? "" : " zero"}`}>{high}</span>
    </span>
  );
}

function People({ people }: { people: { user_id: number | null; name: string }[] }) {
  if (!people.length) return <span className="muted">Chưa giao</span>;
  return (
    <>
      {people.map((p, i) => (
        <span key={`${p.user_id ?? p.name}-${i}`}>
          {i > 0 && ", "}
          {p.user_id ? <Link href={`/members/${p.user_id}`}>{p.name}</Link> : p.name}
        </span>
      ))}
    </>
  );
}

/** An MR with its current review: link to GitLab, to the review in CodePulse, its findings. */
function MrLine({ mr }: { mr: JiraTaskMr }) {
  const r = mr.review;
  return (
    <li className="jt-mr">
      <div className="jt-mr-head">
        {mr.url ? <ExternalLink href={mr.url}>!{mr.iid}</ExternalLink> : <span>!{mr.iid}</span>}
        <span className="jt-mr-title">{mr.title ?? "—"}</span>
        {mr.state && <Pill tone={mr.state === "merged" ? "good" : undefined}>{MR_STATE[mr.state] ?? mr.state}</Pill>}
        {mr.link === "mention" && (
          <span className="muted small" title="Key chỉ có trong mô tả MR, không ở tiêu đề hay tên nhánh">
            chỉ nhắc trong mô tả
          </span>
        )}
      </div>
      <div className="muted small">
        {mr.repo ?? "—"}
        {mr.author && <> · {mr.author.user_id ? <Link href={`/members/${mr.author.user_id}`}>{mr.author.name}</Link> : mr.author.name}</>}
        {mr.created_at && <> · mở {relative(mr.created_at)}</>}
      </div>
      <div className="jt-mr-review">
        {r ? (
          <>
            <Link href={`/reviews/${r.id}`}>Xem review</Link>
            <Score value={r.score} />
            {r.verdict && <span className="small">{VERDICT_LABEL[r.verdict] ?? r.verdict}</span>}
            <Severe critical={r.critical} high={r.high} />
          </>
        ) : (
          <span className="muted small">Chưa có review (hoặc bạn không được xem)</span>
        )}
      </div>
    </li>
  );
}

function IssueDrawer({ issueKey, onClose, onOpen }: { issueKey: string; onClose: () => void; onOpen: (key: string) => void }) {
  const { data, error } = useApi<JiraTaskDetail>(`/jira/tasks/${encodeURIComponent(issueKey)}`);
  return (
    <Drawer
      title={
        <span className="jt-drawer-title">
          <code>{issueKey}</code> {data?.title ?? ""}
        </span>
      }
      onClose={onClose}
      actions={
        data?.url ? (
          <a className="button sm" href={data.url} target="_blank" rel="noopener noreferrer">
            <Icon name="external" size={14} /> Mở trên Jira
          </a>
        ) : undefined
      }
    >
      {!data ? (
        <Loading error={error} rows={5} />
      ) : (
        <div className="jt-detail">
          <dl className="jt-facts">
            {data.url && (
              <>
                <dt>Jira</dt>
                <dd>
                  <ExternalLink href={data.url}>{data.key}</ExternalLink>
                </dd>
              </>
            )}
            <dt>Trạng thái</dt>
            <dd>
              <StateChip state={data.state} />
              {data.closed_at && <span className="muted small"> {date(data.closed_at)}</span>}
            </dd>
            <dt>Loại · SP</dt>
            <dd>
              {data.type ?? "—"} · {data.story_points ?? "chưa ước lượng"}
            </dd>
            <dt>Người làm</dt>
            <dd>
              <People people={data.assignees} />
            </dd>
            <dt>Tạo</dt>
            <dd>{date(data.created_at)}</dd>
            {data.due_date && (
              <>
                <dt>Hạn</dt>
                <dd>{date(data.due_date)}</dd>
              </>
            )}
            {data.business_projects.length > 0 && (
              <>
                <dt>Dự án</dt>
                <dd>{data.business_projects.map((b) => b.name).join(", ")}</dd>
              </>
            )}
            {(data.components.length > 0 || data.labels.length > 0) && (
              <>
                <dt>Component · label</dt>
                <dd className="jt-tags">
                  {[...data.components, ...data.labels].map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </dd>
              </>
            )}
            {data.parent && (
              <>
                <dt>Thuộc</dt>
                <dd>
                  <button type="button" className="link" onClick={() => onOpen(data.parent!.key)}>
                    {data.parent.key}
                  </button>{" "}
                  {data.parent.title}
                </dd>
              </>
            )}
          </dl>

          {data.children.length > 0 && (
            <section>
              <h3>Việc con ({data.children.length})</h3>
              <ul className="jt-children">
                {data.children.map((c) => (
                  <li key={c.key}>
                    <button type="button" className="link" onClick={() => onOpen(c.key)}>
                      {c.key}
                    </button>{" "}
                    {c.title} <StateChip state={c.state} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3>Merge request ({data.merge_requests.length})</h3>
            {data.merge_requests.length ? (
              <ul className="jt-mrs">
                {data.merge_requests.map((m) => (
                  <MrLine key={m.id} mr={m} />
                ))}
              </ul>
            ) : (
              <p className="muted">
                Chưa có MR nào ghi key <code>{data.key}</code>. Đặt key vào tiêu đề MR hoặc tên nhánh (vd.{" "}
                <code>feature/{data.key}-mo-ta</code>) để nối.
              </p>
            )}
          </section>
        </div>
      )}
    </Drawer>
  );
}

function IssueTable({ rows, onOpen }: { rows: JiraTask[]; onOpen: (key: string) => void }) {
  return (
    <div className="table-wrap">
      <table className="jt-table">
        <thead>
          <tr>
            <th>Issue</th>
            <th>Người làm</th>
            <th>Trạng thái</th>
            <th className="num">SP</th>
            <th className="num" title="MR ghi key của issue (đã merge / tổng)">
              MR
            </th>
            <th className="num" title="Điểm trung bình review hiện tại của các MR">
              Điểm
            </th>
            <th title="Critical / high đang mở trong review của các MR">Lỗi nặng</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.key} className="jt-row" onClick={() => onOpen(t.key)}>
              <td>
                <div className="jt-issue">
                  <button
                    type="button"
                    className="link jt-key"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(t.key);
                    }}
                  >
                    {t.key}
                  </button>
                  {t.url && (
                    <a
                      className="jt-out"
                      href={t.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Mở ${t.key} trên Jira`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Icon name="external" size={12} />
                    </a>
                  )}
                  <span className="jt-title">{t.title ?? "—"}</span>
                </div>
                <div className="muted small">
                  {t.type ?? "—"}
                  {t.parent_key && <> · thuộc {t.parent_key}</>}
                </div>
              </td>
              <td onClick={(e) => e.stopPropagation()}>
                <People people={t.assignees} />
              </td>
              <td>
                <StateChip state={t.state} />
                {t.closed_at && <div className="muted small">{date(t.closed_at)}</div>}
              </td>
              <td className="num">{t.story_points ?? <span className="muted">—</span>}</td>
              <td className="num">{t.mrs ? `${t.mrs_merged}/${t.mrs}` : <span className="bad">0</span>}</td>
              <td className="num">
                <Score value={t.score} />
              </td>
              <td>
                <Severe critical={t.critical} high={t.high} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UnlinkedTable({ rows }: { rows: JiraTaskMr[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>MR</th>
            <th>Tác giả</th>
            <th>Nhánh · key tìm thấy</th>
            <th className="num">Điểm</th>
            <th>Lỗi nặng</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td>
                <div className="jt-issue">
                  {m.url ? <ExternalLink href={m.url}>!{m.iid}</ExternalLink> : <span>!{m.iid}</span>}
                  <span className="jt-title">{m.title ?? "—"}</span>
                </div>
                <div className="muted small">
                  {m.repo ?? "—"} · {m.created_at ? relative(m.created_at) : "—"}
                  {m.state && <> · {MR_STATE[m.state] ?? m.state}</>}
                </div>
              </td>
              <td>{m.author ? m.author.user_id ? <Link href={`/members/${m.author.user_id}`}>{m.author.name}</Link> : m.author.name : "—"}</td>
              <td className="small">
                <code>{m.branch ?? "—"}</code>
                {m.keys && m.keys.length > 0 && (
                  <div className="muted" title="Có dạng key nhưng không khớp issue nào đã đồng bộ (dự án Jira khác, gõ sai, hoặc chưa đồng bộ)">
                    {m.keys.join(", ")} (không khớp)
                  </div>
                )}
              </td>
              <td className="num">{m.review ? <Link href={`/reviews/${m.review.id}`}><Score value={m.review.score} /></Link> : <span className="muted">—</span>}</td>
              <td>{m.review ? <Severe critical={m.review.critical} high={m.review.high} /> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function JiraTasksPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const options = useApi<JiraTaskOptions>("/jira/tasks/options");
  const people = useApi<Person[]>("/people");
  const [range, setRange] = useUrlRange("30");
  const [viewParam, setView] = useParam("view");
  const [bp, setBp] = useParam("bp");
  const [jiraProject, setJiraProject] = useParam("jira");
  const [state, setState] = useParam("state");
  const [type, setType] = useParam("type");
  const [assignee, setAssignee] = useParam("assignee");
  const [linked, setLinked] = useParam("linked");
  const [q, setQ] = useParam("q");
  const [openKey, setOpenKey] = useParam("issue");
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState(1);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const view: View = viewParam === "unlinked" ? "unlinked" : "issues";
  useEffect(() => setDraft(q), [q]);
  // A new filter starts at the first page.
  useEffect(() => setPage(1), [bp, jiraProject, state, type, assignee, linked, q, range?.from, range?.to, view]);
  // Typing searches after a short pause, not on every key.
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (draft.trim() !== q) setQ(draft.trim());
    }, 350);
    return () => window.clearTimeout(t);
  }, [draft, q, setQ]);

  const period = range ? { start: range.from, end: range.to } : null;
  const listPath =
    period && view === "issues"
      ? `/jira/tasks${qs({
          ...period,
          business_project_id: bp || null,
          jira_project: jiraProject || null,
          state: state || null,
          type: type || null,
          assignee: assignee || null,
          linked: linked || null,
          q: q || null,
          page,
          page_size: PAGE_SIZE,
        })}`
      : null;
  const unlinkedPath =
    period && view === "unlinked" ? `/jira/tasks/unlinked-mrs${qs({ ...period, business_project_id: bp || null, page, page_size: PAGE_SIZE })}` : null;
  const list = useApi<JiraTaskList>(listPath, { keepPreviousData: true });
  const unlinked = useApi<JiraUnlinkedMrs>(unlinkedPath, { keepPreviousData: true });
  const o = options.data;

  const activeFilters = [jiraProject, state, type, assignee, linked, q, ...(bp ? [bp] : [])].filter(Boolean).length;
  const clear = () => {
    for (const set of [setBp, setJiraProject, setState, setType, setAssignee, setLinked, setQ]) set("");
    setDraft("");
  };

  const views: SubTab<View>[] = [
    { key: "issues", label: "Issue", count: list.data?.total, hint: "Issue Jira và MR, review của chúng" },
    { key: "unlinked", label: "MR chưa gắn issue", count: unlinked.data?.total, hint: "MR không ghi key issue Jira nào" },
  ];

  const header = (
    <PageHeader
      eyebrow="Jira"
      title="Task Jira"
      subtitle="Issue Jira của các dự án bạn xem được, cùng MR và kết quả review của từng việc."
      foldable={false}
      actions={
        o?.can_admin ? (
          <Link className="button" href="/admin#jira">
            <Icon name="settings" size={14} /> Cấu hình Jira
          </Link>
        ) : undefined
      }
    />
  );

  if (o && !o.configured)
    return (
      <>
        {header}
        <Card collapsible={false}>
          <EmptyState icon="inbox" title="Chưa kết nối Jira">
            {o.can_admin ? (
              <>
                Kết nối máy chủ Jira và thêm dự án ở <Link href="/admin#jira">Admin › Jira</Link>.
              </>
            ) : (
              "Nhờ admin kết nối Jira để xem task ở đây."
            )}
          </EmptyState>
        </Card>
      </>
    );

  const k = list.data?.kpis;
  return (
    <>
      {header}
      <div className="report-bar">
        <div className="report-bar-top">
          <SubTabs<View> id="jt-view" label="Xem" variant="bar" tabs={views} value={view} onChange={(v) => setView(v === "issues" ? "" : v)} />
          <button
            type="button"
            className={`report-filter-toggle sm${filtersOpen ? " open" : ""}`}
            aria-expanded={filtersOpen}
            aria-controls="jt-filters"
            onClick={() => setFiltersOpen((v) => !v)}
          >
            <Icon name="filter" size={14} /> Bộ lọc{activeFilters ? ` (${activeFilters})` : ""}
          </button>
          <div className="report-range">
            <RangePicker value={range} onChange={setRange} compact />
          </div>
        </div>
        <div id="jt-filters" className={`report-filters${filtersOpen ? " open" : ""}`} role="group" aria-label="Bộ lọc task">
          {view === "issues" && (
          <label className="fsel jt-search">
            <Icon name="search" size={14} />
            <input
              type="search"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Key hoặc tiêu đề"
              aria-label="Tìm issue"
            />
          </label>
          )}
          <label className={`fsel${bp ? " set" : ""}`}>
            <span>Dự án</span>
            <select value={bp} onChange={(e) => setBp(e.target.value)}>
              <option value="">Tất cả</option>
              {(o?.business_projects ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          {view === "issues" && (
            <>
              <label className={`fsel${jiraProject ? " set" : ""}`}>
                <span>Jira</span>
                <select value={jiraProject} onChange={(e) => setJiraProject(e.target.value)}>
                  <option value="">Tất cả</option>
                  {(o?.jira_projects ?? []).map((p) => (
                    <option key={p.key} value={p.key}>
                      {p.key}
                      {p.name ? ` · ${p.name}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className={`fsel${state ? " set" : ""}`}>
                <span>Trạng thái</span>
                <select value={state} onChange={(e) => setState(e.target.value)}>
                  <option value="">Tất cả</option>
                  <option value="open">Đang mở</option>
                  <option value="closed">Đã xong</option>
                </select>
              </label>
              <label className={`fsel${type ? " set" : ""}`}>
                <span>Loại</span>
                <select value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="">Tất cả</option>
                  {(o?.types ?? []).map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label className={`fsel${assignee ? " set" : ""}`}>
                <span>Người làm</span>
                <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                  <option value="">Tất cả</option>
                  {me && <option value={me.id}>Tôi</option>}
                  {(people.data ?? [])
                    .filter((p) => p.id !== me?.id)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.full_name ?? p.primary_email ?? `#${p.id}`}
                      </option>
                    ))}
                </select>
              </label>
              <label className={`fsel${linked ? " set" : ""}`}>
                <span>MR</span>
                <select value={linked} onChange={(e) => setLinked(e.target.value)}>
                  <option value="">Tất cả</option>
                  <option value="with_mr">Đã có MR</option>
                  <option value="without_mr">Chưa có MR</option>
                </select>
              </label>
            </>
          )}
          {activeFilters > 0 && (
            <button type="button" className="ghost sm report-clear" onClick={clear}>
              <Icon name="close" size={14} /> Bỏ lọc
            </button>
          )}
        </div>
      </div>

      {view === "issues" ? (
        <>
          <StatStrip label="Tổng quan task trong kỳ">
            <StripStat label="Issue trong kỳ" value={k?.issues} sub="làm trong khoảng thời gian" />
            <StripStat label="Đang mở" value={k?.open} />
            <StripStat label="Xong trong kỳ" value={k?.closed_in_period} tone={k?.closed_in_period ? "good" : undefined} />
            <StripStat label="Có MR" value={k?.with_mr} sub={k ? pct(k.with_mr, k.issues) : undefined} title="Issue có ít nhất một MR ghi key" />
            <StripStat label="Có Story Points" value={k?.with_points} sub={k ? pct(k.with_points, k.issues) : undefined} />
            <StripStat
              label="Còn lỗi nặng"
              value={k?.severe_open}
              tone={k?.severe_open ? "bad" : undefined}
              title="Issue có MR mà review hiện tại còn critical/high đang mở"
            />
          </StatStrip>
          <Card collapsible={false} flush>
            {!list.data ? (
              <Loading error={list.error} rows={6} />
            ) : list.data.items.length === 0 ? (
              <EmptyState icon="inbox" title="Không có issue nào khớp">
                Đổi khoảng thời gian hoặc bộ lọc. Chỉ hiện issue của dự án Jira đã gắn với dự án bạn xem được.
              </EmptyState>
            ) : (
              <div className={list.isLoading ? "eval-dim" : undefined}>
                <IssueTable rows={list.data.items} onOpen={setOpenKey} />
                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={list.data.total}
                  shown={list.data.items.length}
                  onPage={setPage}
                  label="issue"
                />
              </div>
            )}
          </Card>
        </>
      ) : (
        <Card collapsible={false} flush>
          {o?.task_source === "jira" && (
            <p className="muted small jt-note">
              Jira đang là nguồn task của DPI: mỗi MR nên ghi key issue ở tiêu đề hoặc tên nhánh (vd. <code>feature/ABC-123-mo-ta</code>).
            </p>
          )}
          {!unlinked.data ? (
            <Loading error={unlinked.error} rows={6} />
          ) : unlinked.data.items.length === 0 ? (
            <EmptyState icon="check" title="Mọi MR trong kỳ đều đã gắn issue" />
          ) : (
            <div className={unlinked.isLoading ? "eval-dim" : undefined}>
              <UnlinkedTable rows={unlinked.data.items} />
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={unlinked.data.total}
                shown={unlinked.data.items.length}
                onPage={setPage}
                label="MR"
              />
            </div>
          )}
        </Card>
      )}

      {openKey && <IssueDrawer key={openKey} issueKey={openKey} onClose={() => setOpenKey("")} onOpen={setOpenKey} />}
      <p className="muted small jt-foot">
        Chỉ đọc từ Jira: mở issue trên Jira để sửa. Dữ liệu đồng bộ theo chu kỳ ở Admin › Jira.
      </p>
    </>
  );
}
