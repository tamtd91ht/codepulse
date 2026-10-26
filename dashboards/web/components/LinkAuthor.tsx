"use client";

import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

import { ApiError, api, useApi } from "@/lib/api";
import type { Person } from "@/lib/types";

/**
 * Admin, in a commit list: tie an author nobody is recognised as (their git email) to a person,
 * or create the person from the commit's name and email. Every commit of that email follows.
 */
export function LinkAuthor({ email, name, onDone }: { email: string; name: string | null; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const users = useApi<Person[]>(open ? "/users" : null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);

  // Back on the "Gắn" button once the form closes, so keyboard users keep their place.
  useEffect(() => {
    if (wasOpen.current && !open) button.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const close = () => {
    if (busy) return;
    setPick("");
    setError(null);
    setOpen(false);
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!pick) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/identity/authors", pick === "create" ? { email, name, create: true } : { email, name, user_id: Number(pick) });
      setPick("");
      setOpen(false);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open)
    return (
      <button ref={button} type="button" className="link small" onClick={() => setOpen(true)} aria-label={`Gắn tác giả ${email} với một người`}>
        Gắn
      </button>
    );
  return (
    <form
      onSubmit={submit}
      onKeyDown={(e: KeyboardEvent) => e.key === "Escape" && close()}
      style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}
    >
      <select
        className="compact"
        value={pick}
        onChange={(e) => setPick(e.target.value)}
        aria-label={`Gắn tác giả ${email} với`}
        autoFocus
        style={{ maxWidth: "min(240px, 60vw)" }}
      >
        <option value="">{users.data ? "Gắn với…" : "Đang tải…"}</option>
        <option value="create">Tạo người mới: {name ?? email}</option>
        {(users.data ?? [])
          .filter((u) => u.active !== false)
          .map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name ?? u.primary_email}
              {u.full_name && u.primary_email ? ` · ${u.primary_email}` : ""}
            </option>
          ))}
      </select>
      <button type="submit" className="compact" disabled={!pick || busy}>
        {busy ? "…" : "Lưu"}
      </button>
      <button type="button" className="ghost compact" onClick={close} disabled={busy}>
        Huỷ
      </button>
      {error && (
        <span className="bad small" role="alert">
          {error}
        </span>
      )}
    </form>
  );
}
