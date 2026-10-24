"use client";

import { type FormEvent, useState } from "react";

import { IconButton } from "@/components/icons";
import { Alert, Card, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import type { BusinessProject, NotificationWebhook, Project, WebhookEvent, WebhookFormat, WebhookTestResult } from "@/lib/types";

const EVENTS: { key: WebhookEvent; label: string; hint: string }[] = [
  { key: "review.done", label: "Review xong", hint: "Mọi review hoàn tất, kèm điểm và số finding" },
  { key: "review.severe", label: "Có critical/high", hint: "Chỉ review có finding critical hoặc high (kênh cảnh báo)" },
  { key: "review.failed", label: "Review lỗi", hint: "Review thất bại (GitLab, LLM, ngân sách)" },
];
const FORMATS: { key: WebhookFormat; label: string; hint: string }[] = [
  { key: "json", label: "JSON", hint: "Payload CodePulse, ký HMAC-SHA256 nếu có secret" },
  { key: "slack", label: "Slack", hint: "Incoming webhook của Slack (text mrkdwn)" },
  { key: "teams", label: "Teams", hint: "Incoming webhook của Microsoft Teams (MessageCard)" },
];
const ALL = "all";

function describe(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 422) return `Dữ liệu không hợp lệ: ${e.message}`;
    return e.message;
  }
  return String(e);
}

/** Admin tab: endpoints that get a POST when a review finishes (not the GitLab hook). */
export function WebhookAdmin() {
  const hooks = useApi<NotificationWebhook[]>("/webhooks");
  const projects = useApi<BusinessProject[]>("/business-projects");
  const repos = useApi<Project[]>("/projects");
  const [message, setMessage] = useState<string | null>(null);
  const [tone, setTone] = useState<"success" | "error" | "warning" | undefined>(undefined);
  const [busy, setBusy] = useState<number | null>(null);
  const [editing, setEditing] = useState<NotificationWebhook | null>(null);
  const [formProject, setFormProject] = useState(ALL);
  const [tests, setTests] = useState<Record<number, WebhookTestResult>>({});

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

  const projectName = (id: number | null) => (projects.data ?? []).find((b) => b.id === id)?.name ?? `#${id}`;
  const repoPath = (id: number | null) => (repos.data ?? []).find((p) => p.id === id)?.path_with_namespace ?? `#${id}`;
  const scopeOf = (h: NotificationWebhook) =>
    h.project_id !== null ? repoPath(h.project_id) : h.business_project_id !== null ? projectName(h.business_project_id) : "Mọi dự án";

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const events = EVENTS.map((ev) => ev.key).filter((k) => f.get(`event:${k}`));
    const bp = String(f.get("business_project_id") ?? ALL);
    const repo = String(f.get("project_id") ?? "");
    const secret = String(f.get("secret") ?? "");
    const body = {
      name: f.get("name"),
      url: f.get("url"),
      format: f.get("format"),
      events,
      business_project_id: bp === ALL ? null : Number(bp),
      project_id: repo ? Number(repo) : null,
      ...(editing ? (secret ? { secret } : {}) : { secret: secret || null, enabled: true }),
    };
    void run(
      async () => {
        if (!events.length) throw new ApiError(0, "chọn ít nhất một sự kiện.");
        if (editing) await api.patch(`/webhooks/${editing.id}`, body);
        else await api.post("/webhooks", body);
        await hooks.mutate();
        form.reset();
        setEditing(null);
        setFormProject(ALL);
      },
      editing ? "Đã cập nhật webhook." : "Đã thêm webhook. Bấm Gửi thử để kiểm tra endpoint.",
    );
  }

  // Tracked repos of the chosen dự án, plus the repo the edited hook is pinned to (even if untracked),
  // so re-saving never silently widens the scope to "mọi repo".
  const formRepos = (repos.data ?? []).filter(
    (p) =>
      p.id === editing?.project_id ||
      (p.tracked && (formProject === ALL || p.business_project_id === Number(formProject))),
  );

  return (
    <>
      <Message text={message} tone={tone} onClose={() => setMessage(null)} />
      <Card
        title="Webhook thông báo"
        subtitle="Khi một review xong (hoặc lỗi), CodePulse POST kết quả tới endpoint của bạn. Khác với webhook GitLab (GitLab gọi CodePulse khi có MR/commit mới)."
        flush
      >
        {!hooks.data ? (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={hooks.error} />
          </div>
        ) : hooks.data.length === 0 ? (
          <EmptyState icon="webhook" title="Chưa có webhook thông báo nào">
            Thêm endpoint bên dưới: một kênh Slack/Teams, hoặc một service nội bộ nhận JSON có chữ ký.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Tên</th>
                  <th>Endpoint</th>
                  <th>Phạm vi</th>
                  <th>Sự kiện</th>
                  <th>Gửi gần nhất</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {hooks.data.map((h) => {
                  const test = tests[h.id];
                  return (
                    <tr key={h.id} className={h.enabled ? undefined : "muted"}>
                      <td>
                        <strong>{h.name}</strong>
                        <div className="muted small">
                          {FORMATS.find((f) => f.key === h.format)?.label ?? h.format}
                          {h.has_secret ? " · có secret" : ""}
                          {!h.enabled ? " · đã tắt" : ""}
                        </div>
                      </td>
                      <td>
                        <div className="url-cell" title={h.url}>
                          {h.url}
                        </div>
                      </td>
                      <td>{scopeOf(h)}</td>
                      <td>
                        <div className="chips" style={{ margin: 0, gap: 4 }}>
                          {h.events.map((ev) => (
                            <Pill key={ev} tone={ev === "review.severe" ? "warn" : ev === "review.failed" ? "bad" : "info"}>
                              {EVENTS.find((e) => e.key === ev)?.label ?? ev}
                            </Pill>
                          ))}
                        </div>
                      </td>
                      <td>
                        {h.last_delivery_at ? (
                          <>
                            <span title={dateTime(h.last_delivery_at)}>{relative(h.last_delivery_at)}</span>
                            <div className="small">
                              {h.last_error ? (
                                <span className="error" title={h.last_error}>
                                  {h.last_status_code ? `HTTP ${h.last_status_code}` : "lỗi"} · {h.failures} lần liên tiếp
                                </span>
                              ) : (
                                <span className="muted">HTTP {h.last_status_code ?? "—"} · {h.deliveries} lần</span>
                              )}
                            </div>
                          </>
                        ) : (
                          <span className="muted">Chưa gửi</span>
                        )}
                        {test && (
                          <div className={`small ${test.ok ? "muted" : "error"}`}>
                            Gửi thử: {test.ok ? `OK (HTTP ${test.status_code})` : (test.error ?? "lỗi")}
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="actions">
                          <IconButton
                            icon="activity"
                            label="Gửi thử một payload mẫu"
                            disabled={busy === h.id}
                            onClick={() =>
                              void run(
                                async () => {
                                  const result = await api.post<WebhookTestResult>(`/webhooks/${h.id}/test`);
                                  setTests((prev) => ({ ...prev, [h.id]: result }));
                                  await hooks.mutate();
                                  if (!result.ok) throw new ApiError(result.status_code ?? 0, result.error ?? "endpoint không trả lời");
                                },
                                "Endpoint đã nhận payload mẫu.",
                                h.id,
                              )
                            }
                          />
                          <IconButton
                            icon={h.enabled ? "untrack" : "track"}
                            label={h.enabled ? "Tắt" : "Bật lại"}
                            disabled={busy === h.id}
                            onClick={() =>
                              void run(
                                async () => {
                                  await api.patch(`/webhooks/${h.id}`, { enabled: !h.enabled });
                                  await hooks.mutate();
                                },
                                h.enabled ? `Đã tắt ${h.name}.` : `Đã bật lại ${h.name}.`,
                                h.id,
                              )
                            }
                          />
                          <IconButton
                            icon="edit"
                            label="Sửa"
                            onClick={() => {
                              setEditing(h);
                              setFormProject(h.business_project_id === null ? ALL : String(h.business_project_id));
                              document.getElementById("webhook-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
                            }}
                          />
                          <IconButton
                            icon="remove"
                            label="Xoá"
                            tone="danger"
                            disabled={busy === h.id}
                            onClick={() =>
                              window.confirm(`Xoá webhook "${h.name}"?`) &&
                              void run(
                                async () => {
                                  await api.delete(`/webhooks/${h.id}`);
                                  await hooks.mutate();
                                },
                                `Đã xoá ${h.name}.`,
                                h.id,
                              )
                            }
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title={editing ? `Sửa webhook: ${editing.name}` : "Thêm webhook"}
        subtitle="Phạm vi: mọi dự án, một dự án, hoặc một repo. Webhook tắt sau 20 lần gửi lỗi liên tiếp; bật lại ở bảng trên."
        actions={
          editing ? (
            <button
              type="button"
              onClick={() => {
                setEditing(null);
                setFormProject(ALL);
              }}
            >
              Huỷ sửa
            </button>
          ) : undefined
        }
      >
        <form id="webhook-form" key={editing?.id ?? "new"} style={{ display: "grid", gap: 10 }} onSubmit={submit}>
          <div className="toolbar" style={{ margin: 0 }}>
            <input name="name" placeholder="Tên, ví dụ Kênh #alerts" required maxLength={100} defaultValue={editing?.name} />
            <input
              name="url"
              type="url"
              placeholder="https://hooks.slack.com/services/… hoặc https://service.noi.bo/codepulse"
              required
              defaultValue={editing?.url}
              style={{ flex: "2 1 320px" }}
            />
            <select name="format" defaultValue={editing?.format ?? "json"} aria-label="Định dạng">
              {FORMATS.map((f) => (
                <option key={f.key} value={f.key} title={f.hint}>
                  {f.label}
                </option>
              ))}
            </select>
          </div>
          <div className="toolbar" style={{ margin: 0 }}>
            <select
              name="business_project_id"
              value={formProject}
              onChange={(e) => setFormProject(e.target.value)}
              aria-label="Dự án"
            >
              <option value={ALL}>Mọi dự án</option>
              {(projects.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
            <select name="project_id" key={formProject} defaultValue={editing?.project_id ?? ""} aria-label="Repo (tuỳ chọn)">
              <option value="">Mọi repo{formProject === ALL ? "" : " của dự án"}</option>
              {formRepos.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.path_with_namespace}
                  {p.tracked ? "" : " (đã bỏ theo dõi)"}
                </option>
              ))}
            </select>
            <input
              name="secret"
              type="password"
              placeholder={editing?.has_secret ? "Secret mới (để trống = giữ nguyên)" : "Secret ký HMAC (tuỳ chọn, ≥16 ký tự)"}
              minLength={16}
              maxLength={200}
              autoComplete="new-password"
            />
          </div>
          <div className="toolbar" style={{ margin: 0 }}>
            {EVENTS.map((ev) => (
              <label key={ev.key} title={ev.hint}>
                <input
                  name={`event:${ev.key}`}
                  type="checkbox"
                  defaultChecked={editing ? editing.events.includes(ev.key) : ev.key === "review.done"}
                />{" "}
                {ev.label}
              </label>
            ))}
            <span className="spacer" />
            <button className="primary" type="submit" disabled={busy === -1}>
              {editing ? "Lưu" : "Thêm webhook"}
            </button>
          </div>
        </form>
        <Alert tone="info">
          <p style={{ margin: 0 }}>
            <strong>Payload JSON.</strong> Header <code>X-CodePulse-Event</code> (review.done | review.severe | review.failed | ping), <code>X-CodePulse-Delivery</code>,{" "}
            <code>X-CodePulse-Timestamp</code> và <code>X-CodePulse-Signature: sha256=HMAC(secret, timestamp + "." + body)</code>. Body gồm <code>review</code> (id, status, score,
            loc_reviewed, summary, findings theo mức độ, top_findings, url, web_url), <code>project</code>,{" "}
            <code>business_project</code>, <code>author</code>. Chi tiết trong <code>docs/specs/16_NOTIFICATION_WEBHOOKS.md</code>.
            Endpoint cần trả 2xx trong 10 giây; 5xx/429 được thử lại 3 lần.
          </p>
        </Alert>
      </Card>
    </>
  );
}
