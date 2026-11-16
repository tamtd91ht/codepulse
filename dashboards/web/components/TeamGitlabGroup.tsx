"use client";

import { useState } from "react";

import { Icon } from "@/components/icons";
import { Message } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import type { Team, TeamGitlabSync } from "@/lib/types";

const who = (p: { full_name: string | null; user_id: number }) => p.full_name ?? `#${p.user_id}`;

function summary(r: TeamGitlabSync): string {
  if (r.error) return `Lỗi: ${r.error}`;
  const parts = [`Đã đồng bộ: thêm ${r.added.length}, cho rời ${r.ended.length}`];
  if (r.added.length) parts.push(`Thêm: ${r.added.map(who).join(", ")}`);
  if (r.ended.length) parts.push(`Rời: ${r.ended.map(who).join(", ")}`);
  if (r.unmapped.length)
    parts.push(
      `Chưa gắn người (gắn tài khoản GitLab ở Người & Team › Tài khoản): ${r.unmapped
        .map((m) => `@${m.username}`)
        .join(", ")}`,
    );
  if (r.skipped) parts.push(`Bỏ qua ${r.skipped} (bot, bị khoá, chưa nhận lời mời, người đã tắt)`);
  return parts.join(". ") + ".";
}

/**
 * Admin › Người & Team › Team: the GitLab group a team follows (docs/specs/31 §20.7). Its direct
 * members join the team every hour (or on "Đồng bộ ngay"); people added by hand and leads /
 * managers are never removed by the sync.
 */
export function TeamGitlabGroup({ team, onSynced }: { team: Team; onSynced: () => Promise<unknown> }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function act(action: () => Promise<string>) {
    setBusy(true);
    try {
      setMessage(await action());
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const sync = () =>
    act(async () => {
      const r = await api.post<TeamGitlabSync>(`/teams/${team.id}/gitlab/sync`);
      await onSynced();
      return summary(r);
    });

  const save = (path: string | null) =>
    act(async () => {
      await api.put<Team>(`/teams/${team.id}/gitlab`, { group_path: path });
      setEditing(false);
      if (!path) {
        await onSynced();
        return "Đã bỏ gắn nhóm GitLab; thành viên hiện có vẫn giữ.";
      }
      const r = await api.post<TeamGitlabSync>(`/teams/${team.id}/gitlab/sync`);
      await onSynced();
      return summary(r);
    });

  return (
    <div className="adm-pad">
      {editing ? (
        <form
          className="toolbar adm-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save(draft.trim() || null);
          }}
        >
          <input
            className="compact"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ví dụ omicx/qa hoặc link nhóm"
            aria-label="Nhóm GitLab của team"
            autoFocus
          />
          <button type="submit" disabled={busy}>
            Lưu và đồng bộ
          </button>
          <button type="button" className="ghost" onClick={() => setEditing(false)}>
            Huỷ
          </button>
        </form>
      ) : (
        <div className="toolbar">
          <span className="small muted">
            {team.gitlab_group_path ? (
              <>
                Theo nhóm GitLab <code>{team.gitlab_group_path}</code> (thành viên trực tiếp, mỗi giờ)
                {team.gitlab_sync_at ? ` · lần cuối ${dateTimeMin(team.gitlab_sync_at)}` : " · chưa đồng bộ"}
              </>
            ) : (
              "Chưa gắn nhóm GitLab: thành viên do admin thêm tay."
            )}
          </span>
          {team.gitlab_group_path && (
            <button type="button" className="sm" disabled={busy} onClick={() => void sync()}>
              <Icon name="sync" size={14} />
              Đồng bộ ngay
            </button>
          )}
          <button
            type="button"
            className="ghost sm"
            onClick={() => {
              setDraft(team.gitlab_group_path ?? "");
              setMessage(null);
              setEditing(true);
            }}
          >
            {team.gitlab_group_path ? "Đổi nhóm" : "Gắn nhóm GitLab"}
          </button>
          {team.gitlab_group_path && (
            <button
              type="button"
              className="ghost sm"
              disabled={busy}
              onClick={() => {
                if (window.confirm(`Bỏ gắn ${team.name} với nhóm ${team.gitlab_group_path}? Thành viên hiện có vẫn giữ.`))
                  void save(null);
              }}
            >
              Bỏ gắn
            </button>
          )}
        </div>
      )}
      {team.gitlab_sync_error && !message && <Message text={`Lỗi đồng bộ lần cuối: ${team.gitlab_sync_error}`} />}
      <Message text={message} onClose={() => setMessage(null)} />
    </div>
  );
}
