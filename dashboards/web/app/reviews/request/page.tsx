"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { LinkAuthor } from "@/components/LinkAuthor";
import { Alert, Card, EmptyState, Loading, PageHeader, Pill, Segmented } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, dateTime, localDay, relative } from "@/lib/format";
import type { Branch, CommitList, CommitRow, Person, Repo, ReviewCommitsResult } from "@/lib/types";

type Window = "1" | "3" | "7" | "14" | "30" | "custom";
const WINDOWS: { value: Window; label: string }[] = [
  { value: "1", label: "Hôm nay" },
  { value: "3", label: "3 ngày" },
  { value: "7", label: "7 ngày" },
  { value: "14", label: "14 ngày" },
  { value: "30", label: "30 ngày" },
  { value: "custom", label: "Tuỳ chọn" },
];
const ALL = "all";
const POLL_MS = 10_000;

function sinceOf(window: Window, from: string, to: string): { since?: string; until?: string } {
  if (window === "custom") {
    return {
      since: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
      until: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
    };
  }
  const days = Number(window);
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (days - 1));
  return { since: start.toISOString() };
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
  if (c.review_status === "failed")
    return (
      <Link href={`/reviews/${c.review_id}`}>
        <Pill tone="bad">Lỗi, review lại được</Pill>
      </Link>
    );
  if (c.review_status === "skipped")
    return (
      <Link href={`/reviews/${c.review_id}`}>
        <Pill tone="warn">Đã bỏ qua</Pill>
      </Link>
    );
  if (c.is_merge) return <span className="muted">Merge commit</span>;
  if (!c.reviewable) return <span className="muted">{c.reason}</span>;
  return <span className="muted">Chưa review</span>;
}

/** Pick repo, branch, people and a window; see the commits; queue reviews (docs/specs/17). */
export default function RequestReviewPage() {
  const repos = useApi<Repo[]>("/repos");
  const people = useApi<Person[]>("/people");
  const [bp, setBp] = useState(ALL);
  const [repoId, setRepoId] = useState<number | null>(null);
  const [branch, setBranch] = useState("");
  const [branchSearch, setBranchSearch] = useState("");
  const [who, setWho] = useState<Set<number>>(new Set()); // empty = everyone I may review
  const [window_, setWindow] = useState<Window>("7");
  const [from, setFrom] = useState(localDay(new Date(Date.now() - 6 * 86400000)));
  const [to, setTo] = useState(localDay(new Date()));
  const [query, setQuery] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<ReviewCommitsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
      setRepoId(wanted.id);
      setBranch(wanted.review_branch ?? wanted.default_branch ?? "");
    }
  }, [arrived, repos.data]);
  const visibleRepos = (repos.data ?? []).filter((r) => bp === ALL || String(r.business_project_id ?? "none") === bp);
  const repo = (repos.data ?? []).find((r) => r.id === repoId) ?? null;

  // Branch names for the datalist: the default branch first; typing searches GitLab.
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(branchSearch.trim()), 300);
    return () => clearTimeout(t);
  }, [branchSearch]);
  const branches = useApi<Branch[]>(repo ? `/repos/${repo.id}/branches${qs({ search: debounced || null })}` : null);

  // One subscription; it polls only while a review of the list is queued or running.
  const [polling, setPolling] = useState(false);
  const commits = useApi<CommitList>(query, { keepPreviousData: true, refreshInterval: polling ? POLL_MS : 0 });
  const list = commits.data;
  useEffect(() => {
    setPolling(!!list?.commits.some((c) => c.review_status === "queued" || c.review_status === "running"));
  }, [list]);
  const batchMax = list?.batch_max ?? 50;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!repo || !branch.trim()) return;
    setResult(null);
    setError(null);
    const params = new URLSearchParams();
    params.set("ref", branch.trim());
    const { since, until } = sinceOf(window_, from, to);
    if (since) params.set("since", since);
    if (until) params.set("until", until);
    for (const id of who) params.append("user_id", String(id));
    params.set("limit", "200");
    const next = `/repos/${repo.id}/commits?${params}`;
    if (next === query) void commits.mutate(); // same scope again = refresh
    else {
      setSelected(new Set());
      setQuery(next);
    }
  }

  // A new scope starts with the reviewable commits selected (up to the batch limit); a refresh
  // of the same scope only drops shas that stopped being reviewable, never re-checks boxes.
  const [selectedFor, setSelectedFor] = useState<string | null>(null);
  useEffect(() => {
    if (!list || !query) return;
    const ok = new Set(list.commits.filter((c) => c.reviewable).map((c) => c.sha));
    if (selectedFor !== query) {
      setSelected(new Set([...ok].slice(0, list.batch_max)));
      setSelectedFor(query);
    } else {
      setSelected((prev) => new Set([...prev].filter((sha) => ok.has(sha))));
    }
  }, [list, query, selectedFor]);

  async function queue() {
    if (!repo || !list) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.post<ReviewCommitsResult>(`/repos/${repo.id}/review-commits`, {
        ref: list.ref,
        shas: [...selected],
      });
      setResult(out);
      setSelected(new Set());
      await commits.mutate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const reviewable = (list?.commits ?? []).filter((c) => c.reviewable);
  const reviewed = (list?.commits ?? []).filter((c) => c.review_status === "done").length;
  const chosen = reviewable.filter((c) => selected.has(c.sha));
  const toggle = (sha: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(sha);
      else next.delete(sha);
      return next;
    });

  return (
    <>
      <PageHeader
        eyebrow="Review"
        title="Yêu cầu review"
        subtitle="Chọn repo, branch, người và khoảng thời gian; review chạy trong nền và lưu vào dashboard như review tự động. Chỉ repo thuộc dự án bạn được cấp quyền; trong đó bạn review được việc của mình, lead thêm người mình lead, người quản lý dự án và admin mọi người."
      />

      <Card title="Phạm vi">
        {!repos.data || !people.data ? (
          <Loading error={repos.error ?? people.error} />
        ) : repos.data.length === 0 ? (
          <EmptyState icon="folder" title="Chưa có repo nào bạn được yêu cầu review">
            Chỉ repo thuộc dự án bạn được cấp quyền mới hiện ở đây; liên hệ admin nếu thiếu.
          </EmptyState>
        ) : (
          <form onSubmit={submit} className="request-form" style={{ display: "grid", gap: 14 }}>
            <div className="toolbar" style={{ margin: 0 }}>
              <select
                value={bp}
                onChange={(e) => {
                  setBp(e.target.value);
                  setRepoId(null);
                  setBranch("");
                }}
                aria-label="Dự án"
              >
                <option value={ALL}>Mọi dự án</option>
                {projects.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
              <select
                value={repoId ?? ""}
                onChange={(e) => {
                  const next = (repos.data ?? []).find((r) => r.id === Number(e.target.value)) ?? null;
                  setRepoId(next?.id ?? null);
                  setBranch(next?.review_branch ?? next?.default_branch ?? "");
                  setBranchSearch("");
                }}
                aria-label="Repo"
                required
                style={{ flex: "1 1 260px" }}
              >
                <option value="">Chọn repo…</option>
                {visibleRepos.map((r) => (
                  <option key={r.id} value={r.id} disabled={!r.review_enabled}>
                    {r.path_with_namespace}
                    {r.review_enabled ? "" : " (đã tắt review)"}
                  </option>
                ))}
              </select>
              <input
                list="branch-options"
                value={branch}
                onChange={(e) => {
                  setBranch(e.target.value);
                  setBranchSearch(e.target.value === "*" ? "" : e.target.value);
                }}
                placeholder={repo ? "Branch…" : "Chọn repo trước"}
                aria-label="Branch"
                disabled={!repo}
                required
                autoComplete="off"
                spellCheck={false}
                style={{ flex: "1 1 220px" }}
              />
              <datalist id="branch-options">
                <option value="*">mọi nhánh</option>
                {(branches.data ?? []).map((b) => (
                  <option key={b.name} value={b.name}>
                    {b.default ? "mặc định" : b.last_commit_at ? relative(b.last_commit_at) : ""}
                  </option>
                ))}
              </datalist>
            </div>
            {branches.error && (
              <span className="error small" role="status">
                Không lấy được danh sách branch: {branches.error.message}
              </span>
            )}

            <div className="toolbar" style={{ margin: 0, alignItems: "flex-start" }}>
              <div style={{ display: "grid", gap: 6 }}>
                <span className="muted small">Người (bỏ trống = mọi người bạn được review)</span>
                <div className="chips" style={{ margin: 0 }}>
                  {people.data.map((p) => {
                    const on = who.has(p.id);
                    return (
                      <label key={p.id} className={`chip choice${on ? " on" : ""}`}>
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={(e) =>
                            setWho((prev) => {
                              const next = new Set(prev);
                              if (e.target.checked) next.add(p.id);
                              else next.delete(p.id);
                              return next;
                            })
                          }
                        />
                        {p.full_name ?? p.primary_email}
                      </label>
                    );
                  })}
                  {who.size > 0 && (
                    <button type="button" className="link" onClick={() => setWho(new Set())}>
                      Bỏ chọn
                    </button>
                  )}
                </div>
              </div>
              <span className="spacer" />
              <div style={{ display: "grid", gap: 6 }}>
                <span className="muted small">Khoảng thời gian (theo ngày commit)</span>
                <div className="toolbar" style={{ margin: 0 }}>
                  <Segmented label="Khoảng thời gian" value={window_} onChange={setWindow} options={WINDOWS} />
                  {window_ === "custom" && (
                    <>
                      <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Từ ngày" />
                      <input type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Đến ngày" />
                    </>
                  )}
                </div>
              </div>
            </div>

            <div className="toolbar" style={{ margin: 0 }}>
              <button className="primary" type="submit" disabled={!repo || !branch.trim() || commits.isLoading}>
                {commits.isLoading ? "Đang lấy commit…" : "Xem commit"}
              </button>
              <span className="muted small">Danh sách lấy thẳng từ GitLab, tối đa 200 commit mỗi lần.</span>
            </div>
          </form>
        )}
      </Card>

      {query && (
        <Card
          title={list ? `Commit trên ${list.ref}` : "Commit"}
          subtitle={
            list
              ? `${list.commits.length} commit${list.truncated ? " (còn nữa, thu hẹp khoảng thời gian)" : ""} · ${reviewed} đã review · ${reviewable.length} chọn được · tối đa ${batchMax} mỗi lần${list.hidden ? ` · ${list.hidden} của người ngoài phạm vi của bạn` : ""}${chosen.length > batchMax ? ` · đang chọn ${chosen.length}, bỏ bớt để gửi` : ""}`
              : undefined
          }
          actions={
            list && reviewable.length > 0 ? (
              <>
                <button
                  type="button"
                  onClick={() =>
                    setSelected(
                      chosen.length >= Math.min(reviewable.length, batchMax)
                        ? new Set()
                        : new Set(reviewable.slice(0, batchMax).map((c) => c.sha)),
                    )
                  }
                >
                  {chosen.length >= Math.min(reviewable.length, batchMax) ? "Bỏ chọn hết" : `Chọn ${Math.min(reviewable.length, batchMax)} đầu`}
                </button>
                <button
                  className="primary"
                  type="button"
                  disabled={busy || chosen.length === 0 || chosen.length > batchMax}
                  title={chosen.length > batchMax ? `Tối đa ${batchMax} commit mỗi lần` : undefined}
                  onClick={() => void queue()}
                >
                  {busy ? "Đang đưa vào hàng đợi…" : `Review ${chosen.length} commit`}
                </button>
              </>
            ) : undefined
          }
          flush
        >
          {result && (
            <div style={{ padding: "0 18px" }}>
              <Alert tone={result.queued.length ? "success" : "warning"} onClose={() => setResult(null)}>
                Đã đưa <strong>{result.queued.length}</strong> commit vào hàng đợi review
                {result.skipped.length ? `, bỏ qua ${result.skipped.length}: ${result.skipped.map((s) => `${s.sha.slice(0, 8)} (${s.reason})`).join(", ")}` : ""}
                . Kết quả hiện ở cột trạng thái và trên dashboard sau vài phút.
              </Alert>
            </div>
          )}
          {error && (
            <div style={{ padding: "0 18px" }}>
              <Alert tone="error" onClose={() => setError(null)}>
                {error}
              </Alert>
            </div>
          )}
          {!list ? (
            <div style={{ padding: "0 18px 16px" }}>
              <Loading error={commits.error} />
            </div>
          ) : list.commits.length === 0 ? (
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
                        aria-label="Chọn mọi commit review được"
                        checked={reviewable.length > 0 && chosen.length >= Math.min(reviewable.length, batchMax)}
                        disabled={reviewable.length === 0}
                        onChange={(e) =>
                          setSelected(e.target.checked ? new Set(reviewable.slice(0, batchMax).map((c) => c.sha)) : new Set())
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
                          aria-label={`Chọn ${c.short_sha}`}
                          checked={selected.has(c.sha)}
                          disabled={!c.reviewable}
                          onChange={(e) => toggle(c.sha, e.target.checked)}
                        />
                      </td>
                      <td>
                        <code>{c.short_sha}</code> <span title={c.title}>{c.title}</span>
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
                            <LinkAuthor email={c.author_email} name={c.author_name} onDone={() => void commits.mutate()} />
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
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
