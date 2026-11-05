"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  createContext,
  type CSSProperties,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { mutate as mutateGlobal } from "swr";

import { Icon, type IconName, IconButton } from "@/components/icons";
import { Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";
import type { InboxNote, InboxPage, NotificationKind } from "@/lib/types";

/*
 * In-app notifications (docs/specs/24): the bell, the toasts and the live stream.
 * The stream is Server-Sent Events on /api/v1/inbox/stream; the browser reconnects on its own
 * and the server replays what it missed (Last-Event-ID). The unread count is also re-read on
 * every (re)connect and every few minutes, so a lost push only delays the badge.
 */

const RECENT = 15;
const TOAST_MS = 6000;
const MAX_TOASTS = 3;
const POLL_MS = 3 * 60 * 1000;

export const KIND_LABEL: Record<NotificationKind, string> = {
  review_done: "Review xong",
  review_failed: "Review lỗi",
  finding_closed: "Đóng finding",
  dispute_new: "Khiếu nại mới",
  dispute_decided: "Khiếu nại đã xử lý",
};

const KIND_ICON: Record<NotificationKind, IconName> = {
  review_done: "review",
  review_failed: "alertCircle",
  finding_closed: "checkCircle",
  dispute_new: "scale",
  dispute_decided: "scale",
};

/** Lists that change with an event, re-read when it arrives (SWR keys are API paths). */
const REFRESH: Record<NotificationKind, string[]> = {
  review_done: ["/reviews", "/activity"],
  review_failed: ["/reviews", "/activity"],
  finding_closed: ["/close-requests", "/reviews"],
  dispute_new: ["/disputes"],
  dispute_decided: ["/disputes", "/reviews"],
};

type Toast = InboxNote & { key: number };

interface InboxState {
  unread: number;
  recent: InboxNote[] | null;
  recentError: string | null;
  loadRecent: () => Promise<void>;
  markRead: (ids?: number[]) => Promise<void>;
  /** Pages showing the history apply live changes too. */
  subscribe: (listener: (note: InboxNote) => void) => () => void;
}

const InboxContext = createContext<InboxState | null>(null);

export function useInbox(): InboxState | null {
  return useContext(InboxContext);
}

/** `enabled`: signed in (the shell renders before /auth/me answers; nothing connects until then). */
export function InboxProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const [unread, setUnread] = useState(0);
  const [recent, setRecent] = useState<InboxNote[] | null>(null);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const listeners = useRef(new Set<(note: InboxNote) => void>());

  const refreshCount = useCallback(async () => {
    try {
      const r = await api.get<{ unread: number }>("/inbox/unread-count");
      setUnread(r.unread);
    } catch {
      /* the badge keeps its last value */
    }
  }, []);

  const loadRecent = useCallback(async () => {
    setRecentError(null);
    try {
      const page = await api.get<InboxPage>(`/inbox?limit=${RECENT}`);
      setRecent(page.items);
      setUnread(page.unread);
    } catch (e) {
      setRecentError(e instanceof Error ? e.message : "Không tải được thông báo");
    }
  }, []);

  const markRead = useCallback(async (ids?: number[]) => {
    const now = new Date().toISOString();
    setRecent((list) => list?.map((n) => (!ids || ids.includes(n.id) ? { ...n, read_at: n.read_at ?? now } : n)) ?? list);
    try {
      const r = await api.post<{ unread: number }>("/inbox/read", ids ? { ids } : {});
      setUnread(r.unread);
    } catch {
      void refreshCount();
    }
  }, [refreshCount]);

  const dismiss = useCallback((key: number) => setToasts((list) => list.filter((t) => t.key !== key)), []);

  useEffect(() => {
    if (!enabled) return;
    void refreshCount();
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let lastId = 0; // the newest id known: a reconnect of ours asks for what came after it
    const seen = new Set<number>();

    const connect = () => {
      if (closed) return;
      source = new EventSource(`/api/v1/inbox/stream${lastId ? `?after=${lastId}` : ""}`);
      source.addEventListener("ready", (event) => {
        const id = Number((event as MessageEvent).lastEventId);
        if (Number.isFinite(id)) lastId = Math.max(lastId, id);
        void refreshCount();
      });
      source.addEventListener("notification", (event) => {
        let note: InboxNote;
        try {
          note = JSON.parse((event as MessageEvent<string>).data) as InboxNote;
        } catch {
          return;
        }
        if (seen.has(note.id)) return;
        seen.add(note.id);
        lastId = Math.max(lastId, note.id);
        if (!note.read_at) setUnread((n) => n + 1);
        setRecent((list) => (list ? [note, ...list.filter((n) => n.id !== note.id)].slice(0, RECENT) : list));
        setToasts((list) => [...list, { ...note, key: note.id }].slice(-MAX_TOASTS));
        for (const listener of listeners.current) listener(note);
        const prefixes = REFRESH[note.kind] ?? [];
        void mutateGlobal((key) => typeof key === "string" && prefixes.some((p) => key.startsWith(p)));
      });
      source.onerror = () => {
        // A refused stream (signed out, server restarting) is closed by the browser: try later.
        if (source?.readyState === EventSource.CLOSED) {
          source.close();
          retry = setTimeout(connect, 30_000);
        }
      };
    };
    connect();
    const poll = setInterval(() => void refreshCount(), POLL_MS);
    return () => {
      closed = true;
      source?.close();
      clearTimeout(retry);
      clearInterval(poll);
    };
  }, [enabled, refreshCount]);

  const subscribe = useCallback((listener: (note: InboxNote) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  const value = useMemo(
    () => ({ unread, recent, recentError, loadRecent, markRead, subscribe }),
    [unread, recent, recentError, loadRecent, markRead, subscribe],
  );
  return (
    <InboxContext.Provider value={enabled ? value : null}>
      {children}
      <Toasts toasts={toasts} onDismiss={dismiss} onOpen={(n) => void markRead([n.id])} />
    </InboxContext.Provider>
  );
}

function ToneMark({ note }: { note: InboxNote }) {
  return (
    <span className={`note-mark tone-${note.tone}`} aria-hidden="true">
      <Icon name={KIND_ICON[note.kind] ?? "info"} size={14} />
    </span>
  );
}

function Toasts({
  toasts,
  onDismiss,
  onOpen,
}: {
  toasts: Toast[];
  onDismiss: (key: number) => void;
  onOpen: (note: InboxNote) => void;
}) {
  return (
    <div className="toasts" role="region" aria-label="Thông báo mới" aria-live="polite">
      {toasts.map((t) => (
        <ToastItem key={t.key} toast={t} onDismiss={onDismiss} onOpen={onOpen} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
  onOpen,
}: {
  toast: Toast;
  onDismiss: (key: number) => void;
  onOpen: (note: InboxNote) => void;
}) {
  const router = useRouter();
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => onDismiss(toast.key), TOAST_MS);
    return () => clearTimeout(timer);
  }, [paused, toast.key, onDismiss]);
  const open = () => {
    onOpen(toast);
    onDismiss(toast.key);
    if (toast.link) router.push(toast.link);
  };
  return (
    <div
      className={`toast tone-${toast.tone}`}
      role="status"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <ToneMark note={toast} />
      <button type="button" className="toast-main" onClick={open}>
        <span className="toast-kind">{KIND_LABEL[toast.kind]}</span>
        <span className="toast-title">{toast.title}</span>
        {toast.body && <span className="toast-body">{toast.body}</span>}
      </button>
      <IconButton icon="close" label="Đóng thông báo" size={14} onClick={() => onDismiss(toast.key)} />
    </div>
  );
}

/** One notification as a row: in the bell's panel and on the history page. */
export function NoteRow({ note, onOpen, compact }: { note: InboxNote; onOpen: (note: InboxNote) => void; compact?: boolean }) {
  const content = (
    <>
      <ToneMark note={note} />
      <span className="note-text">
        <span className="note-title">{note.title}</span>
        {note.body && <span className={`note-body${compact ? " clamp" : ""}`}>{note.body}</span>}
        <span className="note-meta">
          {KIND_LABEL[note.kind]}
          {note.created_at && <> · {relative(note.created_at)}</>}
        </span>
      </span>
      {!note.read_at && <span className="note-dot" role="img" aria-label="Chưa đọc" />}
    </>
  );
  const className = `note-row${note.read_at ? "" : " unread"}`;
  return note.link ? (
    <Link href={note.link} className={className} onClick={() => onOpen(note)}>
      {content}
    </Link>
  ) : (
    <button type="button" className={className} onClick={() => onOpen(note)}>
      {content}
    </button>
  );
}

const PANEL_W = 380;

/** The bell: the unread count, and the latest notifications in a panel next to it. */
export function NotificationBell({ className }: { className?: string }) {
  const inbox = useInbox();
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState<CSSProperties>({});
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const place = useCallback(() => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(PANEL_W, vw - 24);
    // The bell lives in the top bar: the panel opens below it, right-aligned to it.
    setStyle({
      width,
      left: Math.max(12, Math.min(rect.right - width, vw - width - 12)),
      top: rect.bottom + 8,
      maxHeight: Math.min(560, vh - rect.bottom - 20),
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    place();
    void inbox?.loadRecent();
    // Focus moves into the panel (its first control); Esc returns it to the bell.
    requestAnimationFrame(() => panelRef.current?.querySelector<HTMLElement>("a, button")?.focus());
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!panelRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false);
    };
    // Tabbing out of the panel closes it, so it never stays open behind the focus.
    const onFocus = (e: FocusEvent) => {
      const target = e.target as Node;
      if (!panelRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false);
    };
    window.addEventListener("focusin", onFocus);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("focusin", onFocus);
      window.removeEventListener("resize", place);
    };
    // loadRecent is stable; this runs when the panel opens.
  }, [open, place]);

  if (!inbox) return null;
  const { unread, recent, recentError } = inbox;
  const label = unread ? `Thông báo, ${unread} chưa đọc` : "Thông báo";
  return (
    <>
      <span className="bell">
        <IconButton
          icon="bell"
          label={label}
          ref={buttonRef}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className={className}
        />
        {unread > 0 && (
          <span className="bell-count" aria-hidden="true">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </span>
      {/* Portalled: the top bar's backdrop-filter would otherwise become the panel's containing
          block and shift its viewport coordinates by the sidebar's width. */}
      {open && createPortal(
        <div className="bell-panel" role="dialog" aria-label="Thông báo" ref={panelRef} style={style}>
          <div className="bell-head">
            <strong>Thông báo</strong>
            <span className="muted small">{unread ? `${unread} chưa đọc` : "Đã đọc hết"}</span>
            <span style={{ flex: 1 }} />
            {unread > 0 && (
              <button type="button" className="link sm" onClick={() => void inbox.markRead()}>
                Đánh dấu đã đọc tất cả
              </button>
            )}
          </div>
          <div className="bell-list">
            {recentError ? (
              <p className="bell-empty error">
                {recentError}{" "}
                <button type="button" className="link sm" onClick={() => void inbox.loadRecent()}>
                  Thử lại
                </button>
              </p>
            ) : recent === null ? (
              <div className="bell-loading" role="status" aria-label="Đang tải">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} height={44} />
                ))}
              </div>
            ) : recent.length === 0 ? (
              <p className="bell-empty muted small">Chưa có thông báo nào.</p>
            ) : (
              recent.map((n) => (
                <NoteRow
                  key={n.id}
                  note={n}
                  compact
                  onOpen={(note) => {
                    if (!note.read_at) void inbox.markRead([note.id]);
                    setOpen(false);
                  }}
                />
              ))
            )}
          </div>
          <div className="bell-foot">
            <Link href="/notifications" onClick={() => setOpen(false)}>
              Xem tất cả thông báo
            </Link>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
