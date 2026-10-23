"use client";

import { type FormEvent, useState } from "react";

import { Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { date } from "@/lib/format";
import type { Account, Connection, GroupImport, LlmCosts, Person, Project } from "@/lib/types";

type Flag = "review_enabled" | "post_mr_comment" | "allow_external_llm";
const FLAGS: { key: Flag; label: string }[] = [
  { key: "review_enabled", label: "AI review" },
  { key: "post_mr_comment", label: "Comment lên MR" },
  { key: "allow_external_llm", label: "Cho gửi code ra LLM" },
];

/** docs/specs/10 "Admin": projects, connections, unmapped accounts, rubric/weights, LLM cost per month. */
export default function AdminPage() {
  const connections = useApi<Connection[]>("/connections");
  const projects = useApi<Project[]>("/projects");
  const unmapped = useApi<Account[]>("/identity/unmapped");
  const users = useApi<Person[]>("/users");
  const costs = useApi<LlmCosts>("/admin/llm-costs");
  const [message, setMessage] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

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

  async function registerWebhook(p: Project): Promise<string> {
    const hook = await api.post<{ url: string; created: boolean }>(`/projects/${p.id}/webhook`);
    return hook.created ? `Đã đăng ký webhook ${hook.url}.` : `Webhook đã có sẵn: ${hook.url}.`;
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
                      </button>{" "}
                      <button onClick={() => run(() => registerWebhook(p))}>Webhook</button>
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
                const project = await api.post<Project>("/projects", {
                  connection_id: Number(f.get("connection_id")),
                  external_id: Number(f.get("external_id")),
                });
                await projects.mutate();
                await api.post(`/projects/${project.id}/sync`);
                let hook = "";
                if (f.get("webhook")) {
                  try {
                    hook = ` ${await registerWebhook(project)}`;
                  } catch (e) {
                    hook = ` Webhook lỗi: ${e instanceof ApiError ? e.message : String(e)}; bấm nút Webhook để thử lại.`;
                  }
                }
                return `Đã thêm ${project.path_with_namespace} và đưa vào hàng đợi sync.${hook}`;
              },
            )
          }
        >
          <select name="connection_id" required defaultValue="">
            <option value="" disabled>Kết nối…</option>
            {(connections.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <input name="external_id" type="number" min={1} placeholder="GitLab project ID" required />
          <label>
            <input name="webhook" type="checkbox" defaultChecked /> Đăng ký webhook
          </label>
          <button className="primary" type="submit">Thêm project</button>
        </form>
        <p className="muted">Project ID nằm ở trang project trên GitLab (Settings → General, hoặc dưới tên project).</p>
        <form
          className="toolbar"
          onSubmit={(e) =>
            submit(e, async (f) => {
              setMessage("Đang lấy danh sách project của group…");
              setImporting(true);
              const result = await api
                .post<GroupImport>("/projects/import-group", {
                  connection_id: Number(f.get("connection_id")),
                  group: f.get("group"),
                  register_webhooks: !!f.get("webhook"),
                })
                .finally(() => {
                  setImporting(false);
                  void projects.mutate(); // show rows added before an error or timeout
                });
              if (result.added.length === 0) return `Không có project mới (${result.skipped} project đã có).`;
              const notQueued = result.not_queued.length
                ? ` Chưa đưa vào hàng đợi: ${result.not_queued.join(", ")} – sẽ được sync ở lần định kỳ; bấm Webhook ở dòng đó nếu cần.`
                : "";
              return (
                `Đã thêm ${result.added.length} project (bỏ qua ${result.skipped} project đã có). ` +
                `Webhook và sync chạy nền trong worker.${notQueued}`
              );
            })
          }
        >
          <select name="connection_id" required defaultValue="">
            <option value="" disabled>Kết nối…</option>
            {(connections.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <input name="group" placeholder="Group ID hoặc đường dẫn, ví dụ vihat/backend" required />
          <label>
            <input name="webhook" type="checkbox" defaultChecked /> Đăng ký webhook
          </label>
          <button className="primary" type="submit" disabled={importing}>
            {importing ? "Đang thêm…" : "Thêm cả group"}
          </button>
        </form>
        <p className="muted">
          Thêm một lần mọi project đang hoạt động trong group, kể cả subgroup; project đã có được bỏ qua. Project tạo
          sau này trong group cần thêm lại bằng nút này.
        </p>
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
