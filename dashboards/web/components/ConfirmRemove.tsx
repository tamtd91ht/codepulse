"use client";

import { useEffect, useRef, useState } from "react";

import { IconButton } from "@/components/icons";

/**
 * Typed confirmation for deleting repos: the admin pastes `phrase` back (the server checks the
 * same phrase). Nothing here can be undone, so the wording says exactly what goes.
 */
export function ConfirmRemove({
  paths,
  phrase,
  busy,
  onConfirm,
  onCancel,
}: {
  paths: string[];
  phrase: string;
  busy: boolean;
  onConfirm: (typed: string) => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [copied, setCopied] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const matches = typed.trim().toLowerCase() === phrase.toLowerCase();

  useEffect(() => {
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div className="modal-backdrop" onClick={() => !busy && onCancel()}>
      <div
        className="modal card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-remove-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="confirm-remove-title">Gỡ {paths.length} repo khỏi CodePulse?</h2>
          <IconButton icon="close" label="Đóng" onClick={onCancel} disabled={busy} />
        </div>
        <p>
          <strong className="error">Không thể hoàn tác.</strong> Toàn bộ commit, MR, review, finding và khiếu nại của
          các repo này bị xoá; webhook trên GitLab được gỡ; điểm và báo cáo các kỳ trước được tính lại nên sẽ thay đổi.
          Nếu chỉ muốn ngừng theo dõi mà giữ lịch sử, hãy dùng &ldquo;Bỏ theo dõi&rdquo;.
        </p>
        <ul className="modal-list">
          {paths.slice(0, 8).map((p) => (
            <li key={p}>
              <code>{p}</code>
            </li>
          ))}
          {paths.length > 8 && <li className="muted">… và {paths.length - 8} repo khác</li>}
        </ul>
        <p>
          Dán lại chuỗi sau để xác nhận:{" "}
          <code className="phrase">{phrase}</code>{" "}
          <IconButton
            icon="copy"
            label={copied ? "Đã chép" : "Chép chuỗi"}
            onClick={() => {
              void navigator.clipboard?.writeText(phrase).then(() => setCopied(true));
            }}
          />
        </p>
        <form
          className="toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            if (matches && !busy) onConfirm(typed);
          }}
        >
          <input
            ref={input}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={phrase}
            aria-label="Chuỗi xác nhận"
            autoComplete="off"
            spellCheck={false}
          />
          <button className="danger" type="submit" disabled={!matches || busy}>
            {busy ? "Đang gỡ…" : "Gỡ vĩnh viễn"}
          </button>
          <button type="button" onClick={onCancel} disabled={busy}>
            Huỷ
          </button>
        </form>
      </div>
    </div>
  );
}
