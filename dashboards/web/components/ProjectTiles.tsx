"use client";

import Link from "next/link";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { BulkBranch, type RepoActions, repoName, RepoTable, UNASSIGNED } from "@/components/RepoTable";
import { Drawer, EmptyState, Pill, Segmented } from "@/components/ui";
import { useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { BusinessProject, NotificationWebhook, Project, TelegramBot } from "@/lib/types";

/** The tile key: a dự án id, or "none" for repos without one. */
export type TileKey = number | typeof UNASSIGNED;

export interface TileStats {
  repos: number;
  tracked: number;
  review: number;
  lastSync: string | null;
}

export function statsOf(items: Project[]): TileStats {
  const synced = items.map((p) => p.last_synced_at).filter((s): s is string => !!s).sort();
  return {
    repos: items.length,
    tracked: items.filter((p) => p.tracked).length,
    review: items.filter((p) => p.tracked && p.review_enabled).length,
    lastSync: synced.length ? synced[synced.length - 1] : null,
  };
}

/** One tile per dự án (plus one for unassigned repos): the basics, click for the drawer. */
export function ProjectTiles({
  projects,
  repos,
  onOpen,
}: {
  projects: BusinessProject[];
  repos: Project[];
  onOpen: (key: TileKey) => void;
}) {
  const unassigned = repos.filter((p) => p.business_project_id === null);
  if (projects.length === 0 && unassigned.length === 0) {
    return (
      <EmptyState icon="folder" title="Chưa có dự án hay repo nào">
        Tạo dự án bên dưới, rồi thêm repo hoặc cả group GitLab vào dự án đó.
      </EmptyState>
    );
  }
  return (
    <div className="tiles">
      {projects.map((b) => (
        <Tile
          key={b.id}
          name={b.name}
          description={b.description}
          stats={statsOf(repos.filter((p) => p.business_project_id === b.id))}
          onOpen={() => onOpen(b.id)}
        />
      ))}
      {unassigned.length > 0 && (
        <Tile
          ghost
          name="Chưa gán dự án"
          description="Repo chưa thuộc dự án nào"
          stats={statsOf(unassigned)}
          onOpen={() => onOpen(UNASSIGNED)}
        />
      )}
    </div>
  );
}

function Tile({
  name,
  description,
  stats,
  ghost,
  onOpen,
}: {
  name: string;
  description: string | null;
  stats: TileStats;
  ghost?: boolean;
  onOpen: () => void;
}) {
  const untracked = stats.repos - stats.tracked;
  return (
    <button type="button" className={`tile card${ghost ? " ghost" : ""}`} onClick={onOpen} aria-haspopup="dialog">
      <div className="head">
        <span className="name">{name}</span>
        <Icon name="chevronRight" size={16} className="muted" />
      </div>
      {description && <div className="desc muted">{description}</div>}
      <div className="big">
        {stats.repos}
        <span className="unit">repo</span>
      </div>
      <div className="meta">
        {stats.repos === 0 ? (
          <span className="muted">Chưa có repo</span>
        ) : (
          <>
            <Pill tone={stats.tracked === 0 ? undefined : stats.review === stats.tracked ? "good" : stats.review ? "warn" : "bad"}>
              {stats.review}/{stats.tracked} review
            </Pill>
            {untracked > 0 && <Pill>{untracked} đã bỏ theo dõi</Pill>}
          </>
        )}
      </div>
      <div className="muted small">Sync gần nhất: {date(stats.lastSync)}</div>
    </button>
  );
}

type Status = "tracked" | "untracked" | "all";

/** Everything about one dự án: repos (with the same controls as the list), bots, webhooks. */
export function ProjectDrawer({
  tile,
  projects,
  repos,
  actions,
  projectOptions,
  onRename,
  onDelete,
  onSaveRules,
  onClose,
  busy,
  notice,
  startEditing = false,
}: {
  tile: TileKey;
  projects: BusinessProject[];
  repos: Project[];
  actions: RepoActions;
  projectOptions: ReactNode;
  onRename: (b: BusinessProject, name: string, description: string | null) => Promise<boolean>;
  onDelete: (b: BusinessProject) => Promise<boolean>;
  onSaveRules: (b: BusinessProject, rules: string) => Promise<boolean>;
  onClose: () => void;
  busy: boolean;
  /** The page's action banner, rendered inside the drawer so it is not hidden behind it. */
  notice?: ReactNode;
  /** Open with the rename form showing (the list view's edit button). */
  startEditing?: boolean;
}) {
  const project = tile === UNASSIGNED ? null : (projects.find((b) => b.id === tile) ?? null);
  const items = repos
    .filter((p) => (project ? p.business_project_id === project.id : p.business_project_id === null))
    .sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));
  const stats = statsOf(items);
  const [status, setStatus] = useState<Status>("tracked");
  const [editing, setEditing] = useState(startEditing);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!editing) return;
    // After the Drawer's own mount focus (close button), which runs after this child effect.
    const t = setTimeout(() => nameInput.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [editing]);
  const bots = useApi<TelegramBot[]>(project ? "/telegram/bots" : null);
  const hooks = useApi<NotificationWebhook[]>(project ? "/webhooks" : null);
  const bot = (bots.data ?? []).find((b) => b.business_project_id === project?.id && b.project_id === null);
  const projectHooks = (hooks.data ?? []).filter(
    (h) => h.business_project_id === project?.id || items.some((p) => p.id === h.project_id),
  );
  const shown = items.filter((p) => status === "all" || (status === "tracked") === p.tracked);
  const addable = repos
    .filter((p) => project && p.business_project_id !== project.id)
    .sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));
  const title = project ? project.name : "Chưa gán dự án";
  const chosen = shown.filter((p) => picked.has(p.id));
  const pick = (ids: number[], on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  const headActions = project ? (
    <>
      <Link className="button" href={`/projects/${project.id}`}>
        <Icon name="activity" size={14} /> Dashboard
      </Link>
      <IconButton icon="edit" label="Đổi tên / mô tả dự án" aria-pressed={editing} onClick={() => setEditing(!editing)} />
      <IconButton
        icon="remove"
        label="Xoá dự án (repo và lịch sử vẫn giữ)"
        tone="danger"
        disabled={busy}
        onClick={() => {
          if (window.confirm(`Xoá dự án "${project.name}"? Repo và lịch sử review vẫn giữ.`))
            void onDelete(project).then((ok) => ok && onClose());
        }}
      />
    </>
  ) : null;

  return (
    <Drawer title={title} onClose={onClose} width="wide" actions={headActions}>
      {notice}
      <div className="drawer-section">
        {project && editing ? (
          <form
            className="rename-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void onRename(project, String(f.get("name") ?? ""), String(f.get("description") ?? "") || null).then(
                (ok) => ok && setEditing(false),
              );
            }}
          >
            <label>
              <span className="muted small">Tên dự án</span>
              <input ref={nameInput} name="name" defaultValue={project.name} required maxLength={100} />
            </label>
            <label className="grow">
              <span className="muted small">Mô tả</span>
              <input
                name="description"
                defaultValue={project.description ?? ""}
                placeholder="Mô tả ngắn (tuỳ chọn)"
                maxLength={500}
              />
            </label>
            <div className="rename-actions">
              <button className="primary" type="submit" disabled={busy}>
                Lưu
              </button>
              <button type="button" onClick={() => setEditing(false)}>
                Huỷ
              </button>
            </div>
          </form>
        ) : (
          <div className="drawer-title-row">
            <div style={{ minWidth: 0 }}>
              {project?.description ? <p className="muted" style={{ margin: 0 }}>{project.description}</p> : null}
              {!project && (
                <p className="muted" style={{ margin: 0 }}>
                  Gán các repo này vào một dự án để có dashboard, bot và webhook theo dự án.
                </p>
              )}
            </div>
          </div>
        )}
        <div className="grid kpis drawer-kpis">
          <Stat label="Repo" value={stats.repos} hint={stats.tracked < stats.repos ? `${stats.repos - stats.tracked} đã bỏ theo dõi` : "đang theo dõi hết"} />
          <Stat label="Bật review" value={stats.review} hint={`trên ${stats.tracked} repo đang theo dõi`} />
          <Stat label="Sync gần nhất" value={date(stats.lastSync)} />
          {project && (
            <>
              <Stat
                label="Bot Telegram"
                value={bots.error ? "—" : bots.data ? (bot ? `@${bot.bot_username ?? bot.name}` : "Chưa có") : "…"}
                hint={bots.error ? <span className="error">{bots.error.message}</span> : <a href="#telegram">Cấu hình</a>}
              />
              <Stat
                label="Webhook thông báo"
                value={hooks.error ? "—" : hooks.data ? projectHooks.length : "…"}
                hint={hooks.error ? <span className="error">{hooks.error.message}</span> : <a href="#webhooks">Cấu hình</a>}
              />
            </>
          )}
        </div>
      </div>

      <div className="drawer-section">
        <div className="toolbar" style={{ marginTop: 0 }}>
          <Segmented
            label="Trạng thái repo"
            value={status}
            onChange={setStatus}
            options={[
              { value: "tracked", label: `Đang theo dõi (${stats.tracked})` },
              { value: "untracked", label: `Đã bỏ (${stats.repos - stats.tracked})` },
              { value: "all", label: `Tất cả (${stats.repos})` },
            ]}
          />
          <span className="spacer" />
          {project && addable.length > 0 && (
            <select
              value=""
              aria-label="Thêm repo có sẵn vào dự án"
              onChange={(e) =>
                e.target.value &&
                void actions.assign([Number(e.target.value)], String(project.id), `Đã thêm repo vào ${project.name}.`)
              }
            >
              <option value="">Thêm repo có sẵn…</option>
              {addable.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.path_with_namespace}
                </option>
              ))}
            </select>
          )}
        </div>
        {chosen.length > 0 && (
          <div className="bulk-bar" role="region" aria-label="Thao tác với repo đã chọn">
            <strong>Đã chọn {chosen.length} repo</strong>
            <BulkBranch
              items={chosen}
              onApply={(branch) =>
                actions.setBranchMany(chosen, branch).then((ok) => {
                  if (ok) setPicked(new Set());
                  return ok;
                })
              }
            />
            <IconButton
              icon="untrack"
              label="Bỏ theo dõi các repo đã chọn"
              onClick={() => void actions.setTracking(chosen, false).then(() => setPicked(new Set()))}
            />
            <IconButton
              icon="track"
              label="Theo dõi lại các repo đã chọn"
              onClick={() => void actions.setTracking(chosen, true).then(() => setPicked(new Set()))}
            />
            <span className="spacer" />
            <IconButton icon="close" label="Bỏ chọn" onClick={() => setPicked(new Set())} />
          </div>
        )}
        {shown.length === 0 ? (
          <p className="muted" style={{ padding: "8px 0" }}>
            {items.length === 0 ? "Dự án chưa có repo: thêm repo có sẵn ở trên, hoặc thêm repo / cả group GitLab ở cuối trang." : "Không có repo ở trạng thái này."}
          </p>
        ) : (
          <RepoTable
            sections={[{ key: "all", label: null, items: shown, expanded: true }]}
            actions={actions}
            projectOptions={projectOptions}
            fullPath
            showProject={!project}
            selectable
            selected={picked}
            onSelect={pick}
          />
        )}
        {items.length > 0 && (
          <p className="muted small">
            {items.length} repo: {items.slice(0, 6).map(repoName).join(", ")}
            {items.length > 6 ? ` và ${items.length - 6} repo khác` : ""}.
          </p>
        )}
      </div>

      {project && (
        <div className="drawer-section">
          <RulesEditor key={project.id} project={project} repos={items} actions={actions} onSaveRules={onSaveRules} busy={busy} />
        </div>
      )}
    </Drawer>
  );
}

/**
 * Team rules that go into every review prompt of the dự án (docs/specs/17): one markdown block
 * for the dự án, and per repo an optional block plus files inside the repo read at review time.
 */
function RulesEditor({
  project,
  repos,
  actions,
  onSaveRules,
  busy,
}: {
  project: BusinessProject;
  repos: Project[];
  actions: RepoActions;
  onSaveRules: (b: BusinessProject, rules: string) => Promise<boolean>;
  busy: boolean;
}) {
  const [rules, setRules] = useState(project.review_rules ?? "");
  const [repoOpen, setRepoOpen] = useState<number | null>(null);
  const dirty = rules.trim() !== (project.review_rules ?? "");
  const withRules = repos.filter((p) => p.review_rules || p.review_rules_paths).length;
  return (
    <>
      <div className="drawer-title-row" style={{ marginBottom: 8 }}>
        <div>
          <h3 style={{ margin: 0 }}>Quy tắc review</h3>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Reviewer đọc các quy tắc này cùng diff (tenant isolation, money safety, PII, convention…). Finding vi
            phạm sẽ ghi tên quy tắc. Tối đa 12.000 ký tự; viết ngắn, mỗi quy tắc một gạch đầu dòng dưới một tiêu đề.
          </p>
        </div>
      </div>
      <textarea
        className="rules"
        value={rules}
        onChange={(e) => setRules(e.target.value)}
        maxLength={12000}
        placeholder={"## Tenant isolation\n- Mọi truy vấn Mongo/ES/Redis phải có tenantId lấy từ JWT\n\n## Money safety\n- Thao tác tiền phải idempotent và cập nhật atomic"}
        aria-label={`Quy tắc review của dự án ${project.name}`}
      />
      <div className="toolbar" style={{ margin: "8px 0 0" }}>
        <span className="muted small">{rules.length.toLocaleString("vi-VN")} / 12.000 ký tự</span>
        <span className="spacer" />
        {dirty && (
          <button type="button" onClick={() => setRules(project.review_rules ?? "")} disabled={busy}>
            Hoàn tác
          </button>
        )}
        <button
          className="primary"
          type="button"
          disabled={!dirty || busy}
          onClick={() => void onSaveRules(project, rules.trim()).then((ok) => ok && setRules(rules.trim()))}
        >
          Lưu quy tắc dự án
        </button>
      </div>

      {repos.length > 0 && (
        <>
          <h4 style={{ margin: "18px 0 6px" }}>
            Theo repo <span className="muted small">({withRules}/{repos.length} repo có quy tắc riêng)</span>
          </h4>
          <p className="muted small" style={{ margin: "0 0 8px" }}>
            Ngoài quy tắc dự án, mỗi repo có thể thêm quy tắc riêng và chỉ tới file quy tắc nằm trong repo (ví dụ{" "}
            <code>.claude/rules/critical/tenant-isolation.md</code>), đọc ở default branch; tối đa 10 file, mỗi file 6.000
            ký tự.
          </p>
          <div className="table-wrap">
            <table>
              <tbody>
                {repos.map((p) => (
                  <Fragment key={p.id}>
                    <tr>
                      <td style={{ width: "40%" }}>
                        <code>{p.path_with_namespace}</code>
                      </td>
                      <td className="muted small">
                        {p.review_rules_paths ? `${p.review_rules_paths.split(/[,\n]/).filter(Boolean).length} file` : "không có file"}
                        {p.review_rules ? ` · ${p.review_rules.length} ký tự riêng` : ""}
                        {" · hồ sơ "}
                        {p.profile_at ? date(p.profile_at) : "chưa có"}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button
                          type="button"
                          className="sm"
                          title="Dựng lại hồ sơ dự án từ default branch (một lượt gọi LLM, chạy nền)"
                          disabled={busy}
                          onClick={() => void actions.refreshProfile(p)}
                        >
                          Làm mới hồ sơ
                        </button>{" "}
                        <button type="button" className="sm" aria-expanded={repoOpen === p.id} onClick={() => setRepoOpen(repoOpen === p.id ? null : p.id)}>
                          {repoOpen === p.id ? "Đóng" : "Sửa"}
                        </button>
                      </td>
                    </tr>
                    {repoOpen === p.id && (
                      <tr>
                        <td colSpan={3}>
                          <form
                            style={{ display: "grid", gap: 8 }}
                            onSubmit={(e) => {
                              e.preventDefault();
                              const f = new FormData(e.currentTarget);
                              void actions
                                .setRules(p, String(f.get("rules") ?? ""), String(f.get("paths") ?? ""))
                                .then((ok) => ok && setRepoOpen(null));
                            }}
                          >
                            <input
                              name="paths"
                              defaultValue={p.review_rules_paths ?? ""}
                              placeholder="File quy tắc trong repo, cách nhau dấu phẩy: .claude/rules/critical/tenant-isolation.md, docs/RULES.md"
                              aria-label={`File quy tắc của ${p.path_with_namespace}`}
                              maxLength={2000}
                            />
                            <textarea
                              name="rules"
                              className="rules"
                              style={{ minHeight: 80 }}
                              defaultValue={p.review_rules ?? ""}
                              maxLength={12000}
                              placeholder="Quy tắc riêng của repo (tuỳ chọn)"
                              aria-label={`Quy tắc riêng của ${p.path_with_namespace}`}
                            />
                            <div className="toolbar" style={{ margin: 0 }}>
                              <button className="primary sm" type="submit" disabled={busy}>
                                Lưu
                              </button>
                              <button type="button" className="sm" onClick={() => setRepoOpen(null)}>
                                Huỷ
                              </button>
                            </div>
                          </form>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="card kpi compact">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}
