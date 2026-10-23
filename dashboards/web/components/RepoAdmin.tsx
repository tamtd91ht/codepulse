"use client";

import { type FormEvent, Fragment, useEffect, useMemo, useState } from "react";

import { ConfirmRemove } from "@/components/ConfirmRemove";
import { IconButton } from "@/components/icons";
import { Loading, Message } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { date, localDay } from "@/lib/format";
import type { BusinessProject, Connection, GroupImport, Project } from "@/lib/types";

type Flag = "review_enabled" | "post_mr_comment" | "allow_external_llm";
const FLAGS: { key: Flag; label: string; hint: string }[] = [
  { key: "review_enabled", label: "Review", hint: "AI review MR/commit mới" },
  { key: "post_mr_comment", label: "Comment", hint: "Đăng comment tổng hợp lên MR" },
  { key: "allow_external_llm", label: "LLM", hint: "Cho gửi code ra LLM bên ngoài (thiếu thì không review)" },
];

type Status = "tracked" | "untracked" | "all";
const STATUS_TABS: { key: Status; label: string }[] = [
  { key: "tracked", label: "Đang theo dõi" },
  { key: "untracked", label: "Đã bỏ theo dõi" },
  { key: "all", label: "Tất cả" },
];
const UNASSIGNED = "none";
const NO_PROJECT_SECTION = "Chưa gán dự án";

type GroupBy = "group" | "project";
const GROUP_BY_TABS: { key: GroupBy; label: string }[] = [
  { key: "group", label: "Theo group" },
  { key: "project", label: "Theo dự án" },
];
const VIEW_KEY = "codepulse.admin.repos.view";
const COLUMNS = FLAGS.length + 5; // select, repo, dự án, flags, last sync, actions

/** Per-viewer convenience only: unreadable storage (private mode, blocked) just means defaults. */
function loadView(): { groupBy: GroupBy; open: string[] } {
  try {
    const saved = JSON.parse(window.localStorage.getItem(VIEW_KEY) ?? "null");
    if (saved && (saved.groupBy === "group" || saved.groupBy === "project") && Array.isArray(saved.open))
      return { groupBy: saved.groupBy, open: saved.open.filter((k: unknown) => typeof k === "string") };
  } catch {
    /* ignore */
  }
  return { groupBy: "group", open: [] };
}

/** "vihat/omicrm/api" → group "vihat/omicrm", repo "api". */
const groupOf = (p: Project) => p.path_with_namespace.split("/").slice(0, -1).join("/") || "(không có group)";
const repoName = (p: Project) => p.path_with_namespace.split("/").pop() ?? p.path_with_namespace;
const isoDay = (d: Date) => localDay(d);
/** Must match app/collector/removal.py confirmation_phrase. */
const removalPhrase = (paths: string[]) => (paths.length === 1 ? paths[0] : `xoa ${paths.length} repo`);

/**
 * Admin cards for "Dự án / Group / Repo": a dự án is defined here and groups repos; a group is the
 * GitLab group a repo lives in; a repo is one GitLab project (a git repository).
 */
export function RepoAdmin({ connections }: { connections: Connection[] }) {
  const repos = useApi<Project[]>("/projects");
  const projects = useApi<BusinessProject[]>("/business-projects");
  const [message, setMessage] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("tracked");
  const [projectFilter, setProjectFilter] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [search, setSearch] = useState("");
  const [reviewing, setReviewing] = useState<number | null>(null);
  const [importing, setImporting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [removing, setRemoving] = useState<Project[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [groupBy, setGroupBy] = useState<GroupBy>("group");
  // Sections start collapsed so the list fits on one screen; the open ones are remembered.
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [viewLoaded, setViewLoaded] = useState(false);

  useEffect(() => {
    const view = loadView();
    setGroupBy(view.groupBy);
    setOpen(new Set(view.open));
    setViewLoaded(true);
  }, []);
  useEffect(() => {
    if (!viewLoaded) return;
    try {
      window.localStorage.setItem(VIEW_KEY, JSON.stringify({ groupBy, open: [...open] }));
    } catch {
      /* ignore */
    }
  }, [groupBy, open, viewLoaded]);

  /** Runs an action and reports it; resolves true on success so callers can skip follow-ups on failure. */
  async function run(action: () => Promise<unknown>, done?: string): Promise<boolean> {
    try {
      const result = await action();
      setMessage(done ?? String(result));
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      return false;
    }
  }

  function submit(e: FormEvent<HTMLFormElement>, action: (f: FormData) => Promise<unknown>, done?: string) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    run(async () => {
      const result = await action(fields);
      form.reset();
      return result;
    }, done);
  }

  async function refresh() {
    await Promise.all([repos.mutate(), projects.mutate()]);
  }

  async function registerWebhook(p: Project): Promise<string> {
    const hook = await api.post<{ url: string; created: boolean }>(`/projects/${p.id}/webhook`);
    return hook.created ? `Đã đăng ký webhook ${hook.url}.` : `Webhook đã có sẵn: ${hook.url}.`;
  }

  async function assign(ids: number[], value: string, label: string): Promise<boolean> {
    return run(async () => {
      await api.put("/projects/business-project", {
        project_ids: ids,
        business_project_id: value === UNASSIGNED ? null : Number(value),
      });
      await refresh();
    }, label);
  }

  async function setTracking(items: Project[], track: boolean) {
    const targets = items.filter((p) => p.tracked !== track);
    if (!targets.length) return;
    if (!track && !window.confirm(`Bỏ theo dõi ${targets.length} repo? Lịch sử review vẫn giữ.`)) return;
    await run(async () => {
      await Promise.all(targets.map((p) => api.post(`/projects/${p.id}/${track ? "track" : "untrack"}`)));
      setSelected(new Set());
      await refresh();
    }, `${track ? "Đã theo dõi lại" : "Đã bỏ theo dõi"} ${targets.length} repo.`);
  }

  async function remove(items: Project[], typed: string) {
    setBusy(true);
    try {
      const result = await api.post<{
        removed: string[];
        reviews_deleted: number;
        cleanup_queued: boolean;
      }>("/projects/remove", {
        project_ids: items.map((p) => p.id),
        confirm: typed,
      });
      setMessage(
        `Đã gỡ ${result.removed.length} repo và ${result.reviews_deleted} review. ` +
          (result.cleanup_queued
            ? "Webhook được gỡ và chỉ số được tính lại trong nền."
            : "Chưa đưa được việc gỡ webhook/tính lại chỉ số vào hàng đợi (Redis?): xoá webhook tay trên GitLab và chạy codepulse metrics recompute."),
      );
      setRemoving(null);
      setSelected(new Set());
      await refresh();
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const byId = useMemo(() => new Map((repos.data ?? []).map((p) => [p.id, p])), [repos.data]);
  const allGroups = useMemo(() => [...new Set((repos.data ?? []).map(groupOf))].sort(), [repos.data]);

  const visible = (repos.data ?? []).filter(
    (p) =>
      (status === "all" || (status === "tracked") === p.tracked) &&
      (!projectFilter ||
        (projectFilter === UNASSIGNED ? p.business_project_id === null : p.business_project_id === Number(projectFilter))) &&
      (!groupFilter || groupOf(p) === groupFilter || groupOf(p).startsWith(`${groupFilter}/`)) &&
      (!search || p.path_with_namespace.toLowerCase().includes(search.toLowerCase())),
  );
  const sectionOf = (p: Project) =>
    groupBy === "group"
      ? groupOf(p)
      : (projects.data?.find((b) => b.id === p.business_project_id)?.name ?? NO_PROJECT_SECTION);
  const sections = new Map<string, Project[]>();
  for (const p of [...visible].sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace))) {
    sections.set(sectionOf(p), [...(sections.get(sectionOf(p)) ?? []), p]);
  }
  const sectionKeys = [...sections.keys()].sort((a, b) =>
    a === NO_PROJECT_SECTION ? 1 : b === NO_PROJECT_SECTION ? -1 : a.localeCompare(b),
  );
  const keyOf = (section: string) => `${groupBy}:${section}`;
  // A search or filter shows its matches without extra clicks.
  const filtering = !!(search || projectFilter || groupFilter);
  const isOpen = (section: string) => filtering || open.has(keyOf(section));
  const toggle = (section: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(keyOf(section))) next.delete(keyOf(section));
      else next.add(keyOf(section));
      return next;
    });
  const setAll = (expanded: boolean) =>
    setOpen((prev) => {
      const next = new Set([...prev].filter((k) => !k.startsWith(`${groupBy}:`)));
      if (expanded) for (const section of sectionKeys) next.add(keyOf(section));
      return next;
    });

  // Selection survives filtering and collapsing; repos that disappear are simply ignored.
  const chosen = [...selected].map((id) => byId.get(id)).filter((p): p is Project => !!p);
  const toggleSelect = (ids: number[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  const projectOptions = (
    <>
      <option value={UNASSIGNED}>Chưa gán dự án</option>
      {(projects.data ?? []).map((b) => (
        <option key={b.id} value={b.id}>
          {b.name}
        </option>
      ))}
    </>
  );

  return (
    <>
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Dự án</h2>
            <div className="sub">
              Dự án do bạn đặt (ví dụ OmiCRM), gồm nhiều repo, có thể từ nhiều group GitLab. Xoá dự án chỉ bỏ cách
              nhóm; repo và lịch sử review vẫn giữ.
            </div>
          </div>
        </div>
        {!projects.data ? (
          <Loading error={projects.error} />
        ) : projects.data.length === 0 ? (
          <p className="muted">Chưa có dự án nào.</p>
        ) : (
          <div className="chips">
            {projects.data.map((b) => (
              <div key={b.id} className="chip" title={b.description ?? undefined}>
                <button className="link" onClick={() => setProjectFilter(String(b.id))}>
                  {b.name}
                </button>
                <span className="muted">{b.repos} repo</span>
                <IconButton
                  icon="edit"
                  label="Đổi tên"
                  onClick={() => {
                    const name = window.prompt("Tên mới của dự án:", b.name);
                    if (name && name !== b.name)
                      void run(async () => {
                        await api.patch(`/business-projects/${b.id}`, { name, description: b.description });
                        await refresh();
                      }, "Đã đổi tên dự án.");
                  }}
                />
                <IconButton
                  icon="remove"
                  label="Xoá dự án"
                  tone="danger"
                  onClick={() =>
                    window.confirm(`Xoá dự án "${b.name}"? Repo và lịch sử review vẫn giữ.`) &&
                    void run(async () => {
                      await api.delete(`/business-projects/${b.id}`);
                      if (projectFilter === String(b.id)) setProjectFilter("");
                      await refresh();
                    }, `Đã xoá dự án ${b.name}.`)
                  }
                />
              </div>
            ))}
          </div>
        )}
        <form
          className="toolbar"
          onSubmit={(e) =>
            submit(
              e,
              async (f) => {
                await api.post("/business-projects", { name: f.get("name"), description: f.get("description") || null });
                await projects.mutate();
              },
              "Đã tạo dự án.",
            )
          }
        >
          <input name="name" placeholder="Tên dự án, ví dụ OmiCRM" required />
          <input name="description" placeholder="Mô tả (tuỳ chọn)" />
          <button className="primary" type="submit">
            Tạo dự án
          </button>
        </form>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Repo</h2>
            <div className="sub">Mỗi repo là một GitLab project; nhóm theo group GitLab hoặc theo dự án.</div>
          </div>
        </div>
        <Message text={message} onClose={() => setMessage(null)} />

        <div className="tabs" role="tablist">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={status === t.key}
              className={status === t.key ? "active" : undefined}
              onClick={() => setStatus(t.key)}
            >
              {t.label} ({(repos.data ?? []).filter((p) => t.key === "all" || (t.key === "tracked") === p.tracked).length})
            </button>
          ))}
        </div>
        <div className="toolbar">
          <select value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)} aria-label="Lọc theo dự án">
            <option value="">Mọi dự án</option>
            {projectOptions}
          </select>
          <select value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)} aria-label="Lọc theo group">
            <option value="">Mọi group</option>
            {allGroups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm repo…"
            aria-label="Tìm repo"
          />
          {filtering && (
            <button
              onClick={() => {
                setProjectFilter("");
                setGroupFilter("");
                setSearch("");
              }}
            >
              Bỏ lọc
            </button>
          )}
          <span className="spacer" />
          <div className="tabs compact" role="tablist" aria-label="Nhóm repo">
            {GROUP_BY_TABS.map((t) => (
              <button
                key={t.key}
                role="tab"
                aria-selected={groupBy === t.key}
                className={groupBy === t.key ? "active" : undefined}
                onClick={() => setGroupBy(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button onClick={() => setAll(true)} disabled={filtering}>
            Mở rộng tất cả
          </button>
          <button onClick={() => setAll(false)} disabled={filtering}>
            Thu gọn tất cả
          </button>
        </div>

        {chosen.length > 0 && (
          <div className="bulk-bar" role="region" aria-label="Thao tác với repo đã chọn">
            <strong>Đã chọn {chosen.length} repo</strong>
            <select
              value=""
              aria-label="Gán dự án cho repo đã chọn"
              onChange={(e) =>
                e.target.value &&
                void assign(
                  chosen.map((p) => p.id),
                  e.target.value,
                  `Đã gán ${chosen.length} repo.`,
                ).then((ok) => ok && setSelected(new Set()))
              }
            >
              <option value="">Gán dự án…</option>
              {projectOptions}
            </select>
            <IconButton icon="untrack" label="Bỏ theo dõi các repo đã chọn" onClick={() => void setTracking(chosen, false)} />
            <IconButton icon="track" label="Theo dõi lại các repo đã chọn" onClick={() => void setTracking(chosen, true)} />
            <IconButton icon="remove" label="Gỡ hẳn các repo đã chọn" tone="danger" onClick={() => setRemoving(chosen)} />
            <span className="spacer" />
            <IconButton icon="close" label="Bỏ chọn" onClick={() => setSelected(new Set())} />
          </div>
        )}

        {!repos.data ? (
          <Loading error={repos.error} />
        ) : visible.length === 0 ? (
          <p className="muted" style={{ padding: "12px 0" }}>Không có repo nào khớp bộ lọc.</p>
        ) : (
          <div className="table-wrap">
            <table className="repo-table">
              <thead>
                <tr>
                  <th className="select-col">
                    <input
                      type="checkbox"
                      aria-label="Chọn mọi repo đang hiện"
                      checked={visible.every((p) => selected.has(p.id))}
                      onChange={(e) =>
                        toggleSelect(
                          visible.map((p) => p.id),
                          e.target.checked,
                        )
                      }
                    />
                  </th>
                  <th>Repo</th>
                  <th>Dự án</th>
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
                {sectionKeys.map((section) => {
                  const items = sections.get(section) ?? [];
                  const expanded = isOpen(section);
                  const untracked = items.filter((p) => !p.tracked).length;
                  const allChosen = items.every((p) => selected.has(p.id));
                  return (
                    <Fragment key={section}>
                      <tr className="group-row">
                        <td className="select-col">
                          <input
                            type="checkbox"
                            aria-label={`Chọn cả ${section}`}
                            checked={allChosen}
                            onChange={(e) =>
                              toggleSelect(
                                items.map((p) => p.id),
                                e.target.checked,
                              )
                            }
                          />
                        </td>
                        <td colSpan={COLUMNS - 1}>
                          <div className="group-head">
                            <button
                              className="section-toggle"
                              aria-expanded={expanded}
                              onClick={() => toggle(section)}
                              disabled={filtering}
                              title={filtering ? "Đang lọc: mọi nhóm khớp đều mở" : expanded ? "Thu gọn" : "Mở rộng"}
                            >
                              <span aria-hidden="true">{expanded ? "▾" : "▸"}</span> <strong>{section}</strong>
                            </button>
                            <span className="muted">
                              {items.length} repo{untracked ? ` · ${untracked} đã bỏ theo dõi` : ""}
                            </span>
                            {groupBy === "group" && (
                              <select
                                value=""
                                aria-label={`Gán dự án cho cả group ${section}`}
                                onChange={(e) =>
                                  e.target.value &&
                                  void assign(
                                    items.map((p) => p.id),
                                    e.target.value,
                                    `Đã gán ${items.length} repo của ${section}.`,
                                  )
                                }
                              >
                                <option value="">Gán dự án cho cả group…</option>
                                {projectOptions}
                              </select>
                            )}
                          </div>
                        </td>
                      </tr>
                      {expanded &&
                        items.map((p) => (
                          <Fragment key={p.id}>
                            <tr className={p.tracked ? undefined : "muted"}>
                              <td className="select-col">
                                <input
                                  type="checkbox"
                                  aria-label={`Chọn ${p.path_with_namespace}`}
                                  checked={selected.has(p.id)}
                                  onChange={(e) => toggleSelect([p.id], e.target.checked)}
                                />
                              </td>
                              <td className="repo-col" title={p.path_with_namespace}>
                                {groupBy === "group" ? repoName(p) : p.path_with_namespace}
                                {!p.tracked && <span className="muted"> · đã bỏ {date(p.untracked_at)}</span>}
                              </td>
                              <td>
                                <select
                                  className="compact"
                                  value={p.business_project_id ?? UNASSIGNED}
                                  aria-label={`Dự án của ${p.path_with_namespace}`}
                                  onChange={(e) =>
                                    void assign([p.id], e.target.value, `Đã cập nhật dự án của ${repoName(p)}.`)
                                  }
                                >
                                  {projectOptions}
                                </select>
                              </td>
                              {FLAGS.map((f) => (
                                <td key={f.key} className="flag-col" data-label={f.label}>
                                  <input
                                    type="checkbox"
                                    checked={!!p[f.key]}
                                    disabled={!p.tracked}
                                    aria-label={f.hint}
                                    title={f.hint}
                                    onChange={(e) =>
                                      run(async () => {
                                        await api.patch(`/projects/${p.id}`, { [f.key]: e.target.checked });
                                        await repos.mutate();
                                      }, `Đã cập nhật ${repoName(p)}.`)
                                    }
                                  />
                                </td>
                              ))}
                              <td className="muted hide-sm">{date(p.last_synced_at)}</td>
                              <td className="actions-col">
                                <div className="actions">
                                  {p.tracked ? (
                                    <>
                                      <IconButton
                                        icon="sync"
                                        label="Sync ngay"
                                        onClick={() =>
                                          run(() => api.post(`/projects/${p.id}/sync`), "Đã đưa vào hàng đợi sync.")
                                        }
                                      />
                                      <IconButton
                                        icon="webhook"
                                        label="Đăng ký / làm mới webhook"
                                        onClick={() => run(() => registerWebhook(p))}
                                      />
                                      <IconButton
                                        icon="review"
                                        label="Review MR trong khoảng ngày"
                                        aria-expanded={reviewing === p.id}
                                        onClick={() => setReviewing(reviewing === p.id ? null : p.id)}
                                      />
                                      <IconButton
                                        icon="untrack"
                                        label="Bỏ theo dõi"
                                        onClick={() => void setTracking([p], false)}
                                      />
                                    </>
                                  ) : (
                                    <IconButton icon="track" label="Theo dõi lại" onClick={() => void setTracking([p], true)} />
                                  )}
                                  <IconButton icon="remove" label="Gỡ hẳn" tone="danger" onClick={() => setRemoving([p])} />
                                </div>
                              </td>
                            </tr>
                            {reviewing === p.id && (
                              <tr>
                                <td colSpan={COLUMNS}>
                                  <form
                                    className="toolbar"
                                    onSubmit={(e) =>
                                      submit(e, async (f) => {
                                        const params = new URLSearchParams({
                                          from: String(f.get("from")),
                                          to: String(f.get("to")),
                                          include_open: f.get("include_open") ? "true" : "false",
                                        });
                                        const result = await api.post<{ queued: number }>(
                                          `/projects/${p.id}/backfill-reviews?${params}`,
                                        );
                                        setReviewing(null);
                                        return result.queued
                                          ? `Đã đưa ${result.queued} MR của ${repoName(p)} vào hàng đợi review; kết quả hiện ở trang review sau vài phút.`
                                          : "Không có MR nào chưa được review trong khoảng này (cần Sync trước nếu repo mới thêm).";
                                      })
                                    }
                                  >
                                    <span className="muted">Review MR chưa được review của {repoName(p)} từ</span>
                                    <input
                                      name="from"
                                      type="date"
                                      required
                                      defaultValue={isoDay(new Date(Date.now() - 30 * 86400000))}
                                      aria-label="Từ ngày"
                                    />
                                    <span className="muted">đến</span>
                                    <input name="to" type="date" required defaultValue={isoDay(new Date())} aria-label="Đến ngày" />
                                    <label>
                                      <input name="include_open" type="checkbox" defaultChecked /> Gồm MR đang mở
                                    </label>
                                    <button className="primary" type="submit">
                                      Review
                                    </button>
                                    <IconButton icon="close" label="Huỷ" onClick={() => setReviewing(null)} />
                                  </form>
                                  <p className="muted">
                                    Tốn ngân sách LLM theo số MR. MR mới tạo hoặc có commit mới được review tự động qua
                                    webhook, không cần bấm.
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
        )}

        <div className="grid two add-forms">
          <div>
            <h3>Thêm repo</h3>
            <form
              className="toolbar"
              onSubmit={(e) =>
                submit(e, async (f) => {
                  const project = await api.post<Project>("/projects", {
                    connection_id: Number(f.get("connection_id")),
                    external_id: Number(f.get("external_id")),
                  });
                  const bp = String(f.get("business_project_id") ?? UNASSIGNED);
                  if (bp !== UNASSIGNED)
                    await api.put("/projects/business-project", { project_ids: [project.id], business_project_id: Number(bp) });
                  await api.post(`/projects/${project.id}/sync`);
                  await refresh();
                  let hook = "";
                  if (f.get("webhook")) {
                    try {
                      hook = ` ${await registerWebhook(project)}`;
                    } catch (err) {
                      hook = ` Webhook lỗi: ${err instanceof ApiError ? err.message : String(err)}; bấm nút Webhook để thử lại.`;
                    }
                  }
                  return `Đã thêm ${project.path_with_namespace} và đưa vào hàng đợi sync.${hook}`;
                })
              }
            >
              <select name="connection_id" required defaultValue="">
                <option value="" disabled>
                  Kết nối…
                </option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <input name="external_id" type="number" min={1} placeholder="GitLab project ID" required />
              <select name="business_project_id" defaultValue={UNASSIGNED} aria-label="Dự án">
                {projectOptions}
              </select>
              <label>
                <input name="webhook" type="checkbox" defaultChecked /> Webhook
              </label>
              <button className="primary" type="submit">
                Thêm repo
              </button>
            </form>
            <p className="muted">Project ID nằm dưới tên repo trên GitLab, hoặc Settings → General.</p>
          </div>
          <div>
            <h3>Thêm cả group</h3>
            <form
              className="toolbar"
              onSubmit={(e) =>
                submit(e, async (f) => {
                  setMessage("Đang lấy danh sách repo của group…");
                  setImporting(true);
                  const bp = String(f.get("business_project_id") ?? UNASSIGNED);
                  const result = await api
                    .post<GroupImport>("/projects/import-group", {
                      connection_id: Number(f.get("connection_id")),
                      group: f.get("group"),
                      register_webhooks: !!f.get("webhook"),
                      business_project_id: bp === UNASSIGNED ? null : Number(bp),
                    })
                    .finally(() => {
                      setImporting(false);
                      void refresh(); // show rows added before an error or timeout
                    });
                  if (result.added.length === 0) return `Không có repo mới (${result.skipped} repo đã có).`;
                  const notQueued = result.not_queued.length
                    ? ` Chưa đưa vào hàng đợi: ${result.not_queued.join(", ")} – sẽ được sync ở lần định kỳ; bấm Webhook ở dòng đó nếu cần.`
                    : "";
                  return (
                    `Đã thêm ${result.added.length} repo (bỏ qua ${result.skipped} repo đã có). ` +
                    `Webhook và sync chạy nền trong worker.${notQueued}`
                  );
                })
              }
            >
              <select name="connection_id" required defaultValue="">
                <option value="" disabled>
                  Kết nối…
                </option>
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <input name="group" placeholder="Group ID hoặc đường dẫn, ví dụ vihat/omicrm" required />
              <select name="business_project_id" defaultValue={UNASSIGNED} aria-label="Gán vào dự án">
                {projectOptions}
              </select>
              <label>
                <input name="webhook" type="checkbox" defaultChecked /> Webhook
              </label>
              <button className="primary" type="submit" disabled={importing}>
                {importing ? "Đang thêm…" : "Thêm cả group"}
              </button>
            </form>
            <p className="muted">
              Mọi repo đang hoạt động trong group, kể cả subgroup. Repo đã có (kể cả đã bỏ theo dõi) được bỏ qua; repo
              tạo sau cần thêm lại.
            </p>
          </div>
        </div>
      </section>

      {removing && (
        <ConfirmRemove
          paths={removing.map((p) => p.path_with_namespace)}
          phrase={removalPhrase(removing.map((p) => p.path_with_namespace))}
          busy={busy}
          onConfirm={(typed) => void remove(removing, typed)}
          onCancel={() => setRemoving(null)}
        />
      )}
    </>
  );
}
