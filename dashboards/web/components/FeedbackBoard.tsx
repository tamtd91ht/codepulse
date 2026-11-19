"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSWRConfig } from "swr";

import { Modal } from "@/components/CriteriaParts";
import { AnalysisBadge, FeedbackView, KindPill, StatusPill } from "@/components/Feedback";
import { Icon, IconButton } from "@/components/icons";
import { MultiSelect } from "@/components/MultiSelect";
import { Alert, Avatar, EmptyState, Loading, Message, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import {
  type FeedbackBoard as Board,
  type FeedbackExportRow,
  type FeedbackKind,
  type FeedbackMeta,
  type FeedbackRow,
  type FeedbackStatus,
  EXPORTS_KEY,
  KIND_LABEL,
  OPEN_STATUSES,
  PRIORITY_TONE,
  STATUS_LABEL,
  STATUS_MAX,
  type TreeNode,
  download,
  exportUrl,
  inNode,
  isAnalysing,
  isItemKey,
  setFeedbackStatus,
} from "@/lib/feedback";
import { dateTimeMin, relative } from "@/lib/format";

/*
 * Tổng hợp phản ánh (docs/specs/32 §7.2): three columns. The tree (lĩnh vực ▸ chức năng, with
 * counts and a "pick the whole branch" box), the reports of the node picked (filters, a box per
 * row; picks survive moving around the tree), the report itself with its triage. A bar at the
 * bottom says what is picked and exports it as one Markdown file for the coding agent.
 */

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));
const PRIORITY_RANK: Record<string, number> = { p1: 0, p2: 1, p3: 2 };
const EXPORT_MAX = 200; // app/api/routers/feedback.py EXPORT_MAX

function readItem(): number | null {
  if (typeof window === "undefined") return null;
  const v = new URLSearchParams(window.location.search).get("item");
  return v && /^\d+$/.test(v) ? Number(v) : null;
}

function writeItem(id: number | null) {
  const p = new URLSearchParams(window.location.search);
  if (id) p.set("item", String(id));
  else p.delete("item");
  window.history.replaceState(null, "", `${window.location.pathname}?${p}`);
}

export function FeedbackBoard({ meta }: { meta: FeedbackMeta }) {
  const { data: board, error, mutate } = useApi<Board>("/feedback", {
    keepPreviousData: true,
    refreshInterval: (latest) => (latest?.items.some(isAnalysing) ? 4000 : 0),
  });
  const exports = useApi<FeedbackExportRow[]>(EXPORTS_KEY);
  const { mutate: mutateKey } = useSWRConfig();
  const [node, setNode] = useState<TreeNode>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [statuses, setStatuses] = useState<FeedbackStatus[]>(OPEN_STATUSES);
  const [kinds, setKinds] = useState<FeedbackKind[]>([]);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"new" | "priority">("priority");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [current, setCurrent] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [moving, setMoving] = useState<FeedbackStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => setCurrent(readItem()), []);
  const open = (id: number | null) => {
    setCurrent(id);
    writeItem(id);
    if (id && window.matchMedia("(max-width: 1279px)").matches) {
      requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  };

  const items = useMemo(() => board?.items ?? [], [board]);
  const byId = useMemo(() => new Map(items.map((r) => [r.id, r])), [items]);
  // Picks of reports that are gone (deleted) are dropped.
  useEffect(() => {
    setPicked((old) => {
      const next = new Set([...old].filter((id) => byId.has(id)));
      return next.size === old.size ? old : next;
    });
  }, [byId]);

  const passes = (r: FeedbackRow) => {
    if (statuses.length && !statuses.includes(r.status)) return false;
    if (kinds.length && !kinds.includes(r.kind)) return false;
    const q = query.trim().toLowerCase();
    if (!q) return true;
    if (q.startsWith("#")) return String(r.id) === q.slice(1);
    return [r.title, r.summary ?? "", r.author?.name ?? "", r.feature].some((s) => s.toLowerCase().includes(q));
  };
  const filtered = items.filter(passes);
  const shown = filtered
    .filter((r) => inNode(r, node))
    .sort((a, b) =>
      sort === "priority"
        ? (PRIORITY_RANK[a.priority ?? ""] ?? 3) - (PRIORITY_RANK[b.priority ?? ""] ?? 3) || b.id - a.id
        : b.id - a.id,
    );
  const features = useMemo(() => [...new Set(items.map((r) => r.feature))].filter((f) => f !== "Chưa phân loại").sort(), [items]);

  const toggle = (ids: number[], on: boolean) =>
    setPicked((old) => {
      const next = new Set(old);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  const allShown = shown.length > 0 && shown.every((r) => picked.has(r.id));
  const someShown = shown.some((r) => picked.has(r.id));
  const pickedRows = [...picked].map((id) => byId.get(id)).filter((r): r is FeedbackRow => !!r);
  const pickedAreas = new Set(pickedRows.map((r) => r.area));

  const areaLabel = (key: string) => meta.areas.find((a) => a.key === key)?.label ?? key;
  const nodeLabel = node ? (node.feature ? `${areaLabel(node.area)} ▸ ${node.feature}` : areaLabel(node.area)) : "Tất cả phản ánh";

  if (error && !board) return <Alert tone="error">{error.message}</Alert>;
  if (!board) return <Loading rows={8} />;
  if (items.length === 0)
    return (
      <div className="card">
        <EmptyState icon="message" title="Chưa có phản ánh nào">
          Khi mọi người gửi phản ánh, AI phân loại và chúng hiện ở đây theo lĩnh vực ▸ chức năng.
        </EmptyState>
      </div>
    );

  return (
    <>
      <Message text={note} onClose={() => setNote(null)} />
      {board.truncated && <Alert tone="warning">Chỉ hiện 2.000 phản ánh mới nhất.</Alert>}
      <div className={`fb-board${picked.size ? " has-picks" : ""}`}>
        <aside className="card fb-tree" aria-label="Cây phản ánh">
          <TreeView
            board={board}
            rows={filtered}
            node={node}
            expanded={expanded}
            picked={picked}
            onNode={(n) => {
              setNode(n);
              if (n && !n.feature) setExpanded((old) => new Set(old).add(n.area));
            }}
            onExpand={(area) =>
              setExpanded((old) => {
                const next = new Set(old);
                if (next.has(area)) next.delete(area);
                else next.add(area);
                return next;
              })
            }
            onPick={toggle}
          />
          <ExportHistory
            rows={exports.data}
            error={exports.error}
            onPick={(r) => {
              const live = r.item_ids.filter((id) => byId.has(id));
              setPicked(new Set(live));
              setStatuses([]); // done / rejected ones of the batch show too
              setNode(null);
              setNote(
                `Đã chọn ${live.length} phản ánh của gói "${r.title}"${live.length < r.items ? ` (${r.items - live.length} đã bị xoá)` : ""}. Chuyển trạng thái ở thanh dưới.`,
              );
            }}
          />
        </aside>

        <section className="card fb-list" aria-labelledby="fb-list-title">
          <div className="fb-list-head">
            <div>
              <h2 id="fb-list-title">{nodeLabel}</h2>
              <span className="muted small">
                {shown.length} phản ánh{filtered.length !== items.length ? ` (đã lọc từ ${items.length})` : ""}
              </span>
            </div>
          </div>
          <div className="fb-filters">
            <label className="fb-search">
              <Icon name="search" size={14} />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm tiêu đề, tác giả, #id" aria-label="Tìm phản ánh" />
            </label>
            <MultiSelect
              label="Trạng thái"
              options={meta.statuses.map((s) => ({ value: s.key as FeedbackStatus, label: s.label }))}
              value={statuses}
              onChange={setStatuses}
              empty="Mọi trạng thái"
            />
            <MultiSelect
              label="Loại"
              options={(Object.keys(KIND_LABEL) as FeedbackKind[]).map((k) => ({ value: k, label: KIND_LABEL[k] }))}
              value={kinds}
              onChange={setKinds}
              empty="Mọi loại"
            />
            <select value={sort} onChange={(e) => setSort(e.target.value as "new" | "priority")} aria-label="Sắp xếp">
              <option value="priority">Ưu tiên trước</option>
              <option value="new">Mới nhất trước</option>
            </select>
          </div>
          <div className="fb-rows-head">
            <label className="inline-check small">
              <input
                type="checkbox"
                checked={allShown}
                ref={(el) => {
                  if (el) el.indeterminate = !allShown && someShown;
                }}
                onChange={(e) => toggle(shown.map((r) => r.id), e.target.checked)}
                disabled={shown.length === 0}
              />
              Chọn {shown.length} phản ánh đang hiện
            </label>
          </div>
          {shown.length === 0 ? (
            <EmptyState icon="filter" title="Không có phản ánh khớp bộ lọc">
              Thử bỏ bớt trạng thái hoặc chọn nhánh khác trên cây.
            </EmptyState>
          ) : (
            <ul className="fb-rows">
              {shown.map((r) => (
                <li key={r.id} className={`fb-row${current === r.id ? " active" : ""}${picked.has(r.id) ? " picked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={picked.has(r.id)}
                    onChange={(e) => toggle([r.id], e.target.checked)}
                    aria-label={`Chọn #${r.id} ${r.title}`}
                  />
                  <button type="button" className="fb-row-main" onClick={() => open(r.id)} aria-current={current === r.id ? "true" : undefined}>
                    <span className="fb-row-top">
                      <span className="fb-id">#{r.id}</span>
                      <span className="fb-row-title">{r.title}</span>
                      {r.priority && <Pill tone={PRIORITY_TONE[r.priority]}>{r.priority.toUpperCase()}</Pill>}
                      <StatusPill status={r.status} />
                    </span>
                    {r.summary && <span className="fb-row-summary small">{r.summary}</span>}
                    <span className="fb-row-meta small muted">
                      <Avatar name={r.author?.name ?? "?"} src={r.author?.avatar_url} size="sm" />
                      <span>{r.author?.name ?? "(không rõ)"}</span>
                      <span>·</span>
                      <KindPill kind={r.kind} />
                      {!node?.feature && (
                        <span className="fb-row-feature">
                          {node ? r.feature : `${areaLabel(r.area)} ▸ ${r.feature}`}
                        </span>
                      )}
                      <time dateTime={r.created_at} title={dateTimeMin(r.created_at)}>
                        {relative(r.created_at)}
                      </time>
                      {r.duplicates.length > 0 && <span title="AI cho là trùng">trùng {r.duplicates.map((d) => `#${d}`).join(", ")}</span>}
                      {r.exported > 0 && (
                        <span title="Đã nằm trong gói export">
                          <Icon name="download" size={11} /> đã export
                        </span>
                      )}
                      <AnalysisBadge row={r} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card fb-detail" ref={detailRef} aria-label="Chi tiết phản ánh">
          {current && byId.has(current) ? (
            <FeedbackView
              id={current}
              meta={meta}
              features={features}
              onChanged={() => void mutate()}
              onDeleted={() => {
                open(null);
                setNote("Đã xoá phản ánh.");
                void mutate();
              }}
            />
          ) : (
            <EmptyState icon="file" title="Chọn một phản ánh để xem">
              Bên này hiện tác giả, bản phân tích .md của AI, nội dung gốc và ô phân loại.
            </EmptyState>
          )}
        </section>
      </div>

      {picked.size > 0 && (
        <div className="fb-pickbar" role="region" aria-label="Phản ánh đã chọn">
          <span>
            <strong>Đã chọn {picked.size} phản ánh</strong>
            <span className="muted small"> · {pickedAreas.size} lĩnh vực</span>
          </span>
          <button type="button" className="ghost sm" onClick={() => setPicked(new Set())}>
            Bỏ chọn
          </button>
          <label className="fb-pickbar-status">
            <span className="sr-only">Chuyển trạng thái các phản ánh đã chọn</span>
            <select
              value=""
              onChange={(e) => e.target.value && setMoving(e.target.value as FeedbackStatus)}
              disabled={picked.size > STATUS_MAX}
              title={picked.size > STATUS_MAX ? `Tối đa ${STATUS_MAX} phản ánh mỗi lần` : "Chuyển trạng thái các phản ánh đã chọn"}
            >
              <option value="">Chuyển trạng thái…</option>
              {meta.statuses.map((s) => (
                <option key={s.key} value={s.key}>
                  → {s.label}
                </option>
              ))}
            </select>
          </label>
          {picked.size > EXPORT_MAX && <span className="small error">Tối đa {EXPORT_MAX} phản ánh mỗi gói</span>}
          <button type="button" className="primary" onClick={() => setExporting(true)} disabled={picked.size > EXPORT_MAX}>
            <Icon name="download" size={14} /> Export .md
          </button>
        </div>
      )}

      {exporting && (
        <ExportDialog
          rows={pickedRows}
          areaLabel={areaLabel}
          onClose={() => setExporting(false)}
          onDone={async (row) => {
            setExporting(false);
            setPicked(new Set());
            setNote(`Đã export "${row.title}" (${row.items} phản ánh). File đang tải về; tải lại ở Lịch sử export.`);
            // The new batch goes into the history at once (the shared key also feeds the report's
            // "đã export N lần" pill), and the lists are refreshed before the download starts: a
            // download fired first could leave those refreshes behind and the counts stale.
            await Promise.allSettled([
              exports.mutate((old) => [row, ...(old ?? []).filter((r) => r.id !== row.id)], { revalidate: true }),
              mutate(),
              mutateKey(isItemKey),
            ]);
            download(exportUrl(row.id));
          }}
        />
      )}

      {moving && (
        <StatusDialog
          rows={pickedRows}
          status={moving}
          onClose={() => setMoving(null)}
          onDone={async (changed, unchanged) => {
            setMoving(null);
            setPicked(new Set());
            setNote(
              `Đã chuyển ${changed} phản ánh sang “${STATUS_LABEL[moving]}”${unchanged ? ` (${unchanged} đã ở trạng thái này)` : ""}.`,
            );
            await Promise.allSettled([mutate(), mutateKey(isItemKey)]);
          }}
        />
      )}
    </>
  );
}

function TreeView({
  board,
  rows,
  node,
  expanded,
  picked,
  onNode,
  onExpand,
  onPick,
}: {
  board: Board;
  rows: FeedbackRow[];
  node: TreeNode;
  expanded: Set<string>;
  picked: Set<number>;
  onNode: (node: TreeNode) => void;
  onExpand: (area: string) => void;
  onPick: (ids: number[], on: boolean) => void;
}) {
  const ids = (n: TreeNode) => rows.filter((r) => inNode(r, n)).map((r) => r.id);
  const Check = ({ n, label }: { n: TreeNode; label: string }) => {
    const mine = ids(n);
    const all = mine.length > 0 && mine.every((id) => picked.has(id));
    const some = mine.some((id) => picked.has(id));
    return (
      <input
        type="checkbox"
        className="fb-tree-check"
        checked={all}
        disabled={mine.length === 0}
        ref={(el) => {
          if (el) el.indeterminate = !all && some;
        }}
        onChange={(e) => onPick(mine, e.target.checked)}
        aria-label={`Chọn cả nhánh ${label}`}
        title="Chọn cả nhánh (theo bộ lọc đang dùng)"
      />
    );
  };
  const same = (a: TreeNode, b: TreeNode) => a?.area === b?.area && a?.feature === b?.feature;
  return (
    <nav className="fb-tree-nav" aria-label="Lĩnh vực và chức năng">
      <div className="eyebrow">Lĩnh vực ▸ Chức năng</div>
      <button type="button" className={`fb-node root${node === null ? " active" : ""}`} onClick={() => onNode(null)}>
        <Icon name="folder" size={14} />
        <span className="fb-node-name">Tất cả</span>
        <span className="fb-count">{rows.length}</span>
      </button>
      <ul>
        {board.tree.map((area) => {
          const open = expanded.has(area.key);
          const n = { area: area.key };
          const visible = ids(n).length;
          return (
            <li key={area.key}>
              <div className={`fb-node-row${same(node, n) ? " active" : ""}`}>
                <button type="button" className="fb-caret" onClick={() => onExpand(area.key)} aria-expanded={open} aria-label={`${open ? "Thu" : "Mở"} ${area.label}`}>
                  <Icon name={open ? "chevronDown" : "chevronRight"} size={14} />
                </button>
                <Check n={n} label={area.label} />
                <button type="button" className="fb-node" onClick={() => onNode(n)}>
                  <span className="fb-node-name">{area.label}</span>
                  <span className="fb-count" title={`${visible} khớp bộ lọc / ${area.count} tất cả`}>
                    {visible}
                  </span>
                </button>
              </div>
              {open && (
                <ul className="fb-features">
                  {area.features.map((f) => {
                    const fn = { area: area.key, feature: f.name };
                    const count = ids(fn).length;
                    return (
                      <li key={f.name} className={`fb-node-row leaf${same(node, fn) ? " active" : ""}`}>
                        <Check n={fn} label={f.name} />
                        <button type="button" className="fb-node" onClick={() => onNode(fn)}>
                          <Icon name="file" size={12} />
                          <span className="fb-node-name">{f.name}</span>
                          <span className="fb-count">{count}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function ExportHistory({
  rows,
  error,
  onPick,
}: {
  rows?: FeedbackExportRow[];
  error?: Error;
  /** Pick every report of the batch (e.g. to mark them done once the agent shipped it). */
  onPick: (row: FeedbackExportRow) => void;
}) {
  return (
    <details className="fb-history">
      <summary className="small">
        <Icon name="history" size={12} /> Lịch sử export {rows ? `(${rows.length})` : ""}
      </summary>
      {error ? (
        <p className="small error">{error.message}</p>
      ) : !rows ? (
        <Loading rows={2} />
      ) : rows.length === 0 ? (
        <p className="muted small">Chưa export gói nào.</p>
      ) : (
        <ul>
          {rows.map((r) => (
            <li key={r.id}>
              <span className="fb-history-top">
                <a href={exportUrl(r.id)} download className="fb-history-link">
                  <Icon name="download" size={12} /> {r.title}
                </a>
                <IconButton icon="check" label="Chọn các phản ánh của gói này" className="tip-right" onClick={() => onPick(r)} />
              </span>
              <span className="muted small">
                {r.items} phản ánh · {r.created_by?.name ?? "?"} · {relative(r.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function StatusDialog({
  rows,
  status,
  onClose,
  onDone,
}: {
  rows: FeedbackRow[];
  status: FeedbackStatus;
  onClose: () => void;
  onDone: (changed: number, unchanged: number) => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const already = rows.filter((r) => r.status === status).length;
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await setFeedbackStatus(rows.map((r) => r.id), status, note);
      onDone(got.changed.length, got.unchanged.length);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Chuyển ${rows.length} phản ánh sang “${STATUS_LABEL[status]}”`}
      onClose={onClose}
      busy={busy}
      width={560}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void go()} disabled={busy}>
            {busy ? "Đang chuyển…" : `Chuyển ${rows.length} phản ánh`}
          </button>
        </>
      }
    >
      <p className="muted small" style={{ marginTop: 0 }}>
        Tác giả thấy trạng thái mới ở tab “Của tôi”.{already ? ` ${already} phản ánh đã ở trạng thái này, giữ nguyên.` : ""}
      </p>
      <label className="adm-field">
        <span className="small muted">Ghi chú cho tác giả (tuỳ chọn, thêm dưới ghi chú đang có)</span>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          maxLength={1000}
          placeholder={status === "done" ? "Ví dụ: Đã lên bản 19/11." : status === "rejected" ? "Lý do từ chối…" : ""}
          autoFocus
        />
      </label>
      <ul className="fb-status-preview">
        {rows.map((r) => (
          <li key={r.id} className="small">
            <span className="fb-id">#{r.id}</span> <span className="fb-status-preview-title">{r.title}</span>
            <StatusPill status={r.status} />
            {r.status !== status && (
              <>
                <Icon name="chevronRight" size={12} />
                <StatusPill status={status} />
              </>
            )}
          </li>
        ))}
      </ul>
      {error && <Alert tone="error">{error}</Alert>}
    </Modal>
  );
}

function ExportDialog({
  rows,
  areaLabel,
  onClose,
  onDone,
}: {
  rows: FeedbackRow[];
  areaLabel: (key: string) => string;
  onClose: () => void;
  onDone: (row: FeedbackExportRow) => void | Promise<void>;
}) {
  const today = new Date().toLocaleDateString("vi-VN");
  const [title, setTitle] = useState(`Đợt nâng cấp ${today}`);
  const [note, setNote] = useState("");
  const [plan, setPlan] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const groups = new Map<string, FeedbackRow[]>();
  for (const r of rows) groups.set(r.area, [...(groups.get(r.area) ?? []), r]);
  const unanalysed = rows.filter((r) => r.analysis_status !== "ready").length;
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const row = await api.post<FeedbackExportRow>("/feedback/exports", {
        item_ids: rows.map((r) => r.id),
        title: title.trim(),
        note: note.trim() || null,
        mark_planned: plan,
      });
      void onDone(row);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Export cho agent code"
      onClose={onClose}
      busy={busy}
      width={620}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void go()} disabled={busy || !title.trim()}>
            <Icon name="download" size={14} /> {busy ? "Đang tạo…" : `Tạo file .md (${rows.length})`}
          </button>
        </>
      }
    >
      <p className="muted small" style={{ marginTop: 0 }}>
        Một file Markdown: hướng dẫn cho agent, bảng tóm tắt, rồi từng phản ánh theo lĩnh vực ▸ chức năng (P1 trước) với tác giả, phân tích, tiêu chí chấp nhận, ghi chú duyệt và nội dung gốc.
      </p>
      <label className="adm-field">
        <span className="small muted">Tên gói</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus />
      </label>
      <label className="adm-field">
        <span className="small muted">Ghi chú cho agent (tuỳ chọn)</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={4000} placeholder="Ví dụ: Ưu tiên phần Task; không đổi API công khai." />
      </label>
      <label className="inline-check small">
        <input type="checkbox" checked={plan} onChange={(e) => setPlan(e.target.checked)} />
        Đánh dấu các phản ánh Mới / Đã nhận là “{STATUS_LABEL.planned}” (tác giả thấy)
      </label>
      {unanalysed > 0 && <Alert tone="warning">{unanalysed} phản ánh chưa có phân tích AI: sẽ export bằng nội dung gốc.</Alert>}
      <div className="fb-export-preview">
        {[...groups.entries()].map(([area, rs]) => (
          <div key={area}>
            <strong className="small">{areaLabel(area)}</strong>
            <ul>
              {rs.map((r) => (
                <li key={r.id} className="small">
                  <span className="fb-id">#{r.id}</span> {r.title} <span className="muted">· {r.author?.name ?? "?"}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {error && <Alert tone="error">{error}</Alert>}
    </Modal>
  );
}
