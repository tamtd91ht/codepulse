"use client";

import { useEffect, useMemo, useState } from "react";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { IconButton } from "@/components/icons";
import { Alert, EmptyState, Loading, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type {
  ProjectPermCatalog,
  ProjectPermSet,
  ProjectPersonRights,
} from "@/lib/types";

type View = "people" | "sets";

const ROLE_LABEL = (p: ProjectPersonRights) =>
  p.admin_role === "manager"
    ? "Manager dự án"
    : p.admin_role === "lead"
      ? "Lead dự án"
      : p.role === "manage"
        ? "Quản lý"
        : "Xem";

/**
 * Phân quyền trong một dự án (docs/specs/21 §13), in the dự án's drawer for its Manager dự án:
 * "Người & quyền" ticks rights for one person of the dự án, "Bộ quyền" makes the dự án's own
 * sets. Every right counts over this dự án only; only what the caller holds here can be handed
 * out (the others show greyed out, with the reason).
 */
export function ProjectPermissions({
  projectId,
  projectName,
}: {
  projectId: number;
  projectName: string;
}) {
  const base = `/business-projects/${projectId}`;
  const catalog = useApi<ProjectPermCatalog>(`${base}/permissions/catalog`);
  const people = useApi<ProjectPersonRights[]>(`${base}/permissions/people`);
  const sets = useApi<ProjectPermSet[]>(`${base}/permission-sets`);
  const [view, setView] = useState<View>("people");
  const error = catalog.error ?? people.error ?? sets.error;
  if (!catalog.data || !people.data || !sets.data)
    return <Loading error={error} rows={4} />;
  const refresh = async () => {
    await Promise.all([people.mutate(), sets.mutate()]);
  };
  const tabs: SubTab<View>[] = [
    {
      key: "people",
      label: "Quyền từng người",
      count: people.data.length,
      hint: "Tick quyền cho một người trong dự án",
    },
    {
      key: "sets",
      label: "Bộ quyền của dự án",
      count: sets.data.length,
      hint: "Bộ quyền chỉ dùng trong dự án này",
    },
  ];
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <p className="muted small" style={{ margin: 0 }}>
        Quyền đặt ở đây chỉ có hiệu lực trong <b>{projectName}</b>, và chỉ với
        người đang có quyền vào dự án. Bạn chỉ cấp được quyền mình đang có ở dự
        án này.
      </p>
      <SubTabs
        id={`pperm-${projectId}`}
        label="Phân quyền trong dự án"
        variant="mini"
        tabs={tabs}
        value={view}
        onChange={setView}
      />
      <SubPanel id={`pperm-${projectId}`} tab={view}>
        {view === "people" ? (
          <PeopleView
            base={base}
            catalog={catalog.data}
            people={people.data}
            sets={sets.data}
            onSaved={refresh}
          />
        ) : (
          <SetsView
            base={base}
            catalog={catalog.data}
            people={people.data}
            sets={sets.data}
            onSaved={refresh}
          />
        )}
      </SubPanel>
    </div>
  );
}

/* ---- shared ------------------------------------------------------------------------------- */

function useSave(onSaved: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);
  const run = async (
    go: () => Promise<unknown>,
    ok: string,
  ): Promise<boolean> => {
    setBusy(true);
    setResult(null);
    try {
      await go();
      await onSaved();
      setResult({ tone: "success", text: ok });
      return true;
    } catch (e) {
      setResult({
        tone: "error",
        text: e instanceof ApiError ? e.message : String(e),
      });
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, result, setResult, run };
}

/** The rights of a dự án's set, grouped by feature: groups on the left, ticks on the right. */
function KeyPicker({
  catalog,
  value,
  inherited,
  disabled,
  onChange,
  idPrefix,
}: {
  catalog: ProjectPermCatalog;
  value: Set<string>;
  /** Already given through another set: shown ticked, not changed here. */
  inherited?: Map<string, string>;
  disabled: boolean;
  onChange: (next: Set<string>) => void;
  idPrefix: string;
}) {
  const [group, setGroup] = useState(catalog.groups[0]?.name ?? "");
  const current =
    catalog.groups.find((g) => g.name === group) ?? catalog.groups[0];
  if (!current)
    return (
      <p className="muted small">
        Không có quyền nào cấp được trong phạm vi dự án.
      </p>
    );
  const toggle = (key: string, on: boolean) => {
    const next = new Set(value);
    if (on) next.add(key);
    else next.delete(key);
    onChange(next);
  };
  return (
    <div className="perm-split">
      <nav className="perm-groups" aria-label="Nhóm chức năng">
        {catalog.groups.map((g) => {
          const n = g.permissions.filter(
            (p) => value.has(p.key) || inherited?.has(p.key),
          ).length;
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
      <div
        className="perm-panel"
        role="group"
        aria-label={`Quyền: ${current.name}`}
      >
        <div className="perm-panel-head small">
          <strong>{current.name}</strong>
          <span className="muted">
            Mỗi quyền tick ở đây áp cho cả dự án này.
          </span>
        </div>
        <ul className="perm-rows">
          {current.permissions.map((p) => {
            const id = `${idPrefix}-${p.key}`;
            const from = inherited?.get(p.key);
            return (
              <li key={p.key} className="perm-row">
                <div className="perm-row-label">
                  <label htmlFor={id}>
                    {p.label} {p.sensitive && <Pill tone="warn">Nhạy cảm</Pill>}
                  </label>
                  {from && (
                    <span className="small muted">Đã có · từ {from}</span>
                  )}
                  {!p.grantable && (
                    <span className="small muted">
                      Bạn không có quyền này ở dự án nên không cấp / thu được.
                    </span>
                  )}
                </div>
                <input
                  id={id}
                  type="checkbox"
                  checked={value.has(p.key) || Boolean(from)}
                  disabled={disabled || !p.grantable || Boolean(from)}
                  onChange={(e) => toggle(p.key, e.target.checked)}
                />
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function SaveBar({
  dirty,
  busy,
  confirm,
  onSave,
  onReset,
  onConfirm,
  onCancelConfirm,
  labelOf,
}: {
  dirty: boolean;
  busy: boolean;
  confirm: string[] | null;
  onSave: () => void;
  onReset: () => void;
  onConfirm: () => void;
  onCancelConfirm: () => void;
  labelOf: (key: string) => string;
}) {
  if (confirm)
    return (
      <div
        className="perm-savebar small"
        role="alertdialog"
        aria-label="Xác nhận quyền nhạy cảm"
      >
        <span>
          Cấp quyền <strong>nhạy cảm</strong> (dữ liệu từng người):{" "}
          {confirm.map(labelOf).join(", ")}?
        </span>
        <span style={{ display: "flex", gap: 6 }}>
          <button
            type="button"
            className="primary sm"
            disabled={busy}
            onClick={onConfirm}
          >
            Xác nhận cấp
          </button>
          <button
            type="button"
            className="ghost sm"
            disabled={busy}
            onClick={onCancelConfirm}
          >
            Thôi
          </button>
        </span>
      </div>
    );
  if (!dirty) return null;
  return (
    <div className="perm-savebar">
      <span className="small muted">Có thay đổi chưa lưu</span>
      <span style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          className="ghost sm"
          disabled={busy}
          onClick={onReset}
        >
          Huỷ
        </button>
        <button
          type="button"
          className="primary sm"
          disabled={busy}
          onClick={onSave}
        >
          {busy ? "Đang lưu…" : "Lưu"}
        </button>
      </span>
    </div>
  );
}

const same = (a: Set<string | number>, b: Set<string | number>) =>
  a.size === b.size && [...a].every((x) => b.has(x));

function useLabels(catalog: ProjectPermCatalog) {
  return useMemo(() => {
    const out = new Map<string, { label: string; sensitive: boolean }>();
    for (const g of catalog.groups)
      for (const p of g.permissions)
        out.set(p.key, { label: p.label, sensitive: p.sensitive });
    return out;
  }, [catalog]);
}

/* ---- people ------------------------------------------------------------------------------- */

function PeopleView({
  base,
  catalog,
  people,
  sets,
  onSaved,
}: {
  base: string;
  catalog: ProjectPermCatalog;
  people: ProjectPersonRights[];
  sets: ProjectPermSet[];
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const person = people.find((p) => p.user.id === open) ?? null;
  if (people.length === 0)
    return (
      <EmptyState icon="members" title="Chưa có ai trong dự án">
        Thêm người ở tab Quyền truy cập trước.
      </EmptyState>
    );
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Người</th>
              <th>Vai trò</th>
              <th>Quyền trong dự án</th>
              <th aria-label="Thao tác" />
            </tr>
          </thead>
          <tbody>
            {people.map((p) => (
              <tr
                key={p.user.id}
                className={p.user.id === open ? "selected" : undefined}
              >
                <td>
                  <strong>
                    {p.user.full_name ?? p.user.email ?? `#${p.user.id}`}
                  </strong>
                  {p.user.email && (
                    <div className="small muted">{p.user.email}</div>
                  )}
                </td>
                <td>
                  <Pill tone={p.admin_role ? "primary" : undefined}>
                    {ROLE_LABEL(p)}
                  </Pill>
                </td>
                <td className="small">
                  {p.effective.length ? (
                    `${p.effective.length} quyền`
                  ) : (
                    <span className="muted">Không có thêm</span>
                  )}
                </td>
                <td style={{ width: 1 }}>
                  <IconButton
                    icon="key"
                    size={14}
                    label={
                      p.locked ??
                      `Đặt quyền cho ${p.user.full_name ?? p.user.email ?? p.user.id}`
                    }
                    disabled={p.locked !== null}
                    aria-pressed={p.user.id === open}
                    onClick={() =>
                      setOpen(p.user.id === open ? null : p.user.id)
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {person && (
        <PersonEditor
          key={person.user.id}
          base={base}
          catalog={catalog}
          person={person}
          sets={sets}
          onSaved={onSaved}
        />
      )}
    </div>
  );
}

function PersonEditor({
  base,
  catalog,
  person,
  sets,
  onSaved,
}: {
  base: string;
  catalog: ProjectPermCatalog;
  person: ProjectPersonRights;
  sets: ProjectPermSet[];
  onSaved: () => Promise<void>;
}) {
  const labels = useLabels(catalog);
  const [keys, setKeys] = useState(() => new Set(person.permissions));
  const [chosen, setChosen] = useState(() => new Set(person.set_ids));
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const { busy, result, setResult, run } = useSave(onSaved);
  useEffect(() => {
    setKeys(new Set(person.permissions));
    setChosen(new Set(person.set_ids));
  }, [person]);
  // Rights the chosen sets already give: shown ticked with their set.
  const inherited = useMemo(() => {
    const out = new Map<string, string>();
    for (const s of sets)
      if (chosen.has(s.id))
        for (const k of s.permissions) if (!out.has(k)) out.set(k, s.name);
    return out;
  }, [sets, chosen]);
  const dirty =
    !same(keys, new Set(person.permissions)) ||
    !same(chosen, new Set(person.set_ids));
  const name =
    person.user.full_name ?? person.user.email ?? `#${person.user.id}`;
  const save = (confirmed: boolean) => {
    const before = new Set([
      ...person.permissions,
      ...sets
        .filter((s) => person.set_ids.includes(s.id))
        .flatMap((s) => s.permissions),
    ]);
    const after = new Set([...keys, ...inherited.keys()]);
    const sensitive = [...after].filter(
      (k) => !before.has(k) && labels.get(k)?.sensitive,
    );
    if (sensitive.length && !confirmed) {
      setConfirm(sensitive);
      return;
    }
    setConfirm(null);
    void run(
      () =>
        api.put(`${base}/users/${person.user.id}/permissions`, {
          set_ids: [...chosen],
          permissions: [...keys],
          confirm_sensitive: confirmed,
        }),
      `Đã lưu quyền của ${name} trong dự án.`,
    );
  };
  return (
    <section
      className="card"
      aria-label={`Quyền của ${name} trong dự án`}
      style={{ display: "grid", gap: 10 }}
    >
      <strong>Quyền của {name}</strong>
      {result && (
        <Alert tone={result.tone} onClose={() => setResult(null)}>
          {result.text}
        </Alert>
      )}
      {sets.length > 0 && (
        <fieldset
          style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 6 }}
        >
          <legend className="small" style={{ fontWeight: 600 }}>
            Bộ quyền của dự án
          </legend>
          <div className="chips" style={{ margin: 0 }}>
            {sets.map((s) => (
              <label key={s.id} className="inline-check small">
                <input
                  type="checkbox"
                  checked={chosen.has(s.id)}
                  disabled={busy}
                  onChange={(e) =>
                    setChosen((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) next.add(s.id);
                      else next.delete(s.id);
                      return next;
                    })
                  }
                />
                {s.name}{" "}
                <span className="muted">· {s.permissions.length} quyền</span>
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <KeyPicker
        catalog={catalog}
        value={keys}
        inherited={inherited}
        disabled={busy}
        onChange={setKeys}
        idPrefix={`pp-${person.user.id}`}
      />
      <SaveBar
        dirty={dirty}
        busy={busy}
        confirm={confirm}
        onSave={() => save(false)}
        onReset={() => {
          setKeys(new Set(person.permissions));
          setChosen(new Set(person.set_ids));
        }}
        onConfirm={() => save(true)}
        onCancelConfirm={() => setConfirm(null)}
        labelOf={(k) => labels.get(k)?.label ?? k}
      />
    </section>
  );
}

/* ---- sets --------------------------------------------------------------------------------- */

function SetsView({
  base,
  catalog,
  people,
  sets,
  onSaved,
}: {
  base: string;
  catalog: ProjectPermCatalog;
  people: ProjectPersonRights[];
  sets: ProjectPermSet[];
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState<number | "new" | null>(null);
  const { busy, result, setResult, run } = useSave(onSaved);
  const current =
    typeof open === "number" ? (sets.find((s) => s.id === open) ?? null) : null;
  const remove = (s: ProjectPermSet) => {
    if (
      !window.confirm(
        `Xoá bộ quyền "${s.name}"? ${s.members.length} người sẽ mất các quyền bộ này cho.`,
      )
    )
      return;
    void run(
      () => api.delete(`${base}/permission-sets/${s.id}`),
      `Đã xoá bộ quyền ${s.name}.`,
    ).then((ok) => ok && setOpen(null));
  };
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="toolbar" style={{ margin: 0 }}>
        <span className="small muted">
          Bộ quyền gom sẵn một nhóm quyền để gán nhanh cho nhiều người trong dự
          án.
        </span>
        <span className="spacer" />
        <button type="button" onClick={() => setOpen("new")} disabled={busy}>
          Tạo bộ quyền
        </button>
      </div>
      {result && (
        <Alert tone={result.tone} onClose={() => setResult(null)}>
          {result.text}
        </Alert>
      )}
      {sets.length === 0 ? (
        <p className="muted small">Chưa có bộ quyền nào của dự án.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Bộ quyền</th>
                <th>Quyền</th>
                <th>Người</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {sets.map((s) => (
                <tr
                  key={s.id}
                  className={s.id === open ? "selected" : undefined}
                >
                  <td>
                    <strong>{s.name}</strong>
                    {s.description && (
                      <div className="small muted">{s.description}</div>
                    )}
                  </td>
                  <td className="small">{s.permissions.length}</td>
                  <td className="small">{s.members.length}</td>
                  <td style={{ width: 1, whiteSpace: "nowrap" }}>
                    <IconButton
                      icon="edit"
                      size={14}
                      label={`Sửa ${s.name}`}
                      onClick={() => setOpen(s.id)}
                    />
                    <IconButton
                      icon="remove"
                      size={14}
                      label={`Xoá ${s.name}`}
                      disabled={busy}
                      onClick={() => remove(s)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open !== null && (open === "new" || current) && (
        <SetEditor
          key={String(open)}
          base={base}
          catalog={catalog}
          people={people}
          set={current}
          onSaved={onSaved}
          onDone={(id) => setOpen(id)}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function SetEditor({
  base,
  catalog,
  people,
  set,
  onSaved,
  onDone,
  onClose,
}: {
  base: string;
  catalog: ProjectPermCatalog;
  people: ProjectPersonRights[];
  set: ProjectPermSet | null;
  onSaved: () => Promise<void>;
  onDone: (id: number) => void;
  onClose: () => void;
}) {
  const labels = useLabels(catalog);
  const [name, setName] = useState(set?.name ?? "");
  const [description, setDescription] = useState(set?.description ?? "");
  const [keys, setKeys] = useState(() => new Set(set?.permissions ?? []));
  const [members, setMembers] = useState(
    () => new Set(set?.members.map((m) => m.id) ?? []),
  );
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const { busy, result, setResult, run } = useSave(onSaved);
  const startKeys = new Set(set?.permissions ?? []);
  const startMembers = new Set(set?.members.map((m) => m.id) ?? []);
  const dirty =
    !set ||
    name.trim() !== set.name ||
    (description.trim() || null) !== (set.description ?? null) ||
    !same(keys, startKeys) ||
    !same(members, startMembers);
  const save = (confirmed: boolean) => {
    const membersAdded = [...members].some((m) => !startMembers.has(m));
    const newKeys = [...keys].filter((k) => !startKeys.has(k) || membersAdded);
    const sensitive = [
      ...new Set(newKeys.filter((k) => labels.get(k)?.sensitive)),
    ];
    if (sensitive.length && !confirmed) {
      setConfirm(sensitive);
      return;
    }
    setConfirm(null);
    void run(
      async () => {
        const body = {
          name: name.trim(),
          description: description.trim() || null,
          permissions: [...keys],
          confirm_sensitive: confirmed,
        };
        const saved = set
          ? await api.patch<ProjectPermSet>(
              `${base}/permission-sets/${set.id}`,
              body,
            )
          : await api.post<ProjectPermSet>(`${base}/permission-sets`, body);
        if (!same(members, startMembers))
          await api.put(`${base}/permission-sets/${saved.id}/users`, {
            user_ids: [...members],
            confirm_sensitive: confirmed,
          });
        onDone(saved.id);
      },
      set ? "Đã lưu bộ quyền." : "Đã tạo bộ quyền.",
    );
  };
  return (
    <section
      className="card"
      aria-label={set ? `Sửa bộ quyền ${set.name}` : "Bộ quyền mới"}
      style={{ display: "grid", gap: 10 }}
    >
      <div className="toolbar" style={{ margin: 0 }}>
        <strong>{set ? `Sửa bộ quyền ${set.name}` : "Bộ quyền mới"}</strong>
        <span className="spacer" />
        <IconButton icon="close" size={14} label="Đóng" onClick={onClose} />
      </div>
      {result && (
        <Alert tone={result.tone} onClose={() => setResult(null)}>
          {result.text}
        </Alert>
      )}
      <div className="grid two" style={{ gap: 10 }}>
        <label className="adm-field">
          Tên
          <input
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="adm-field">
          Mô tả
          <input
            value={description}
            maxLength={500}
            onChange={(e) => setDescription(e.target.value)}
            disabled={busy}
          />
        </label>
      </div>
      <KeyPicker
        catalog={catalog}
        value={keys}
        disabled={busy}
        onChange={setKeys}
        idPrefix={`ps-${set?.id ?? "new"}`}
      />
      <fieldset
        style={{ border: 0, padding: 0, margin: 0, display: "grid", gap: 6 }}
      >
        <legend className="small" style={{ fontWeight: 600 }}>
          Gán cho
        </legend>
        <div className="chips" style={{ margin: 0 }}>
          {people.map((p) => (
            <label
              key={p.user.id}
              className="inline-check small"
              title={p.locked ?? undefined}
            >
              <input
                type="checkbox"
                checked={members.has(p.user.id)}
                disabled={busy || p.locked !== null}
                onChange={(e) =>
                  setMembers((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) next.add(p.user.id);
                    else next.delete(p.user.id);
                    return next;
                  })
                }
              />
              {p.user.full_name ?? p.user.email ?? `#${p.user.id}`}
            </label>
          ))}
        </div>
      </fieldset>
      <SaveBar
        dirty={dirty && name.trim().length > 0}
        busy={busy}
        confirm={confirm}
        onSave={() => save(false)}
        onReset={() => {
          setName(set?.name ?? "");
          setDescription(set?.description ?? "");
          setKeys(new Set(startKeys));
          setMembers(new Set(startMembers));
        }}
        onConfirm={() => save(true)}
        onCancelConfirm={() => setConfirm(null)}
        labelOf={(k) => labels.get(k)?.label ?? k}
      />
    </section>
  );
}
