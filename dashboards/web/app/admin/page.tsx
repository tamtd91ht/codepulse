"use client";

import { useState } from "react";

import { Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Account, LlmCosts, Person, Project } from "@/lib/types";

type Flag = "review_enabled" | "post_mr_comment" | "allow_external_llm";
const FLAGS: { key: Flag; label: string }[] = [
  { key: "review_enabled", label: "AI review" },
  { key: "post_mr_comment", label: "Comment lên MR" },
  { key: "allow_external_llm", label: "Cho gửi code ra LLM" },
];

/** docs/specs/10 "Admin": projects, unmapped accounts, rubric/weights, LLM cost per month. */
export default function AdminPage() {
  const projects = useApi<Project[]>("/projects");
  const unmapped = useApi<Account[]>("/identity/unmapped");
  const users = useApi<Person[]>("/users");
  const costs = useApi<LlmCosts>("/admin/llm-costs");
  const [message, setMessage] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      setMessage(done);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <>
      <h1>Admin</h1>
      {message && <p className="muted">{message}</p>}

      <div className="card">
        <h2>Projects</h2>
        {!projects.data ? (
          <Loading error={projects.error} />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Project</th>
                  {FLAGS.map((f) => <th key={f.key}>{f.label}</th>)}
                  <th>Sync gần nhất</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {projects.data.map((p) => (
                  <tr key={p.id}>
                    <td>{p.path_with_namespace}</td>
                    {FLAGS.map((f) => (
                      <td key={f.key}>
                        <input
                          type="checkbox"
                          checked={!!p[f.key]}
                          onChange={(e) =>
                            run(async () => {
                              await api.patch(`/projects/${p.id}`, { [f.key]: e.target.checked });
                              await projects.mutate();
                            }, `Đã cập nhật ${p.path_with_namespace}.`)
                          }
                        />
                      </td>
                    ))}
                    <td className="muted">{date(p.last_synced_at)}</td>
                    <td>
                      <button onClick={() => run(() => api.post(`/projects/${p.id}/sync`), "Đã đưa vào hàng đợi sync.")}>
                        Sync ngay
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted">Thêm project / kết nối GitLab: <code>codepulse projects add &lt;id&gt;</code> hoặc API <code>POST /api/v1/projects</code>.</p>
      </div>

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
    </>
  );
}
