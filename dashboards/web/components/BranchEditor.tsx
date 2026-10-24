"use client";

import { type FormEvent, useEffect, useState } from "react";

import { IconButton } from "@/components/icons";
import { qs, useApi } from "@/lib/api";
import { relative } from "@/lib/format";
import type { Branch, Project } from "@/lib/types";

/** Project.review_branch value meaning "every branch" (docs/specs/17 §Nhánh review). */
export const ALL_BRANCHES = "*";

/** What the repo row shows: the chosen branch, every branch, or GitLab's default. */
export function branchText(p: Pick<Project, "review_branch" | "default_branch">): string {
  if (p.review_branch === ALL_BRANCHES) return "mọi nhánh";
  if (p.review_branch) return p.review_branch;
  return `${p.default_branch ?? "HEAD"} (mặc định)`;
}

/** Inline editor for a repo's review branch; the list comes from GitLab, typing searches it. */
export function BranchEditor({
  project,
  name,
  onSave,
  onClose,
}: {
  project: Project;
  name: string;
  /** false when the save failed (the owner shows the error); the editor then stays open. */
  onSave: (branch: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const current = project.review_branch ?? "";
  const [value, setValue] = useState(current);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const branches = useApi<Branch[]>(`/repos/${project.id}/branches${qs({ search: debounced || null })}`, {
    keepPreviousData: true,
  });
  const names = new Set((branches.data ?? []).map((b) => b.name));

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    try {
      if (await onSave(value)) onClose();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="toolbar" onSubmit={(e) => void submit(e)}>
        <span className="muted">Nhánh review của {name}</span>
        <select
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label={`Nhánh review của ${name}`}
          autoFocus
        >
          <option value="">Mặc định của GitLab ({project.default_branch ?? "HEAD"})</option>
          <option value={ALL_BRANCHES}>Mọi nhánh</option>
          {value && value !== ALL_BRANCHES && !names.has(value) && <option value={value}>{value}</option>}
          {(branches.data ?? []).map((b) => (
            <option key={b.name} value={b.name}>
              {b.name}
              {b.default ? " · mặc định" : b.last_commit_at ? ` · ${relative(b.last_commit_at)}` : ""}
            </option>
          ))}
        </select>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Tìm nhánh…"
          aria-label="Tìm nhánh trên GitLab"
          autoComplete="off"
          spellCheck={false}
        />
        <button className="primary" type="submit" disabled={busy || value === current}>
          {busy ? "Đang lưu…" : "Lưu"}
        </button>
        <IconButton icon="close" label="Đóng" onClick={onClose} />
        {branches.isValidating && <span className="muted small">đang tìm…</span>}
      </form>
      {branches.error && (
        <p className="error small" role="alert">
          Không lấy được danh sách nhánh từ GitLab: {branches.error.message}
        </p>
      )}
      <p className="muted small">
        Nhánh này dùng cho danh sách &quot;commit gần đây&quot; (Telegram /review, trang Yêu cầu review). Review tự động
        khi push vẫn chỉ áp dụng cho nhánh mặc định.
      </p>
    </>
  );
}
