"use client";

import Link from "next/link";
import { useState } from "react";

import { Card, EmptyState, Loading, Message, PageHeader, Pill, Segmented, SeverityBadge } from "@/components/ui";
import { mutate as mutateAll } from "swr";

import { ApiError, api, managesAnyProject, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Dispute, DisputeAiVerdict, Me } from "@/lib/types";

export default function DisputesPage() {
  const [pending, setPending] = useState(true);
  const { data: me } = useApi<Me>("/auth/me");
  const { data, error, mutate } = useApi<Dispute[]>(`/disputes?pending=${pending}`, {
    keepPreviousData: true,
    // Poll while an AI analysis is queued or running.
    refreshInterval: (latest) =>
      latest?.some((d) => d.ai_status === "queued" || d.ai_status === "running") ? 4000 : 0,
  });
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const isLead = !!me && (me.is_admin || me.lead_teams.length > 0);

  async function decide(dispute: Dispute, decision: "accepted" | "rejected") {
    setBusy(dispute.id);
    try {
      await api.post(`/disputes/${dispute.id}/decide`, { decision });
      setMessage(decision === "accepted" ? "Đã chấp nhận; điểm đã được tính lại." : "Đã từ chối khiếu nại.");
      // Both lists (pending and all) hold this row; revalidate whichever is cached.
      await mutateAll((key) => typeof key === "string" && key.startsWith("/disputes"));
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function askAi(dispute: Dispute) {
    setBusy(dispute.id);
    try {
      await api.post(`/disputes/${dispute.id}/ai-check`, {});
      setMessage("Đã nhờ AI phân tích lại; kết quả hiện dưới lý do khiếu nại sau ít phút. Bạn vẫn là người quyết định.");
      await mutate();
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Disputes"
        title={isLead ? "Duyệt khiếu nại" : managesAnyProject(me) ? "Khiếu nại trong dự án" : "Khiếu nại của tôi"}
        subtitle={
          isLead
            ? "Khiếu nại được chấp nhận sẽ không tính vào điểm và các chỉ số liên quan được tính lại ngay."
            : "Bạn khiếu nại từng finding ở trang review; lead của bạn duyệt tại đây."
        }
        actions={
          <Segmented
            label="Trạng thái"
            value={pending ? "pending" : "all"}
            onChange={(v) => setPending(v === "pending")}
            options={[
              { value: "pending", label: "Đang chờ duyệt" },
              { value: "all", label: "Tất cả" },
            ]}
          />
        }
      />
      <Message text={message} onClose={() => setMessage(null)} />

      {!data ? (
        <Loading error={error} rows={4} />
      ) : data.length === 0 ? (
        <Card>
          <EmptyState icon="scale" title={pending ? "Không có khiếu nại nào đang chờ" : "Chưa có khiếu nại nào"} />
        </Card>
      ) : (
        <Card flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Finding</th>
                  <th>Người khiếu nại</th>
                  <th>Lý do</th>
                  <th>Kết quả</th>
                </tr>
              </thead>
              <tbody>
                {data.map((d) => (
                  <tr key={d.id}>
                    <td style={{ whiteSpace: "normal", minWidth: 220 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                        {d.finding_severity && <SeverityBadge severity={d.finding_severity} />}
                        <Link href={`/reviews/${d.review_id}`}>{d.finding_title ?? `#${d.finding_id}`}</Link>
                      </div>
                    </td>
                    <td>{d.raised_by_name ?? "—"}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 420 }}>
                      {d.reason}
                      <AiAnalysis d={d} />
                    </td>
                    <td>
                      {d.decision ? (
                        <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                          <Pill tone={d.decision === "accepted" ? "good" : "bad"} icon={d.decision === "accepted" ? "check" : "close"}>
                            {d.decision === "accepted" ? "Chấp nhận" : "Từ chối"}
                          </Pill>
                          {d.decided_at && <span className="muted small">{date(d.decided_at)}</span>}
                        </span>
                      ) : d.raised_by !== me?.id && isLead ? (
                        <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <button className="primary sm" disabled={busy === d.id} onClick={() => decide(d, "accepted")}>
                            Chấp nhận
                          </button>
                          <button className="sm" disabled={busy === d.id} onClick={() => decide(d, "rejected")}>
                            Từ chối
                          </button>
                          {(d.can_ai_check || d.ai_status === "queued" || d.ai_status === "running") && (
                            <button
                              className="sm"
                              disabled={busy === d.id || !d.can_ai_check}
                              onClick={() => askAi(d)}
                              title="AI đọc lại finding, lý do khiếu nại và mã nguồn ở commit đã review rồi nêu bên nào có lý; bạn vẫn quyết định. Tốn ngân sách LLM."
                            >
                              {d.ai_status === "queued" || d.ai_status === "running"
                                ? "AI đang phân tích…"
                                : d.ai_status === "done" || d.ai_status === "failed"
                                  ? "Nhờ AI phân tích lại"
                                  : "Nhờ AI phân tích"}
                            </button>
                          )}
                        </span>
                      ) : (
                        <Pill tone="warn">Chờ lead duyệt</Pill>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}

const VERDICT: Record<DisputeAiVerdict, { label: string; tone: "good" | "bad" | "warn" }> = {
  finding_holds: { label: "AI: finding đúng", tone: "bad" },
  dispute_holds: { label: "AI: khiếu nại có lý", tone: "good" },
  unclear: { label: "AI: chưa đủ để kết luận", tone: "warn" },
};

/** The AI second look at a dispute, for the lead before deciding (never a decision). */
function AiAnalysis({ d }: { d: Dispute }) {
  if (!d.ai_status) return null;
  const box = { marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "var(--surface-2)" } as const;
  if (d.ai_status === "queued" || d.ai_status === "running")
    return (
      <div className="small muted" style={box} role="status" aria-live="polite">
        {d.ai_status === "queued" ? "Đang chờ AI phân tích…" : "AI đang đọc finding và mã nguồn…"}
      </div>
    );
  if (d.ai_status === "failed")
    return (
      <div className="small" style={box} role="status">
        <Pill tone="bad">AI chưa phân tích được</Pill> <span className="muted">{d.ai_reason}</span>
      </div>
    );
  const v = d.ai_verdict ? VERDICT[d.ai_verdict] : null;
  return (
    <div className="small" style={{ ...box, display: "grid", gap: 4 }} role="status">
      <span style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        {v && <Pill tone={v.tone}>{v.label}</Pill>}
        {d.ai_confidence != null && <span className="muted">độ tin cậy {Math.round(d.ai_confidence * 100)}%</span>}
        {d.ai_evidence && <code>{d.ai_evidence}</code>}
      </span>
      {d.ai_reason && <span>{d.ai_reason}</span>}
      <span className="muted">Chỉ để tham khảo: bạn vẫn là người chấp nhận hoặc từ chối.</span>
    </div>
  );
}
