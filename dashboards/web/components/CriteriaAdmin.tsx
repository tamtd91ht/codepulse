"use client";

import { useMemo, useState } from "react";

import type { SubNav } from "@/components/AdminTabs";
import { CriteriaEditor, type CriteriaSeed } from "@/components/CriteriaEditor";
import { Modal, Swatch, WeightBar, useRefreshSets } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Card, EmptyState, Loading, Message, Pill, Segmented } from "@/components/ui";
import { ApiError, api, can, useApi } from "@/lib/api";
import { DEFAULT_META, SET_NAME_MAX } from "@/lib/criteria";
import type { CriteriaMeta, CriteriaSet, Me } from "@/lib/types";

type Filter = "active" | "archived";
type CreateMode = "template" | "blank" | "clone";

/**
 * Admin › Bộ tiêu chí (docs/specs/22 §7): the sets as compact cards, and the editor of one set
 * (`#criteria/12`) or of a new one (`#criteria/new`) in the same tab.
 */
export function CriteriaAdmin({ nav, me }: { nav: SubNav; me: Me | undefined }) {
  const meta = useApi<CriteriaMeta>("/criteria/meta");
  const [seed, setSeed] = useState<CriteriaSeed | null>(null);
  const canManage = can(me, "criteria.manage");
  const canAssign = can(me, "criteria.assign");
  const m = meta.data ?? DEFAULT_META;

  const sub = nav.sub ?? "";
  if (sub === "new" || /^\d+$/.test(sub)) {
    const id = sub === "new" ? null : Number(sub);
    return (
      <CriteriaEditor
        key={sub}
        setId={id}
        seed={id === null ? seed : null}
        meta={m}
        canManage={canManage}
        canAssign={canAssign}
        onBack={() => nav.onSub("")}
        onCreated={(newId) => {
          setSeed(null);
          nav.onSub(String(newId));
        }}
      />
    );
  }
  return (
    <CriteriaList
      meta={m}
      metaError={meta.error}
      canManage={canManage}
      onOpen={(id) => nav.onSub(String(id))}
      onNew={(s) => {
        setSeed(s);
        nav.onSub("new");
      }}
    />
  );
}

function CriteriaList({
  meta,
  metaError,
  canManage,
  onOpen,
  onNew,
}: {
  meta: CriteriaMeta;
  metaError?: Error;
  canManage: boolean;
  onOpen: (id: number) => void;
  onNew: (seed: CriteriaSeed) => void;
}) {
  const [filter, setFilter] = useState<Filter>("active");
  const [query, setQuery] = useState("");
  const sets = useApi<CriteriaSet[]>(`/criteria-sets?archived=${filter === "archived"}`);
  const [creating, setCreating] = useState<{ mode: CreateMode; from?: number } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = useRefreshSets();

  const q = query.trim().toLowerCase();
  const shown = (sets.data ?? []).filter(
    (s) =>
      !q ||
      `${s.name} ${s.description ?? ""} ${(s.current_version?.components ?? []).map((c) => c.name).join(" ")}`
        .toLowerCase()
        .includes(q),
  );

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      await refresh();
      setMessage(done);
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      {metaError && !(metaError instanceof ApiError && metaError.status === 404) && (
        <Message text={`Lỗi: không tải được danh mục loại finding và mẫu (${metaError.message}).`} />
      )}
      <Card
        title="Bộ tiêu chí"
        subtitle="Mỗi bộ chia điểm của một thay đổi thành các thành phần có trọng số. Gán bộ cho dự án ở Dự án & Repo; điểm 0–100 và kết luận merge vẫn giữ nguyên."
        collapsible={false}
        flush
        actions={
          canManage && (
            <button type="button" className="primary sm" onClick={() => setCreating({ mode: "template" })}>
              <Icon name="plus" size={14} />
              Tạo bộ
            </button>
          )
        }
      >
        <div className="toolbar adm-pad">
          <input
            className="compact"
            type="search"
            placeholder="Tìm theo tên, mô tả, thành phần"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Tìm bộ tiêu chí"
          />
          <Segmented<Filter>
            label="Trạng thái bộ"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "active", label: "Đang dùng" },
              { value: "archived", label: "Lưu trữ" },
            ]}
          />
          <span className="spacer" />
          {sets.data && (
            <span className="muted small">
              {shown.length === sets.data.length ? `${sets.data.length} bộ` : `${shown.length} / ${sets.data.length} bộ`}
            </span>
          )}
        </div>
        {!sets.data ? (
          <div className="adm-pad">
            <Loading error={sets.error} rows={3} />
          </div>
        ) : sets.data.length === 0 ? (
          filter === "archived" ? (
            <EmptyState icon="inbox" title="Không có bộ nào đang lưu trữ">
              Bộ đã từng dùng không xoá được, chỉ lưu trữ; chúng hiện ở đây và khôi phục được bất cứ lúc nào.
            </EmptyState>
          ) : (
            <EmptyState icon="scale" title="Chưa có bộ tiêu chí nào">
              {canManage ? (
                <>
                  Bấm <b>Tạo bộ</b> để bắt đầu từ một mẫu có sẵn (Chuẩn chung, Bảo mật cao, Sản phẩm mới) hoặc từ trống, rồi gán
                  bộ cho dự án ở Dự án &amp; Repo.
                </>
              ) : (
                "Admin chưa tạo bộ tiêu chí nào."
              )}
            </EmptyState>
          )
        ) : shown.length === 0 ? (
          <p className="muted small adm-pad">Không có bộ nào khớp &ldquo;{query}&rdquo;.</p>
        ) : (
          <div className="crit-grid adm-pad">
            {shown.map((s) => (
              <SetCard
                key={s.id}
                set={s}
                busy={busy}
                canManage={canManage}
                onOpen={() => onOpen(s.id)}
                onClone={() => setCreating({ mode: "clone", from: s.id })}
                onArchive={() =>
                  run(
                    () => api.post(`/criteria-sets/${s.id}/${s.archived ? "unarchive" : "archive"}`),
                    s.archived ? `Đã khôi phục bộ ${s.name}.` : `Đã lưu trữ bộ ${s.name}.`,
                  )
                }
                onDelete={() =>
                  window.confirm(`Xoá hẳn bộ "${s.name}"? Bộ chưa từng được dùng nên không mất điểm nào.`) &&
                  void run(() => api.delete(`/criteria-sets/${s.id}`), `Đã xoá bộ ${s.name}.`)
                }
              />
            ))}
          </div>
        )}
      </Card>
      {creating && (
        <CreateDialog
          meta={meta}
          initial={creating}
          onClose={() => setCreating(null)}
          onSeed={(s) => {
            setCreating(null);
            onNew(s);
          }}
          onCloned={async (set) => {
            setCreating(null);
            await refresh();
            onOpen(set.id);
          }}
        />
      )}
    </>
  );
}

function SetCard({
  set,
  busy,
  canManage,
  onOpen,
  onClone,
  onArchive,
  onDelete,
}: {
  set: CriteriaSet;
  busy: boolean;
  canManage: boolean;
  onOpen: () => void;
  onClone: () => void;
  onArchive: () => void;
  onDelete: () => void;
}) {
  const comps = set.current_version?.components ?? [];
  const primaries = set.projects.filter((p) => p.is_primary).length;
  const primaryHere = primaries > 0;
  const deletable = set.projects.length === 0 && !set.used;
  return (
    <article className={`crit-card${set.archived ? " archived" : ""}`}>
      <div className="crit-card-head">
        <button type="button" className="link crit-card-name" onClick={onOpen}>
          {set.name}
        </button>
        {set.current_version && <span className="crit-ver">v{set.current_version.version}</span>}
        {set.archived && <Pill>Lưu trữ</Pill>}
      </div>
      <p className="crit-card-desc muted small" title={set.description ?? undefined}>
        {set.description || "Chưa có mô tả."}
      </p>
      <WeightBar parts={comps} label={`Trọng số của ${set.name}`} />
      <ul className="crit-legend" aria-label="Thành phần">
        {comps.slice(0, 4).map((c, i) => (
          <li key={c.key}>
            <Swatch index={i} />
            {c.name} <span className="muted">{c.weight}%</span>
          </li>
        ))}
        {comps.length > 4 && <li className="muted">+{comps.length - 4}</li>}
      </ul>
      <div className="crit-card-foot">
        <span className="muted small" title={set.projects.map((p) => p.name).join(", ") || undefined}>
          {set.projects.length ? `${set.projects.length} dự án đang dùng` : "Chưa gán dự án"}
        </span>
        {primaryHere && <Pill tone="primary">Bộ chính ở {primaries} dự án</Pill>}
        <span className="spacer" />
        <IconButton icon="edit" label={canManage ? "Mở trình soạn" : "Xem chi tiết"} onClick={onOpen} />
        {canManage && (
          <>
            <IconButton icon="copy" label="Nhân bản" onClick={onClone} disabled={busy} />
            <IconButton
              icon={set.archived ? "track" : "untrack"}
              label={
                set.archived
                  ? "Khôi phục"
                  : primaryHere
                    ? "Đang là bộ chính ở dự án: đổi bộ chính trước khi lưu trữ"
                    : "Lưu trữ"
              }
              onClick={onArchive}
              disabled={busy || (!set.archived && primaryHere)}
            />
            <IconButton
              icon="remove"
              tone="danger"
              label={deletable ? "Xoá bộ" : "Bộ đã được dùng: chỉ lưu trữ được"}
              onClick={onDelete}
              disabled={busy || !deletable}
            />
          </>
        )}
      </div>
    </article>
  );
}

/** "Tạo bộ": start from a template (§8), from blank, or clone a set that exists. */
function CreateDialog({
  meta,
  initial,
  onClose,
  onSeed,
  onCloned,
}: {
  meta: CriteriaMeta;
  initial: { mode: CreateMode; from?: number };
  onClose: () => void;
  onSeed: (seed: CriteriaSeed) => void;
  onCloned: (set: CriteriaSet) => Promise<void>;
}) {
  const templates = meta.templates;
  const all = useApi<CriteriaSet[]>("/criteria-sets?archived=false");
  const archived = useApi<CriteriaSet[]>("/criteria-sets?archived=true");
  const sources = useMemo(() => [...(all.data ?? []), ...(archived.data ?? [])], [all.data, archived.data]);
  const [mode, setMode] = useState<CreateMode>(initial.mode === "template" && !templates.length ? "blank" : initial.mode);
  const [template, setTemplate] = useState(templates[0]?.key ?? "");
  const [source, setSource] = useState<number | null>(initial.from ?? null);
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chosenTemplate = templates.find((t) => t.key === template) ?? null;
  const chosenSource = sources.find((s) => s.id === source) ?? null;
  const suggested =
    mode === "template" ? (chosenTemplate?.name ?? "") : mode === "clone" ? (chosenSource ? `${chosenSource.name} (bản sao)` : "") : "";
  const shownName = nameTouched ? name : suggested;
  const taken = sources.some((s) => s.name.trim().toLowerCase() === shownName.trim().toLowerCase());
  const ready =
    shownName.trim().length > 0 &&
    !taken &&
    (mode === "blank" || (mode === "template" && !!chosenTemplate) || (mode === "clone" && !!chosenSource));

  async function go() {
    if (!ready) return;
    if (mode === "clone" && chosenSource) {
      setBusy(true);
      setError(null);
      try {
        const set = await api.post<CriteriaSet>(`/criteria-sets/${chosenSource.id}/clone`, { name: shownName.trim() });
        await onCloned(set);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        setBusy(false);
      }
      return;
    }
    if (mode === "template" && chosenTemplate) {
      onSeed({
        name: shownName.trim(),
        description: chosenTemplate.description,
        components: chosenTemplate.components,
        apply_caps: true,
        from_template: chosenTemplate.key,
      });
      return;
    }
    onSeed({ name: shownName.trim(), description: "", components: null, apply_caps: true });
  }

  const options: { value: CreateMode; label: string; hint: string; disabled?: boolean }[] = [
    { value: "template", label: "Từ mẫu", hint: "Bắt đầu nhanh với trọng số gợi ý", disabled: !templates.length },
    { value: "blank", label: "Bộ trống", hint: "Tự đặt thành phần và trọng số" },
    { value: "clone", label: "Nhân bản bộ có sẵn", hint: "Chép thành phần của một bộ đã có", disabled: !sources.length && !!all.data },
  ];

  return (
    <Modal
      title="Tạo bộ tiêu chí"
      onClose={onClose}
      busy={busy}
      width={640}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void go()} disabled={!ready || busy}>
            {busy ? "Đang nhân bản…" : mode === "clone" ? "Nhân bản" : "Mở trình soạn"}
          </button>
        </>
      }
    >
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void go();
        }}
      >
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <fieldset className="crit-choice" aria-label="Cách bắt đầu">
          {options.map((o) => (
            <label key={o.value} className={`crit-choice-item${mode === o.value ? " active" : ""}${o.disabled ? " disabled" : ""}`}>
              <input
                type="radio"
                name="crit-create-mode"
                value={o.value}
                checked={mode === o.value}
                disabled={o.disabled}
                onChange={() => setMode(o.value)}
              />
              <span>
                <strong>{o.label}</strong>
                <span className="muted small">{o.disabled && o.value === "template" ? "Chưa tải được danh sách mẫu" : o.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {mode === "template" && (
          <fieldset className="crit-templates" aria-label="Mẫu có sẵn">
            {templates.map((t) => (
              <label key={t.key} className={`crit-template${template === t.key ? " active" : ""}`}>
                <input
                  type="radio"
                  name="crit-template"
                  value={t.key}
                  checked={template === t.key}
                  onChange={() => setTemplate(t.key)}
                />
                <span className="crit-template-body">
                  <strong>{t.name}</strong>
                  <span className="muted small">{t.description}</span>
                  <WeightBar parts={t.components} label={`Trọng số mẫu ${t.name}`} size="sm" />
                  <span className="crit-template-parts small muted">
                    {t.components.map((c) => `${c.name} ${c.weight}`).join(" · ")}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        )}

        {mode === "blank" && (
          <p className="muted small">
            Bộ trống có hai thành phần chia đôi trọng số; đặt tên, thêm thành phần (2–12), rồi gán từng loại finding vào một
            thành phần ở ma trận.
          </p>
        )}

        {mode === "clone" && (
          <label className="adm-field">
            <span className="small muted">Bộ nguồn</span>
            <select value={source ?? ""} onChange={(e) => setSource(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{all.data ? "Chọn bộ để nhân bản…" : "Đang tải…"}</option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.current_version ? ` · v${s.current_version.version}` : ""}
                  {s.archived ? " · lưu trữ" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="adm-field">
          <span className="small muted">Tên bộ mới</span>
          <input
            value={shownName}
            maxLength={SET_NAME_MAX}
            onChange={(e) => {
              setNameTouched(true);
              setName(e.target.value);
            }}
            placeholder="Ví dụ: Chuẩn OmiCRM"
            aria-invalid={taken || undefined}
            aria-describedby={taken ? "crit-new-name-err" : undefined}
            required
          />
          {taken && (
            <span id="crit-new-name-err" className="error small">
              Đã có bộ tên này; chọn tên khác.
            </span>
          )}
        </label>
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Modal>
  );
}
