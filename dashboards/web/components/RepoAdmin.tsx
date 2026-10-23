"use client";

import { type FormEvent, Fragment, useEffect, useMemo, useState } from "react";

import { Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { BusinessProject, Connection, GroupImport, Project } from "@/lib/types";

type Flag = "review_enabled" | "post_mr_comment" | "allow_external_llm";
const FLAGS: { key: Flag; label: string }[] = [
  { key: "review_enabled", label: "AI review" },
  { key: "post_mr_comment", label: "Comment MR" },
  { key: "allow_external_llm", label: "Gửi code ra LLM" },
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
const isoDay = (d: Date) => d.toISOString().slice(0, 10);

/**
 * Admin card for "Dự án / Group / Repo": a dự án is defined here and groups repos; a group is the
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

  async function run(action: () => Promise<unknown>, done?: string) {
    try {
      const result = await action();
      setMessage(done ?? String(result));
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
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

  async function assign(ids: number[], value: string, label: string) {
    await run(async () => {
      await api.put("/projects/business-project", {
        project_ids: ids,
        business_project_id: value === UNASSIGNED ? null : Number(value),
      });
      await refresh();
    }, label);
  }

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
      <div className="card">
        <h2>Dự án</h2>
        <p className="muted">
          Dự án do bạn đặt (ví dụ OmiCRM), gồm nhiều repo, có thể từ nhiều group GitLab. Xoá dự án chỉ bỏ cách nhóm,
          repo và lịch sử review vẫn giữ.
        </p>
        {!projects.data ? (
          <Loading error={projects.error} />
        ) : projects.data.length === 0 ? (
          <p className="muted">Chưa có dự án nào.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Dự án</th>
                  <th className="num">Repo đang theo dõi</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {projects.data.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <button className="link" onClick={() => setProjectFilter(String(b.id))}>
                        {b.name}
                      </button>
                      {b.description && <span className="muted"> – {b.description}</span>}
                    </td>
                    <td className="num">{b.repos}</td>
                    <td>
                      <button
                        onClick={() => {
                          const name = window.prompt("Tên mới của dự án:", b.name);
                          if (name && name !== b.name)
                            void run(async () => {
                              await api.patch(`/business-projects/${b.id}`, { name, description: b.description });
                              await refresh();
                            }, "Đã đổi tên dự án.");
                        }}
                      >
                        Đổi tên
                      </button>{" "}
                      <button
                        onClick={() =>
                          window.confirm(`Xoá dự án "${b.name}"? Repo và lịch sử review vẫn giữ.`) &&
                          void run(async () => {
                            await api.delete(`/business-projects/${b.id}`);
                            if (projectFilter === String(b.id)) setProjectFilter("");
                            await refresh();
                          }, `Đã xoá dự án ${b.name}.`)
                        }
                      >
                        Xoá
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
      </div>

      <div className="card">
        <h2>Repo</h2>
        {message && <p className="muted">{message}</p>}

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
          {(projectFilter || groupFilter || search) && (
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
        </div>

        {!repos.data ? (
          <Loading error={repos.error} />
        ) : visible.length === 0 ? (
          <p className="muted">Không có repo nào khớp bộ lọc.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Repo</th>
                  <th>Dự án</th>
                  {FLAGS.map((f) => (
                    <th key={f.key}>{f.label}</th>
                  ))}
                  <th>Sync gần nhất</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sectionKeys.map((section) => {
                  const items = sections.get(section) ?? [];
                  const expanded = isOpen(section);
                  const untracked = items.filter((p) => !p.tracked).length;
                  return (
                  <Fragment key={section}>
                    <tr className="group-row">
                      <td colSpan={2}>
                        <button
                          className="section-toggle"
                          aria-expanded={expanded}
                          onClick={() => toggle(section)}
                          disabled={filtering}
                          title={filtering ? "Đang lọc: mọi nhóm khớp đều mở" : expanded ? "Thu gọn" : "Mở rộng"}
                        >
                          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span> <strong>{section}</strong>
                        </button>{" "}
                        <span className="muted">
                          · {items.length} repo{untracked ? ` (${untracked} đã bỏ theo dõi)` : ""}
                        </span>
                      </td>
                      <td colSpan={FLAGS.length + 2}>
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
                      </td>
                    </tr>
                    {expanded && items.map((p) => (
                      <Fragment key={p.id}>
                        <tr className={p.tracked ? undefined : "muted"}>
                          <td title={p.path_with_namespace}>
                            {groupBy === "group" ? repoName(p) : p.path_with_namespace}
                            {!p.tracked && <span className="muted"> (đã bỏ {date(p.untracked_at)})</span>}
                          </td>
                          <td>
                            <select
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
                            <td key={f.key}>
                              <input
                                type="checkbox"
                                checked={!!p[f.key]}
                                disabled={!p.tracked}
                                aria-label={f.label}
                                onChange={(e) =>
                                  run(async () => {
                                    await api.patch(`/projects/${p.id}`, { [f.key]: e.target.checked });
                                    await repos.mutate();
                                  }, `Đã cập nhật ${repoName(p)}.`)
                                }
                              />
                            </td>
                          ))}
                          <td className="muted">{date(p.last_synced_at)}</td>
                          <td className="actions">
                            {p.tracked ? (
                              <>
                                <button
                                  title="Lấy commit/MR mới từ GitLab ngay"
                                  onClick={() => run(() => api.post(`/projects/${p.id}/sync`), "Đã đưa vào hàng đợi sync.")}
                                >
                                  Sync
                                </button>
                                <button
                                  title="Đăng ký (hoặc làm mới) webhook để GitLab báo push/MR mới ngay lập tức"
                                  onClick={() => run(() => registerWebhook(p))}
                                >
                                  Webhook
                                </button>
                                <button
                                  title="Review các MR chưa được review trong một khoảng ngày"
                                  onClick={() => setReviewing(reviewing === p.id ? null : p.id)}
                                >
                                  Review…
                                </button>
                                <button
                                  title="Ngừng sync và review repo này; lịch sử vẫn giữ"
                                  onClick={() =>
                                    window.confirm(`Bỏ theo dõi ${p.path_with_namespace}? Lịch sử review vẫn giữ.`) &&
                                    void run(async () => {
                                      await api.post(`/projects/${p.id}/untrack`);
                                      await refresh();
                                    }, `Đã bỏ theo dõi ${repoName(p)}.`)
                                  }
                                >
                                  Bỏ theo dõi
                                </button>
                              </>
                            ) : (
                              <button
                                onClick={() =>
                                  run(async () => {
                                    await api.post(`/projects/${p.id}/track`);
                                    await refresh();
                                  }, `Đã theo dõi lại ${repoName(p)} và đưa vào hàng đợi sync.`)
                                }
                              >
                                Theo dõi lại
                              </button>
                            )}
                          </td>
                        </tr>
                        {reviewing === p.id && (
                          <tr>
                            <td colSpan={FLAGS.length + 4}>
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
                                      : `Không có MR nào chưa được review trong khoảng này (cần Sync trước nếu repo mới thêm).`;
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
                                <button type="button" onClick={() => setReviewing(null)}>
                                  Huỷ
                                </button>
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
          <input name="external_id" type="number" min={1} placeholder="GitLab project ID của repo" required />
          <select name="business_project_id" defaultValue={UNASSIGNED} aria-label="Dự án">
            {projectOptions}
          </select>
          <label>
            <input name="webhook" type="checkbox" defaultChecked /> Đăng ký webhook
          </label>
          <button className="primary" type="submit">
            Thêm repo
          </button>
        </form>
        <p className="muted">Project ID nằm ở trang repo trên GitLab (Settings → General, hoặc dưới tên repo).</p>

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
            <input name="webhook" type="checkbox" defaultChecked /> Đăng ký webhook
          </label>
          <button className="primary" type="submit" disabled={importing}>
            {importing ? "Đang thêm…" : "Thêm cả group"}
          </button>
        </form>
        <p className="muted">
          Thêm một lần mọi repo đang hoạt động trong group, kể cả subgroup. Repo đã có (kể cả repo đã bỏ theo dõi) được
          bỏ qua; repo tạo sau này cần thêm lại bằng nút này.
        </p>
      </div>
    </>
  );
}
