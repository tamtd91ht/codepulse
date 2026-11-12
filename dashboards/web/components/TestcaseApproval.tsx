"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Alert, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import type { CaseDetail, CaseReview } from "@/lib/testcases";
import { errorText } from "@/lib/testruns";

/*
 * Approval of a case (docs/specs/30 §15.9 G7, API §15.11): "Gửi duyệt" (draft → review, editors),
 * "Duyệt" / "Từ chối" (review, tests.approve; a rejection needs a comment and goes back to draft),
 * and the list of decisions.
 */

const REVIEW_LABEL: Record<string, string> = { submit: "Gửi duyệt", approve: "Đã duyệt", reject: "Từ chối" };

export function ApprovalBar({ detail, onChanged }: { detail: CaseDetail; onChanged: (c: CaseDetail) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const canSubmit = detail.status === "draft" && detail.can_edit;
  const canDecide = detail.status === "review" && !!detail.can_approve;
  if (!canSubmit && !canDecide && detail.status !== "review") return null;

  const call = async (path: string, body?: unknown) => {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.post<CaseDetail>(`/testcases/${detail.id}/${path}`, body ?? {}));
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tc-approval" role="region" aria-label="Duyệt testcase">
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="tc-approval-row">
        <span className="small">
          {detail.status === "review" ? (
            <>
              <Pill tone="info">Chờ duyệt</Pill> {canDecide ? "Đọc lại nội dung rồi duyệt hoặc từ chối." : "Đang chờ người có quyền duyệt."}
            </>
          ) : detail.approval_on ? (
            "Dự án bắt buộc duyệt: gửi duyệt khi testcase đã viết xong."
          ) : (
            "Gửi cho người có quyền duyệt xem lại testcase này."
          )}
        </span>
        <span className="tc-approval-actions">
          {canSubmit && (
            <button type="button" className="sm" onClick={() => void call("submit")} disabled={busy}>
              <Icon name="arrowUp" size={14} /> Gửi duyệt
            </button>
          )}
          {canDecide && (
            <>
              <button type="button" className="ghost sm tc-danger" onClick={() => setRejecting(true)} disabled={busy}>
                Từ chối
              </button>
              <button type="button" className="primary sm" onClick={() => void call("approve")} disabled={busy}>
                <Icon name="check" size={14} /> Duyệt
              </button>
            </>
          )}
        </span>
      </div>
      {rejecting && (
        <RejectModal
          busy={busy}
          error={error}
          onClose={() => {
            setRejecting(false);
            setError(null);
          }}
          onReject={async (comment) => {
            if (await call("reject", { comment })) setRejecting(false);
          }}
        />
      )}
    </div>
  );
}

function RejectModal({ busy, error, onClose, onReject }: { busy: boolean; error: string | null; onClose: () => void; onReject: (comment: string) => void }) {
  const [comment, setComment] = useState("");
  return (
    <Modal
      title="Từ chối testcase"
      onClose={onClose}
      busy={busy}
      width={480}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="danger" onClick={() => onReject(comment.trim())} disabled={busy || !comment.trim()}>
            {busy ? "Đang gửi…" : "Từ chối"}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <label className="adm-field">
        <span className="small muted">
          Lý do (bắt buộc) — testcase quay về Nháp để người viết sửa <span className="tf-req">*</span>
        </span>
        <textarea rows={4} value={comment} maxLength={2000} onChange={(e) => setComment(e.target.value)} autoFocus aria-required="true" placeholder="Thiếu bước kiểm tra phân quyền, kết quả mong đợi bước 3 chưa rõ…" />
      </label>
    </Modal>
  );
}

/** "Duyệt": approvals and rejections of the case, newest first (remount with a new key to reload). */
export function ReviewHistory({ caseId }: { caseId: number }) {
  const list = useApi<CaseReview[]>(`/testcases/${caseId}/reviews`);
  return (
    <section className="tc-reviews" aria-labelledby="tc-reviews-title">
      <h3 className="tc-h3" id="tc-reviews-title">
        Duyệt
      </h3>
      {!list.data ? (
        <Loading error={list.error} rows={2} />
      ) : !list.data.length ? (
        <p className="small muted">Chưa có lần duyệt nào.</p>
      ) : (
        <ul className="tc-result-list">
          {list.data.map((r) => (
            <li key={r.id}>
              <div className="tc-result-head">
                <Pill tone={r.decision === "approve" ? "good" : r.decision === "reject" ? "bad" : "info"}>
                  {REVIEW_LABEL[r.decision] ?? r.decision}
                </Pill>
                <strong className="small">{r.user_name ?? "—"}</strong>
                <span className="small muted">
                  {dateTimeMin(r.created_at)}
                  {r.revision ? ` · bản ${r.revision}` : ""}
                </span>
              </div>
              {r.comment && <div className="tc-lines small">{r.comment}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
