"use client";

import { type FormEvent, useState } from "react";
import { useSWRConfig } from "swr";

import { UserTokensDrawer } from "@/components/ApiTokens";
import { Icon, IconButton } from "@/components/icons";
import { ProfileDrawer } from "@/components/ProfileDrawer";
import { Avatar, Drawer, EmptyState, Loading, Message, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { localDay } from "@/lib/format";
import type { Member, Person, Team } from "@/lib/types";

const ROLES: { value: string; label: string }[] = [
  { value: "member", label: "Thành viên" },
  { value: "lead", label: "Lead" },
  { value: "manager", label: "Manager" },
];

const today = () => localDay();
const nameOf = (u: { full_name: string | null; primary_email: string | null }, fallback = "người này") =>
  u.full_name ?? u.primary_email ?? fallback;

/** Run an action, report its outcome; resolves true on success (drawers close only then). */
function useRun() {
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>, done: string): Promise<boolean> {
    setBusy(true);
    try {
      await action();
      setMessage(done);
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }
  function submit(e: FormEvent<HTMLFormElement>, action: (f: FormData) => Promise<unknown>, done: string, after?: () => void) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    void run(async () => {
      await action(fields);
      form.reset();
    }, done).then((ok) => ok && after?.());
  }
  return { message, setMessage, busy, run, submit };
}

type UserFilter = "active" | "inactive" | "manage" | "all";

/**
 * Admin › Người & Team › Người dùng: everyone CodePulse knows, searchable, in a fixed-height
 * list with a sticky header; "Thêm người" opens a drawer, a row's pen opens the profile drawer.
 */
export function UsersAdmin() {
  const users = useApi<Person[]>("/users");
  const { mutate } = useSWRConfig();
  const { message, setMessage, busy, run, submit } = useRun();
  const [profileId, setProfileId] = useState<number | null>(null);
  const [tokensOf, setTokensOf] = useState<Person | null>(null);
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<UserFilter>("active");

  // A person removed or renamed also changes the member lists of their teams.
  const refreshMembers = () =>
    mutate((key) => typeof key === "string" && key.startsWith("/teams/") && key.endsWith("/members"));

  const all = users.data ?? [];
  const counts = {
    active: all.filter((u) => u.active !== false).length,
    inactive: all.filter((u) => u.active === false).length,
    manage: all.filter((u) => u.manage_all || u.is_admin).length,
    all: all.length,
  };
  const q = query.trim().toLowerCase();
  const shown = all.filter((u) => {
    if (filter === "active" && u.active === false) return false;
    if (filter === "inactive" && u.active !== false) return false;
    if (filter === "manage" && !(u.manage_all || u.is_admin)) return false;
    return !q || `${u.full_name ?? ""} ${u.primary_email ?? ""}`.toLowerCase().includes(q);
  });

  return (
    <section className="card flush">
      {profileId !== null && (
        <ProfileDrawer
          userId={profileId}
          onClose={() => setProfileId(null)}
          onChanged={() => {
            void users.mutate();
            void refreshMembers();
          }}
        />
      )}
      {tokensOf && (
        <UserTokensDrawer userId={tokensOf.id} name={nameOf(tokensOf, `#${tokensOf.id}`)} onClose={() => setTokensOf(null)} />
      )}
      {adding && (
        <Drawer title="Thêm người dùng" width={460} onClose={() => setAdding(false)}>
          <form
            className="adm-form"
            onSubmit={(e) =>
              submit(
                e,
                async (f) => {
                  await api.post("/users", { full_name: f.get("full_name"), primary_email: f.get("primary_email") });
                  await users.mutate();
                },
                "Đã thêm người dùng.",
                () => setAdding(false),
              )
            }
          >
            <Message text={message?.startsWith("Lỗi") ? message : null} onClose={() => setMessage(null)} />
            <label className="adm-field">
              <span className="small muted">Họ tên</span>
              <input name="full_name" required autoFocus />
            </label>
            <label className="adm-field">
              <span className="small muted">Email công ty (trùng email GitLab)</span>
              <input name="primary_email" type="email" required />
            </label>
            <p className="muted small">
              Commit và MR được gắn với người qua email. Tài khoản Git/GitLab chưa nhận ra được gắn ở mục &ldquo;Tài khoản
              chưa gắn&rdquo;.
            </p>
            <div className="adm-form-foot">
              <button type="button" className="ghost" onClick={() => setAdding(false)}>
                Đóng
              </button>
              <button className="primary" type="submit" disabled={busy}>
                Thêm người
              </button>
            </div>
          </form>
        </Drawer>
      )}
      <div className="card-head">
        <div className="card-title" style={{ minWidth: 0 }}>
          <h2>Người dùng</h2>
          <div className="sub">Bấm bút để xem, sửa hồ sơ (tên, email, ảnh); chìa khoá để xem, thu hồi token Claude Code; thùng rác để xoá người khai báo sai.</div>
        </div>
        <div className="actions">
          <button className="primary sm" type="button" onClick={() => { setMessage(null); setAdding(true); }}>
            <Icon name="plus" size={14} />
            Thêm người
          </button>
        </div>
      </div>
      {!adding && message && (
        <div className="adm-pad">
          <Message text={message} onClose={() => setMessage(null)} />
        </div>
      )}
      <div className="toolbar adm-pad">
        <input
          className="compact"
          type="search"
          placeholder="Tìm tên hoặc email"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Tìm người dùng"
        />
        <Segmented<UserFilter>
          label="Lọc người dùng"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "active", label: `Đang hoạt động (${counts.active})` },
            { value: "manage", label: `Quản lý tổng (${counts.manage})` },
            { value: "inactive", label: `Đã tắt (${counts.inactive})` },
            { value: "all", label: `Tất cả (${counts.all})` },
          ]}
        />
      </div>
      {!users.data ? (
        <div className="adm-pad">
          <Loading error={users.error} />
          {users.error && (
            <button type="button" onClick={() => void users.mutate()} style={{ marginTop: 8 }}>
              Thử lại
            </button>
          )}
        </div>
      ) : all.length === 0 ? (
        <EmptyState icon="members" title="Chưa có người dùng nào">
          Thêm người bằng nút &ldquo;Thêm người&rdquo;, hoặc lấy từ GitLab ở mục &ldquo;Từ GitLab&rdquo;.
        </EmptyState>
      ) : shown.length === 0 ? (
        <p className="muted small adm-pad">Không có ai khớp bộ lọc.</p>
      ) : (
        <div className="table-wrap scroll-y">
          <table>
            <thead>
              <tr>
                <th>Người</th>
                <th>Email</th>
                <th>Quyền</th>
                <th>
                  <span className="visually-hidden">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.id} className={u.active === false ? "muted" : undefined}>
                  <td>
                    <span className="cell-main">
                      <Avatar name={u.full_name} email={u.primary_email} size="sm" src={u.avatar_url} />
                      <span className="who">{u.full_name ?? "—"}</span>
                    </span>
                  </td>
                  <td>
                    <code>{u.primary_email ?? "—"}</code>
                    {u.active === false ? " · đã tắt" : ""}
                  </td>
                  <td className="nowrap">
                    {u.is_admin ? (
                      <span className="muted small">Admin</span>
                    ) : (
                      <label
                        className="inline-check small"
                        title="Xem và quản lý mọi dự án như quản lý dự án, không cần cấp quyền từng dự án; không vào được Admin."
                      >
                        <input
                          type="checkbox"
                          checked={!!u.manage_all}
                          onChange={(e) => {
                            const on = e.target.checked;
                            const name = u.full_name ?? u.primary_email ?? `#${u.id}`;
                            const ok = window.confirm(
                              on
                                ? `Cho "${name}" quyền Quản lý tổng? Họ sẽ xem và quản lý MỌI dự án (yêu cầu, chạy lại review, sửa Tiền đề, gắn tác giả) mà không cần cấp quyền từng dự án.`
                                : `Bỏ quyền Quản lý tổng của "${name}"? Họ chỉ còn các quyền dự án được cấp riêng.`,
                            );
                            if (!ok) return;
                            void run(async () => {
                              await api.patch(`/users/${u.id}`, { manage_all: on });
                              await users.mutate();
                            }, on ? `${name} giờ quản lý mọi dự án.` : `${name} không còn quản lý mọi dự án.`);
                          }}
                        />
                        Quản lý tổng
                      </label>
                    )}
                  </td>
                  <td style={{ textAlign: "right" }} className="nowrap">
                    <IconButton
                      icon="edit"
                      label={`Xem / sửa hồ sơ ${u.full_name ?? u.primary_email ?? ""}`}
                      onClick={() => setProfileId(u.id)}
                    />
                    <IconButton
                      icon="key"
                      label={`Token Claude Code của ${u.full_name ?? u.primary_email ?? "người này"}`}
                      onClick={() => setTokensOf(u)}
                    />
                    <IconButton
                      icon="remove"
                      label={`Xoá ${u.full_name ?? u.primary_email ?? "người này"}`}
                      tone="danger"
                      onClick={() => {
                        const name = u.full_name ?? u.primary_email ?? `#${u.id}`;
                        if (
                          !window.confirm(
                            `Xoá người dùng "${name}"? Lịch sử review vẫn giữ (không còn gắn tác giả); ` +
                              "tài khoản Git/GitLab/Telegram của họ trở về chưa gắn người.",
                          )
                        )
                          return;
                        void run(async () => {
                          await api.delete(`/users/${u.id}`);
                          await users.mutate();
                          await refreshMembers();
                        }, `Đã xoá ${name}.`);
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {users.data && all.length > 0 && (
        <p className="adm-foot muted small">
          Hiển thị {shown.length.toLocaleString("vi-VN")} / {all.length.toLocaleString("vi-VN")} người.
        </p>
      )}
    </section>
  );
}

/**
 * Admin › Người & Team › Team: the teams on the left, the members of the selected one on the
 * right (docs/specs/05). Lead sees each member of their team; manager sees team aggregates.
 */
export function TeamsAdmin() {
  const teams = useApi<Team[]>("/teams");
  const users = useApi<Person[]>("/users");
  const [picked, setPicked] = useState<number | null>(null);
  // The first team is open by default so the right side is never empty when teams exist.
  const teamId = picked !== null && teams.data?.some((t) => t.id === picked) ? picked : (teams.data?.[0]?.id ?? null);
  const members = useApi<Member[]>(teamId ? `/teams/${teamId}/members` : null);
  const { message, setMessage, busy, run, submit } = useRun();
  const [creating, setCreating] = useState(false);
  const [adding, setAdding] = useState(false);
  const [showLeft, setShowLeft] = useState(false);
  const [teamQuery, setTeamQuery] = useState("");

  const teamName = (id: number | null) => teams.data?.find((t) => t.id === id)?.name ?? "—";
  const all = members.data ?? [];
  const current = all.filter((m) => !m.left_at);
  const left = all.length - current.length;
  const inTeam = new Set(current.map((m) => m.user_id));
  const shownMembers = showLeft ? all : current;
  const tq = teamQuery.trim().toLowerCase();
  const shownTeams = (teams.data ?? []).filter((t) => !tq || t.name.toLowerCase().includes(tq));

  const pick = (id: number) => {
    setPicked(id);
    setAdding(false);
  };

  return (
    <>
      {creating && (
        <Drawer title="Tạo team" width={460} onClose={() => setCreating(false)}>
          <form
            className="adm-form"
            onSubmit={(e) =>
              submit(
                e,
                async (f) => {
                  const parent = f.get("parent_id");
                  await api.post("/teams", { name: f.get("name"), parent_id: parent ? Number(parent) : null });
                  await teams.mutate();
                },
                "Đã tạo team.",
                () => setCreating(false),
              )
            }
          >
            <Message text={message?.startsWith("Lỗi") ? message : null} onClose={() => setMessage(null)} />
            <label className="adm-field">
              <span className="small muted">Tên team</span>
              <input name="name" placeholder="Ví dụ Backend" required autoFocus />
            </label>
            <label className="adm-field">
              <span className="small muted">Team cha (manager của team cha xem được số liệu team này)</span>
              <select name="parent_id" defaultValue="">
                <option value="">Không có team cha</option>
                {(teams.data ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="adm-form-foot">
              <button type="button" className="ghost" onClick={() => setCreating(false)}>
                Đóng
              </button>
              <button className="primary" type="submit" disabled={busy}>
                Tạo team
              </button>
            </div>
          </form>
        </Drawer>
      )}
      {!creating && <Message text={message} onClose={() => setMessage(null)} />}
      <div className="master-detail">
        <section className="card flush master">
          <div className="card-head">
            <div className="card-title" style={{ minWidth: 0 }}>
              <h2>Team{teams.data ? ` (${teams.data.length})` : ""}</h2>
            </div>
            <div className="actions">
              <button type="button" className="sm" onClick={() => { setMessage(null); setCreating(true); }}>
                <Icon name="plus" size={14} />
                Tạo team
              </button>
            </div>
          </div>
          {(teams.data?.length ?? 0) > 8 && (
            <div className="adm-pad" style={{ paddingBottom: 8 }}>
              <input
                className="compact"
                type="search"
                placeholder="Tìm team"
                value={teamQuery}
                onChange={(e) => setTeamQuery(e.target.value)}
                aria-label="Tìm team"
                style={{ width: "100%" }}
              />
            </div>
          )}
          {!teams.data ? (
            <div className="adm-pad">
              <Loading error={teams.error} />
            </div>
          ) : teams.data.length === 0 ? (
            <EmptyState icon="members" title="Chưa có team nào">
              Bấm &ldquo;Tạo team&rdquo; để bắt đầu.
            </EmptyState>
          ) : shownTeams.length === 0 ? (
            <p className="muted small adm-pad">Không có team nào khớp.</p>
          ) : (
            <ul className="pick-list" aria-label="Chọn team">
              {shownTeams.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    className={t.id === teamId ? "pick-item active" : "pick-item"}
                    aria-current={t.id === teamId ? "true" : undefined}
                    onClick={() => pick(t.id)}
                  >
                    <span className="pick-name">{t.name}</span>
                    {t.parent_id !== null && <span className="pick-sub">thuộc {teamName(t.parent_id)}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card flush detail">
          {teamId === null ? (
            <EmptyState icon="members" title="Chọn một team">
              Thành viên của team hiện ở đây.
            </EmptyState>
          ) : (
            <>
              <div className="card-head">
                <div className="card-title" style={{ minWidth: 0 }}>
                  <h2>
                    Thành viên của {teamName(teamId)}
                    {members.data ? ` (${current.length})` : ""}
                  </h2>
                  <div className="sub">
                    Lead xem được dữ liệu từng thành viên trong team mình; manager xem số liệu tổng hợp của team và các team
                    con.
                  </div>
                </div>
                <div className="actions">
                  {left > 0 && (
                    <label className="inline-check small">
                      <input type="checkbox" checked={showLeft} onChange={(e) => setShowLeft(e.target.checked)} />
                      Hiện người đã rời ({left})
                    </label>
                  )}
                  <button
                    className="primary sm"
                    type="button"
                    aria-expanded={adding}
                    aria-controls="team-add-form"
                    onClick={() => setAdding((v) => !v)}
                  >
                    <Icon name={adding ? "close" : "plus"} size={14} />
                    {adding ? "Đóng" : "Thêm vào team"}
                  </button>
                </div>
              </div>
              {adding && (
                <form
                  id="team-add-form"
                  className="toolbar adm-pad adm-inline-form"
                  onSubmit={(e) => {
                    const f = new FormData(e.currentTarget);
                    const who = users.data?.find((u) => String(u.id) === f.get("user_id"));
                    const label = ROLES.find((r) => r.value === f.get("role"))?.label ?? String(f.get("role"));
                    if (
                      !window.confirm(
                        `Thêm ${who?.full_name ?? who?.primary_email ?? "người này"} vào ${teamName(teamId)} với vai trò "${label}"?`,
                      )
                    ) {
                      e.preventDefault();
                      return;
                    }
                    submit(
                      e,
                      async (f) => {
                        await api.put(`/teams/${teamId}/members`, {
                          user_id: Number(f.get("user_id")),
                          role: f.get("role"),
                          joined_at: f.get("joined_at") || today(),
                        });
                        await members.mutate();
                      },
                      "Đã thêm vào team.",
                    );
                  }}
                >
                  <select name="user_id" required defaultValue="" aria-label="Người">
                    <option value="" disabled>
                      Chọn người…
                    </option>
                    {(users.data ?? [])
                      .filter((u) => u.active !== false && !inTeam.has(u.id))
                      .map((u) => (
                        <option key={u.id} value={u.id}>
                          {u.full_name ?? u.primary_email}
                        </option>
                      ))}
                  </select>
                  <select name="role" defaultValue="member" aria-label="Vai trò">
                    {ROLES.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                  <input name="joined_at" type="date" defaultValue={today()} aria-label="Ngày vào team" />
                  <button type="submit" disabled={busy}>
                    Thêm
                  </button>
                </form>
              )}
              {!members.data ? (
                <div className="adm-pad">
                  <Loading error={members.error} />
                </div>
              ) : shownMembers.length === 0 ? (
                <EmptyState icon="members" title={all.length ? "Không còn ai trong team" : "Team chưa có thành viên"}>
                  Bấm &ldquo;Thêm vào team&rdquo; để thêm người.
                </EmptyState>
              ) : (
                <div className="table-wrap scroll-y">
                  <table>
                    <thead>
                      <tr>
                        <th>Người</th>
                        <th>Vai trò</th>
                        <th>Từ ngày</th>
                        <th>Đến ngày</th>
                        <th>
                          <span className="visually-hidden">Thao tác</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownMembers.map((m) => (
                        <tr key={`${m.user_id}-${m.joined_at}`} className={m.left_at ? "muted" : undefined}>
                          <td>{m.full_name ?? m.primary_email}</td>
                          <td>
                            {m.left_at ? (
                              (ROLES.find((r) => r.value === m.role)?.label ?? m.role ?? "Thành viên")
                            ) : (
                              <select
                                className="compact"
                                value={m.role ?? "member"}
                                aria-label={`Vai trò của ${nameOf(m)}`}
                                onChange={(e) => {
                                  const role = e.target.value;
                                  const label = ROLES.find((r) => r.value === role)?.label ?? role;
                                  const before = ROLES.find((r) => r.value === (m.role ?? "member"))?.label ?? m.role;
                                  if (
                                    !window.confirm(
                                      `Đổi vai trò của ${m.full_name ?? m.primary_email} trong ${teamName(teamId)} từ "${before}" sang "${label}"? Vai trò quyết định họ xem được điểm và review của ai.`,
                                    )
                                  )
                                    return;
                                  void run(async () => {
                                    await api.put(`/teams/${teamId}/members`, {
                                      user_id: m.user_id,
                                      role,
                                      joined_at: m.joined_at,
                                    });
                                    await members.mutate();
                                  }, `Đã đổi vai trò của ${m.full_name ?? m.primary_email}.`);
                                }}
                              >
                                {ROLES.map((r) => (
                                  <option key={r.value} value={r.value}>
                                    {r.label}
                                  </option>
                                ))}
                              </select>
                            )}
                          </td>
                          <td className="nowrap">{m.joined_at}</td>
                          <td className="nowrap">{m.left_at ?? "—"}</td>
                          <td className="nowrap" style={{ textAlign: "right" }}>
                            {!m.left_at && (
                              <IconButton
                                icon="leave"
                                label="Rời team (giữ lịch sử)"
                                onClick={() => {
                                  if (
                                    !window.confirm(
                                      `Cho ${m.full_name ?? m.primary_email} rời ${teamName(teamId)} từ hôm nay? Họ mất các quyền theo vai trò trong team; dữ liệu cũ vẫn giữ.`,
                                    )
                                  )
                                    return;
                                  void run(async () => {
                                    await api.put(`/teams/${teamId}/members`, {
                                      user_id: m.user_id,
                                      role: m.role ?? "member",
                                      joined_at: m.joined_at,
                                      left_at: today(),
                                    });
                                    await members.mutate();
                                  }, `${m.full_name ?? m.primary_email} đã rời team (dữ liệu cũ vẫn giữ).`);
                                }}
                              />
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="adm-foot muted small">
                &ldquo;Rời team&rdquo; ghi ngày rời thay vì xoá, để chỉ số các kỳ trước vẫn tính đúng người.
              </p>
            </>
          )}
        </section>
      </div>
    </>
  );
}
