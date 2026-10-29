"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { IconButton } from "@/components/icons";
import { ExternalLink, Loading, Message, Pill } from "@/components/ui";
import { ApiError, canEditKnowledge, knowledgeApi, knowledgePath, knowledgePoll, useApi } from "@/lib/api";
import { date, dateTime } from "@/lib/format";
import type { KnowledgeResponse, KnowledgeSource, KnowledgeStaleReason, KnowledgeVersion, Me } from "@/lib/types";

export const BUILT_BY_LABEL: Record<string, string> = {
  manual: "rà soát thủ công",
  auto: "tự động sau push",
  first_review: "lần review đầu",
};

/** "Đã duyệt bởi …" or "Chưa duyệt" (docs/specs/19 §7: usable at once, lead approves later). */
export function ApprovalPill({ active }: { active: Pick<KnowledgeVersion, "approved_by" | "approved_at"> }) {
  return active.approved_by ? (
    <Pill tone="good" icon="checkCircle">
      Đã duyệt bởi {active.approved_by.full_name ?? `#${active.approved_by.id}`}
      {active.approved_at ? ` · ${date(active.approved_at)}` : ""}
    </Pill>
  ) : (
    <Pill tone="warn">Chưa duyệt</Pill>
  );
}

const STALE_SOURCE_LABEL: Record<KnowledgeStaleReason["source"], string> = {
  claude: "Thư mục .claude ở default branch",
  shared: "Repo .claude dùng chung của dự án",
};

/** The hover text of "Đã cũ": which source moved since the build, from which sha to which. */
export function staleTitle(reasons: KnowledgeStaleReason[] | undefined): string {
  const lines = (reasons ?? []).map(
    (r) => `• ${STALE_SOURCE_LABEL[r.source]} đã đổi: ${r.built ?? "chưa có lúc dựng"} → ${r.current}`,
  );
  return [
    "Nguồn tiền đề đã thay đổi sau khi dựng bản này:",
    ...(lines.length ? lines : ["• .claude ở default branch hoặc repo dùng chung đã đổi"]),
    "Review vẫn dùng bản này cho tới khi Rà soát lại dự án.",
  ].join("\n");
}

export function StalePill({ reasons }: { reasons?: KnowledgeStaleReason[] }) {
  return (
    <Pill tone="warn" icon="alert" title={staleTitle(reasons)}>
      Đã cũ
    </Pill>
  );
}

/** A GitLab file link at the sha the knowledge was built from, or the plain path:lines. */
export function SourceRef({
  source,
  webUrl,
  sha,
}: {
  source: KnowledgeSource | null | undefined;
  webUrl?: string | null;
  sha?: string | null;
}) {
  if (!source?.path) return <span className="faint">—</span>;
  const text = source.lines ? `${source.path}:${source.lines}` : source.path;
  if (!webUrl) return <code>{text}</code>;
  const anchor = /^\d+(-\d+)?$/.test(String(source.lines ?? "")) ? `#L${source.lines}` : "";
  const href = `${webUrl.replace(/\/$/, "")}/-/blob/${sha || "HEAD"}/${source.path}${anchor}`;
  return (
    <ExternalLink href={href}>
      <code>{text}</code>
    </ExternalLink>
  );
}

/**
 * "Rà soát lại dự án": queue a knowledge rebuild for one repo. It costs an LLM run, so the lead
 * confirms first; a 409 means the worker is already on it.
 */
export function RefreshKnowledgeButton({
  projectId,
  repoName,
  building,
  me,
  businessProjectId,
  onQueued,
  size,
}: {
  projectId: number;
  businessProjectId: number | null;
  repoName: string;
  building: boolean;
  me: Me | undefined;
  onQueued: () => Promise<unknown>;
  size?: "sm";
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (!canEditKnowledge(me, businessProjectId)) return null;

  async function confirm(): Promise<void> {
    setBusy(true);
    try {
      await knowledgeApi.refresh(projectId);
      setOpen(false);
      setMessage("Đã đưa việc rà soát vào hàng đợi; tiến trình hiện ở panel Đang review, thẻ này tự cập nhật khi xong.");
      await onQueued();
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        setOpen(false);
        setMessage("Repo này đang được rà soát; đợi lượt hiện tại xong.");
        await onQueued();
      } else {
        setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={size === "sm" ? "sm" : undefined}
        disabled={building}
        title="Đọc lại .claude, tài liệu và mã nguồn ở default branch rồi biên dịch tiền đề mới (một lượt gọi LLM, chạy nền)"
        onClick={() => {
          setMessage(null);
          setOpen(true);
        }}
      >
        {building ? "Đang rà soát…" : "Rà soát lại dự án"}
      </button>
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {open && <ConfirmRefresh repoName={repoName} busy={busy} onConfirm={() => void confirm()} onCancel={() => !busy && setOpen(false)} />}
    </>
  );
}

function ConfirmRefresh({
  repoName,
  busy,
  onConfirm,
  onCancel,
}: {
  repoName: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    button.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal card" role="dialog" aria-modal="true" aria-labelledby="confirm-refresh-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 id="confirm-refresh-title">Rà soát lại {repoName}?</h2>
          <IconButton icon="close" label="Đóng" onClick={onCancel} disabled={busy} />
        </div>
        <p>
          Worker sẽ đọc lại <code>.claude</code>, tài liệu và mã nguồn ở default branch rồi biên dịch một bản tiền đề mới.
          Đây là <strong>một lượt gọi LLM có tính phí</strong> (thường vài chục nghìn token, chi phí ghi ở tab Thống kê). Bản
          hiện tại được giữ lại dưới dạng phiên bản cũ; sửa tay của lead vẫn áp lên bản mới.
        </p>
        <div className="toolbar" style={{ margin: 0 }}>
          <button ref={button} type="button" className="primary ink" disabled={busy} onClick={onConfirm}>
            {busy ? "Đang gửi…" : "Rà soát lại"}
          </button>
          <button type="button" onClick={onCancel} disabled={busy}>
            Huỷ
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * One repo's knowledge in a few lines (docs/specs/19 §3.5): version and state as chips, what
 * the repo does in two lines, the item counts in one, and one way in: "Xem chi tiết" opens the
 * knowledge page, where the premises are read, edited, approved and rebuilt.
 */
export function KnowledgeTile({ repo }: { repo: { id: number; path_with_namespace: string } }) {
  const { data, error } = useApi<KnowledgeResponse>(knowledgePath(repo.id), { refreshInterval: knowledgePoll });
  const active = data?.active;
  const counts = active
    ? `${active.counts.invariants} bất biến · ${active.counts.checks} check · ${active.counts.severity_rules} quy tắc severity · ${active.counts.code_conventions} convention`
    : null;
  return (
    <article className="knowledge-tile compact">
      <div className="kt-head">
        <h3>Tiền đề review</h3>
        {active && (
          <span
            className="muted small"
            title={`Dựng ${dateTime(active.created_at)} · ${BUILT_BY_LABEL[active.built_by] ?? active.built_by}`}
          >
            v{active.version} · {date(active.created_at)}
          </span>
        )}
        <span className="chips" style={{ margin: 0 }}>
          {data?.building && (
            <Pill tone="info">
              <span className="spinner" aria-hidden="true" /> Đang rà soát
            </Pill>
          )}
          {data?.last_failure && (
            <Pill
              tone="bad"
              icon="alertCircle"
              title={`Lần rà soát ${dateTime(data.last_failure.created_at)} lỗi: ${data.last_failure.error ?? "không rõ"}`}
            >
              Lần dựng gần nhất lỗi
            </Pill>
          )}
          {active?.stale && <StalePill reasons={active.stale_reasons} />}
          {active && <ApprovalPill active={active} />}
        </span>
        <Link href={`/projects/${repo.id}/knowledge`} className="button sm kt-open">
          Xem chi tiết
        </Link>
      </div>
      {!data ? (
        <Loading error={error} rows={2} />
      ) : !active ? (
        <p className="muted small kt-line">Chưa có: lần review sâu đầu tiên sẽ tự dựng, hoặc dựng ngay ở trang chi tiết.</p>
      ) : (
        <>
          {active.data.domain?.summary && (
            <p className="kt-summary" title={active.data.domain.summary}>
              {active.data.domain.summary}
            </p>
          )}
          <p className="muted small kt-line">{counts}</p>
        </>
      )}
    </article>
  );
}
