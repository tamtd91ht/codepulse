"use client";

import { useState } from "react";

import { ClosureMcpMeta } from "@/components/ClosureMeta";
import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { dateTimeMin } from "@/lib/format";
import type { ClosureListItem } from "@/lib/types";

/** Did the author send anything worth opening: commits, a note, an argument, MCP details. */
export function hasSubmission(c: ClosureListItem): boolean {
  return c.shas.length > 0 || !!c.note || !!c.argument || c.via === "mcp";
}

/** "3 commit · ghi chú · phản biện": what the button opens, in words. */
function summary(c: ClosureListItem): string {
  const parts: string[] = [];
  if (c.shas.length) parts.push(`${c.shas.length} commit`);
  if (c.note) parts.push("ghi chú");
  if (c.argument) parts.push("phản biện");
  const files = c.meta?.files_changed?.length ?? 0;
  if (!parts.length && files) parts.push(`${files} file`);
  return parts.join(" · ") || "chi tiết";
}

/** One fixing commit: its full SHA (copyable as text) and, when the repo is known, a GitLab link. */
export function CommitList({ shas, repoUrl }: { shas: string[]; repoUrl?: string | null }) {
  return (
    <ul className="closure-commits">
      {shas.map((sha) => (
        <li key={sha}>
          <code>{sha}</code>
          {repoUrl && (
            <a href={`${repoUrl}/-/commit/${sha}`} target="_blank" rel="noreferrer noopener" className="small">
              Mở trên GitLab <Icon name="external" size={12} />
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * "Xem nội dung gửi": what the author sent with a close request — every commit (with its GitLab
 * link), the note, the argument and, from Claude Code (MCP), the files changed and tests run —
 * in a dialog, so a decider reads it without unfolding the row (user 2026-11-11).
 */
export function SubmissionButton({ closure: c }: { closure: ClosureListItem }) {
  const [open, setOpen] = useState(false);
  if (!hasSubmission(c)) return null;
  const sender = c.requested_by_name ?? c.author?.name ?? "Người gửi";
  return (
    <>
      <button type="button" className="link small" onClick={() => setOpen(true)} title="Xem commit, ghi chú và phản biện đã gửi">
        <Icon name="code" size={13} /> {summary(c)}
      </button>
      {open && (
        <Modal
          title={`Nội dung gửi · finding #${c.finding.id}`}
          onClose={() => setOpen(false)}
          width={680}
          footer={
            <button type="button" className="ghost" onClick={() => setOpen(false)}>
              Đóng
            </button>
          }
        >
          <div className="closure-submission">
            <p className="muted small" style={{ margin: 0 }}>
              {sender} gửi lúc {dateTimeMin(c.created_at)}
              {c.via === "mcp" ? " qua Claude Code (MCP)" : c.kind === "request" ? " từ trang web" : ""}
              {c.repo ? ` · repo ${c.repo.path}` : ""}
            </p>
            <section>
              <h3>Commit ({c.shas.length})</h3>
              {c.shas.length ? (
                <CommitList shas={c.shas} repoUrl={c.repo?.web_url} />
              ) : (
                <p className="muted small">Không kèm commit.</p>
              )}
            </section>
            <section>
              <h3>Ghi chú</h3>
              {c.note ? <p className="closure-text">{c.note}</p> : <p className="muted small">Không có ghi chú.</p>}
            </section>
            {c.argument && (
              <section>
                <h3>Phản biện</h3>
                <blockquote className="closure-argument small">{c.argument}</blockquote>
              </section>
            )}
            {c.via === "mcp" && (
              <section>
                <h3>Từ Claude Code</h3>
                <ClosureMcpMeta closure={c} />
              </section>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
