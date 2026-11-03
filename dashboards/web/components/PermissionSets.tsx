"use client";

import { type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { type SubNav, SubPanel, type SubTab, SubTabs, pickSub } from "@/components/AdminTabs";
import { Icon, IconButton } from "@/components/icons";
import { Card, Drawer, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { CatalogPermission, Person, PermissionCatalog, PermissionItem, PermissionSetRow, UserPermissions } from "@/lib/types";

const RANK: Record<string, number> = { own: 1, team: 2, project: 3, all: 4, flag: 4 };
const ROLE_LABEL: Record<string, string> = {
  member: "mọi người",
  lead: "Lead",
  manager: "Manager",
  project_manager: "Quản lý dự án",
  manage_all: "Quản lý tổng",
};

type Draft = Record<string, string>;
const toDraft = (items: PermissionItem[]): Draft => Object.fromEntries(items.map((i) => [i.permission, i.scope]));
const toItems = (draft: Draft): PermissionItem[] =>
  Object.entries(draft)
    .filter(([, scope]) => scope)
    .map(([permission, scope]) => ({ permission, scope }));
/** What `next` grants beyond `prev`: added, or at a wider scope. */
const widened = (prev: Draft, next: Draft) => Object.keys(next).filter((k) => next[k] && (!prev[k] || RANK[next[k]] > RANK[prev[k]]));
const personName = (p: { full_name: string | null; email?: string | null; primary_email?: string | null }) =>
  p.full_name ?? p.email ?? p.primary_email ?? "?";

function useCatalog() {
  const catalog = useApi<PermissionCatalog>("/admin/permissions/catalog");
  const byKey = useMemo(() => {
    const out: Record<string, CatalogPermission & { group: string }> = {};
    for (const g of catalog.data?.groups ?? []) for (const p of g.permissions) out[p.key] = { ...p, group: g.name };
    return out;
  }, [catalog.data]);
  const scopeLabel = (scope: string) =>
    scope === "flag" ? "Có" : (catalog.data?.scopes.find((s) => s.key === scope)?.label ?? scope);
  return { catalog, byKey, scopeLabel };
}

/** "run an action, show its outcome", as the other Admin tabs do. */
function useRun() {
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<unknown>, done: string): Promise<boolean> {
    setBusy(true);
    try {
      await action();
      setMessage(done);
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { message, setMessage, busy, run };
}

type Sub = "sets" | "preview";

/** Admin › Phân quyền (docs/specs/21 phase C): the permission sets and one person's effective rights. */
export function PermissionsAdmin({ nav }: { nav: SubNav }) {
  const sets = useApi<PermissionSetRow[]>("/admin/permission-sets");
  const tabs: SubTab<Sub>[] = [
    { key: "sets", label: "Bộ quyền", count: sets.data?.length, hint: "Tạo, sửa, nhân bản và gán bộ quyền" },
    { key: "preview", label: "Quyền hiệu lực", hint: "Xem trước một người có quyền gì, từ bộ nào" },
  ];
  const sub = pickSub(nav, tabs, "sets");
  return (
    <>
      <SubTabs id="perm" label="Phân quyền" tabs={tabs} value={sub} onChange={nav.onSub} />
      <SubPanel id="perm" tab={sub}>
        {sub === "sets" && <SetsView />}
        {sub === "preview" && <EffectiveView />}
      </SubPanel>
    </>
  );
}

function SetsView() {
  const sets = useApi<PermissionSetRow[]>("/admin/permission-sets");
  const { catalog, byKey, scopeLabel } = useCatalog();
  const [selected, setSelected] = useState<number | null>(null);
  const [creating, setCreating] = useState<{ from?: PermissionSetRow } | null>(null);
  const { message, setMessage, busy, run } = useRun();
  const rows = sets.data ?? [];
  const current = rows.find((s) => s.id === selected) ?? rows[0];

  if (!sets.data || !catalog.data) return <Loading error={sets.error ?? catalog.error} rows={4} />;

  const create = (name: string, description: string) =>
    run(async () => {
      const made = await api.post<PermissionSetRow>("/admin/permission-sets", {
        name,
        description: description || null,
        ...(creating?.from ? { from_set_id: creating.from.id } : { items: [] }),
      });
      await sets.mutate();
      setSelected(made.id);
      setCreating(null);
    }, creating?.from ? `Đã nhân bản thành "${name}".` : `Đã tạo bộ quyền "${name}".`);

  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      {creating && (
        <NameDialog
          title={creating.from ? `Nhân bản "${creating.from.name}"` : "Tạo bộ quyền"}
          initial={creating.from ? `${creating.from.name} (bản sao)` : ""}
          busy={busy}
          onSave={create}
          onCancel={() => setCreating(null)}
        />
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 240px", maxWidth: "100%", minWidth: 0 }}>
          <Card
            title="Bộ quyền"
            collapsible={false}
            flush
            actions={
              <button type="button" className="sm" onClick={() => setCreating({})}>
                <Icon name="plus" size={14} />
                Tạo bộ
              </button>
            }
          >
            <div role="listbox" aria-label="Bộ quyền" style={{ display: "grid", gap: 4, padding: 8 }}>
              {rows.map((s) => {
                const on = current?.id === s.id;
                return (
                  <button
                    key={s.id}
                    type="button"
                    role="option"
                    aria-selected={on}
                    onClick={() => setSelected(s.id)}
                    style={{
                      display: "grid",
                      gap: 4,
                      textAlign: "left",
                      padding: "8px 10px",
                      borderRadius: 8,
                      border: `1px solid ${on ? "var(--primary)" : "transparent"}`,
                      background: on ? "var(--primary-soft)" : "transparent",
                      color: "var(--text)",
                      height: "auto",
                      justifyContent: "stretch",
                    }}
                  >
                    <span style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                      <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.name}</strong>
                      <span className="muted small nowrap">
                        {s.member_count.toLocaleString("vi-VN")} người · {s.items.length} quyền
                      </span>
                    </span>
                    {s.auto_role ? (
                      <span className="small muted">Tự gán theo vai trò: {ROLE_LABEL[s.auto_role] ?? s.auto_role}</span>
                    ) : (
                      <span className="small muted">Gán tay</span>
                    )}
                  </button>
                );
              })}
            </div>
          </Card>
        </div>
        <div style={{ flex: "3 1 520px", maxWidth: "100%", minWidth: 0 }}>
          {current ? (
            <SetEditor
              key={current.id}
              set={current}
              catalog={catalog.data}
              byKey={byKey}
              scopeLabel={scopeLabel}
              onSaved={async () => {
                await sets.mutate();
              }}
              onClone={() => setCreating({ from: current })}
              onDeleted={async () => {
                setSelected(null);
                await sets.mutate();
              }}
              notify={setMessage}
            />
          ) : (
            <EmptyState icon="settings" title="Chưa có bộ quyền nào" />
          )}
        </div>
      </div>
    </>
  );
}

function SetEditor({
  set,
  catalog,
  byKey,
  scopeLabel,
  onSaved,
  onClone,
  onDeleted,
  notify,
}: {
  set: PermissionSetRow;
  catalog: PermissionCatalog;
  byKey: Record<string, CatalogPermission & { group: string }>;
  scopeLabel: (scope: string) => string;
  onSaved: () => Promise<void>;
  onClone: () => void;
  onDeleted: () => Promise<void>;
  notify: (m: string | null) => void;
}) {
  const original = useMemo(() => toDraft(set.items), [set.items]);
  const [draft, setDraft] = useState<Draft>(original);
  const [description, setDescription] = useState(set.description ?? "");
  const [name, setName] = useState(set.name);
  const [query, setQuery] = useState("");
  const [heldOnly, setHeldOnly] = useState(false);
  const [confirming, setConfirming] = useState<{ keys: string[]; go: () => void } | null>(null);
  const [assigning, setAssigning] = useState(false);
  const [busy, setBusy] = useState(false);

  const dirty =
    JSON.stringify(toItems(draft).sort((a, b) => a.permission.localeCompare(b.permission))) !==
      JSON.stringify(toItems(original).sort((a, b) => a.permission.localeCompare(b.permission))) ||
    description !== (set.description ?? "") ||
    name.trim() !== set.name;

  async function act(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      notify(done);
    } catch (e) {
      notify(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  /** Ask once for the sensitive permissions `keys` grants, then run `go(confirmed)`. */
  function withConfirm(keys: string[], go: (confirmed: boolean) => void) {
    const sensitive = keys.filter((k) => byKey[k]?.sensitive);
    if (sensitive.length) setConfirming({ keys: sensitive, go: () => go(true) });
    else go(false);
  }

  const save = () =>
    withConfirm(widened(original, draft), (confirmed) => {
      setConfirming(null);
      void act(async () => {
        await api.patch(`/admin/permission-sets/${set.id}`, {
          ...(set.builtin ? {} : { name: name.trim() }),
          description: description.trim() || null,
          items: toItems(draft),
          confirm_sensitive: confirmed,
        });
        await onSaved();
      }, `Đã lưu bộ quyền "${name.trim() || set.name}".`);
    });

  const q = query.trim().toLowerCase();
  const groups = catalog.groups
    .map((g) => ({
      ...g,
      permissions: g.permissions.filter(
        (p) => (!q || `${p.label} ${p.key} ${g.name}`.toLowerCase().includes(q)) && (!heldOnly || draft[p.key]),
      ),
    }))
    .filter((g) => g.permissions.length);
  const held = Object.values(draft).filter(Boolean).length;

  return (
    <>
      {confirming && (
        <SensitiveDialog
          keys={confirming.keys}
          byKey={byKey}
          busy={busy}
          onConfirm={confirming.go}
          onCancel={() => setConfirming(null)}
        />
      )}
      {assigning && (
        <AssignDrawer
          set={set}
          busy={busy}
          onClose={() => setAssigning(false)}
          onSave={(ids) => {
            const before = new Set(set.members.map((m) => m.id));
            const added = ids.some((id) => !before.has(id));
            withConfirm(added ? set.items.map((i) => i.permission) : [], (confirmed) => {
              setConfirming(null);
              void act(async () => {
                await api.put(`/admin/permission-sets/${set.id}/users`, { user_ids: ids, confirm_sensitive: confirmed });
                await onSaved();
                setAssigning(false);
              }, `Đã cập nhật người được gán "${set.name}".`);
            });
          }}
        />
      )}
      <Card
        title={set.name}
        subtitle={
          set.auto_role
            ? `Tự gán theo vai trò: ${ROLE_LABEL[set.auto_role] ?? set.auto_role} · ${set.member_count.toLocaleString("vi-VN")} người. Sửa bộ này thì mọi người có vai trò đó đổi theo.`
            : `Gán tay · ${set.member_count.toLocaleString("vi-VN")} người`
        }
        collapsible={false}
        flush
        actions={
          <>
            <button type="button" className="sm ghost" onClick={onClone} disabled={busy}>
              <Icon name="copy" size={14} />
              Nhân bản
            </button>
            {!set.builtin && (
              <>
                <button type="button" className="sm ghost" onClick={() => setAssigning(true)} disabled={busy}>
                  <Icon name="members" size={14} />
                  Gán người
                </button>
                <IconButton
                  icon="remove"
                  label="Xoá bộ quyền"
                  tone="danger"
                  disabled={busy}
                  onClick={() =>
                    window.confirm(
                      `Xoá bộ quyền "${set.name}"? ${set.member_count} người được gán sẽ mất các quyền bộ này cho.`,
                    ) &&
                    void act(async () => {
                      await api.delete(`/admin/permission-sets/${set.id}`);
                      await onDeleted();
                    }, `Đã xoá bộ quyền "${set.name}".`)
                  }
                />
              </>
            )}
          </>
        }
      >
        <div className="adm-pad" style={{ display: "grid", gap: 10 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {!set.builtin && (
              <label className="adm-field" style={{ flex: "1 1 200px" }}>
                <span className="small muted">Tên</span>
                <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
              </label>
            )}
            <label className="adm-field" style={{ flex: "3 1 260px" }}>
              <span className="small muted">Mô tả</span>
              <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} />
            </label>
          </div>
          {!set.builtin && set.members.length > 0 && (
            <div className="chips" style={{ marginBottom: 0 }}>
              {set.members.slice(0, 12).map((m) => (
                <span key={m.id} className="pill">
                  {personName(m)}
                </span>
              ))}
              {set.members.length > 12 && <span className="pill">+{set.members.length - 12}</span>}
            </div>
          )}
          <div className="toolbar" style={{ margin: 0 }}>
            <input
              className="compact"
              type="search"
              placeholder="Tìm quyền"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Tìm quyền"
            />
            <label className="inline-check small">
              <input type="checkbox" checked={heldOnly} onChange={(e) => setHeldOnly(e.target.checked)} />
              Chỉ quyền đang có ({held})
            </label>
            <span className="spacer" />
            {dirty && (
              <button type="button" className="sm ghost" onClick={() => { setDraft(original); setName(set.name); setDescription(set.description ?? ""); }} disabled={busy}>
                Hoàn tác
              </button>
            )}
            <button type="button" className="primary sm" onClick={save} disabled={busy || !dirty || (!set.builtin && !name.trim())}>
              Lưu
            </button>
          </div>
        </div>
        <div className="table-wrap scroll-y" style={{ maxHeight: "min(62vh, 640px)" }}>
          <table>
            <thead>
              <tr>
                <th>Quyền</th>
                <th style={{ width: 150 }}>Phạm vi</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <GroupRows key={g.name} name={g.name}>
                  {g.permissions.map((p) => (
                    <tr key={p.key}>
                      <td>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                          <span>{p.label}</span>
                          {p.sensitive && (
                            <Pill tone="warn" icon="alert" title="Chạm tới dữ liệu cá nhân: cần xác nhận khi cấp">
                              Nhạy cảm
                            </Pill>
                          )}
                        </div>
                        <code className="muted small">{p.key}</code>
                      </td>
                      <td>
                        {p.flag ? (
                          <label className="inline-check small">
                            <input
                              type="checkbox"
                              checked={draft[p.key] === "flag"}
                              onChange={(e) => setDraft({ ...draft, [p.key]: e.target.checked ? "flag" : "" })}
                              aria-label={p.label}
                            />
                            {draft[p.key] === "flag" ? "Có" : "Không"}
                          </label>
                        ) : (
                          <select
                            className="compact"
                            value={draft[p.key] ?? ""}
                            onChange={(e) => setDraft({ ...draft, [p.key]: e.target.value })}
                            aria-label={`Phạm vi: ${p.label}`}
                          >
                            <option value="">Không</option>
                            {p.scopes.map((s) => (
                              <option key={s} value={s}>
                                {scopeLabel(s)}
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                    </tr>
                  ))}
                </GroupRows>
              ))}
              {groups.length === 0 && (
                <tr>
                  <td colSpan={2} className="muted small">
                    Không có quyền nào khớp.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="adm-foot muted small">
          Phạm vi: Của mình ⊂ Team (người mình lead/manage) ⊂ Dự án (dự án mình manage) ⊂ Tất cả (mọi dự án mình truy cập
          được). Quyền hiệu lực là hợp các bộ, lấy phạm vi rộng nhất. Admin luôn có mọi quyền.
        </p>
      </Card>
    </>
  );
}

function GroupRows({ name, children }: { name: string; children: ReactNode }) {
  return (
    <>
      <tr className="group-row">
        <td colSpan={2}>
          <strong className="small">{name}</strong>
        </td>
      </tr>
      {children}
    </>
  );
}

/** Small modal shell, as ConfirmRemove: Escape and the backdrop close it unless busy. */
function Modal({ title, busy, onCancel, children }: { title: string; busy: boolean; onCancel: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);
  return (
    <div className="modal-backdrop" onClick={() => !busy && onCancel()}>
      <div className="modal card" role="dialog" aria-modal="true" aria-labelledby="perm-modal-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2 id="perm-modal-title">{title}</h2>
          <IconButton icon="close" label="Đóng" onClick={onCancel} disabled={busy} />
        </div>
        {children}
      </div>
    </div>
  );
}

function SensitiveDialog({
  keys,
  byKey,
  busy,
  onConfirm,
  onCancel,
}: {
  keys: string[];
  byKey: Record<string, CatalogPermission>;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal title="Cấp quyền nhạy cảm?" busy={busy} onCancel={onCancel}>
      <p>Các quyền sau chạm tới dữ liệu cá nhân (đánh giá, hồ sơ tháng, điểm). Việc cấp được ghi audit log.</p>
      <ul className="modal-list">
        {keys.map((k) => (
          <li key={k}>
            {byKey[k]?.label ?? k} <code className="muted small">{k}</code>
          </li>
        ))}
      </ul>
      <div className="adm-form-foot">
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
          Huỷ
        </button>
        <button type="button" className="primary" onClick={onConfirm} disabled={busy} autoFocus>
          Xác nhận cấp
        </button>
      </div>
    </Modal>
  );
}

function NameDialog({
  title,
  initial,
  busy,
  onSave,
  onCancel,
}: {
  title: string;
  initial: string;
  busy: boolean;
  onSave: (name: string, description: string) => void;
  onCancel: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.select(), []);
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    onSave(String(f.get("name") ?? "").trim(), String(f.get("description") ?? "").trim());
  };
  return (
    <Modal title={title} busy={busy} onCancel={onCancel}>
      <form className="adm-form" onSubmit={submit}>
        <label className="adm-field">
          <span className="small muted">Tên</span>
          <input ref={input} name="name" defaultValue={initial} required maxLength={100} autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả (tuỳ chọn)</span>
          <input name="description" maxLength={500} />
        </label>
        <div className="adm-form-foot">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
            Huỷ
          </button>
          <button type="submit" className="primary" disabled={busy}>
            {initial ? "Nhân bản" : "Tạo"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function AssignDrawer({
  set,
  busy,
  onClose,
  onSave,
}: {
  set: PermissionSetRow;
  busy: boolean;
  onClose: () => void;
  onSave: (ids: number[]) => void;
}) {
  const users = useApi<Person[]>("/users");
  const [chosen, setChosen] = useState<Set<number>>(() => new Set(set.members.map((m) => m.id)));
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const people = (users.data ?? []).filter((u) => u.active !== false && !u.is_admin);
  const shown = people.filter((u) => !q || `${u.full_name ?? ""} ${u.primary_email ?? ""}`.toLowerCase().includes(q));
  const toggle = (id: number) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  };
  return (
    <Drawer
      title={`Gán "${set.name}"`}
      width={480}
      onClose={onClose}
      actions={
        <button type="button" className="primary sm" disabled={busy || !users.data} onClick={() => onSave([...chosen])}>
          Lưu ({chosen.size})
        </button>
      }
    >
      {!users.data ? (
        <Loading error={users.error} />
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          <div className="toolbar" style={{ margin: 0 }}>
            <input
              className="compact"
              type="search"
              placeholder="Tìm người"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Tìm người"
              autoFocus
            />
            <button type="button" className="sm ghost" onClick={() => setChosen(new Set([...chosen, ...shown.map((u) => u.id)]))}>
              Chọn tất cả ({shown.length})
            </button>
            <button type="button" className="sm ghost" onClick={() => setChosen(new Set())}>
              Bỏ chọn
            </button>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Admin luôn có mọi quyền nên không cần gán.
          </p>
          <div style={{ display: "grid", gap: 2 }}>
            {shown.map((u) => (
              <label key={u.id} className="inline-check" style={{ padding: "6px 4px" }}>
                <input type="checkbox" checked={chosen.has(u.id)} onChange={() => toggle(u.id)} />
                <span>
                  {personName(u)}
                  {u.full_name && u.primary_email && <span className="muted small"> · {u.primary_email}</span>}
                </span>
              </label>
            ))}
            {shown.length === 0 && <p className="muted small">Không có ai khớp.</p>}
          </div>
        </div>
      )}
    </Drawer>
  );
}

/** "Quyền hiệu lực của một người": pick a person, see each right and the set it comes from. */
function EffectiveView() {
  const users = useApi<Person[]>("/users");
  const [userId, setUserId] = useState<number | null>(null);
  const preview = useApi<UserPermissions>(userId ? `/admin/users/${userId}/permissions` : null);
  const { catalog, byKey, scopeLabel } = useCatalog();
  const data = preview.data;
  const held = new Map((data?.permissions ?? []).map((p) => [p.key, p]));
  const total = Object.keys(byKey).length;
  return (
    <Card
      title="Quyền hiệu lực của một người"
      subtitle="Hợp các bộ theo vai trò và các bộ được gán; mỗi quyền lấy phạm vi rộng nhất."
      collapsible={false}
      flush
    >
      <div className="toolbar adm-pad">
        <select
          className="compact"
          value={userId ?? ""}
          onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}
          aria-label="Chọn người"
          disabled={!users.data}
        >
          <option value="">Chọn người…</option>
          {(users.data ?? [])
            .filter((u) => u.active !== false)
            .map((u) => (
              <option key={u.id} value={u.id}>
                {personName(u)}
              </option>
            ))}
        </select>
        {data && (
          <span className="chips" style={{ margin: 0 }}>
            {data.is_admin ? (
              <Pill tone="primary">Admin: mọi quyền</Pill>
            ) : (
              data.sets.map((s) => (
                <Pill key={s.id} tone={s.via === "given" ? "info" : undefined} title={s.via === "role" ? "Theo vai trò" : "Được gán"}>
                  {s.name}
                </Pill>
              ))
            )}
            <span className="muted small">
              {data.permissions.length}/{total} quyền
            </span>
          </span>
        )}
      </div>
      {!userId ? (
        <EmptyState icon="user" title="Chọn một người để xem trước quyền" />
      ) : !data || !catalog.data ? (
        <div className="adm-pad">
          <Loading error={preview.error ?? catalog.error} />
        </div>
      ) : !data.active ? (
        <EmptyState icon="info" title="Người này đã ngừng hoạt động: không có quyền nào" />
      ) : (
        <div className="table-wrap scroll-y" style={{ maxHeight: "min(66vh, 680px)" }}>
          <table>
            <thead>
              <tr>
                <th>Quyền</th>
                <th>Phạm vi</th>
                <th>Từ bộ</th>
              </tr>
            </thead>
            <tbody>
              {catalog.data.groups.map((g) => (
                <GroupRows3 key={g.name} name={g.name}>
                  {g.permissions.map((p) => {
                    const row = held.get(p.key);
                    return (
                      <tr key={p.key} className={row ? undefined : "muted"}>
                        <td>
                          {p.label} {p.sensitive && <Icon name="alert" size={12} />}
                        </td>
                        <td className="nowrap">{row ? <strong>{scopeLabel(row.scope)}</strong> : "Không"}</td>
                        <td className="small">
                          {row?.sources.map((s, i) => (
                            <span key={`${s.set_id}-${s.scope}`}>
                              {i > 0 && ", "}
                              <span className={i === 0 ? undefined : "muted"}>
                                {s.name}
                                {p.flag ? "" : ` (${scopeLabel(s.scope)})`}
                              </span>
                            </span>
                          ))}
                        </td>
                      </tr>
                    );
                  })}
                </GroupRows3>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function GroupRows3({ name, children }: { name: string; children: ReactNode }) {
  return (
    <>
      <tr className="group-row">
        <td colSpan={3}>
          <strong className="small">{name}</strong>
        </td>
      </tr>
      {children}
    </>
  );
}
