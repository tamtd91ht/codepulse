"use client";

import { type FormEvent, useState } from "react";

import { LlmCheck } from "@/components/LlmCheck";
import { RepoAdmin } from "@/components/RepoAdmin";
import { TeamAdmin } from "@/components/TeamAdmin";
import { Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { Account, Connection, LlmCosts, Person } from "@/lib/types";

/** docs/specs/10 "Admin": connections, dự án/repos, unmapped accounts, teams, LLM cost and check. */
export default function AdminPage() {
  const connections = useApi<Connection[]>("/connections");
  const unmapped = useApi<Account[]>("/identity/unmapped");
  const users = useApi<Person[]>("/users");
  const costs = useApi<LlmCosts>("/admin/llm-costs");
  const [message, setMessage] = useState<string | null>(null);

  /** Runs an action and shows `done`, or the action's own message when it returns one. */
  async function run(action: () => Promise<unknown>, done?: string) {
    try {
      const result = await action();
      setMessage(done ?? String(result));
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }

  /** Reads the form's named fields, then clears it once the action succeeds. */
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

  return (
    <>
      <h1>Admin</h1>
      {message && <p className="muted">{message}</p>}

      <div className="card">
        <h2>Kết nối GitLab</h2>
        {!connections.data ? (
          <Loading error={connections.error} />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tên</th>
                  <th>URL</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {connections.data.map((c) => (
                  <tr key={c.id}>
                    <td>{c.name}</td>
                    <td className="muted">{c.base_url}</td>
                    <td>
                      <form
                        className="toolbar"
                        onSubmit={(e) =>
                          submit(
                            e,
                            (f) => api.patch(`/connections/${c.id}`, { token: f.get("token") }),
                            `Đã đổi token của ${c.name}; thu hồi token cũ trên GitLab.`,
                          )
                        }
                      >
                        <input name="token" type="password" placeholder="Token mới" required autoComplete="off" />
                        <button type="submit">Đổi token</button>
                      </form>
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
          <input name="token" type="password" placeholder="Token bot (read_api, + api để comment)" required autoComplete="off" />
          <button className="primary" type="submit">Thêm kết nối</button>
        </form>
        <p className="muted">Token được mã hoá trong DB và không bao giờ hiển thị lại.</p>
      </div>

      <RepoAdmin connections={connections.data ?? []} />

      <div className="grid two">
        <div className="card">
          <h2>Tài khoản chưa gắn người</h2>
          {!unmapped.data || !users.data ? (
            <Loading error={unmapped.error ?? users.error} />
          ) : unmapped.data.length === 0 ? (
            <p className="muted">Mọi tài khoản đã được gắn.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <tbody>
                  {unmapped.data.map((a) => (
                    <tr key={a.id}>
                      <td className="muted">{a.kind}</td>
                      <td>{a.external_key}{a.display_name ? ` (${a.display_name})` : ""}</td>
                      <td>
                        <select
                          defaultValue=""
                          onChange={(e) =>
                            e.target.value &&
                            run(async () => {
                              await api.put(`/accounts/${a.id}/user`, { user_id: Number(e.target.value) });
                              await unmapped.mutate();
                            }, `Đã gắn ${a.external_key}.`)
                          }
                        >
                          <option value="">Gắn với…</option>
                          {(users.data ?? []).filter((u) => u.active !== false).map((u) => (
                            <option key={u.id} value={u.id}>{u.full_name ?? u.primary_email}</option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Chi phí LLM theo tháng</h2>
          {!costs.data ? (
            <Loading error={costs.error} />
          ) : (
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
                {costs.data.months.map((m) => (
                  <tr key={m.month}>
                    <td>{m.month}</td>
                    <td className="num">{m.reviews}</td>
                    <td className="num">${m.cost_usd.toFixed(2)}</td>
                    <td className="num">
                      {costs.data!.budget_usd ? `${((100 * m.cost_usd) / costs.data!.budget_usd).toFixed(0)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="muted">
            Ngân sách tháng: ${costs.data?.budget_usd ?? "—"} (MONTHLY_LLM_BUDGET_USD). Rubric v1; trọng số severity đổi qua
            SCORING_WEIGHTS rồi chạy <code>codepulse metrics recompute</code>.
          </p>
        </div>
      </div>

      <TeamAdmin />

      <LlmCheck />
    </>
  );
}
