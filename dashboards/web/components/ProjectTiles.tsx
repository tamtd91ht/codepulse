"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";

import { Icon, IconButton } from "@/components/icons";
import { type RepoActions, RepoTable, UNASSIGNED, repoName } from "@/components/RepoTable";
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
  onClose,
  busy,
  notice,
}: {
  tile: TileKey;
  projects: BusinessProject[];
  repos: Project[];
  actions: RepoActions;
  projectOptions: ReactNode;
  onRename: (b: BusinessProject, name: string, description: string | null) => Promise<boolean>;
  onDelete: (b: BusinessProject) => Promise<boolean>;
  onClose: () => void;
  busy: boolean;
  /** The page's action banner, rendered inside the drawer so it is not hidden behind it. */
  notice?: ReactNode;
}) {
  const project = tile === UNASSIGNED ? null : (projects.find((b) => b.id === tile) ?? null);
  const items = repos
    .filter((p) => (project ? p.business_project_id === project.id : p.business_project_id === null))
    .sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));
  const stats = statsOf(items);
  const [status, setStatus] = useState<Status>("tracked");
  const [editing, setEditing] = useState(false);
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

  return (
    <Drawer title={title} onClose={onClose} width={860}>
      {notice}
      <div className="drawer-section">
        {project && editing ? (
          <form
            className="toolbar"
            style={{ margin: 0 }}
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              void onRename(project, String(f.get("name") ?? ""), String(f.get("description") ?? "") || null).then(
                (ok) => ok && setEditing(false),
              );
            }}
          >
            <input name="name" defaultValue={project.name} required aria-label="Tên dự án" />
            <input name="description" defaultValue={project.description ?? ""} placeholder="Mô tả" aria-label="Mô tả" />
            <button className="primary" type="submit" disabled={busy}>
              Lưu
            </button>
            <button type="button" onClick={() => setEditing(false)}>
              Huỷ
            </button>
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
            {project && (
              <div className="actions">
                <Link className="button" href={`/projects/${project.id}`}>
                  <Icon name="activity" size={14} /> Dashboard
                </Link>
                <IconButton icon="edit" label="Đổi tên / mô tả" onClick={() => setEditing(true)} />
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
              </div>
            )}
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
          />
        )}
        {items.length > 0 && (
          <p className="muted small">
            {items.length} repo: {items.slice(0, 6).map(repoName).join(", ")}
            {items.length > 6 ? ` và ${items.length - 6} repo khác` : ""}.
          </p>
        )}
      </div>
    </Drawer>
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
