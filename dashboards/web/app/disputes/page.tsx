"use client";

import Link from "next/link";
import { useState } from "react";

import { Loading, SeverityBadge } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { Dispute, Me } from "@/lib/types";

export default function DisputesPage() {
  const [pending, setPending] = useState(true);
  const { data: me } = useApi<Me>("/auth/me");
  const { data, error, mutate } = useApi<Dispute[]>(`/disputes?pending=${pending}`);
  const [message, setMessage] = useState<string | null>(null);

  async function decide(dispute: Dispute, decision: "accepted" | "rejected") {
    try {
      await api.post(`/disputes/${dispute.id}/decide`, { decision });
      setMessage(decision === "accepted" ? "Đã chấp nhận; điểm đã được tính lại." : "Đã từ chối khiếu nại.");
      await mutate();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <h1>Khiếu nại finding</h1>
      <div className="toolbar">
        <select value={String(pending)} onChange={(e) => setPending(e.target.value === "true")}>
          <option value="true">Đang chờ duyệt</option>
          <option value="false">Tất cả</option>
        </select>
        {message && <span className="muted">{message}</span>}
      </div>
      {!data ? (
        <Loading error={error} />
      ) : data.length === 0 ? (
        <p className="muted">Không có khiếu nại nào.</p>
      ) : (
        <div className="card table-wrap">
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
                  <td style={{ whiteSpace: "normal" }}>
                    {d.finding_severity && <SeverityBadge severity={d.finding_severity} />}{" "}
                    <Link href={`/reviews/${d.review_id}`}>{d.finding_title ?? `#${d.finding_id}`}</Link>
                  </td>
                  <td>{d.raised_by_name ?? "—"}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 380 }}>{d.reason}</td>
                  <td>
                    {d.decision ? (
                      d.decision === "accepted" ? "Chấp nhận" : "Từ chối"
                    ) : d.raised_by !== me?.id ? (
                      <span style={{ display: "flex", gap: 6 }}>
                        <button className="primary" onClick={() => decide(d, "accepted")}>Chấp nhận</button>
                        <button onClick={() => decide(d, "rejected")}>Từ chối</button>
                      </span>
                    ) : (
                      <span className="muted">Chờ lead duyệt</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
