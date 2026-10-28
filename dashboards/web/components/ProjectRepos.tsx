"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Score, scoreTone } from "@/components/Evaluation";
import { Icon } from "@/components/icons";
import { KnowledgeTile } from "@/components/Knowledge";
import { ReviewResults } from "@/components/ReviewResults";
import { Card, Drawer, EmptyState, Pager, Pill, Segmented, SortTh, StatStrip, StripStat, useSort } from "@/components/ui";
import { type BoardScope, boardLink, qs, useApi } from "@/lib/api";
import { date, num, relative } from "@/lib/format";
import type { KnowledgeSummary, Me, ProjectRepoRow } from "@/lib/types";

/*
 * The repos of a dự án, built for 1 to 100+ of them: a map that shows the structure (grouped by
 * sub-namespace, one small tile per repo, coloured by what needs attention) or a compact sortable
 * table, both filtered by the same chips and search. Everything about one repo (its numbers,
 * premises and reviews) opens in a side drawer, so the page itself stays an overview.
 */

const PAGE = 20;
const VIEW_STORE = "cp:repo-view";

type Health = "critical" | "high" | "ok" | "idle";
type Filter = "all" | Health | "kn_missing" | "kn_pending";
type View = "map" | "table";

const HEALTH_LABEL: Record<Health, string> = {
  critical: "Critical đang mở",
  high: "High đang mở",
  ok: "Ổn",
  idle: "Không review trong kỳ",
};

function health(r: ProjectRepoRow): Health {
  if (r.open_critical > 0) return "critical";
  if (r.open_high > 0) return "high";
  return r.reviews > 0 ? "ok" : "idle";
}

/** The namespace every repo shares ("vihat/omicrm/"), so rows show only what tells them apart. */
function sharedPrefix(paths: string[]): string {
  if (paths.length === 0) return "";
  const parts = paths.map((p) => p.split("/").slice(0, -1));
  const first = parts[0];
  let n = 0;
  while (n < first.length && parts.every((p) => p[n] === first[n])) n++;
  return n ? `${first.slice(0, n).join("/")}/` : "";
}

function splitPath(path: string, prefix: string): { group: string; name: string } {
  const rest = path.startsWith(prefix) ? path.slice(prefix.length) : path;
  const cut = rest.lastIndexOf("/");
  return cut < 0 ? { group: "", name: rest } : { group: rest.slice(0, cut), name: rest.slice(cut + 1) };
}

function readView(): View | null {
  try {
    const v = window.localStorage.getItem(VIEW_STORE);
    return v === "map" || v === "table" ? v : null;
  } catch {
    return null;
  }
}

function writeView(v: View) {
  try {
    window.localStorage.setItem(VIEW_STORE, v);
  } catch {
    /* blocked storage: the choice just does not persist */
  }
}

/** The repo whose drawer is open lives in the URL (?repo=), so a link or a reload keeps it. */
function useRepoParam(): [number | null, (id: number | null) => void] {
  const [id, setId] = useState<number | null>(null);
  useEffect(() => {
    const raw = new URL(window.location.href).searchParams.get("repo");
    setId(raw && /^\d+$/.test(raw) ? Number(raw) : null);
  }, []);
  const update = (next: number | null) => {
    setId(next);
    const url = new URL(window.location.href);
    if (next === null) url.searchParams.delete("repo");
    else url.searchParams.set("repo", String(next));
    window.history.replaceState(null, "", url);
  };
  return [id, update];
}

/* ---------- small cells ---------- */

/** Critical / high as two small chips that open the findings; zeros fade. */
function SevPair({ critical, high, hrefC, hrefH, what }: { critical: number; high: number; hrefC: string; hrefH: string; what: string }) {
  const chip = (n: number, sev: "critical" | "high", href: string) => {
    const cls = `sev-count ${sev}${n ? "" : " zero"}`;
    const label = `${n} ${sev} ${what}`;
    return n ? (
      <Link href={href} className={cls} title={label} aria-label={label} onClick={(e) => e.stopPropagation()}>
        {n}
      </Link>
    ) : (
      <span className={cls} title={label} aria-label={label}>
        {n}
      </span>
    );
  };
  return (
    <span className="sev-row">
      {chip(critical, "critical", hrefC)}
      {chip(high, "high", hrefH)}
    </span>
  );
}

function knowledgeTitle(k: KnowledgeSummary): string {
  const parts = [`Tiền đề v${k.version}`, k.approved ? "đã duyệt" : "chưa duyệt"];
  if (k.counts) parts.push(`${k.counts.invariants} bất biến · ${k.counts.checks} check · ${k.counts.severity_rules} quy tắc severity`);
  if (k.building) parts.push("đang dựng bản mới");
  if (k.failed) parts.push("lần dựng mới nhất lỗi");
  return parts.join(" · ");
}

/** The premises of one repo in one chip: version and approval, or why there is none. */
export function KnowledgeChip({ k }: { k: KnowledgeSummary | undefined }) {
  if (!k) return <span className="faint">…</span>;
  if (k.version === null) {
    if (k.building) return <Pill tone="info">Đang dựng</Pill>;
    if (k.failed) return <Pill tone="bad" icon="alertCircle" title="Lần dựng tiền đề gần nhất lỗi; mở repo để xem lý do">Lỗi dựng</Pill>;
    return <span className="faint" title="Chưa có tiền đề: lần review sâu đầu tiên sẽ tự dựng">Chưa có</span>;
  }
  const alert = k.failed || k.building;
  return (
    <Pill tone={k.approved ? "good" : "warn"} icon={k.failed ? "alert" : k.approved ? "check" : undefined} title={knowledgeTitle(k)}>
      v{k.version}
      {alert && <span className="visually-hidden">, {k.failed ? "lần dựng mới nhất lỗi" : "đang dựng bản mới"}</span>}
      {!k.approved && <span className="visually-hidden">, chưa duyệt</span>}
    </Pill>
  );
}

/* ---------- views ---------- */

type Row = ProjectRepoRow & { group: string; name: string; health: Health; knowledge: KnowledgeSummary | undefined };

function repoScope(scope: BoardScope, r: Row, period: boolean): BoardScope {
  const repo = { id: r.id, path: r.path_with_namespace };
  return period ? { ...scope, repo } : { project: scope.project, repo };
}

function RepoMap({ rows, onOpen }: { rows: Row[]; onOpen: (id: number) => void }) {
  const groups = useMemo(() => {
    const by = new Map<string, Row[]>();
    for (const r of rows) by.set(r.group, [...(by.get(r.group) ?? []), r]);
    return [...by.entries()].sort(([a], [b]) => a.localeCompare(b, "vi"));
  }, [rows]);
  const grouped = groups.length > 1 || (groups[0] && groups[0][0] !== "");
  return (
    <div className="repo-map-wrap">
      {groups.map(([group, items]) => (
        <section key={group || "_"} className="repo-group" aria-label={group || "Repo"}>
          {grouped && (
            <h3 className="repo-group-head">
              <Icon name="folder" size={13} />
              {group || "(gốc)"}
              <span className="faint">{items.length}</span>
            </h3>
          )}
          <ul className="repo-map">
            {items.map((r) => {
              const open = r.open_critical + r.open_high;
              const label = `${r.path_with_namespace}: ${HEALTH_LABEL[r.health]} · ${r.reviews} review · điểm ${r.avg_score === null ? "—" : num(r.avg_score, 1)} · mở ${r.open_critical} critical, ${r.open_high} high`;
              return (
                <li key={r.id}>
                  <button type="button" className={`repo-tile h-${r.health}${r.tracked ? "" : " untracked"}`} onClick={() => onOpen(r.id)} title={label} aria-label={label}>
                    <span className="t-name">{r.name}</span>
                    <span className="t-score">
                      {r.avg_score === null ? <span className="faint">—</span> : <span className={`score ${scoreTone(r.avg_score) ?? ""}`}>{num(r.avg_score, 0)}</span>}
                    </span>
                    <span className="t-meta">
                      {open ? (
                        <>
                          {r.open_critical > 0 && <span className="sev-count critical">{r.open_critical}</span>}
                          {r.open_high > 0 && <span className="sev-count high">{r.open_high}</span>}
                        </>
                      ) : (
                        <span>{r.reviews ? `${r.reviews} review` : "không review"}</span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

const cell = (r: Row, key: string): number | string | null => {
  switch (key) {
    case "path":
      return r.path_with_namespace;
    case "reviews":
      return r.reviews;
    case "score":
      return r.avg_score;
    case "new":
      return r.critical * 1000 + r.high;
    case "open":
      return r.open_critical * 1000 + r.open_high;
    case "knowledge":
      return r.knowledge?.version ?? null;
    default:
      return r.last_review_at;
  }
};

function RepoList({ rows, scope, onOpen }: { rows: Row[]; scope: BoardScope; onOpen: (id: number) => void }) {
  const { sorted, sort, toggle } = useSort(rows, { key: "open", dir: "desc" }, cell);
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [rows]);
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE));
  const shown = sorted.slice(page * PAGE, page * PAGE + PAGE);
  return (
    <>
      <div className="table-wrap">
        <table className="repo-list">
          <thead>
            <tr>
              <SortTh id="path" sort={sort} onSort={toggle}>
                Repo
              </SortTh>
              <SortTh id="reviews" sort={sort} onSort={toggle} num>
                Review
              </SortTh>
              <SortTh id="score" sort={sort} onSort={toggle} num>
                Điểm
              </SortTh>
              <SortTh id="new" sort={sort} onSort={toggle}>
                Mới C/H
              </SortTh>
              <SortTh id="open" sort={sort} onSort={toggle}>
                Mở C/H
              </SortTh>
              <SortTh id="knowledge" sort={sort} onSort={toggle}>
                Tiền đề
              </SortTh>
              <SortTh id="last" sort={sort} onSort={toggle}>
                Gần nhất
              </SortTh>
              <th aria-hidden="true" />
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className={`clickable${r.tracked ? "" : " muted"}`} onClick={() => onOpen(r.id)}>
                <td>
                  <button type="button" className="link-btn repo-name" onClick={(e) => (e.stopPropagation(), onOpen(r.id))} title={r.path_with_namespace}>
                    <span className={`health-dot h-${r.health}`} aria-hidden="true" />
                    {r.group && <span className="faint">{r.group}/</span>}
                    <strong>{r.name}</strong>
                  </button>
                  {!r.tracked && <span className="faint small"> · bỏ theo dõi</span>}
                  <span className="visually-hidden">, {HEALTH_LABEL[r.health]}</span>
                </td>
                <td className="num">{r.reviews || <span className="faint">0</span>}</td>
                <td className="num">
                  <Score value={r.avg_score} strong />
                </td>
                <td>
                  <SevPair critical={r.critical} high={r.high} what="mới trong kỳ" hrefC={boardLink("critical", repoScope(scope, r, true))} hrefH={boardLink("high", repoScope(scope, r, true))} />
                </td>
                <td>
                  <SevPair critical={r.open_critical} high={r.open_high} what="đang mở" hrefC={boardLink("critical", repoScope(scope, r, false))} hrefH={boardLink("high", repoScope(scope, r, false))} />
                </td>
                <td>{r.tracked ? <KnowledgeChip k={r.knowledge} /> : <span className="faint">—</span>}</td>
                <td className="muted nowrap" title={date(r.last_review_at)}>
                  {r.last_review_at ? relative(r.last_review_at) : <span className="faint">—</span>}
                </td>
                <td className="chev" aria-hidden="true">
                  <Icon name="chevronRight" size={14} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="results-foot">
          <span className="muted small">
            {page * PAGE + 1}–{Math.min(sorted.length, page * PAGE + PAGE)} / {sorted.length} repo
          </span>
          <Pager label={`Trang ${page + 1}/${pages}`} onPrev={() => setPage(page - 1)} onNext={() => setPage(page + 1)} prevDisabled={page === 0} nextDisabled={page >= pages - 1} />
        </div>
      )}
    </>
  );
}

/* ---------- the drawer ---------- */

function RepoDrawer({
  row,
  scope,
  me,
  period,
  businessProjectId,
  onClose,
}: {
  row: Row;
  scope: BoardScope;
  me: Me | undefined;
  period: { from: string; to: string; label: string };
  businessProjectId: number;
  onClose: () => void;
}) {
  return (
    <Drawer
      title={
        <>
          <span className={`health-dot h-${row.health}`} aria-hidden="true" /> {row.path_with_namespace}
        </>
      }
      onClose={onClose}
      width={960}
      actions={
        <>
          <span className="muted small" title={date(row.last_review_at)}>
            {row.last_review_at ? `Review gần nhất ${relative(row.last_review_at)}` : "Chưa review"}
          </span>
          <span className="pill" title="Số liệu của kỳ đang xem">
            {period.label}
          </span>
        </>
      }
    >
      <StatStrip label={`Số liệu của ${row.path_with_namespace}`}>
        <StripStat label="Review" value={row.reviews} />
        <StripStat label="Điểm TB" value={row.avg_score} digits={1} tone={scoreTone(row.avg_score) ?? undefined} />
        <StripStat label="Critical mới" value={row.critical} tone={row.critical ? "bad" : undefined} href={row.critical ? boardLink("critical", repoScope(scope, row, true)) : undefined} />
        <StripStat label="High mới" value={row.high} tone={row.high ? "warn" : undefined} href={row.high ? boardLink("high", repoScope(scope, row, true)) : undefined} />
        <StripStat label="Critical mở" title="Chưa xử lý, mọi thời kỳ" value={row.open_critical} tone={row.open_critical ? "bad" : undefined} href={row.open_critical ? boardLink("critical", repoScope(scope, row, false)) : undefined} />
        <StripStat label="High mở" title="Chưa xử lý, mọi thời kỳ" value={row.open_high} tone={row.open_high ? "warn" : undefined} href={row.open_high ? boardLink("high", repoScope(scope, row, false)) : undefined} />
      </StatStrip>
      {row.tracked ? (
        <div className="drawer-block">
          <KnowledgeTile repo={row} me={me} />
        </div>
      ) : (
        <p className="muted small">Repo đã bỏ theo dõi: không còn tiền đề review.</p>
      )}
      <div className="drawer-block">
        <ReviewResults businessProjectId={businessProjectId} repo={row.id} period={period} id="review-results-repo" />
      </div>
    </Drawer>
  );
}

/* ---------- the card ---------- */

export function ProjectRepos({
  repos,
  businessProjectId,
  scope,
  me,
  period,
}: {
  repos: ProjectRepoRow[];
  businessProjectId: number;
  scope: BoardScope;
  me: Me | undefined;
  period: { from: string; to: string; label: string };
}) {
  const summary = useApi<KnowledgeSummary[]>(`/knowledge/summary${qs({ business_project_id: businessProjectId })}`, {
    // While a build runs somewhere in the dự án, keep the chips current.
    refreshInterval: (latest?: KnowledgeSummary[]) => (latest?.some((k) => k.building) ? 5000 : 0),
  });
  const [view, setView] = useState<View>("map");
  useEffect(() => {
    const stored = readView();
    if (stored) setView(stored);
  }, []);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useRepoParam();

  const rows: Row[] = useMemo(() => {
    const prefix = sharedPrefix(repos.map((r) => r.path_with_namespace));
    const byId = new Map((summary.data ?? []).map((k) => [k.project_id, k]));
    return repos.map((r) => ({ ...r, ...splitPath(r.path_with_namespace, prefix), health: health(r), knowledge: byId.get(r.id) }));
  }, [repos, summary.data]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: rows.length, critical: 0, high: 0, ok: 0, idle: 0, kn_missing: 0, kn_pending: 0 };
    for (const r of rows) {
      c[r.health]++;
      if (r.tracked && r.knowledge?.version === null) c.kn_missing++;
      if (r.tracked && r.knowledge?.version != null && !r.knowledge.approved) c.kn_pending++;
    }
    return c;
  }, [rows]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (q && !r.path_with_namespace.toLowerCase().includes(q)) return false;
      if (filter === "all") return true;
      if (filter === "kn_missing") return r.tracked && r.knowledge?.version === null;
      if (filter === "kn_pending") return r.tracked && r.knowledge?.version != null && !r.knowledge.approved;
      return r.health === filter;
    });
  }, [rows, filter, query]);

  const open = openId === null ? undefined : rows.find((r) => r.id === openId);
  const chips: { value: Filter; label: ReactNode; dot?: Health }[] = [
    { value: "all", label: "Tất cả" },
    { value: "critical", label: "Critical mở", dot: "critical" },
    { value: "high", label: "High mở", dot: "high" },
    { value: "ok", label: "Ổn", dot: "ok" },
    { value: "idle", label: "Không review", dot: "idle" },
    ...(summary.data
      ? ([
          { value: "kn_missing", label: "Chưa có tiền đề" },
          { value: "kn_pending", label: "Tiền đề chưa duyệt" },
        ] as const)
      : []),
  ];

  return (
    <>
      <Card
        id="project-repos"
        title={`Repo · ${repos.length}`}
        actions={
          <Segmented<View>
            label="Kiểu xem"
            value={view}
            onChange={(v) => {
              setView(v);
              writeView(v);
            }}
            options={[
              { value: "map", label: "Bản đồ" },
              { value: "table", label: "Bảng" },
            ]}
          />
        }
        flush
      >
        {repos.length === 0 ? (
          <EmptyState icon="folder" title="Dự án chưa có repo" />
        ) : (
          <>
            <div className="repo-toolbar">
              <label className="search-box">
                <Icon name="search" size={14} />
                <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm repo…" aria-label="Tìm repo" />
              </label>
              <div className="filter-chips" role="group" aria-label="Lọc repo">
                {chips.map((c) => (
                  <button
                    key={c.value}
                    type="button"
                    className={`fchip${filter === c.value ? " active" : ""}`}
                    aria-pressed={filter === c.value}
                    onClick={() => setFilter(filter === c.value && c.value !== "all" ? "all" : c.value)}
                    disabled={c.value !== "all" && counts[c.value] === 0 && filter !== c.value}
                    title={c.dot ? HEALTH_LABEL[c.dot] : undefined}
                  >
                    {c.dot && <span className={`health-dot h-${c.dot}`} aria-hidden="true" />}
                    {c.label}
                    <span className="n">{counts[c.value]}</span>
                  </button>
                ))}
              </div>
            </div>
            {visible.length === 0 ? (
              <EmptyState icon="search" title="Không có repo nào khớp">
                <button type="button" className="sm ghost" onClick={() => (setFilter("all"), setQuery(""))}>
                  Bỏ lọc
                </button>
              </EmptyState>
            ) : view === "map" ? (
              <RepoMap rows={visible} onOpen={setOpenId} />
            ) : (
              <RepoList rows={visible} scope={scope} onOpen={setOpenId} />
            )}
          </>
        )}
      </Card>
      {open && <RepoDrawer row={open} scope={scope} me={me} period={period} businessProjectId={businessProjectId} onClose={() => setOpenId(null)} />}
    </>
  );
}
