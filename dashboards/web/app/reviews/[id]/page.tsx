"use client";

import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";

import { CloseActions, ClosureNote } from "@/components/FindingClose";
import { BasisCard, basisSummary, changedRules, previousRun } from "@/components/ReviewBasis";
import { announceReviewQueued } from "@/components/ReviewActivity";
import { Alert, Card, EmptyState, ExternalLink, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { CATEGORY_LABEL, TRIGGER_LABEL, VERDICT_LABEL, date, num } from "@/lib/format";
import type { Finding, FindingClosure, Me, Repo, ReviewDetail } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  open: "Đang mở",
  fixed: "Đã sửa",
  disputed: "Đang khiếu nại",
  accepted_risk: "Chấp nhận rủi ro",
  false_positive: "Không phải lỗi (đã duyệt)",
  rejected: "Kiểm chứng bác bỏ (không chấm điểm)",
};
const VERIFIED_LABEL: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  confirmed: { label: "✔ đã kiểm chứng", tone: "good" },
  downgraded: { label: "↓ hạ mức sau kiểm chứng", tone: "info" },
  rejected: { label: "✘ bác bỏ", tone: "good" },
  unverified: { label: "? chưa kiểm chứng được", tone: "warn" },
};
const FINDING_TONE: Record<string, "good" | "bad" | "warn" | "info" | undefined> = {
  open: undefined,
  fixed: "good",
  disputed: "warn",
  accepted_risk: "info",
  false_positive: "good",
  rejected: "good",
};
const REVIEW_STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  done: { label: "Đã review", tone: "good" },
  superseded: { label: "Đã thay bằng lần mới", tone: "info" },
  failed: { label: "Lỗi", tone: "bad" },
  skipped: { label: "Bỏ qua", tone: "warn" },
  pending: { label: "Đang chờ", tone: "info" },
  running: { label: "Đang chạy", tone: "info" },
};

function DisputeForm({ finding, onDone }: { finding: Finding; onDone: () => Promise<unknown> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const fieldId = `reason-${finding.id}`;
  const close = () => {
    setOpen(false);
    // The form unmounts; keep the keyboard user on the control that opened it.
    requestAnimationFrame(() => opener.current?.focus());
  };
  if (!open)
    return (
      <button className="sm" onClick={() => setOpen(true)} style={{ marginTop: 10 }} ref={opener}>
        Khiếu nại
      </button>
    );
  return (
    <form
      style={{ display: "grid", gap: 8, maxWidth: 560, marginTop: 10 }}
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await api.post(`/findings/${finding.id}/dispute`, { reason });
          await onDone();
          setOpen(false);
        } catch (err) {
          setError(err instanceof ApiError ? err.message : String(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <label htmlFor={fieldId} style={{ fontWeight: 600 }}>
        Lý do khiếu nại
      </label>
      <textarea
        id={fieldId}
        rows={3}
        value={reason}
        placeholder="Vì sao đây không phải lỗi?"
        aria-describedby={`${fieldId}-help${error ? ` ${fieldId}-err` : ""}`}
        onChange={(e) => setReason(e.target.value)}
        autoFocus
      />
      <span id={`${fieldId}-help`} className="muted small">
        Ít nhất 5 ký tự. Lead của bạn sẽ duyệt.
      </span>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="primary sm" type="submit" disabled={reason.trim().length < 5 || busy}>
          {busy ? "Đang gửi…" : "Gửi khiếu nại"}
        </button>
        <button className="sm" type="button" onClick={close} disabled={busy}>
          Huỷ
        </button>
      </div>
      {error && (
        <span id={`${fieldId}-err`} className="error small" role="alert">
          {error}
        </span>
      )}
    </form>
  );
}

/** A rerun the page started: polled until the worker picks it up (force) or it settles. */
interface RerunState {
  force: boolean;
  pending: boolean;
  message: string;
  tone: "info" | "success" | "warning" | "error";
  startedAt: number;
}

const RERUN_POLL_MS = 5000;
const RERUN_POLL_MAX_MS = 15 * 60 * 1000;

/** Lead/admin: a deliberate new round for this commit or open MR (the old round is kept). */
function RerunBar({
  review,
  state,
  onRun,
}: {
  review: ReviewDetail;
  state: RerunState | null;
  onRun: (force: boolean, ignoreLocLimit?: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const finished = review.status === "done";
  const blocked = busy || !!state?.pending || !review.can_rerun;
  // Skipped as "MR quá lớn: N LOC": a lead may run it anyway, past MAX_REVIEW_LOC, this once.
  const tooLarge = review.status === "skipped" && /^MR quá lớn/.test(review.error ?? "");
  const run = async (force: boolean, ignoreLocLimit?: boolean) => {
    setBusy(true);
    try {
      await onRun(force, ignoreLocLimit);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {/* What the button did, shown where it is seen; the hint below stays as it was. */}
      {state && <Alert tone={state.tone}>{state.message}</Alert>}
      <div className="toolbar rerun-bar" style={{ marginBottom: 12 }}>
      <span className="muted small">
        {finished
            ? review.target_type === "mr"
              ? "MR còn mở ở đúng head này: có thể review lại thành lần mới; lần hiện tại được giữ làm lịch sử."
              : "Có thể review lại commit này thành lần mới; lần hiện tại được giữ làm lịch sử."
            : tooLarge
              ? `${review.error}, vượt giới hạn LOC nên bị bỏ qua. Có thể chạy lại bỏ qua giới hạn này cho riêng lần review này.`
              : "Lần review này lỗi hoặc bị bỏ qua: chạy lại được."}
      </span>
      <span className="spacer" />
      {review.can_rerun && tooLarge && (
        <button
          type="button"
          className="primary ink"
          disabled={blocked}
          title="Chỉ áp dụng cho lần chạy này; giới hạn LOC chung không đổi"
          onClick={async () => {
            const ok = window.confirm(
              `${review.error}. Chạy lại bỏ qua giới hạn LOC sẽ review toàn bộ thay đổi này: tốn nhiều token LLM hơn và chạy lâu hơn bình thường. Tiếp tục?`,
            );
            if (ok) await run(false, true);
          }}
        >
          {busy ? "Đang gửi…" : state?.pending ? "Đang chờ worker…" : "Chạy lại, bỏ qua giới hạn LOC"}
        </button>
      )}
      {review.can_rerun && (
        <button
          type="button"
          className={finished || tooLarge ? undefined : "primary ink"}
          disabled={blocked}
          onClick={async () => {
            if (finished && !window.confirm("Review lại sẽ tốn ngân sách LLM và tạo lần review mới. Tiếp tục?")) return;
            await run(finished);
          }}
        >
          {busy ? "Đang gửi…" : state?.pending ? "Đang chờ worker…" : finished ? "↻ Review lại" : "Chạy lại"}
        </button>
      )}
      </div>
    </>
  );
}

function VerificationCard({ review }: { review: ReviewDetail }) {
  const v = review.deep?.verification ?? {};
  if (v.skipped || v.error) {
    return (
      <Alert tone="info">
        Chưa kiểm chứng độc lập: {v.skipped ?? v.error}. Finding critical/high chưa kiểm chứng chỉ được chấm điểm khi
        độ tin cậy ≥ 0,7 và verdict là &quot;cần người xem&quot;.
      </Alert>
    );
  }
  return (
    <Card
      title="Kiểm chứng độc lập"
      subtitle="Mỗi finding critical/high/medium được một lượt gọi mới, không thấy lý luận của lượt review, cố chứng minh nó sai trong mã nguồn (docs/specs/19 §4.3)"
    >
      <div className="chips">
        <Pill tone="good" icon="checkCircle">{v.confirmed ?? 0} xác nhận</Pill>
        <Pill tone={v.downgraded ? "info" : undefined}>{v.downgraded ?? 0} hạ mức</Pill>
        <Pill tone={v.rejected ? "good" : undefined}>{v.rejected ?? 0} bác bỏ</Pill>
        <Pill tone={v.unverified ? "warn" : undefined}>{v.unverified ?? 0} chưa kiểm chứng được</Pill>
        {!!v.ignored_evidence && <Pill tone="warn" icon="alert">{v.ignored_evidence} bằng chứng không hợp lệ</Pill>}
        <Pill>{v.calls ?? 0} lượt gọi{v.failed_calls ? ` (${v.failed_calls} lỗi)` : ""}</Pill>
      </div>
    </Card>
  );
}

function DeepCard({ review }: { review: ReviewDetail }) {
  const d = review.deep ?? {};
  if (review.mode !== "deep") {
    const why =
      d.skipped ??
      (d.error === "checkout"
        ? "không tải được bản sao repo (quá lớn hoặc GitLab lỗi)"
        : d.error === "LLMError"
          ? "lượt gọi LLM lỗi hoặc quá giờ"
          : d.error === "ValueError"
            ? "LLM trả kết quả không đúng định dạng"
            : d.error
              ? "lỗi khi chạy lượt sâu"
              : "lượt sâu không chạy");
    return (
      <Alert tone="info">
        <span title={d.detail ?? undefined}>Chưa đối chứng trong mã nguồn: {why}.</span> Kết quả trên là của lượt review
        nhanh (chỉ diff).
      </Alert>
    );
  }
  const dropped = d.dropped ?? [];
  const downgraded = d.downgraded ?? [];
  const fit = d.project_fit ?? [];
  return (
    <Card
      title="Đối chứng trong mã nguồn"
      subtitle={`Reviewer đã mở repo${
        [d.files_seen != null && `${d.files_seen} file`, d.turns != null && `${d.turns} lượt đọc`].filter(Boolean).length
          ? ` (${[d.files_seen != null && `${d.files_seen} file`, d.turns != null && `${d.turns} lượt đọc`].filter(Boolean).join(", ")})`
          : ""
      } để kiểm tra từng finding và tìm lỗi xuyên file`}
    >
      <div className="chips" style={{ marginBottom: 12 }}>
        <Pill tone="good" icon="checkCircle">{d.kept ?? 0} giữ nguyên</Pill>
        <Pill tone={downgraded.length ? "warn" : undefined}>{downgraded.length} hạ mức</Pill>
        <Pill tone={dropped.length ? "info" : undefined}>{dropped.length} loại bỏ</Pill>
        <Pill tone={d.added ? "primary" : undefined}>{d.added ?? 0} phát hiện thêm</Pill>
        {!!d.ignored_verifications && (
          <Pill tone="warn" icon="alert">{d.ignored_verifications} đề nghị bỏ không có bằng chứng, đã giữ nguyên</Pill>
        )}
      </div>
      {fit.length > 0 && (
        <>
          <h4 style={{ margin: "6px 0" }}>Độ phù hợp với dự án</h4>
          <ul style={{ margin: "0 0 12px", paddingLeft: 20, color: "var(--text-2)" }}>
            {fit.map((c, i) => (
              <li key={`fit-${i}`}>{c}</li>
            ))}
          </ul>
        </>
      )}
      {(dropped.length > 0 || downgraded.length > 0) && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Quyết định</th>
                <th>Finding của lượt nhanh</th>
                <th>Lý do</th>
                <th>Bằng chứng</th>
              </tr>
            </thead>
            <tbody>
              {downgraded.map((n, i) => (
                <tr key={`d-${i}`}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <SeverityBadge severity={n.severity} /> → {n.new_severity && <SeverityBadge severity={n.new_severity} />}
                  </td>
                  <td>
                    {n.title} <code>{n.file_path}:{n.line_start}</code>
                  </td>
                  <td>{n.reason}</td>
                  <td>
                    <code>{n.evidence}</code>
                  </td>
                </tr>
              ))}
              {dropped.map((n, i) => (
                <tr key={`x-${i}`} className="muted">
                  <td style={{ whiteSpace: "nowrap" }}>
                    <Pill>loại</Pill> <SeverityBadge severity={n.severity} />
                  </td>
                  <td>
                    {n.title} <code>{n.file_path}:{n.line_start}</code>
                  </td>
                  <td>{n.reason}</td>
                  <td>
                    <code>{n.evidence}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d.instruction_sources && d.instruction_sources.length > 0 && !review.basis?.instructions && (
        <p className="muted small" style={{ marginBottom: 0 }}>
          Đã đọc hướng dẫn của repo: {d.instruction_sources.join(", ")}
        </p>
      )}
    </Card>
  );
}

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [rerun, setRerun] = useState<RerunState | null>(null);
  const { data: review, error, mutate } = useApi<ReviewDetail>(`/reviews/${id}`, {
    refreshInterval: rerun?.pending ? RERUN_POLL_MS : 0,
  });
  const { data: me } = useApi<Me>("/auth/me");
  // How findings were closed as fixed; polled while a request to close one is being checked.
  const closures = useApi<FindingClosure[]>(`/reviews/${id}/closures`, {
    refreshInterval: (latest) => (latest?.some((c) => c.status === "checking") ? 5000 : 0),
  });
  // The knowledge line links to the repo's knowledge page; the detail only names the repo, so look its id up.
  const repos = useApi<Repo[]>(review?.basis?.knowledge ? "/repos" : null);
  const repoId = repos.data?.find((r) => r.path_with_namespace === review?.project)?.id ?? null;
  // Stop polling once the worker took the rerun: a forced one supersedes this round, a plain
  // one leaves queued/running; give up after RERUN_POLL_MAX_MS either way.
  useEffect(() => {
    if (!rerun?.pending || !review) return;
    const taken = rerun.force ? review.superseded_by !== null : !["queued", "running"].includes(review.status);
    const tooLong = Date.now() - rerun.startedAt > RERUN_POLL_MAX_MS;
    if (taken || tooLong) {
      setRerun({
        ...rerun,
        pending: false,
        tone: taken ? "success" : "warning",
        message: taken
          ? rerun.force
            ? "Lần review mới đã bắt đầu; xem trong bảng Lịch sử review."
            : "Lần chạy lại đã xong."
          : "Worker chưa nhận việc sau 15 phút; tải lại trang để xem trạng thái.",
      });
    }
  }, [rerun, review]);
  if (!review) return <Loading error={error} rows={4} />;

  async function startRerun(force: boolean, ignoreLocLimit = false): Promise<void> {
    try {
      await api.post(`/reviews/${id}/rerun`, { force, ignore_loc_limit: ignoreLocLimit });
      setRerun({
        force,
        pending: true,
        startedAt: Date.now(),
        tone: "info",
        message: "Đã đưa vào hàng đợi; tiến trình hiện ở góc phải màn hình, trang này tự cập nhật khi worker nhận việc.",
      });
      // The corner pill polls slowly while idle: it looks now and follows the run closely.
      announceReviewQueued();
      await mutate();
    } catch (e) {
      setRerun({
        force,
        pending: false,
        startedAt: Date.now(),
        tone: "error",
        message: `Không chạy lại được: ${e instanceof ApiError ? e.message : String(e)}`,
      });
    }
  }
  const isAuthor = me?.id === review.author_user_id;
  const st = REVIEW_STATUS[review.status] ?? { label: review.status };
  const newer = review.history.find((h) => h.id === review.superseded_by);
  const bySeverity = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  for (const f of review.findings) bySeverity[f.severity] += 1;

  return (
    <>
      <PageHeader
        eyebrow={`${review.project ?? "Review"} · ${review.target_type === "mr" ? "Merge request" : "Commit"}`}
        title={
          <>
            Review <code>{review.reviewed_sha.slice(0, 8)}</code>
          </>
        }
        subtitle={
          <span className="status-line">
            <Pill tone={st.tone}>{st.label}</Pill>
            <span>{date(review.finished_at)}</span>
            <span>· rubric {review.rubric_version}</span>
            <span>· {review.model ?? "—"}</span>
            <span>· {TRIGGER_LABEL[review.trigger] ?? review.trigger}</span>
            {review.mode === "deep" && (
              <Pill tone="primary" icon="search">
                Review sâu
              </Pill>
            )}
            {review.mode === "agent" && (
              <Pill tone="primary" icon="search">
                Review sâu (trong repo)
              </Pill>
            )}
            {review.deep?.fallback && (
              <span title={review.deep.fallback.error}>
                <Pill tone="warn" icon="alert">
                  Lượt agent lỗi, dùng lượt không tool
                </Pill>
              </span>
            )}
            {review.round > 1 && <Pill tone="info">lần {review.round}</Pill>}
            {review.web_url && <ExternalLink href={review.web_url}>Mở trên GitLab</ExternalLink>}
          </span>
        }
      />

      {review.status === "superseded" && (
        <Alert tone="info">
          Lần review này đã được thay bằng lần mới hơn
          {newer ? (
            <>
              {" "}
              (<Link href={`/reviews/${newer.id}`}>lần {newer.round}</Link>)
            </>
          ) : (
            " (head mới của MR)"
          )}
          ; findings ở đây là lịch sử, không tính vào điểm nữa.
        </Alert>
      )}

      {/* can_rerun already says whether this person manages the review's dự án. */}
      {(review.can_rerun || rerun) && <RerunBar review={review} state={rerun} onRun={startRerun} />}

      <div className="grid kpis">
        {review.status === "done" && (
          <div className="card kpi">
            <div className="label">Kết luận merge</div>
            <div className="value">
              <span className={`verdict lg ${review.verdict ?? ""}`}>{review.verdict ? VERDICT_LABEL[review.verdict] : "—"}</span>
            </div>
            <div className="delta">
              <span className="muted">
                Risk {review.risk_score ?? 0} · critical ×10, high ×3, medium ×1
              </span>
            </div>
          </div>
        )}
        <div className="card kpi">
          <div className="label">Điểm</div>
          <div className="value">
            {num(review.score, 0)}
            <span className="unit">/ 100</span>
          </div>
          <div className="delta">
            <span className="muted">{bySeverity.critical ? "Có critical: tối đa 50" : "Công thức cố định theo finding"}</span>
          </div>
        </div>
        <div className="card kpi">
          <div className="label">LOC đã review</div>
          <div className="value">{review.loc_reviewed ?? "—"}</div>
          <div className="delta">
            <span className="muted">Dòng thay đổi được chấm</span>
          </div>
        </div>
        <div className="card kpi">
          <div className="label">Finding</div>
          <div className="value">{review.findings.length}</div>
          <div className="delta" style={{ gap: 6, flexWrap: "wrap" }}>
            {bySeverity.critical > 0 && <span className="sev critical">{bySeverity.critical}</span>}
            {bySeverity.high > 0 && <span className="sev high">{bySeverity.high}</span>}
            {bySeverity.medium > 0 && <span className="sev medium">{bySeverity.medium}</span>}
            {bySeverity.low > 0 && <span className="sev low">{bySeverity.low}</span>}
            {review.findings.length === 0 && <span className="muted">Không có vấn đề</span>}
          </div>
        </div>
      </div>

      {review.error && <Alert tone="error">Lý do: {review.error}</Alert>}

      {review.intent && (review.intent.summary || review.intent.concerns.length > 0) && (
        <Card
          title="Thay đổi làm gì và có đúng hướng không"
          subtitle="Nhận xét thiết kế của reviewer, không tính vào điểm hay kết luận merge"
        >
          {review.intent.summary && <p style={{ marginTop: 0, color: "var(--text-2)" }}>{review.intent.summary}</p>}
          <div className="chips" style={{ marginBottom: review.intent.concerns.length ? 10 : 0 }}>
            {review.intent.matches_intent !== null && (
              <Pill tone={review.intent.matches_intent ? "good" : "warn"} icon={review.intent.matches_intent ? "checkCircle" : "alert"}>
                {review.intent.matches_intent ? "Đúng với mô tả" : "Lệch so với mô tả"}
              </Pill>
            )}
            {review.intent.altitude && (
              <Pill tone={review.intent.altitude === "right" ? "good" : "warn"}>
                {review.intent.altitude === "right" ? "Độ phức tạp vừa" : review.intent.altitude === "over" ? "Over-engineering" : "Under-engineering"}
              </Pill>
            )}
          </div>
          {review.intent.concerns.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 20, color: "var(--text-2)" }}>
              {review.intent.concerns.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {review.status === "done" && review.deep?.verification && <VerificationCard review={review} />}
      {review.status === "done" && review.mode === "deep" && <DeepCard review={review} />}

      {(review.status === "done" || review.status === "superseded") && (
        <BasisCard
          basis={review.basis}
          findings={review.findings}
          mode={review.mode}
          deepError={
            review.deep?.fallback
              ? `lượt agent lỗi: ${review.deep.fallback.error ?? "không rõ"}`
              : review.deep?.error === "checkout"
                ? "không tải được bản sao repo (quá lớn hoặc GitLab lỗi)"
                : review.deep?.error
                  ? `lỗi ${review.deep.error}`
                  : undefined
          }
          deepSkipped={review.deep?.skipped}
          deep={review.deep}
          history={review.history}
          reviewId={review.id}
          projectId={repoId}
        />
      )}

      {review.summary && (
        <Card title="Tóm tắt">
          <p style={{ whiteSpace: "pre-wrap", color: "var(--text-2)" }}>{review.summary}</p>
        </Card>
      )}

      {review.history.length > 1 && (
        <Card title="Lịch sử review" subtitle="Mọi lần review của MR / commit này; điểm và finding của từng lần được giữ nguyên" flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Lần</th>
                  <th>Commit</th>
                  <th>Kết luận</th>
                  <th className="num">Điểm</th>
                  <th>Nguồn</th>
                  <th>Căn cứ</th>
                  <th>Ngày</th>
                </tr>
              </thead>
              <tbody>
                {review.history.map((h, i) => (
                  <tr key={h.id} className={h.id === review.id ? undefined : "muted"}>
                    <td>
                      {h.id === review.id ? <strong>#{h.id} · lần {h.round}</strong> : <Link href={`/reviews/${h.id}`}>#{h.id} · lần {h.round}</Link>}
                      {h.mode === "deep" || h.mode === "agent" ? " · sâu" : ""}
                    </td>
                    <td>
                      <code>{h.reviewed_sha.slice(0, 8)}</code>
                    </td>
                    <td>
                      {h.status === "done" && h.verdict ? (
                        <span className={`verdict ${h.verdict}`}>{VERDICT_LABEL[h.verdict]}</span>
                      ) : (
                        <Pill tone={h.status === "failed" ? "bad" : h.status === "superseded" ? undefined : "warn"}>
                          {REVIEW_STATUS[h.status]?.label ?? h.status}
                        </Pill>
                      )}
                    </td>
                    <td className="num">{h.score !== null ? num(h.score, 0) : "—"}</td>
                    <td className="muted">{TRIGGER_LABEL[h.trigger] ?? h.trigger}</td>
                    <td className="muted small">
                      {basisSummary(h.basis)}
                      {(() => {
                        const before = previousRun(review.history, i);
                        const changed = before ? changedRules(h.basis, before.basis) : [];
                        return changed.length > 0 ? (
                          <>
                            {" · "}
                            <span title={`So với #${before?.id}: ${changed.join(", ")}`}>quy tắc đã đổi</span>
                          </>
                        ) : null;
                      })()}
                    </td>
                    <td className="muted">{date(h.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="Findings" subtitle={review.findings.length ? "Mỗi finding kèm vị trí, độ tin cậy và trích đoạn code" : undefined}>
        {review.findings.length === 0 && (
          <EmptyState icon="checkCircle" tone="good" title="Không phát hiện vấn đề nào" />
        )}
        {review.findings.map((f) => {
          const status = f.status ?? "open";
          // Newest first: the latest closing (or request) of this finding.
          const closure = closures.data?.find((c) => c.finding_id === f.id);
          return (
            <article key={f.id} className={`finding ${f.severity}`}>
              <div className="finding-head">
                <SeverityBadge severity={f.severity} />
                <strong>{f.title}</strong>
                {status !== "open" && <Pill tone={FINDING_TONE[status]}>{STATUS_LABEL[status] ?? status}</Pill>}
                {f.verified && VERIFIED_LABEL[f.verified] && (
                  <span title={f.verification?.reason ?? undefined}>
                    <Pill tone={VERIFIED_LABEL[f.verified].tone}>{VERIFIED_LABEL[f.verified].label}</Pill>
                  </span>
                )}
              </div>
              <div className="finding-meta">
                <span>{CATEGORY_LABEL[f.category] ?? f.category}</span>
                {f.rule_id && (
                  <span className="rule-tag" title="Quy tắc của dự án/repo mà finding này vi phạm">
                    {f.rule_id}
                  </span>
                )}
                <code>
                  {f.file_path}:{f.line_start}
                </code>
                <span>độ tin cậy {num(f.confidence, 2)}</span>
              </div>
              <div className="finding-body">
                <p>{f.message}</p>
                {f.suggestion && (
                  <p>
                    <strong>Gợi ý:</strong> {f.suggestion}
                  </p>
                )}
                {f.code_excerpt && <pre className="excerpt">{f.code_excerpt}</pre>}
              </div>
              <ClosureNote closure={closure} onDone={() => Promise.all([mutate(), closures.mutate()])} />
              <CloseActions
                finding={f}
                closure={closure}
                isAuthor={isAuthor}
                isAdmin={!!me?.is_admin}
                onDone={() => Promise.all([mutate(), closures.mutate()])}
              />
              {isAuthor && status === "open" && closure?.status !== "pending" && closure?.status !== "checking" && (
                <DisputeForm finding={f} onDone={() => mutate()} />
              )}
            </article>
          );
        })}
      </Card>
    </>
  );
}
