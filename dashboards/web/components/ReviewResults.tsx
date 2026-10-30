"use client";

import Link from "next/link";
import { useState } from "react";
import useSWRInfinite from "swr/infinite";

import { RangePicker, type SharedRange, useCardRange } from "@/components/Evaluation";
import { announceReviewQueued } from "@/components/ReviewActivity";
import { Alert, Avatar, Card, EmptyState, ExternalLink, Loading, Pill } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, dateTime, num } from "@/lib/format";
import type { Page, Person, RerunManyOut, RerunnableOut, ReviewListItem, Verdict } from "@/lib/types";

const PAGE = 25;
// POST /reviews/rerun takes this many ids at a time.
const BATCH = 100;

const STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  failed: { label: "Lỗi", tone: "bad" },
  skipped: { label: "Bỏ qua", tone: "warn" },
  queued: { label: "Đang chờ", tone: "info" },
  running: { label: "Đang chạy", tone: "info" },
};

type StatusFilter = "results" | "all" | "done" | "in_progress" | "failed" | "skipped" | "problems";
// "Kết quả": what a review ended with (older rounds of the same change are left out).
const STATUSES: Record<StatusFilter, string[]> = {
  results: ["done", "failed", "skipped"],
  all: ["done", "failed", "skipped", "queued", "running"],
  done: ["done"],
  in_progress: ["queued", "running"],
  failed: ["failed"],
  skipped: ["skipped"],
  problems: ["failed", "skipped"],
};
// While a review shown is queued or running, the list refreshes itself this often.
const POLL_MS = 10_000;
const DEEP_MODES = new Set(["agent", "deep"]);

/**
 * Review results of a dự án (or of everything the viewer may see), filtered by time, member,
 * repo, verdict and status, newest first, 25 at a time.
 */
export function ReviewResults({
  businessProjectId,
  repos,
  repo,
  person,
  range: shared,
  period,
  id = "review-results",
  defaultCollapsed,
  projects,
  title = "Kết quả review",
}: {
  businessProjectId?: number;
  /** The dự án offered by a dự án filter (the shared /reviews page; shown when there is more than one). */
  projects?: { id: number; name: string }[];
  title?: string;
  /** One repo's results (the repo drawer of the dự án page): no repo filter, no repo column. */
  repo?: number;
  /** Key the card's fold state is remembered under (pages that fold it by default use their own). */
  id?: string;
  defaultCollapsed?: boolean;
  /** The repos offered by the repo filter (shown when there is more than one). */
  repos?: { id: number; path_with_namespace: string; business_project_id?: number | null }[];
  /** One person's results (the member page): no member filter, no member column. */
  person?: number;
  /** The page's range, when other cards follow the same one. */
  range?: SharedRange;
  /** A fixed period chosen elsewhere on the page (the dự án page's week / month): no picker. */
  period?: { from: string; to: string; label: string };
}) {
  const [own, setRange] = useCardRange(shared);
  const range = period ? { preset: "custom" as const, from: period.from, to: period.to } : own;
  const [userId, setUserId] = useState("");
  // Picked by clicking a name: may be someone /people leaves out (deactivated since).
  const [picked, setPicked] = useState<{ id: string; name: string } | null>(null);
  const [bpId, setBpId] = useState("");
  const [repoId, setRepoId] = useState("");
  const [verdict, setVerdict] = useState<"" | Verdict>("");
  const [status, setStatus] = useState<StatusFilter>("results");
  const [mode, setMode] = useState<"" | "fast" | "deep">("");
  // Re-run several at once: only rows the API marks can_rerun (the viewer manages them).
  const [chosen, setChosen] = useState<Set<number>>(() => new Set());
  // "Chọn tất cả" past the loaded pages: every match of the filters `base` was, from the API.
  const [everyMatch, setEveryMatch] = useState<(RerunnableOut & { base: string }) | null>(null);
  const [finding, setFinding] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<{ tone: "success" | "warning" | "error"; text: string } | null>(null);
  // Everyone whose reviews the viewer may see (developer: themselves; lead: the team; admin: all).
  const { data: people } = useApi<Person[]>(person === undefined ? "/people" : null);

  const base =
    range && range.from && range.to
      ? `/reviews${qs({
          from: range.from,
          to: range.to,
          business_project_id: businessProjectId ?? bpId,
          project_id: repo ?? repoId,
          user_id: person ?? userId,
          verdict,
          mode,
          limit: PAGE,
        })}${STATUSES[status].map((s) => `&status=${s}`).join("")}`
      : null;
  const pages = useSWRInfinite<Page<ReviewListItem>, ApiError>(
    (index, previous) => {
      if (!base) return null;
      if (index === 0) return base;
      return previous?.next_cursor ? `${base}&cursor=${encodeURIComponent(previous.next_cursor)}` : null;
    },
    (path: string) => api.get<Page<ReviewListItem>>(path),
    {
      keepPreviousData: true,
      shouldRetryOnError: (e: ApiError) => !e.status || e.status >= 500,
      // Follow reviews still queued or running until they end.
      refreshInterval: (loaded) =>
        loaded?.some((p) => p.items.some((r) => r.status === "queued" || r.status === "running")) ? POLL_MS : 0,
    },
  );
  const items = pages.data?.flatMap((p) => p.items) ?? [];
  const last = pages.data?.at(-1);
  const loadingMore = pages.isValidating && pages.size > (pages.data?.length ?? 0);
  const filtered = !!(bpId || userId || repoId || verdict || mode || status !== "results");
  // The repo filter follows the chosen dự án.
  const repoChoices = bpId ? (repos ?? []).filter((r) => String(r.business_project_id) === bpId) : repos;
  // Nobody else to pick: the member page, or a developer who only sees themselves.
  const onlySelf = person !== undefined || people?.length === 1;

  const nameOf = (p: { id: number; full_name: string | null; primary_email: string | null }) =>
    p.full_name ?? p.primary_email ?? `#${p.id}`;
  const listed = !userId || (people ?? []).some((p) => String(p.id) === userId);
  // #4: ask for exactly one more page than loaded, also after a failed one.
  const loadMore = () => void pages.setSize((pages.data?.length ?? 0) + 1);
  const selectable = items.filter((r) => r.can_rerun);
  // Rows that left the list (filter changed) are no longer chosen.
  const selected = selectable.filter((r) => chosen.has(r.id));
  const allSelected = selectable.length > 0 && selected.length === selectable.length;
  // Valid only for the filters it was asked under; changing one drops it.
  const all = everyMatch && everyMatch.base === base ? everyMatch : null;
  const count = all ? all.ids.length : selected.length;
  const clear = () => {
    setChosen(new Set());
    setEveryMatch(null);
  };
  const chooseEveryMatch = async () => {
    if (!base) return;
    setFinding(true);
    setSent(null);
    try {
      const out = await api.get<RerunnableOut>(base.replace("/reviews?", "/reviews/rerunnable?"));
      setEveryMatch({ ...out, base });
    } catch (e) {
      setSent({ tone: "error", text: `Không lấy được danh sách: ${e instanceof ApiError ? e.message : String(e)}` });
    } finally {
      setFinding(false);
    }
  };
  const toggle = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const rerun = async () => {
    const ids = all ? all.ids : selected.map((r) => r.id);
    const finished = all ? all.done : selected.filter((r) => r.status === "done").length;
    const ok = window.confirm(
      `Review lại ${ids.length} kết quả? Việc này tốn ngân sách LLM${
        finished ? `; ${finished} review đã chấm điểm sẽ có lần review mới, lần cũ được giữ làm lịch sử` : ""
      }.`,
    );
    if (!ok) return;
    setSending(true);
    setSent(null);
    const out: RerunManyOut = { queued: [], failed: [] };
    try {
      for (let i = 0; i < ids.length; i += BATCH) {
        const part = await api.post<RerunManyOut>("/reviews/rerun", { ids: ids.slice(i, i + BATCH) });
        out.queued.push(...part.queued);
        out.failed.push(...part.failed);
      }
      const why =
        out.failed
          .slice(0, 5)
          .map((f) => `#${f.id}: ${f.error}`)
          .join("; ") + (out.failed.length > 5 ? `; và ${out.failed.length - 5} review khác` : "");
      setSent(
        out.failed.length === 0
          ? { tone: "success", text: `Đã đưa ${out.queued.length} review vào hàng đợi; theo dõi ở góc phải màn hình.` }
          : {
              tone: out.queued.length ? "warning" : "error",
              text: `Đã đưa ${out.queued.length} review vào hàng đợi, ${out.failed.length} không chạy được (${why}).`,
            },
      );
      clear();
      if (out.queued.length) announceReviewQueued();
      await pages.mutate();
    } catch (e) {
      // Earlier batches of a long selection may be queued already: say so, and keep the list fresh.
      const done = out.queued.length ? `Đã đưa ${out.queued.length} review vào hàng đợi; phần còn lại ` : "";
      setSent({ tone: "error", text: `${done}Không gửi được: ${e instanceof ApiError ? e.message : String(e)}` });
      if (out.queued.length) {
        clear();
        announceReviewQueued();
        void pages.mutate();
      }
    } finally {
      setSending(false);
    }
  };
  const reset = () => {
    setBpId("");
    setMode("");
    setUserId("");
    setRepoId("");
    setVerdict("");
    setStatus("results");
  };

  return (
    <Card
      id={id}
      title={title}
      subtitle={period ? undefined : "Commit mới nhất trước; lọc theo thời gian commit"}
      actions={
        period ? (
          <span className="pill" title="Commit (MR: lúc mở) trong kỳ đang xem; đổi kỳ ở đầu trang">
            {period.label}
          </span>
        ) : (
          <RangePicker value={range} onChange={setRange} />
        )
      }
      defaultCollapsed={defaultCollapsed}
      flush
    >
      <div className="toolbar filters results-filters">
        {!onlySelf && (
          <label className="filter">
            <span className="small muted">Thành viên</span>
            <select value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="Lọc theo thành viên">
              <option value="">Mọi thành viên</option>
              {!listed && picked?.id === userId && <option value={picked.id}>{picked.name}</option>}
              {(people ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {nameOf(p)}
                </option>
              ))}
            </select>
          </label>
        )}
        {businessProjectId === undefined && projects && projects.length > 1 && (
          <label className="filter">
            <span className="small muted">Dự án</span>
            <select
              value={bpId}
              onChange={(e) => {
                setBpId(e.target.value);
                setRepoId("");
              }}
              aria-label="Lọc theo dự án"
            >
              <option value="">Mọi dự án</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {repo === undefined && repoChoices && repoChoices.length > 1 && (
          <label className="filter">
            <span className="small muted">Repo</span>
            <select value={repoId} onChange={(e) => setRepoId(e.target.value)} aria-label="Lọc theo repo">
              <option value="">Mọi repo</option>
              {repoChoices.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.path_with_namespace}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="filter">
          <span className="small muted">Kết luận</span>
          <select value={verdict} onChange={(e) => setVerdict(e.target.value as "" | Verdict)} aria-label="Lọc theo kết luận">
            <option value="">Mọi kết luận</option>
            {(Object.keys(VERDICT_LABEL) as Verdict[]).map((v) => (
              <option key={v} value={v}>
                {VERDICT_LABEL[v]}
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Trạng thái</span>
          <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)} aria-label="Lọc theo trạng thái">
            <option value="results">Mọi kết quả</option>
            <option value="all">Tất cả (cả đang chạy)</option>
            <option value="done">Hoàn thành</option>
            <option value="in_progress">Đang diễn ra</option>
            <option value="failed">Lỗi</option>
            <option value="skipped">Bỏ qua</option>
            <option value="problems">Lỗi hoặc bỏ qua</option>
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Kiểu review</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as "" | "fast" | "deep")}
            aria-label="Lọc theo kiểu review"
            title="Kiểu được ghi khi review xong; review đang chờ hoặc đang chạy tính là nhanh"
          >
            <option value="">Nhanh và sâu</option>
            <option value="fast">Nhanh (chỉ đọc diff)</option>
            <option value="deep">Sâu (mở mã nguồn)</option>
          </select>
        </label>
        <span className="spacer" />
        {filtered && (
          <button type="button" className="sm ghost" onClick={reset}>
            Bỏ lọc
          </button>
        )}
      </div>

      {(count > 0 || sent) && (
        <div className="results-error">
          {sent && (
            <Alert tone={sent.tone} onClose={() => setSent(null)}>
              {sent.text}
            </Alert>
          )}
          {count > 0 && (
            <>
              <span className="small">
                {all
                  ? `Đã chọn tất cả ${all.ids.length} review chạy lại được khớp bộ lọc${
                      all.truncated ? " (mới nhất trước; phần còn lại chọn lại sau lượt này)" : ""
                    }`
                  : `Đã chọn ${selected.length}`}
              </span>
              {!all && allSelected && last?.next_cursor && (
                <button type="button" className="sm ghost" onClick={() => void chooseEveryMatch()} disabled={finding || sending}>
                  {finding ? "Đang tìm…" : "Chọn tất cả khớp bộ lọc (cả trang chưa tải)"}
                </button>
              )}
              <button type="button" className="sm primary ink" onClick={() => void rerun()} disabled={sending || finding}>
                {sending ? "Đang gửi…" : `↻ Review lại ${count}`}
              </button>
              <button type="button" className="sm ghost" onClick={clear} disabled={sending}>
                Bỏ chọn
              </button>
            </>
          )}
        </div>
      )}
      {pages.error && (
        <div className="results-error">
          <Alert tone="error">
            Không tải được kết quả review: {pages.error.message}
            {pages.data ? ". Đang hiện danh sách trước." : ""}
          </Alert>
          <button type="button" className="sm" onClick={() => void pages.mutate()}>
            Thử lại
          </button>
        </div>
      )}
      {!pages.data ? (
        !pages.error && (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading rows={4} />
          </div>
        )
      ) : items.length === 0 ? (
        <EmptyState title="Không có review nào khớp bộ lọc">
          {filtered ? "Nới bộ lọc hoặc chọn khoảng thời gian dài hơn." : "Chọn khoảng thời gian dài hơn."}
        </EmptyState>
      ) : (
        <>
          <div className={`table-wrap${pages.isValidating && !loadingMore ? " eval-dim" : ""}`}>
            <table>
              <thead>
                <tr>
                  {selectable.length > 0 && (
                    <th className="check-col">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={() => {
                          setEveryMatch(null);
                          setChosen(allSelected ? new Set() : new Set(selectable.map((r) => r.id)));
                        }}
                        aria-label="Chọn mọi review chạy lại được"
                      />
                    </th>
                  )}
                  <th>Thay đổi</th>
                  {person === undefined && <th>Thành viên</th>}
                  {repo === undefined && <th>Repo</th>}
                  <th>Kết luận</th>
                  <th className="num">Điểm</th>
                  <th title="Lúc tạo commit; với MR là lúc mở MR. Danh sách xếp theo cột này, mới nhất trước">
                    Thời gian commit
                  </th>
                  <th>Review xong</th>
                  <th>
                    <span className="visually-hidden">Mở trên GitLab</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => {
                  const st = STATUS[r.status];
                  const who = r.author ? nameOf(r.author) : null;
                  const active = !!r.author && userId === String(r.author.id);
                  return (
                    <tr key={r.id}>
                      {selectable.length > 0 && (
                        <td className="check-col">
                          {r.can_rerun && (
                            <input
                              type="checkbox"
                              checked={chosen.has(r.id) || !!all?.ids.includes(r.id)}
                              onChange={() => {
                                setEveryMatch(null);
                                toggle(r.id);
                              }}
                              aria-label={`Chọn ${r.target} để review lại`}
                            />
                          )}
                        </td>
                      )}
                      <td>
                        <Link href={`/reviews/${r.id}`}>{r.target}</Link>
                        {r.round > 1 && <span className="muted small"> · vòng {r.round}</span>}
                        {r.status === "done" && DEEP_MODES.has(r.mode) && (
                          <>
                            {" "}
                            <Pill tone="primary" title="Review sâu: đã mở mã nguồn của repo để đối chứng">
                              Sâu
                            </Pill>
                          </>
                        )}
                        {r.status !== "done" && r.error && (
                          <div className="muted small" title={r.error}>
                            {r.error.length > 90 ? `${r.error.slice(0, 90)}…` : r.error}
                          </div>
                        )}
                      </td>
                      {person === undefined && (
                      <td>
                        {r.author ? (
                          <button
                            type="button"
                            className="link-btn cell-main"
                            onClick={() => {
                              if (!r.author || !who) return;
                              setPicked({ id: String(r.author.id), name: who });
                              setUserId(String(r.author.id));
                            }}
                            title={onlySelf || active ? undefined : `Chỉ xem review của ${who}`}
                            disabled={onlySelf || active}
                          >
                            <Avatar name={r.author.full_name} email={r.author.primary_email} size="sm" />
                            <span>{who}</span>
                          </button>
                        ) : (
                          <span className="muted">Chưa gắn người</span>
                        )}
                      </td>
                      )}
                      {repo === undefined && <td className="muted">{r.project ?? "—"}</td>}
                      <td>{r.verdict ? <span className={`verdict ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span> : <span className="muted">—</span>}</td>
                      <td className="num">
                        {r.status === "done" ? <strong>{num(r.score, 0)}</strong> : <Pill tone={st?.tone}>{st?.label ?? r.status}</Pill>}
                      </td>
                      <td className="nowrap">{r.changed_at ? dateTime(r.changed_at) : <span className="muted">—</span>}</td>
                      <td className="muted nowrap">{dateTime(r.finished_at)}</td>
                      <td>{r.web_url && <ExternalLink href={r.web_url}>GitLab</ExternalLink>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="results-foot">
            <span className="muted small">
              {items.length} review{last?.next_cursor ? " (còn nữa)" : ""}
            </span>
            {last?.next_cursor && (
              <button type="button" className="sm" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Đang tải…" : "Tải thêm"}
              </button>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
