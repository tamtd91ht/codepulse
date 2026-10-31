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
          Đã gửi yêu cầu đóng với commit {shortShas(closure.shas)} · {date(closure.created_at)}. Manager hoặc admin sẽ chạy
          review để kiểm.
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
  if (closure.status === "checking")
    return (
      <p className="small" style={{ marginTop: 8 }}>
        <span className="spinner inline" aria-hidden="true" /> Đang review commit {shortShas(closure.shas)} để kiểm đã sửa
        chưa…
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
        <>theo yêu cầu, commit {shortShas(closure.shas)}</>
      ) : (
        <>admin đóng thủ công</>
      );
    return (
      <div className="small" style={{ marginTop: 8, display: "grid", gap: 4 }}>
        <span>
          <Pill tone="good" icon="check">
            Đã đóng
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
        Đã rút yêu cầu đóng (commit {shortShas(closure.shas)}){when}.
      </p>
    );
  return (
    <p className="small" style={{ marginTop: 8 }} role="status">
      <Pill tone={closure.status === "failed" ? "warn" : "bad"}>
        {closure.status === "failed" ? "Chưa kiểm được" : "Chưa đủ điều kiện đóng"}
      </Pill>{" "}
      <span className="muted">
        commit {shortShas(closure.shas)}
        {when}: {closure.reason}
      </span>
    </p>
  );
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
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const id = `${mode}-${finding.id}`;
  const list = commits.split(/[\s,]+/).filter(Boolean);
  const ready = mode === "request" ? list.length > 0 && list.length <= 5 : note.trim().length >= 5;
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
          const body = mode === "request" ? { commits: list, note: note.trim() || null } : { note: note.trim() };
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
          <label htmlFor={`${id}-commits`} style={{ fontWeight: 600 }}>
            Commit đã sửa lỗi này
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
          <span id={`${id}-help`} className="muted small">
            Manager của team bạn hoặc admin sẽ xem và chạy review các commit này. Đạt thì finding được đóng (vẫn giữ mức
            và vẫn tính điểm); không đạt thì lý do hiện ngay dưới finding.
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
  const inFlight = closure?.status === "pending" || closure?.status === "checking";
  const actions: Mode[] = [];
  if (isAuthor && status === "open" && !inFlight && SEVERE.has(finding.severity)) actions.push("request");
  if (isAdmin && status === "open" && closure?.status !== "checking") actions.push("close");
  if (isAdmin && status === "fixed") actions.push("reopen");
  if (!actions.length) return null;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
      {actions.map((mode) => (
        <CloseForm key={mode} finding={finding} mode={mode} onDone={onDone} />
      ))}
    </div>
  );
}
