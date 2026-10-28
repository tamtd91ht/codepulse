"use client";

import { useEffect, useMemo, useState } from "react";
import { useSWRConfig } from "swr";

import { IconButton } from "@/components/icons";
import { Alert, Loading, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { BusinessProject, Person, ProjectGrant, ProjectRole } from "@/lib/types";

const ROLES: { value: ProjectRole; label: string }[] = [
  { value: "view", label: "Xem" },
  { value: "manage", label: "Quản lý" },
];

/**
 * Who may see a dự án (docs/specs/09 "Phân quyền theo dự án"). "Xem": its numbers, and the
 * reviews the team rules allow; "Quản lý": everyone's reviews there, plus requesting and
 * re-running reviews and editing the premises. Admins (ADMIN_EMAILS) need no grant.
 */
export function ProjectGrants({ project, busy }: { project: BusinessProject; busy: boolean }) {
  const path = `/business-projects/${project.id}/grants`;
  const saved = useApi<ProjectGrant[]>(path);
  const users = useApi<Person[]>("/users");
  const { mutate } = useSWRConfig();
  const [draft, setDraft] = useState<ProjectGrant[] | null>(null);
  const [adding, setAdding] = useState("");
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  // A fresh copy whenever the server list changes (first load, after a save).
  useEffect(() => {
    if (saved.data) setDraft(saved.data);
  }, [saved.data]);

  const rows = draft ?? [];
  const granted = useMemo(() => new Set(rows.map((g) => g.user_id)), [rows]);
  const candidates = (users.data ?? []).filter((u) => u.active !== false && !granted.has(u.id));
  const dirty =
    saved.data !== undefined &&
    (rows.length !== saved.data.length ||
      rows.some((g) => saved.data?.find((s) => s.user_id === g.user_id)?.role !== g.role));
  const managers = rows.filter((g) => g.role === "manage").length;

  function setRole(userId: number, role: ProjectRole) {
    setDraft(rows.map((g) => (g.user_id === userId ? { ...g, role } : g)));
  }
  function add(id: string) {
    const user = users.data?.find((u) => String(u.id) === id);
    setAdding("");
    if (!user) return;
    setDraft([
      ...rows,
      { user_id: user.id, full_name: user.full_name, email: user.primary_email, active: user.active !== false, role: "view" },
    ]);
  }
  async function save() {
    setWorking(true);
    setResult(null);
    try {
      const next = await api.put<ProjectGrant[]>(path, {
        grants: rows.map((g) => ({ user_id: g.user_id, role: g.role })),
      });
      await saved.mutate(next, { revalidate: false });
      await mutate("/business-projects");
      setResult({ tone: "success", text: `Đã lưu quyền: ${next.length} người.` });
    } catch (e) {
      setResult({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      <div className="drawer-title-row" style={{ marginBottom: 8 }}>
        <div>
          <h3 style={{ margin: 0 }}>
            Quyền truy cập{" "}
            <span className="muted small">
              {draft !== null && `(${rows.length} người${managers ? `, ${managers} quản lý` : ""})`}
            </span>
          </h3>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Chỉ người được cấp mới thấy dự án này. <b>Xem</b>: số liệu, review của mình và của người trong team mình lead.{" "}
            <b>Quản lý</b>: thêm review của mọi người trong dự án, yêu cầu và chạy lại review, sửa và duyệt Tiền đề review.
            Admin luôn có toàn quyền.
          </p>
        </div>
      </div>

      {result && (
        <Alert tone={result.tone} onClose={() => setResult(null)}>
          {result.text}
        </Alert>
      )}

      {draft === null ? (
        <Loading error={saved.error} rows={2} />
      ) : rows.length === 0 ? (
        <p className="muted small">Chưa ai được cấp quyền: chỉ admin thấy dự án này.</p>
      ) : (
        <ul className="grant-list">
          {rows.map((g) => (
            <li key={g.user_id}>
              <span className="grant-name">
                {g.full_name ?? g.email ?? `#${g.user_id}`}
                {g.email && g.full_name && <span className="muted small"> · {g.email}</span>}
                {!g.active && <span className="muted small"> · đang tắt</span>}
              </span>
              <Segmented
                value={g.role}
                onChange={(role) => setRole(g.user_id, role)}
                options={ROLES}
                label={`Quyền của ${g.full_name ?? g.email ?? g.user_id}`}
              />
              <IconButton
                icon="close"
                label={`Bỏ quyền của ${g.full_name ?? g.email ?? g.user_id}`}
                onClick={() => setDraft(rows.filter((r) => r.user_id !== g.user_id))}
                size={14}
              />
            </li>
          ))}
        </ul>
      )}

      <div className="toolbar" style={{ margin: "8px 0 0" }}>
        <select
          value={adding}
          onChange={(e) => add(e.target.value)}
          aria-label="Thêm người vào dự án"
          disabled={draft === null || !users.data || candidates.length === 0}
        >
          <option value="">
            {!users.data ? "Đang tải danh sách người…" : candidates.length ? "+ Thêm người…" : "Mọi người đã có quyền"}
          </option>
          {candidates.map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name ?? u.primary_email ?? `#${u.id}`}
              {u.full_name && u.primary_email ? ` (${u.primary_email})` : ""}
            </option>
          ))}
        </select>
        <span className="spacer" />
        {dirty && (
          <button type="button" onClick={() => setDraft(saved.data ?? [])} disabled={working}>
            Hoàn tác
          </button>
        )}
        <button className="primary" type="button" disabled={!dirty || working || busy} onClick={() => void save()}>
          {working ? "Đang lưu…" : "Lưu quyền"}
        </button>
      </div>
    </>
  );
}
