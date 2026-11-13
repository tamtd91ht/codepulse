"use client";

import { useCallback, useEffect, useState } from "react";

import { KIND_LABEL, NoteRow, useInbox } from "@/components/Inbox";
import { Alert, Card, EmptyState, Loading, PageHeader, Segmented } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { InboxNote, InboxPage, NotificationKind } from "@/lib/types";

const PAGE = 30;
type Show = "all" | "unread";

/** The history of one's notifications (docs/specs/24): kept 180 days, newest first. */
export default function NotificationsPage() {
  const inbox = useInbox();
  const [show, setShow] = useState<Show>("all");
  const [kind, setKind] = useState<NotificationKind | "">("");
  const [items, setItems] = useState<InboxNote[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const query = useCallback(
    (before?: number | null) => {
      const params = new URLSearchParams({ limit: String(PAGE) });
      if (show === "unread") params.set("unread", "true");
      if (kind) params.set("kind", kind);
      if (before) params.set("before", String(before));
      return `/inbox?${params}`;
    },
    [show, kind],
  );

  const load = useCallback(async () => {
    setError(null);
    setItems(null);
    try {
      const page = await api.get<InboxPage>(query());
      setItems(page.items);
      setNext(page.next_before);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  // A notification arriving while the page is open goes on top (when it fits the filter).
  useEffect(() => {
    if (!inbox) return;
    return inbox.subscribe((note) => {
      if (kind && note.kind !== kind) return;
      setItems((list) => (list ? [note, ...list.filter((n) => n.id !== note.id)] : list));
    });
  }, [inbox, kind]);

  async function more() {
    if (!next) return;
    setLoadingMore(true);
    try {
      const page = await api.get<InboxPage>(query(next));
      setItems((list) => [...(list ?? []), ...page.items]);
      setNext(page.next_before);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
  }

  function markOne(note: InboxNote) {
    if (note.read_at) return;
    const now = new Date().toISOString();
    setItems((list) => list?.map((n) => (n.id === note.id ? { ...n, read_at: now } : n)) ?? list);
    void inbox?.markRead([note.id]);
  }

  async function markAll() {
    const now = new Date().toISOString();
    setItems((list) => (show === "unread" ? [] : (list?.map((n) => ({ ...n, read_at: n.read_at ?? now })) ?? list)));
    await inbox?.markRead();
  }

  const unread = inbox?.unread ?? 0;
  return (
    <>
      <PageHeader
        eyebrow="Thông báo"
        title="Thông báo"
        subtitle="Review xong, finding được đóng, khiếu nại mới và khiếu nại đã xử lý. Lịch sử giữ 180 ngày."
        actions={
          unread > 0 && (
            <button type="button" onClick={() => void markAll()}>
              Đánh dấu đã đọc tất cả ({unread})
            </button>
          )
        }
      />
      <Card
        collapsible={false}
        title="Lịch sử"
        actions={
          <span className="inbox-filters">
            <Segmented<Show>
              label="Hiển thị"
              value={show}
              onChange={setShow}
              options={[
                { value: "all", label: "Tất cả" },
                { value: "unread", label: "Chưa đọc" },
              ]}
            />
            <select aria-label="Loại thông báo" value={kind} onChange={(e) => setKind(e.target.value as NotificationKind | "")}>
              <option value="">Mọi loại</option>
              {(Object.keys(KIND_LABEL) as NotificationKind[]).map((k) => (
                <option key={k} value={k}>
                  {KIND_LABEL[k]}
                </option>
              ))}
            </select>
          </span>
        }
      >
        {error ? (
          <Alert tone="error">
            Lỗi: {error}{" "}
            <button type="button" className="link sm" onClick={() => void load()}>
              Thử lại
            </button>
          </Alert>
        ) : items === null ? (
          <Loading rows={5} />
        ) : items.length === 0 ? (
          <EmptyState icon="bell" title={show === "unread" ? "Không còn thông báo chưa đọc." : "Chưa có thông báo nào."}>
            Thông báo đến khi review của bạn xong, finding được đóng, hoặc có khiếu nại cần bạn xử lý.
          </EmptyState>
        ) : (
          <div className="inbox-list">
            {items.map((n) => (
              <NoteRow key={n.id} note={n} onOpen={markOne} />
            ))}
            {next && (
              <div className="inbox-more">
                <button type="button" onClick={() => void more()} disabled={loadingMore}>
                  {loadingMore ? "Đang tải…" : "Tải thêm"}
                </button>
              </div>
            )}
          </div>
        )}
      </Card>
    </>
  );
}
