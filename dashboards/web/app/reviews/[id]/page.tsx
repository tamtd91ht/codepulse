"use client";

import { use, useRef, useState } from "react";

import { Alert, Card, EmptyState, ExternalLink, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { CATEGORY_LABEL, date, num } from "@/lib/format";
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
            {review.web_url && <ExternalLink href={review.web_url}>Mở trên GitLab</ExternalLink>}
          </span>
        }
      />

      <div className="grid kpis">
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
