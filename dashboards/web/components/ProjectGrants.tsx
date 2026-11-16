"use client";

import { useEffect, useMemo, useState } from "react";
import { useSWRConfig } from "swr";

import { IconButton } from "@/components/icons";
import { Alert, Loading, Segmented } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { BusinessProject, Me, Person, ProjectGrant } from "@/lib/types";

/** A grant as one choice: its role plus, for "manage", the dự án's Lead / Manager (docs/specs/21 §10). */
type Choice = "view" | "manage" | "lead" | "manager";
const ROLES: { value: Choice; label: string }[] = [
  { value: "view", label: "Xem" },
  { value: "manage", label: "Quản lý" },
  { value: "lead", label: "Lead dự án" },
  { value: "manager", label: "Manager dự án" },
];
/** What a dự án's Manager hands out; the other choices stay the admins'. */
const STAFF_ROLES = ROLES.filter((r) => r.value === "view" || r.value === "lead");

const choiceOf = (g: ProjectGrant): Choice => (g.role === "manage" && g.admin_role ? g.admin_role : g.role);
function withChoice(g: ProjectGrant, c: Choice): ProjectGrant {
  return c === "view" || c === "manage" ? { ...g, role: c, admin_role: null } : { ...g, role: "manage", admin_role: c };
}
const label = (c: Choice) => ROLES.find((x) => x.value === c)?.label ?? c;

/**
 * Who may see a dự án (docs/specs/09 "Phân quyền theo dự án"). "Xem": its numbers, and the
 * reviews the team rules allow; "Quản lý": everyone's reviews there, plus requesting and
 * re-running reviews and editing the premises. "Lead dự án" / "Manager dự án" (docs/specs/21
 * §10): "Quản lý" plus setting the dự án up (repos, .claude repo, rules); the Manager also
 * decides who sees it. Admins (ADMIN_EMAILS) need no grant.
 *
 * `mode`: "admin" (admin.projects) edits every row; "staff" (the dự án's Manager) gives "Xem"
 * and "Lead dự án" only, and leaves the other rows and their own as they are; "readonly" lists.
 */
export function ProjectGrants({
  project,
  busy,
  mode = "admin",
}: {
  project: BusinessProject;
  busy: boolean;
  mode?: "admin" | "staff" | "readonly";
}) {
  const path = `/business-projects/${project.id}/grants`;
  const saved = useApi<ProjectGrant[]>(path);
  const { data: me } = useApi<Me>("/auth/me");
  const users = useApi<Person[]>(
    mode === "admin" ? "/users" : mode === "staff" ? `/business-projects/${project.id}/grant-candidates` : null,
  );
  const editable = (g: ProjectGrant) =>
    mode === "admin" || (mode === "staff" && g.user_id !== me?.id && (choiceOf(g) === "view" || choiceOf(g) === "lead"));
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
      rows.some((g) => {
        const was = saved.data?.find((s) => s.user_id === g.user_id);
        return !was || choiceOf(was) !== choiceOf(g);
      }));
  const managers = rows.filter((g) => g.role === "manage").length;
  const admins = rows.filter((g) => g.admin_role).length;

  function setRole(userId: number, choice: Choice) {
    setDraft(rows.map((g) => (g.user_id === userId ? withChoice(g, choice) : g)));
  }
  function add(id: string) {
    const user = users.data?.find((u) => String(u.id) === id);
    setAdding("");
    if (!user) return;
    setDraft([
      ...rows,
      { user_id: user.id, full_name: user.full_name, email: user.primary_email, active: user.active !== false, role: "view", admin_role: null },
    ]);
  }
  /** What saving changes, for the confirmation. */
  function changes(): string[] {
    const before = new Map((saved.data ?? []).map((g) => [g.user_id, g]));
    const after = new Map(rows.map((g) => [g.user_id, g]));
    const name = (g: ProjectGrant) => g.full_name ?? g.email ?? `#${g.user_id}`;
    const out: string[] = [];
    for (const g of rows) {
      const was = before.get(g.user_id);
      if (!was) out.push(`+ ${name(g)}: ${label(choiceOf(g))}`);
      else if (choiceOf(was) !== choiceOf(g)) out.push(`~ ${name(g)}: ${label(choiceOf(was))} → ${label(choiceOf(g))}`);
    }
    for (const g of saved.data ?? []) if (!after.has(g.user_id)) out.push(`- ${name(g)}: bỏ quyền`);
    return out;
  }

  async function save() {
    const list = changes();
    if (!window.confirm(`Lưu quyền dự án "${project.name}"?\n\n${list.join("\n")}`)) return;
    setWorking(true);
    setResult(null);
    try {
      const next = await api.put<ProjectGrant[]>(path, {
        grants: rows.map((g) => ({ user_id: g.user_id, role: g.role, admin_role: g.admin_role ?? null })),
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
              {draft !== null &&
                `(${rows.length} người${managers ? `, ${managers} quản lý` : ""}${admins ? `, ${admins} lead/manager dự án` : ""})`}
            </span>
          </h3>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Chỉ người được cấp mới thấy dự án này. <b>Xem</b>: số liệu, review của mình và của người trong team mình lead.{" "}
            <b>Quản lý</b>: thêm review của mọi người trong dự án, yêu cầu và chạy lại review, sửa và duyệt Tiền đề review.{" "}
            <b>Lead dự án</b>: thêm quản lý dự án: thêm repo, cấu hình repo, repo .claude, file quy tắc và quy tắc review.{" "}
            <b>Manager dự án</b>: như Lead dự án, thêm quyết định ai được xem dự án và giao Lead dự án. Admin luôn có toàn quyền.
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
              {editable(g) ? (
                <>
                  <Segmented
                    value={choiceOf(g)}
                    onChange={(choice) => setRole(g.user_id, choice)}
                    options={mode === "admin" ? ROLES : STAFF_ROLES}
                    label={`Quyền của ${g.full_name ?? g.email ?? g.user_id}`}
                  />
                  <IconButton
                    icon="close"
                    label={`Bỏ quyền của ${g.full_name ?? g.email ?? g.user_id}`}
                    onClick={() => setDraft(rows.filter((r) => r.user_id !== g.user_id))}
                    size={14}
                  />
                </>
              ) : (
                <span className="pill" title={mode === "staff" ? "Chỉ admin đổi được quyền này" : undefined}>
                  {label(choiceOf(g))}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {mode !== "readonly" && (
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
      )}
      {mode === "readonly" && (
        <p className="muted small" style={{ marginTop: 8 }}>
          Chỉ Manager dự án hoặc admin đổi được quyền truy cập.
        </p>
      )}
    </>
  );
}
