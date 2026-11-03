"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { ReorderControls, WeightBar, useDragList, useRefreshSets } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { Alert, Loading, Pill } from "@/components/ui";
import { ApiError, api, can, useApi } from "@/lib/api";
import { moveItem } from "@/lib/criteria";
import type { CriteriaSet, Me, ProjectCriteriaSet } from "@/lib/types";

interface Row {
  set_id: number;
  name: string;
  is_primary: boolean;
  version?: number | null;
  archived?: boolean;
}

/**
 * The criteria sets of one dự án (docs/specs/22 §7.3): add or remove (searchable), pick the
 * primary, reorder; removing the primary asks first. Saving replaces the whole list; newly added
 * sets are recomputed in the background by the server.
 */
export function ProjectCriteria({ projectId, projectName }: { projectId: number; projectName: string }) {
  const path = `/business-projects/${projectId}/criteria-sets`;
  const saved = useApi<ProjectCriteriaSet[]>(path);
  const { data: me } = useApi<Me>("/auth/me");
  const editable = can(me, "criteria.assign");
  const all = useApi<CriteriaSet[]>(editable ? "/criteria-sets?archived=false" : null);
  const refreshSets = useRefreshSets();
  const [draft, setDraft] = useState<Row[] | null>(null);
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [announce, setAnnounce] = useState("");

  const fromServer = (list: ProjectCriteriaSet[]): Row[] =>
    [...list]
      .sort((a, b) => a.position - b.position)
      .map((s) => ({ set_id: s.set_id, name: s.name, is_primary: s.is_primary, version: s.version, archived: s.archived }));
  useEffect(() => {
    if (saved.data) setDraft(fromServer(saved.data));
  }, [saved.data]);

  const rows = draft ?? [];
  const byId = useMemo(() => new Map((all.data ?? []).map((s) => [s.id, s])), [all.data]);
  const original = saved.data ? JSON.stringify(fromServer(saved.data).map((r) => [r.set_id, r.is_primary])) : "";
  const dirty = draft !== null && JSON.stringify(rows.map((r) => [r.set_id, r.is_primary])) !== original;
  const candidates = (all.data ?? []).filter((s) => !rows.some((r) => r.set_id === s.id));

  const move = (from: number, to: number) => {
    if (to < 0 || to >= rows.length) return;
    setDraft(moveItem(rows, from, to));
    setAnnounce(`Đã chuyển ${rows[from].name} tới vị trí ${to + 1} / ${rows.length}.`);
  };
  const dragRow = useDragList(move);
  const add = (s: CriteriaSet) => {
    setDraft([...rows, { set_id: s.id, name: s.name, is_primary: rows.length === 0, version: s.current_version?.version }]);
    setAnnounce(`Đã thêm ${s.name}${rows.length === 0 ? " làm bộ chính" : ""}.`);
  };
  const remove = (r: Row) => {
    if (r.is_primary && rows.length > 1) {
      const next = rows.find((x) => x.set_id !== r.set_id);
      if (!window.confirm(`"${r.name}" đang là bộ chính của ${projectName}: chỉ số và báo cáo dùng bộ này. Bỏ nó và đặt "${next?.name}" làm bộ chính?`))
        return;
    } else if (r.is_primary && !window.confirm(`Bỏ bộ chính "${r.name}"? Dự án sẽ không còn điểm theo tiêu chí cho review mới.`)) {
      return;
    }
    const left = rows.filter((x) => x.set_id !== r.set_id);
    if (r.is_primary && left.length) left[0] = { ...left[0], is_primary: true };
    setDraft(left);
    setAnnounce(`Đã bỏ ${r.name}.`);
  };
  const setPrimary = (id: number) => setDraft(rows.map((r) => ({ ...r, is_primary: r.set_id === id })));

  async function save() {
    setWorking(true);
    setResult(null);
    const before = new Set((saved.data ?? []).map((s) => s.set_id));
    const added = rows.filter((r) => !before.has(r.set_id)).length;
    try {
      const next = await api.put<ProjectCriteriaSet[]>(path, {
        sets: rows.map((r) => ({ set_id: r.set_id, is_primary: r.is_primary })),
      });
      await saved.mutate(Array.isArray(next) ? next : undefined, { revalidate: !Array.isArray(next) });
      await refreshSets();
      setResult({
        tone: "success",
        text: `Đã lưu ${rows.length} bộ tiêu chí.${added ? ` Điểm của ${added} bộ mới gán đang được tính lại cho các review cũ.` : ""}`,
      });
    } catch (e) {
      setResult({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setWorking(false);
    }
  }

  // Before 22.2 is deployed the route answers 404: say nothing rather than an error.
  if (saved.error instanceof ApiError && saved.error.status === 404) return null;

  return (
    <>
      <div className="drawer-title-row" style={{ marginBottom: 8 }}>
        <div>
          <h3 style={{ margin: 0 }}>
            Bộ tiêu chí <span className="muted small">{draft !== null && `(${rows.length})`}</span>
          </h3>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Mỗi review của dự án được chấm thêm theo các bộ này. <b>Bộ chính</b> dùng cho chỉ số và báo cáo; thứ tự là thứ
            tự hiển thị. Tạo và sửa bộ ở tab <a href="#criteria">Bộ tiêu chí</a>.
          </p>
        </div>
      </div>
      <p className="visually-hidden" aria-live="polite">
        {announce}
      </p>
      {result && (
        <Alert tone={result.tone} onClose={() => setResult(null)}>
          {result.text}
        </Alert>
      )}
      {draft === null ? (
        <Loading error={saved.error} rows={2} />
      ) : rows.length === 0 ? (
        <p className="muted small">
          Chưa gán bộ nào: review của dự án chỉ có điểm 0–100.{editable ? " Thêm một bộ bên dưới để bắt đầu." : ""}
        </p>
      ) : (
        <ol className="crit-assign" aria-label={`Bộ tiêu chí của ${projectName}`}>
          {rows.map((r, i) => {
            const full = byId.get(r.set_id);
            return (
              <li key={r.set_id} className="crit-assign-row" {...(editable ? dragRow(i) : {})}>
                {editable && rows.length > 1 && <ReorderControls index={i} count={rows.length} name={r.name} onMove={(to) => move(i, to)} />}
                <span className="crit-assign-name">
                  <strong>{r.name}</strong>
                  {r.version ? <span className="crit-ver">v{r.version}</span> : null}
                  {r.archived && <Pill>Lưu trữ</Pill>}
                  {full?.current_version && <WeightBar parts={full.current_version.components} label={`Trọng số ${r.name}`} size="sm" />}
                </span>
                <label className="inline-check small crit-assign-primary">
                  <input
                    type="radio"
                    name={`crit-primary-${projectId}`}
                    checked={r.is_primary}
                    disabled={!editable}
                    onChange={() => setPrimary(r.set_id)}
                  />
                  Bộ chính
                </label>
                {editable && <IconButton icon="close" size={14} label={`Bỏ ${r.name} khỏi dự án`} onClick={() => remove(r)} />}
              </li>
            );
          })}
        </ol>
      )}
      {editable && (
        <div className="toolbar" style={{ margin: "8px 0 0" }}>
          <SetPicker sets={candidates} loading={!all.data} onPick={add} />
          <span className="spacer" />
          {dirty && (
            <button type="button" onClick={() => saved.data && setDraft(fromServer(saved.data))} disabled={working}>
              Hoàn tác
            </button>
          )}
          <button className="primary" type="button" disabled={!dirty || working} onClick={() => void save()}>
            {working ? "Đang lưu…" : "Lưu bộ tiêu chí"}
          </button>
        </div>
      )}
    </>
  );
}

/** A search box listing matching sets (combobox): arrows move, Enter adds, Escape closes. */
function SetPicker({ sets, loading, onPick }: { sets: CriteriaSet[]; loading: boolean; onPick: (s: CriteriaSet) => void }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const q = query.trim().toLowerCase();
  const matches = sets.filter((s) => !q || `${s.name} ${s.description ?? ""}`.toLowerCase().includes(q)).slice(0, 8);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  const pick = (s: CriteriaSet) => {
    onPick(s);
    setQuery("");
    setActive(0);
  };
  return (
    <div className="crit-picker" ref={box}>
      <Icon name="search" size={14} className="crit-picker-icon" />
      <input
        type="search"
        role="combobox"
        aria-expanded={open && matches.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && matches[active] ? `${listId}-${matches[active].id}` : undefined}
        aria-label="Thêm bộ tiêu chí vào dự án"
        placeholder={loading ? "Đang tải bộ tiêu chí…" : sets.length ? "+ Thêm bộ tiêu chí…" : "Đã gán mọi bộ đang dùng"}
        disabled={loading || sets.length === 0}
        value={query}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, matches.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && open && matches[active]) {
            e.preventDefault();
            pick(matches[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {open && matches.length > 0 && (
        <ul className="crit-picker-list" role="listbox" id={listId} aria-label="Bộ tiêu chí">
          {matches.map((s, i) => (
            <li
              key={s.id}
              id={`${listId}-${s.id}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : undefined}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
            >
              <span className="crit-picker-name">
                {s.name}
                {s.current_version && <span className="crit-ver">v{s.current_version.version}</span>}
              </span>
              {s.current_version && <WeightBar parts={s.current_version.components} label={`Trọng số ${s.name}`} size="sm" />}
            </li>
          ))}
        </ul>
      )}
      {open && q && matches.length === 0 && (
        <div className="crit-picker-list empty muted small" role="status">
          Không có bộ nào khớp.
        </div>
      )}
    </div>
  );
}
