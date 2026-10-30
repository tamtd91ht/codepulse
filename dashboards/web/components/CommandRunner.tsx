"use client";

import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { Alert, Card, EmptyState, Loading, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type {
  Branch,
  BusinessProject,
  ClaudeCommand,
  ClaudeCommandList,
  CommandLogEntry,
  CommandRun,
  CommandRunDetail,
  CommandRunStatus,
  Repo,
  ReviewSetting,
} from "@/lib/types";

const POLL_MS = 2000;
const NO_REPO = "none";

const STATUS: Record<CommandRunStatus, { label: string; tone: "good" | "bad" | "warn" | "info" | undefined }> = {
  queued: { label: "Đang chờ", tone: "info" },
  running: { label: "Đang chạy", tone: "info" },
  succeeded: { label: "Xong", tone: "good" },
  failed: { label: "Lỗi", tone: "bad" },
  cancelled: { label: "Đã huỷ", tone: undefined },
};
const active = (status: CommandRunStatus) => status === "queued" || status === "running";
const commandKey = (c: ClaudeCommand) => `${c.source}:${c.name}`;

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Admin > Chạy command: run a command of a repo's .claude/commands (or of the dự án's shared
 * .claude repo) on the command worker, follow it live, download what it wrote (once: the
 * download deletes it from the server).
 */
export function CommandRunner() {
  const [runId, setRunId] = useState<number | null>(null);
  const history = useApi<CommandRun[]>("/admin/command-runs?limit=30", {
    refreshInterval: (rows) => (rows?.some((r) => active(r.status)) ? POLL_MS * 2 : 0),
  });
  if (history.error?.status === 403) return null;
  return (
    <>
      <RunForm
        onStarted={(run) => {
          setRunId(run.id);
          void history.mutate();
        }}
      />
      {runId !== null && <RunView runId={runId} onChanged={() => void history.mutate()} />}
      <Card title="Lịch sử chạy" subtitle="30 lần gần nhất. Nội dung file không được lưu; chỉ còn tên và dung lượng." flush>
        {!history.data ? (
          <div style={{ padding: "0 18px 16px" }}>
            <Loading error={history.error} />
          </div>
        ) : history.data.length === 0 ? (
          <EmptyState icon="code" title="Chưa chạy command nào" />
        ) : (
          <div className="table-wrap">
            <table className="stack-sm">
              <thead>
                <tr>
                  <th>Command</th>
                  <th>Ở đâu</th>
                  <th>Trạng thái</th>
                  <th className="num">Chi phí</th>
                  <th>Người chạy</th>
                  <th>Lúc</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <button type="button" className="link" onClick={() => setRunId(r.id)}>
                        <code>/{r.command}</code>
                      </button>
                      {r.arguments && <span className="muted small"> {r.arguments}</span>}
                    </td>
                    <td className="muted">
                      {r.repo ? `${r.repo} @ ${r.ref}` : `${r.business_project ?? "?"} (repo .claude dùng chung)`}
                    </td>
                    <td>
                      <Pill tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Pill>
                      {r.download && (
                        <Pill tone="primary" icon="inbox">
                          Có file
                        </Pill>
                      )}
                    </td>
                    <td className="num">{r.cost_usd !== null ? `$${r.cost_usd.toFixed(2)}` : "–"}</td>
                    <td>{r.started_by ?? "–"}</td>
                    <td className="muted">{dateTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <CommandLimits />
    </>
  );
}

function RunForm({ onStarted }: { onStarted: (run: CommandRun) => void }) {
  const projects = useApi<BusinessProject[]>("/business-projects");
  const repos = useApi<Repo[]>("/repos");
  const [bpId, setBpId] = useState<number | null>(null);
  const [repoId, setRepoId] = useState<string>("");
  const [ref, setRef] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [args, setArgs] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const business = projects.data?.find((p) => p.id === bpId) ?? null;
  const inProject = useMemo(() => (repos.data ?? []).filter((r) => r.business_project_id === bpId), [repos.data, bpId]);
  const repo = inProject.find((r) => String(r.id) === repoId) ?? null;
  const branches = useApi<Branch[]>(repo ? `/repos/${repo.id}/branches` : null);

  const listPath =
    repo && ref
      ? `/admin/commands?project_id=${repo.id}&ref=${encodeURIComponent(ref)}`
      : repoId === NO_REPO && bpId !== null
        ? `/admin/commands?business_project_id=${bpId}`
        : null;
  const list = useApi<ClaudeCommandList>(listPath);
  const command = list.data?.commands.find((c) => commandKey(c) === picked) ?? null;

  // A new repo starts on its default branch; a new place clears the picked command.
  useEffect(() => {
    setRef(repo?.default_branch ?? "");
  }, [repo?.id, repo?.default_branch]);
  useEffect(() => {
    setPicked(null);
    setArgs("");
  }, [listPath]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!command) return;
    const where = repo ? `${repo.path_with_namespace} @ ${ref}` : `repo .claude dùng chung của ${business?.name}`;
    if (!window.confirm(`Chạy /${command.name}${args.trim() ? ` ${args.trim()}` : ""} trong ${where}?`)) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.post<CommandRun>("/admin/command-runs", {
        project_id: repo?.id ?? null,
        business_project_id: repo ? null : bpId,
        ref: repo ? ref : null,
        source: command.source,
        command: command.name,
        arguments: args,
      });
      onStarted(run);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!projects.data || !repos.data) return <Loading error={projects.error ?? repos.error} rows={3} />;

  return (
    <Card
      title="Chạy command"
      subtitle="Chạy một command trong .claude/commands của repo hoặc repo .claude dùng chung của dự án, như gõ /command ở máy local. Chỉ admin."
    >
      <form onSubmit={submit} style={{ display: "grid", gap: 12 }}>
        <div className="toolbar" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">Dự án</span>
            <select
              value={bpId ?? ""}
              onChange={(e) => {
                setBpId(e.target.value ? Number(e.target.value) : null);
                setRepoId("");
              }}
            >
              <option value="">Chọn dự án…</option>
              {projects.data.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">Repo</span>
            <select value={repoId} onChange={(e) => setRepoId(e.target.value)} disabled={bpId === null}>
              <option value="">Chọn repo…</option>
              {business?.claude_repo_path && (
                <option value={NO_REPO}>Không chọn repo: chạy trong {business.claude_repo_path}</option>
              )}
              {inProject.map((r) => (
                <option key={r.id} value={r.id} disabled={!r.review_enabled}>
                  {r.path_with_namespace}
                  {r.review_enabled ? "" : " (đang tắt review)"}
                </option>
              ))}
            </select>
          </label>
          {repo && (
            <label style={{ display: "grid", gap: 4 }}>
              <span className="small muted">Nhánh</span>
              <select value={ref} onChange={(e) => setRef(e.target.value)} disabled={!branches.data}>
                {!branches.data && <option value={ref}>{ref || "Đang tải…"}</option>}
                {branches.data?.map((b) => (
                  <option key={b.name} value={b.name}>
                    {b.name}
                    {b.default ? " (mặc định)" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {business && !business.claude_repo_path && repoId === "" && (
          <p className="muted small">Dự án này chưa khai báo repo .claude dùng chung, nên chỉ chạy được command của từng repo.</p>
        )}

        {listPath && !list.data && <Loading error={list.error} rows={2} />}
        {list.data && (
          <>
            {list.data.repo_error && <Alert tone="warning">Không đọc được .claude/commands của repo: {list.data.repo_error}</Alert>}
            {list.data.shared_error && (
              <Alert tone="warning">Không đọc được repo .claude dùng chung: {list.data.shared_error}</Alert>
            )}
            {list.data.commands.length === 0 ? (
              <EmptyState icon="code" title="Không có command nào">
                Không thấy file .md nào trong .claude/commands
                {list.data.shared_repo ? ` của repo hay của ${list.data.shared_repo}` : " của repo"} ở nhánh này.
              </EmptyState>
            ) : (
              <fieldset className="command-list">
                <legend className="small muted">
                  Command ({list.data.commands.length})
                  {list.data.shared_repo && ` · dùng chung: ${list.data.shared_repo} @ ${list.data.shared_ref}`}
                </legend>
                {list.data.commands.map((c) => (
                  <label key={commandKey(c)} className={c.overridden ? "muted" : undefined}>
                    <input
                      type="radio"
                      name="command"
                      checked={picked === commandKey(c)}
                      disabled={c.overridden}
                      onChange={() => setPicked(commandKey(c))}
                    />
                    <span>
                      <code>/{c.name}</code>{" "}
                      <Pill tone={c.source === "repo" ? "primary" : "info"}>{c.source === "repo" ? "Repo" : "Dùng chung"}</Pill>
                      {c.overridden && <Pill tone="warn">bị command của repo ghi đè</Pill>}
                      {c.description && <span className="small muted command-desc">{c.description}</span>}
                    </span>
                  </label>
                ))}
              </fieldset>
            )}
          </>
        )}

        {command && (
          <>
            <details className="small">
              <summary className="muted">
                Nội dung {command.path}
                {command.allowed_tools.length > 0 && ` · allowed-tools: ${command.allowed_tools.join(", ")}`}
                {command.model && ` · model: ${command.model}`}
              </summary>
              <pre className="command-body">{command.body}</pre>
            </details>
            {command.allowed_tools.length === 0 && (
              <p className="muted small">
                Command không khai báo allowed-tools: Claude chỉ đọc và ghi file trong repo; lệnh shell (Bash) bị từ chối trừ khi
                .claude/settings.json của repo cho phép.
              </p>
            )}
            <label style={{ display: "grid", gap: 4 }}>
              <span className="small muted">Tham số ($ARGUMENTS){command.argument_hint && `: ${command.argument_hint}`}</span>
              <textarea
                value={args}
                onChange={(e) => setArgs(e.target.value)}
                rows={2}
                maxLength={4000}
                placeholder={command.argument_hint || "Để trống nếu command không cần tham số"}
              />
            </label>
            <div className="toolbar">
              <button type="submit" className="primary" disabled={busy}>
                {busy ? "Đang gửi…" : `Chạy /${command.name}`}
              </button>
            </div>
          </>
        )}
        {error && <Alert tone="error">Lỗi: {error}</Alert>}
      </form>
    </Card>
  );
}

function line(e: CommandLogEntry): string {
  switch (e.kind) {
    case "start":
      return `Claude Code bắt đầu${e.model ? ` (${e.model})` : ""}`;
    case "denied":
      return `${e.tool} bị từ chối (command không cho phép tool này)`;
    case "end":
      return e.text ?? (e.ok ? `Xong sau ${e.turns ?? "?"} lượt` : "Dừng (lỗi, hết lượt hoặc hết ngân sách)");
    default:
      return e.text ?? "";
  }
}

function RunView({ runId, onChanged }: { runId: number; onChanged: () => void }) {
  const [entries, setEntries] = useState<CommandLogEntry[]>([]);
  const next = useRef(0);
  const shown = useRef(runId);
  if (shown.current !== runId) {
    // Another run: its log starts from the beginning (reset before the key below is built).
    shown.current = runId;
    next.current = 0;
  }
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLOListElement>(null);
  const run = useApi<CommandRunDetail>(`/admin/command-runs/${runId}?after=${next.current}`, {
    keepPreviousData: true,
    refreshInterval: (d) => (d && !active(d.status) ? 0 : POLL_MS),
  });
  const data = run.data?.id === runId ? run.data : undefined;
  const status = data?.status;

  useEffect(() => {
    setEntries([]);
  }, [runId]);
  useEffect(() => {
    if (!data || data.log_next <= next.current) return;
    setEntries((old) => [...old, ...data.log]);
    next.current = data.log_next;
  }, [data]);
  // Once per run, when it ends: the history table shows the new status.
  useEffect(() => {
    if (status && !active(status)) changed.current();
  }, [status, runId]);
  useEffect(() => {
    const el = box.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/admin/command-runs/${runId}/cancel`);
      await run.mutate();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <Loading error={run.error} rows={3} />;
  const where = data.repo ? `${data.repo} @ ${data.ref}${data.repo_sha ? ` (${data.repo_sha.slice(0, 8)})` : ""}` : data.business_project;
  return (
    <Card
      title={`/${data.command}${data.arguments ? ` ${data.arguments}` : ""}`}
      subtitle={`${where ?? ""} · ${data.started_by ?? ""} · ${dateTime(data.created_at)}`}
    >
      <div style={{ display: "grid", gap: 10 }}>
        <div className="toolbar" style={{ flexWrap: "wrap" }}>
          <Pill tone={STATUS[data.status].tone}>{STATUS[data.status].label}</Pill>
          {data.cost_usd !== null && <span className="muted small">${data.cost_usd.toFixed(2)}</span>}
          {data.num_turns !== null && <span className="muted small">{data.num_turns} lượt</span>}
          {data.shared_sha && (
            <span className="muted small">
              .claude dùng chung @ {data.shared_ref} ({data.shared_sha.slice(0, 8)})
            </span>
          )}
          {active(data.status) && (
            <button type="button" onClick={cancel} disabled={busy}>
              Huỷ
            </button>
          )}
        </div>
        {data.error && <Alert tone="error">{data.error}</Alert>}
        {data.meta.recognized === false && (
          <Alert tone="warning">Claude Code không nhận ra /{data.command} trong .claude/commands: kết quả có thể không đúng command.</Alert>
        )}
        {!!data.meta.denied?.length && (
          <Alert tone="warning">
            Bị từ chối: {data.meta.denied.join(", ")}. Thêm vào allowed-tools của command nếu command cần.
          </Alert>
        )}
        {!!data.meta.replaced?.length && (
          <p className="muted small">File dùng chung bị file của repo thay: {data.meta.replaced.join(", ")}</p>
        )}
        {entries.length > 0 && (
          <ol ref={box} className="live-log command-log" aria-label="Diễn biến" aria-live="off">
            {entries.map((e, i) => (
              <li key={`${i}-${e.at ?? ""}`} className={`k-${e.kind}`}>
                {e.kind === "tool" && <span className="tool">{e.tool}</span>}
                <span>{line(e)}</span>
              </li>
            ))}
          </ol>
        )}
        {data.result && (
          <details open={!active(data.status)}>
            <summary className="muted small">Kết quả</summary>
            <pre className="command-body">{data.result}</pre>
          </details>
        )}
        {data.download ? (
          <div style={{ display: "grid", gap: 6 }}>
            <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
              {data.files.map((f) => (
                <li key={f.name}>
                  {f.name} <span className="muted">({size(f.size)})</span>
                </li>
              ))}
            </ul>
            <div className="toolbar">
              <a
                className="button"
                href={`/api/v1/admin/command-runs/${data.id}/download`}
                download={data.download}
                onClick={() => window.setTimeout(() => void run.mutate(), 1500)}
              >
                Tải {data.download}
              </a>
              <span className="muted small">Tải xong file bị xoá khỏi server; không ai tải thì tự xoá sau thời hạn ở dưới.</span>
            </div>
          </div>
        ) : (
          !active(data.status) &&
          data.files.length > 0 &&
          data.files_deleted && <p className="muted small">File đã được tải hoặc đã hết hạn, không còn trên server.</p>
        )}
        {!active(data.status) && data.status === "succeeded" && data.files.length === 0 && (
          <p className="muted small">Command không tạo hay sửa file nào.</p>
        )}
        {!!data.meta.skipped?.length && (
          <Alert tone="warning">Bỏ qua vì vượt dung lượng tối đa: {data.meta.skipped.join(", ")}</Alert>
        )}
        {error && <Alert tone="error">Lỗi: {error}</Alert>}
      </div>
    </Card>
  );
}

const LIMITS: { key: ReviewSetting["key"]; label: string; hint: string }[] = [
  { key: "command_timeout_min", label: "Thời gian tối đa (phút)", hint: "Quá thời gian thì dừng command." },
  { key: "command_max_turns", label: "Số lượt tối đa", hint: "Mỗi lượt là một lần model trả lời hoặc gọi tool." },
  { key: "command_max_budget_usd", label: "Chi phí tối đa mỗi lần (USD)", hint: "Vẫn tính vào ngân sách LLM của tháng." },
  { key: "command_clone_depth", label: "Số commit lịch sử khi clone", hint: "0 = toàn bộ lịch sử (cho command dùng git log xa)." },
  { key: "command_max_output_mb", label: "Dung lượng file trả về tối đa (MB)", hint: "File vượt quá không được giữ." },
  { key: "command_file_ttl_hours", label: "Tự xoá file chưa tải sau (giờ)", hint: "Tải xong thì xoá ngay." },
];

function CommandLimits() {
  const settings = useApi<ReviewSetting[]>("/admin/review-settings");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  async function save(key: ReviewSetting["key"], value: number | null) {
    setBusy(true);
    setMessage(null);
    try {
      await settings.mutate(await api.put<ReviewSetting[]>("/admin/review-settings", { [key]: value }), false);
      setMessage({ tone: "success", text: value === null ? "Đã trả về giá trị mặc định." : "Đã lưu. Áp dụng cho lần chạy tiếp theo." });
    } catch (err) {
      setMessage({ tone: "error", text: `Lỗi: ${err instanceof ApiError ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Giới hạn mỗi lần chạy" subtitle="Ghi đè giá trị trong .env; áp dụng cho lần chạy tiếp theo, không cần khởi động lại.">
      {!settings.data ? (
        <Loading error={settings.error} />
      ) : (
        <div className="command-limits">
          {LIMITS.map(({ key, label, hint }) => {
            const s = settings.data?.find((x) => x.key === key);
            if (!s) return null;
            return (
              <form
                key={key}
                onSubmit={(e) => {
                  e.preventDefault();
                  const raw = new FormData(e.currentTarget).get(key);
                  void save(key, Number(raw));
                }}
                style={{ display: "grid", gap: 4 }}
              >
                <label style={{ display: "grid", gap: 4 }}>
                  <span className="small muted">
                    {label} ({s.env})
                  </span>
                  <span className="toolbar" style={{ margin: 0 }}>
                    <input
                      key={String(s.value)}
                      name={key}
                      type="number"
                      required
                      min={s.minimum ?? undefined}
                      max={s.maximum ?? undefined}
                      defaultValue={Number(s.value)}
                      style={{ width: 120 }}
                    />
                    <button type="submit" disabled={busy}>
                      Lưu
                    </button>
                    {s.overridden && (
                      <button type="button" onClick={() => save(key, null)} disabled={busy}>
                        Mặc định ({String(s.default)})
                      </button>
                    )}
                  </span>
                </label>
                <span className="muted small">{hint}</span>
              </form>
            );
          })}
        </div>
      )}
      {message && (
        <div style={{ marginTop: 10 }}>
          <Alert tone={message.tone} onClose={() => setMessage(null)}>
            {message.text}
          </Alert>
        </div>
      )}
    </Card>
  );
}
