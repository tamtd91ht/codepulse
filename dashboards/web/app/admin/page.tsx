"use client";

import { type FormEvent, type KeyboardEvent, useEffect, useState } from "react";

import { type SubNav, SubPanel, type SubTab, SubTabs, pickSub } from "@/components/AdminTabs";
import { ClaudeLogin } from "@/components/ClaudeLogin";
import { CommandRunner } from "@/components/CommandRunner";
import { GitLabMembers } from "@/components/GitLabMembers";
import { Icon, type IconName, IconButton } from "@/components/icons";
import { LlmCheck } from "@/components/LlmCheck";
import { PurgeData } from "@/components/PurgeData";
import { RepoAdmin } from "@/components/RepoAdmin";
import { ReviewSettings } from "@/components/ReviewSettings";
import { TeamsAdmin, UsersAdmin } from "@/components/TeamAdmin";
import { TelegramAdmin } from "@/components/TelegramAdmin";
import { Card, Drawer, EmptyState, Loading, Message, PageHeader, Segmented, StatStrip } from "@/components/ui";
import { WebhookAdmin } from "@/components/WebhookAdmin";
import { ApiError, api, useApi } from "@/lib/api";
import { attributedText } from "@/lib/format";
import type { Account, AccountLinked, Connection, LlmCosts, Me, Person, Team } from "@/lib/types";

type Tab = "gitlab" | "repos" | "people" | "telegram" | "webhooks" | "llm" | "commands" | "data";
const TABS: { key: Tab; label: string; icon: IconName; hint: string }[] = [
  { key: "gitlab", label: "Kết nối GitLab", icon: "gitlab", hint: "Token bot và máy chủ GitLab" },
  { key: "repos", label: "Dự án & Repo", icon: "folder", hint: "Dự án, group, repo, review theo repo" },
  { key: "people", label: "Người & Team", icon: "members", hint: "Tài khoản, người dùng, team, vai trò" },
  { key: "telegram", label: "Telegram", icon: "pulse", hint: "Bot nhận lệnh review theo dự án" },
  { key: "webhooks", label: "Webhook thông báo", icon: "webhook", hint: "POST kết quả review tới endpoint của bạn" },
  { key: "llm", label: "LLM & Chi phí", icon: "activity", hint: "Kết nối LLM, chi phí theo tháng" },
  { key: "commands", label: "Chạy command", icon: "code", hint: "Chạy command trong .claude của repo, tải file kết quả" },
  { key: "data", label: "Xoá dữ liệu", icon: "remove", hint: "Làm sạch dữ liệu trên server này" },
];
const isTab = (v: string): v is Tab => TABS.some((t) => t.key === v);
const KIND: Record<string, string> = { git_email: "Email git", gitlab_user: "GitLab", telegram: "Telegram" };

/** `#people/teams` → ["people", "teams"]; the sub-tab part is optional. */
function readHash(): [string, string | null] {
  const [key, sub] = window.location.hash.replace("#", "").split("/");
  return [key ?? "", sub ? decodeURIComponent(sub) : null];
}

/**
 * docs/specs/10 "Admin", one tab per configuration area. The tab and its sub-tab live in the URL
 * hash (`#people/teams`), so a link opens the exact view and Back/Forward step through them.
 */
export default function AdminPage() {
  // null until the hash is read, so a deep link never paints (or fetches) the wrong tab first.
  const [tab, setTab] = useState<Tab | null>(null);
  const [sub, setSub] = useState<string | null>(null);
  // The shell already fetched /auth/me; this reads the same cache entry. Chạy command only
  // shows while COMMAND_RUNS_ENABLED is on (a deep link to it then lands on the first tab).
  const { data: me, error: meError } = useApi<Me>("/auth/me");
  const ready = me !== undefined || meError !== undefined;
  const commands = !!me?.command_runs;
  const tabs = commands ? TABS : TABS.filter((t) => t.key !== "commands");

  useEffect(() => {
    if (!ready) return;
    const fromHash = () => {
      const [key, part] = readHash();
      const next = isTab(key) && (key !== "commands" || commands) ? key : "gitlab";
      setTab(next);
      // Dự án & Repo links here with ?members=bp:3: that list lives under Người & Team › Từ GitLab.
      const members = next === "people" && !part && new URLSearchParams(window.location.search).has("members");
      setSub(members ? "gitlab" : next === key ? part : null);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, [ready, commands]);

  // The hash is the source of truth: setting it fires hashchange and keeps Back/Forward working.
  const go = (key: Tab, part?: string) => {
    const hash = part ? `#${key}/${encodeURIComponent(part)}` : `#${key}`;
    if (window.location.hash === hash) return;
    window.location.hash = hash;
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex((t) => t.key === tab);
    const next =
      e.key === "ArrowRight" ? (index + 1) % tabs.length
      : e.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : -1;
    if (next < 0) return;
    e.preventDefault();
    go(tabs[next].key);
    document.getElementById(`tab-${tabs[next].key}`)?.focus();
  };
  const current = tabs.find((t) => t.key === tab) ?? tabs[0];
  const nav: SubNav = { sub, onSub: (part) => go(current.key, part) };

  return (
    <>
      <PageHeader eyebrow="Admin" title="Quản trị" subtitle="Mọi thay đổi ở đây được ghi audit log." foldable={false} />
      <div className="tabs admin-tabs" role="tablist" aria-label="Khu vực cấu hình" onKeyDown={onKey}>
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`tab-${t.key}`}
            aria-selected={current.key === t.key}
            aria-controls={`panel-${t.key}`}
            tabIndex={current.key === t.key ? 0 : -1}
            className={current.key === t.key ? "active" : undefined}
            onClick={() => go(t.key)}
            title={t.hint}
          >
            <Icon name={t.icon} size={15} />
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${current.key}`} aria-labelledby={`tab-${current.key}`}>
        {tab === null && <Loading rows={3} />}
        {tab === "gitlab" && <ConnectionsTab />}
        {tab === "repos" && <ReposTab />}
        {tab === "people" && <PeopleTab nav={nav} />}
        {tab === "telegram" && <TelegramAdmin />}
        {tab === "webhooks" && <WebhookAdmin />}
        {tab === "llm" && <LlmTab nav={nav} />}
        {tab === "commands" && <CommandRunner nav={nav} />}
        {tab === "data" && <PurgeData />}
      </div>
    </>
  );
}

/** Shared "run an action, show its outcome" helpers for the small tabs below. */
function useActions() {
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>, done?: string): Promise<boolean> {
    setBusy(true);
    try {
      const result = await action();
      setMessage(done ?? String(result));
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }
  function submit(
    e: FormEvent<HTMLFormElement>,
    action: (f: FormData) => Promise<unknown>,
    done?: string,
    after?: () => void,
  ) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    void run(async () => {
      const result = await action(fields);
      form.reset();
      return result;
    }, done).then((ok) => ok && after?.());
  }
  return { message, setMessage, run, submit, busy };
}

function ConnectionsTab() {
  const connections = useApi<Connection[]>("/connections");
  const { message, setMessage, run, submit, busy } = useActions();
  const [renaming, setRenaming] = useState<number | null>(null);
  const [retoken, setRetoken] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const error = message?.startsWith("Lỗi") ? message : null;
  return (
    <>
      {!adding && <Message text={message} onClose={() => setMessage(null)} />}
      {adding && (
        <Drawer title="Thêm kết nối GitLab" width={480} onClose={() => setAdding(false)}>
          <form
            className="adm-form"
            onSubmit={(e) =>
              submit(
                e,
                async (f) => {
                  await api.post("/connections", {
                    name: f.get("name"),
                    base_url: f.get("base_url"),
                    token: f.get("token"),
                  });
                  await connections.mutate();
                },
                "Đã thêm kết nối GitLab.",
                () => setAdding(false),
              )
            }
          >
            <Message text={error} onClose={() => setMessage(null)} />
            <p className="muted small">Tài khoản bot GitLab với scope read_api (thêm api nếu muốn comment lên MR).</p>
            <label className="adm-field">
              <span className="small muted">Tên</span>
              <input name="name" placeholder="Ví dụ gitlab" required autoFocus />
            </label>
            <label className="adm-field">
              <span className="small muted">URL máy chủ</span>
              <input name="base_url" type="url" placeholder="https://gitlab.company.vn" required />
            </label>
            <label className="adm-field">
              <span className="small muted">Token bot (read_api, + api để comment)</span>
              <input name="token" type="password" required autoComplete="new-password" />
            </label>
            <div className="adm-form-foot">
              <button type="button" className="ghost" onClick={() => setAdding(false)}>
                Đóng
              </button>
              <button className="primary" type="submit" disabled={busy}>
                Thêm kết nối
              </button>
            </div>
          </form>
        </Drawer>
      )}
      <Card
        title="Kết nối GitLab"
        subtitle="Token được mã hoá trong DB và không bao giờ hiển thị lại."
        collapsible={false}
        flush
        actions={
          <button type="button" className="primary sm" onClick={() => { setMessage(null); setAdding(true); }}>
            <Icon name="plus" size={14} />
            Thêm kết nối
          </button>
        }
      >
        {!connections.data ? (
          <div className="adm-pad">
            <Loading error={connections.error} />
          </div>
        ) : connections.data.length === 0 ? (
          <EmptyState icon="gitlab" title="Chưa có kết nối GitLab nào">
            Bấm &ldquo;Thêm kết nối&rdquo; để khai báo máy chủ GitLab và token bot, rồi thêm repo ở tab Dự án &amp; Repo.
          </EmptyState>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="stack-sm">
              <thead>
                <tr>
                  <th>Tên</th>
                  <th>URL</th>
                  <th className="num">Repo</th>
                  <th>
                    <span className="visually-hidden">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {connections.data.map((c) => (
                  <tr key={c.id}>
                    <td>
                      {renaming === c.id ? (
                        <form
                          className="inline-edit"
                          onSubmit={(e) =>
                            submit(
                              e,
                              async (f) => {
                                await api.patch(`/connections/${c.id}`, { name: f.get("name") });
                                await connections.mutate();
                                setRenaming(null);
                              },
                              "Đã đổi tên kết nối.",
                            )
                          }
                        >
                          <input name="name" defaultValue={c.name} required maxLength={100} aria-label="Tên kết nối" autoFocus />
                          <button type="submit" disabled={busy}>
                            Lưu
                          </button>
                          <IconButton icon="close" label="Huỷ" onClick={() => setRenaming(null)} />
                        </form>
                      ) : (
                        <span className="inline-edit">
                          <strong>{c.name}</strong>
                          <IconButton icon="edit" label="Đổi tên" onClick={() => setRenaming(c.id)} />
                        </span>
                      )}
                    </td>
                    <td className="muted">{c.base_url}</td>
                    <td className="num">{c.repos}</td>
                    <td>
                      <div className="toolbar" style={{ margin: 0, justifyContent: "flex-end" }}>
                        {retoken === c.id ? (
                          <form
                            className="toolbar"
                            style={{ margin: 0 }}
                            onSubmit={(e) =>
                              submit(
                                e,
                                (f) => api.patch(`/connections/${c.id}`, { token: f.get("token") }),
                                `Đã đổi token của ${c.name}; thu hồi token cũ trên GitLab.`,
                                () => setRetoken(null),
                              )
                            }
                          >
                            <input
                              name="token"
                              type="password"
                              className="compact"
                              placeholder="Token mới"
                              aria-label={`Token mới cho ${c.name}`}
                              required
                              autoComplete="new-password"
                              autoFocus
                            />
                            <button type="submit" className="sm" disabled={busy}>
                              Đổi token
                            </button>
                            <IconButton icon="close" label="Huỷ đổi token" onClick={() => setRetoken(null)} />
                          </form>
                        ) : (
                          <button type="button" className="sm ghost" onClick={() => setRetoken(c.id)}>
                            Đổi token
                          </button>
                        )}
                        <IconButton
                          icon="remove"
                          label={c.repos ? `Còn ${c.repos} repo dùng kết nối này: gỡ repo trước (Dự án & Repo)` : "Xoá kết nối"}
                          tone="danger"
                          disabled={busy || c.repos > 0}
                          onClick={() =>
                            window.confirm(`Xoá kết nối "${c.name}" (${c.base_url})?`) &&
                            void run(async () => {
                              await api.delete(`/connections/${c.id}`);
                              await connections.mutate();
                            }, `Đã xoá kết nối ${c.name}.`)
                          }
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function ReposTab() {
  const connections = useApi<Connection[]>("/connections");
  return <RepoAdmin connections={connections.data ?? []} />;
}

type PeopleSub = "users" | "teams" | "unmapped" | "gitlab";

/** Người & Team: one sub-tab per list, so 3 teams and 300 people never share one long page. */
function PeopleTab({ nav }: { nav: SubNav }) {
  // The counts on the sub-tabs; the lists below read the same SWR cache entries.
  const users = useApi<Person[]>("/users");
  const teams = useApi<Team[]>("/teams");
  const unmapped = useApi<Account[]>("/identity/unmapped");
  const tabs: SubTab<PeopleSub>[] = [
    { key: "users", label: "Người dùng", count: users.data?.filter((u) => u.active !== false).length, hint: "Người đang hoạt động" },
    { key: "teams", label: "Team", count: teams.data?.length, hint: "Team, thành viên và vai trò" },
    { key: "unmapped", label: "Tài khoản chưa gắn", count: unmapped.data?.length, hint: "Tài khoản Git, GitLab, Telegram chưa nhận ra" },
    { key: "gitlab", label: "Từ GitLab", hint: "Lấy thành viên của repo hoặc dự án trên GitLab để tạo hoặc gắn người" },
  ];
  const sub = pickSub(nav, tabs, "users");
  return (
    <>
      <SubTabs id="people" label="Người & Team" tabs={tabs} value={sub} onChange={nav.onSub} />
      <SubPanel id="people" tab={sub}>
        {sub === "users" && <UsersAdmin />}
        {sub === "teams" && <TeamsAdmin />}
        {sub === "unmapped" && <UnmappedAccounts />}
        {sub === "gitlab" && <GitLabMembers />}
      </SubPanel>
    </>
  );
}

function UnmappedAccounts() {
  const unmapped = useApi<Account[]>("/identity/unmapped");
  const users = useApi<Person[]>("/users");
  const { message, setMessage, run, busy } = useActions();
  // Earlier reviews of the account follow the link (and their weeks / months are re-scored).
  const [backfill, setBackfill] = useState(true);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const all = unmapped.data ?? [];
  const kinds = [...new Set(all.map((a) => a.kind))];
  const q = query.trim().toLowerCase();
  const shown = all.filter(
    (a) =>
      (kind === "all" || a.kind === kind) &&
      (!q || `${a.external_key} ${a.display_name ?? ""}`.toLowerCase().includes(q)),
  );
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      <Card
        title={`Tài khoản chưa gắn người${unmapped.data ? ` (${unmapped.data.length})` : ""}`}
        subtitle="Tài khoản Git, GitLab hoặc Telegram chưa nhận ra; chọn người để gắn."
        collapsible={false}
        flush
      >
        {!unmapped.data || !users.data ? (
          <div className="adm-pad">
            <Loading error={unmapped.error ?? users.error} />
          </div>
        ) : unmapped.data.length === 0 ? (
          <EmptyState icon="checkCircle" tone="good" title="Mọi tài khoản đã được gắn" />
        ) : (
          <>
            <div className="toolbar adm-pad">
              <input
                className="compact"
                type="search"
                placeholder="Tìm tài khoản"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Tìm tài khoản chưa gắn"
              />
              {kinds.length > 1 && (
                <Segmented
                  label="Loại tài khoản"
                  value={kind}
                  onChange={setKind}
                  options={[
                    { value: "all", label: `Tất cả (${all.length})` },
                    ...kinds.map((k) => ({ value: k, label: `${KIND[k] ?? k} (${all.filter((a) => a.kind === k).length})` })),
                  ]}
                />
              )}
              <label className="inline-check small">
                <input type="checkbox" checked={backfill} onChange={(e) => setBackfill(e.target.checked)} />
                Chuyển cả các review trước đây sang người được gắn và tính lại điểm
              </label>
            </div>
            {shown.length === 0 ? (
              <p className="muted small adm-pad">Không có tài khoản nào khớp.</p>
            ) : (
              <div className="table-wrap scroll-y">
                <table>
                  <thead>
                    <tr>
                      <th>Loại</th>
                      <th>Tài khoản</th>
                      <th>Gắn với</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((a) => (
                      <tr key={a.id}>
                        <td className="muted nowrap">{KIND[a.kind] ?? a.kind}</td>
                        <td>
                          {a.external_key}
                          {a.display_name ? <span className="muted"> ({a.display_name})</span> : ""}
                        </td>
                        <td>
                          <select
                            className="compact"
                            defaultValue=""
                            disabled={busy}
                            aria-label={`Gắn ${a.external_key} với người`}
                            onChange={(e) =>
                              e.target.value &&
                              run(async () => {
                                const done = await api.put<AccountLinked>(`/accounts/${a.id}/user`, {
                                  user_id: Number(e.target.value),
                                  backfill,
                                });
                                await unmapped.mutate();
                                return `Đã gắn ${a.external_key}.${attributedText(done.reviews_attributed, done.periods_queued)}`;
                              })
                            }
                          >
                            <option value="">Gắn với…</option>
                            {(users.data ?? [])
                              .filter((u) => u.active !== false)
                              .map((u) => (
                                <option key={u.id} value={u.id}>
                                  {u.full_name ?? u.primary_email}
                                </option>
                              ))}
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="adm-foot muted small">
              Hiển thị {shown.length.toLocaleString("vi-VN")} / {all.length.toLocaleString("vi-VN")} tài khoản.
            </p>
          </>
        )}
      </Card>
    </>
  );
}

type LlmSub = "costs" | "settings" | "claude";
const LLM_TABS: SubTab<LlmSub>[] = [
  { key: "costs", label: "Chi phí", hint: "Chi phí LLM theo tháng và ngân sách" },
  { key: "settings", label: "Cài đặt review", hint: "Model, ngân sách, giới hạn, tự đóng finding" },
  { key: "claude", label: "Kết nối Claude", hint: "Đăng nhập Claude và kiểm tra kết nối LLM" },
];

function LlmTab({ nav }: { nav: SubNav }) {
  const sub = pickSub(nav, LLM_TABS, "costs");
  return (
    <>
      <SubTabs id="llm" label="LLM & Chi phí" tabs={LLM_TABS} value={sub} onChange={nav.onSub} />
      <SubPanel id="llm" tab={sub}>
        {sub === "costs" && <LlmCostsView onSettings={() => nav.onSub("settings")} />}
        {sub === "settings" && <ReviewSettings />}
        {sub === "claude" && (
          <div className="grid two adm-top">
            <ClaudeLogin />
            <LlmCheck />
          </div>
        )}
      </SubPanel>
    </>
  );
}

function LlmCostsView({ onSettings }: { onSettings: () => void }) {
  const costs = useApi<LlmCosts>("/admin/llm-costs");
  const data = costs.data;
  const latest = data?.months[0];
  const pct = data && latest && data.budget_usd ? Math.round((100 * latest.total_usd) / data.budget_usd) : null;
  const usd = (n: number) => `$${n.toFixed(2)}`;
  return (
    <>
      <StatStrip label="Chi phí LLM tháng này">
        <div className="kstat" title="Review, knowledge, lệnh .claude, đóng finding và Stetho">
          <span className="kstat-label">Chi phí tháng này</span>
          <span className="kstat-value">{latest ? usd(latest.total_usd) : "—"}</span>
          <span className="kstat-sub">Mọi việc có gọi LLM</span>
        </div>
        <div className="kstat">
          <span className="kstat-label">% ngân sách</span>
          <span className="kstat-value">
            <span className={pct !== null && pct >= 80 ? "tone-bad" : undefined}>{pct === null ? "—" : `${pct}%`}</span>
          </span>
          <span className="kstat-sub">{data ? `Ngân sách $${data.budget_usd}, dừng khi hết` : ""}</span>
        </div>
        <div className="kstat">
          <span className="kstat-label">Reviews tháng này</span>
          <span className="kstat-value">{latest ? latest.reviews : "—"}</span>
          <span className="kstat-sub">{latest ? usd(latest.cost_usd) : "Có gọi LLM"}</span>
        </div>
        <div className="kstat">
          <span className="kstat-label">Câu hỏi Stetho tháng này</span>
          <span className="kstat-value">{latest ? latest.assistant_turns : "—"}</span>
          <span className="kstat-sub">{latest ? usd(latest.assistant_usd) : "Trợ lý AI"}</span>
        </div>
      </StatStrip>
      <Card title="Chi phí LLM theo tháng" collapsible={false} flush>
        {!data ? (
          <div className="adm-pad">
            <Loading error={costs.error} />
          </div>
        ) : data.months.length === 0 ? (
          <p className="muted adm-pad">Chưa có chi phí LLM nào.</p>
        ) : (
          <div className="table-wrap scroll-y">
            <table>
              <thead>
                <tr>
                  <th>Tháng</th>
                  <th className="num">Review</th>
                  <th className="num">Knowledge</th>
                  <th className="num">Lệnh .claude</th>
                  <th className="num">Đóng finding</th>
                  <th className="num">Stetho</th>
                  <th className="num">Tổng</th>
                  <th className="num">% ngân sách</th>
                </tr>
              </thead>
              <tbody>
                {data.months.map((m) => (
                  <tr key={m.month}>
                    <td>{m.month}</td>
                    <td className="num">
                      {usd(m.cost_usd)} <span className="muted small">· {m.reviews}</span>
                    </td>
                    <td className="num">
                      {usd(m.knowledge_usd)} <span className="muted small">· {m.knowledge_builds}</span>
                    </td>
                    <td className="num">
                      {usd(m.commands_usd)} <span className="muted small">· {m.commands_runs}</span>
                    </td>
                    <td className="num">
                      {usd(m.closures_usd)} <span className="muted small">· {m.closure_checks}</span>
                    </td>
                    <td className="num" title={m.assistant_estimated ? `${m.assistant_estimated} câu dừng giữa chừng: chi phí ước tính` : undefined}>
                      {usd(m.assistant_usd)}
                      {m.assistant_estimated > 0 && <span className="muted">*</span>} <span className="muted small">· {m.assistant_turns}</span>
                    </td>
                    <td className="num">
                      <strong>{usd(m.total_usd)}</strong>
                    </td>
                    <td className="num">{data.budget_usd ? `${((100 * m.total_usd) / data.budget_usd).toFixed(0)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="adm-foot muted small">
          Ngân sách tháng: MONTHLY_LLM_BUDGET_USD, sửa được ở{" "}
          <button type="button" className="link" onClick={onSettings}>
            Cài đặt review
          </button>
          . Số sau dấu · là số lượt (review, lần dựng knowledge, lần chạy lệnh, lần kiểm yêu cầu đóng, câu hỏi); kiểm tự động sau review nằm trong chi phí review. * Chi phí Stetho ước tính (dư) cho câu hỏi bị dừng trước khi Claude báo giá. Rubric v1; trọng số severity đổi qua SCORING_WEIGHTS rồi chạy{" "}
          <code>codepulse metrics recompute</code>.
        </p>
      </Card>
    </>
  );
}
