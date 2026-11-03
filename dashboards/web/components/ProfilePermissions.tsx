"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { CatalogPermission, PermissionCatalog, PermissionSetRow, UserPermissions } from "@/lib/types";

const RANK: Record<string, number> = { own: 1, team: 2, project: 3, all: 4, flag: 4 };
type Own = Record<string, string>;

/**
 * One person's permissions (docs/specs/21 §9), on their profile and on Admin › Phân quyền:
 * a menu of features on the left; the feature picked shows its permissions to tick (a flag) or
 * to set at a scope. What their roles give stays shown ("đã có từ Lead"), ticks go to their own
 * set; a second tab gives custom sets. Not a long page: one feature at a time.
 */
export function ProfilePermissions({ userId, onChanged }: { userId: number; onChanged?: () => void }) {
  const held = useApi<UserPermissions>(`/admin/users/${userId}/permissions`);
  const own = useApi<Own>(`/admin/users/${userId}/own-permissions`);
  const sets = useApi<PermissionSetRow[]>("/admin/permission-sets");
  const catalog = useApi<PermissionCatalog>("/admin/permissions/catalog");
  const [draft, setDraft] = useState<Own | null>(null);
  const [chosen, setChosen] = useState<Set<number> | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [tab, setTab] = useState<"perms" | "sets">("perms");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ keys: string[]; go: () => void } | null>(null);

  const byKey = useMemo(() => {
    const out: Record<string, CatalogPermission & { group: string }> = {};
    for (const g of catalog.data?.groups ?? []) for (const p of g.permissions) out[p.key] = { ...p, group: g.name };
    return out;
  }, [catalog.data]);
  const scopeLabel = (scope: string) =>
    scope === "flag" ? "Có" : (catalog.data?.scopes.find((s) => s.key === scope)?.label ?? scope);
  const custom = useMemo(() => (sets.data ?? []).filter((s) => !s.builtin && s.auto_role == null), [sets.data]);
  const given = useMemo(() => new Set((held.data?.sets ?? []).filter((s) => s.via === "given").map((s) => s.id)), [held.data]);
  useEffect(() => {
    if (own.data) setDraft({ ...own.data });
  }, [own.data]);
  useEffect(() => {
    if (held.data) setChosen(new Set(given));
  }, [held.data, given]);
  useEffect(() => {
    if (!group && catalog.data?.groups.length) setGroup(catalog.data.groups[0].name);
  }, [catalog.data, group]);

  const loadError = held.error ?? own.error ?? sets.error ?? catalog.error;
  if (!held.data || !own.data || !sets.data || !catalog.data || !draft || !chosen)
    return (
      <p className="small muted" role="status">
        {loadError ? `Không tải được quyền: ${loadError.message}` : "Đang tải quyền…"}
      </p>
    );
  const h = held.data;
  if (h.is_admin)
    return (
      <p className="small">
        <Pill tone="info">Admin</Pill> <span className="muted">Có mọi quyền (theo ADMIN_EMAILS), không cần cấp thêm.</span>
      </p>
    );
  if (!h.active) return <p className="small muted">Tài khoản đang tắt: không có quyền nào.</p>;

  /** What the person holds without their own set: from role sets and custom sets. */
  const personalId = h.sets.find((s) => s.via === "personal")?.id;
  const inherited = new Map<string, { scope: string; from: string }>();
  for (const p of h.permissions) {
    const src = p.sources.filter((s) => s.set_id !== personalId);
    const best = src.sort((a, b) => RANK[b.scope] - RANK[a.scope])[0];
    if (best) inherited.set(p.key, { scope: best.scope, from: best.name });
  }
  const ownBefore = own.data;
  const permDirty = JSON.stringify(Object.entries(draft).filter(([, v]) => v).sort()) !== JSON.stringify(Object.entries(ownBefore).sort());
  const setsDirty = chosen.size !== given.size || [...chosen].some((id) => !given.has(id));
  const dirty = tab === "perms" ? permDirty : setsDirty;
  const groups = catalog.data.groups;
  const current = groups.find((g) => g.name === group) ?? groups[0];
  const heldIn = (name: string) =>
    groups.find((g) => g.name === name)?.permissions.filter((p) => inherited.has(p.key) || draft[p.key]).length ?? 0;

  const run = async (go: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await go();
      setConfirm(null);
      setMessage(ok);
      await Promise.all([held.mutate(), own.mutate(), sets.mutate()]);
      onChanged?.();
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };
  const savePerms = () => {
    const items = Object.fromEntries(Object.entries(draft).filter(([, v]) => v));
    const widened = Object.keys(items).filter((k) => !ownBefore[k] || RANK[items[k]] > RANK[ownBefore[k]]);
    const sensitive = widened.filter((k) => byKey[k]?.sensitive);
    const go = (confirmed: boolean) =>
      void run(() => api.put(`/admin/users/${userId}/own-permissions`, { items, confirm_sensitive: confirmed }), "Đã lưu quyền riêng.");
    if (sensitive.length) setConfirm({ keys: sensitive, go: () => go(true) });
    else go(false);
  };
  const saveSets = () => {
    const added = custom.filter((s) => chosen.has(s.id) && !given.has(s.id));
    const sensitive = [...new Set(added.flatMap((s) => s.items.map((i) => i.permission)).filter((k) => byKey[k]?.sensitive))];
    const go = (confirmed: boolean) =>
      void run(
        () => api.put(`/admin/users/${userId}/permission-sets`, { set_ids: [...chosen], confirm_sensitive: confirmed }),
        "Đã lưu bộ quyền gán thêm.",
      );
    if (sensitive.length) setConfirm({ keys: sensitive, go: () => go(true) });
    else go(false);
  };
  const reset = () => (tab === "perms" ? setDraft({ ...ownBefore }) : setChosen(new Set(given)));

  const tabs: SubTab<"perms" | "sets">[] = [
    { key: "perms", label: "Quyền theo chức năng", count: h.permissions.length, hint: "Tick quyền cho riêng người này" },
    { key: "sets", label: "Bộ quyền", count: h.sets.length, hint: "Bộ theo vai trò và bộ gán thêm" },
  ];

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <SubTabs id={`pp-${userId}`} label="Phân quyền" variant="mini" tabs={tabs} value={tab} onChange={(t) => setTab(t)} />
      <SubPanel id={`pp-${userId}`} tab={tab}>
        {tab === "perms" ? (
          <div className="perm-split">
            <nav className="perm-groups" aria-label="Nhóm chức năng">
              {groups.map((g) => {
                const n = heldIn(g.name);
                const active = g.name === current.name;
                return (
                  <button
                    key={g.name}
                    type="button"
                    className={`perm-group${active ? " active" : ""}`}
                    aria-current={active ? "true" : undefined}
                    onClick={() => setGroup(g.name)}
                  >
                    <span>{g.name}</span>
                    <span className="perm-count" aria-label={`${n} quyền đang có`}>
                      {n}/{g.permissions.length}
                    </span>
                  </button>
                );
              })}
            </nav>
            <div className="perm-panel" role="group" aria-label={`Quyền: ${current.name}`}>
              <div className="perm-panel-head small">
                <strong>{current.name}</strong>
                <span className="muted">Tick để cấp riêng cho người này; quyền đã có theo vai trò hiện kèm nguồn.</span>
              </div>
              <ul className="perm-rows">
                {current.permissions.map((p) => {
                  const base = inherited.get(p.key);
                  const mine = draft[p.key] ?? "";
                  const id = `pp-${userId}-${p.key}`;
                  return (
                    <li key={p.key} className="perm-row">
                      <div className="perm-row-label">
                        <label htmlFor={id}>
                          {p.label} {p.sensitive && <Pill tone="warn">Nhạy cảm</Pill>}
                        </label>
                        {base && (
                          <span className="small muted">
                            Đã có: {scopeLabel(base.scope)} · từ {base.from}
                          </span>
                        )}
                      </div>
                      {p.flag ? (
                        <input
                          id={id}
                          type="checkbox"
                          checked={Boolean(mine) || Boolean(base)}
                          disabled={busy || Boolean(base)}
                          title={base ? `Đã có từ ${base.from}` : undefined}
                          onChange={(e) => setDraft((d) => ({ ...(d ?? {}), [p.key]: e.target.checked ? "flag" : "" }))}
                        />
                      ) : (
                        <select
                          id={id}
                          className="compact"
                          value={mine}
                          disabled={busy}
                          onChange={(e) => setDraft((d) => ({ ...(d ?? {}), [p.key]: e.target.value }))}
                        >
                          <option value="">{base ? `Giữ như vai trò (${scopeLabel(base.scope)})` : "Không"}</option>
                          {p.scopes.map((sc) => (
                            <option key={sc} value={sc} disabled={base ? RANK[sc] <= RANK[base.scope] : false}>
                              {scopeLabel(sc)}
                            </option>
                          ))}
                        </select>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ display: "grid", gap: 6 }}>
              <span className="small" style={{ fontWeight: 600 }}>
                Tự có theo vai trò
              </span>
              <div className="chips" style={{ margin: 0 }}>
                {h.sets.filter((s) => s.via !== "given").map((s) => (
                  <Pill key={s.id} tone={s.via === "personal" ? "info" : undefined}>
                    {s.name}
                  </Pill>
                ))}
              </div>
            </div>
            <fieldset style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 6 }}>
              <legend className="small" style={{ fontWeight: 600 }}>
                Bộ quyền gán thêm
              </legend>
              {custom.length === 0 ? (
                <p className="small muted" style={{ margin: 0 }}>
                  Chưa có bộ quyền tự tạo. Tạo ở{" "}
                  <Link href="/admin#permissions" className="small">
                    Admin › Phân quyền
                  </Link>
                  , hoặc tick quyền riêng ở tab "Quyền theo chức năng".
                </p>
              ) : (
                custom.map((s) => (
                  <label key={s.id} className="small" style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                    <input
                      type="checkbox"
                      checked={chosen.has(s.id)}
                      disabled={busy}
                      onChange={() =>
                        setChosen((prev) => {
                          const next = new Set(prev);
                          if (next.has(s.id)) next.delete(s.id);
                          else next.add(s.id);
                          return next;
                        })
                      }
                    />
                    <span>
                      <strong>{s.name}</strong> <span className="muted">· {s.items.length} quyền</span>
                      {s.items.some((i) => byKey[i.permission]?.sensitive) && (
                        <>
                          {" "}
                          <Pill tone="warn">Nhạy cảm</Pill>
                        </>
                      )}
                      {s.description && (
                        <span className="muted" style={{ display: "block" }}>
                          {s.description}
                        </span>
                      )}
                    </span>
                  </label>
                ))
              )}
            </fieldset>
          </div>
        )}
      </SubPanel>

      {confirm ? (
        <div className="perm-savebar small" role="alertdialog" aria-label="Xác nhận quyền nhạy cảm">
          <span>
            Cấp quyền <strong>nhạy cảm</strong> (dữ liệu từng người): {confirm.keys.map((k) => byKey[k]?.label ?? k).join(", ")}?
          </span>
          <span style={{ display: "flex", gap: 6 }}>
            <button type="button" className="primary sm" disabled={busy} onClick={confirm.go}>
              Xác nhận cấp
            </button>
            <button type="button" className="ghost sm" disabled={busy} onClick={() => setConfirm(null)}>
              Thôi
            </button>
          </span>
        </div>
      ) : (
        dirty && (
          <div className="perm-savebar">
            <span className="small muted">Có thay đổi chưa lưu</span>
            <span style={{ display: "flex", gap: 6 }}>
              <button type="button" className="ghost sm" disabled={busy} onClick={reset}>
                Huỷ
              </button>
              <button type="button" className="primary sm" disabled={busy} onClick={tab === "perms" ? savePerms : saveSets}>
                {busy ? "Đang lưu…" : "Lưu"}
              </button>
            </span>
          </div>
        )
      )}
      {message && (
        <span className="small" role="status">
          {message}
        </span>
      )}
    </div>
  );
}
