"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";

import { IconButton } from "@/components/icons";
import { ApiError, api, useApi } from "@/lib/api";
import { attributedText } from "@/lib/format";
import type { AccountLinked, ReviewAuthorAccount, ReviewAuthorOut } from "@/lib/types";

/** "Tâm Trần · tamtd@x.vn" (commit) or "GitLab @tamtd" (MR). */
export function authorLabel(a: ReviewAuthorAccount): string {
  if (a.kind === "gitlab_user") return a.name ? `GitLab @${a.name}` : "Tài khoản GitLab";
  return [a.name, a.email].filter(Boolean).join(" · ") || "Tác giả không tên";
}

/**
 * Admin or manager of the dự án, in the review list: tie the author of a review with no person
 * (git email / GitLab account) to someone, then say whether the author's earlier reviews follow
 * ("Đồng bộ": they take the person and the weeks / months are re-scored) or only what comes next.
 */
export function LinkReviewAuthor({
  reviewId,
  account,
  onDone,
}: {
  reviewId: number;
  account: ReviewAuthorAccount;
  /** After a link: what to tell the viewer (the list reloads). */
  onDone: (note: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const form = useApi<ReviewAuthorOut>(open ? `/reviews/${reviewId}/author` : null);
  // Already linked (after this review ran): offer that person, a sync moves the reviews.
  const [pick, setPick] = useState(account.user ? String(account.user.id) : "");
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    if (wasOpen.current && !open) button.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const who = authorLabel(account);
  const chosen =
    pick === "create"
      ? account.name || account.email || "người mới"
      : (() => {
          const p = form.data?.candidates.find((c) => String(c.id) === pick) ?? (account.user && String(account.user.id) === pick ? account.user : null);
          return p ? (p.full_name ?? p.primary_email ?? `#${p.id}`) : "";
        })();

  const close = () => {
    if (busy) return;
    setAsking(false);
    setError(null);
    setOpen(false);
  };

  function ask(e: FormEvent) {
    e.preventDefault();
    if (pick) setAsking(true);
  }

  async function link(sync: boolean) {
    setBusy(true);
    setError(null);
    try {
      const done = await api.post<AccountLinked>(
        `/reviews/${reviewId}/author`,
        pick === "create" ? { create: true, sync } : { user_id: Number(pick), sync },
      );
      setAsking(false);
      setOpen(false);
      const head = `Đã gắn ${who} với ${chosen}.`;
      onDone(
        sync
          ? `${head}${attributedText(done.reviews_attributed, done.periods_queued) || " Không có review cũ nào cần chuyển."}`
          : `${head} Review cũ giữ nguyên; bấm "Đồng bộ" ở dòng đó khi cần.`,
      );
    } catch (err) {
      setAsking(false);
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open)
    return (
      <button ref={button} type="button" className="link small" onClick={() => setOpen(true)} aria-label={`Gắn tác giả ${who} với một người`}>
        {account.user ? "Đồng bộ" : "Gắn"}
      </button>
    );

  const pending = form.data?.reviews ?? 0;
  return (
    <>
      <form
        onSubmit={ask}
        onKeyDown={(e) => e.key === "Escape" && !asking && close()}
        style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}
      >
        <select
          className="compact"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
          aria-label={`Gắn tác giả ${who} với`}
          autoFocus
          style={{ maxWidth: "min(240px, 60vw)" }}
        >
          <option value="">{form.data ? "Gắn với…" : form.error ? "Không tải được" : "Đang tải…"}</option>
          {form.data?.can_create && <option value="create">Tạo người mới: {account.name ?? account.email}</option>}
          {(form.data?.candidates ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name ?? u.primary_email}
              {u.full_name && u.primary_email ? ` · ${u.primary_email}` : ""}
            </option>
          ))}
        </select>
        <button type="submit" className="compact" disabled={!pick || busy}>
          Gắn
        </button>
        <button type="button" className="ghost compact" onClick={close} disabled={busy}>
          Huỷ
        </button>
        {(error || form.error) && (
          <span className="bad small" role="alert">
            {error ?? (form.error instanceof ApiError ? form.error.message : String(form.error))}
          </span>
        )}
      </form>
      {asking && (
        <div className="modal-backdrop" onClick={() => !busy && setAsking(false)}>
          <div
            className="modal card"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`link-author-${reviewId}`}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && !busy && setAsking(false)}
          >
            <div className="modal-head">
              <h2 id={`link-author-${reviewId}`}>Đồng bộ dữ liệu cũ?</h2>
              <IconButton icon="close" label="Đóng" onClick={() => setAsking(false)} disabled={busy} />
            </div>
            <p>
              Gắn <strong>{who}</strong> với <strong>{chosen}</strong>. Từ nay mọi commit/MR của tác giả này được tính cho{" "}
              {chosen}.
            </p>
            <p>
              {pending > 0 ? (
                <>
                  Có <strong>{pending}</strong> review cũ của tác giả này chưa có người.{" "}
                </>
              ) : (
                "Chưa thấy review cũ nào của tác giả này đang chờ gắn. "
              )}
              <strong>Đồng bộ</strong> rà lại mọi review của tác giả này, tính chúng cho {chosen} và tính lại điểm các tuần/tháng
              liên quan (chạy nền vài phút).
            </p>
            <div className="toolbar">
              <button type="button" className="primary" autoFocus onClick={() => void link(true)} disabled={busy}>
                {busy ? "Đang gắn…" : "Có, đồng bộ"}
              </button>
              <button type="button" onClick={() => void link(false)} disabled={busy}>
                Không, chỉ gắn
              </button>
              <button type="button" className="ghost" onClick={() => setAsking(false)} disabled={busy}>
                Huỷ
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
