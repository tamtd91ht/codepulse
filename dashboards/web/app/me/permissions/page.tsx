"use client";

import { type FormEvent, useMemo, useState } from "react";

import { Alert, Card, Loading, PageHeader, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { MyPermissions, PermissionCheck } from "@/lib/types";

const SCOPE_LABEL: Record<string, string> = {
  flag: "Có",
  own: "Của mình",
  team: "Team",
  project: "Dự án",
  all: "Tất cả",
};
const VIA_LABEL: Record<string, string> = {
  role: "Theo vai trò",
  given: "Được gán",
  personal: "Quyền riêng",
  team: "Qua team",
};
const ROLE_LABEL: Record<string, string> = {
  admin: "Admin",
  member: "Mọi người",
  lead: "Lead",
  manager: "Manager",
  project_manager: "Quản lý dự án",
  manage_all: "Quản lý tổng",
  project_admin: "Lead / Manager dự án",
};
// Asked about most ("sao tôi không làm được X"), offered even when not held.
const COMMON = [
  "tasks.edit",
  "tasks.assign",
  "tasks.transition",
  "tasks.delete",
  "tasks.worklog",
  "tasks.link",
  "tasks.moderate",
  "tasks.sprint",
  "tasks.manage",
  "tests.edit",
  "tests.run",
  "tests.approve",
  "tests.delete",
  "tests.manage",
];

/**
 * Quyền của tôi (docs/specs/31 §21.5): the caller's effective permissions, the sets they come
 * from and what each scope reaches; "Tôi có làm được không?" explains one permission on one task
 * or one dự án in plain words.
 */
export default function MyPermissionsPage() {
  const { data, error } = useApi<MyPermissions>("/me/permissions?explain=1");
  const [key, setKey] = useState("tasks.edit");
  const [on, setOn] = useState<"task" | "project">("task");
  const [task, setTask] = useState("");
  const [project, setProject] = useState("");
  const [answer, setAnswer] = useState<PermissionCheck | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const keys = useMemo(() => {
    const held = (data?.permissions ?? []).map((p) => p.key);
    return [...new Set([...COMMON, ...held])].sort();
  }, [data]);
  const labels = useMemo(
    () => Object.fromEntries((data?.permissions ?? []).map((p) => [p.key, p.label ?? p.key])),
    [data],
  );

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    const params = new URLSearchParams({ check: key });
    if (on === "task" && task.trim()) params.set("task", task.trim().toUpperCase());
    if (on === "project" && project) params.set("project", project);
    setBusy(true);
    setFailed(null);
    setAnswer(null);
    try {
      const res = await api.get<MyPermissions>(`/me/permissions?${params}`);
      setAnswer(res.check);
    } catch (err) {
      setFailed(
        err instanceof ApiError && err.status === 404
          ? on === "task"
            ? "Không thấy task này (sai mã, hoặc bạn không xem được dự án của nó)."
            : "Không thấy dự án này."
          : `Lỗi: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <Loading error={error} rows={6} />;
  return (
    <>
      <PageHeader
        title="Quyền của tôi"
        subtitle="Bạn làm được gì, ở mức nào, và quyền đến từ bộ quyền nào. Muốn thêm quyền: hỏi admin hoặc Lead / Manager dự án."
      />

      <Card title="Tôi có làm được không?" subtitle="Chọn một quyền và một task (hoặc một dự án) để biết vì sao được hay không.">
        <form className="toolbar filters" onSubmit={(e) => void ask(e)}>
          <label className="filter">
            <span className="small muted">Quyền</span>
            <select value={key} onChange={(e) => setKey(e.target.value)}>
              {keys.map((k) => (
                <option key={k} value={k}>
                  {labels[k] ? `${k} · ${labels[k]}` : k}
                </option>
              ))}
            </select>
          </label>
          <label className="filter">
            <span className="small muted">Ở đâu</span>
            <select value={on} onChange={(e) => setOn(e.target.value as "task" | "project")}>
              <option value="task">Một task</option>
              <option value="project">Một dự án</option>
            </select>
          </label>
          {on === "task" ? (
            <label className="filter">
              <span className="small muted">Mã task</span>
              <input value={task} placeholder="VD: CRM-12" onChange={(e) => setTask(e.target.value)} />
            </label>
          ) : (
            <label className="filter">
              <span className="small muted">Dự án</span>
              <select value={project} onChange={(e) => setProject(e.target.value)}>
                <option value="">Chọn dự án</option>
                {data.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "Đang xem…" : "Giải thích"}
          </button>
        </form>
        {failed && <Alert tone="error">{failed}</Alert>}
        {answer && (
          <Alert tone={answer.allowed ? "success" : answer.allowed === false ? "warning" : "info"}>
            <strong>
              {answer.allowed ? "Được" : answer.allowed === false ? "Không được" : "Tuỳ task"}
              {answer.target ? ` · ${answer.target}` : ""}
            </strong>
            {answer.lines.map((line, i) => (
              <p key={i} className="small" style={{ margin: "4px 0 0" }}>
                {line}
              </p>
            ))}
          </Alert>
        )}
      </Card>

      <Card title="Vai trò và bộ quyền">
        {data.is_admin ? (
          <p className="small">Bạn là admin: có mọi quyền ở mọi dự án, không qua bộ quyền.</p>
        ) : (
          <>
            <p className="small muted">
              Vai trò: {data.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ") || "chưa có"}
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {data.sets.map((s) => (
                <Pill key={s.id} tone={s.via === "role" ? undefined : "info"} title={VIA_LABEL[s.via] ?? s.via}>
                  {s.name} · {VIA_LABEL[s.via] ?? s.via}
                </Pill>
              ))}
            </div>
          </>
        )}
      </Card>

      <Card title={`Quyền đang có (${data.permissions.length})`} flush>
        <div className="table-wrap">
          <table className="stack-sm">
            <thead>
              <tr>
                <th>Quyền</th>
                <th>Mức</th>
                <th>Nghĩa là</th>
                <th>Từ bộ</th>
              </tr>
            </thead>
            <tbody>
              {data.permissions.map((p) => (
                <tr key={p.key}>
                  <td>
                    <div>{p.label ?? p.key}</div>
                    <code className="small muted">{p.key}</code>
                  </td>
                  <td>{SCOPE_LABEL[p.scope] ?? p.scope}</td>
                  <td className="small">{p.meaning ?? ""}</td>
                  <td className="small">
                    {p.sources.map((s) => `${s.name} (${SCOPE_LABEL[s.scope] ?? s.scope})`).join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
