"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/icons";
import { Avatar } from "@/components/ui";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

/**
 * The signed-in person, top right of the top bar (omicx-ui app shell): the avatar opens a small
 * panel with who they are, a link to their own page and sign-out.
 */
export function UserMenu({ me, role }: { me: Me; role: string }) {
  const [open, setOpen] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const outside = (target: EventTarget | null) => !wrapRef.current?.contains(target as Node);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => outside(e.target) && setOpen(false);
    // Tabbing out closes it, so it never stays open behind the focus.
    const onFocus = (e: FocusEvent) => outside(e.target) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("focusin", onFocus);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("focusin", onFocus);
    };
  }, [open]);

  const logout = async () => {
    setLogoutFailed(false);
    try {
      await api.post("/auth/logout");
      window.location.href = "/login";
    } catch {
      // The session is still live; say so instead of pretending to be signed out.
      setLogoutFailed(true);
    }
  };

  const name = me.full_name ?? me.email ?? "";
  return (
    <div className="user-menu" ref={wrapRef}>
      <button
        type="button"
        className="user-menu-btn"
        ref={buttonRef}
        aria-label={`Tài khoản: ${name}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar name={me.full_name} email={me.email} src={me.avatar_url} />
      </button>
      {open && (
        <div className="user-menu-panel" id={panelId} role="dialog" aria-label="Tài khoản">
          <div className="user-menu-head">
            <Avatar name={me.full_name} email={me.email} src={me.avatar_url} size="lg" />
            <div className="who">
              <div className="name">{name}</div>
              {me.email && me.full_name && <div className="email">{me.email}</div>}
              <div className="role">{role}</div>
            </div>
          </div>
          <div className="user-menu-list">
            <Link href={`/members/${me.id}`} className="user-menu-item" onClick={() => setOpen(false)}>
              <Icon name="user" size={18} />
              Của tôi
            </Link>
            <Link href="/notifications" className="user-menu-item" onClick={() => setOpen(false)}>
              <Icon name="bell" size={18} />
              Lịch sử thông báo
            </Link>
            <button type="button" className="user-menu-item tone-danger" onClick={() => void logout()}>
              <Icon name="logout" size={18} />
              Đăng xuất
            </button>
          </div>
          {logoutFailed && (
            <p className="user-menu-error error small" role="alert">
              Đăng xuất thất bại, thử lại.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
