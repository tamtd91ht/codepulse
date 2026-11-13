"use client";

import Link from "next/link";
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { useSWRConfig } from "swr";

import { REVIEW_MENU, ReviewTabs } from "@/components/ReviewTabs";
import { type DayRange, DateRangePicker } from "@/components/DateRangePicker";
import { Icon } from "@/components/icons";
import { LinkAuthor } from "@/components/LinkAuthor";
import { MultiSelect } from "@/components/MultiSelect";
import { announceReviewQueued } from "@/components/ReviewActivity";
import { Alert, Avatar, Card, EmptyState, Loading, PageHeader, Pill } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, dateTime, localDay, relative, shiftDays, thisMonth } from "@/lib/format";
import type { Branch, CommitList, CommitRow, Person, Repo, ReviewCommitsResult } from "@/lib/types";

/** Quick picks of the commit-day range, all ending today (they replace the old window chips). */
function quickPicks(today: string): { label: string; range: DayRange }[] {
  const back = (days: number) => ({ from: shiftDays(today, -(days - 1)), to: today });
  return [
    { label: "Hôm nay", range: back(1) },
    { label: "3 ngày", range: back(3) },
    { label: "7 ngày", range: back(7) },
    { label: "14 ngày", range: back(14) },
    { label: "30 ngày", range: back(30) },
    { label: "Tháng này", range: { from: thisMonth(), to: today } },
  ];
}
const ALL = "all";
const POLL_MS = 10_000;
const POLL_HIDDEN_MS = 60_000; // a tab not shown asks GitLab less often
// Commit lists fetched at once (each asks GitLab): the next repo starts when one is in.
const LOAD_AT_ONCE = 4;

/** Local days to the API's window: from the start of `from`; up to now when `to` is today. */
function sinceOf(range: DayRange): { since?: string; until?: string } {
  return {
    since: range.from ? new Date(`${range.from}T00:00:00`).toISOString() : undefined,
    until: range.to && range.to !== localDay() ? new Date(`${range.to}T23:59:59`).toISOString() : undefined,
  };
}

/** One labelled field of the scope form. */
function Field({ label, htmlFor, hint, children }: { label: string; htmlFor?: string; hint?: string; children: ReactNode }) {
  return (
    <div className="rq-field">
      {htmlFor ? (
        <label className="rq-label" htmlFor={htmlFor}>
          {label}
        </label>
      ) : (
        <span className="rq-label">{label}</span>
      )}
      {children}
      {hint && <span className="muted small">{hint}</span>}
    </div>
  );
}

function StatusCell({ c }: { c: CommitRow }) {
  if (c.review_id && c.review_status === "done")
    return (
      <Link href={`/reviews/${c.review_id}`} className="status-link" aria-label={`Xem review ${c.short_sha}`}>
        {c.verdict ? <span className={`verdict ${c.verdict}`}>{VERDICT_LABEL[c.verdict] ?? c.verdict}</span> : null}
        <span className="muted">{c.score !== null ? ` ${Math.round(c.score)}/100` : c.verdict ? "" : "Xem review"}</span>
      </Link>
    );
  if (c.review_status === "queued" || c.review_status === "running")
    return <Pill tone="info" icon="sync">{c.review_status === "running" ? "Đang review" : "Trong hàng đợi"}</Pill>;
  if (c.review_status === "failed" || c.review_status === "skipped") {
    const pill =
      c.review_status === "failed" ? (
        <Pill tone="bad" title={c.review_error ?? undefined}>
          Lỗi, review lại được
        </Pill>
      ) : (
        <Pill tone="warn" title={c.review_error ?? undefined}>
          Đã bỏ qua
        </Pill>
      );
    if (!c.review_id) return pill;
    // A skipped commit is not picked here (a batch would skip it again): its review page says
    // why and runs it again, past the LOC limit when that is the reason.
    return (
      <>
        <Link href={`/reviews/${c.review_id}`}>{pill}</Link>{" "}
        <Link
          href={`/reviews/${c.review_id}`}
          className="small"
          title={c.review_status === "skipped" ? "Xem lý do và review lại (có tuỳ chọn bỏ qua giới hạn LOC)" : undefined}
        >
          Xem chi tiết
        </Link>
      </>
    );
  }
  if (c.is_merge) return <span className="muted">Merge commit</span>;
  if (!c.reviewable) return <span className="muted">{c.reason}</span>;
  return <span className="muted">Chưa review</span>;
}

/** Not reviewed yet: what a new scope and "chọn hết" pick. A reviewed one is only picked by hand. */
const fresh = (c: CommitRow) => c.reviewable && !c.again;

/** One submitted scope: the same branch, people and window for every chosen repo. */
interface Scope {
  seq: number; // every new scope remounts the tabs, so each primes and reports again
  ref: string;
  params: string;
  repos: number[];
}
const commitsPath = (repoId: number, params: string) => `/repos/${repoId}/commits?${params}`;

type Outcome = ReviewCommitsResult | { error: string };

/** Pick repos (any number, all at once if wanted), a branch, people and a window; see the commits per repo; queue reviews (docs/specs/17). */
export default function RequestReviewPage() {
  const repos = useApi<Repo[]>("/repos");
  const people = useApi<Person[]>("/people?for=review");
  const { mutate } = useSWRConfig();
  const [bp, setBp] = useState(ALL);
  const [repoIds, setRepoIds] = useState<number[]>([]);
  const [branch, setBranch] = useState("");
  const [branchSearch, setBranchSearch] = useState("");
  const [who, setWho] = useState<number[]>([]); // empty = everyone I may review
  const [range, setRange] = useState<DayRange>(() => ({ from: shiftDays(localDay(), -6), to: localDay() }));
  const [excludeReviewed, setExcludeReviewed] = useState(false);
  const [scope, setScope] = useState<Scope | null>(null);
  const [tab, setTab] = useState<number | null>(null);
  const [lists, setLists] = useState<Record<number, CommitList | undefined>>({});
  // The per-repo cap the server applied (REVIEW_COMMITS_LIST_MAX, Admin › Cấu hình review).
  const listLimit = Object.values(lists).find((l) => l?.limit)?.limit;
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const [selected, setSelected] = useState<Record<number, Set<string>>>({});
  const [outcomes, setOutcomes] = useState<Record<number, Outcome> | null>(null);
  const [busy, setBusy] = useState(false);

  const byId = useMemo(() => new Map((repos.data ?? []).map((r) => [r.id, r])), [repos.data]);
  const projects = useMemo(() => {
    const seen = new Map<string, string>();
    for (const r of repos.data ?? []) seen.set(String(r.business_project_id ?? "none"), r.business_project ?? "Chưa gán dự án");
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [repos.data]);
  // Opened from a repo elsewhere (the dự án page's repo drawer): ?project=&repo= are read once,
  // when the repo list arrives, and prefill dự án, repo and its review branch; the rest is the
  // reader's. A repo that is gone or not reviewable leaves only its dự án picked.
  const [arrived, setArrived] = useState(false);
  useEffect(() => {
    if (arrived || !repos.data) return;
    setArrived(true);
    const params = new URL(window.location.href).searchParams;
    const wanted = (repos.data ?? []).find((r) => String(r.id) === params.get("repo"));
    const project = params.get("project") ?? (wanted ? String(wanted.business_project_id ?? "none") : null);
    if (project && (repos.data ?? []).some((r) => String(r.business_project_id ?? "none") === project)) setBp(project);
    if (wanted?.review_enabled && (!project || String(wanted.business_project_id ?? "none") === project)) {
      setRepoIds([wanted.id]);
      setBranch(wanted.review_branch ?? wanted.default_branch ?? "");
    }
  }, [arrived, repos.data]);
  // The dự án filter narrows the repo box; repos picked under another dự án stay listed.
  const repoOptions = (repos.data ?? []).filter(
    (r) => bp === ALL || String(r.business_project_id ?? "none") === bp || repoIds.includes(r.id),
  );
  const picked = repoIds.map((id) => byId.get(id)).filter((r): r is Repo => !!r);
  const first = picked[0] ?? null;
  const pickRepos = (next: number[]) => {
    // The first repo picked fills the branch with its review branch.
    const firstNew = repoIds.length === 0 ? byId.get(next[0]) : undefined;
    if (firstNew && !branch.trim()) setBranch(firstNew.review_branch ?? firstNew.default_branch ?? "");
    setRepoIds(next);
    setBranchSearch("");
  };
  // Repos whose own review branch differs from the one typed: they are listed on it anyway.
  const otherBranch = picked.filter((r) => branch.trim() && branch.trim() !== "*" && (r.review_branch ?? r.default_branch) !== branch.trim());

  // Branch names for the datalist, from the first chosen repo: the default first; typing searches GitLab.
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(branchSearch.trim()), 300);
    return () => clearTimeout(t);
  }, [branchSearch]);
  const branches = useApi<Branch[]>(first ? `/repos/${first.id}/branches${qs({ search: debounced || null })}` : null);

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (picked.length === 0 || !branch.trim()) return;
    setOutcomes(null);
    const params = new URLSearchParams();
    params.set("ref", branch.trim());
    const { since, until } = sinceOf(range);
    if (since) params.set("since", since);
    if (until) params.set("until", until);
    for (const id of who) params.append("user_id", String(id));
    if (excludeReviewed) params.set("exclude_reviewed", "true");
    const next: Scope = { seq: (scope?.seq ?? 0) + 1, ref: branch.trim(), params: params.toString(), repos: picked.map((r) => r.id) };
    if (scope && next.params === scope.params && next.repos.join() === scope.repos.join()) {
      // Same scope again = refresh every tab.
      for (const id of next.repos) void mutate(commitsPath(id, next.params));
      return;
    }
    setSelected({});
    setLists({});
    setFailed({});
    setScope(next);
    setTab(next.repos[0]);
  }

  const onList = (repoId: number, list: CommitList | undefined, error: boolean) => {
    setLists((prev) => ({ ...prev, [repoId]: list }));
    setFailed((prev) => ({ ...prev, [repoId]: error }));
  };
  const setRepoSelected = (repoId: number, update: (prev: Set<string>) => Set<string>) =>
    setSelected((prev) => ({ ...prev, [repoId]: update(prev[repoId] ?? new Set()) }));

  // What would be sent, per repo: only shas the latest list still offers.
  const chosen = (scope?.repos ?? []).map((id) => {
    const rows = (lists[id]?.commits ?? []).filter((c) => c.reviewable && selected[id]?.has(c.sha));
    return { id, rows, max: lists[id]?.batch_max ?? 50 };
  });
  const total = chosen.reduce((n, c) => n + c.rows.length, 0);
  const againCount = chosen.reduce((n, c) => n + c.rows.filter((r) => r.again).length, 0);
  const tooMany = chosen.filter((c) => c.rows.length > c.max);
  const reposChosen = chosen.filter((c) => c.rows.length > 0).length;
  // Repos whose commit list is in (or failed): the next ones may start loading.
  const settled = (scope?.repos ?? []).filter((id) => lists[id] !== undefined || failed[id]).length;

  async function queue() {
    if (!scope || total === 0 || tooMany.length) return;
    if (againCount > 0) {
      // Reviewing again spends LLM budget on work that has a result: say how much, by verdict.
      const byVerdict = new Map<string, number>();
      for (const c of chosen)
        for (const r of c.rows.filter((row) => row.again)) {
          const label = r.verdict ? (VERDICT_LABEL[r.verdict] ?? r.verdict) : "Chưa có kết luận";
          byVerdict.set(label, (byVerdict.get(label) ?? 0) + 1);
        }
      const lines = [...byVerdict].map(([label, n]) => `  • ${label}: ${n}`).join("\n");
      const ok = window.confirm(
        `Sẽ review lại ${againCount} commit đã có kết quả:\n${lines}\n` +
          (total > againCount ? `và review ${total - againCount} commit chưa review.\n` : "") +
          "\nReview lại tốn thêm ngân sách LLM và tạo lần review mới; kết quả cũ được giữ làm lịch sử. Tiếp tục?",
      );
      if (!ok) return;
    }
    setBusy(true);
    setOutcomes(null);
    try {
      const sent = chosen.filter((c) => c.rows.length > 0);
      // Not yet reviewed and review-again go separately: `again` must not reach a commit that
      // finished reviewing after the list was drawn, which the page never warned about.
      const post = (id: number, rows: CommitRow[], again: boolean) =>
        rows.length
          ? api.post<ReviewCommitsResult>(`/repos/${id}/review-commits`, { ref: scope.ref, shas: rows.map((r) => r.sha), again })
          : Promise.resolve<ReviewCommitsResult>({ queued: [], skipped: [] });
      const settled = await Promise.allSettled(
        sent.map(async (c) => {
          const [a, b] = await Promise.all([
            post(c.id, c.rows.filter((r) => !r.again), false),
            post(c.id, c.rows.filter((r) => r.again), true),
          ]);
          return { queued: [...a.queued, ...b.queued], skipped: [...a.skipped, ...b.skipped] };
        }),
      );
      const out: Record<number, Outcome> = {};
      settled.forEach((s, i) => {
        out[sent[i].id] =
          s.status === "fulfilled" ? s.value : { error: s.reason instanceof ApiError ? s.reason.message : String(s.reason) };
      });
      setOutcomes(out);
      // A repo that failed keeps its picks, to send again.
      setSelected((prev) => {
        const next = { ...prev };
        for (const [id, o] of Object.entries(out)) if (!("error" in o)) next[Number(id)] = new Set();
        return next;
      });
      if (Object.values(out).some((o) => !("error" in o) && o.queued.length)) announceReviewQueued();
      await Promise.all(sent.map((c) => mutate(commitsPath(c.id, scope.params))));
    } finally {
      setBusy(false);
    }
  }

  const queuedTotal = outcomes ? Object.values(outcomes).reduce((n, o) => n + ("error" in o ? 0 : o.queued.length), 0) : 0;
  const activeTab = scope && tab !== null && scope.repos.includes(tab) ? tab : (scope?.repos[0] ?? null);
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!scope || activeTab === null) return;
    const i = scope.repos.indexOf(activeTab);
    const n = scope.repos.length;
    const next =
      e.key === "ArrowRight" ? (i + 1) % n
      : e.key === "ArrowLeft" ? (i - 1 + n) % n
      : e.key === "Home" ? 0
      : e.key === "End" ? n - 1
      : -1;
    if (next < 0) return;
    e.preventDefault();
    setTab(scope.repos[next]);
    document.getElementById(`repo-tab-${scope.repos[next]}`)?.focus();
  };

  return (
    <>
      <PageHeader
        eyebrow={REVIEW_MENU}
        title="Yêu cầu review"
        subtitle="Chọn repo (bao nhiêu cũng được, hoặc tất cả), một branch, người và khoảng thời gian; review chạy trong nền và lưu vào dashboard như review tự động. Chỉ repo thuộc dự án bạn được cấp quyền; trong đó bạn review được việc của mình; người quản lý dự án và admin review được mọi người."
      />
      <ReviewTabs current="/reviews/request" />

      <Card title="Phạm vi">
        {!repos.data || !people.data ? (
          <Loading error={repos.error ?? people.error} />
        ) : repos.data.length === 0 ? (
          <EmptyState icon="folder" title="Chưa có repo nào bạn được yêu cầu review">
            Chỉ repo thuộc dự án bạn được cấp quyền mới hiện ở đây; liên hệ admin nếu thiếu.
          </EmptyState>
        ) : (
          <form onSubmit={submit} className="request-form">
            <div className="rq-grid">
              <Field label="Dự án" htmlFor="rq-project">
                <select id="rq-project" value={bp} onChange={(e) => setBp(e.target.value)}>
                  <option value={ALL}>Mọi dự án</option>
                  {projects.map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="Repo"
                hint={repoIds.length > LOAD_AT_ONCE ? `Danh sách commit tải lần lượt ${LOAD_AT_ONCE} repo một lúc.` : undefined}
              >
                <MultiSelect
                  label="Repo"
                  empty="Chọn repo…"
                  unit="repo"
                  placeholder="Tìm repo…"
                  value={repoIds}
                  onChange={pickRepos}
                  options={repoOptions.map((r) => ({
                    value: r.id,
                    label: r.path_with_namespace,
                    hint: bp === ALL ? (r.business_project ?? undefined) : undefined,
                    disabled: !r.review_enabled,
                    note: r.review_enabled ? undefined : "Đã tắt review",
                  }))}
                />
              </Field>
              <Field label="Branch" htmlFor="rq-branch">
                <input
                  id="rq-branch"
                  list="branch-options"
                  value={branch}
                  onChange={(e) => {
                    setBranch(e.target.value);
                    setBranchSearch(e.target.value === "*" ? "" : e.target.value);
                  }}
                  placeholder={first ? "Tên branch, * = mọi nhánh" : "Chọn repo trước"}
                  disabled={!first}
                  required
                  autoComplete="off"
                  spellCheck={false}
                />
                <datalist id="branch-options">
                  <option value="*">mọi nhánh</option>
                  {(branches.data ?? []).map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.default ? "mặc định" : b.last_commit_at ? relative(b.last_commit_at) : ""}
                    </option>
                  ))}
                </datalist>
              </Field>
              <Field label="Thành viên">
                <MultiSelect
                  label="Thành viên"
                  empty="Mọi người bạn được review"
                  placeholder="Tìm tên hoặc email…"
                  value={who}
                  onChange={setWho}
                  options={people.data.map((p) => ({
                    value: p.id,
                    label: p.full_name ?? p.primary_email ?? `#${p.id}`,
                    hint: p.full_name ? (p.primary_email ?? undefined) : undefined,
                    lead: <Avatar name={p.full_name} email={p.primary_email} size="sm" src={p.avatar_url} />,
                  }))}
                />
              </Field>
              <Field label="Ngày commit">
                <DateRangePicker
                  label="Ngày commit"
                  showLabel={false}
                  value={range}
                  max={localDay()}
                  onChange={setRange}
                  quickPicks={quickPicks(localDay())}
                />
              </Field>
              <Field label="Lọc thêm">
                <label className="rq-check" title="Bỏ commit đã có review xong hoặc đang chờ / đang review">
                  <input type="checkbox" checked={excludeReviewed} onChange={(e) => setExcludeReviewed(e.target.checked)} />
                  Ẩn commit đã review
                </label>
              </Field>
            </div>

            {branches.error && (
              <span className="error small" role="status">
                Không lấy được danh sách branch: {branches.error.message}
              </span>
            )}
            {picked.length > 1 && branch.trim() && (
              <Alert tone="warning">
                Cả {picked.length} repo sẽ lấy commit trên cùng branch <code>{branch.trim()}</code>
                {branch.trim() === "*" ? " (mọi nhánh)" : ""}. Repo không có branch này sẽ báo lỗi trong tab của nó.
                {otherBranch.length > 0 && ` Branch review của ${otherBranch.map((r) => r.path_with_namespace).join(", ")} khác với branch này.`}
              </Alert>
            )}

            <div className="rq-foot">
              <span className="muted small">
                Danh sách lấy thẳng từ GitLab, {listLimit ? `tối đa ${listLimit}` : "số"} commit mỗi repo theo cấu hình (Admin › Cấu hình review).
              </span>
              <button className={scope ? undefined : "primary ink"} type="submit" disabled={busy || picked.length === 0 || !branch.trim()}>
                <Icon name="search" size={14} /> Xem commit
              </button>
            </div>
          </form>
        )}
      </Card>

      {scope && (
        <Card
          title={`Commit trên ${scope.ref}`}
          subtitle={`${scope.repos.length} repo · đang chọn ${total} commit${reposChosen > 1 ? ` ở ${reposChosen} repo` : ""}${tooMany.length ? ` · ${tooMany.map((c) => `${byId.get(c.id)?.path_with_namespace ?? c.id} quá ${c.max}`).join(", ")}, bỏ bớt để gửi` : ""}`}
          actions={
            <button
              className="primary"
              type="button"
              disabled={busy || total === 0 || tooMany.length > 0}
              title={tooMany.length ? "Mỗi repo có giới hạn số commit mỗi lần" : undefined}
              onClick={() => void queue()}
            >
              {busy ? "Đang đưa vào hàng đợi…" : `Review ${total} commit`}
            </button>
          }
          flush
        >
          {againCount > 0 && (
            <div style={{ padding: "0 18px" }}>
              <Alert tone="warning">
                <strong>{againCount}</strong> commit đã chọn đã có kết quả review. Gửi đi sẽ review lại thành lần mới (tốn thêm ngân sách
                LLM); kết quả cũ được giữ và chỉ được thay khi lần mới xong.
              </Alert>
            </div>
          )}
          {outcomes && (
            <div style={{ padding: "0 18px", display: "grid", gap: 8 }}>
              <Alert tone={queuedTotal ? "success" : "warning"} onClose={() => setOutcomes(null)}>
                Đã đưa <strong>{queuedTotal}</strong> commit vào hàng đợi review. Kết quả hiện ở cột trạng thái và trên dashboard sau vài phút.
                {Object.entries(outcomes).map(([id, o]) => {
                  const name = byId.get(Number(id))?.path_with_namespace ?? id;
                  if ("error" in o) return null;
                  return o.skipped.length ? (
                    <div key={id} className="small">
                      {name}: bỏ qua {o.skipped.map((s) => `${s.sha.slice(0, 8)} (${s.reason})`).join(", ")}
                    </div>
                  ) : null;
                })}
              </Alert>
              {Object.entries(outcomes)
                .filter(([, o]) => "error" in o)
                .map(([id, o]) => (
                  <Alert key={id} tone="error">
                    {byId.get(Number(id))?.path_with_namespace ?? id}: {"error" in o ? o.error : ""}. Các commit đã chọn của repo này vẫn được giữ, gửi lại được.
                  </Alert>
                ))}
            </div>
          )}
          <div className="tabs repo-tabs" role="tablist" aria-label="Repo" onKeyDown={onTabKey} style={{ padding: "0 18px" }}>
            {scope.repos.map((id) => {
              const list = lists[id];
              const n = chosen.find((c) => c.id === id)?.rows.length ?? 0;
              const on = id === activeTab;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  id={`repo-tab-${id}`}
                  aria-selected={on}
                  aria-controls={`repo-panel-${id}`}
                  tabIndex={on ? 0 : -1}
                  className={on ? "active" : undefined}
                  onClick={() => setTab(id)}
                  title={byId.get(id)?.path_with_namespace}
                >
                  {byId.get(id)?.path_with_namespace ?? `#${id}`}
                  {failed[id] && !list ? (
                    <span className="error small"> lỗi</span>
                  ) : (
                    <span className="muted small" title={list ? `đã chọn ${n} trên ${list.commits.length} commit` : undefined}>
                      {" "}
                      {list ? (n ? `${n}/${list.commits.length}` : list.commits.length) : "…"}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          {scope.repos.map((id, index) => (
            <RepoCommits
              key={`${scope.seq}:${id}`}
              repoId={id}
              // A few at a time (LOAD_AT_ONCE), the shown tab always: many repos must not ask
              // GitLab all at once.
              path={id === activeTab || index < settled + LOAD_AT_ONCE ? commitsPath(id, scope.params) : null}
              active={id === activeTab}
              selected={selected[id] ?? new Set()}
              setSelected={(update) => setRepoSelected(id, update)}
              onList={onList}
            />
          ))}
        </Card>
      )}
    </>
  );
}

/** One repo's tab: it fetches (and polls while a review of it runs) even when not shown, so the tab counts are live. */
function RepoCommits({
  repoId,
  path,
  active,
  selected,
  setSelected,
  onList,
}: {
  repoId: number;
  /** null: waiting for its turn to load. */
  path: string | null;
  active: boolean;
  selected: Set<string>;
  setSelected: (update: (prev: Set<string>) => Set<string>) => void;
  onList: (repoId: number, list: CommitList | undefined, error: boolean) => void;
}) {
  const [polling, setPolling] = useState(false);
  // What linking an author did to their earlier reviews (LinkAuthor), until the next link.
  const [linkNote, setLinkNote] = useState<string | null>(null);
  const commits = useApi<CommitList>(path, {
    keepPreviousData: true,
    refreshInterval: polling ? (active ? POLL_MS : POLL_HIDDEN_MS) : 0,
  });
  const list = commits.data;
  const failed = !!commits.error;
  useEffect(() => {
    setPolling(!!list?.commits.some((c) => c.review_status === "queued" || c.review_status === "running"));
    onList(repoId, list, failed);
    // onList is a fresh closure each render; only a new list (or error) is news.
  }, [list, failed, repoId]);
  const batchMax = list?.batch_max ?? 50;

  // The first list starts with the not-yet-reviewed commits selected (up to the batch limit); a
  // refresh only drops shas that stopped being reviewable, never re-checks boxes.
  const [primed, setPrimed] = useState(false);
  useEffect(() => {
    if (!list) return;
    const ok = new Set(list.commits.filter((c) => c.reviewable).map((c) => c.sha));
    if (!primed) {
      setSelected(() => new Set(list.commits.filter(fresh).slice(0, list.batch_max).map((c) => c.sha)));
      setPrimed(true);
    } else {
      setSelected((prev) => new Set([...prev].filter((sha) => ok.has(sha))));
    }
  }, [list]); // setSelected is a fresh closure each render

  const reviewable = (list?.commits ?? []).filter((c) => c.reviewable);
  const unreviewed = reviewable.filter((c) => !c.again);
  const reviewed = (list?.commits ?? []).filter((c) => c.review_status === "done").length;
  const chosen = reviewable.filter((c) => selected.has(c.sha));
  const allOn = unreviewed.length > 0 && unreviewed.slice(0, batchMax).every((c) => selected.has(c.sha));
  const allPicked = reviewable.length > 0 && reviewable.slice(0, batchMax).every((c) => selected.has(c.sha));
  const toggle = (sha: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(sha);
      else next.delete(sha);
      return next;
    });

  return (
    <div role="tabpanel" id={`repo-panel-${repoId}`} aria-labelledby={`repo-tab-${repoId}`} hidden={!active}>
      {!active ? null : !list ? (
        <div style={{ padding: "0 18px 16px", display: "grid", gap: 8 }}>
          <Loading error={commits.error} />
          {commits.error && (
            <div>
              <button type="button" className="sm" onClick={() => void commits.mutate()}>
                Thử lại
              </button>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="toolbar" style={{ margin: 0, padding: "0 18px 10px" }}>
            <span className="muted small">
              {`${list.commits.length} commit${list.truncated ? " (còn nữa, thu hẹp khoảng thời gian)" : ""}${list.reviewed_hidden ? ` · bỏ ${list.reviewed_hidden} đã review` : ""} · ${reviewed} đã review · ${reviewable.length} chọn được · tối đa ${batchMax} mỗi lần${list.hidden ? ` · ${list.hidden} của người ngoài phạm vi của bạn` : ""}${chosen.length > batchMax ? ` · đang chọn ${chosen.length}, bỏ bớt để gửi` : ""}`}
            </span>
            <span className="spacer" />
            {unreviewed.length > 0 && (
              <button
                type="button"
                className="sm"
                onClick={() => setSelected(() => (allOn ? new Set() : new Set(unreviewed.slice(0, batchMax).map((c) => c.sha))))}
              >
                {allOn ? "Bỏ chọn hết" : `Chọn ${Math.min(unreviewed.length, batchMax)} chưa review`}
              </button>
            )}
            {reviewable.length > unreviewed.length && (
              // Reviewed ones too: sending them asks for a confirmation that counts them by verdict.
              <button
                type="button"
                className="sm"
                title="Gồm cả commit đã có kết quả (review lại thành lần mới); commit bị bỏ qua không được chọn"
                onClick={() =>
                  setSelected(() =>
                    allPicked ? new Set() : new Set(reviewable.slice(0, batchMax).map((c) => c.sha)),
                  )
                }
              >
                {allPicked ? "Bỏ chọn tất cả" : `Chọn tất cả ${Math.min(reviewable.length, batchMax)} (cả đã review)`}
              </button>
            )}
            {commits.error && <span className="error small">Không làm mới được: {commits.error.message}</span>}
            {linkNote && (
              <span className="muted small" role="status">
                {linkNote}
              </span>
            )}
          </div>
          {list.commits.length === 0 ? (
            <EmptyState icon="inbox" title="Không có commit nào trong khoảng này">
              Thử branch khác, khoảng thời gian rộng hơn, hoặc bỏ lọc người.
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="commit-table">
                <thead>
                  <tr>
                    <th className="select-col">
                      <input
                        type="checkbox"
                        aria-label="Chọn mọi commit chưa review"
                        checked={allOn}
                        disabled={unreviewed.length === 0}
                        onChange={(e) =>
                          setSelected(() => (e.target.checked ? new Set(unreviewed.slice(0, batchMax).map((c) => c.sha)) : new Set()))
                        }
                      />
                    </th>
                    <th>Commit</th>
                    <th>Tác giả</th>
                    <th>Khi nào</th>
                    <th className="num hide-sm">+ / −</th>
                    <th>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {list.commits.map((c) => (
                    <tr key={c.sha} className={c.reviewable ? undefined : "muted"}>
                      <td className="select-col">
                        <input
                          type="checkbox"
                          aria-label={c.again ? `Review lại ${c.short_sha}` : `Chọn ${c.short_sha}`}
                          title={c.again ? "Đã review: chọn để review lại thành lần mới" : undefined}
                          checked={selected.has(c.sha)}
                          disabled={!c.reviewable}
                          onChange={(e) => toggle(c.sha, e.target.checked)}
                        />
                      </td>
                      <td>
                        {c.web_url ? (
                          <a href={c.web_url} target="_blank" rel="noreferrer" title="Mở commit trên GitLab">
                            <code>{c.short_sha}</code>
                          </a>
                        ) : (
                          <code>{c.short_sha}</code>
                        )}{" "}
                        <span title={c.title}>{c.title}</span>
                      </td>
                      <td>
                        {c.user_id ? (
                          <Link href={`/members/${c.user_id}`}>{c.user_name ?? c.author_name}</Link>
                        ) : (
                          <span className="muted" title="Chưa gắn với người nào trong CodePulse">
                            {c.author_name ?? "—"} <Icon name="alertCircle" size={12} />
                          </span>
                        )}
                        {!c.user_id && c.author_email && (
                          <>
                            {" "}
                            <LinkAuthor
                              email={c.author_email}
                              name={c.author_name}
                              onDone={(note) => {
                                setLinkNote(note);
                                void commits.mutate();
                              }}
                            />
                          </>
                        )}
                      </td>
                      <td className="muted" title={c.authored_at ? dateTime(c.authored_at) : undefined}>
                        {c.authored_at ? relative(c.authored_at) : "—"}
                      </td>
                      <td className="num hide-sm">
                        <span className="good">+{c.additions ?? 0}</span> <span className="bad">−{c.deletions ?? 0}</span>
                      </td>
                      <td>
                        <StatusCell c={c} />
                        {c.again && selected.has(c.sha) && (
                          <>
                            {" "}
                            <Pill tone="warn" icon="sync">
                              Sẽ review lại
                            </Pill>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
