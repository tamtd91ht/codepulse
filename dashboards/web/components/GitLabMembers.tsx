"use client";

import { useEffect, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { EmptyState, Loading, Message, Pill, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { BusinessProject, GitLabMember, GitLabMembers as Members, GitLabMembersImported, Person, Project } from "@/lib/types";

/** What the admin chose for one member: skip, create, link to the person `userId`, or still pick one ("other"). */
type RowAction = "skip" | "create" | "link" | "other";

interface Row {
  action: RowAction;
  userId: number | null;
  fullName: string;
  email: string;
  /** The text typed in "Người khác…", resolved to `userId` when it names one person. */
  search: string;
}

type Filter = "todo" | "link" | "create" | "skip" | "linked";

const EMAIL = /^[^@\s]+@[^@\s]+$/;
const ACCESS_LABEL: Record<string, string> = {
  guest: "Khách",
  planner: "Lập kế hoạch",
  reporter: "Chỉ xem",
  developer: "Lập trình",
  maintainer: "Maintainer",
  owner: "Chủ sở hữu",
};
const STATE_LABEL: Record<string, string> = { blocked: "đã khoá", deactivated: "ngưng hoạt động", banned: "bị cấm" };
const REASON_LABEL: Record<string, string> = {
  same_email: "cùng email",
  username_domain: "email công ty trùng username",
  commit_noreply: "đã commit bằng email noreply của tài khoản này",
  commit_email: "đã commit bằng email công ty trùng username",
  same_name: "chỉ trùng họ tên, hãy kiểm tra",
};

const personLabel = (u: Person) => (u.primary_email ? `${u.full_name ?? u.primary_email} · ${u.primary_email}` : (u.full_name ?? `#${u.id}`));

const initial = (m: GitLabMember): Row => ({
  // "review" (a weak hint) is shown but not preselected; the admin confirms it.
  action: m.action === "link" ? "link" : m.action === "create" ? "create" : "skip",
  userId: m.action === "link" || m.action === "review" ? m.user_id : null,
  fullName: m.name ?? m.username,
  email: m.email ?? "",
  search: "",
});

/** Which list a member sits in for the filter, from the admin's current choice. */
const bucket = (m: GitLabMember, row: Row): Filter => (m.action === "linked" ? "linked" : row.action === "other" ? "link" : row.action);

/**
 * Admin card: who has access to a repo or a dự án on GitLab, linked to CodePulse people (outside
 * docs/specs/05; see app/identity/members.py). Each member comes with a suggestion; nothing is
 * created until the admin reviews a summary and confirms. A linked GitLab account is what
 * sign-in, the Telegram bot (`user=<username>`) and commit attribution use.
 */
export function GitLabMembers() {
  const projects = useApi<BusinessProject[]>("/business-projects");
  const repos = useApi<Project[]>("/projects");
  const users = useApi<Person[]>("/users");
  const { mutate } = useSWRConfig();
  const [source, setSource] = useState("");
  const [members, setMembers] = useState<Members | null>(null);
  const [rows, setRows] = useState<Record<number, Row>>({});
  const [invalid, setInvalid] = useState<Record<number, string>>({});
  const [filter, setFilter] = useState<Filter>("todo");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<{ text: string; tone?: "warning" } | null>(null);
  const card = useRef<HTMLElement>(null);
  const status = useRef<HTMLDivElement>(null);
  // Only the latest request may fill the table (a deep link still loading, then another pick).
  const seq = useRef(0);

  // A source picked elsewhere (Admin › Dự án & Repo links here with ?members=bp:3 or repo:7):
  // listed right away; the parameter is dropped so coming back does not page GitLab again.
  useEffect(() => {
    const from = new URLSearchParams(window.location.search).get("members");
    if (from && /^(bp|repo):\d+$/.test(from)) {
      window.history.replaceState(null, "", `/admin${window.location.hash}`);
      setSource(from);
      void load(from, true);
    }
  }, []); // once, on arrival

  async function load(from: string, scroll = false): Promise<boolean> {
    if (!from) return false;
    const n = ++seq.current;
    const [kind, id] = from.split(":");
    setLoading(true);
    setConfirming(false);
    try {
      const data = await api.get<Members>(kind === "bp" ? `/business-projects/${id}/gitlab-members` : `/projects/${id}/gitlab-members`);
      if (n !== seq.current) return false;
      setMembers(data);
      setRows(Object.fromEntries(data.members.map((m) => [m.gitlab_id, initial(m)])));
      setInvalid({});
      setFilter("todo");
      if (scroll) requestAnimationFrame(() => card.current?.scrollIntoView({ block: "start" }));
      return true;
    } catch (e) {
      if (n !== seq.current) return false;
      setMembers(null);
      setMessage({ text: `Lỗi: ${e instanceof ApiError ? e.message : String(e)}` });
      return false;
    } finally {
      if (n === seq.current) setLoading(false);
    }
  }

  const people = (users.data ?? []).filter((u) => u.active !== false);
  const byLabel = new Map(people.map((u) => [personLabel(u).toLowerCase(), u.id]));
  const nameOf = (id: number | null) => {
    const u = users.data?.find((p) => p.id === id);
    return u ? (u.full_name ?? u.primary_email ?? `#${u.id}`) : "—";
  };
  const set = (id: number, patch: Partial<Row>) => {
    setRows((r) => ({ ...r, [id]: { ...r[id], ...patch } }));
    setInvalid(({ [id]: _, ...rest }) => rest);
    setConfirming(false);
  };
  const pending = (members?.members ?? []).filter((m) => m.action !== "linked");
  const counts = { create: 0, link: 0, skip: 0, other: 0 };
  for (const m of pending) counts[rows[m.gitlab_id]?.action ?? "skip"] += 1;

  /** Every row's problem before anything is sent; the first one gets focus. */
  function check(): boolean {
    const problems: Record<number, string> = {};
    for (const m of pending) {
      const row = rows[m.gitlab_id];
      if (row.action === "create" && row.email.trim() && !EMAIL.test(row.email.trim())) problems[m.gitlab_id] = "Email không hợp lệ.";
      if (row.action === "create" && !row.fullName.trim()) problems[m.gitlab_id] = "Cần họ tên.";
      if (row.action === "other") problems[m.gitlab_id] = "Chọn một người trong danh sách, hoặc đổi lựa chọn.";
    }
    setInvalid(problems);
    const first = Object.keys(problems)[0];
    if (first) {
      setFilter("todo");
      requestAnimationFrame(() => document.getElementById(`gm-${first}`)?.focus());
    }
    return !first;
  }

  async function save() {
    if (!members) return;
    const choices = pending.map((m) => {
      const row = rows[m.gitlab_id];
      const base = { gitlab_id: m.gitlab_id, connection_id: m.connection_id };
      if (row.action === "create") return { ...base, action: "create", full_name: row.fullName.trim(), email: row.email.trim() || null };
      if (row.action === "link" && row.userId) return { ...base, action: "link", user_id: row.userId };
      return { ...base, action: "skip" };
    });
    setSaving(true);
    try {
      const done = await api.post<GitLabMembersImported>("/gitlab-members/import", { members: choices });
      await Promise.all([users.mutate(), mutate("/identity/unmapped"), load(source)]);
      setMessage({
        text:
          `Đã tạo ${done.created} người, gắn ${done.linked} tài khoản GitLab` +
          (done.emails_linked ? `, tự gắn thêm ${done.emails_linked} email commit` : "") +
          ".",
      });
      requestAnimationFrame(() => status.current?.focus());
    } catch (e) {
      const text = e instanceof ApiError ? e.message : String(e);
      // An email another person has: point at the row that carries it.
      const row = pending.find((m) => {
        const email = rows[m.gitlab_id]?.email.trim().toLowerCase();
        return rows[m.gitlab_id]?.action === "create" && email && text.toLowerCase().includes(email);
      });
      if (row) {
        setInvalid({ [row.gitlab_id]: text });
        setFilter("todo");
        requestAnimationFrame(() => document.getElementById(`gm-${row.gitlab_id}`)?.focus());
      }
      setMessage({ text: `Lỗi: ${text}` });
    } finally {
      setSaving(false);
      setConfirming(false);
    }
  }

  const all = members?.members ?? [];
  const q = query.trim().toLowerCase();
  const shown = all.filter((m) => {
    const row = rows[m.gitlab_id];
    if (!row) return false;
    const b = bucket(m, row);
    if (filter === "todo" ? b === "linked" : b !== filter) return false;
    return !q || `${m.name ?? ""} ${m.username} ${m.email ?? ""}`.toLowerCase().includes(q);
  });
  const isProject = source.startsWith("bp:");

  return (
    <section className="card" ref={card} style={{ scrollMarginTop: 16 }}>
      <div className="card-head">
        <div>
          <h2>Thành viên GitLab</h2>
          <div className="sub">
            Lấy danh sách người có quyền vào repo (kể cả thành viên của group) để tạo hoặc gắn với người dùng CodePulse.
            Tài khoản GitLab đã gắn dùng cho đăng nhập, bot Telegram (<code>user=username</code>) và gắn commit.
          </div>
        </div>
      </div>
      <div ref={status} tabIndex={-1}>
        <Message text={message?.text ?? null} tone={message?.tone} onClose={() => setMessage(null)} />
      </div>
      <div className="toolbar">
        <select
          value={source}
          disabled={loading || saving}
          onChange={(e) => {
            seq.current += 1; // an answer for the previous pick is now stale
            setSource(e.target.value);
            setMembers(null);
            setLoading(false);
          }}
          aria-label="Lấy thành viên của"
        >
          <option value="">Chọn dự án hoặc repo…</option>
          {(projects.data ?? []).length > 0 && (
            <optgroup label="Dự án (mọi repo đang theo dõi)">
              {projects.data?.map((p) => (
                <option key={`bp:${p.id}`} value={`bp:${p.id}`}>
                  {p.name} ({p.repos} repo)
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Repo">
            {(repos.data ?? []).map((r) => (
              <option key={`repo:${r.id}`} value={`repo:${r.id}`}>
                {r.path_with_namespace}
              </option>
            ))}
          </optgroup>
        </select>
        <button onClick={() => void load(source)} disabled={!source || loading || saving}>
          {loading ? "Đang lấy…" : "Lấy danh sách"}
        </button>
      </div>

      {/* One list of people for every "Người khác…" field. */}
      <datalist id="cp-people">
        {people.map((u) => (
          <option key={u.id} value={personLabel(u)} />
        ))}
      </datalist>

      {loading && <Loading rows={4}>Đang đọc thành viên từ GitLab…</Loading>}
      {!loading && members && all.length === 0 && (
        <EmptyState icon="members" title={members.repos === 0 ? "Dự án chưa có repo nào đang theo dõi" : `${isProject ? "Dự án" : "Repo"} không có thành viên nào bot đọc được`} />
      )}
      {!loading && members && all.length > 0 && (
        <>
          {members.truncated && (
            <Message
              tone="warning"
              text="Danh sách chưa đủ: chỉ đọc 200 repo và 1.000 thành viên đầu tiên. Lấy theo từng repo để xem phần còn lại."
            />
          )}
          <div className="toolbar">
            <Segmented<Filter>
              label="Lọc thành viên"
              value={filter}
              onChange={setFilter}
              options={[
                { value: "todo", label: `Chưa gắn (${pending.length})` },
                { value: "link", label: `Sẽ gắn (${counts.link + counts.other})` },
                { value: "create", label: `Sẽ tạo (${counts.create})` },
                { value: "skip", label: `Bỏ qua (${counts.skip})` },
                { value: "linked", label: `Đã gắn (${all.length - pending.length})` },
              ]}
            />
            <input className="compact" type="search" placeholder="Tìm tên, username, email" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Tìm thành viên" />
            {pending.length > 0 && (
              <>
                <button
                  className="ghost"
                  onClick={() => {
                    setRows(Object.fromEntries(all.map((m) => [m.gitlab_id, initial(m)])));
                    setInvalid({});
                  }}
                >
                  Theo đề xuất
                </button>
                <button
                  className="ghost"
                  onClick={() => setRows((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, { ...v, action: "skip" as const }])))}
                >
                  Bỏ qua tất cả
                </button>
              </>
            )}
          </div>

          {shown.length === 0 ? (
            <p className="muted small" style={{ padding: "0 18px" }}>
              Không có thành viên nào trong mục này.
            </p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Thành viên GitLab</th>
                    <th>Quyền</th>
                    <th>Làm gì</th>
                    <th>Khi tạo mới</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((m) => {
                    const row = rows[m.gitlab_id];
                    const problem = invalid[m.gitlab_id];
                    const suggested = m.user_id !== null && m.action !== "linked" ? people.find((u) => u.id === m.user_id) : undefined;
                    const picked = row.action === "link" && row.userId !== null && row.userId !== suggested?.id ? people.find((u) => u.id === row.userId) : undefined;
                    const value = row.action === "link" ? (row.userId === suggested?.id ? "suggested" : "picked") : row.action;
                    return (
                      <tr key={m.gitlab_id}>
                        <td>
                          <strong>{m.name ?? m.username}</strong> <span className="muted">@{m.username}</span>
                          {m.email && <div className="muted small">{m.email}</div>}
                          {members.repos > 1 && <div className="muted small">{m.repos.length} repo</div>}
                        </td>
                        <td className="muted">
                          {ACCESS_LABEL[m.access] ?? m.access}
                          {m.state !== "active" && (
                            <>
                              {" "}
                              <Pill tone="warn">{STATE_LABEL[m.state] ?? m.state}</Pill>
                            </>
                          )}
                        </td>
                        <td>
                          {m.action === "linked" ? (
                            <span>Đã gắn với {nameOf(m.user_id)}</span>
                          ) : (
                            <>
                              <select
                                id={`gm-${m.gitlab_id}`}
                                className="compact"
                                value={value}
                                aria-invalid={problem ? true : undefined}
                                aria-describedby={problem ? `gm-${m.gitlab_id}-err` : undefined}
                                onChange={(e) => {
                                  const v = e.target.value;
                                  if (v === "suggested") set(m.gitlab_id, { action: "link", userId: suggested?.id ?? null });
                                  else if (v === "picked") set(m.gitlab_id, { action: "link" });
                                  else set(m.gitlab_id, { action: v as RowAction, ...(v === "other" ? { userId: null, search: "" } : {}) });
                                }}
                                aria-label={`Làm gì với ${m.username}`}
                              >
                                <option value="skip">Bỏ qua</option>
                                <option value="create">Tạo người mới</option>
                                {suggested && <option value="suggested">Gắn với {personLabel(suggested)}</option>}
                                {picked && <option value="picked">Gắn với {personLabel(picked)}</option>}
                                <option value="other">Gắn với người khác…</option>
                              </select>
                              {row.action === "other" && (
                                <input
                                  className="compact"
                                  list="cp-people"
                                  placeholder="Gõ tên hoặc email"
                                  value={row.search}
                                  aria-label={`Người để gắn ${m.username}`}
                                  onChange={(e) => {
                                    const id = byLabel.get(e.target.value.toLowerCase());
                                    set(m.gitlab_id, id ? { action: "link", userId: id, search: "" } : { search: e.target.value });
                                  }}
                                  style={{ display: "block", marginTop: 4 }}
                                />
                              )}
                              <div className="muted small">
                                {m.action === "skip"
                                  ? `Đề xuất bỏ qua: ${m.reason === "bot" ? "tài khoản bot" : `tài khoản GitLab ${STATE_LABEL[m.reason ?? ""] ?? m.reason}`}`
                                  : suggested
                                    ? `${m.action === "review" ? "Có thể là" : "Đề xuất gắn với"} ${nameOf(suggested.id)} (${REASON_LABEL[m.reason ?? ""] ?? m.reason})`
                                    : "Không thấy ai khớp, đề xuất tạo người mới"}
                              </div>
                              {problem && (
                                <div id={`gm-${m.gitlab_id}-err`} className="bad small" role="alert">
                                  {problem}
                                </div>
                              )}
                            </>
                          )}
                        </td>
                        <td>
                          {row.action === "create" && m.action !== "linked" ? (
                            <div style={{ display: "grid", gap: 4 }}>
                              <input className="compact" value={row.fullName} onChange={(e) => set(m.gitlab_id, { fullName: e.target.value })} aria-label={`Họ tên của ${m.username}`} />
                              <input
                                className="compact"
                                type="email"
                                placeholder="email (không bắt buộc)"
                                value={row.email}
                                onChange={(e) => set(m.gitlab_id, { email: e.target.value })}
                                aria-label={`Email của ${m.username}`}
                              />
                            </div>
                          ) : (
                            <span className="faint">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {pending.length > 0 && (
            <div className="toolbar" style={{ justifyContent: "flex-end" }}>
              {confirming ? (
                <>
                  <span role="status">
                    Sẽ <strong>tạo {counts.create}</strong> người mới, <strong>gắn {counts.link}</strong> tài khoản, bỏ qua {counts.skip}.
                  </span>
                  <button className="ghost" onClick={() => setConfirming(false)} disabled={saving}>
                    Xem lại
                  </button>
                  <button className="primary" onClick={() => void save()} disabled={saving}>
                    {saving ? "Đang lưu…" : "Xác nhận"}
                  </button>
                </>
              ) : (
                <button
                  className="primary"
                  disabled={saving}
                  onClick={() => {
                    if (counts.create + counts.link === 0) {
                      setMessage({ text: "Chưa chọn ai để tạo hoặc gắn.", tone: "warning" });
                      return;
                    }
                    if (check()) setConfirming(true);
                  }}
                >
                  Lưu lựa chọn…
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
