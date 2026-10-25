"use client";

import { type FormEvent, useState } from "react";
import { useSWRConfig } from "swr";

import { Alert } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { BusinessProject, ClaudeRepoPreview, Connection } from "@/lib/types";

const KIND_LABEL: Record<string, string> = {
  skill: "skill",
  agent: "agent",
  command: "command",
  rules: "rules",
  instructions: "hướng dẫn",
  doc: "tài liệu",
};
const REASON_LABEL: Record<string, string> = {
  name: "tên không thuộc nhóm review (thêm vào “File nạp thêm” nếu cần)",
  budget: "hết giới hạn ký tự",
};

/**
 * The dự án's optional shared .claude repo: one GitLab repo with the skills, agents and rules
 * every service of the dự án shares. The deep review reads it next to each repo's own files;
 * "Kiểm tra" shows which files that would be.
 */
export function ClaudeRepoEditor({ project, busy }: { project: BusinessProject; busy: boolean }) {
  const connections = useApi<Connection[]>("/connections");
  const { mutate } = useSWRConfig();
  const [editing, setEditing] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ClaudeRepoPreview | null>(null);
  const configured = Boolean(project.claude_repo_path);

  async function run(action: () => Promise<void>) {
    setWorking(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  }

  const save = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    run(async () => {
      await api.put(`/business-projects/${project.id}/claude-repo`, {
        connection_id: Number(f.get("connection_id")),
        path: String(f.get("path") ?? "").trim(),
        ref: String(f.get("ref") ?? "").trim() || null,
        include: String(f.get("include") ?? "").trim() || null,
      });
      await mutate("/business-projects");
      setEditing(false);
      setPreview(null);
    });
  };

  const clear = () =>
    run(async () => {
      await api.delete(`/business-projects/${project.id}/claude-repo`);
      await mutate("/business-projects");
      setPreview(null);
    });

  const check = () =>
    run(async () => setPreview(await api.post<ClaudeRepoPreview>(`/business-projects/${project.id}/claude-repo/preview`)));

  const disabled = busy || working;
  return (
    <>
      <div className="drawer-title-row" style={{ marginBottom: 8 }}>
        <div>
          <h3 style={{ margin: 0 }}>
            Repo .claude dùng chung <span className="muted small">(không bắt buộc)</span>
          </h3>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Một repo GitLab chứa skills, agents, rules, CLAUDE.md dùng chung cho mọi repo của dự án. Bước review sâu đọc
            nó cùng các file <code>.claude</code> của từng repo; khi trùng nhau thì file của repo được ưu tiên.
          </p>
        </div>
      </div>
      {error && <Alert tone="error">Lỗi: {error}</Alert>}

      {!editing && configured && (
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <th>Repo</th>
                <td>
                  <code>{project.claude_repo_path}</code>
                </td>
              </tr>
              <tr>
                <th>Nhánh</th>
                <td>{project.claude_repo_ref ?? "nhánh mặc định"}</td>
              </tr>
              <tr>
                <th>File nạp thêm</th>
                <td>{project.claude_repo_include ? <code>{project.claude_repo_include}</code> : "—"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {editing ? (
        <form onSubmit={save} style={{ display: "grid", gap: 10 }}>
          {(connections.data?.length ?? 0) > 1 && (
            <label style={{ display: "grid", gap: 4 }}>
              <span className="small muted">Kết nối GitLab</span>
              <select name="connection_id" defaultValue={project.claude_repo_connection_id ?? connections.data?.[0]?.id}>
                {connections.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.base_url})
                  </option>
                ))}
              </select>
            </label>
          )}
          {(connections.data?.length ?? 0) <= 1 && (
            <input type="hidden" name="connection_id" value={connections.data?.[0]?.id ?? ""} />
          )}
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">Repo trên GitLab: dán link repo hoặc đường dẫn nhóm/repo</span>
            <input
              name="path"
              required
              maxLength={512}
              defaultValue={project.claude_repo_path ?? ""}
              placeholder="https://gitlab.example.com/omicrm/claude-config hoặc omicrm/claude-config"
            />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">Nhánh (để trống = nhánh mặc định)</span>
            <input name="ref" defaultValue={project.claude_repo_ref ?? ""} placeholder="main" maxLength={255} />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="small muted">
              File nạp thêm, tính từ thư mục .claude (mặc định chỉ nạp skill/agent/command có tên chứa review, security,
              audit; mọi rules và CLAUDE.md)
            </span>
            <textarea
              name="include"
              rows={3}
              defaultValue={project.claude_repo_include ?? ""}
              placeholder={"skills/java-conventions/SKILL.md\nskills/*-standards/SKILL.md"}
              maxLength={2000}
            />
          </label>
          <p className="muted small" style={{ margin: 0 }}>
            Repo có thể có thư mục <code>.claude/</code> hoặc chính là nội dung thư mục đó (skills/, agents/, rules/ ở gốc).
            “File nạp thêm” áp dụng cho cả repo dùng chung lẫn <code>.claude</code> của từng repo trong dự án.
          </p>
          <div className="toolbar">
            <button className="primary" type="submit" disabled={disabled || !connections.data?.length}>
              {working ? "Đang kiểm tra GitLab…" : "Lưu"}
            </button>
            <button type="button" onClick={() => setEditing(false)} disabled={disabled}>
              Huỷ
            </button>
          </div>
        </form>
      ) : (
        <div className="toolbar" style={{ margin: "8px 0 0" }}>
          <button type="button" className={configured ? undefined : "primary"} onClick={() => setEditing(true)} disabled={disabled}>
            {configured ? "Sửa" : "Cấu hình repo .claude"}
          </button>
          {configured && (
            <>
              <button type="button" onClick={check} disabled={disabled}>
                {working ? "Đang tải repo…" : "Kiểm tra: sẽ nạp file nào"}
              </button>
              <button type="button" className="danger" onClick={clear} disabled={disabled}>
                Bỏ cấu hình
              </button>
            </>
          )}
        </div>
      )}

      {preview && <PreviewResult preview={preview} />}
    </>
  );
}

function PreviewResult({ preview }: { preview: ClaudeRepoPreview }) {
  const { info } = preview;
  if (info.status !== "used")
    return (
      <Alert tone="error">
        {info.status === "other_connection"
          ? "Repo nằm ở kết nối GitLab khác với các repo của dự án nên review không đọc được."
          : `Không tải được repo: ${info.detail ?? "lỗi không rõ"}`}
      </Alert>
    );
  return (
    <div style={{ marginTop: 10 }}>
      <p className="small">
        <code>{info.repo}</code> @ {info.ref} ({info.sha}) · {info.files} file. Review sâu sẽ nạp{" "}
        <b>{preview.loaded.length}</b> file:
      </p>
      <ul className="small" style={{ margin: "0 0 8px", paddingLeft: 18 }}>
        {preview.loaded.map((d) => (
          <li key={d.path}>
            <code>{d.path}</code> <span className="muted">· {KIND_LABEL[d.kind] ?? d.kind}</span>
            {d.clipped && <span className="muted"> · bị rút gọn (file dài hơn 8.000 ký tự)</span>}
          </li>
        ))}
      </ul>
      {preview.not_loaded.length > 0 && (
        <>
          <p className="small muted" style={{ margin: "0 0 4px" }}>
            Không nạp {preview.not_loaded.length} file:
          </p>
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            {preview.not_loaded.map((d) => (
              <li key={d.path}>
                <code>{d.path}</code> · {REASON_LABEL[d.reason] ?? d.reason}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="muted small">
        Khi review thật, file dùng chung và file của repo chia nhau giới hạn 40.000 ký tự; trang chi tiết review (thẻ
        Căn cứ review) cho biết file nào đã thực sự vào prompt.
      </p>
    </div>
  );
}
