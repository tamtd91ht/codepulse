"use client";

import { use, useState } from "react";

import { Loading, SeverityBadge } from "@/components/ui";
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

function DisputeForm({ finding, onDone }: { finding: Finding; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) return <button onClick={() => setOpen(true)}>Khiếu nại</button>;
  return (
    <div style={{ display: "grid", gap: 8, maxWidth: 560 }}>
      <textarea rows={3} value={reason} placeholder="Vì sao đây không phải lỗi?" onChange={(e) => setReason(e.target.value)} />
      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="primary"
          disabled={reason.trim().length < 5}
          onClick={async () => {
            try {
              await api.post(`/findings/${finding.id}/dispute`, { reason });
              onDone();
            } catch (e) {
              setError(e instanceof ApiError ? e.message : String(e));
            }
          }}
        >
          Gửi khiếu nại
        </button>
        <button onClick={() => setOpen(false)}>Huỷ</button>
      </div>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: review, error, mutate } = useApi<ReviewDetail>(`/reviews/${id}`);
  const { data: me } = useApi<Me>("/auth/me");
  if (!review) return <Loading error={error} />;
  const isAuthor = me?.id === review.author_user_id;

  return (
    <>
      <h1>
        {review.project} · {review.target_type === "mr" ? "Merge request" : "Commit"}{" "}
        <code>{review.reviewed_sha.slice(0, 8)}</code>
      </h1>
      <p className="muted">
        {review.status} · {date(review.finished_at)} · rubric {review.rubric_version} · {review.model ?? "—"}{" "}
        {review.web_url && (
          <a href={review.web_url} target="_blank" rel="noreferrer">Mở trên GitLab ↗</a>
        )}
      </p>

      <div className="grid kpis">
        <div className="card kpi">
          <div className="label">Điểm</div>
          <div className="value">{num(review.score, 0)}</div>
        </div>
        <div className="card kpi">
          <div className="label">LOC đã review</div>
          <div className="value">{review.loc_reviewed ?? "—"}</div>
        </div>
        <div className="card kpi">
          <div className="label">Finding</div>
          <div className="value">{review.findings.length}</div>
        </div>
      </div>

      {review.summary && (
        <div className="card">
          <h2>Tóm tắt</h2>
          <p style={{ whiteSpace: "pre-wrap" }}>{review.summary}</p>
        </div>
      )}
      {review.error && <p className="error">Lý do: {review.error}</p>}

      <div className="card">
        <h2>Findings</h2>
        {review.findings.length === 0 && <p className="muted">Không phát hiện vấn đề nào.</p>}
        {review.findings.map((f) => (
          <div key={f.id} className="finding">
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <SeverityBadge severity={f.severity} />
              <strong>{f.title}</strong>
              <span className="muted">
                {CATEGORY_LABEL[f.category] ?? f.category} · <code>{f.file_path}:{f.line_start}</code> · độ tin cậy{" "}
                {num(f.confidence, 2)} · {STATUS_LABEL[f.status ?? "open"] ?? f.status}
              </span>
            </div>
            <p>{f.message}</p>
            {f.suggestion && <p><strong>Gợi ý:</strong> {f.suggestion}</p>}
            {f.code_excerpt && <pre className="excerpt">{f.code_excerpt}</pre>}
            {isAuthor && f.status === "open" && <DisputeForm finding={f} onDone={() => mutate()} />}
          </div>
        ))}
      </div>
    </>
  );
}
