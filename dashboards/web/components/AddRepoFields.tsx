"use client";

import { ApiError, api } from "@/lib/api";
import type { Connection, Project } from "@/lib/types";

/** POST /projects answers the repo plus whether it was already registered (moved, not created). */
export type AddedRepo = Project & { existed: boolean };

export interface AddReposResult {
  text: string;
  failed: number;
}

/** Splits what was pasted into repo references: links, paths or IDs, one per line (or comma). */
export function repoRefs(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean))];
}

/**
 * Adds each pasted repo (GitLab link, path or project ID) in turn, into a dự án when given.
 * Never throws for one bad repo: the result lists what was added and what failed.
 */
export async function addRepos(
  text: string,
  options: { connectionId: number | null; businessProjectId: number | null; webhook: boolean },
): Promise<AddReposResult> {
  const added: string[] = [];
  const moved: string[] = [];
  const failed: string[] = [];
  const hookErrors: string[] = [];
  for (const ref of repoRefs(text)) {
    let repo: AddedRepo;
    try {
      repo = await api.post<AddedRepo>("/projects", {
        repo: ref,
        connection_id: options.connectionId,
        business_project_id: options.businessProjectId,
      });
    } catch (e) {
      failed.push(`${ref}: ${e instanceof ApiError ? e.message : String(e)}`);
      continue;
    }
    (repo.existed ? moved : added).push(repo.path_with_namespace);
    if (!repo.existed && options.webhook) {
      try {
        await api.post(`/projects/${repo.id}/webhook`);
      } catch (e) {
        hookErrors.push(`${repo.path_with_namespace} (${e instanceof ApiError ? e.message : String(e)})`);
      }
    }
  }
  const parts: string[] = [];
  if (added.length) parts.push(`Đã thêm ${added.join(", ")}; sync chạy nền.`);
  if (moved.length) parts.push(`Đã có sẵn, nay thuộc dự án: ${moved.join(", ")}.`);
  if (hookErrors.length) parts.push(`Webhook lỗi: ${hookErrors.join(", ")}; bấm nút Webhook ở dòng đó để thử lại.`);
  if (failed.length) parts.push(`Không thêm được ${failed.join("; ")}.`);
  return { text: parts.join(" ") || "Không có repo nào để thêm.", failed: failed.length };
}

/** The connection select (only when there is more than one), the pasted repos and the webhook box. */
export function AddRepoFields({
  connections,
  optional = false,
  autoFocus = false,
}: {
  connections: Connection[];
  /** The dự án form: the repos may be left empty. */
  optional?: boolean;
  autoFocus?: boolean;
}) {
  return (
    <>
      {connections.length === 0 && (
        <p className="muted small">Chưa có kết nối GitLab: thêm một kết nối trước khi thêm repo.</p>
      )}
      {connections.length > 1 && (
        <label className="adm-field">
          <span className="small muted">Kết nối GitLab</span>
          <select name="connection_id" defaultValue="">
            <option value="">Tự nhận theo link</option>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.base_url})
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="adm-field">
        <span className="small muted">Repo GitLab{optional ? " (tuỳ chọn)" : ""}</span>
        <textarea
          name="repos"
          rows={3}
          required={!optional}
          autoFocus={autoFocus}
          spellCheck={false}
          placeholder={"https://gitlab.example.com/group/repo\ngroup/sub/repo\n1234"}
        />
      </label>
      <label className="inline-check">
        <input name="webhook" type="checkbox" defaultChecked /> Đăng ký webhook GitLab cho repo mới
      </label>
      <p className="muted small">
        Dán link repo (link merge request, nhánh hay clone URL cũng được), đường dẫn group/repo hoặc project ID; mỗi
        repo một dòng. Repo đã có trong CodePulse sẽ được chuyển vào dự án này.
      </p>
    </>
  );
}

/** Reads AddRepoFields out of a submitted form. */
export function repoFieldsOf(f: FormData): { text: string; connectionId: number | null; webhook: boolean } {
  const connection = String(f.get("connection_id") ?? "");
  return {
    text: String(f.get("repos") ?? ""),
    connectionId: connection ? Number(connection) : null,
    webhook: !!f.get("webhook"),
  };
}
