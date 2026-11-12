"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { DocEditor, type EditorStart } from "@/components/DocEditor";
import { DraftReview, DraftsCard, LinkForm, useDrafts } from "@/components/DocFromLinks";
import { DocHistory } from "@/components/DocHistory";
import { DocMarkdown, docOutline, fold, type OutlineItem } from "@/components/DocMarkdown";
import { Icon, IconButton } from "@/components/icons";
import { Alert, EmptyState, Loading, Message, PageHeader, Pill, Segmented, Skeleton } from "@/components/ui";
import { UploadDrop, type UploadItem, UploadList, fileSize, uploadAll } from "@/components/UploadDrop";
import { ApiError, api, can, useApi } from "@/lib/api";
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  type DocCategory,
  type DocDetail,
  type DocHit,
  type DocItem,
  type DocList,
  type DocMeta,
  type DocProject,
  type DocTemplate,
  type DocVersion,
  type DraftList,
  type DraftMode,
  KIND_LABEL,
  readMinutes,
  sourceCount,
} from "@/lib/docs";
import { dateTimeMin, relative } from "@/lib/format";
import type { Me } from "@/lib/types";

/*
 * Tài liệu dự án (docs/specs/29, user 2026-11-11). One screen, three columns like a docs portal:
 * the dự án's documents grouped by kind (with search across every dự án one sees and how
 * complete the set is), the document itself rendered (headings with anchors, tables, code,
 * callouts; a PDF also in the browser's own viewer), and its outline that follows the reading.
 * Those who may edit add documents (several files at once, a template, or a blank page), write
 * new versions in a split Markdown editor, upload a newer file, look at and restore versions.
 * The URL keeps the dự án, the document and the version (?project=&doc=&v=), so a link opens the
 * same place.
 */

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

type Where = { project: number | null; doc: number | null; v: number | null };

function readWhere(): Where {
  if (typeof window === "undefined") return { project: null, doc: null, v: null };
  const p = new URLSearchParams(window.location.search);
  const num = (k: string) => (p.get(k) && /^\d+$/.test(p.get(k)!) ? Number(p.get(k)) : null);
  return { project: num("project"), doc: num("doc"), v: num("v") };
}

function writeWhere(w: Where, push: boolean) {
  const p = new URLSearchParams();
  if (w.project) p.set("project", String(w.project));
  if (w.doc) p.set("doc", String(w.doc));
  if (w.v) p.set("v", String(w.v));
  const url = `${window.location.pathname}${p.size ? `?${p}` : ""}`;
  if (push) window.history.pushState(null, "", url);
  else window.history.replaceState(null, "", url);
}

const LAST_PROJECT = "cp:docs:project";

function remembered(): number | null {
  try {
    const v = window.localStorage.getItem(LAST_PROJECT);
    return v && /^\d+$/.test(v) ? Number(v) : null;
  } catch {
    return null;
  }
}

export default function DocsPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const projects = useApi<DocProject[]>(me && can(me, "docs.view") ? "/docs/projects" : null);
  const meta = useApi<DocMeta>(me && can(me, "docs.view") ? "/docs/meta" : null);
  const [where, setWhere] = useState<Where>({ project: null, doc: null, v: null });
  const [ready, setReady] = useState(false);
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [archived, setArchived] = useState(false);
  const [adding, setAdding] = useState<false | "upload" | "template" | "link">(false);
  const [editor, setEditor] = useState<EditorStart | null>(null);
  // Tổng hợp bằng AI (docs/specs/29 §12): the draft being reviewed.
  const [review, setReview] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    setWhere(readWhere());
    setReady(true);
    const onPop = () => setWhere(readWhere());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // The dự án: the URL's, else the last one used here, else the first with documents.
  const projectId = useMemo(() => {
    const list = projects.data ?? [];
    if (!list.length) return null;
    const ok = (id: number | null) => (id && list.some((p) => p.id === id) ? id : null);
    return ok(where.project) ?? ok(ready ? remembered() : null) ?? (list.find((p) => p.docs > 0) ?? list[0]).id;
  }, [projects.data, where.project, ready]);

  useEffect(() => {
    if (!projectId) return;
    try {
      window.localStorage.setItem(LAST_PROJECT, String(projectId));
    } catch {
      /* per-viewer convenience only */
    }
  }, [projectId]);

  const go = useCallback((next: Partial<Where>, push = true) => {
    setWhere((w) => {
      const merged = { ...w, ...next };
      writeWhere(merged, push);
      return merged;
    });
    setNavOpen(false);
  }, []);

  const list = useApi<DocList>(projectId ? `/docs?business_project_id=${projectId}${archived ? "&archived=true" : ""}` : null, { keepPreviousData: true });
  const detail = useApi<DocDetail>(where.doc ? `/docs/${where.doc}` : null);
  const old = useApi<DocVersion>(where.doc && where.v && detail.data && where.v !== detail.data.version ? `/docs/${where.doc}/versions/${where.v}` : null);
  const project = projects.data?.find((p) => p.id === projectId) ?? null;
  const canEdit = Boolean(list.data?.can_edit ?? project?.can_edit);
  const drafts = useDrafts(projectId, canEdit);

  const refresh = async (doc?: DocDetail) => {
    await Promise.all([list.mutate(), projects.mutate(), doc ? detail.mutate(doc, false) : detail.mutate()]);
  };

  if (me && !can(me, "docs.view"))
    return (
      <>
        <PageHeader title="Tài liệu dự án" foldable={false} />
        <EmptyState icon="file" title="Bạn chưa có quyền xem tài liệu dự án">
          Nhờ admin cấp quyền “Xem tài liệu dự án” (Admin › Phân quyền).
        </EmptyState>
      </>
    );

  return (
    <>
      <PageHeader
        title="Tài liệu dự án"
        subtitle="Tài liệu hệ thống của từng dự án: tổng quan, kiến trúc, cài đặt, API, triển khai, vận hành, quy ước."
        foldable={false}
        actions={
          canEdit && projectId ? (
            <>
              <button type="button" onClick={() => setAdding("link")}>
                <Icon name="sparkle" size={14} /> Tổng hợp bằng AI
              </button>
              <button type="button" className="primary" onClick={() => setAdding("upload")}>
                <Icon name="plus" size={14} /> Thêm tài liệu
              </button>
            </>
          ) : undefined
        }
      />
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {projects.error ? (
        <Alert tone="error">
          Không tải được danh sách dự án: {projects.error.message}{" "}
          <button type="button" className="link sm" onClick={() => void projects.mutate()}>
            Thử lại
          </button>
        </Alert>
      ) : projects.data && projects.data.length === 0 ? (
        <EmptyState icon="folder" title="Chưa có dự án nào bạn được xem">
          Tài liệu gắn với dự án; khi được cấp quyền vào một dự án, tài liệu của dự án đó hiện ở đây.
        </EmptyState>
      ) : (
        <div className={`docs-shell${navOpen ? " nav-open" : ""}`}>
          <button type="button" className="docs-nav-toggle" onClick={() => setNavOpen(!navOpen)} aria-expanded={navOpen}>
            <Icon name="menu" size={14} /> Danh sách tài liệu
          </button>
          <aside className="docs-nav card" aria-label="Danh sách tài liệu">
            <label className="adm-field">
              <span className="small muted">Dự án</span>
              <select value={projectId ?? ""} onChange={(e) => go({ project: Number(e.target.value), doc: null, v: null })} disabled={!projects.data}>
                {(projects.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.docs})
                  </option>
                ))}
              </select>
            </label>
            <SearchBox q={q} setQ={setQ} />
            {q.trim().length >= 2 ? (
              <SearchResults
                q={q}
                projectId={projectId}
                onOpen={(hit) => {
                  setTerm(q.trim());
                  go({ project: hit.business_project_id, doc: hit.document_id, v: null });
                  if (hit.heading) setPendingHeading(hit.heading);
                }}
              />
            ) : (
              <DocTree
                list={list.data}
                error={list.error}
                retry={() => void list.mutate()}
                selected={where.doc}
                archived={archived}
                setArchived={setArchived}
                onOpen={(id) => {
                  setTerm("");
                  go({ doc: id, v: null });
                }}
              />
            )}
          </aside>

          <main className="docs-main" id="doc-main">
            {!where.doc ? (
              <ProjectHome
                list={list.data}
                loading={!list.data && !list.error}
                meta={meta.data}
                canEdit={canEdit}
                onOpen={(id) => go({ doc: id, v: null })}
                onTemplate={(t) => projectId && setEditor({ kind: "new", projectId, title: t.title, category: t.category, body: t.body, template: t.key, summary: t.summary })}
                onAdd={() => setAdding("upload")}
                drafts={canEdit ? drafts.data : undefined}
                onDraft={setReview}
                onLinks={() => setAdding("link")}
              />
            ) : detail.error ? (
              <Alert tone="error">
                {detail.error.status === 404 ? "Tài liệu không tồn tại hoặc bạn không có quyền xem." : `Lỗi: ${detail.error.message}`}{" "}
                <button type="button" className="link sm" onClick={() => go({ doc: null, v: null })}>
                  Về danh sách
                </button>
              </Alert>
            ) : !detail.data ? (
              <DocSkeleton />
            ) : (
              <DocView
                doc={detail.data}
                old={where.v && where.v !== detail.data.version ? (old.data ?? null) : null}
                oldLoading={Boolean(where.v && where.v !== detail.data.version && !old.data && !old.error)}
                term={term}
                setTerm={setTerm}
                onEdit={() => setEditor({ kind: "edit", doc: detail.data! })}
                onChanged={async (d, text) => {
                  await refresh(d);
                  if (text) setMessage(text);
                }}
                onDeleted={async (title) => {
                  go({ doc: null, v: null });
                  await refresh();
                  setMessage(`Đã xoá vĩnh viễn “${title}”.`);
                }}
                onVersion={(v) => go({ v })}
                drafts={canEdit ? drafts.data : undefined}
                onDraft={(id) => {
                  void drafts.mutate();
                  setReview(id);
                }}
              />
            )}
          </main>
        </div>
      )}

      {adding && projectId && meta.data && (
        <AddDocs
          projectId={projectId}
          projectName={project?.name ?? ""}
          meta={meta.data}
          start={adding}
          docs={list.data?.docs ?? []}
          drafts={drafts.data}
          onQueued={(id) => {
            setAdding(false);
            void drafts.mutate();
            setReview(id);
          }}
          existing={list.data?.categories ?? []}
          onClose={() => setAdding(false)}
          onUploaded={async (n, last) => {
            await refresh();
            setMessage(`Đã thêm ${n} tài liệu.`);
            if (n === 1 && last) {
              setAdding(false);
              go({ doc: last, v: null });
            }
          }}
          onTemplate={(t) => {
            setAdding(false);
            setEditor({ kind: "new", projectId, title: t.title, category: t.category, body: t.body, template: t.key, summary: t.summary });
          }}
          onBlank={() => {
            setAdding(false);
            setEditor({ kind: "new", projectId, title: "", category: "other", body: "# Tiêu đề\n\n" });
          }}
        />
      )}
      {review && (
        <DraftReview
          draftId={review}
          onClose={() => setReview(null)}
          onChanged={() => void drafts.mutate()}
          onApplied={async (doc, draft) => {
            setReview(null);
            go({ project: doc.business_project_id, doc: doc.id, v: null });
            await Promise.all([refresh(doc), drafts.mutate()]);
            setMessage(draft.mode === "new" ? `Đã tạo “${doc.title}” từ ${sourceCount(draft)}.` : `Đã lưu phiên bản ${doc.version} (tổng hợp bằng AI từ ${sourceCount(draft)}).`);
          }}
        />
      )}
      {editor && (
        <DocEditor
          start={editor}
          onClose={() => setEditor(null)}
          onSaved={async (doc) => {
            setEditor(null);
            go({ doc: doc.id, v: null });
            await refresh(doc);
            setMessage(editor.kind === "edit" ? `Đã lưu phiên bản ${doc.version}.` : "Đã tạo tài liệu.");
          }}
        />
      )}
    </>
  );
}

// A search hit names the section it was found in: once the document is shown, scroll there.
let pendingHeading: string | null = null;
function setPendingHeading(h: string) {
  pendingHeading = h;
}

function SearchBox({ q, setQ }: { q: string; setQ: (v: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="docs-search">
      <Icon name="search" size={14} />
      <input
        ref={input}
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Tìm trong tài liệu (phím /)"
        aria-label="Tìm trong tài liệu dự án"
      />
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function SearchResults({ q, projectId, onOpen }: { q: string; projectId: number | null; onOpen: (h: DocHit) => void }) {
  const [everywhere, setEverywhere] = useState(false);
  const term = useDebounced(q.trim(), 300);
  const path = term.length >= 2 ? `/docs/search?q=${encodeURIComponent(term)}${!everywhere && projectId ? `&business_project_id=${projectId}` : ""}` : null;
  const hits = useApi<DocHit[]>(path, { keepPreviousData: true });
  return (
    <div className="docs-results">
      <label className="inline-check small">
        <input type="checkbox" checked={everywhere} onChange={(e) => setEverywhere(e.target.checked)} />
        <span>Tìm ở mọi dự án</span>
      </label>
      {hits.error ? (
        <Alert tone="error">Lỗi tìm kiếm: {hits.error.message}</Alert>
      ) : !hits.data ? (
        <Loading rows={3} />
      ) : hits.data.length === 0 ? (
        <p className="muted small">Không thấy “{term}”. Thử từ khác, không cần dấu.</p>
      ) : (
        <ul>
          {hits.data.map((h) => (
            <li key={h.document_id}>
              <button type="button" className="docs-hit" onClick={() => onOpen(h)}>
                <strong>{h.title}</strong>
                <span className="muted small">
                  {everywhere ? `${h.business_project} › ` : ""}
                  {h.heading ? h.heading.split(" > ").slice(-2).join(" › ") : CATEGORY_LABEL[h.category]}
                </span>
                <span className="small docs-snippet">
                  <Mark text={h.snippet} term={term} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Words of the search marked in a snippet (accent-insensitive, word by word). */
function Mark({ text, term }: { text: string; term: string }) {
  const words = fold(term).split(/\s+/).filter((w) => w.length >= 2);
  if (!words.length) return <>{text}</>;
  const parts = text.split(/(\s+)/);
  return (
    <>
      {parts.map((p, i) => {
        const f = fold(p);
        return words.some((w) => f.includes(w)) ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>;
      })}
    </>
  );
}

function kindIcon(kind: string | null): string {
  return kind ? (KIND_LABEL[kind] ?? kind.replace(".", "").toUpperCase()) : "Web";
}

function DocTree({
  list,
  error,
  retry,
  selected,
  archived,
  setArchived,
  onOpen,
}: {
  list?: DocList;
  error?: ApiError;
  retry: () => void;
  selected: number | null;
  archived: boolean;
  setArchived: (v: boolean) => void;
  onOpen: (id: number) => void;
}) {
  const groups = useMemo(() => {
    const docs = list?.docs ?? [];
    const pinned = archived ? [] : docs.filter((d) => d.pinned);
    return [
      ...(pinned.length ? [{ key: "pinned", label: "Đã ghim", docs: pinned }] : []),
      ...CATEGORY_ORDER.map((c) => ({ key: c, label: CATEGORY_LABEL[c], docs: docs.filter((d) => d.category === c && (archived || !d.pinned)) })).filter((g) => g.docs.length),
    ];
  }, [list, archived]);
  if (error)
    return (
      <Alert tone="error">
        Lỗi: {error.message}{" "}
        <button type="button" className="link sm" onClick={retry}>
          Thử lại
        </button>
      </Alert>
    );
  if (!list) return <Loading rows={6} />;
  return (
    <nav className="docs-tree" aria-label="Tài liệu theo nhóm">
      <Coverage list={list} />
      {groups.length === 0 && <p className="muted small">{archived ? "Không có tài liệu đã lưu trữ." : "Dự án chưa có tài liệu nào."}</p>}
      {groups.map((g) => (
        <div key={g.key} className="docs-group">
          <div className="docs-group-title">
            {g.key === "pinned" && <Icon name="pin" size={12} />} {g.label} <span className="muted">{g.docs.length}</span>
          </div>
          <ul>
            {g.docs.map((d) => (
              <li key={d.id}>
                <button
                  type="button"
                  className={`docs-item${selected === d.id ? " active" : ""}`}
                  aria-current={selected === d.id ? "page" : undefined}
                  onClick={() => onOpen(d.id)}
                  title={d.summary ?? d.title}
                >
                  <Icon name="file" size={14} />
                  <span className="docs-item-title">{d.title}</span>
                  {(d.projects ?? 1) > 1 && (
                    <span
                      className="docs-shared"
                      title={d.shared_from ? `Dùng chung từ dự án ${d.shared_from}` : `Dùng chung với ${(d.projects ?? 1) - 1} dự án khác`}
                      aria-label="tài liệu dùng chung"
                    >
                      <Icon name="link" size={12} />
                    </span>
                  )}
                  {d.stale && <span className="docs-stale" title={`Không cập nhật hơn ${list.stale_days} ngày`} aria-label="có thể đã cũ" />}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <label className="inline-check small docs-archived">
        <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
        <span>Xem tài liệu đã lưu trữ</span>
      </label>
    </nav>
  );
}

/** How many of the expected kinds of documents the dự án has (a manager's checklist). */
function Coverage({ list }: { list: DocList }) {
  const core = list.categories.filter((c) => c.core);
  const have = core.filter((c) => c.count > 0).length;
  const pct = Math.round(list.coverage * 100);
  const tone = pct >= 85 ? "good" : pct >= 50 ? "warn" : "bad";
  return (
    <details className="docs-coverage">
      <summary>
        <span className="small">
          Độ đầy đủ: <strong>{have}/{core.length}</strong> nhóm
        </span>
        <span className={`crit-score-bar ${tone}`} role="meter" aria-label="Độ đầy đủ tài liệu" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <span style={{ width: `${pct}%` }} />
        </span>
      </summary>
      <ul>
        {core.map((c) => (
          <li key={c.key} className="small">
            <Icon name={c.count ? "checkCircle" : "alertCircle"} size={12} /> {c.label}
            {c.count ? <span className="muted"> · {c.count}</span> : <span className="muted"> · chưa có</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function DocSkeleton() {
  return (
    <div className="card docs-doc" aria-busy="true">
      <Skeleton width="40%" height={12} />
      <div style={{ height: 10 }} />
      <Skeleton width="70%" height={28} />
      <div style={{ height: 16 }} />
      <Loading rows={8} />
    </div>
  );
}

function ProjectHome({
  list,
  loading,
  meta,
  canEdit,
  onOpen,
  onTemplate,
  onAdd,
  drafts,
  onDraft,
  onLinks,
}: {
  list?: DocList;
  loading: boolean;
  meta?: DocMeta;
  canEdit: boolean;
  onOpen: (id: number) => void;
  onTemplate: (t: DocTemplate) => void;
  onAdd: () => void;
  drafts?: DraftList;
  onDraft: (id: number) => void;
  onLinks: () => void;
}) {
  if (loading || !list) return <DocSkeleton />;
  const docs = list.docs;
  const recent = [...docs].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? "")).slice(0, 6);
  const month = docs.filter((d) => d.updated_at && Date.now() - new Date(d.updated_at).getTime() < 30 * 86_400_000).length;
  const stale = docs.filter((d) => d.stale);
  const missing = (meta?.templates ?? []).filter((t) => {
    const c = list.categories.find((x) => x.key === t.category);
    return c && c.count === 0;
  });
  return (
    <div className="docs-home">
      <div className="card docs-doc">
        <div className="eyebrow">Dự án</div>
        <h2 style={{ margin: "2px 0 12px" }}>{list.business_project}</h2>
        <div className="docs-kpis">
          <div>
            <span className="docs-kpi-value">{docs.length}</span>
            <span className="muted small">tài liệu</span>
          </div>
          <div>
            <span className="docs-kpi-value">{month}</span>
            <span className="muted small">cập nhật 30 ngày qua</span>
          </div>
          <div>
            <span className="docs-kpi-value">{Math.round(list.coverage * 100)}%</span>
            <span className="muted small">độ đầy đủ</span>
          </div>
          <div>
            <span className={`docs-kpi-value${stale.length ? " warn" : ""}`}>{stale.length}</span>
            <span className="muted small">có thể đã cũ (&gt; {list.stale_days} ngày)</span>
          </div>
        </div>
      </div>

      {canEdit && <DraftsCard drafts={drafts} onOpen={onDraft} onNew={onLinks} />}

      {docs.length === 0 ? (
        <div className="card docs-doc">
          <EmptyState icon="file" title="Dự án chưa có tài liệu">
            {canEdit ? "Tải lên tài liệu có sẵn (Word, PDF, Markdown, Excel) hoặc bắt đầu từ một mẫu bên dưới." : "Người quản lý dự án sẽ đưa tài liệu lên đây."}
          </EmptyState>
          {canEdit && (
            <div style={{ textAlign: "center" }}>
              <button type="button" onClick={onAdd}>
                <Icon name="upload" size={14} /> Tải tài liệu lên
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="card docs-doc">
          <h3 style={{ marginTop: 0 }}>Cập nhật gần đây</h3>
          <ul className="docs-recent">
            {recent.map((d) => (
              <li key={d.id}>
                <button type="button" className="link" onClick={() => onOpen(d.id)}>
                  {d.title}
                </button>
                <span className="muted small">
                  {CATEGORY_LABEL[d.category]} · phiên bản {d.version} · {d.updated_by?.name ?? "—"} · {d.updated_at ? relative(d.updated_at) : "—"}
                </span>
                {d.stale && <Pill tone="warn">có thể đã cũ</Pill>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {canEdit && missing.length > 0 && (
        <div className="card docs-doc">
          <h3 style={{ marginTop: 0 }}>Còn thiếu — bắt đầu từ mẫu</h3>
          <p className="muted small" style={{ marginTop: 0 }}>
            Mỗi mẫu có sẵn bố cục, câu hỏi cần trả lời và chỗ &lt;…&gt; để điền. Một bộ tài liệu đủ giúp người mới vào việc nhanh và
            giảm phụ thuộc vào một người.
          </p>
          <div className="docs-templates">
            {missing.map((t) => (
              <button key={t.key} type="button" className="docs-template" onClick={() => onTemplate(t)}>
                <strong>{t.title}</strong>
                <span className="muted small">{t.summary}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** The outline of the document, the current section marked while reading. */
function Outline({ items, body }: { items: OutlineItem[]; body: string }) {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    if (!items.length) return;
    const nodes = items.map((i) => document.getElementById(i.id)).filter((n): n is HTMLElement => Boolean(n));
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => seen.set(e.target.id, e.isIntersecting));
        const first = items.find((i) => seen.get(i.id));
        if (first) setActive(first.id);
      },
      { rootMargin: "-72px 0px -65% 0px" },
    );
    nodes.forEach((n) => io.observe(n));
    return () => io.disconnect();
  }, [items, body]);
  if (items.length < 2) return null;
  const top = Math.min(...items.map((i) => i.level));
  return (
    <nav className="docs-toc" aria-label="Trên trang này">
      <span className="toc-title">Trên trang này</span>
      {items.map((i) => (
        <a key={i.id} href={`#${i.id}`} className={`lvl-${Math.min(3, i.level - top)}${active === i.id ? " active" : ""}`} aria-current={active === i.id ? "location" : undefined}>
          {i.text}
        </a>
      ))}
    </nav>
  );
}

/** The browser's own PDF viewer on the original file (fetched, shown from a blob: URL). */
function PdfView({ docId, number }: { docId: number; number: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    setUrl(null);
    setError(null);
    fetch(`/api/v1/docs/${docId}/versions/${number}/original?inline=true`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        if (!live) return;
        made = URL.createObjectURL(new Blob([b], { type: "application/pdf" }));
        setUrl(made);
      })
      .catch((e: unknown) => live && setError(String(e)));
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [docId, number]);
  if (error) return <Alert tone="error">Không mở được file PDF ({error}).</Alert>;
  if (!url) return <Skeleton height={600} />;
  return <iframe className="docs-pdf" src={url} title="Bản gốc PDF" />;
}

function DocView({
  doc,
  old,
  oldLoading,
  term,
  setTerm,
  onEdit,
  onChanged,
  onDeleted,
  onVersion,
  drafts,
  onDraft,
}: {
  doc: DocDetail;
  old: DocVersion | null;
  oldLoading: boolean;
  term: string;
  setTerm: (t: string) => void;
  onEdit: () => void;
  onChanged: (doc?: DocDetail, message?: string) => Promise<void>;
  onDeleted: (title: string) => Promise<void>;
  onVersion: (v: number | null) => void;
  drafts?: DraftList;
  onDraft: (id: number) => void;
}) {
  const shown = old ?? doc.current;
  const body = old?.body ?? doc.body;
  const [view, setView] = useState<"text" | "original">("text");
  const [history, setHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [replacing, setReplacing] = useState<UploadItem[]>([]);
  const [find, setFind] = useState(false);
  const [fromLinks, setFromLinks] = useState<DraftMode | null>(null);
  const [sharing, setSharing] = useState(false);
  const waiting = (drafts?.drafts ?? []).filter((d) => d.document_id === doc.id && d.mode !== "new" && ["queued", "fetching", "writing", "ready"].includes(d.status));
  // The document's own first "# Title" repeats the page title: not shown twice.
  const outline = useMemo(() => docOutline(body).filter((o, i) => !(i === 0 && o.level === 1 && fold(o.text) === fold(doc.title))), [body, doc.title]);
  const isPdf = shown.file_kind === ".pdf" && shown.has_original;
  const plain = Boolean(shown.meta?.plain);
  const matches = useMemo(() => {
    const t = fold(term.trim());
    if (t.length < 2) return 0;
    const hay = fold(body);
    let n = 0;
    for (let at = hay.indexOf(t); at !== -1 && n < 999; at = hay.indexOf(t, at + t.length)) n++;
    return n;
  }, [term, body]);

  useEffect(() => setView("text"), [doc.id]);
  useEffect(() => {
    // Opened from a search hit: scroll to the section it was found in.
    if (!pendingHeading) return;
    const last = pendingHeading.split(" > ").pop() ?? "";
    pendingHeading = null;
    const target = outline.find((o) => fold(o.text) === fold(last));
    if (target) requestAnimationFrame(() => document.getElementById(target.id)?.scrollIntoView({ behavior: "smooth", block: "start" }));
    else requestAnimationFrame(() => document.querySelector(".doc-body mark")?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, [outline, doc.id]);

  async function act(run: () => Promise<DocDetail | undefined | void>, text?: string) {
    setBusy(true);
    setError(null);
    try {
      const d = await run();
      await onChanged(d ?? undefined, text);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function newVersion(files: File[]) {
    const file = files[0];
    if (!file) return;
    setBusy(true);
    const ok = await uploadAll([file], [".md", ".markdown", ".txt", ".doc", ".docx", ".xlsx", ".pdf"], 20, setReplacing, async (f) => {
      const d = await api.postRaw<DocDetail>(`/docs/${doc.id}/upload?filename=${encodeURIComponent(f.name)}`, f);
      await onChanged(d, `Đã tải lên phiên bản ${d.version}.`);
      return `phiên bản ${d.version}`;
    });
    setBusy(false);
    if (ok) setTimeout(() => setReplacing([]), 2500);
  }

  const toc = view === "original" && isPdf ? [] : outline;
  return (
    <div className={`docs-reader${toc.length < 2 ? " no-toc" : ""}`}>
      <article className="card docs-doc" aria-labelledby="doc-title">
        <div className="docs-crumbs small muted">
          {doc.business_project} › {CATEGORY_LABEL[doc.category]}
          {doc.archived && <Pill tone="warn">Đã lưu trữ</Pill>}
        </div>
        <div className="docs-title-row">
          <h1 id="doc-title">{doc.title}</h1>
          <div className="docs-actions">
            <IconButton icon="search" label="Tìm trong tài liệu" onClick={() => setFind(!find)} aria-pressed={find} />
            <IconButton icon="history" label="Lịch sử phiên bản" onClick={() => setHistory(true)} />
            {shown.has_original && (
              <a className="icon-btn" href={`/api/v1/docs/${doc.id}/versions/${shown.number}/original`} download aria-label="Tải file gốc" data-tip="Tải file gốc">
                <Icon name="download" />
              </a>
            )}
            <IconButton icon="printer" label="In / lưu PDF" onClick={() => window.print()} />
            {doc.can_edit && !doc.archived && (
              <>
                <IconButton icon="pin" label={doc.pinned ? "Bỏ ghim" : "Ghim lên đầu"} aria-pressed={doc.pinned} disabled={busy} onClick={() => void act(() => api.patch(`/docs/${doc.id}`, { pinned: !doc.pinned }))} />
                <IconButton icon="settings" label="Đổi tên, nhóm" onClick={() => setRenaming(true)} />
                <UploadButton disabled={busy} onFiles={(f) => void newVersion(f)} />
                <button type="button" className="sm" onClick={() => setFromLinks("merge")} disabled={busy || Boolean(old)} title="AI đọc link / file và bổ sung / nâng cấp tài liệu này; bạn duyệt trước khi lưu">
                  <Icon name="sparkle" size={14} /> Bổ sung bằng AI
                </button>
                <button type="button" className="sm" onClick={onEdit} disabled={busy || Boolean(old)}>
                  <Icon name="edit" size={14} /> Sửa
                </button>
              </>
            )}
            {doc.can_edit && !doc.archived && <IconButton icon="link" label="Dự án dùng tài liệu này" onClick={() => setSharing(true)} />}
            {doc.can_delete && <IconButton icon="remove" tone="danger" label="Xoá tài liệu" disabled={busy} onClick={() => setConfirmDelete(true)} />}
          </div>
        </div>
        {doc.summary && <p className="docs-summary">{doc.summary}</p>}
        <div className="docs-meta small muted">
          <span>Phiên bản {shown.number}</span>
          <span title={shown.created_at ? dateTimeMin(shown.created_at) : undefined}>
            {old ? "Lưu" : "Cập nhật"} {shown.created_at ? relative(shown.created_at) : "—"} bởi {shown.created_by?.name ?? "—"}
          </span>
          {shown.file_name && (
            <span>
              <Icon name="file" size={12} /> {shown.file_name} · {KIND_LABEL[shown.file_kind ?? ""] ?? shown.file_kind}
              {shown.file_size ? ` · ${fileSize(shown.file_size)}` : ""}
              {shown.meta?.pages ? ` · ${shown.meta.pages} trang` : ""}
            </span>
          )}
          <span>~{readMinutes(shown.chars)} phút đọc</span>
          {doc.shared.length + doc.hidden_projects > 1 && (
            <span className="docs-shared-line">
              <Icon name="link" size={12} /> Dùng chung:{" "}
              {doc.shared.map((p, i) => (
                <span key={p.id}>
                  {i > 0 && ", "}
                  {p.name}
                  {p.home && " (gốc)"}
                </span>
              ))}
              {doc.hidden_projects > 0 && ` và ${doc.hidden_projects} dự án khác`}
            </span>
          )}
        </div>

        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <UploadList items={replacing} onClear={() => setReplacing([])} />
        {old && (
          <Alert tone="warning">
            Bạn đang xem <strong>phiên bản {old.number}</strong> (bản cũ). Bản hiện tại là phiên bản {doc.version}.{" "}
            <button type="button" className="link sm" onClick={() => onVersion(null)}>
              Về bản hiện tại
            </button>
            {doc.can_edit && (
              <>
                {" · "}
                <button
                  type="button"
                  className="link sm"
                  disabled={busy}
                  onClick={() => void act(() => api.post<DocDetail>(`/docs/${doc.id}/versions/${old.number}/restore`), `Đã khôi phục phiên bản ${old.number}.`).then(() => onVersion(null))}
                >
                  Khôi phục bản này
                </button>
              </>
            )}
          </Alert>
        )}
        {!old &&
          waiting.map((d) => (
            <Alert key={d.id} tone="info">
              {d.status === "ready" ? "Có bản nháp" : "Đang viết bản nháp"} {d.mode === "merge" ? "bổ sung" : "nâng cấp"} tài liệu này từ {sourceCount(d)}
              {d.created_by ? ` (${d.created_by.name})` : ""}.{" "}
              <button type="button" className="link sm" onClick={() => onDraft(d.id)}>
                {d.status === "ready" ? "Xem và duyệt" : "Xem tiến độ"}
              </button>
            </Alert>
          ))}
        {doc.stale && !old && (
          <Alert tone="info">
            Tài liệu chưa được cập nhật hơn 6 tháng: có thể không còn đúng với hệ thống hiện tại.{doc.can_edit ? " Xem lại và lưu phiên bản mới nếu cần." : ""}
          </Alert>
        )}
        {doc.archived && doc.can_edit && (
          <Alert tone="warning">
            Tài liệu đã lưu trữ: không hiện trong danh sách và kết quả tìm kiếm.{" "}
            <button type="button" className="link sm" disabled={busy} onClick={() => void act(() => api.patch(`/docs/${doc.id}`, { archived: false }), "Đã đưa tài liệu trở lại.")}>
              Khôi phục
            </button>
            {" · "}
            <button type="button" className="link sm error" onClick={() => setConfirmDelete(true)}>
              Xoá vĩnh viễn
            </button>
          </Alert>
        )}
        {find && (
          <div className="docs-find">
            <Icon name="search" size={14} />
            <input autoFocus value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Tìm trong tài liệu này" aria-label="Tìm trong tài liệu này" />
            <span className="muted small">{term.trim().length >= 2 ? `${matches} kết quả` : ""}</span>
            <IconButton icon="close" label="Đóng tìm kiếm" size={14} onClick={() => { setFind(false); setTerm(""); }} />
          </div>
        )}
        {isPdf && (
          <Segmented<"text" | "original">
            value={view}
            onChange={setView}
            label="Cách xem"
            options={[
              { value: "text", label: "Văn bản (tìm được, có mục lục)" },
              { value: "original", label: "Bản gốc PDF" },
            ]}
          />
        )}
        {oldLoading ? (
          <Loading rows={8} />
        ) : isPdf && view === "original" ? (
          <PdfView docId={doc.id} number={shown.number} />
        ) : plain ? (
          <pre className="doc-plain">{body}</pre>
        ) : (
          <DocMarkdown text={body} term={term} dropTitle={doc.title} />
        )}
        {!old && doc.sources.length > 0 && (
          <section className="docs-sources" aria-labelledby="doc-sources">
            <h2 id="doc-sources">Nguồn đã tổng hợp</h2>
            <ol>
              {doc.sources.map((s) => (
                <li key={s.url ?? `file:${s.name}`}>
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.title || s.url}
                    </a>
                  ) : (
                    <span>{s.name ?? s.title}</span>
                  )}{" "}
                  <span className="muted">
                    · {s.via === "gitlab" ? "GitLab" : s.via === "file" ? "File tải lên" : s.via === "paste" ? "Văn bản dán" : s.via === "image" ? "Ảnh chụp" : "Web"} · phiên bản {s.version}
                    {s.at ? ` · ${relative(s.at)}` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </section>
        )}
        {doc.can_edit && !doc.archived && !old && (
          <div className="docs-foot small muted">
            <span>
              Tạo {doc.created_at ? relative(doc.created_at) : "—"} bởi {doc.created_by?.name ?? "—"}
            </span>
            <button type="button" className="link sm" disabled={busy} onClick={() => void act(() => api.patch(`/docs/${doc.id}`, { archived: true }), "Đã lưu trữ tài liệu.")}>
              Lưu trữ tài liệu
            </button>
          </div>
        )}
      </article>
      <Outline items={toc} body={body} />

      {history && (
        <DocHistory
          doc={doc}
          busy={busy}
          onClose={() => setHistory(false)}
          onView={(n) => {
            setHistory(false);
            onVersion(n);
          }}
          onRestore={(n) => {
            setHistory(false);
            void act(() => api.post<DocDetail>(`/docs/${doc.id}/versions/${n}/restore`), `Đã khôi phục phiên bản ${n} thành phiên bản mới.`);
          }}
        />
      )}
      {sharing && (
        <ShareProjects
          doc={doc}
          onClose={() => setSharing(false)}
          onSaved={async (d) => {
            setSharing(false);
            await onChanged(d, "Đã cập nhật các dự án dùng tài liệu này.");
          }}
        />
      )}
      {fromLinks && (
        <Modal title={`Bổ sung “${doc.title}” bằng AI`} onClose={() => setFromLinks(null)} width={760}>
          <LinkForm
            projectId={doc.business_project_id}
            docs={[doc]}
            limits={drafts}
            preset={{ mode: fromLinks, documentId: doc.id }}
            onCancel={() => setFromLinks(null)}
            onQueued={(d) => {
              setFromLinks(null);
              onDraft(d.id);
            }}
          />
        </Modal>
      )}
      {renaming && (
        <RenameDoc
          doc={doc}
          onClose={() => setRenaming(false)}
          onSaved={async () => {
            setRenaming(false);
            await onChanged(undefined, "Đã lưu thông tin tài liệu.");
          }}
        />
      )}
      {confirmDelete && (
        <Modal
          title={`Xoá vĩnh viễn “${doc.title}”?`}
          onClose={() => setConfirmDelete(false)}
          busy={busy}
          width={460}
          footer={
            <>
              <button type="button" className="ghost" onClick={() => setConfirmDelete(false)} disabled={busy}>
                Huỷ
              </button>
              {!doc.archived && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setConfirmDelete(false);
                    void act(() => api.patch(`/docs/${doc.id}`, { archived: true }), "Đã lưu trữ tài liệu (khôi phục được trong “Xem tài liệu đã lưu trữ”).");
                  }}
                >
                  Lưu trữ thay vì xoá
                </button>
              )}
              <button
                type="button"
                className="danger"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await api.delete(`/docs/${doc.id}`);
                    setConfirmDelete(false);
                    await onDeleted(doc.title);
                  } catch (e) {
                    setError(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Xoá vĩnh viễn
              </button>
            </>
          }
        >
          <p style={{ marginTop: 0 }}>
            Xoá hẳn tài liệu khỏi dự án <strong>{doc.business_project}</strong>:
          </p>
          <ul className="doc-delete-list">
            <li>
              {doc.version} phiên bản và lịch sử thay đổi{doc.current.file_name ? `, file gốc (${doc.current.file_name}${doc.version > 1 ? "…" : ""})` : ""}
            </li>
            <li>kết quả tìm kiếm và bản nháp AI của tài liệu này</li>
          </ul>
          <p className="muted small">Không khôi phục được. Ảnh đã dán vào tài liệu vẫn giữ trong dự án.</p>
          {!doc.archived && (
            <Alert tone="info">
              Chỉ muốn ẩn tài liệu cũ? <strong>Lưu trữ</strong> thì tài liệu biến khỏi danh sách và tìm kiếm nhưng vẫn khôi phục được.
            </Alert>
          )}
        </Modal>
      )}
    </div>
  );
}

/**
 * The dự án a document serves (docs/specs/29 §17): its home, and the ones it is shared with. One
 * adds a dự án where one may edit documents; one drops a link there or as an editor of the home.
 */
function ShareProjects({ doc, onClose, onSaved }: { doc: DocDetail; onClose: () => void; onSaved: (d: DocDetail) => Promise<void> }) {
  const projects = useApi<DocProject[]>("/docs/projects");
  const home = doc.shared.find((p) => p.home);
  const homeEditable = Boolean(home?.can_edit);
  const [chosen, setChosen] = useState<Set<number>>(() => new Set(doc.shared.filter((p) => !p.home).map((p) => p.id)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const linked = new Set(doc.shared.filter((p) => !p.home).map((p) => p.id));
  const rows = (projects.data ?? []).filter((p) => p.id !== doc.business_project_id);
  const changed = rows.some((p) => chosen.has(p.id) !== linked.has(p.id));

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await api.put<DocDetail>(`/docs/${doc.id}/projects`, { project_ids: [...chosen] });
      await onSaved(d);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Dự án dùng “${doc.title}”`}
      onClose={onClose}
      busy={busy}
      width={560}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !changed}>
            {busy ? "Đang lưu…" : "Lưu"}
          </button>
        </>
      }
    >
      <p className="muted small" style={{ marginTop: 0 }}>
        Một tài liệu dùng chung cho nhiều dự án (ví dụ tài liệu sản phẩm cho cả BE, FE, Mobile): hiện trong danh sách, độ đầy đủ và tìm kiếm của
        từng dự án; sửa ở đâu cũng là một nội dung. Chỉ dự án gốc xoá được tài liệu.
      </p>
      {projects.error ? (
        <Alert tone="error">Không tải được danh sách dự án: {projects.error.message}</Alert>
      ) : !projects.data ? (
        <Loading rows={4} />
      ) : (
        <ul className="docs-share-list">
          <li>
            <label className="inline-check">
              <input type="checkbox" checked disabled />
              <span>
                {doc.business_project || home?.name} <Pill tone="info">gốc</Pill>
              </span>
            </label>
          </li>
          {rows.map((p) => {
            const on = chosen.has(p.id);
            // Adding needs the right to edit documents there; dropping needs it there or at home.
            const allowed = on ? p.can_edit || homeEditable : p.can_edit;
            return (
              <li key={p.id}>
                <label className={`inline-check${allowed ? "" : " muted"}`} title={allowed ? undefined : "Bạn không có quyền sửa tài liệu của dự án này"}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={busy || !allowed}
                    onChange={(e) =>
                      setChosen((old) => {
                        const next = new Set(old);
                        if (e.target.checked) next.add(p.id);
                        else next.delete(p.id);
                        return next;
                      })
                    }
                  />
                  <span>
                    {p.name} <span className="muted small">({p.docs} tài liệu)</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {doc.hidden_projects > 0 && <p className="muted small">Còn dùng chung với {doc.hidden_projects} dự án bạn không xem được: giữ nguyên.</p>}
      {error && <Alert tone="error">{error}</Alert>}
    </Modal>
  );
}

function UploadButton({ disabled, onFiles }: { disabled: boolean; onFiles: (f: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        hidden
        accept=".md,.markdown,.txt,.doc,.docx,.xlsx,.pdf"
        onChange={(e) => {
          if (e.target.files?.length) onFiles(Array.from(e.target.files));
          e.target.value = "";
        }}
      />
      <IconButton icon="upload" label="Tải file làm phiên bản mới" disabled={disabled} onClick={() => input.current?.click()} />
    </>
  );
}

function RenameDoc({ doc, onClose, onSaved }: { doc: DocDetail; onClose: () => void; onSaved: () => Promise<void> }) {
  const [title, setTitle] = useState(doc.title);
  const [category, setCategory] = useState<DocCategory>(doc.category);
  const [summary, setSummary] = useState(doc.summary ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/docs/${doc.id}`, { title: title.trim(), category, summary: summary.trim() || null });
      await onSaved();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Thông tin tài liệu"
      onClose={onClose}
      busy={busy}
      width={480}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !title.trim()}>
            Lưu
          </button>
        </>
      }
    >
      <form
        className="adm-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <label className="adm-field">
          <span className="small muted">Tiêu đề</span>
          <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </label>
        <label className="adm-field">
          <span className="small muted">Nhóm</span>
          <select value={category} onChange={(e) => setCategory(e.target.value as DocCategory)}>
            {CATEGORY_ORDER.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABEL[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Mô tả ngắn</span>
          <input value={summary} maxLength={300} onChange={(e) => setSummary(e.target.value)} />
        </label>
      </form>
    </Modal>
  );
}

function AddDocs({
  projectId,
  projectName,
  meta,
  start,
  docs,
  drafts,
  onQueued,
  existing,
  onClose,
  onUploaded,
  onTemplate,
  onBlank,
}: {
  projectId: number;
  projectName: string;
  meta: DocMeta;
  start: "upload" | "template" | "link";
  docs: DocItem[];
  drafts?: DraftList;
  onQueued: (draftId: number) => void;
  existing: { key: DocCategory; count: number }[];
  onClose: () => void;
  onUploaded: (count: number, lastId: number | null) => Promise<void>;
  onTemplate: (t: DocTemplate) => void;
  onBlank: () => void;
}) {
  const [tab, setTab] = useState<"upload" | "template" | "link">(start);
  const [category, setCategory] = useState<DocCategory>("other");
  const [items, setItems] = useState<UploadItem[]>([]);
  const [busy, setBusy] = useState(false);
  const upload = async (files: File[]) => {
    setBusy(true);
    let last: number | null = null;
    const ok = await uploadAll(files, meta.kinds, meta.max_mb, setItems, async (f) => {
      const d = await api.postRaw<DocDetail>(`/docs/upload?business_project_id=${projectId}&category=${category}&filename=${encodeURIComponent(f.name)}`, f);
      last = d.id;
      return `“${d.title}”${d.current.meta?.pages ? ` · ${d.current.meta.pages} trang` : ""}`;
    });
    setBusy(false);
    if (ok) await onUploaded(ok, files.length === 1 ? last : null);
  };
  const has = (c: DocCategory) => (existing.find((e) => e.key === c)?.count ?? 0) > 0;
  return (
    <Modal
      title={`Thêm tài liệu vào ${projectName}`}
      onClose={onClose}
      busy={busy}
      width={720}
      footer={
        <button type="button" className="ghost" onClick={onClose} disabled={busy}>
          Đóng
        </button>
      }
    >
      <Segmented<"upload" | "template" | "link">
        value={tab}
        onChange={setTab}
        label="Cách thêm"
        options={[
          { value: "upload", label: "Tải file lên" },
          { value: "link", label: "Tổng hợp bằng AI" },
          { value: "template", label: "Soạn mới (mẫu / trang trống)" },
        ]}
      />
      {tab === "link" ? (
        <div style={{ marginTop: 12 }}>
          <LinkForm projectId={projectId} docs={docs} limits={drafts} onQueued={(d) => onQueued(d.id)} />
        </div>
      ) : tab === "upload" ? (
        <div className="adm-form" style={{ marginTop: 12 }}>
          <label className="adm-field">
            <span className="small muted">Xếp vào nhóm (đổi được sau)</span>
            <select value={category} onChange={(e) => setCategory(e.target.value as DocCategory)} disabled={busy}>
              {CATEGORY_ORDER.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          <UploadDrop
            kinds={meta.kinds}
            maxMb={meta.max_mb}
            disabled={busy}
            onFiles={(f) => void upload(f)}
            hint={`Word (.doc, .docx), PDF, Markdown, Text, Excel · tối đa ${meta.max_mb} MB mỗi file · mỗi file thành một tài liệu`}
          />
          <UploadList items={items} onClear={() => setItems([])} />
          <p className="muted small">
            File được chuyển thành văn bản để hiển thị đẹp, tìm kiếm và có mục lục; file gốc vẫn được giữ để tải về (PDF xem được ngay
            trong trình duyệt). Tiêu đề lấy từ tiêu đề đầu tiên trong file, hoặc tên file. Ảnh nhúng trong Word/PDF chưa hiển thị.
          </p>
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <div className="docs-templates">
            {meta.templates.map((t) => (
              <button key={t.key} type="button" className="docs-template" onClick={() => onTemplate(t)}>
                <strong>
                  {t.title} {!has(t.category) && <Pill tone="info">còn thiếu</Pill>}
                </strong>
                <span className="muted small">{t.summary}</span>
              </button>
            ))}
            <button type="button" className="docs-template" onClick={onBlank}>
              <strong>Trang trống</strong>
              <span className="muted small">Tự viết bằng Markdown</span>
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
