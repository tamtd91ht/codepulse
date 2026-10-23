"use client";

import Link from "next/link";
import { useState } from "react";

import { Card, EmptyState, Loading, Message, PageHeader, Pill, Segmented, SeverityBadge } from "@/components/ui";
import { mutate as mutateAll } from "swr";

import { ApiError, api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Dispute, Me } from "@/lib/types";

export default function DisputesPage() {
  const [pending, setPending] = useState(true);
  const { data: me } = useApi<Me>("/auth/me");
  const { data, error } = useApi<Dispute[]>(`/disputes?pending=${pending}`, { keepPreviousData: true });
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

  return (
    <>
      <PageHeader
        eyebrow="Disputes"
        title={isLead ? "Duyệt khiếu nại" : "Khiếu nại của tôi"}
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
                    <td style={{ whiteSpace: "normal", maxWidth: 420 }}>{d.reason}</td>
                    <td>
                      {d.decision ? (
                        <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                          <Pill tone={d.decision === "accepted" ? "good" : "bad"} icon={d.decision === "accepted" ? "check" : "close"}>
                            {d.decision === "accepted" ? "Chấp nhận" : "Từ chối"}
                          </Pill>
                          {d.decided_at && <span className="muted small">{date(d.decided_at)}</span>}
                        </span>
                      ) : d.raised_by !== me?.id && isLead ? (
                        <span style={{ display: "flex", gap: 6 }}>
                          <button className="primary sm" disabled={busy === d.id} onClick={() => decide(d, "accepted")}>
                            Chấp nhận
                          </button>
                          <button className="sm" disabled={busy === d.id} onClick={() => decide(d, "rejected")}>
                            Từ chối
                          </button>
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
