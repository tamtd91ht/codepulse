"use client";

import { useEffect, useRef, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Message } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { FilterScope, Me, SavedTaskFilter, SearchTree } from "@/lib/types";

import { fromTree } from "./FilterChips";
import { Popover } from "./FilterBar";
import { type FilterValue, filterCount, filterTree } from "./filterState";
import { errorText, useConfirm } from "./taskParts";

/*
 * "★ Bộ lọc" (UI/UX §3.3, docs/specs/31 §22.2): pick a saved filter (it opens as chips), save the
 * current one, its sharing / pin / digest, delete it, "Khôi phục mặc định". `?filter=ID` (Ctrl+K,
 * §20.2) applies that filter once the list has loaded. Goes in FilterBar's `saved` slot.
 */

const SCOPE_LABEL: Record<FilterScope, string> = {
  me: "Chỉ mình tôi",
  team: "Team của tôi",
  project: "Mọi người xem dự án",
};

export function SavedFilterMenu({ bp, me, value, onApply, onReset }: { bp: number; me: Me | undefined; value: FilterValue; onApply: (v: FilterValue) => void; onReset: () => void }) {
  const saved = useApi<SavedTaskFilter[]>(`/tasks/search/filters?space=${bp}`);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState<"new" | "edit" | null>(null);
  const [applied, setApplied] = useState<{ id: number; tree: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const trigger = useRef<HTMLButtonElement>(null);
  const tree = filterTree(value);
  const treeJson = JSON.stringify(tree);
  const mine = (saved.data ?? []).filter((f) => f.space === null || f.space === undefined || f.space === bp);
  // The saved filter in use, while the chips are still its own.
  const current = applied && applied.tree === treeJson ? (mine.find((f) => f.id === applied.id) ?? null) : null;
  const canDelete = !!current && (current.can_delete ?? current.user_id === me?.id);

  function apply(f: SavedTaskFilter) {
    const next: FilterValue = { text: "", chips: fromTree(f.tree) };
    setApplied({ id: f.id, tree: JSON.stringify(filterTree(next)) });
    onApply(next);
    setOpen(false);
  }

  const [pending, setPending] = useState<string | null>(() => {
    try {
      return new URLSearchParams(window.location.search).get("filter");
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (!pending || !saved.data) return;
    const f = mine.find((x) => String(x.id) === pending);
    if (f) apply(f);
    setPending(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, saved.data]);

  async function remove() {
    if (!current) return;
    setOpen(false);
    if (!(await ask({ title: `Xoá bộ lọc “${current.name}”?`, body: <p>Người khác đang dùng bộ lọc chia sẻ này cũng mất nó.</p>, ok: "Xoá", danger: true }))) return;
    try {
      await api.delete(`/tasks/filters/${current.id}`);
      setApplied(null);
      await saved.mutate();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <>
      {confirmEl}
      <button
        ref={trigger}
        type="button"
        className={`fb-chip fb-saved${current ? " fb-active" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        aria-label={current ? `Bộ lọc đã lưu, đang dùng: ${current.name}` : "Bộ lọc đã lưu"}
        data-tip={current ? `Đang dùng: ${current.name}` : "Bộ lọc đã lưu"}
      >
        <Icon name="flag" size={14} /> <span className="fb-saved-name">{current ? current.name : "Bộ lọc"}</span> <Icon name="more" size={12} />
      </button>
      {open && (
        <Popover anchor={trigger} onClose={() => setOpen(false)} label="Bộ lọc đã lưu" align="end" width={300}>
          <div className="fb-saved-pop">
            {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
            <ul className="fb-list" aria-label="Bộ lọc đã lưu">
              {!saved.data && !saved.error && <li className="fb-empty small muted">Đang tải…</li>}
              {saved.error && (
                <li className="fb-empty small">
                  Không tải được.{" "}
                  <button type="button" className="link" onClick={() => void saved.mutate()}>
                    Thử lại
                  </button>
                </li>
              )}
              {saved.data && !mine.length && <li className="fb-empty small muted">Chưa có bộ lọc nào được lưu</li>}
              {mine.map((f) => (
                <li key={f.id}>
                  <button type="button" className={`fb-opt fb-opt-btn${current?.id === f.id ? " on" : ""}`} onClick={() => apply(f)} aria-current={current?.id === f.id || undefined}>
                    <span className="fb-check" aria-hidden="true">
                      {current?.id === f.id && <Icon name="check" size={12} />}
                    </span>
                    <span className="fb-opt-label">{f.name}</span>
                    {f.pinned && <Icon name="pin" size={12} />}
                    {f.shared_scope !== "me" && <span className="fb-opt-count">{f.shared_scope === "team" ? "team" : "dự án"}</span>}
                  </button>
                </li>
              ))}
            </ul>
            <div className="fb-pop-foot">
              <button
                type="button"
                className="ghost sm"
                disabled={!filterCount(value)}
                onClick={() => {
                  setOpen(false);
                  setSaving("new");
                }}
              >
                <Icon name="plus" size={14} /> Lưu bộ lọc hiện tại
              </button>
              {current?.can_edit && (
                <button
                  type="button"
                  className="ghost sm"
                  onClick={() => {
                    setOpen(false);
                    setSaving("edit");
                  }}
                >
                  <Icon name="settings" size={14} /> Chia sẻ, ghim, nhận tóm tắt
                </button>
              )}
              {canDelete && (
                <button type="button" className="ghost sm tc-danger" onClick={() => void remove()}>
                  <Icon name="remove" size={14} /> Xoá bộ lọc này
                </button>
              )}
              <button
                type="button"
                className="ghost sm"
                onClick={() => {
                  setApplied(null);
                  onReset();
                  setOpen(false);
                }}
              >
                <Icon name="history" size={14} /> Khôi phục mặc định
              </button>
            </div>
          </div>
        </Popover>
      )}
      {saving === "new" && (
        <SaveFilterModal
          bp={bp}
          tree={tree}
          onClose={() => setSaving(null)}
          onSaved={(id) => {
            setApplied({ id, tree: treeJson });
            void saved.mutate();
          }}
        />
      )}
      {saving === "edit" && current && <SaveFilterModal bp={bp} tree={tree} existing={current} onClose={() => setSaving(null)} onSaved={() => void saved.mutate()} />}
    </>
  );
}

/* Lưu bộ lọc (§21.4): its tree, who sees it, a Board pin, a daily / weekly digest. With
 * `existing`: change those settings of one's own filter (its conditions stay). */
export function SaveFilterModal({
  bp,
  tree,
  existing,
  onClose,
  onSaved,
}: {
  bp: number;
  tree: SearchTree;
  existing?: SavedTaskFilter;
  onClose: () => void;
  onSaved: (id: number) => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [scope, setScope] = useState<FilterScope>(existing?.shared_scope ?? "me");
  const [pinned, setPinned] = useState(existing?.pinned ?? false);
  const [cadence, setCadence] = useState<"" | "daily" | "weekly">(existing?.subscription?.cadence ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      let id: number;
      if (existing) {
        const body = { name: name.trim(), shared_scope: scope, pinned, ...(cadence ? { subscription: { cadence } } : { unsubscribe: true }) };
        id = (await api.patch<SavedTaskFilter>(`/tasks/search/filters/${existing.id}`, body)).id;
      } else {
        const body = { name: name.trim(), space: bp, query: {}, tree, shared_scope: scope, pinned, subscription: cadence ? { cadence } : null };
        id = (await api.post<SavedTaskFilter>("/tasks/search/filters", body)).id;
      }
      onSaved(id);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={existing ? `Bộ lọc “${existing.name}”` : "Lưu bộ lọc"}
      onClose={onClose}
      busy={busy}
      width={460}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !name.trim()}>
            Lưu
          </button>
        </>
      }
    >
      <form
        className="adm-form tk-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="rq-field">
          <span className="rq-label">Tên</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Bug quá hạn của tôi" autoFocus />
        </label>
        <label className="rq-field">
          <span className="rq-label">Ai thấy</span>
          <select value={scope} onChange={(e) => setScope(e.target.value as FilterScope)}>
            {(Object.keys(SCOPE_LABEL) as FilterScope[]).map((k) => (
              <option key={k} value={k}>
                {SCOPE_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-check">
          <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Ghim lên Board làm lọc nhanh
        </label>
        <label className="rq-field">
          <span className="rq-label">Nhận tóm tắt</span>
          <select value={cadence} onChange={(e) => setCadence(e.target.value as "" | "daily" | "weekly")}>
            <option value="">Không</option>
            <option value="daily">Hằng ngày (8:00)</option>
            <option value="weekly">Hằng tuần (thứ Hai 8:00)</option>
          </select>
          <span className="small muted">Gửi qua Telegram nếu bạn đã liên kết, không thì vào hộp thông báo.</span>
        </label>
      </form>
    </Modal>
  );
}
