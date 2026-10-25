"use client";

import { useEffect, useRef } from "react";

import { useApi } from "@/lib/api";
import type { LiveEvent, LiveReview } from "@/lib/types";

const POLL_MS = 2000;
const SHOWN = 80;

function line(e: LiveEvent): string {
  switch (e.kind) {
    case "start":
      return `Bắt đầu${e.model ? ` (${e.model})` : ""}`;
    case "thinking":
      return "đang suy nghĩ…";
    case "answer":
      return "đang trả kết quả";
    case "end":
      return e.ok ? `Xong sau ${e.turns ?? e.turn} lượt` : "Dừng (lỗi hoặc hết lượt)";
    default: {
      const where = [e.path && `trong ${e.path}`, e.glob && `(${e.glob})`, e.lines && `từ dòng ${e.lines}`]
        .filter(Boolean)
        .join(" ");
      return [e.target, where].filter(Boolean).join(" ");
    }
  }
}

/**
 * What the deep review agent does right now: the files it opens and the patterns it searches,
 * polled from the worker's progress records. Contents and the model's text are never sent.
 */
export function LiveLog({ reviewId }: { reviewId: number }) {
  const { data, error } = useApi<LiveReview>(`/activity/reviews/${reviewId}/live`, {
    redirect: false,
    keepPreviousData: true,
    refreshInterval: (d?: LiveReview) => (d && !d.running ? 0 : POLL_MS),
  });
  const box = useRef<HTMLOListElement>(null);
  const events = (data?.events ?? []).slice(-SHOWN);

  useEffect(() => {
    const el = box.current;
    // Follow the newest line unless the person scrolled up to read.
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  if (error) return <p className="muted small">Không tải được diễn biến: {error.message}</p>;
  if (!data) return <p className="muted small">Đang tải…</p>;
  if (events.length === 0)
    return <p className="muted small">Chưa có hoạt động (diễn biến chỉ có ở bước review sâu).</p>;
  return (
    <ol ref={box} className="live-log" aria-label="Diễn biến review sâu" aria-live="off">
      {events.map((e, i) => (
        <li key={`${i}-${e.at}`}>
          <span className="turn">#{e.turn}</span>
          {e.kind === "tool" && <span className="tool">{e.tool}</span>}
          <span>{line(e)}</span>
        </li>
      ))}
    </ol>
  );
}
