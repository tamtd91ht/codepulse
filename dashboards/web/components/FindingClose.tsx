"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { Pill } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { date } from "@/lib/format";
import type { Finding, FindingClosure } from "@/lib/types";

/**
 * Closing a critical/high finding a later change fixed (app/review/resolve.py), on the review
 * page: how it was closed or where the request stands, and the forms. The author asks with the
 * fixing commits; a manager of their team or an admin runs the check (page "Đóng finding"); an
 * admin may also close or reopen it by hand with a note. Closing keeps the severity and the
 * score: a wrong finding is a dispute instead.
 */

const SEVERE = new Set(["critical", "high"]);
/** As the API (app/api/routers/closures.py MIN_ARGUMENT). */
export const MIN_ARGUMENT = 50;

/** What a closing made of the finding. */
export const CLOSED_AS: Record<string, string> = { fixed: "Đã sửa", accepted_risk: "Chấp nhận rủi ro" };

export const shortShas = (shas: string[]) => shas.map((s) => s.slice(0, 10)).join(", ");

/** How the finding was closed, or what became of the last request. */
export function ClosureNote({ closure, onDone }: { closure: FindingClosure | undefined; onDone: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!closure) return null;
  const when = closure.decided_at ? ` · ${date(closure.decided_at)}` : "";
  if (closure.status === "pending")
    return (
      <div className="small" style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Pill tone="info">Chờ duyệt</Pill>
        <span className="muted">
          Đã gửi yêu cầu đóng {basis(closure)} · {date(closure.created_at)}. Manager hoặc admin sẽ chạy review để kiểm.
        </span>
        <button
          className="ghost sm"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await api.post(`/close-requests/${closure.id}/cancel`);
              await onDone();
            } catch (e) {
              setError(e instanceof ApiError ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Rút yêu cầu
        </button>
        {error && (
          <span className="error" role="alert">
            {error}
          </span>
        )}
      </div>
    );
  if (closure.status === "proposed")
    return (
      <div className="small" style={{ marginTop: 8, display: "grid", gap: 4 }}>
        <span>
          <Pill tone="info">Đề xuất chấp nhận rủi ro</Pill>{" "}
          <span className="muted">Phản biện được code chứng minh; chờ manager hoặc admin xác nhận.</span>
        </span>
        {closure.reason && <span className="muted">{closure.reason}</span>}
        {closure.evidence && (
          <span className="muted">
            Bằng chứng: <code>{closure.evidence}</code>
          </span>
        )}
      </div>
    );
  if (closure.status === "checking")
    return (
      <p className="small" style={{ marginTop: 8 }}>
        <span className="spinner inline" aria-hidden="true" /> Đang review {basis(closure)}…
      </p>
    );
  if (closure.status === "closed") {
    const how =
      closure.kind === "auto" ? (
        <>
          tự động, bởi thay đổi của{" "}
          {closure.review_id ? <Link href={`/reviews/${closure.review_id}`}>review #{closure.review_id}</Link> : "một review sau"}
          {closure.shas.length ? ` (commit ${shortShas(closure.shas)})` : ""}
        </>
      ) : closure.kind === "request" ? (
        <>theo yêu cầu, {basis(closure)}</>
      ) : (
        <>admin đóng thủ công</>
      );
    return (
      <div className="small" style={{ marginTop: 8, display: "grid", gap: 4 }}>
        <span>
          <Pill tone="good" icon="check">
            Đã đóng · {CLOSED_AS[closure.closed_as ?? "fixed"] ?? "Đã sửa"}
          </Pill>{" "}
          {how}
          {when}
        </span>
        {closure.reason && <span className="muted">{closure.reason}</span>}
        {closure.evidence && (
          <span className="muted">
            Chỗ sửa: <code>{closure.evidence}</code>
          </span>
        )}
        {closure.note && <span className="muted">Ghi chú: {closure.note}</span>}
      </div>
    );
  }
  if (closure.status === "reopened")
    return (
      <p className="small muted" style={{ marginTop: 8 }}>
        Admin đã mở lại{when}: {closure.note}
      </p>
    );
  if (closure.status === "cancelled")
    return (
      <p className="small muted" style={{ marginTop: 8 }}>
        Đã rút yêu cầu đóng ({basis(closure)}){when}.
      </p>
    );
  return (
    <p className="small" style={{ marginTop: 8 }} role="status">
      <Pill tone={closure.status === "failed" ? "warn" : "bad"}>
        {closure.status === "failed" ? "Chưa kiểm được" : "Chưa đủ điều kiện đóng"}
      </Pill>{" "}
      <span className="muted">
        {basis(closure)}
        {when}: {closure.reason}
      </span>
    </p>
  );
}

/** "với commit …", "với phản biện", or both. */
export function basis(closure: FindingClosure): string {
  const parts = [];
  if (closure.shas.length) parts.push(`commit ${shortShas(closure.shas)}`);
  if (closure.argument) parts.push("phản biện");
  return parts.length ? `với ${parts.join(" và ")}` : "";
}

type Mode = "request" | "close" | "reopen";

const MODE: Record<Mode, { open: string; submit: string; path: string }> = {
  request: { open: "Yêu cầu đóng", submit: "Gửi yêu cầu", path: "close-requests" },
  close: { open: "Đóng thủ công", submit: "Đóng finding", path: "close" },
  reopen: { open: "Mở lại", submit: "Mở lại finding", path: "reopen" },
};

function CloseForm({ finding, mode, onDone }: { finding: Finding; mode: Mode; onDone: () => Promise<unknown> }) {
  const [open, setOpen] = useState(false);
  const [commits, setCommits] = useState("");
  const [argument, setArgument] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const id = `${mode}-${finding.id}`;
  const list = commits.split(/[\s,]+/).filter(Boolean);
  const argued = argument.trim().length;
  // At least one of the two, as the API checks: commits, or an argument long enough to check.
  const argumentOk = argued === 0 || argued >= MIN_ARGUMENT;
  const ready =
    mode === "request"
      ? list.length <= 5 && argumentOk && (list.length > 0 || argued >= MIN_ARGUMENT)
      : note.trim().length >= 5;
  const why =
    mode !== "request"
      ? null
      : list.length > 5
        ? "Tối đa 5 commit."
        : !argumentOk
          ? `Phản biện cần ít nhất ${MIN_ARGUMENT} ký tự (đang có ${argued}).`
          : list.length === 0 && argued === 0
            ? `Nhập commit đã sửa, hoặc phản biện từ ${MIN_ARGUMENT} ký tự.`
            : null;
  const close = () => {
    setOpen(false);
    requestAnimationFrame(() => opener.current?.focus());
  };
  if (!open)
    return (
      <button className="sm" onClick={() => setOpen(true)} ref={opener}>
        {MODE[mode].open}
      </button>
    );
  return (
    <form
      style={{ display: "grid", gap: 8, maxWidth: 560, marginTop: 10, flexBasis: "100%" }}
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const body =
            mode === "request"
              ? { commits: list, argument: argument.trim() || null, note: note.trim() || null }
              : { note: note.trim() };
          await api.post(`/findings/${finding.id}/${MODE[mode].path}`, body);
          await onDone();
          setOpen(false);
        } catch (err) {
          setError(err instanceof ApiError ? err.message : String(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      {mode === "request" && (
        <>
          <p className="small muted" style={{ margin: 0 }}>
            Cần ít nhất một trong hai: commit đã sửa, hoặc phản biện vì sao finding không đúng ở môi trường thật.
          </p>
          <label htmlFor={`${id}-commits`} style={{ fontWeight: 600 }}>
            Commit đã sửa lỗi này (nếu có)
          </label>
          <textarea
            id={`${id}-commits`}
            rows={3}
            value={commits}
            placeholder={"Mã commit hoặc link GitLab, mỗi dòng một (tối đa 5)\nvd: https://gitlab.company.vn/nhom/repo/-/commit/3f2a9c1"}
            aria-describedby={`${id}-help`}
            onChange={(e) => setCommits(e.target.value)}
            autoFocus
          />
          <label htmlFor={`${id}-argument`} style={{ fontWeight: 600 }}>
            Phản biện (nếu có)
          </label>
          <textarea
            id={`${id}-argument`}
            rows={4}
            maxLength={4000}
            value={argument}
            placeholder={
              "Vì sao finding không đúng ở môi trường thật, và file nào chứng minh.\n" +
              "vd: Key trong .env-dev chỉ dùng cho profile dev để debug; profile prod (.env-prod) không hardcode, bắt buộc đọc AES_KEY từ biến môi trường k8s."
            }
            aria-describedby={`${id}-help ${id}-count`}
            aria-invalid={!argumentOk}
            onChange={(e) => setArgument(e.target.value)}
          />
          <span id={`${id}-count`} className={`small ${argumentOk ? "muted" : "error"}`}>
            {argued}/{MIN_ARGUMENT} ký tự tối thiểu{argued === 0 ? " (bỏ trống nếu chỉ gửi commit)" : ""}
          </span>
          <span id={`${id}-help`} className="muted small">
            Manager của team bạn hoặc admin sẽ chạy review: hệ thống đối chiếu commit và/hoặc phản biện với code hiện tại
            của nhánh chính (file của finding và các file bạn nhắc tới; giá trị trong file .env được che). Đạt thì finding
            được đóng (“Đã sửa” hoặc “Chấp nhận rủi ro”, vẫn giữ mức và vẫn tính điểm); không đạt thì lý do hiện ngay dưới
            finding.
          </span>
        </>
      )}
      <label htmlFor={`${id}-note`} style={{ fontWeight: 600 }}>
        {mode === "request" ? "Ghi chú cho người duyệt (không bắt buộc)" : "Ghi chú (bắt buộc)"}
      </label>
      <textarea
        id={`${id}-note`}
        rows={2}
        value={note}
        placeholder={mode === "reopen" ? "Vì sao mở lại?" : mode === "close" ? "Vì sao đóng không cần kiểm?" : "Đã sửa thế nào?"}
        onChange={(e) => setNote(e.target.value)}
        autoFocus={mode !== "request"}
      />
      {why && (
        <span className="small muted" role="status">
          {why}
        </span>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button className="primary sm" type="submit" disabled={!ready || busy}>
          {busy ? "Đang gửi…" : MODE[mode].submit}
        </button>
        <button className="sm" type="button" onClick={close} disabled={busy}>
          Huỷ
        </button>
      </div>
      {error && (
        <span className="error small" role="alert">
          {error}
        </span>
      )}
    </form>
  );
}

/** The buttons a finding offers: the author asks to close it (critical/high, open); an admin
 * closes or reopens it by hand. */
export function CloseActions({
  finding,
  closure,
  isAuthor,
  isAdmin,
  onDone,
}: {
  finding: Finding;
  closure: FindingClosure | undefined;
  isAuthor: boolean;
  isAdmin: boolean;
  onDone: () => Promise<unknown>;
}) {
  const status = finding.status ?? "open";
  const inFlight = closure?.status === "pending" || closure?.status === "checking" || closure?.status === "proposed";
  const actions: Mode[] = [];
  if (isAuthor && status === "open" && !inFlight && SEVERE.has(finding.severity)) actions.push("request");
  if (isAdmin && status === "open" && closure?.status !== "checking") actions.push("close");
  if (isAdmin && (status === "fixed" || status === "accepted_risk")) actions.push("reopen");
  if (!actions.length) return null;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
      {actions.map((mode) => (
        <CloseForm key={mode} finding={finding} mode={mode} onDone={onDone} />
      ))}
    </div>
  );
}
