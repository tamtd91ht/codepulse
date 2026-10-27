"use client";

import Link from "next/link";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { LiveLog } from "@/components/LiveLog";
import { ExternalLink } from "@/components/ui";
import { useApi } from "@/lib/api";
import { TRIGGER_LABEL } from "@/lib/format";
import type { ActivityItem, ActivityOut } from "@/lib/types";

/** Poll fast while something runs, slowly when idle; SWR pauses while the tab is hidden. */
const BUSY_MS = 4000;
const IDLE_MS = 20000;

const STAGES: { id: string; label: string }[] = [
  { id: "diff", label: "Lấy diff" },
  { id: "checkout", label: "Tải mã nguồn" },
  { id: "agent", label: "Review AI" },
  { id: "verify", label: "Kiểm chứng" },
  { id: "save", label: "Chấm điểm" },
];
const STAGE_LABEL: Record<string, string> = {
  start: "Chuẩn bị",
  diff: "Lấy diff từ GitLab",
  checkout: "Tải bản sao repo",
  llm: "AI đang review (không tool)",
  agent: "AI đang review trong repo",
  verify: "Kiểm chứng độc lập từng finding",
  profile: "Dựng hồ sơ dự án",
  deep: "Review sâu",
  save: "Chấm điểm và lưu",
};
const OUTCOME: Record<string, { label: string; ok: boolean }> = {
  done: { label: "xong", ok: true },
  skipped: { label: "bỏ qua", ok: false },
  failed: { label: "lỗi", ok: false },
};

const targetLabel = (i: ActivityItem) => (i.kind === "mr" ? `MR !${i.ref}` : `commit ${i.ref.slice(0, 8)}`);
const repoName = (path: string) => path.split("/").pop() ?? path;
const itemKey = (i: ActivityItem) => `${i.state}:${i.review_id ?? ""}:${i.kind}:${i.project_id}:${i.ref}`;

function elapsed(from: string | null, now: number): string {
  if (!from) return "";
  const s = Math.max(0, Math.round((now - new Date(from).getTime()) / 1000));
  if (!Number.isFinite(s)) return "";
  if (s < 60) return `${s} giây`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} phút ${s % 60}s` : `${Math.floor(m / 60)} giờ ${m % 60} phút`;
}

/** "profile" happens inside the deep pass; "start" before the diff. */
function stageIndex(stage: string | null): number {
  const id =
    stage === "profile" || stage === "deep" || stage === "llm"
      ? "agent"
      : !stage || stage === "start"
        ? "diff"
        : stage;
  return STAGES.findIndex((s) => s.id === id);
}

/**
 * Live "đang review …" indicator (docs/specs/17 §Tiến trình): a pill in the corner that opens a
 * panel with every running and waiting review the viewer may see, and the ones that just ended
 * (with their real outcome, reported by the worker).
 */
export function ReviewActivity() {
  const [open, setOpen] = useState(false);
  const { data, error } = useApi<ActivityOut>("/activity/reviews", {
    redirect: false,
    refreshInterval: (d?: ActivityOut) => (d && d.running.length + d.queued_total > 0 ? BUSY_MS : IDLE_MS),
  });
  const [dismissed, setDismissed] = useState<Set<number>>(() => new Set());
  const [watching, setWatching] = useState<number | null>(null);
  const pillRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const panelId = useId();

  const running = data?.running ?? [];
  const queuedTotal = data?.queued_total ?? 0;
  const recent = (data?.recent ?? []).filter((r) => r.review_id === null || !dismissed.has(r.review_id));
  const active = running.length + queuedTotal > 0;

  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  if (!data || (!active && recent.length === 0)) return null;

  function close() {
    setOpen(false);
    // Ended reviews the person has now seen leave the pill.
    setDismissed((prev) => new Set([...prev, ...recent.flatMap((r) => (r.review_id === null ? [] : [r.review_id]))]));
    pillRef.current?.focus();
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      close();
    }
  }

  const first = running[0];
  const summary = first
    ? `Đang review ${repoName(first.project)} ${targetLabel(first)}`
    : queuedTotal
      ? `${queuedTotal} review đang chờ`
      : `${recent.length} review vừa kết thúc`;
  const more = running.length > 1 ? running.length - 1 : 0;

  return (
    <div className="activity" onKeyDown={onKeyDown}>
      <button
        ref={pillRef}
        type="button"
        className={`activity-pill${active ? " busy" : ""}${error ? " stale" : ""}`}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-busy={active}
        title={error ? "Mất kết nối, đang thử lại" : undefined}
        onClick={() => (open ? close() : setOpen(true))}
      >
        {active ? <span className="spinner inline" aria-hidden="true" /> : <Icon name="check" size={14} />}
        <span className="activity-text">
          {summary}
          {error ? " · mất kết nối" : ""}
        </span>
        {more > 0 && <span className="activity-more">+{more}</span>}
      </button>
      <span className="sr-only" aria-live="polite">
        {summary}
      </span>
      {open && (
        <section
          ref={panelRef}
          tabIndex={-1}
          className="activity-panel card"
          id={panelId}
          role="region"
          aria-label="Tiến trình review"
        >
          <div className="activity-head">
            <strong>Tiến trình review</strong>
            <span className="spacer" />
            <IconButton icon="close" label="Đóng" onClick={close} />
          </div>
          <div className="activity-body">
            {running.length > 0 && (
              <>
                <h4>Đang chạy · {running.length}</h4>
                <ul>
                  {running.map((r) => (
                    <li key={itemKey(r)} className="activity-item">
                      <ItemHead item={r} />
                      <Stages stage={r.stage} />
                      <div className="small">
                        <span className="spinner inline" aria-hidden="true" /> {STAGE_LABEL[r.stage ?? "start"] ?? r.stage}
                        {r.detail ? ` · ${r.detail}` : ""}
                        <span className="muted">
                          {" "}
                          · đã chạy <Elapsed from={r.started_at} />
                        </span>
                        {r.review_id !== null && (r.stage === "deep" || r.stage === "profile" || r.stage === "agent" || r.stage === "verify") && (
                          <>
                            {" "}
                            <button
                              type="button"
                              className="link"
                              aria-expanded={watching === r.review_id}
                              onClick={() => setWatching(watching === r.review_id ? null : r.review_id)}
                            >
                              {watching === r.review_id ? "Ẩn diễn biến" : "Xem trực tiếp"}
                            </button>
                          </>
                        )}
                      </div>
                      {r.review_id !== null && watching === r.review_id && <LiveLog reviewId={r.review_id} />}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {data.queued.length > 0 && (
              <>
                <h4>Đang chờ · {queuedTotal}</h4>
                <ul>
                  {data.queued.map((q) => (
                    <li key={itemKey(q)} className="activity-item">
                      <ItemHead item={q} />
                    </li>
                  ))}
                </ul>
                {queuedTotal > data.queued.length && (
                  <p className="muted small">và {queuedTotal - data.queued.length} review khác đang chờ.</p>
                )}
              </>
            )}
            {recent.length > 0 && (
              <>
                <h4>Vừa kết thúc</h4>
                <ul className="activity-recent">
                  {recent.map((f) => {
                    const outcome = f.status ? OUTCOME[f.status] : undefined;
                    const label = `${repoName(f.project)} ${targetLabel(f)}`;
                    return (
                      <li key={itemKey(f)}>
                        <Icon name={outcome?.ok ? "check" : "alert"} size={14} />{" "}
                        {f.review_id !== null && f.status ? <Link href={`/reviews/${f.review_id}`}>{label}</Link> : label}
                        <span className="muted small">
                          {" "}
                          · {outcome ? outcome.label : "lỗi giữa chừng, sẽ thử lại"}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
            {data.hidden > 0 && (
              <p className="muted small">
                {data.hidden} review khác của người ngoài phạm vi của bạn đang chạy hoặc chờ.
              </p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function Stages({ stage }: { stage: string | null }) {
  const current = stageIndex(stage);
  return (
    <ol className="stages" aria-label="Các bước">
      {STAGES.map((s, idx) => {
        const state = idx < current ? "done" : idx === current ? "now" : "todo";
        return (
          <li key={s.id} className={state} aria-current={state === "now" ? "step" : undefined}>
            {s.label}
          </li>
        );
      })}
    </ol>
  );
}

/** Ticks once a second on its own, so the rest of the panel does not re-render. */
function Elapsed({ from }: { from: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return <>{elapsed(from, now)}</>;
}

function ItemHead({ item }: { item: ActivityItem }) {
  const who = [
    item.author ? `của ${item.author}` : "tác giả chưa map",
    item.requested_by ? `${item.requested_by} yêu cầu` : (TRIGGER_LABEL[item.trigger] ?? item.trigger),
    item.round > 1 ? `lần ${item.round}` : null,
    item.loc ? `${item.loc} dòng` : null,
  ].filter(Boolean);
  return (
    <>
      <div className="activity-target">
        <span className="muted">{item.project}</span>{" "}
        {item.web_url ? <ExternalLink href={item.web_url}>{targetLabel(item)}</ExternalLink> : targetLabel(item)}
      </div>
      {item.title && <div className="activity-title">{item.title}</div>}
      <div className="muted small">{who.join(" · ")}</div>
    </>
  );
}
