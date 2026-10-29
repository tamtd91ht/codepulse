"use client";

import Link from "next/link";
import { useState } from "react";
import { mutate as mutateAll } from "swr";
import useSWRInfinite from "swr/infinite";

import { RangePicker, type SharedRange, useCardRange } from "@/components/Evaluation";
import { Alert, Avatar, Card, EmptyState, ExternalLink, Loading, Pill } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, dateTime, num } from "@/lib/format";
import type { Page, Person, RerunManyOut, ReviewListItem, Verdict } from "@/lib/types";

const PAGE = 25;
/** The corner pill (ReviewActivity): refreshed right away so a queued re-run shows there. */
const ACTIVITY = "/activity/reviews";

const STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  failed: { label: "Lỗi", tone: "bad" },
  skipped: { label: "Bỏ qua", tone: "warn" },
  queued: { label: "Đang chờ", tone: "info" },
  running: { label: "Đang chạy", tone: "info" },
};

type StatusFilter = "results" | "done" | "problems";
// "Kết quả": what a review ended with (older rounds of the same change are left out).
const STATUSES: Record<StatusFilter, string[]> = {
  results: ["done", "failed", "skipped"],
  done: ["done"],
  problems: ["failed", "skipped"],
};

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
}: {
  businessProjectId?: number;
  /** One repo's results (the repo drawer of the dự án page): no repo filter, no repo column. */
  repo?: number;
  /** Key the card's fold state is remembered under (pages that fold it by default use their own). */
  id?: string;
  defaultCollapsed?: boolean;
  /** The repos offered by the repo filter (shown when there is more than one). */
  repos?: { id: number; path_with_namespace: string }[];
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
  const [repoId, setRepoId] = useState("");
  const [verdict, setVerdict] = useState<"" | Verdict>("");
  const [status, setStatus] = useState<StatusFilter>("results");
  // Re-run several at once: only rows the API marks can_rerun (the viewer manages them).
  const [chosen, setChosen] = useState<Set<number>>(() => new Set());
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<{ tone: "success" | "warning" | "error"; text: string } | null>(null);
  // Everyone whose reviews the viewer may see (developer: themselves; lead: the team; admin: all).
  const { data: people } = useApi<Person[]>(person === undefined ? "/people" : null);

  const base =
    range && range.from && range.to
      ? `/reviews${qs({
          from: range.from,
          to: range.to,
          business_project_id: businessProjectId,
          project_id: repo ?? repoId,
          user_id: person ?? userId,
          verdict,
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
    { keepPreviousData: true, shouldRetryOnError: (e: ApiError) => !e.status || e.status >= 500 },
  );
  const items = pages.data?.flatMap((p) => p.items) ?? [];
  const last = pages.data?.at(-1);
  const loadingMore = pages.isValidating && pages.size > (pages.data?.length ?? 0);
  const filtered = !!(userId || repoId || verdict || status !== "results");
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
  const toggle = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const rerun = async () => {
    const finished = selected.filter((r) => r.status === "done").length;
    const ok = window.confirm(
      `Review lại ${selected.length} kết quả? Việc này tốn ngân sách LLM${
        finished ? `; ${finished} review đã chấm điểm sẽ có lần review mới, lần cũ được giữ làm lịch sử` : ""
      }.`,
    );
    if (!ok) return;
    setSending(true);
    setSent(null);
    try {
      const out = await api.post<RerunManyOut>("/reviews/rerun", { ids: selected.map((r) => r.id) });
      const why = out.failed.map((f) => `#${f.id}: ${f.error}`).join("; ");
      setSent(
        out.failed.length === 0
          ? { tone: "success", text: `Đã đưa ${out.queued.length} review vào hàng đợi; theo dõi ở góc phải màn hình.` }
          : {
              tone: out.queued.length ? "warning" : "error",
              text: `Đã đưa ${out.queued.length} review vào hàng đợi, ${out.failed.length} không chạy được (${why}).`,
            },
      );
      setChosen(new Set());
      await Promise.all([pages.mutate(), mutateAll(ACTIVITY)]);
    } catch (e) {
      setSent({ tone: "error", text: `Không gửi được: ${e instanceof ApiError ? e.message : String(e)}` });
    } finally {
      setSending(false);
    }
  };
  const reset = () => {
    setUserId("");
    setRepoId("");
    setVerdict("");
    setStatus("results");
  };

  return (
    <Card
      id={id}
      title="Kết quả review"
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
        {repo === undefined && repos && repos.length > 1 && (
          <label className="filter">
            <span className="small muted">Repo</span>
            <select value={repoId} onChange={(e) => setRepoId(e.target.value)} aria-label="Lọc theo repo">
              <option value="">Mọi repo</option>
              {repos.map((r) => (
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
            <option value="done">Đã chấm điểm</option>
            <option value="problems">Lỗi hoặc bỏ qua</option>
          </select>
        </label>
        <span className="spacer" />
        {filtered && (
          <button type="button" className="sm ghost" onClick={reset}>
            Bỏ lọc
          </button>
        )}
      </div>

      {(selected.length > 0 || sent) && (
        <div className="results-error">
          {sent && (
            <Alert tone={sent.tone} onClose={() => setSent(null)}>
              {sent.text}
            </Alert>
          )}
          {selected.length > 0 && (
            <>
              <span className="small">Đã chọn {selected.length}</span>
              <button type="button" className="sm primary" onClick={() => void rerun()} disabled={sending}>
                {sending ? "Đang gửi…" : "↻ Review lại"}
              </button>
              <button type="button" className="sm ghost" onClick={() => setChosen(new Set())} disabled={sending}>
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
                        onChange={() => setChosen(allSelected ? new Set() : new Set(selectable.map((r) => r.id)))}
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
                              checked={chosen.has(r.id)}
                              onChange={() => toggle(r.id)}
                              aria-label={`Chọn ${r.target} để review lại`}
                            />
                          )}
                        </td>
                      )}
                      <td>
                        <Link href={`/reviews/${r.id}`}>{r.target}</Link>
                        {r.round > 1 && <span className="muted small"> · vòng {r.round}</span>}
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
