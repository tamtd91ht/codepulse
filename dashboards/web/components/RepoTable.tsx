"use client";

import { type FormEvent, Fragment, type ReactNode, useState } from "react";

import { IconButton } from "@/components/icons";
import { date, localDay } from "@/lib/format";
import type { Project } from "@/lib/types";

export type Flag = "review_enabled" | "post_mr_comment" | "allow_external_llm";
export const FLAGS: { key: Flag; label: string; hint: string }[] = [
  { key: "review_enabled", label: "Review", hint: "AI review MR/commit mới" },
  { key: "post_mr_comment", label: "Comment", hint: "Đăng comment tổng hợp lên MR" },
  { key: "allow_external_llm", label: "LLM", hint: "Cho gửi code ra LLM bên ngoài (thiếu thì không review)" },
];

export const UNASSIGNED = "none";
/** "vihat/omicrm/api" → group "vihat/omicrm", repo "api". */
export const groupOf = (p: Project) => p.path_with_namespace.split("/").slice(0, -1).join("/") || "(không có group)";
export const repoName = (p: Project) => p.path_with_namespace.split("/").pop() ?? p.path_with_namespace;

/** What a row can do; the owner (RepoAdmin) talks to the API and reports outcomes. */
export interface RepoActions {
  assign(ids: number[], value: string, label: string): Promise<boolean>;
  setFlag(p: Project, flag: Flag, value: boolean): Promise<unknown>;
  sync(p: Project): Promise<unknown>;
  webhook(p: Project): Promise<unknown>;
  backfill(p: Project, fields: FormData): Promise<string>;
  setTracking(items: Project[], track: boolean): Promise<void>;
  remove(items: Project[]): void;
}

export interface RepoSection {
  key: string;
  label: string | null; // null = no group row (a flat list)
  items: Project[];
  expanded: boolean;
  extra?: ReactNode; // e.g. "assign the whole group" select
}

export function RepoTable({
  sections,
  actions,
  projectOptions,
  fullPath,
  selectable,
  selected,
  onSelect,
  onToggle,
  toggleDisabled,
  showProject = true,
}: {
  sections: RepoSection[];
  actions: RepoActions;
  projectOptions: ReactNode;
  /** Show the full GitLab path instead of the repo name (useful when rows are not grouped by group). */
  fullPath: boolean;
  selectable?: boolean;
  selected?: Set<number>;
  onSelect?: (ids: number[], on: boolean) => void;
  onToggle?: (key: string) => void;
  toggleDisabled?: boolean;
  showProject?: boolean;
}) {
  const [reviewing, setReviewing] = useState<number | null>(null);
  const columns = FLAGS.length + 3 + (selectable ? 1 : 0) + (showProject ? 1 : 0);
  const all = sections.flatMap((s) => s.items);
  const isSelected = (id: number) => !!selected?.has(id);

  function submitBackfill(e: FormEvent<HTMLFormElement>, p: Project) {
    e.preventDefault();
    void actions.backfill(p, new FormData(e.currentTarget)).then(() => setReviewing(null));
  }

  return (
    <div className="table-wrap">
      <table className="repo-table">
        <thead>
          <tr>
            {selectable && (
              <th className="select-col">
                <input
                  type="checkbox"
                  aria-label="Chọn mọi repo đang hiện"
                  checked={all.length > 0 && all.every((p) => isSelected(p.id))}
                  onChange={(e) =>
                    onSelect?.(
                      all.map((p) => p.id),
                      e.target.checked,
                    )
                  }
                />
              </th>
            )}
            <th>Repo</th>
            {showProject && <th>Dự án</th>}
            {FLAGS.map((f) => (
              <th key={f.key} className="flag-col" title={f.hint}>
                {f.label}
              </th>
            ))}
            <th className="hide-sm">Sync</th>
            <th className="actions-col" />
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => {
            const untracked = section.items.filter((p) => !p.tracked).length;
            return (
              <Fragment key={section.key}>
                {section.label !== null && (
                  <tr className="group-row">
                    {selectable && (
                      <td className="select-col">
                        <input
                          type="checkbox"
                          aria-label={`Chọn cả ${section.label}`}
                          checked={section.items.every((p) => isSelected(p.id))}
                          onChange={(e) =>
                            onSelect?.(
                              section.items.map((p) => p.id),
                              e.target.checked,
                            )
                          }
                        />
                      </td>
                    )}
                    <td colSpan={columns - (selectable ? 1 : 0)}>
                      <div className="group-head">
                        <button
                          className="section-toggle"
                          aria-expanded={section.expanded}
                          onClick={() => onToggle?.(section.key)}
                          disabled={toggleDisabled}
                          title={toggleDisabled ? "Đang lọc: mọi nhóm khớp đều mở" : section.expanded ? "Thu gọn" : "Mở rộng"}
                        >
                          <span aria-hidden="true">{section.expanded ? "▾" : "▸"}</span> <strong>{section.label}</strong>
                        </button>
                        <span className="muted">
                          {section.items.length} repo{untracked ? ` · ${untracked} đã bỏ theo dõi` : ""}
                        </span>
                        {section.extra}
                      </div>
                    </td>
                  </tr>
                )}
                {section.expanded &&
                  section.items.map((p) => (
                    <Fragment key={p.id}>
                      <tr className={p.tracked ? undefined : "muted"}>
                        {selectable && (
                          <td className="select-col">
                            <input
                              type="checkbox"
                              aria-label={`Chọn ${p.path_with_namespace}`}
                              checked={isSelected(p.id)}
                              onChange={(e) => onSelect?.([p.id], e.target.checked)}
                            />
                          </td>
                        )}
                        <td className="repo-col" title={p.path_with_namespace}>
                          {fullPath ? p.path_with_namespace : repoName(p)}
                          {!p.tracked && <span className="muted"> · đã bỏ {date(p.untracked_at)}</span>}
                        </td>
                        {showProject && (
                          <td>
                            <select
                              className="compact"
                              value={p.business_project_id ?? UNASSIGNED}
                              aria-label={`Dự án của ${p.path_with_namespace}`}
                              onChange={(e) =>
                                void actions.assign([p.id], e.target.value, `Đã cập nhật dự án của ${repoName(p)}.`)
                              }
                            >
                              {projectOptions}
                            </select>
                          </td>
                        )}
                        {FLAGS.map((f) => (
                          <td key={f.key} className="flag-col" data-label={f.label}>
                            <input
                              type="checkbox"
                              checked={!!p[f.key]}
                              disabled={!p.tracked}
                              aria-label={f.hint}
                              title={f.hint}
                              onChange={(e) => void actions.setFlag(p, f.key, e.target.checked)}
                            />
                          </td>
                        ))}
                        <td className="muted hide-sm">{date(p.last_synced_at)}</td>
                        <td className="actions-col">
                          <div className="actions">
                            {p.tracked ? (
                              <>
                                <IconButton icon="sync" label="Sync ngay" onClick={() => void actions.sync(p)} />
                                <IconButton
                                  icon="webhook"
                                  label="Đăng ký / làm mới webhook GitLab"
                                  onClick={() => void actions.webhook(p)}
                                />
                                <IconButton
                                  icon="review"
                                  label="Review MR trong khoảng ngày"
                                  aria-expanded={reviewing === p.id}
                                  onClick={() => setReviewing(reviewing === p.id ? null : p.id)}
                                />
                                <IconButton icon="untrack" label="Bỏ theo dõi" onClick={() => void actions.setTracking([p], false)} />
                              </>
                            ) : (
                              <IconButton icon="track" label="Theo dõi lại" onClick={() => void actions.setTracking([p], true)} />
                            )}
                            <IconButton icon="remove" label="Gỡ hẳn" tone="danger" onClick={() => actions.remove([p])} />
                          </div>
                        </td>
                      </tr>
                      {reviewing === p.id && (
                        <tr>
                          <td colSpan={columns}>
                            <form className="toolbar" onSubmit={(e) => submitBackfill(e, p)}>
                              <span className="muted">Review MR chưa được review của {repoName(p)} từ</span>
                              <input
                                name="from"
                                type="date"
                                required
                                defaultValue={localDay(new Date(Date.now() - 30 * 86400000))}
                                aria-label="Từ ngày"
                              />
                              <span className="muted">đến</span>
                              <input name="to" type="date" required defaultValue={localDay(new Date())} aria-label="Đến ngày" />
                              <label>
                                <input name="include_open" type="checkbox" defaultChecked /> Gồm MR đang mở
                              </label>
                              <button className="primary" type="submit">
                                Review
                              </button>
                              <IconButton icon="close" label="Huỷ" onClick={() => setReviewing(null)} />
                            </form>
                            <p className="muted">
                              Tốn ngân sách LLM theo số MR. MR mới tạo hoặc có commit mới được review tự động qua webhook,
                              không cần bấm.
                            </p>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
