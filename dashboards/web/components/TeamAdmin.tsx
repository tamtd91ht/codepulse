"use client";

import { type FormEvent, useState } from "react";

import { Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { Member, Person, Team } from "@/lib/types";

const ROLES: { value: string; label: string }[] = [
  { value: "member", label: "Thành viên" },
  { value: "lead", label: "Lead" },
  { value: "manager", label: "Manager" },
];

const today = () => new Date().toISOString().slice(0, 10);

/** Admin card: teams, people and who belongs where (docs/specs/05), instead of the CSV import. */
export function TeamAdmin() {
  const teams = useApi<Team[]>("/teams");
  const users = useApi<Person[]>("/users");
  const [teamId, setTeamId] = useState<number | null>(null);
  const members = useApi<Member[]>(teamId ? `/teams/${teamId}/members` : null);
  const [message, setMessage] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      setMessage(done);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }

  function submit(e: FormEvent<HTMLFormElement>, action: (f: FormData) => Promise<unknown>, done: string) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    run(async () => {
      await action(fields);
      form.reset();
    }, done);
  }

  const teamName = (id: number | null) => teams.data?.find((t) => t.id === id)?.name ?? "—";
  const current = (members.data ?? []).filter((m) => !m.left_at);
  const inTeam = new Set(current.map((m) => m.user_id));

  return (
    <div className="card">
      <h2>Team &amp; thành viên</h2>
      <p className="muted">
        Người dùng được gắn với commit/MR qua email. Lead xem được dữ liệu từng thành viên trong team mình; manager xem
        số liệu tổng hợp của team và các team con.
      </p>
      {message && <p className="muted">{message}</p>}

      <div className="grid two">
        <div>
          <h3>Team</h3>
          {!teams.data ? (
            <Loading error={teams.error} />
          ) : teams.data.length === 0 ? (
            <p className="muted">Chưa có team nào.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Tên</th>
                    <th>Team cha</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {teams.data.map((t) => (
                    <tr key={t.id}>
                      <td>{t.id === teamId ? <strong>{t.name}</strong> : t.name}</td>
                      <td className="muted">{teamName(t.parent_id)}</td>
                      <td>
                        <button onClick={() => setTeamId(t.id)} disabled={t.id === teamId}>
                          Thành viên
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
                  const parent = f.get("parent_id");
                  await api.post("/teams", { name: f.get("name"), parent_id: parent ? Number(parent) : null });
                  await teams.mutate();
                },
                "Đã tạo team.",
              )
            }
          >
            <input name="name" placeholder="Tên team, ví dụ Backend" required />
            <select name="parent_id" defaultValue="">
              <option value="">Không có team cha</option>
              {(teams.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <button className="primary" type="submit">
              Tạo team
            </button>
          </form>
        </div>

        <div>
          <h3>Người dùng</h3>
          <form
            className="toolbar"
            onSubmit={(e) =>
              submit(
                e,
                async (f) => {
                  await api.post("/users", { full_name: f.get("full_name"), primary_email: f.get("primary_email") });
                  await users.mutate();
                },
                "Đã thêm người dùng.",
              )
            }
          >
            <input name="full_name" placeholder="Họ tên" required />
            <input name="primary_email" type="email" placeholder="Email công ty (trùng email GitLab)" required />
            <button className="primary" type="submit">
              Thêm người
            </button>
          </form>
          <p className="muted">
            {users.data ? `${users.data.filter((u) => u.active !== false).length} người đang hoạt động.` : ""} Tài khoản
            Git/GitLab chưa nhận ra được gắn với người ở mục &ldquo;Tài khoản chưa gắn người&rdquo;.
          </p>
        </div>
      </div>

      {teamId && (
        <>
          <h3>Thành viên của {teamName(teamId)}</h3>
          {!members.data ? (
            <Loading error={members.error} />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Người</th>
                    <th>Vai trò</th>
                    <th>Từ ngày</th>
                    <th>Đến ngày</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {members.data.map((m) => (
                    <tr key={`${m.user_id}-${m.joined_at}`} className={m.left_at ? "muted" : undefined}>
                      <td>{m.full_name ?? m.primary_email}</td>
                      <td>
                        {m.left_at ? (
                          (ROLES.find((r) => r.value === m.role)?.label ?? m.role ?? "Thành viên")
                        ) : (
                          <select
                            value={m.role ?? "member"}
                            aria-label="Vai trò"
                            onChange={(e) =>
                              run(async () => {
                                await api.put(`/teams/${teamId}/members`, {
                                  user_id: m.user_id,
                                  role: e.target.value,
                                  joined_at: m.joined_at,
                                });
                                await members.mutate();
                              }, `Đã đổi vai trò của ${m.full_name ?? m.primary_email}.`)
                            }
                          >
                            {ROLES.map((r) => (
                              <option key={r.value} value={r.value}>
                                {r.label}
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td>{m.joined_at}</td>
                      <td>{m.left_at ?? "—"}</td>
                      <td>
                        {!m.left_at && (
                          <button
                            onClick={() =>
                              run(async () => {
                                await api.put(`/teams/${teamId}/members`, {
                                  user_id: m.user_id,
                                  role: m.role ?? "member",
                                  joined_at: m.joined_at,
                                  left_at: today(),
                                });
                                await members.mutate();
                              }, `${m.full_name ?? m.primary_email} đã rời team (dữ liệu cũ vẫn giữ).`)
                            }
                          >
                            Rời team
                          </button>
                        )}
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
                  await api.put(`/teams/${teamId}/members`, {
                    user_id: Number(f.get("user_id")),
                    role: f.get("role"),
                    joined_at: f.get("joined_at") || today(),
                  });
                  await members.mutate();
                },
                "Đã thêm vào team.",
              )
            }
          >
            <select name="user_id" required defaultValue="">
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
            <select name="role" defaultValue="member">
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
            <input name="joined_at" type="date" defaultValue={today()} aria-label="Ngày vào team" />
            <button className="primary" type="submit">
              Thêm vào team
            </button>
          </form>
          <p className="muted">
            &ldquo;Rời team&rdquo; ghi ngày rời thay vì xoá, để chỉ số các kỳ trước vẫn tính đúng người.
          </p>
        </>
      )}
    </div>
  );
}
