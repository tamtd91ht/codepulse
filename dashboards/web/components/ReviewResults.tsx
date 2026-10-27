"use client";

import Link from "next/link";
import { useState } from "react";
import useSWRInfinite from "swr/infinite";

import { RangePicker, useRange } from "@/components/Evaluation";
import { Alert, Avatar, Card, EmptyState, ExternalLink, Loading, Pill } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, dateTime, num } from "@/lib/format";
import type { Page, Person, ReviewListItem, Verdict } from "@/lib/types";

const PAGE = 25;

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
}: {
  businessProjectId?: number;
  /** The dự án's repos, for the repo filter. */
  repos?: { id: number; path_with_namespace: string }[];
}) {
  const [range, setRange] = useRange("30");
  const [userId, setUserId] = useState("");
  // Picked by clicking a name: may be someone /people leaves out (deactivated since).
  const [picked, setPicked] = useState<{ id: string; name: string } | null>(null);
  const [repoId, setRepoId] = useState("");
  const [verdict, setVerdict] = useState<"" | Verdict>("");
  const [status, setStatus] = useState<StatusFilter>("results");
  // Everyone whose reviews the viewer may see (developer: themselves; lead: the team; admin: all).
  const { data: people } = useApi<Person[]>("/people");

  const base =
    range && range.from && range.to
      ? `/reviews${qs({
          from: range.from,
          to: range.to,
          business_project_id: businessProjectId,
          project_id: repoId,
          user_id: userId,
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
  const onlySelf = people?.length === 1;

  const nameOf = (p: { id: number; full_name: string | null; primary_email: string | null }) =>
    p.full_name ?? p.primary_email ?? `#${p.id}`;
  const listed = !userId || (people ?? []).some((p) => String(p.id) === userId);
  // #4: ask for exactly one more page than loaded, also after a failed one.
  const loadMore = () => void pages.setSize((pages.data?.length ?? 0) + 1);
  const reset = () => {
    setUserId("");
    setRepoId("");
    setVerdict("");
    setStatus("results");
  };

  return (
    <Card
      id="review-results"
      title="Kết quả review"
      subtitle="Review tạo sau đứng trước. Lọc theo thời gian hoàn tất, thành viên, repo, kết luận và trạng thái."
      actions={<RangePicker value={range} onChange={setRange} />}
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
        {repos && repos.length > 1 && (
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
                  <th>Thay đổi</th>
                  <th>Thành viên</th>
                  <th>Repo</th>
                  <th>Kết luận</th>
                  <th className="num">Điểm</th>
                  <th>Hoàn tất</th>
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
                      <td>
                        <Link href={`/reviews/${r.id}`}>{r.target}</Link>
                        {r.round > 1 && <span className="muted small"> · vòng {r.round}</span>}
                      </td>
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
                      <td className="muted">{r.project ?? "—"}</td>
                      <td>{r.verdict ? <span className={`verdict ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span> : <span className="muted">—</span>}</td>
                      <td className="num">
                        {r.status === "done" ? <strong>{num(r.score, 0)}</strong> : <Pill tone={st?.tone}>{st?.label ?? r.status}</Pill>}
                      </td>
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
