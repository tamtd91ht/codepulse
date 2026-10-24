"use client";

import { use, useRef, useState } from "react";

import { Alert, Card, EmptyState, ExternalLink, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { CATEGORY_LABEL, TRIGGER_LABEL, VERDICT_LABEL, date, num } from "@/lib/format";
import type { Finding, Me, ReviewDetail } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  open: "Đang mở",
  fixed: "Đã sửa",
  disputed: "Đang khiếu nại",
  accepted_risk: "Chấp nhận rủi ro",
  false_positive: "Không phải lỗi (đã duyệt)",
};
const FINDING_TONE: Record<string, "good" | "bad" | "warn" | "info" | undefined> = {
  open: undefined,
  fixed: "good",
  disputed: "warn",
  accepted_risk: "info",
  false_positive: "good",
};
const REVIEW_STATUS: Record<string, { label: string; tone?: "good" | "bad" | "warn" | "info" }> = {
  done: { label: "Đã review", tone: "good" },
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
      {d.instruction_sources && d.instruction_sources.length > 0 && (
        <p className="muted small" style={{ marginBottom: 0 }}>
          Đã đọc hướng dẫn của repo: {d.instruction_sources.join(", ")}
        </p>
      )}
    </Card>
  );
}

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: review, error, mutate } = useApi<ReviewDetail>(`/reviews/${id}`);
  const { data: me } = useApi<Me>("/auth/me");
  if (!review) return <Loading error={error} rows={4} />;
  const isAuthor = me?.id === review.author_user_id;
  const st = REVIEW_STATUS[review.status] ?? { label: review.status };
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
            {review.web_url && <ExternalLink href={review.web_url}>Mở trên GitLab</ExternalLink>}
          </span>
        }
      />

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

      {review.status === "done" && (review.mode === "deep" || review.deep?.error || review.deep?.skipped) && (
        <DeepCard review={review} />
      )}

      {review.summary && (
        <Card title="Tóm tắt">
          <p style={{ whiteSpace: "pre-wrap", color: "var(--text-2)" }}>{review.summary}</p>
        </Card>
      )}

      <Card title="Findings" subtitle={review.findings.length ? "Mỗi finding kèm vị trí, độ tin cậy và trích đoạn code" : undefined}>
        {review.findings.length === 0 && (
          <EmptyState icon="checkCircle" tone="good" title="Không phát hiện vấn đề nào" />
        )}
        {review.findings.map((f) => {
          const status = f.status ?? "open";
          return (
            <article key={f.id} className={`finding ${f.severity}`}>
              <div className="finding-head">
                <SeverityBadge severity={f.severity} />
                <strong>{f.title}</strong>
                {status !== "open" && <Pill tone={FINDING_TONE[status]}>{STATUS_LABEL[status] ?? status}</Pill>}
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
              {isAuthor && status === "open" && <DisputeForm finding={f} onDone={() => mutate()} />}
            </article>
          );
        })}
      </Card>
    </>
  );
}
