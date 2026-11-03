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
 * fixing commits; their lead, a manager of their team or an admin runs the check, here or on the
 * page "Đóng finding"; an
 * admin may also close or reopen it by hand with a note. Closing keeps the severity and the
 * score: a wrong finding is a dispute instead.
 */

const SEVERE = new Set(["critical", "high"]);
/** As the API (app/api/routers/closures.py MIN_ARGUMENT). */
export const MIN_ARGUMENT = 50;

/** What a closing made of the finding. */
export const CLOSED_AS: Record<string, string> = {
  fixed: "Đã sửa",
  accepted_risk: "Chấp nhận rủi ro",
  false_positive: "Finding sai",
};

/** What an accepted argument or a commit-less close makes of the finding (as the API's Settle). */
export type Settle = "medium" | "low" | "false_positive";
const RANK: Record<string, number> = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };
const SETTLE_LABEL: Record<Settle, string> = {
  medium: "Hạ xuống medium",
  low: "Hạ xuống low",
  false_positive: "Finding sai (loại khỏi điểm)",
};

/** Choosing the outcome of a close without a commit: lower to medium or low (never raise; a
 * critical to low only for a manager or an admin, with a reason), or the finding was wrong.
 * Re-scores the finding; closing by commits never comes here. */
export function SettlePicker({
  severity,
  suggested,
  canLowerFar,
  value,
  onChange,
  note,
  onNote,
  id,
  withNote = true,
}: {
  severity: string | null | undefined;
  suggested?: string | null;
  canLowerFar: boolean;
  value: Settle | null;
  onChange: (v: Settle) => void;
  note: string;
  onNote: (v: string) => void;
  id: string;
  /** False where the form has its own note (the admin close: that note is the reason). */
  withNote?: boolean;
}) {
  const now = RANK[severity ?? "medium"] ?? 2;
  const allowed = (o: Settle) =>
    o === "false_positive" || (RANK[o] <= now && !(severity === "critical" && o === "low" && !canLowerFar));
  const needsReason = severity === "critical" && value === "low";
  return (
    <fieldset style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 4 }}>
      <legend className="small" style={{ fontWeight: 600 }}>
        Mức mới của finding (đang {severity ?? "?"}; điểm sẽ được tính lại)
      </legend>
      {(["medium", "low", "false_positive"] as Settle[]).map((o) => (
        <label
          key={o}
          className="small"
          style={{ display: "flex", gap: 6, alignItems: "center", opacity: allowed(o) ? 1 : 0.5 }}
          title={
            allowed(o)
              ? undefined
              : RANK[o] > now
                ? "Không nâng mức"
                : "Chỉ manager của team tác giả hoặc admin được hạ critical xuống low"
          }
        >
          <input
            type="radio"
            name={`settle-${id}`}
            value={o}
            checked={value === o}
            disabled={!allowed(o)}
            onChange={() => onChange(o)}
          />
          {SETTLE_LABEL[o]}
          {suggested === o && <span className="muted">(AI đề xuất)</span>}
        </label>
      ))}
      {withNote && (needsReason || value === "false_positive") && (
        <>
          <label htmlFor={`settle-note-${id}`} className="small" style={{ fontWeight: 600 }}>
            {needsReason ? "Lý do hạ critical xuống low (bắt buộc)" : "Ghi chú (không bắt buộc)"}
          </label>
          <textarea id={`settle-note-${id}`} rows={2} maxLength={1000} value={note} onChange={(e) => onNote(e.target.value)} />
        </>
      )}
    </fieldset>
  );
}

/** The default choice: the check's suggestion when allowed, else medium if it lowers, else low. */
export function defaultSettle(severity: string | null | undefined, suggested: string | null | undefined, canLowerFar: boolean): Settle {
  const now = RANK[severity ?? "medium"] ?? 2;
  const ok = (o: string) => RANK[o] <= now && !(severity === "critical" && o === "low" && !canLowerFar);
  if ((suggested === "medium" || suggested === "low") && ok(suggested)) return suggested;
  return ok("medium") ? "medium" : "low";
}

/** "Xác nhận" of a proposed acceptance: pick the outcome, then confirm. */
export function ConfirmSettle({
  closure,
  severity,
  canLowerFar,
  onDone,
}: {
  closure: FindingClosure;
  severity: string | null | undefined;
  canLowerFar: boolean;
  onDone: (message: string) => Promise<unknown>;
}) {
  const [value, setValue] = useState<Settle>(() => defaultSettle(severity, closure.suggested_severity, canLowerFar));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsReason = severity === "critical" && value === "low";
  return (
    <form
      style={{ display: "grid", gap: 8, maxWidth: 520 }}
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          await api.post(`/close-requests/${closure.id}/confirm`, { outcome: value, note: note.trim() || null });
          await onDone(value === "false_positive" ? "Đã đóng: finding sai, loại khỏi điểm." : `Đã đóng: chấp nhận rủi ro, hạ xuống ${value}.`);
        } catch (err) {
          setError(err instanceof ApiError ? err.message : String(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <SettlePicker
        id={String(closure.id)}
        severity={severity}
        suggested={closure.suggested_severity}
        canLowerFar={canLowerFar}
        value={value}
        onChange={setValue}
        note={note}
        onNote={setNote}
      />
      <div>
        <button type="submit" className="primary sm" disabled={busy || (needsReason && note.trim().length < 5)}>
          Xác nhận
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

export const shortShas = (shas: string[]) => shas.map((s) => s.slice(0, 10)).join(", ");

/** How the finding was closed, or what became of the last request. */
/** What the lead/manager/admin does with a request, on the review page: run the AI check (again
 * when it failed), confirm a proposed acceptance, or refuse with a note the author sees. */
function DecideBar({ closure, onDone }: { closure: FindingClosure; onDone: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [refusing, setRefusing] = useState(false);
  const [note, setNote] = useState("");
  const act = async (path: string, body: unknown, ok: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/close-requests/${closure.id}/${path}`, body);
      setDone(ok);
      setRefusing(false);
      await onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  if (!closure.can_decide && !closure.can_cancel) return null;
  return (
    <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
      {closure.can_decide && closure.status === "proposed" && !refusing && (
        <ConfirmSettle
          closure={closure}
          severity={closure.finding_severity}
          canLowerFar={!!closure.can_lower_far}
          onDone={async (m) => {
            setDone(m);
            await onDone();
          }}
        />
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {closure.can_decide && (closure.status === "pending" || closure.status === "failed") && (
          <button
            className="primary sm"
            disabled={busy}
            title="AI đọc commit sửa và/hoặc phản biện của tác giả cùng mã hiện tại, rồi đóng finding nếu đủ điều kiện. Tốn một ít ngân sách LLM."
            onClick={() => void act("run", undefined, "Đã gửi AI review; kết quả hiện ở đây sau ít phút.")}
          >
            {closure.status === "failed" ? "Chạy review lại" : "Chạy review"}
          </button>
        )}
        {closure.can_decide && !refusing && (
          <button className="sm" disabled={busy} onClick={() => setRefusing(true)}>
            Từ chối
          </button>
        )}
        {closure.can_cancel && (
          <button className="ghost sm" disabled={busy} onClick={() => void act("cancel", undefined, "Đã rút yêu cầu.")}>
            Rút yêu cầu
          </button>
        )}
      </div>
      {refusing && (
        <form
          style={{ display: "grid", gap: 6, maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            void act("reject", { note: note.trim() }, "Đã từ chối yêu cầu.");
          }}
        >
          <label htmlFor={`refuse-${closure.id}`} className="small" style={{ fontWeight: 600 }}>
            Lý do từ chối (tác giả sẽ thấy)
          </label>
          <textarea id={`refuse-${closure.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
          <div style={{ display: "flex", gap: 6 }}>
            <button type="submit" className="sm" disabled={busy || note.trim().length < 5}>
              Gửi từ chối
            </button>
            <button type="button" className="ghost sm" disabled={busy} onClick={() => setRefusing(false)}>
              Thôi
            </button>
          </div>
        </form>
      )}
      {done && !error && (
        <span className="small muted" role="status">
          {done}
        </span>
      )}
      {error && (
        <span className="error small" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

const WHO_DECIDES = "lead hoặc manager của tác giả, hoặc admin";

export function ClosureNote({ closure, onDone }: { closure: FindingClosure | undefined; onDone: () => Promise<unknown> }) {
  if (!closure) return null;
  const when = closure.decided_at ? ` · ${date(closure.decided_at)}` : "";
  if (closure.status === "pending")
    return (
      <div className="small" style={{ marginTop: 8, display: "grid", gap: 4 }}>
        <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Pill tone="info">Chờ duyệt</Pill>
          <span className="muted">
            Đã gửi yêu cầu đóng {basis(closure)} · {date(closure.created_at)}.{" "}
            {closure.can_decide
              ? "Bạn có thể chạy AI review để kiểm, hoặc từ chối."
              : closure.can_cancel
                ? `Chờ ${WHO_DECIDES} chạy review; bạn không tự duyệt yêu cầu của mình.`
                : `Chờ ${WHO_DECIDES} chạy review.`}
          </span>
        </span>
        {closure.argument && <span className="muted">Phản biện: {closure.argument}</span>}
        <DecideBar closure={closure} onDone={onDone} />
      </div>
    );
  if (closure.status === "proposed")
    return (
      <div className="small" style={{ marginTop: 8, display: "grid", gap: 4 }}>
        <span>
          <Pill tone="info">Đề xuất chấp nhận rủi ro</Pill>{" "}
          <span className="muted">
            Phản biện được code chứng minh
            {closure.suggested_severity ? ` (AI đề xuất hạ xuống ${closure.suggested_severity})` : ""};{" "}
            {closure.can_decide
              ? "hãy mở file bằng chứng trên GitLab, chọn mức mới rồi xác nhận (hoặc từ chối)."
              : `chờ ${WHO_DECIDES} xác nhận và chọn mức mới.`}
          </span>
        </span>
        {closure.reason && <span className="muted">{closure.reason}</span>}
        {closure.evidence && (
          <span className="muted">
            Bằng chứng: <code>{closure.evidence}</code>
          </span>
        )}
        <DecideBar closure={closure} onDone={onDone} />
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
        {closure.closed_as === "accepted_risk" && closure.severity_before && closure.severity_after && (
          <span>
            <Pill tone="info">
              Hạ mức {closure.severity_before} → {closure.severity_after}
            </Pill>{" "}
            <span className="muted">điểm đã được tính lại theo mức mới</span>
          </span>
        )}
        {closure.closed_as === "false_positive" && <span className="muted">Finding sai: đã loại khỏi điểm.</span>}
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
    <div className="small" style={{ marginTop: 8 }} role="status">
      <Pill tone={closure.status === "failed" ? "warn" : "bad"}>
        {closure.status === "failed" ? "Chưa kiểm được" : "Chưa đủ điều kiện đóng"}
      </Pill>{" "}
      <span className="muted">
        {basis(closure)}
        {when}: {closure.reason}
      </span>
      {closure.status === "failed" && <DecideBar closure={closure} onDone={onDone} />}
    </div>
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
  // An admin's close: "fixed" by commits (severity and score kept) or accepting the risk.
  const [how, setHow] = useState<"fixed" | "risk">("fixed");
  const [settle, setSettle] = useState<Settle>(() => defaultSettle(finding.severity, null, true));
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
      : mode === "close" && how === "fixed"
        ? note.trim().length >= 5 && list.length > 0 && list.length <= 5
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
              : mode === "close"
                ? how === "fixed"
                  ? { note: note.trim(), commits: list }
                  : { note: note.trim(), outcome: settle }
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
            Lead, manager của team bạn hoặc admin sẽ chạy review: hệ thống đối chiếu commit và/hoặc phản biện với code hiện
            tại của nhánh chính (file của finding và các file bạn nhắc tới; giá trị trong file .env được che). Đóng nhờ
            commit (“Đã sửa”) thì giữ mức và giữ điểm. Đóng nhờ phản biện (“Chấp nhận rủi ro”) thì finding được hạ xuống
            medium hoặc low và điểm được tính lại; finding sai hẳn thì nên dùng Khiếu nại. Không đạt thì lý do hiện ngay
            dưới finding.
          </span>
        </>
      )}
      {mode === "close" && (
        <fieldset style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 6 }}>
          <legend className="small" style={{ fontWeight: 600 }}>
            Đóng vì
          </legend>
          <label className="small" style={{ display: "flex", gap: 6 }}>
            <input type="radio" name={`${id}-how`} checked={how === "fixed"} onChange={() => setHow("fixed")} />
            Đã sửa bằng commit: giữ mức {finding.severity} và giữ điểm
          </label>
          <label className="small" style={{ display: "flex", gap: 6 }}>
            <input type="radio" name={`${id}-how`} checked={how === "risk"} onChange={() => setHow("risk")} />
            Chấp nhận rủi ro (không có commit): hạ mức và tính lại điểm
          </label>
          {how === "fixed" ? (
            <>
              <label htmlFor={`${id}-commits`} className="small" style={{ fontWeight: 600 }}>
                Commit đã sửa (bắt buộc, tối đa 5)
              </label>
              <textarea
                id={`${id}-commits`}
                rows={2}
                value={commits}
                placeholder="Mã commit hoặc link GitLab, mỗi dòng một"
                onChange={(e) => setCommits(e.target.value)}
              />
            </>
          ) : (
            <SettlePicker
              id={id}
              severity={finding.severity}
              canLowerFar
              value={settle}
              onChange={setSettle}
              note=""
              onNote={() => undefined}
              withNote={false}
            />
          )}
        </fieldset>
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
