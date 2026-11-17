"use client";

import { type ReactNode, type RefObject, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import useSWR from "swr";

import { Icon } from "@/components/icons";
import { api, useApi } from "@/lib/api";
import type { Me, SearchCond, SearchFieldInfo, SearchFields, SearchTree, TaskSearchFacets, TaskSpace } from "@/lib/types";

import { type ChipCond, ChipEditor, FIELD_LABEL, chipId, chipValueLabel, first, opLabel, valueOptions } from "./FilterChips";
import { type FilterValue, filterCount, filterTree } from "./filterState";
import { dialogOpen, typingIn } from "./shortcutEvents";
import { PersonAvatar, errorText, personName, useSprints, useTaskPeople, useVersions } from "./taskParts";

/*
 * FilterBar (docs/specs/31 §22.2, UI/UX §3.3): one 40 px row shared by the Task screens. Left to
 * right: the smart search (text, or a sentence that turns into chips), "Của tôi", the people as
 * stacked avatars, two pinned field chips (Loại, Ưu tiên by default), "+ Lọc", the active chips
 * (past four they fold into "+N bộ lọc"), "Xoá lọc"; on the right the count ("24 task · đã lọc"),
 * a status label, the saved filters slot, "Hiển thị" and extra actions. The value is
 * `{text, chips}` (filterState.ts) and becomes the search tree with `filterTree`.
 *
 * Keys (when not typing, no dialog open): `/` or `F` search, Shift+F "+ Lọc", Shift+X clear,
 * V "Hiển thị".
 */

const DEFAULT_PINNED = ["type", "priority"];
/** Fields the server counts per value (POST /tasks/search/facets). */
const FACETS = new Set(["status", "type", "priority", "resolution", "assignee", "reporter", "sprint", "version", "label", "component"]);
/** "+ Lọc" order (UI/UX §3.4); custom fields follow; the rest of the catalogue after these. */
const ADD_ORDER = ["status", "sprint", "version", "label", "due", "resolution", "parent", "reporter", "component", "watcher", "created", "updated", "resolved", "points", "age_in_column_hours", "has_mr", "test_failed"];
/** One-click flags at the foot of "+ Lọc". */
const FLAGS: { label: string; cond: SearchCond }[] = [
  { label: "Bị chặn", cond: { field: "blocked", op: "is", value: true } },
  { label: "Có critical", cond: { field: "has_critical", op: "is", value: true } },
  { label: "DoD chưa đạt", cond: { field: "dod_met", op: "is", value: "unmet" } },
];
const HIDDEN_FIELDS = new Set(["text", "space", "category"]);
const PEOPLE_SHOWN = 3;
const CHIPS_SHOWN = 4;

export const FIELD_LABEL_FB: Record<string, string> = { ...FIELD_LABEL, parent: "Task cha / Epic" };

/* ------------------------------------------------------------------ catalogue */

export type Option = { value: string; label: string; lead?: ReactNode };

/** The field catalogue of a dự án and how to name chips and list values: shared with the Board. */
export function useFilterCatalogue(space: TaskSpace) {
  const bp = space.business_project_id;
  const fields = useApi<SearchFields>(`/tasks/search/fields?space=${bp}`);
  const people = useTaskPeople(bp);
  const sprints = useSprints(bp);
  const versions = useVersions(bp);
  const infos = useMemo(() => {
    const m = new Map<string, SearchFieldInfo>();
    for (const f of fields.data?.fields ?? []) if (!HIDDEN_FIELDS.has(f.field)) m.set(f.field, f);
    for (const f of fields.data?.custom ?? []) m.set(f.field, f);
    return m;
  }, [fields.data]);
  const lookups = useMemo(() => ({ people: people.data ?? [], sprints: sprints.data ?? [], versions: versions.data ?? [] }), [people.data, sprints.data, versions.data]);
  const fieldName = useCallback((f: string) => FIELD_LABEL_FB[f] ?? infos.get(f)?.name ?? f, [infos]);
  const valueLabel = useCallback((c: SearchCond) => chipValueLabel(c, { space, infos, ...lookups }), [space, infos, lookups]);
  /** The values to pick for "là", or null when the field takes a typed value (dates, numbers). */
  const options = useCallback(
    (field: string, counts?: Record<string, number>): Option[] | null => {
      const info = infos.get(field);
      if (field === "label" || field === "component") return Object.keys(counts ?? {}).sort().map((v) => ({ value: v, label: v }));
      const pairs = valueOptions(field, info?.kind ?? "", "is", space, info, lookups);
      if (!pairs) return null;
      return pairs.map(([value, label]) => ({
        value,
        label,
        lead: info?.kind === "person" ? <PersonAvatar person={lookups.people.find((p) => String(p.id) === value) ?? null} /> : undefined,
      }));
    },
    [infos, space, lookups],
  );
  return { space, fields, infos, lookups, fieldName, valueLabel, options };
}

export type FilterCatalogue = ReturnType<typeof useFilterCatalogue>;

type Counts = TaskSearchFacets["counts"];

/** Per value counts of `fields` among the tasks matching `tree` (null: not asked). */
export function useFacets(bp: number, tree: SearchTree, fields: string[] | null) {
  const asked = (fields ?? []).filter((f) => FACETS.has(f));
  const body = asked.length ? { space: [bp], tree, fields: asked } : null;
  return useSWR<Counts>(body ? `/tasks/search/facets#${JSON.stringify(body)}` : null, async () => (await api.post<TaskSearchFacets>("/tasks/search/facets", body)).counts, {
    keepPreviousData: true,
    revalidateOnFocus: false,
  });
}

/** The value without the "là" chips of `field`: what its counts are taken over. */
const without = (v: FilterValue, field: string): FilterValue => ({ ...v, chips: v.chips.filter((c) => !(c.field === field && c.op === "is")) });
const isValues = (v: FilterValue, field: string) => v.chips.filter((c) => c.field === field && c.op === "is").map((c) => first(c.value));

/** Turns "là" picks of a field on or off: one chip per value (ORed by toTree). */
function toggleValue(v: FilterValue, field: string, value: string, kind: string): FilterValue {
  const on = v.chips.some((c) => c.field === field && c.op === "is" && first(c.value) === value);
  if (on) return { ...v, chips: v.chips.filter((c) => !(c.field === field && c.op === "is" && first(c.value) === value)) };
  const cond: SearchCond = { field, op: "is", value: kind === "bool" ? value === "true" : [value] };
  return { ...v, chips: [...v.chips, { ...cond, id: chipId() }] };
}

/* ------------------------------------------------------------------ popover */

/**
 * A popover under its anchor, in a portal (the bar clips its overflow): focus goes in and stays in
 * (Tab cycles), Escape or a click away closes it and focus returns to the anchor.
 */
export function Popover({
  anchor,
  onClose,
  label,
  width = 280,
  align = "start",
  className,
  children,
}: {
  anchor: RefObject<HTMLElement | null>;
  onClose: () => void;
  label: string;
  width?: number;
  align?: "start" | "end";
  className?: string;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useLayoutEffect(() => {
    const place = () => {
      const r = anchor.current?.getBoundingClientRect();
      if (!r) return;
      const w = Math.min(width, window.innerWidth - 16);
      const left = Math.max(8, Math.min(align === "end" ? r.right - w : r.left, window.innerWidth - w - 8));
      setPos({ top: r.bottom + 4, left });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [anchor, width, align]);

  useEffect(() => {
    const opener = anchor.current;
    const target =
      box.current?.querySelector<HTMLElement>("[autofocus], [data-autofocus]") ?? box.current?.querySelector<HTMLElement>("input, select, button, [tabindex='0']");
    target?.focus();
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (box.current?.contains(t) || anchor.current?.contains(t)) return;
      close.current();
    };
    document.addEventListener("pointerdown", away);
    return () => {
      document.removeEventListener("pointerdown", away);
      if (opener && document.activeElement && (box.current?.contains(document.activeElement) || document.activeElement === document.body)) opener.focus();
    };
  }, [anchor]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      e.preventDefault();
      close.current();
      anchor.current?.focus();
      return;
    }
    if (e.key !== "Tab" || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea, [tabindex='0']")];
    if (!items.length) return;
    const firstEl = items[0];
    const lastEl = items[items.length - 1];
    if (e.shiftKey && document.activeElement === firstEl) {
      e.preventDefault();
      lastEl.focus();
    } else if (!e.shiftKey && document.activeElement === lastEl) {
      e.preventDefault();
      firstEl.focus();
    }
  }

  return createPortal(
    <div
      ref={box}
      role="dialog"
      aria-label={label}
      className={`fb-pop${className ? ` ${className}` : ""}`}
      style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width: Math.min(width, typeof window === "undefined" ? width : window.innerWidth - 16) }}
      onKeyDown={onKeyDown}
    >
      {children}
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------------ value list */

/**
 * The values of one field with a search, several picked, a count each ("Bug · 3"). ↑↓ move, Enter
 * or Space toggles, Backspace in the empty search drops the last pick.
 */
function ValueList({
  title,
  options,
  picked,
  counts,
  onToggle,
  onBack,
  onClear,
  loading,
}: {
  title: string;
  options: Option[];
  picked: string[];
  counts?: Record<string, number>;
  onToggle: (value: string) => void;
  onBack?: () => void;
  onClear?: () => void;
  loading?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? options.filter((o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)) : options;
  }, [options, query]);
  useEffect(() => setActive(0), [query]);

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, shown.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = shown[active];
      if (o) onToggle(o.value);
    } else if (e.key === "Backspace" && !query && picked.length) {
      e.preventDefault();
      onToggle(picked[picked.length - 1]);
    }
  }

  return (
    <div className="fb-values">
      <div className="fb-pop-head">
        {onBack && (
          <button type="button" className="icon-btn fb-back" aria-label="Quay lại danh sách trường" onClick={onBack}>
            <Icon name="minus" size={14} />
          </button>
        )}
        <strong>{title}</strong>
        {picked.length > 0 && onClear && (
          <button type="button" className="link small fb-pop-clear" onClick={onClear}>
            Bỏ chọn
          </button>
        )}
      </div>
      <label className="fb-pop-search">
        <Icon name="search" size={14} />
        <span className="sr-only">Tìm giá trị {title}</span>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
          placeholder="Tìm…"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined}
          data-autofocus
        />
      </label>
      <ul className="fb-list" id={listId} role="listbox" aria-multiselectable="true" aria-label={title}>
        {shown.map((o, i) => {
          const on = picked.includes(o.value);
          const n = counts?.[o.value];
          return (
            <li
              key={o.value}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={on}
              className={`fb-opt${i === active ? " active" : ""}${on ? " on" : ""}`}
              onPointerDown={(e) => e.preventDefault()}
              onClick={() => {
                setActive(i);
                onToggle(o.value);
              }}
            >
              <span className="fb-check" aria-hidden="true">
                {on && <Icon name="check" size={12} />}
              </span>
              {o.lead}
              <span className="fb-opt-label">{o.label}</span>
              {counts && <span className="fb-opt-count">{n ?? (o.value === "active" ? "" : 0)}</span>}
            </li>
          );
        })}
        {!shown.length && <li className="fb-empty small muted">{loading ? "Đang tải…" : "Không có giá trị khớp"}</li>}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ the bar */

export interface FilterBarProps {
  space: TaskSpace;
  me: Me | undefined;
  /** "list", "board", "backlog"…: remembers the pinned fields per screen. */
  screen: string;
  value: FilterValue;
  onChange: (next: FilterValue) => void;
  /** Fields under "+ Lọc" (default: the whole catalogue, in UI/UX §3.4 order). */
  fields?: string[];
  /** Pinned field chips, at most 2 (default Loại, Ưu tiên); the person can change them. */
  pinned?: string[];
  /** "Của tôi" and the avatar stack (default on). */
  people?: boolean;
  /** "Bug · 3" counts in the value lists (default on). */
  counts?: boolean;
  /** Matching tasks; null while loading. */
  total?: number | null;
  unit?: string;
  /** A small label before the right side ("Sprint demo · còn 6 ngày"). */
  status?: ReactNode;
  /** "★ Bộ lọc" (SavedFilterMenu). */
  saved?: ReactNode;
  /** The "⧉ Hiển thị" popover's content; no button without it. */
  display?: ReactNode;
  /** More buttons at the right end (CSV). */
  actions?: ReactNode;
  /** Takes the row's place (the bulk bar), so nothing below moves. */
  replaceWith?: ReactNode;
  placeholder?: string;
}

const PIN_KEY = (screen: string) => `cp:fb-pins:${screen}`;

function readPins(screen: string, fallback: string[]): string[] {
  try {
    const raw = window.localStorage.getItem(PIN_KEY(screen));
    const v: unknown = raw ? JSON.parse(raw) : null;
    return Array.isArray(v) && v.every((x) => typeof x === "string") ? (v as string[]).slice(0, 2) : fallback;
  } catch {
    return fallback;
  }
}

type Open = { kind: "add" } | { kind: "field"; field: string; anchor: RefObject<HTMLElement | null> } | { kind: "edit"; chip: ChipCond; anchor: RefObject<HTMLElement | null> } | { kind: "people" } | { kind: "more" } | { kind: "display" } | null;

export function FilterBar(props: FilterBarProps) {
  const { space, me, screen, value, onChange, people = true, counts = true, total, unit = "task", status, saved, display, actions, replaceWith } = props;
  const bp = space.business_project_id;
  const cat = useFilterCatalogue(space);
  const [pins, setPinsState] = useState<string[]>(props.pinned ?? DEFAULT_PINNED);
  useEffect(() => setPinsState(readPins(screen, props.pinned ?? DEFAULT_PINNED)), [screen, props.pinned]);
  const setPins = (next: string[]) => {
    setPinsState(next);
    try {
      window.localStorage.setItem(PIN_KEY(screen), JSON.stringify(next));
    } catch {
      /* remembered for this page only */
    }
  };
  const [open, setOpen] = useState<Open>(null);
  const close = useCallback(() => setOpen(null), []);
  const latest = useRef(value);
  latest.current = value;
  const change = useCallback((next: FilterValue) => onChange(next), [onChange]);

  const searchRef = useRef<HTMLInputElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const peopleRef = useRef<HTMLButtonElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const displayRef = useRef<HTMLButtonElement>(null);

  // Keyboard (UI/UX §7). Capture phase, so `/` beats the global "find a task" palette here.
  useEffect(() => {
    if (replaceWith) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || typingIn(e.target) || dialogOpen()) return;
      if (document.querySelector(".fb-pop")) return;
      const k = e.key.toLowerCase();
      if ((k === "/" || k === "f") && !e.shiftKey) {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (k === "f" && e.shiftKey) {
        e.preventDefault();
        setOpen({ kind: "add" });
      } else if (k === "x" && e.shiftKey) {
        e.preventDefault();
        change({ text: "", chips: [] });
      } else if (k === "v" && !e.shiftKey && display) {
        e.preventDefault();
        setOpen({ kind: "display" });
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [replaceWith, display, change]);

  // People: the 5 with most open tasks, those picked first.
  const peopleCounts = useFacets(bp, { all: [{ field: "category", op: "is_not", value: ["done"] }] }, people ? ["assignee"] : null);
  const pickedPeople = isValues(value, "assignee").filter((v) => v !== "me" && v !== "none");
  const mine = isValues(value, "assignee").includes("me");
  const stack = useMemo(() => {
    const byCount = Object.entries(peopleCounts.data?.assignee ?? {})
      .filter(([id]) => id !== "none")
      .sort((a, b) => b[1] - a[1])
      .map(([id]) => id);
    const known = cat.lookups.people.map((p) => String(p.id));
    const order = [...pickedPeople, ...byCount.filter((id) => !pickedPeople.includes(id)), ...known.filter((id) => !byCount.includes(id) && !pickedPeople.includes(id))];
    return order.filter((id) => known.includes(id));
  }, [peopleCounts.data, cat.lookups.people, pickedPeople]);
  const stackShown = stack.slice(0, Math.max(PEOPLE_SHOWN, pickedPeople.length));
  const stackMore = stack.length - stackShown.length;

  // Chips on the bar: pinned fields and people have their own control; the rest, grouped by field.
  type Group = { key: string; field: string; chips: ChipCond[]; text: string };
  const groups: Group[] = useMemo(() => {
    const out: Group[] = [];
    const byField = new Map<string, Group>();
    for (const c of value.chips) {
      if (c.op === "is" && pins.includes(c.field)) continue;
      if (c.field === "assignee" && c.op === "is" && first(c.value) !== "none" && people) continue;
      if (c.op === "is") {
        const g = byField.get(c.field);
        if (g) {
          g.chips.push(c);
          continue;
        }
        const fresh = { key: `is:${c.field}`, field: c.field, chips: [c], text: "" };
        byField.set(c.field, fresh);
        out.push(fresh);
      } else out.push({ key: c.id, field: c.field, chips: [c], text: "" });
    }
    for (const g of out) {
      const kind = cat.infos.get(g.field)?.kind ?? "";
      const c = g.chips[0];
      g.text =
        c.op === "is"
          ? g.chips.map((x) => cat.valueLabel(x)).join(", ")
          : [opLabel(kind, c.op), cat.valueLabel(c)].filter(Boolean).join(" ");
    }
    return out;
  }, [value.chips, pins, people, cat]);
  // At most 4 chips, fewer when the row is full: the rest fold into "+N bộ lọc" (no wrap, no jump).
  const chipsRef = useRef<HTMLDivElement>(null);
  const sig = `${groups.map((g) => g.key + g.text).join("|")}#${pins.join()}#${value.text ? 1 : 0}`;
  const [fit, setFit] = useState<{ sig: string; n: number }>({ sig: "", n: CHIPS_SHOWN });
  const slots = fit.sig === sig ? fit.n : CHIPS_SHOWN;
  useLayoutEffect(() => {
    const el = chipsRef.current;
    if (el && el.scrollWidth > el.clientWidth + 1 && shownCount > 0) setFit({ sig, n: shownCount - 1 });
  });
  useEffect(() => {
    const again = () => setFit({ sig: "", n: CHIPS_SHOWN });
    window.addEventListener("resize", again);
    return () => window.removeEventListener("resize", again);
  }, []);
  // Past 4 chips: 3 and "+N"; "+N" and "Xoá lọc" sit outside the clipped strip, always seen.
  const shownCount = Math.min(groups.length > CHIPS_SHOWN ? CHIPS_SHOWN - 1 : CHIPS_SHOWN, slots, groups.length);
  const shownGroups = groups.slice(0, shownCount);
  const hiddenGroups = groups.slice(shownGroups.length);
  const anyFilter = filterCount(value) > 0;

  const removeGroup = (g: Group) => change({ ...value, chips: value.chips.filter((c) => !g.chips.some((x) => x.id === c.id)) });
  const openGroup = (g: Group, anchor: RefObject<HTMLElement | null>) => {
    const c = g.chips[0];
    if (c.op === "is" && cat.options(g.field) !== null) setOpen({ kind: "field", field: g.field, anchor });
    else setOpen({ kind: "edit", chip: c, anchor });
  };

  if (replaceWith)
    return (
      <div className="fb-bar fb-replaced" role="toolbar" aria-label="Thao tác hàng loạt">
        {replaceWith}
      </div>
    );

  return (
    <div className="fb-bar" role="toolbar" aria-label="Lọc task">
      <SmartSearch ref={searchRef} bp={bp} value={value} onChange={change} placeholder={props.placeholder} />
      <div className="fb-fixed">
        {people && (
          <>
            <button
              type="button"
              className={`fb-chip fb-toggle${mine ? " on" : ""}`}
              aria-pressed={mine}
              onClick={() => change(toggleValue(latest.current, "assignee", "me", "person"))}
              data-tip="Chỉ task giao cho tôi"
            >
              <Icon name="user" size={14} /> Của tôi
            </button>
            <div className="fb-people" role="group" aria-label="Lọc theo người làm">
              {stackShown.map((id) => {
                const p = cat.lookups.people.find((x) => String(x.id) === id) ?? null;
                const on = pickedPeople.includes(id);
                const n = peopleCounts.data?.assignee?.[id];
                return (
                  <button
                    key={id}
                    type="button"
                    className={`fb-face${on ? " on" : ""}`}
                    aria-pressed={on}
                    aria-label={`${on ? "Bỏ lọc" : "Lọc"} người làm: ${personName(p)}${n ? ` (${n} task đang mở)` : ""}`}
                    data-tip={personName(p)}
                    onClick={() => change(toggleValue(latest.current, "assignee", id, "person"))}
                  >
                    <PersonAvatar person={p} />
                  </button>
                );
              })}
              <button
                ref={peopleRef}
                type="button"
                className="fb-face fb-face-more"
                aria-label={stackMore > 0 ? `Thêm ${stackMore} người` : "Chọn người làm"}
                aria-haspopup="dialog"
                aria-expanded={open?.kind === "people"}
                onClick={() => setOpen(open?.kind === "people" ? null : { kind: "people" })}
              >
                {stackMore > 0 ? `+${stackMore}` : <Icon name="members" size={14} />}
              </button>
            </div>
          </>
        )}
        {pins.map((field) => (
          <PinnedChip key={field} field={field} value={value} cat={cat} onOpen={(anchor) => setOpen({ kind: "field", field, anchor })} onClear={() => change(without(value, field))} />
        ))}
        <button
          ref={addRef}
          type="button"
          className="fb-chip fb-add"
          aria-haspopup="dialog"
          aria-expanded={open?.kind === "add"}
          aria-keyshortcuts="Shift+F"
          onClick={() => setOpen(open?.kind === "add" ? null : { kind: "add" })}
          disabled={!cat.fields.data}
        >
          <Icon name="plus" size={14} /> Lọc
        </button>
      </div>
      <div className="fb-chips" ref={chipsRef}>
        {shownGroups.map((g) => (
          <ActiveChip key={g.key} label={cat.fieldName(g.field)} text={g.text} onOpen={(a) => openGroup(g, a)} onRemove={() => removeGroup(g)} />
        ))}
        {cat.fields.error && (
          <button type="button" className="link small fb-error" onClick={() => void cat.fields.mutate()} title={errorText(cat.fields.error)}>
            Lỗi tải trường · thử lại
          </button>
        )}
      </div>
      {hiddenGroups.length > 0 && (
        <button
          ref={moreRef}
          type="button"
          className="fb-chip fb-more"
          aria-haspopup="dialog"
          aria-expanded={open?.kind === "more"}
          onClick={() => setOpen(open?.kind === "more" ? null : { kind: "more" })}
        >
          +{hiddenGroups.length} bộ lọc <Icon name="more" size={12} />
        </button>
      )}
      {anyFilter && (
        <button type="button" className="link small fb-clear" onClick={() => change({ text: "", chips: [] })} aria-keyshortcuts="Shift+X">
          Xoá lọc
        </button>
      )}
      <div className="fb-right">
        {status && <span className="fb-status small">{status}</span>}
        <span className={`fb-count${anyFilter ? " filtered" : ""}`} aria-live="polite">
          {total === null || total === undefined ? "…" : `${total} ${unit}`}
          {anyFilter && <span className="fb-count-tag"> · đã lọc</span>}
        </span>
        {saved}
        {display && (
          <button
            ref={displayRef}
            type="button"
            className="fb-chip fb-display"
            aria-haspopup="dialog"
            aria-expanded={open?.kind === "display"}
            aria-keyshortcuts="V"
            onClick={() => setOpen(open?.kind === "display" ? null : { kind: "display" })}
          >
            <Icon name="settings" size={14} /> Hiển thị
          </button>
        )}
        {actions}
      </div>

      {open?.kind === "add" && (
        <Popover anchor={addRef} onClose={close} label="Thêm bộ lọc">
          <AddFilter cat={cat} bp={bp} value={value} people={people} meId={me?.id} fields={props.fields} pins={pins} onPins={setPins} counts={counts} onChange={change} latest={latest} />
        </Popover>
      )}
      {open?.kind === "field" && (
        <Popover anchor={open.anchor} onClose={close} label={`Lọc ${cat.fieldName(open.field)}`}>
          <FieldValues field={open.field} cat={cat} bp={bp} meId={me?.id} value={value} counts={counts} onChange={change} latest={latest} />
        </Popover>
      )}
      {open?.kind === "edit" && (
        <Popover anchor={open.anchor} onClose={close} label="Sửa điều kiện" width={320}>
          <ChipEditor
            space={space}
            initial={open.chip}
            infos={cat.infos}
            fieldName={cat.fieldName}
            lookups={cat.lookups}
            onCancel={close}
            onSave={(c) => {
              change({ ...latest.current, chips: latest.current.chips.map((x) => (x.id === open.chip.id ? { ...c, id: x.id } : x)) });
              close();
            }}
          />
        </Popover>
      )}
      {open?.kind === "people" && (
        <Popover anchor={peopleRef} onClose={close} label="Lọc người làm">
          <FieldValues field="assignee" cat={cat} bp={bp} meId={me?.id} value={value} counts={counts} onChange={change} latest={latest} />
        </Popover>
      )}
      {open?.kind === "more" && (
        <Popover anchor={moreRef} onClose={close} label="Bộ lọc khác">
          <ul className="fb-list">
            {hiddenGroups.map((g) => (
              <li key={g.key} className="fb-opt fb-more-row">
                <span className="fb-opt-label">
                  <strong>{cat.fieldName(g.field)}</strong>: {g.text}
                </span>
                <button type="button" className="icon-btn" aria-label={`Bỏ lọc ${cat.fieldName(g.field)}: ${g.text}`} onClick={() => removeGroup(g)}>
                  <Icon name="close" size={14} />
                </button>
              </li>
            ))}
          </ul>
        </Popover>
      )}
      {open?.kind === "display" && display && (
        <Popover anchor={displayRef} onClose={close} label="Hiển thị" width={320} align="end">
          {display}
        </Popover>
      )}
    </div>
  );
}

function ActiveChip({ label, text, onOpen, onRemove }: { label: string; text: string; onOpen: (anchor: RefObject<HTMLElement | null>) => void; onRemove: () => void }) {
  const ref = useRef<HTMLSpanElement>(null);
  return (
    <span className="fb-chip fb-active" ref={ref}>
      <button type="button" className="fb-chip-text" onClick={() => onOpen(ref)} aria-label={`Sửa lọc ${label}: ${text}`}>
        <span className="fb-chip-field">{label}:</span> {text}
      </button>
      <button type="button" className="fb-x" aria-label={`Bỏ lọc ${label}: ${text}`} onClick={onRemove}>
        <Icon name="close" size={12} />
      </button>
    </span>
  );
}

function PinnedChip({ field, value, cat, onOpen, onClear }: { field: string; value: FilterValue; cat: FilterCatalogue; onOpen: (anchor: RefObject<HTMLElement | null>) => void; onClear: () => void }) {
  const ref = useRef<HTMLSpanElement>(null);
  const picked = value.chips.filter((c) => c.field === field && c.op === "is");
  const name = cat.fieldName(field);
  const text = picked.map((c) => cat.valueLabel(c)).join(", ");
  return (
    <span className={`fb-chip fb-pinned${picked.length ? " fb-active" : ""}`} ref={ref}>
      <button type="button" className="fb-chip-text" aria-haspopup="dialog" onClick={() => onOpen(ref)} aria-label={picked.length ? `Sửa lọc ${name}: ${text}` : `Lọc theo ${name}`}>
        {picked.length ? (
          <>
            <span className="fb-chip-field">{name}:</span> {text}
          </>
        ) : (
          <>
            {name} <Icon name="more" size={12} />
          </>
        )}
      </button>
      {picked.length > 0 && (
        <button type="button" className="fb-x" aria-label={`Bỏ lọc ${name}: ${text}`} onClick={onClear}>
          <Icon name="close" size={12} />
        </button>
      )}
    </span>
  );
}

/** The value list of one field, its counts over the other filters. */
function FieldValues({
  field,
  cat,
  bp,
  meId,
  value,
  counts,
  onChange,
  latest,
  onBack,
}: {
  field: string;
  cat: FilterCatalogue;
  bp: number;
  meId?: number;
  value: FilterValue;
  counts: boolean;
  onChange: (v: FilterValue) => void;
  latest: RefObject<FilterValue>;
  onBack?: () => void;
}) {
  const others = useMemo(() => filterTree(without(value, field)), [value, field]);
  const facet = useFacets(bp, others, counts || field === "label" || field === "component" ? [field] : null);
  const info = cat.infos.get(field);
  const options = cat.options(field, facet.data?.[field]) ?? [];
  const picked = isValues(value, field);
  return (
    <ValueList
      title={cat.fieldName(field)}
      options={options}
      picked={picked}
      counts={counts && FACETS.has(field) ? withMe(facet.data?.[field] ?? {}, cat.infos.get(field)?.kind, meId) : undefined}
      loading={!facet.data && !facet.error}
      onToggle={(v) => onChange(toggleValue(latest.current ?? value, field, v, info?.kind ?? ""))}
      onClear={() => onChange(without(latest.current ?? value, field))}
      onBack={onBack}
    />
  );
}

/** "Tôi" counts what the person's own id counts. */
function withMe(counts: Record<string, number>, kind: string | undefined, meId: number | undefined): Record<string, number> {
  return kind === "person" && meId !== undefined ? { ...counts, me: counts[String(meId)] ?? 0 } : counts;
}

/** "+ Lọc": the fields (search, ↑↓, Enter), then the picked field's values or its editor. */
function AddFilter({
  cat,
  bp,
  value,
  fields,
  pins,
  onPins,
  counts,
  people,
  meId,
  onChange,
  latest,
}: {
  cat: FilterCatalogue;
  bp: number;
  value: FilterValue;
  people: boolean;
  meId?: number;
  fields?: string[];
  pins: string[];
  onPins: (next: string[]) => void;
  counts: boolean;
  onChange: (v: FilterValue) => void;
  latest: RefObject<FilterValue>;
}) {
  const [field, setField] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const all = useMemo(() => {
    const known = [...cat.infos.keys()];
    // Pinned fields stay listed (their pin is how they are unpinned); people have the avatars.
    const base = fields ?? [
      ...ADD_ORDER.filter((f) => known.includes(f)),
      ...known.filter((f) => !ADD_ORDER.includes(f) && !f.startsWith("custom:") && !FLAGS.some((x) => x.cond.field === f) && (f !== "assignee" || !people)),
      ...known.filter((f) => f.startsWith("custom:")),
    ];
    return base.filter((f) => cat.infos.has(f));
  }, [cat.infos, fields, people]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? all.filter((f) => cat.fieldName(f).toLowerCase().includes(q)) : all;
  }, [all, query, cat]);
  useEffect(() => setActive(0), [query]);

  if (field) {
    const hasList = cat.options(field) !== null || field === "label" || field === "component";
    if (hasList) return <FieldValues field={field} cat={cat} bp={bp} meId={meId} value={value} counts={counts} onChange={onChange} latest={latest} onBack={() => setField(null)} />;
    const info = cat.infos.get(field);
    return (
      <div className="fb-editor">
        <div className="fb-pop-head">
          <button type="button" className="icon-btn fb-back" aria-label="Quay lại danh sách trường" onClick={() => setField(null)}>
            <Icon name="minus" size={14} />
          </button>
          <strong>{cat.fieldName(field)}</strong>
        </div>
        {field === "due" && (
          <div className="fb-presets" role="group" aria-label="Hạn nhanh">
            {[
              ["overdue", "Quá hạn"],
              ["week", "7 ngày tới"],
            ].map(([v, l]) => (
              <button key={v} type="button" className="fb-chip" onClick={() => onChange({ ...latest.current, chips: [...(latest.current?.chips ?? []), { id: chipId(), field: "due", op: "is", value: v }] })}>
                {l}
              </button>
            ))}
          </div>
        )}
        <ChipEditor
          space={cat.space}
          initial={{ id: "", field, op: info?.ops[0] ?? "is", value: [] }}
          infos={cat.infos}
          fieldName={cat.fieldName}
          lookups={cat.lookups}
          onCancel={() => setField(null)}
          onSave={(c) => {
            onChange({ ...latest.current, chips: [...(latest.current?.chips ?? []), { ...c, id: chipId() }] });
            setField(null);
          }}
        />
      </div>
    );
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, shown.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (shown[active]) setField(shown[active]);
    } else if (e.key === "Backspace" && !query) {
      const cur = latest.current;
      if (cur?.chips.length) {
        e.preventDefault();
        onChange({ ...cur, chips: cur.chips.slice(0, -1) });
      }
    }
  }

  const flagOn = (c: SearchCond) => value.chips.some((x) => x.field === c.field && x.op === c.op && JSON.stringify(x.value) === JSON.stringify(c.value));
  return (
    <div className="fb-add-pop">
      <label className="fb-pop-search">
        <Icon name="search" size={14} />
        <span className="sr-only">Tìm trường</span>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
          placeholder="Tìm trường…"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined}
          data-autofocus
        />
      </label>
      <ul className="fb-list" id={listId} role="listbox" aria-label="Trường">
        {shown.map((f, i) => {
          const pinnedHere = pins.includes(f);
          return (
            <li
              key={f}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={`fb-opt${i === active ? " active" : ""}`}
              onPointerDown={(e) => e.preventDefault()}
              onClick={() => setField(f)}
            >
              <span className="fb-opt-label">{cat.fieldName(f)}</span>
              {cat.options(f) !== null || f === "label" ? <Icon name="more" size={12} /> : null}
              <button
                type="button"
                tabIndex={-1}
                className={`icon-btn fb-pin${pinnedHere ? " on" : ""}`}
                aria-label={pinnedHere ? `Bỏ ghim ${cat.fieldName(f)}` : `Ghim ${cat.fieldName(f)} lên thanh lọc`}
                aria-pressed={pinnedHere}
                data-tip={pinnedHere ? "Bỏ ghim" : "Ghim (tối đa 2)"}
                onClick={(e) => {
                  e.stopPropagation();
                  onPins(pinnedHere ? pins.filter((x) => x !== f) : [...pins, f].slice(-2));
                }}
              >
                <Icon name="pin" size={12} />
              </button>
            </li>
          );
        })}
        {!shown.length && <li className="fb-empty small muted">Không có trường khớp</li>}
      </ul>
      {!fields && (
        <div className="fb-flags" role="group" aria-label="Lọc nhanh">
          {FLAGS.filter((x) => cat.infos.has(x.cond.field)).map((x) => {
            const on = flagOn(x.cond);
            return (
              <button
                key={x.label}
                type="button"
                className={`fb-chip fb-toggle${on ? " on" : ""}`}
                aria-pressed={on}
                onClick={() => {
                  const cur = latest.current ?? value;
                  onChange(
                    on
                      ? { ...cur, chips: cur.chips.filter((c) => !(c.field === x.cond.field && c.op === x.cond.op && JSON.stringify(c.value) === JSON.stringify(x.cond.value))) }
                      : { ...cur, chips: [...cur.chips, { ...x.cond, id: chipId() }] },
                  );
                }}
              >
                {x.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ smart search */

interface NlChip {
  label: string;
  cond?: SearchCond;
}

/**
 * The search box (UI/UX §3.3): text applies 200 ms after typing stops; a sentence the dự án's words
 * explain ("bug cao chưa giao") is offered as chips under the box, Enter takes them, and when every
 * word was understood (two or more) they are taken on their own after a short pause. Backspace in
 * the empty box drops the last chip.
 */
function SmartSearch({ ref, bp, value, onChange, placeholder }: { ref: RefObject<HTMLInputElement | null>; bp: number; value: FilterValue; onChange: (v: FilterValue) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(value.text);
  const [focus, setFocus] = useState(false);
  const [suggest, setSuggest] = useState<{ text: string; chips: NlChip[]; rest: string } | null>(null);
  const latest = useRef(value);
  latest.current = value;
  const typed = useRef(false);

  // An outside change (Xoá lọc, a saved filter, Back) shows in the box.
  useEffect(() => {
    if (!typed.current) setDraft(value.text);
  }, [value.text]);

  // Text: 200 ms after the last key.
  useEffect(() => {
    if (!typed.current) return;
    const t = window.setTimeout(() => {
      typed.current = false;
      if (latest.current.text !== draft) onChange({ ...latest.current, text: draft });
    }, 200);
    return () => window.clearTimeout(t);
  }, [draft, onChange]);

  // The sentence: asked after a pause; taken alone when fully understood.
  useEffect(() => {
    const sentence = draft.trim();
    if (sentence.length < 2) {
      setSuggest(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(async () => {
      try {
        const res = await api.post<{ chips: NlChip[] }>("/tasks/nl-filter", { space: bp, text: sentence });
        if (!live) return;
        const conds = res.chips.filter((c) => c.cond && c.cond.field !== "text");
        const rest = res.chips.find((c) => c.cond?.field === "text");
        if (!conds.length) return setSuggest(null);
        setSuggest({ text: sentence, chips: conds, rest: rest ? first(rest.cond?.value) : "" });
      } catch {
        if (live) setSuggest(null);
      }
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [draft, bp]);

  const take = useCallback(
    (s: { chips: NlChip[]; rest: string }) => {
      const cur = latest.current;
      const fresh = s.chips.map((c) => c.cond as SearchCond);
      const fields = new Set(fresh.map((c) => c.field));
      const kept = cur.chips.filter((c) => !(c.op === "is" && fields.has(c.field)));
      typed.current = false;
      setDraft(s.rest);
      setSuggest(null);
      onChange({ text: s.rest, chips: [...kept, ...fresh.map((c) => ({ ...c, id: chipId() }))] });
    },
    [onChange],
  );

  useEffect(() => {
    if (!suggest || suggest.rest || suggest.text.split(/\s+/).length < 2 || suggest.text !== draft.trim()) return;
    const t = window.setTimeout(() => take(suggest), 700);
    return () => window.clearTimeout(t);
  }, [suggest, draft, take]);

  const shownSuggest = focus && suggest && suggest.text === draft.trim() ? suggest : null;
  return (
    <div className={`fb-search${draft ? " wide" : ""}`} role="search">
      <Icon name="search" size={14} />
      <label className="sr-only" htmlFor={`fb-q-${bp}`}>
        Tìm hoặc lọc bằng câu
      </label>
      <input
        ref={ref}
        id={`fb-q-${bp}`}
        type="text"
        value={draft}
        maxLength={300}
        placeholder={placeholder ?? "Tìm hoặc gõ “bug cao chưa giao”…"}
        aria-keyshortcuts="/ F"
        aria-describedby={shownSuggest ? `fb-q-${bp}-hint` : undefined}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        onChange={(e) => {
          typed.current = true;
          setDraft(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && suggest && suggest.text === draft.trim()) {
            e.preventDefault();
            take(suggest);
          } else if (e.key === "Escape") {
            e.currentTarget.blur();
          } else if (e.key === "Backspace" && !draft && latest.current.chips.length) {
            e.preventDefault();
            onChange({ ...latest.current, chips: latest.current.chips.slice(0, -1) });
          }
        }}
      />
      {draft && (
        <button
          type="button"
          className="fb-x"
          aria-label="Xoá chữ tìm"
          onClick={() => {
            typed.current = false;
            setDraft("");
            onChange({ ...latest.current, text: "" });
          }}
        >
          <Icon name="close" size={12} />
        </button>
      )}
      {shownSuggest && (
        <div className="fb-suggest" id={`fb-q-${bp}-hint`} role="status" onPointerDown={(e) => e.preventDefault()}>
          <button type="button" className="fb-suggest-btn" onClick={() => take(shownSuggest)}>
            <kbd>Enter</kbd> lọc theo: {shownSuggest.chips.map((c) => c.label).join(" · ")}
            {shownSuggest.rest && <span className="muted"> · tìm “{shownSuggest.rest}”</span>}
          </button>
        </div>
      )}
    </div>
  );
}
