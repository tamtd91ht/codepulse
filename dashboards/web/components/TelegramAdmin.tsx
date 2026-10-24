"use client";

import { type FormEvent, Fragment, useState } from "react";

import { IconButton } from "@/components/icons";
import { Alert, Card, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import type { BusinessProject, Project, TelegramBot, TelegramRequest } from "@/lib/types";

const COMMANDS: [string, string][] = [
  ["/review type=mr id=412 repo=global-api", "review MR !412; repo viết tắt được (cloud-saas-global-api)"],
  ["/review type=commit id=<sha> [repo=…]", "review một commit"],
  ["/review type=commit user=tamtd [range=7d]", "commit gần nhất (hoặc trong 7 ngày) của một người, user viết tắt được"],
  ["/review type=mr user=an", "MR gần nhất của một người (bỏ user = của bạn)"],
  ["/review open [repo]", "MR đang mở chưa được review (lead)"],
  ["/review mr|commit|last|lastmr|today …", "dạng cũ, vẫn dùng được"],
  ["/status · /critical [repo] · /score [người] [week|month]", "xem nhanh"],
  ["/repos · /sync [repo] · /whoami · /link <mã> · /unlink", "tiện ích"],
];

/** Comma/space separated Telegram chat ids; throws on anything that is not an integer. */
function parseChatIds(text: string): number[] | null {
  const words = text
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const bad = words.filter((w) => !/^-?\d+$/.test(w));
  if (bad.length) throw new ApiError(0, `Chat id không hợp lệ: ${bad.join(", ")} (chỉ nhận số nguyên).`);
  if (words.length > 50) throw new ApiError(0, "Tối đa 50 chat id.");
  return words.length ? words.map(Number) : null;
}

/** Vietnamese wording for the statuses this screen can get back. */
function describe(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return "Dự án (hoặc repo) này đã có bot.";
    if (e.status === 422) return `Telegram từ chối token hoặc dữ liệu không hợp lệ: ${e.message}`;
    return e.message;
  }
  return String(e);
}

/** Admin tab: one review bot per dự án (or per repo), polled by the scheduler. */
export function TelegramAdmin() {
  const bots = useApi<TelegramBot[]>("/telegram/bots");
  const projects = useApi<BusinessProject[]>("/business-projects");
  const repos = useApi<Project[]>("/projects");
  const [message, setMessage] = useState<string | null>(null);
  const [tone, setTone] = useState<"success" | "error" | "warning" | undefined>(undefined);
  const [busy, setBusy] = useState<number | null>(null);
  const [rotating, setRotating] = useState<number | null>(null);
  const [showing, setShowing] = useState<number | null>(null);
  const [formProject, setFormProject] = useState("");
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; error: string | null }>>({});

  async function run(action: () => Promise<unknown>, done: string, id?: number) {
    setBusy(id ?? -1);
    try {
      await action();
      setTone("success");
      setMessage(done);
    } catch (e) {
      setTone("error");
      setMessage(`Lỗi: ${describe(e)}`);
    } finally {
      setBusy(null);
    }
  }

  function submit(e: FormEvent<HTMLFormElement>, action: (f: FormData) => Promise<unknown>, done: string) {
    e.preventDefault();
    const form = e.currentTarget;
    const fields = new FormData(form);
    void run(async () => {
      await action(fields);
      form.reset();
    }, done);
  }

  const forget = (id: number) => {
    setRotating((r) => (r === id ? null : r));
    setShowing((r) => (r === id ? null : r));
    setTestResult((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const projectName = (id: number) => projects.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  const repoPath = (id: number | null) => (id === null ? null : (repos.data?.find((r) => r.id === id)?.path_with_namespace ?? `#${id}`));
  const reposOf = (bp: string) => (repos.data ?? []).filter((r) => r.tracked && String(r.business_project_id) === bp);

  return (
    <>
      <Message text={message} tone={tone} onClose={() => setMessage(null)} />

      <Card
        title="Bot Telegram theo dự án"
        subtitle="Mỗi dự án (hoặc một repo trong dự án) có một bot; thành viên gửi lệnh review, hệ thống tự poll và trả kết quả vào chat."
        flush
      >
        {!bots.data ? (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={bots.error} rows={3} />
          </div>
        ) : bots.data.length === 0 ? (
          <EmptyState icon="pulse" title="Chưa có bot nào">
            Tạo bot với @BotFather trên Telegram, dán token vào biểu mẫu bên dưới.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Bot</th>
                  <th>Phạm vi</th>
                  <th>Trạng thái</th>
                  <th>Lần poll gần nhất</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {bots.data.map((b) => (
                  <Fragment key={b.id}>
                    <tr>
                      <td>
                        <strong>{b.name}</strong>
                        <div className="small muted">{b.bot_username ? `@${b.bot_username}` : "—"}</div>
                      </td>
                      <td style={{ whiteSpace: "normal" }}>
                        {projectName(b.business_project_id)}
                        {b.project_id !== null && (
                          <div className="small muted">
                            <code>{repoPath(b.project_id)}</code>
                          </div>
                        )}
                        {b.allowed_chat_ids && (
                          <div className="small muted">chat: {b.allowed_chat_ids.join(", ")}</div>
                        )}
                      </td>
                      <td>
                        {b.enabled ? <Pill tone="good">Đang chạy</Pill> : <Pill>Tạm dừng</Pill>}
                        {b.last_error && (
                          <div className="small error" style={{ whiteSpace: "normal", maxWidth: 320 }} title={b.last_error}>
                            {b.last_error.slice(0, 120)}
                          </div>
                        )}
                        {testResult[b.id] && (
                          <div className={`small ${testResult[b.id].ok ? "good" : "error"}`}>
                            {testResult[b.id].ok ? "getMe OK" : testResult[b.id].error}
                          </div>
                        )}
                      </td>
                      <td className="muted" title={b.last_polled_at ? dateTime(b.last_polled_at) : undefined}>
                        {b.last_polled_at ? relative(b.last_polled_at) : "chưa poll lần nào"}
                      </td>
                      <td>
                        <div className="actions">
                          <IconButton
                            icon="activity"
                            label="Kiểm tra token (getMe)"
                            disabled={busy !== null}
                            onClick={() =>
                              run(
                                async () => {
                                  const r = await api.post<{ ok: boolean; error: string | null }>(`/telegram/bots/${b.id}/test`);
                                  setTestResult((prev) => ({ ...prev, [b.id]: r }));
                                  await bots.mutate();
                                  if (!r.ok) throw new ApiError(0, r.error ?? "getMe thất bại");
                                },
                                `Bot ${b.name} trả lời getMe.`,
                                b.id,
                              )
                            }
                          />
                          <IconButton
                            icon={b.enabled ? "untrack" : "track"}
                            label={b.enabled ? "Tạm dừng bot" : "Bật bot"}
                            disabled={busy !== null}
                            onClick={() =>
                              run(
                                async () => {
                                  await api.patch(`/telegram/bots/${b.id}`, { enabled: !b.enabled });
                                  await bots.mutate();
                                },
                                b.enabled ? `Đã tạm dừng ${b.name}.` : `Đã bật ${b.name}.`,
                                b.id,
                              )
                            }
                          />
                          <IconButton
                            icon="edit"
                            label="Đổi token"
                            disabled={busy !== null}
                            aria-expanded={rotating === b.id}
                            onClick={() => setRotating(rotating === b.id ? null : b.id)}
                          />
                          <IconButton
                            icon="review"
                            label="Yêu cầu gần đây"
                            aria-expanded={showing === b.id}
                            onClick={() => setShowing(showing === b.id ? null : b.id)}
                          />
                          <IconButton
                            icon="remove"
                            label="Xoá bot"
                            tone="danger"
                            disabled={busy !== null}
                            onClick={() =>
                              window.confirm(`Xoá bot "${b.name}"? Lịch sử yêu cầu của bot cũng bị xoá; review đã chạy vẫn giữ.`) &&
                              void run(
                                async () => {
                                  await api.delete(`/telegram/bots/${b.id}`);
                                  forget(b.id);
                                  await bots.mutate();
                                },
                                `Đã xoá bot ${b.name}.`,
                                b.id,
                              )
                            }
                          />
                        </div>
                      </td>
                    </tr>
                    {rotating === b.id && (
                      <tr>
                        <td colSpan={5}>
                          <form
                            className="toolbar"
                            style={{ margin: 0 }}
                            onSubmit={(e) =>
                              submit(
                                e,
                                async (f) => {
                                  await api.patch(`/telegram/bots/${b.id}`, { token: String(f.get("token") ?? "").trim() });
                                  forget(b.id);
                                  await bots.mutate();
                                },
                                `Đã đổi token của ${b.name}; thu hồi token cũ với @BotFather.`,
                              )
                            }
                          >
                            <input
                              name="token"
                              type="password"
                              placeholder="Token mới từ @BotFather"
                              required
                              autoComplete="new-password"
                              style={{ flex: "1 1 200px", minWidth: 0 }}
                            />
                            <button type="submit" disabled={busy !== null}>
                              Đổi token
                            </button>
                            <IconButton icon="close" label="Huỷ" onClick={() => setRotating(null)} />
                          </form>
                        </td>
                      </tr>
                    )}
                    {showing === b.id && (
                      <tr>
                        <td colSpan={5} style={{ whiteSpace: "normal" }}>
                          <Requests botId={b.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid two">
        <Card title="Thêm bot" subtitle="Token được kiểm tra với Telegram (getMe) rồi mã hoá trong DB; không hiển thị lại.">
          <form
            style={{ display: "grid", gap: 10 }}
            onSubmit={(e) =>
              submit(
                e,
                async (f) => {
                  const allowed_chat_ids = parseChatIds(String(f.get("allowed_chat_ids") ?? ""));
                  await api.post("/telegram/bots", {
                    name: String(f.get("name") ?? "").trim(),
                    token: String(f.get("token") ?? "").trim(),
                    business_project_id: Number(f.get("business_project_id")),
                    project_id: f.get("project_id") ? Number(f.get("project_id")) : null,
                    allowed_chat_ids,
                  });
                  setFormProject("");
                  await bots.mutate();
                },
                "Đã thêm bot. Người dùng liên kết Telegram ở trang Của tôi, rồi gửi /help cho bot.",
              )
            }
          >
            <label style={{ display: "grid", gap: 4 }}>
              <span className="small muted">Tên hiển thị</span>
              <input name="name" placeholder="Ví dụ: OmiCRM review bot" required maxLength={100} />
            </label>
            <label style={{ display: "grid", gap: 4 }}>
              <span className="small muted">Token từ @BotFather</span>
              <input
                name="token"
                type="password"
                placeholder="123456789:AAH…"
                required
                autoComplete="new-password"
                pattern="^\s*\d+:[A-Za-z0-9_-]{20,}\s*$"
                title="Dạng 123456789:AAH… (token từ @BotFather)"
              />
            </label>
            <div className="grid two" style={{ gap: 10 }}>
              <label style={{ display: "grid", gap: 4 }}>
                <span className="small muted">Dự án</span>
                <select name="business_project_id" required value={formProject} onChange={(e) => setFormProject(e.target.value)}>
                  <option value="" disabled>
                    {projects.data ? "Chọn dự án…" : "Đang tải…"}
                  </option>
                  {(projects.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.repos} repo)
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ display: "grid", gap: 4 }}>
                <span className="small muted">Chỉ một repo (tuỳ chọn)</span>
                <select key={formProject} name="project_id" defaultValue="" disabled={!formProject}>
                  <option value="">Cả dự án</option>
                  {reposOf(formProject).map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.path_with_namespace}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label style={{ display: "grid", gap: 4 }}>
              <span className="small muted">Chỉ trả lời trong các chat id (tuỳ chọn, cách nhau bằng dấu phẩy)</span>
              <input
                name="allowed_chat_ids"
                placeholder="-1001234567890, 987654321"
                inputMode="numeric"
                pattern="^\s*(-?\d+(\s*,\s*-?\d+)*)?\s*$"
                title="Các số nguyên, cách nhau bằng dấu phẩy"
              />
            </label>
            <div>
              <button className="primary" type="submit" disabled={busy !== null || !projects.data?.length}>
                Thêm bot
              </button>
            </div>
            {projects.error && <Alert tone="error">Không tải được danh sách dự án: {projects.error.message}</Alert>}
            {projects.data && projects.data.length === 0 && (
              <p className="muted small">
                Tạo dự án ở tab <a href="#repos">Dự án &amp; Repo</a> trước.
              </p>
            )}
          </form>
        </Card>

        <Card title="Cách dùng" subtitle="Gửi cho bot trong chat riêng hoặc nhóm của team">
          <ol style={{ paddingLeft: 18, display: "grid", gap: 6 }}>
            <li>
              Tạo bot với <strong>@BotFather</strong> (<code>/newbot</code>), lấy token. Với nhóm: tắt{" "}
              <em>Group Privacy</em> (<code>/setprivacy</code> → Disable) để bot đọc được lệnh.
            </li>
            <li>Thêm bot ở đây, chọn dự án (và repo nếu muốn giới hạn).</li>
            <li>
              Mỗi thành viên mở <strong>Của tôi → Telegram → Tạo mã liên kết</strong>, gửi <code>/link MÃ</code> cho bot.
            </li>
            <li>Gửi lệnh; kết quả (điểm, số finding, link) được trả về cùng chat khi review xong.</li>
          </ol>
          <h3>Lệnh</h3>
          <ul className="cmd-list">
            {COMMANDS.map(([cmd, what]) => (
              <li key={cmd}>
                <code>{cmd}</code>
                <span className="muted small">{what}</span>
              </li>
            ))}
          </ul>
          <p className="muted small" style={{ marginTop: 8 }}>
            Quyền như dashboard: thành viên review việc của mình; lead review người trong team mình; admin mọi người. Review tốn ngân sách LLM như
            bình thường và tính vào chỉ số tuần/tháng.
          </p>
        </Card>
      </div>
    </>
  );
}

function Requests({ botId }: { botId: number }) {
  const { data, error } = useApi<TelegramRequest[]>(`/telegram/bots/${botId}/requests?limit=10`);
  if (!data) return <Loading error={error} rows={2} />;
  if (data.length === 0) return <p className="muted small">Chưa có yêu cầu nào.</p>;
  return (
    <table className="stack-sm">
      <thead>
        <tr>
          <th>Lúc</th>
          <th>Người</th>
          <th>Lệnh</th>
          <th>Mục</th>
          <th>Kết quả</th>
        </tr>
      </thead>
      <tbody>
        {data.map((r) => (
          <tr key={r.id}>
            <td className="muted">{dateTime(r.created_at)}</td>
            <td>{r.user_name ?? "—"}</td>
            <td>
              <code>{r.command}</code>
            </td>
            <td className="muted">{r.targets.length}</td>
            <td>{r.status === "notified" ? <Pill tone="good">Đã báo</Pill> : <Pill tone="info">Đang chờ</Pill>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
