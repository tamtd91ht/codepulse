"use client";

import { type DragEvent, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { MoreMenu } from "@/components/MoreMenu";
import { Alert, Loading } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { CodePathsField } from "@/components/TestcaseLinks";
import { type Section, type Suite, splitList } from "@/lib/testcases";

/*
 * The section tree of a suite (docs/specs/30 §13.2), TestRail's left tree: pick a section to see
 * its cases (sub-sections included), add / rename / delete, drag a case row onto a section to move
 * it, drag a section onto another (or onto the suite's root) to move it. The "…" menu of a section
 * does the same moves without a mouse.
 */

/** dataTransfer type of dragged case rows (JSON list of ids). */
export const CASE_DRAG_TYPE = "application/x-codepulse-cases";
const SECTION_DRAG_TYPE = "application/x-codepulse-section";

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** The ids of a section's sub-sections (any depth), from the tree-ordered list. */
export function descendantsOf(sections: Section[], id: number): Set<number> {
  const out = new Set<number>();
  const children = new Map<number, number[]>();
  for (const s of sections) if (s.parent_id !== null) children.set(s.parent_id, [...(children.get(s.parent_id) ?? []), s.id]);
  const walk = (n: number) => {
    for (const c of children.get(n) ?? []) {
      out.add(c);
      walk(c);
    }
  };
  walk(id);
  return out;
}

/** `<option>`s of a section select: "— Gốc của bộ —" then each section by its path. */
export function SectionOptions({ sections, exclude, root = "Gốc của bộ (không thư mục)" }: { sections: Section[]; exclude?: Set<number>; root?: string | null }) {
  return (
    <>
      {root !== null && <option value="">{root}</option>}
      {sections
        .filter((s) => !exclude?.has(s.id))
        .map((s) => (
          <option key={s.id} value={s.id}>
            {s.path}
          </option>
        ))}
    </>
  );
}

/** Case ids carried by a drag of case rows, or null when the drag is something else. */
function draggedCases(e: DragEvent): number[] | null {
  const raw = e.dataTransfer.getData(CASE_DRAG_TYPE);
  if (!raw) return null;
  try {
    const ids = JSON.parse(raw) as unknown;
    return Array.isArray(ids) ? ids.filter((n): n is number => typeof n === "number") : null;
  } catch {
    return null;
  }
}

export function SectionTree({
  suite,
  sections,
  error,
  selected,
  onSelect,
  canEdit,
  onChanged,
  onMoveCases,
  onCreateRun,
  onMessage,
}: {
  suite: Suite;
  sections: Section[] | undefined;
  error?: Error;
  selected: number | null;
  onSelect: (id: number | null) => void;
  canEdit: boolean;
  onChanged: () => void | Promise<void>;
  onMoveCases: (ids: number[], sectionId: number | null) => void;
  onCreateRun?: (sectionId: number) => void;
  onMessage: (text: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<number | "root" | null>(null);
  const [modal, setModal] = useState<null | { kind: "add"; parent: Section | null } | { kind: "rename" | "move" | "delete"; section: Section }>(null);
  const list = useMemo(() => sections ?? [], [sections]);
  const hasChildren = useMemo(() => new Set(list.filter((s) => s.parent_id !== null).map((s) => s.parent_id as number)), [list]);
  const blocked = useMemo(() => (dragging === null ? new Set<number>() : new Set([dragging, ...descendantsOf(list, dragging)])), [dragging, list]);

  // A section is hidden when one of its ancestors is collapsed (the list is in tree order).
  const visible = useMemo(() => {
    const out: Section[] = [];
    let hideBelow: number | null = null;
    for (const s of list) {
      if (hideBelow !== null && s.depth > hideBelow) continue;
      hideBelow = null;
      out.push(s);
      if (collapsed.has(s.id) && hasChildren.has(s.id)) hideBelow = s.depth;
    }
    return out;
  }, [list, collapsed, hasChildren]);

  const reparent = async (id: number, parentId: number | null) => {
    const s = list.find((x) => x.id === id);
    if (!s || s.parent_id === parentId) return;
    try {
      await api.patch(`/testcases/sections/${id}`, { parent_id: parentId });
      const target = parentId === null ? "gốc của bộ" : `“${list.find((x) => x.id === parentId)?.name ?? ""}”`;
      onMessage(`Đã chuyển thư mục “${s.name}” vào ${target}.`);
      await onChanged();
    } catch (e) {
      onMessage(`Lỗi: ${errorText(e)}`);
    }
  };

  const dropProps = (target: number | null) => {
    const key = target ?? "root";
    return {
      onDragOver: (e: DragEvent) => {
        const types = [...e.dataTransfer.types];
        const caseDrag = types.includes(CASE_DRAG_TYPE);
        const sectionDrag = types.includes(SECTION_DRAG_TYPE) && dragging !== null && (target === null || !blocked.has(target));
        if (!canEdit || (!caseDrag && !sectionDrag)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(key);
      },
      onDragLeave: () => setOver((o) => (o === key ? null : o)),
      onDrop: (e: DragEvent) => {
        e.preventDefault();
        setOver(null);
        const ids = draggedCases(e);
        if (ids?.length) {
          onMoveCases(ids, target);
          return;
        }
        if (dragging !== null && (target === null || !blocked.has(target))) void reparent(dragging, target);
        setDragging(null);
      },
      "data-over": over === key ? "" : undefined,
    };
  };

  const toggle = (id: number) => {
    const next = new Set(collapsed);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setCollapsed(next);
  };

  const rootCount = suite.total - suite.obsolete;
  return (
    <div className="tc-sec" aria-label={`Thư mục của bộ ${suite.name}`} role="group">
      <div className="tc-nav-head">
        <strong>Thư mục</strong>
        {canEdit && (
          <span className="tc-nav-actions">
            <IconButton icon="plus" label="Thêm thư mục" onClick={() => setModal({ kind: "add", parent: null })} />
          </span>
        )}
      </div>
      {!sections ? (
        <Loading error={error} rows={3} />
      ) : (
        <ul className="tc-sec-list">
          <li className="tc-sec-row" {...dropProps(null)}>
            <span className="tc-sec-toggle" aria-hidden="true" />
            <button type="button" className={`tc-sec-name${selected === null ? " active" : ""}`} aria-current={selected === null ? "true" : undefined} onClick={() => onSelect(null)}>
              <Icon name="folder" size={14} />
              <span className="tc-suite-name">Cả bộ</span>
              <span className="small muted">{rootCount}</span>
            </button>
          </li>
          {visible.map((s) => {
            const open = !collapsed.has(s.id);
            const menu = [
              ...(onCreateRun && s.total ? [{ icon: "clipboardCheck" as const, label: "Tạo test run từ thư mục", run: () => onCreateRun(s.id) }] : []),
              ...(canEdit
                ? [
                    { icon: "plus" as const, label: "Thêm thư mục con", run: () => setModal({ kind: "add", parent: s }) },
                    { icon: "edit" as const, label: "Đổi tên", run: () => setModal({ kind: "rename", section: s }) },
                    { icon: "folder" as const, label: "Chuyển vào thư mục…", run: () => setModal({ kind: "move", section: s }) },
                    { icon: "remove" as const, label: "Xoá thư mục", danger: true, run: () => setModal({ kind: "delete", section: s }) },
                  ]
                : []),
            ];
            return (
              <li key={s.id} className="tc-sec-row" style={{ paddingLeft: `${Math.min(s.depth, 8) * 14}px` }} data-dragging={dragging === s.id ? "" : undefined} {...dropProps(s.id)}>
                {hasChildren.has(s.id) ? (
                  <button type="button" className="tc-sec-toggle" aria-expanded={open} aria-label={`${open ? "Thu gọn" : "Mở"} ${s.name}`} onClick={() => toggle(s.id)}>
                    <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
                  </button>
                ) : (
                  <span className="tc-sec-toggle" aria-hidden="true" />
                )}
                <button
                  type="button"
                  className={`tc-sec-name${selected === s.id ? " active" : ""}`}
                  aria-current={selected === s.id ? "true" : undefined}
                  title={s.description ? `${s.path}\n${s.description}` : s.path}
                  draggable={canEdit}
                  onDragStart={(e) => {
                    e.dataTransfer.effectAllowed = "move";
                    e.dataTransfer.setData(SECTION_DRAG_TYPE, String(s.id));
                    setDragging(s.id);
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setOver(null);
                  }}
                  onClick={() => onSelect(s.id)}
                >
                  <span className="tc-suite-name">{s.name}</span>
                  <span className="small muted">{s.total}</span>
                </button>
                <MoreMenu items={menu} />
              </li>
            );
          })}
          {!list.length && <li className="small muted tc-empty-nav">{canEdit ? "Chưa có thư mục. Thêm thư mục để chia testcase theo chức năng / màn hình." : "Bộ chưa chia thư mục."}</li>}
        </ul>
      )}
      {canEdit && list.length > 0 && <p className="small muted tc-sec-hint">Kéo testcase hoặc thư mục thả vào một thư mục để chuyển.</p>}

      {modal?.kind === "add" && (
        <SectionModal
          suite={suite}
          parent={modal.parent}
          onClose={() => setModal(null)}
          onSaved={async (s) => {
            onMessage(`Đã thêm thư mục “${s.name}”.`);
            if (modal.parent) setCollapsed((c) => new Set([...c].filter((x) => x !== modal.parent?.id)));
            await onChanged();
          }}
        />
      )}
      {modal?.kind === "rename" && (
        <SectionModal
          suite={suite}
          section={modal.section}
          onClose={() => setModal(null)}
          onSaved={async () => {
            onMessage("Đã đổi tên thư mục.");
            await onChanged();
          }}
        />
      )}
      {modal?.kind === "move" && <MoveSectionModal section={modal.section} sections={list} onClose={() => setModal(null)} onMove={(p) => reparent(modal.section.id, p)} />}
      {modal?.kind === "delete" && (
        <DeleteSectionModal
          section={modal.section}
          subsections={descendantsOf(list, modal.section.id).size}
          onClose={() => setModal(null)}
          onDeleted={async (got) => {
            onMessage(`Đã xoá ${got.sections} thư mục và ${got.cases} testcase.`);
            const gone = new Set([modal.section.id, ...descendantsOf(list, modal.section.id)]);
            if (selected !== null && gone.has(selected)) onSelect(null);
            await onChanged();
          }}
        />
      )}
    </div>
  );
}

/** Add a section (at the root or under `parent`), or rename one. */
function SectionModal({
  suite,
  parent,
  section,
  onClose,
  onSaved,
}: {
  suite: Suite;
  parent?: Section | null;
  section?: Section;
  onClose: () => void;
  onSaved: (s: Section) => void | Promise<void>;
}) {
  const [name, setName] = useState(section?.name ?? "");
  const [description, setDescription] = useState(section?.description ?? "");
  const [codePaths, setCodePaths] = useState((section?.code_paths ?? []).join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body = { name: name.trim(), description: description.trim() || null };
      const paths = splitList(codePaths);
      let got = section
        ? await api.patch<Section>(`/testcases/sections/${section.id}`, { ...body, code_paths: paths })
        : await api.post<Section>("/testcases/sections", { ...body, suite_id: suite.id, parent_id: parent?.id ?? null });
      if (!section && paths.length) got = await api.patch<Section>(`/testcases/sections/${got.id}`, { code_paths: paths });
      await onSaved(got);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={section ? "Đổi tên thư mục" : parent ? `Thư mục con của “${parent.name}”` : "Thư mục mới"}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            {busy ? "Đang lưu…" : section ? "Lưu" : "Thêm thư mục"}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label className="adm-field">
          <span className="small muted">Tên thư mục (chức năng / màn hình)</span>
          <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả</span>
          <textarea value={description} rows={2} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <CodePathsField projectId={suite.business_project_id} value={codePaths} onChange={setCodePaths} />
      </form>
    </Modal>
  );
}

/** Move a section under another one (or to the root): the keyboard way of dragging it. */
function MoveSectionModal({ section, sections, onClose, onMove }: { section: Section; sections: Section[]; onClose: () => void; onMove: (parentId: number | null) => Promise<void> }) {
  const [parent, setParent] = useState(section.parent_id === null ? "" : String(section.parent_id));
  const [busy, setBusy] = useState(false);
  const exclude = useMemo(() => new Set([section.id, ...descendantsOf(sections, section.id)]), [section.id, sections]);
  return (
    <Modal
      title={`Chuyển thư mục “${section.name}”`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await onMove(parent ? Number(parent) : null);
              onClose();
            }}
          >
            Chuyển
          </button>
        </>
      }
    >
      <label className="adm-field">
        <span className="small muted">Vào thư mục</span>
        <select value={parent} onChange={(e) => setParent(e.target.value)} autoFocus>
          <SectionOptions sections={sections} exclude={exclude} />
        </select>
      </label>
      <p className="small muted">Thư mục con và testcase bên trong đi theo.</p>
    </Modal>
  );
}

function DeleteSectionModal({
  section,
  subsections,
  onClose,
  onDeleted,
}: {
  section: Section;
  subsections: number;
  onClose: () => void;
  onDeleted: (got: { sections: number; cases: number }) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async () => {
    setBusy(true);
    try {
      const got = await api.delete<{ sections: number; cases: number } | undefined>(`/testcases/sections/${section.id}`);
      await onDeleted(got ?? { sections: subsections + 1, cases: section.total });
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  const empty = !section.total && !subsections;
  return (
    <Modal
      title={`Xoá thư mục “${section.name}”?`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button className="danger" onClick={() => void remove()} disabled={busy}>
            {busy ? "Đang xoá…" : empty ? "Xoá thư mục" : `Xoá thư mục và ${section.total} testcase`}
          </button>
        </>
      }
    >
      {error && <Alert tone="error">{error}</Alert>}
      {empty ? (
        <p>Thư mục đang trống.</p>
      ) : (
        <p>
          Xoá cả <strong>{subsections}</strong> thư mục con và <strong>{section.total}</strong> testcase bên trong (cùng lịch sử sửa). Không khôi phục được — muốn giữ testcase thì chuyển chúng sang thư mục khác trước.
        </p>
      )}
      <p className="small muted">{section.path}</p>
    </Modal>
  );
}
