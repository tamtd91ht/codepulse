"use client";

import { type FormEvent, type KeyboardEvent, useEffect, useState } from "react";

import { ClaudeLogin } from "@/components/ClaudeLogin";
import { CommandRunner } from "@/components/CommandRunner";
import { GitLabMembers } from "@/components/GitLabMembers";
import { Icon, type IconName, IconButton } from "@/components/icons";
import { LlmCheck } from "@/components/LlmCheck";
import { PurgeData } from "@/components/PurgeData";
import { RepoAdmin } from "@/components/RepoAdmin";
import { ReviewSettings } from "@/components/ReviewSettings";
import { TeamAdmin } from "@/components/TeamAdmin";
import { TelegramAdmin } from "@/components/TelegramAdmin";
import { Card, EmptyState, Loading, Message, PageHeader } from "@/components/ui";
import { WebhookAdmin } from "@/components/WebhookAdmin";
import { ApiError, api, useApi } from "@/lib/api";
import type { Account, Connection, LlmCosts, Me, Person } from "@/lib/types";

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

/** docs/specs/10 "Admin", one tab per configuration area; the tab lives in the URL hash. */
export default function AdminPage() {
  // null until the hash is read, so a deep link never paints (or fetches) the wrong tab first.
  const [tab, setTab] = useState<Tab | null>(null);
  // The shell already fetched /auth/me; this reads the same cache entry. Chạy command only
  // shows while COMMAND_RUNS_ENABLED is on (a deep link to it then lands on the first tab).
  const { data: me, error: meError } = useApi<Me>("/auth/me");
  const ready = me !== undefined || meError !== undefined;
  const commands = !!me?.command_runs;
  const tabs = commands ? TABS : TABS.filter((t) => t.key !== "commands");

  useEffect(() => {
    if (!ready) return;
    const fromHash = () => {
      const key = window.location.hash.replace("#", "");
      setTab(isTab(key) && (key !== "commands" || commands) ? key : "gitlab");
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, [ready, commands]);

  // The hash is the source of truth: setting it fires hashchange and keeps Back/Forward working.
  const go = (key: Tab) => {
    if (window.location.hash === `#${key}`) return;
    window.location.hash = key;
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

  return (
    <>
      <PageHeader eyebrow="Admin" title="Quản trị" subtitle="Mọi thay đổi ở đây được ghi audit log." />
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
        {tab === "people" && <PeopleTab />}
        {tab === "telegram" && <TelegramAdmin />}
        {tab === "webhooks" && <WebhookAdmin />}
        {tab === "llm" && <LlmTab />}
        {tab === "commands" && <CommandRunner />}
        {tab === "data" && <PurgeData />}
      </div>
    </>
  );
}

/** Shared "run an action, show its outcome" helpers for the small tabs below. */
function useActions() {
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>, done?: string) {
    setBusy(true);
    try {
      const result = await action();
      setMessage(done ?? String(result));
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }
  function submit(e: FormEvent<HTMLFormElement>, action: (f: FormData) => Promise<unknown>, done?: string) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    void run(async () => {
      const result = await action(fields);
      form.reset();
      return result;
    }, done);
  }
  return { message, setMessage, run, submit, busy };
}

function ConnectionsTab() {
  const connections = useApi<Connection[]>("/connections");
  const { message, setMessage, run, submit, busy } = useActions();
  const [renaming, setRenaming] = useState<number | null>(null);
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      <Card title="Kết nối GitLab" subtitle="Token được mã hoá trong DB và không bao giờ hiển thị lại." flush>
        {!connections.data ? (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={connections.error} />
          </div>
        ) : connections.data.length === 0 ? (
          <EmptyState icon="gitlab" title="Chưa có kết nối GitLab nào">
            Thêm máy chủ GitLab và token bot bên dưới, rồi thêm repo ở tab Dự án &amp; Repo.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="stack-sm">
              <thead>
                <tr>
                  <th>Tên</th>
                  <th>URL</th>
                  <th className="num">Repo</th>
                  <th />
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
                          <button type="submit" className="primary" disabled={busy}>
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
                        <form
                          className="toolbar"
                          style={{ margin: 0 }}
                          onSubmit={(e) =>
                            submit(
                              e,
                              (f) => api.patch(`/connections/${c.id}`, { token: f.get("token") }),
                              `Đã đổi token của ${c.name}; thu hồi token cũ trên GitLab.`,
                            )
                          }
                        >
                          <input name="token" type="password" placeholder="Token mới" required autoComplete="new-password" />
                          <button type="submit" disabled={busy}>
                            Đổi token
                          </button>
                        </form>
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
      <Card title="Thêm kết nối" subtitle="Tài khoản bot GitLab với scope read_api (thêm api nếu muốn comment lên MR).">
        <form
          className="toolbar"
          style={{ margin: 0 }}
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
            )
          }
        >
          <input name="name" placeholder="Tên, ví dụ gitlab" required />
          <input name="base_url" type="url" placeholder="https://gitlab.company.vn" required />
          <input name="token" type="password" placeholder="Token bot (read_api, + api để comment)" required autoComplete="new-password" />
          <button className="primary" type="submit" disabled={busy}>
            Thêm kết nối
          </button>
        </form>
      </Card>
    </>
  );
}

function ReposTab() {
  const connections = useApi<Connection[]>("/connections");
  return <RepoAdmin connections={connections.data ?? []} />;
}

function PeopleTab() {
  const unmapped = useApi<Account[]>("/identity/unmapped");
  const users = useApi<Person[]>("/users");
  const { message, setMessage, run, busy } = useActions();
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      <GitLabMembers />
      <TeamAdmin />
      <Card
        title={`Tài khoản chưa gắn người${unmapped.data ? ` (${unmapped.data.length})` : ""}`}
        subtitle="Tài khoản Git, GitLab hoặc Telegram chưa nhận ra; chọn người để gắn."
        id="unmapped-accounts"
        // Collapsed by default: a long list of unrecognised accounts buries the cards above it.
        defaultCollapsed
        flush
      >
        {!unmapped.data || !users.data ? (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={unmapped.error ?? users.error} />
          </div>
        ) : unmapped.data.length === 0 ? (
          <EmptyState icon="checkCircle" tone="good" title="Mọi tài khoản đã được gắn" />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Loại</th>
                  <th>Tài khoản</th>
                  <th>Gắn với</th>
                </tr>
              </thead>
              <tbody>
                {unmapped.data.map((a) => (
                  <tr key={a.id}>
                    <td className="muted">{KIND[a.kind] ?? a.kind}</td>
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
                            await api.put(`/accounts/${a.id}/user`, { user_id: Number(e.target.value) });
                            await unmapped.mutate();
                          }, `Đã gắn ${a.external_key}.`)
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
      </Card>
    </>
  );
}

function LlmTab() {
  const costs = useApi<LlmCosts>("/admin/llm-costs");
  const data = costs.data;
  const latest = data?.months[0];
  const pct = data && latest && data.budget_usd ? Math.round((100 * latest.cost_usd) / data.budget_usd) : null;
  return (
    <>
      <div className="grid kpis">
        <div className="card kpi">
          <div className="label">Chi phí tháng này</div>
          <div className="value">
            {latest ? `$${latest.cost_usd.toFixed(2)}` : "—"}
          </div>
          <div className="delta">
            <span className="muted">{data ? `Ngân sách $${data.budget_usd}` : ""}</span>
          </div>
        </div>
        <div className="card kpi">
          <div className="label">% ngân sách</div>
          <div className={`value${pct !== null && pct >= 80 ? " bad" : ""}`}>{pct === null ? "—" : `${pct}%`}</div>
          <div className="delta">
            <span className="muted">Cảnh báo từ 80%, dừng review khi hết</span>
          </div>
        </div>
        <div className="card kpi">
          <div className="label">Reviews tháng này</div>
          <div className="value">{latest ? latest.reviews : "—"}</div>
          <div className="delta">
            <span className="muted">Có gọi LLM</span>
          </div>
        </div>
      </div>
      <Card title="Chi phí LLM theo tháng" flush>
        {!data ? (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={costs.error} />
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tháng</th>
                  <th className="num">Reviews</th>
                  <th className="num">Chi phí</th>
                  <th className="num">% ngân sách</th>
                </tr>
              </thead>
              <tbody>
                {data.months.map((m) => (
                  <tr key={m.month}>
                    <td>{m.month}</td>
                    <td className="num">{m.reviews}</td>
                    <td className="num">${m.cost_usd.toFixed(2)}</td>
                    <td className="num">{data.budget_usd ? `${((100 * m.cost_usd) / data.budget_usd).toFixed(0)}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small" style={{ padding: "10px 18px 14px" }}>
          Ngân sách tháng: MONTHLY_LLM_BUDGET_USD, sửa được ở “Cài đặt review” bên dưới. Rubric v1; trọng số severity đổi qua SCORING_WEIGHTS rồi chạy{" "}
          <code>codepulse metrics recompute</code>.
        </p>
      </Card>
      <ReviewSettings />
      <ClaudeLogin />
      <LlmCheck />
    </>
  );
}
