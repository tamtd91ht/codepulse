"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SubPanel, SubTabs } from "@/components/AdminTabs";
import { Modal, ReorderControls, Swatch, WeightBar, useDragList, useRefreshSets } from "@/components/CriteriaParts";
import { Breakdown } from "@/components/CriteriaScores";
import { Icon, IconButton } from "@/components/icons";
import { Alert, Card, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import {
  type CritError,
  type DraftComponent,
  diffComponents,
  evenWeights,
  fieldId,
  fromDraft,
  moveItem,
  normalizeWeights,
  SET_NAME_MAX,
  score1,
  slugify,
  toDraft,
  toNum,
  uid,
  uniqueKey,
  useLeaveGuard,
  validate,
  versionChanged,
} from "@/lib/criteria";
import { dateTime } from "@/lib/format";
import type {
  BusinessProject,
  CriteriaComponent,
  CriteriaMeta,
  CriteriaPreviewRow,
  CriteriaSet,
  CriteriaVersion,
} from "@/lib/types";

/** What "Tạo bộ" hands the editor for a set not saved yet. */
export interface CriteriaSeed {
  name: string;
  description: string;
  /** null: a blank set (two halves, categories left for the admin to place). */
  components: CriteriaComponent[] | null;
  apply_caps: boolean;
  from_template?: string;
}

type EditorTab = "edit" | "versions" | "projects";

interface Snapshot {
  name: string;
  description: string;
  apply_caps: boolean;
  components: CriteriaComponent[];
}

const snap = (s: Snapshot) => JSON.stringify(s);

function blankComponents(): DraftComponent[] {
  return evenWeights(2).map((w, i) => ({
    key: `thanh_phan_${i + 1}`,
    name: "",
    description: "",
    weight: w,
    categories: [],
    uid: uid(),
    locked: false,
    keyTouched: false,
  }));
}

/**
 * The set editor (docs/specs/22 §7.2): components on the left with the total weight bar always
 * in view and the category × component matrix, the preview on the right; tabs for versions and
 * the dự án using the set. Ctrl+S saves; leaving with unsaved changes asks first.
 */
export function CriteriaEditor({
  setId,
  seed,
  meta,
  canManage,
  canAssign,
  onBack,
  onCreated,
}: {
  setId: number | null;
  seed: CriteriaSeed | null;
  meta: CriteriaMeta;
  canManage: boolean;
  canAssign: boolean;
  onBack: () => void;
  onCreated: (id: number) => void;
}) {
  const detail = useApi<CriteriaSet>(setId === null ? null : `/criteria-sets/${setId}`);
  if (setId !== null && !detail.data)
    return (
      <Card collapsible={false}>
        <div className="toolbar">
          <button type="button" className="ghost sm" onClick={onBack}>
            <Icon name="chevronLeft" size={14} /> Bộ tiêu chí
          </button>
        </div>
        <Loading error={detail.error} rows={5} />
      </Card>
    );
  return (
    <EditorBody
      set={detail.data ?? null}
      seed={seed}
      meta={meta}
      canManage={canManage}
      canAssign={canAssign}
      onBack={onBack}
      onCreated={onCreated}
      reload={async () => (await detail.mutate()) ?? null}
    />
  );
}

function EditorBody({
  set,
  seed,
  meta,
  canManage,
  canAssign,
  onBack,
  onCreated,
  reload,
}: {
  set: CriteriaSet | null;
  seed: CriteriaSeed | null;
  meta: CriteriaMeta;
  canManage: boolean;
  canAssign: boolean;
  onBack: () => void;
  onCreated: (id: number) => void;
  reload: () => Promise<CriteriaSet | null>;
}) {
  const refreshSets = useRefreshSets();
  const initial: Snapshot = set
    ? {
        name: set.name,
        description: set.description ?? "",
        apply_caps: set.current_version?.apply_caps ?? true,
        components: set.current_version?.components ?? [],
      }
    : {
        name: seed?.name ?? "",
        description: seed?.description ?? "",
        apply_caps: seed?.apply_caps ?? true,
        components: seed?.components ?? [],
      };
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [applyCaps, setApplyCaps] = useState(initial.apply_caps);
  const [comps, setComps] = useState<DraftComponent[]>(() =>
    set ? toDraft(initial.components, true) : seed?.components ? toDraft(seed.components, false) : blankComponents(),
  );
  // A new set is unsaved from the start: what counts as "changed" is anything at all.
  const [baseline, setBaseline] = useState<string>(() => (set ? snap(initial) : ""));
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [attempted, setAttempted] = useState(false);
  const [tab, setTab] = useState<EditorTab>("edit");
  const [saveOpen, setSaveOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [focusNext, setFocusNext] = useState<string | null>(null);
  const summaryRef = useRef<HTMLDivElement>(null);

  const current: Snapshot = useMemo(
    () => ({ name: name.trim(), description: description.trim(), apply_caps: applyCaps, components: fromDraft(comps) }),
    [name, description, applyCaps, comps],
  );
  const errors = useMemo(() => validate(name, comps, meta), [name, comps, meta]);
  const valid = errors.length === 0;
  const dirty = snap(current) !== baseline;
  const newVersion = versionChanged(set?.current_version ?? null, current);
  const nextVersion = (set?.current_version?.version ?? 0) + 1;
  const release = useLeaveGuard(dirty && canManage);
  const total = comps.reduce((s, c) => s + (Number.isFinite(c.weight) ? c.weight : 0), 0);
  const { min_components: minC, max_components: maxC, name_max: nameMax, description_max: descMax } = meta.limits;
  const catLabel = useCallback(
    (key: string) => meta.categories.find((c) => c.key === key)?.label ?? key,
    [meta.categories],
  );

  const show = (field: string) => attempted || touched.has(field);
  const errorOf = (field: string): CritError | undefined => (show(field) ? errors.find((e) => e.field === field) : undefined);
  const touch = (field: string) => setTouched((t) => (t.has(field) ? t : new Set(t).add(field)));

  useEffect(() => {
    if (!focusNext) return;
    document.getElementById(focusNext)?.focus();
    setFocusNext(null);
  }, [focusNext]);

  /* ---- component edits ---- */
  const patch = (id: string, p: Partial<DraftComponent>) => setComps((list) => list.map((c) => (c.uid === id ? { ...c, ...p } : c)));
  const rename = (c: DraftComponent, value: string) => {
    if (c.locked || c.keyTouched) return patch(c.uid, { name: value });
    const others = comps.filter((o) => o.uid !== c.uid).map((o) => o.key);
    const slug = slugify(value);
    patch(c.uid, { name: value, key: slug ? uniqueKey(slug, others) : c.key });
  };
  const move = (from: number, to: number) => {
    if (to < 0 || to >= comps.length) return;
    setComps((list) => moveItem(list, from, to));
    setAnnounce(`Đã chuyển ${comps[from].name || `thành phần ${from + 1}`} tới vị trí ${to + 1} / ${comps.length}.`);
  };
  const dragRow = useDragList(move);
  const add = () => {
    if (comps.length >= maxC) return;
    const taken = comps.map((c) => c.key);
    const rest = 100 - total;
    const c: DraftComponent = {
      key: uniqueKey(`thanh_phan_${comps.length + 1}`, taken),
      name: "",
      description: "",
      weight: rest >= 1 ? Math.min(rest, 100) : 10,
      categories: [],
      uid: uid(),
      locked: false,
      keyTouched: false,
    };
    setComps([...comps, c]);
    setFocusNext(fieldId.name(c.uid));
  };
  const remove = (c: DraftComponent, i: number) => {
    if (comps.length <= minC) return;
    if (c.categories.length && !window.confirm(`Bỏ thành phần "${c.name || `#${i + 1}`}"? Các loại finding của nó (${c.categories.map(catLabel).join(", ")}) sẽ chưa thuộc thành phần nào.`))
      return;
    setComps(comps.filter((o) => o.uid !== c.uid));
    setAnnounce(`Đã bỏ ${c.name || `thành phần ${i + 1}`}.`);
    const next = comps[i + 1] ?? comps[i - 1];
    if (next) setFocusNext(fieldId.name(next.uid));
  };
  const setWeight = (c: DraftComponent, raw: string) => {
    const n = raw === "" ? 0 : Math.round(Number(raw));
    patch(c.uid, { weight: Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0 });
  };
  const assign = (cat: string, compUid: string) =>
    setComps((list) =>
      list.map((c) => ({
        ...c,
        categories: c.uid === compUid ? [...c.categories.filter((x) => x !== cat), cat] : c.categories.filter((x) => x !== cat),
      })),
    );
  const splitEven = () => {
    const w = evenWeights(comps.length);
    setComps(comps.map((c, i) => ({ ...c, weight: w[i] })));
    setAnnounce("Đã chia đều trọng số.");
  };
  const normalize = () => {
    const w = normalizeWeights(comps.map((c) => c.weight));
    setComps(comps.map((c, i) => ({ ...c, weight: w[i] })));
    setAnnounce("Đã chuẩn hoá trọng số về tổng 100, giữ tỉ lệ.");
  };
  const reset = () => {
    if (!window.confirm("Bỏ mọi thay đổi chưa lưu?")) return;
    setName(initial.name);
    setDescription(initial.description);
    setApplyCaps(initial.apply_caps);
    setComps(set ? toDraft(initial.components, true) : seed?.components ? toDraft(seed.components, false) : blankComponents());
    setTouched(new Set());
    setAttempted(false);
    setServerError(null);
  };
  const loadVersion = (v: CriteriaVersion) => {
    if (dirty && !window.confirm(`Nạp phiên bản ${v.version} vào trình soạn? Các thay đổi chưa lưu sẽ mất.`)) return;
    const keys = new Set(set?.current_version?.components.map((c) => c.key) ?? []);
    setComps(toDraft(v.components, true).map((c) => ({ ...c, locked: keys.has(c.key) })));
    setApplyCaps(v.apply_caps);
    setTab("edit");
    setMessage(`Đã nạp phiên bản ${v.version}. Lưu để tạo phiên bản ${nextVersion} giống nó.`);
  };

  /* ---- saving ---- */
  const requestSave = () => {
    if (!canManage || saving) return;
    if (!valid) {
      setAttempted(true);
      summaryRef.current?.focus();
      return;
    }
    if (!dirty) return;
    if (set && newVersion) setSaveOpen(true);
    else void save("", false);
  };
  async function save(note: string, recompute: boolean) {
    setSaving(true);
    setServerError(null);
    try {
      if (!set) {
        const created = await api.post<CriteriaSet>("/criteria-sets", {
          name: current.name,
          description: current.description,
          components: current.components,
          apply_caps: current.apply_caps,
          ...(seed?.from_template ? { from_template: seed.from_template } : {}),
        });
        release();
        await refreshSets();
        onCreated(created.id);
        return;
      }
      await api.put<CriteriaSet>(`/criteria-sets/${set.id}`, {
        name: current.name,
        description: current.description,
        components: current.components,
        apply_caps: current.apply_caps,
        note: note.trim() || null,
      });
      let queued = 0;
      const failed: string[] = [];
      if (recompute && newVersion) {
        for (const p of set.projects) {
          try {
            await api.post(`/business-projects/${p.id}/criteria-sets/${set.id}/recompute`, {});
            queued++;
          } catch {
            failed.push(p.name);
          }
        }
      }
      const fresh = await reload();
      await refreshSets();
      const saved: Snapshot = fresh
        ? {
            name: fresh.name,
            description: fresh.description ?? "",
            apply_caps: fresh.current_version?.apply_caps ?? current.apply_caps,
            components: fresh.current_version?.components ?? current.components,
          }
        : current;
      setComps((list) => list.map((c) => ({ ...c, locked: true, keyTouched: true })));
      setBaseline(snap(saved));
      // The server may trim or reorder: take its version so the draft is clean.
      if (fresh?.current_version) {
        setComps(toDraft(fresh.current_version.components, true));
        setName(fresh.name);
        setDescription(fresh.description ?? "");
        setApplyCaps(fresh.current_version.apply_caps);
      }
      setTouched(new Set());
      setAttempted(false);
      setSaveOpen(false);
      setMessage(
        (newVersion ? `Đã lưu phiên bản ${fresh?.current_version?.version ?? nextVersion}.` : "Đã lưu tên và mô tả (không tạo phiên bản mới).") +
          (queued ? ` Đã xếp hàng tính lại cho ${queued} dự án.` : "") +
          (failed.length ? ` Lỗi khi xếp hàng tính lại: ${failed.join(", ")}.` : ""),
      );
    } catch (e) {
      setServerError(e instanceof ApiError ? e.message : String(e));
      setSaveOpen(false);
    } finally {
      setSaving(false);
    }
  }

  // Ctrl+S / Cmd+S anywhere on the page while the editor is open.
  const saveRef = useRef(requestSave);
  saveRef.current = requestSave;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const versions = set?.versions ?? [];
  const tabs = set
    ? [
        { key: "edit" as const, label: canManage ? "Soạn" : "Thành phần", hint: "Thành phần, trọng số, loại finding, xem trước" },
        { key: "versions" as const, label: "Phiên bản", count: versions.length || undefined, hint: "Lịch sử và so sánh phiên bản" },
        { key: "projects" as const, label: "Dự án đang dùng", count: set.projects.length, hint: "Dự án dùng bộ này, tính lại điểm" },
      ]
    : [];
  const showSummary = errors.length > 0 && (attempted || touched.size > 0 || !set);
  const assigned = new Map<string, string>();
  for (const c of comps) for (const cat of c.categories) if (!assigned.has(cat)) assigned.set(cat, c.uid);
  const unassigned = meta.categories.filter((c) => !assigned.has(c.key));

  return (
    <>
      <section className="card crit-editor-head" aria-label="Trình soạn bộ tiêu chí">
        <button type="button" className="ghost sm" onClick={onBack}>
          <Icon name="chevronLeft" size={14} /> Bộ tiêu chí
        </button>
        <div className="crit-editor-title">
          <h2>{name.trim() || (set ? set.name : "Bộ tiêu chí mới")}</h2>
          {set?.current_version ? <span className="crit-ver">v{set.current_version.version}</span> : <Pill tone="info">Chưa lưu</Pill>}
          {set?.archived && <Pill>Lưu trữ</Pill>}
          {set && dirty && canManage && (
            <Pill tone="warn" title={newVersion ? `Lưu sẽ tạo phiên bản ${nextVersion}` : "Chỉ đổi tên hoặc mô tả"}>
              Có thay đổi chưa lưu
            </Pill>
          )}
        </div>
        <span className="spacer" />
        {canManage && (
          <div className="crit-editor-actions">
            {dirty && set && (
              <button type="button" className="ghost sm" onClick={reset} disabled={saving}>
                Hoàn tác
              </button>
            )}
            <button
              type="button"
              className="primary sm"
              onClick={requestSave}
              disabled={!valid || !dirty || saving}
              title="Ctrl+S"
              aria-keyshortcuts="Control+S"
            >
              {saving ? "Đang lưu…" : !set ? "Tạo bộ" : newVersion && dirty ? `Lưu phiên bản ${nextVersion}` : "Lưu"}
              <kbd className="crit-kbd" aria-hidden="true">
                Ctrl S
              </kbd>
            </button>
          </div>
        )}
      </section>

      {set && <SubTabs id="crit-ed" label="Trình soạn" variant="mini" tabs={tabs} value={tab} onChange={setTab} />}
      <Message text={message} onClose={() => setMessage(null)} />
      <p className="visually-hidden" aria-live="polite">
        {announce}
      </p>

      {tab === "edit" && (
        <SubPanel id="crit-ed" tab="edit">
          <div className="crit-layout">
            <div className="crit-main">
              {serverError && (
                <Alert tone="error" onClose={() => setServerError(null)}>
                  Máy chủ chưa nhận bộ này: {serverError}
                </Alert>
              )}
              {showSummary && (
                <div className="crit-errors" ref={summaryRef} tabIndex={-1} role="alert" aria-labelledby="crit-errors-title">
                  <strong id="crit-errors-title">
                    <Icon name="alertCircle" size={15} /> {errors.length} điều cần sửa trước khi lưu
                  </strong>
                  <ul>
                    {errors.slice(0, 8).map((e, i) => (
                      <li key={`${e.field}-${i}`}>
                        <button
                          type="button"
                          className="link"
                          onClick={() => {
                            const el = document.getElementById(e.field);
                            el?.scrollIntoView({ block: "center" });
                            el?.focus();
                          }}
                        >
                          {e.message}
                        </button>
                      </li>
                    ))}
                    {errors.length > 8 && <li className="muted">… và {errors.length - 8} lỗi khác</li>}
                  </ul>
                </div>
              )}

              <fieldset className="crit-fieldset" disabled={!canManage || saving}>
                <legend className="visually-hidden">Thông tin bộ</legend>
                <section className="card crit-info-card">
                  <div className="crit-info-grid">
                    <label className="adm-field">
                      <span className="small muted">Tên bộ</span>
                      <input
                        id={fieldId.setName}
                        value={name}
                        maxLength={SET_NAME_MAX + 20}
                        onChange={(e) => setName(e.target.value)}
                        onBlur={() => touch(fieldId.setName)}
                        placeholder="Ví dụ: Chuẩn OmiCRM"
                        aria-invalid={!!errorOf(fieldId.setName) || undefined}
                        aria-describedby={errorOf(fieldId.setName) ? `${fieldId.setName}-err` : undefined}
                      />
                      <FieldError id={`${fieldId.setName}-err`} error={errorOf(fieldId.setName)} />
                    </label>
                    <label className="adm-field">
                      <span className="small muted">Mô tả (không tạo phiên bản mới khi đổi)</span>
                      <input
                        id={fieldId.setDesc}
                        value={description}
                        maxLength={500}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="Dùng cho dự án nào, ưu tiên gì"
                      />
                    </label>
                  </div>
                  <label className="inline-check small crit-caps">
                    <input type="checkbox" checked={applyCaps} onChange={(e) => setApplyCaps(e.target.checked)} />
                    <span>
                      <b>Chặn trần như điểm review</b>: có critical thì tối đa 50, có high thì tối đa 80, dù thành phần chứa
                      finding có trọng số nhỏ.
                    </span>
                  </label>
                </section>

                <div
                  id={fieldId.total}
                  tabIndex={-1}
                  className={`crit-total${total === 100 ? " ok" : " off"}`}
                  role="group"
                  aria-label="Tổng trọng số"
                >
                  <div className="crit-total-row">
                    <strong>
                      Tổng trọng số <span className="crit-total-num">{total}</span>
                      <span className="muted"> / 100</span>
                    </strong>
                    <span className={`small ${total === 100 ? "tone-good" : "error"}`} aria-live="polite">
                      {total === 100 ? "Đủ 100" : total < 100 ? `Còn thiếu ${100 - total}` : `Vượt ${total - 100}`}
                    </span>
                    <span className="spacer" />
                    <button type="button" className="sm ghost" onClick={splitEven} title="Mỗi thành phần một phần bằng nhau">
                      Chia đều
                    </button>
                    <button
                      type="button"
                      className="sm"
                      onClick={normalize}
                      disabled={total === 100 || total === 0}
                      title="Giữ tỉ lệ giữa các thành phần, tổng thành 100"
                    >
                      Chuẩn hoá về 100
                    </button>
                  </div>
                  <WeightBar parts={comps} label="Trọng số đang soạn" size="lg" showRest />
                </div>

                <section className="card crit-comps" aria-labelledby="crit-comps-title">
                  <div className="card-head">
                    <div>
                      <h2 id="crit-comps-title">
                        Thành phần <span className="muted small">({comps.length} / {maxC})</span>
                      </h2>
                      <div className="sub">
                        AI đọc mô tả để xếp mỗi finding vào một thành phần. Kéo tay cầm hoặc dùng nút mũi tên để đổi thứ tự.
                      </div>
                    </div>
                    <div className="actions">
                      <button type="button" className="sm" onClick={add} disabled={comps.length >= maxC}>
                        <Icon name="plus" size={14} /> Thêm thành phần
                      </button>
                    </div>
                  </div>
                  {errorOf(fieldId.list) && <FieldError id={`${fieldId.list}-err`} error={errorOf(fieldId.list)} />}
                  <ol className="crit-comp-list" id={fieldId.list} tabIndex={-1} aria-label="Danh sách thành phần">
                    {comps.map((c, i) => {
                      const label = c.name.trim() || `thành phần ${i + 1}`;
                      const nameErr = errorOf(fieldId.name(c.uid));
                      const keyErr = errorOf(fieldId.key(c.uid));
                      const descErr = errorOf(fieldId.desc(c.uid));
                      const weightErr = errorOf(fieldId.weight(c.uid));
                      return (
                        <li key={c.uid} className="crit-comp" {...(canManage ? dragRow(i) : {})}>
                          <div className="crit-comp-side">
                            <Swatch index={i} />
                            {canManage && <ReorderControls index={i} count={comps.length} name={label} onMove={(to) => move(i, to)} />}
                          </div>
                          <div className="crit-comp-body">
                            <div className="crit-comp-row">
                              <label className="adm-field crit-comp-name">
                                <span className="small muted">Tên</span>
                                <input
                                  id={fieldId.name(c.uid)}
                                  value={c.name}
                                  maxLength={nameMax + 20}
                                  onChange={(e) => rename(c, e.target.value)}
                                  onBlur={() => touch(fieldId.name(c.uid))}
                                  placeholder="Ví dụ: Bảo mật"
                                  aria-invalid={!!nameErr || undefined}
                                  aria-describedby={nameErr ? `${fieldId.name(c.uid)}-err` : undefined}
                                />
                                <FieldError id={`${fieldId.name(c.uid)}-err`} error={nameErr} />
                              </label>
                              <label className="adm-field crit-comp-key">
                                <span className="small muted">
                                  Mã{" "}
                                  {c.locked && (
                                    <span title="Mã đã có trong phiên bản đã lưu nên không đổi được (điểm cũ tham chiếu theo mã)">
                                      · cố định
                                    </span>
                                  )}
                                </span>
                                <input
                                  id={fieldId.key(c.uid)}
                                  value={c.key}
                                  readOnly={c.locked}
                                  spellCheck={false}
                                  onChange={(e) => patch(c.uid, { key: e.target.value.toLowerCase(), keyTouched: true })}
                                  onBlur={() => touch(fieldId.key(c.uid))}
                                  aria-invalid={!!keyErr || undefined}
                                  aria-describedby={keyErr ? `${fieldId.key(c.uid)}-err` : undefined}
                                />
                                <FieldError id={`${fieldId.key(c.uid)}-err`} error={keyErr} />
                              </label>
                              <div className="adm-field crit-comp-weight">
                                <label className="small muted" htmlFor={fieldId.weight(c.uid)}>
                                  Trọng số (%)
                                </label>
                                <div className="crit-weight">
                                  <input
                                    type="range"
                                    min={1}
                                    max={100}
                                    step={1}
                                    value={Math.max(1, c.weight || 1)}
                                    onChange={(e) => setWeight(c, e.target.value)}
                                    aria-label={`Trọng số ${label}`}
                                    aria-valuetext={`${c.weight}%`}
                                    style={{ accentColor: `var(--crit-${(i % 12) + 1})` }}
                                  />
                                  <input
                                    id={fieldId.weight(c.uid)}
                                    type="number"
                                    min={1}
                                    max={100}
                                    step={1}
                                    inputMode="numeric"
                                    value={c.weight || ""}
                                    onChange={(e) => setWeight(c, e.target.value)}
                                    onBlur={() => touch(fieldId.weight(c.uid))}
                                    aria-invalid={!!weightErr || undefined}
                                    aria-describedby={weightErr ? `${fieldId.weight(c.uid)}-err` : undefined}
                                  />
                                </div>
                                <FieldError id={`${fieldId.weight(c.uid)}-err`} error={weightErr} />
                              </div>
                              {canManage && (
                                <IconButton
                                  icon="remove"
                                  tone="danger"
                                  className="crit-comp-remove"
                                  label={comps.length <= minC ? `Bộ cần ít nhất ${minC} thành phần` : `Bỏ ${label}`}
                                  disabled={comps.length <= minC}
                                  onClick={() => remove(c, i)}
                                />
                              )}
                            </div>
                            <label className="adm-field">
                              <span className="crit-desc-label small muted">
                                Mô tả cho AI
                                <span className={c.description.length > descMax ? "error" : undefined}>
                                  {c.description.length} / {descMax}
                                </span>
                              </span>
                              <textarea
                                id={fieldId.desc(c.uid)}
                                rows={2}
                                value={c.description}
                                onChange={(e) => patch(c.uid, { description: e.target.value })}
                                onBlur={() => touch(fieldId.desc(c.uid))}
                                placeholder="Những vấn đề nào thuộc thành phần này (AI đọc để xếp finding)"
                                aria-invalid={!!descErr || undefined}
                                aria-describedby={descErr ? `${fieldId.desc(c.uid)}-err` : undefined}
                              />
                              <FieldError id={`${fieldId.desc(c.uid)}-err`} error={descErr} />
                            </label>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </section>

                <section className="card crit-matrix-card" aria-labelledby="crit-matrix-title">
                  <div className="card-head">
                    <div>
                      <h2 id="crit-matrix-title">Loại finding thuộc thành phần nào</h2>
                      <div className="sub">
                        Mỗi loại thuộc đúng một thành phần; finding AI không xếp được thì đi theo loại của nó.
                      </div>
                    </div>
                    <div className="actions">
                      {unassigned.length ? (
                        <Pill tone="bad" icon="alert">
                          {unassigned.length} loại chưa gán
                        </Pill>
                      ) : (
                        <Pill tone="good" icon="checkCircle">
                          Đã gán đủ
                        </Pill>
                      )}
                    </div>
                  </div>
                  <div className="table-wrap crit-matrix-wrap">
                    <table className="crit-matrix">
                      <thead>
                        <tr>
                          <th scope="col">Loại finding</th>
                          {comps.map((c, i) => (
                            <th scope="col" key={c.uid} title={c.name || `Thành phần ${i + 1}`}>
                              <span className="crit-matrix-th">
                                <Swatch index={i} />
                                <span>{c.name.trim() || `#${i + 1}`}</span>
                              </span>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {meta.categories.map((cat) => {
                          const owner = assigned.get(cat.key);
                          return (
                            <tr key={cat.key} className={owner ? undefined : "unassigned"}>
                              <th scope="row">
                                <span id={fieldId.category(cat.key)} tabIndex={-1} className="crit-matrix-cat">
                                  {cat.label}
                                  {!owner && <span className="crit-unassigned">chưa gán</span>}
                                </span>
                              </th>
                              {comps.map((c, i) => (
                                <td key={c.uid}>
                                  <input
                                    type="radio"
                                    name={`crit-cat-${cat.key}`}
                                    checked={owner === c.uid}
                                    onChange={() => assign(cat.key, c.uid)}
                                    aria-label={`${cat.label} thuộc ${c.name.trim() || `thành phần ${i + 1}`}`}
                                  />
                                </td>
                              ))}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </section>
              </fieldset>
            </div>

            <aside className="crit-side">
              <CriteriaPreview
                components={current.components}
                applyCaps={applyCaps}
                valid={valid}
                defaultProject={set?.projects.find((p) => p.is_primary)?.id ?? set?.projects[0]?.id ?? null}
              />
            </aside>
          </div>
        </SubPanel>
      )}

      {tab === "versions" && set && (
        <SubPanel id="crit-ed" tab="versions">
          <VersionsPanel set={set} canManage={canManage} catLabel={catLabel} onLoad={loadVersion} />
        </SubPanel>
      )}

      {tab === "projects" && set && (
        <SubPanel id="crit-ed" tab="projects">
          <UsagePanel set={set} canAssign={canAssign} />
        </SubPanel>
      )}

      {saveOpen && set && (
        <SaveDialog
          set={set}
          draft={current}
          nextVersion={nextVersion}
          saving={saving}
          catLabel={catLabel}
          onCancel={() => setSaveOpen(false)}
          onSave={(note, recompute) => void save(note, recompute)}
        />
      )}
    </>
  );
}

function FieldError({ id, error }: { id: string; error?: CritError }) {
  if (!error) return null;
  return (
    <span id={id} className="error small crit-field-err">
      {error.message}
    </span>
  );
}

/** "Tạo phiên bản N+1": what changes, a note, and recomputing the dự án that use the set. */
function SaveDialog({
  set,
  draft,
  nextVersion,
  saving,
  catLabel,
  onCancel,
  onSave,
}: {
  set: CriteriaSet;
  draft: Snapshot;
  nextVersion: number;
  saving: boolean;
  catLabel: (key: string) => string;
  onCancel: () => void;
  onSave: (note: string, recompute: boolean) => void;
}) {
  const [note, setNote] = useState("");
  const [recompute, setRecompute] = useState(set.projects.length > 0);
  const before = set.current_version;
  const diff = before ? diffComponents(before.components, draft.components, catLabel).filter((d) => d.status !== "same") : [];
  const capsChanged = before && before.apply_caps !== draft.apply_caps;
  return (
    <Modal
      title={`Tạo phiên bản ${nextVersion}`}
      onClose={onCancel}
      busy={saving}
      footer={
        <>
          <button type="button" className="ghost" onClick={onCancel} disabled={saving}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => onSave(note, recompute)} disabled={saving}>
            {saving ? "Đang lưu…" : `Lưu phiên bản ${nextVersion}`}
          </button>
        </>
      }
    >
      <div className="adm-form">
        <p className="muted small">
          Thành phần hoặc trần đã đổi nên bộ có phiên bản mới; phiên bản {before?.version ?? 1} được giữ nguyên. Review mới
          chấm theo phiên bản {nextVersion}; điểm đã có giữ phiên bản cũ trừ khi tính lại.
        </p>
        {(diff.length > 0 || capsChanged) && (
          <ul className="crit-diff-mini">
            {capsChanged && <li>Chặn trần: {before?.apply_caps ? "bật" : "tắt"} → {draft.apply_caps ? "bật" : "tắt"}</li>}
            {diff.map((d) => (
              <li key={d.key}>
                <DiffTag status={d.status} /> <b>{d.name}</b>
                {d.status === "added" && ` · ${d.after?.weight}%`}
                {d.status === "changed" && ` · ${d.changes.join("; ")}`}
              </li>
            ))}
          </ul>
        )}
        <label className="adm-field">
          <span className="small muted">Ghi chú phiên bản (tuỳ chọn)</span>
          <textarea
            rows={2}
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Vì sao đổi, ví dụ: tăng trọng số Bảo mật theo yêu cầu kiểm toán"
            autoFocus
          />
        </label>
        <label className="inline-check small">
          <input
            type="checkbox"
            checked={recompute}
            disabled={!set.projects.length}
            onChange={(e) => setRecompute(e.target.checked)}
          />
          {set.projects.length
            ? `Tính lại điểm cho ${set.projects.length} dự án đang dùng (chạy nền, theo phiên bản ${nextVersion})`
            : "Tính lại điểm cho các dự án đang dùng (chưa dự án nào dùng bộ này)"}
        </label>
      </div>
    </Modal>
  );
}

function DiffTag({ status }: { status: "added" | "removed" | "changed" | "same" }) {
  const map = {
    added: { text: "Thêm", tone: "good" as const },
    removed: { text: "Bỏ", tone: "bad" as const },
    changed: { text: "Đổi", tone: "warn" as const },
    same: { text: "Giữ nguyên", tone: undefined },
  };
  return <Pill tone={map[status].tone}>{map[status].text}</Pill>;
}

/* ---------- Preview ---------- */

/** POST /criteria/preview answers a list, or the list under `items` / `reviews`. */
function rowsOf(body: unknown): CriteriaPreviewRow[] {
  if (Array.isArray(body)) return body as CriteriaPreviewRow[];
  if (body && typeof body === "object") {
    const o = body as { items?: unknown; reviews?: unknown };
    if (Array.isArray(o.items)) return o.items as CriteriaPreviewRow[];
    if (Array.isArray(o.reviews)) return o.reviews as CriteriaPreviewRow[];
  }
  return [];
}

function CriteriaPreview({
  components,
  applyCaps,
  valid,
  defaultProject,
}: {
  components: CriteriaComponent[];
  applyCaps: boolean;
  valid: boolean;
  defaultProject: number | null;
}) {
  const projects = useApi<BusinessProject[]>("/business-projects");
  const [bp, setBp] = useState<number | null>(defaultProject);
  const [rows, setRows] = useState<CriteriaPreviewRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const seq = useRef(0);
  const body = JSON.stringify({ components, apply_caps: applyCaps });

  useEffect(() => {
    if (bp === null && projects.data?.length) setBp(projects.data[0].id);
  }, [bp, projects.data]);

  useEffect(() => {
    const mine = ++seq.current;
    if (!valid || bp === null) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const parsed = JSON.parse(body) as { components: CriteriaComponent[]; apply_caps: boolean };
        const result = await api.post<unknown>("/criteria/preview", { ...parsed, business_project_id: bp, limit: 20 });
        if (mine !== seq.current) return;
        setRows(rowsOf(result));
        setError(null);
      } catch (e) {
        if (mine !== seq.current) return;
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (mine === seq.current) setLoading(false);
      }
    }, 600);
    return () => window.clearTimeout(t);
  }, [body, bp, valid]);

  const scored = (rows ?? []).filter((r) => toNum(r.score) !== null);
  const avgOld = scored.length ? scored.reduce((s, r) => s + (toNum(r.score) ?? 0), 0) / scored.length : null;
  const avgNew = rows?.length ? rows.reduce((s, r) => s + (toNum(r.criteria_score) ?? 0), 0) / rows.length : null;
  const selected = rows?.find((r) => r.review_id === open) ?? null;

  return (
    <section className="card crit-preview" aria-labelledby="crit-preview-title">
      <div className="card-head">
        <div>
          <h2 id="crit-preview-title">Xem trước</h2>
          <div className="sub">Chấm lại tối đa 20 review gần nhất bằng bộ đang soạn; không lưu gì.</div>
        </div>
        {loading && <span className="spinner" role="status" aria-label="Đang tính" />}
      </div>
      <label className="adm-field">
        <span className="small muted">Dự án</span>
        <select value={bp ?? ""} onChange={(e) => { setBp(e.target.value ? Number(e.target.value) : null); setOpen(null); }} disabled={!projects.data}>
          {!projects.data && <option value="">Đang tải…</option>}
          {projects.data?.length === 0 && <option value="">Chưa có dự án</option>}
          {projects.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      {!valid ? (
        <p className="muted small crit-preview-note">
          <Icon name="info" size={14} /> Sửa các lỗi bên trái (tổng 100, loại finding đã gán đủ) để xem trước.
        </p>
      ) : projects.error ? (
        <Loading error={projects.error} />
      ) : projects.data?.length === 0 ? (
        <EmptyState icon="folder" title="Chưa có dự án">
          Tạo dự án ở Dự án &amp; Repo để xem trước trên review thật.
        </EmptyState>
      ) : error ? (
        <Alert tone="error">Không xem trước được: {error}</Alert>
      ) : rows === null ? (
        <Loading rows={5} />
      ) : rows.length === 0 ? (
        <EmptyState icon="inbox" title="Dự án chưa có review nào đã xong">
          Chọn dự án khác, hoặc chờ review đầu tiên.
        </EmptyState>
      ) : (
        <>
          <div className="crit-preview-sum" aria-live="polite">
            <span>
              <span className="muted small">Điểm hiện có TB</span>
              <strong>{score1(avgOld)}</strong>
            </span>
            <span aria-hidden="true">→</span>
            <span>
              <span className="muted small">Theo bộ này TB</span>
              <strong>{score1(avgNew)}</strong>
            </span>
            <DeltaChip value={avgOld === null || avgNew === null ? null : avgNew - avgOld} />
          </div>
          <ul className={`crit-preview-list${loading ? " stale" : ""}`}>
            {rows.map((r) => {
              const oldS = toNum(r.score);
              const newS = toNum(r.criteria_score);
              const isOpen = open === r.review_id;
              return (
                <li key={r.review_id} className={isOpen ? "open" : undefined}>
                  <button
                    type="button"
                    className="crit-preview-row"
                    aria-expanded={isOpen}
                    onClick={() => setOpen(isOpen ? null : r.review_id)}
                  >
                    <span className="crit-preview-name">
                      <span>{r.title || `Review #${r.review_id}`}</span>
                      <span className="muted small">
                        {r.project ? `${r.project} · ` : ""}
                        {dateTime(r.finished_at)}
                      </span>
                    </span>
                    <span className="crit-preview-scores">
                      <span className="muted" title="Điểm hiện có">{score1(oldS)}</span>
                      <span aria-hidden="true">→</span>
                      <strong title="Điểm theo bộ đang soạn">{score1(newS)}</strong>
                      <DeltaChip value={oldS === null || newS === null ? null : newS - oldS} />
                    </span>
                  </button>
                  {isOpen && selected && (
                    <div className="crit-preview-detail">
                      {selected.breakdown.capped && (
                        <p className="small" style={{ margin: "0 0 6px" }}>
                          <Pill tone="warn" icon="alert">Bị chặn trần</Pill>
                        </p>
                      )}
                      <Breakdown
                        breakdown={selected.breakdown}
                        finding={(id) => {
                          const f = selected.findings?.find((x) => x.id === id);
                          return f ? { title: f.title, severity: f.severity, category: f.category } : null;
                        }}
                      />
                      <Link className="small" href={`/reviews/${r.review_id}`}>
                        Mở review #{r.review_id}
                      </Link>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

function DeltaChip({ value }: { value: number | null }) {
  if (value === null) return <span className="crit-delta">—</span>;
  const r = Math.round(value * 10) / 10;
  const tone = r > 0 ? "up" : r < 0 ? "down" : "";
  return (
    <span className={`crit-delta ${tone}`} title="Chênh lệch so với điểm hiện có">
      {r > 0 ? "+" : ""}
      {r.toLocaleString("vi-VN", { maximumFractionDigits: 1 })}
    </span>
  );
}

/* ---------- Versions ---------- */

function VersionsPanel({
  set,
  canManage,
  catLabel,
  onLoad,
}: {
  set: CriteriaSet;
  canManage: boolean;
  catLabel: (key: string) => string;
  onLoad: (v: CriteriaVersion) => void;
}) {
  const list = [...(set.versions ?? [])].sort((a, b) => b.version - a.version);
  const latest = set.current_version?.version ?? list[0]?.version ?? 1;
  const [a, setA] = useState<number>(list.find((v) => v.version < latest)?.version ?? latest);
  const [b, setB] = useState<number>(latest);
  const [hideSame, setHideSame] = useState(true);
  const va = useApi<CriteriaVersion>(list.length > 1 ? `/criteria-sets/${set.id}/versions/${a}` : null);
  const vb = useApi<CriteriaVersion>(list.length > 1 ? `/criteria-sets/${set.id}/versions/${b}` : null);
  const diff = va.data && vb.data ? diffComponents(va.data.components, vb.data.components, catLabel) : [];
  const shown = hideSame ? diff.filter((d) => d.status !== "same") : diff;

  return (
    <div className="grid crit-versions">
      <Card title="Lịch sử phiên bản" collapsible={false} flush>
        {list.length === 0 ? (
          <p className="muted small adm-pad">Chưa có phiên bản nào.</p>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="stack-sm">
              <thead>
                <tr>
                  <th>Phiên bản</th>
                  <th>Ngày</th>
                  <th>Người tạo</th>
                  <th>Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {list.map((v) => (
                  <tr key={v.id}>
                    <td className="nowrap">
                      <strong>v{v.version}</strong> {v.version === latest && <Pill tone="primary">hiện tại</Pill>}
                    </td>
                    <td className="muted nowrap">{dateTime(v.created_at)}</td>
                    <td className="muted">{v.created_by_name ?? (v.created_by ? `#${v.created_by}` : "—")}</td>
                    <td>{v.note || <span className="faint">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="So sánh hai phiên bản" collapsible={false}>
        {list.length < 2 ? (
          <EmptyState icon="scale" title="Mới có một phiên bản">
            Lưu thay đổi về thành phần, trọng số hoặc trần sẽ tạo phiên bản mới để so sánh.
          </EmptyState>
        ) : (
          <>
            <div className="toolbar">
              <label className="small">
                Từ
                <select className="compact" value={a} onChange={(e) => setA(Number(e.target.value))} aria-label="Phiên bản gốc">
                  {list.map((v) => (
                    <option key={v.id} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </label>
              <label className="small">
                đến
                <select className="compact" value={b} onChange={(e) => setB(Number(e.target.value))} aria-label="Phiên bản so sánh">
                  {list.map((v) => (
                    <option key={v.id} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline-check small">
                <input type="checkbox" checked={hideSame} onChange={(e) => setHideSame(e.target.checked)} />
                Ẩn thành phần không đổi
              </label>
              <span className="spacer" />
              {canManage && va.data && (
                <button type="button" className="sm" onClick={() => va.data && onLoad(va.data)}>
                  Nạp v{a} vào trình soạn
                </button>
              )}
            </div>
            {!va.data || !vb.data ? (
              <Loading error={va.error ?? vb.error} rows={4} />
            ) : (
              <>
                <div className="crit-compare-bars">
                  <span className="muted small">v{a}</span>
                  <WeightBar parts={va.data.components} label={`Trọng số v${a}`} />
                  <span className="muted small">v{b}</span>
                  <WeightBar parts={vb.data.components} label={`Trọng số v${b}`} />
                </div>
                {va.data.apply_caps !== vb.data.apply_caps && (
                  <p className="small">
                    <DiffTag status="changed" /> Chặn trần: {va.data.apply_caps ? "bật" : "tắt"} → {vb.data.apply_caps ? "bật" : "tắt"}
                  </p>
                )}
                {shown.length === 0 ? (
                  <p className="muted small">{a === b ? "Chọn hai phiên bản khác nhau." : "Không có thành phần nào thay đổi."}</p>
                ) : (
                  <ul className="crit-diff">
                    {shown.map((d) => (
                      <li key={d.key} className={d.status}>
                        <DiffTag status={d.status} />
                        <span className="crit-diff-name">
                          <b>{d.name}</b> <code className="small">{d.key}</code>
                        </span>
                        <span className="crit-diff-weight">
                          {d.before ? `${d.before.weight}%` : "—"} → {d.after ? `${d.after.weight}%` : "—"}
                        </span>
                        {d.changes.length > 0 && <span className="crit-diff-changes muted small">{d.changes.join(" · ")}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

/* ---------- Dự án đang dùng ---------- */

function UsagePanel({ set, canAssign }: { set: CriteriaSet; canAssign: boolean }) {
  const [busy, setBusy] = useState<number | "all" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  async function recompute(ids: { id: number; name: string }[], which: number | "all") {
    setBusy(which);
    const failed: string[] = [];
    for (const p of ids) {
      try {
        await api.post(`/business-projects/${p.id}/criteria-sets/${set.id}/recompute`, {});
      } catch (e) {
        failed.push(`${p.name} (${e instanceof ApiError ? e.message : String(e)})`);
      }
    }
    setBusy(null);
    setMessage(
      failed.length
        ? `Lỗi: không xếp hàng được ${failed.join(", ")}.`
        : `Đã xếp hàng tính lại cho ${ids.length} dự án theo phiên bản ${set.current_version?.version ?? ""}; điểm cập nhật sau vài phút.`,
    );
  }
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      <Card
        title="Dự án đang dùng"
        subtitle="Tính lại chấm các review đã xong của dự án bằng phiên bản hiện tại của bộ."
        collapsible={false}
        flush
        actions={
          canAssign &&
          set.projects.length > 1 && (
            <button type="button" className="sm" disabled={busy !== null} onClick={() => void recompute(set.projects, "all")}>
              <Icon name="sync" size={14} /> {busy === "all" ? "Đang xếp hàng…" : "Tính lại tất cả"}
            </button>
          )
        }
      >
        {set.projects.length === 0 ? (
          <EmptyState icon="folder" title="Chưa dự án nào dùng bộ này">
            Gán bộ ở Admin › <a href="#repos">Dự án &amp; Repo</a>: mở một dự án, mục &ldquo;Bộ tiêu chí&rdquo;.
          </EmptyState>
        ) : (
          <div className="table-wrap scroll-y">
            <table className="stack-sm">
              <thead>
                <tr>
                  <th>Dự án</th>
                  <th>Vai trò</th>
                  <th>
                    <span className="visually-hidden">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {set.projects.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <Link href={`/projects/${p.id}`}>{p.name}</Link>
                    </td>
                    <td>{p.is_primary ? <Pill tone="primary">Bộ chính</Pill> : <span className="muted">Bộ phụ</span>}</td>
                    <td className="num">
                      {canAssign && (
                        <button type="button" className="sm ghost" disabled={busy !== null} onClick={() => void recompute([p], p.id)}>
                          {busy === p.id ? "Đang xếp hàng…" : "Tính lại"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
