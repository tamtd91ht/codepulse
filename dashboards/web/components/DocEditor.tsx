"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { DocMarkdown } from "@/components/DocMarkdown";
import { Alert, Drawer, Segmented } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { CATEGORY_LABEL, CATEGORY_ORDER, type DocCategory, type DocDetail, placeholders } from "@/lib/docs";

type Mode = "split" | "write" | "preview";

/** What the editor starts from: an existing document (a new version) or a new one. */
export type EditorStart =
  | { kind: "edit"; doc: DocDetail }
  | { kind: "new"; projectId: number; title: string; category: DocCategory; body: string; template?: string; summary?: string };

const DRAFT_KEY = (start: EditorStart) => (start.kind === "edit" ? `cp:doc-draft:${start.doc.id}` : `cp:doc-draft:new:${start.projectId}:${start.template ?? "blank"}`);

function readDraft(key: string): { body: string; at: number } | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as { body: string; at: number }) : null;
  } catch {
    return null;
  }
}

function writeDraft(key: string, body: string | null) {
  try {
    if (body === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify({ body, at: Date.now() }));
  } catch {
    /* storage unavailable: the draft just is not kept */
  }
}

const SNIPPETS: { label: string; title: string; before: string; after?: string; block?: boolean }[] = [
  { label: "H2", title: "Tiêu đề mục", before: "## ", block: true },
  { label: "H3", title: "Tiêu đề mục con", before: "### ", block: true },
  { label: "B", title: "Chữ đậm", before: "**", after: "**" },
  { label: "I", title: "Chữ nghiêng", before: "*", after: "*" },
  { label: "`", title: "Code trong dòng", before: "`", after: "`" },
  { label: "• Danh sách", title: "Danh sách", before: "- ", block: true },
  { label: "☐ Việc", title: "Danh sách việc", before: "- [ ] ", block: true },
  { label: "Link", title: "Liên kết", before: "[", after: "](https://)" },
  { label: "Code", title: "Khối code", before: "```bash\n", after: "\n```", block: true },
  { label: "Bảng", title: "Bảng", before: "| Cột 1 | Cột 2 |\n|---|---|\n| ", after: " |  |", block: true },
  { label: "Lưu ý", title: "Khung lưu ý", before: "> [!NOTE]\n> ", block: true },
  { label: "Cảnh báo", title: "Khung cảnh báo", before: "> [!WARNING]\n> ", block: true },
];

/**
 * The Markdown editor of a document (docs/specs/29 §5): write, preview, or both side by side;
 * a toolbar for the common syntax; template placeholders counted; the draft kept in this browser
 * until saved; Ctrl/⌘+S saves. Saving an existing document sends the version it started from: a
 * version saved meanwhile by someone else is refused (409) and the text stays here.
 */
export function DocEditor({ start, onClose, onSaved }: { start: EditorStart; onClose: () => void; onSaved: (doc: DocDetail) => void }) {
  const initial = start.kind === "edit" ? start.doc.body : start.body;
  const key = DRAFT_KEY(start);
  const [body, setBody] = useState(initial);
  const [title, setTitle] = useState(start.kind === "edit" ? start.doc.title : start.title);
  const [category, setCategory] = useState<DocCategory>(start.kind === "edit" ? start.doc.category : start.category);
  const [summary, setSummary] = useState(start.kind === "edit" ? (start.doc.summary ?? "") : (start.summary ?? ""));
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<Mode>("split");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ body: string; at: number } | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const dirty = body !== initial;
  const left = placeholders(body);

  useEffect(() => {
    const saved = readDraft(key);
    if (saved && saved.body !== initial) setDraft(saved);
  }, [key, initial]);

  useEffect(() => {
    if (!dirty) return;
    const t = setTimeout(() => writeDraft(key, body), 800);
    return () => clearTimeout(t);
  }, [body, dirty, key]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = useCallback(async () => {
    if (busy || !body.trim() || !title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      let doc: DocDetail;
      if (start.kind === "edit") {
        if (title.trim() !== start.doc.title || category !== start.doc.category || summary.trim() !== (start.doc.summary ?? "")) {
          await api.patch(`/docs/${start.doc.id}`, { title: title.trim(), category, summary: summary.trim() || null });
        }
        doc = await api.put<DocDetail>(`/docs/${start.doc.id}/content`, { body, note: note.trim() || null, base_version: start.doc.version });
      } else {
        doc = await api.post<DocDetail>("/docs", {
          business_project_id: start.projectId,
          title: title.trim(),
          category,
          summary: summary.trim() || null,
          body,
          template: start.template ?? null,
          note: note.trim() || null,
        });
      }
      writeDraft(key, null);
      onSaved(doc);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? `${e.message}. Bản nháp của bạn vẫn ở đây và đã lưu trong trình duyệt: chép nội dung, mở lại tài liệu rồi dán vào bản mới.`
          : e instanceof ApiError
            ? e.message
            : String(e),
      );
    } finally {
      setBusy(false);
    }
  }, [busy, body, title, start, category, summary, note, key, onSaved]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  function insert(s: (typeof SNIPPETS)[number]) {
    const el = area.current;
    if (!el) return;
    const { selectionStart: a, selectionEnd: b } = el;
    const chosen = body.slice(a, b);
    const lineStart = s.block && a > 0 && body[a - 1] !== "\n" ? "\n" : "";
    const text = `${lineStart}${s.before}${chosen}${s.after ?? ""}`;
    const next = body.slice(0, a) + text + body.slice(b);
    setBody(next);
    requestAnimationFrame(() => {
      el.focus();
      const caret = a + lineStart.length + s.before.length + chosen.length;
      el.setSelectionRange(caret, caret);
    });
  }

  function nextPlaceholder() {
    const el = area.current;
    if (!el) return;
    const re = /<[^<>\n]{2,120}>/g;
    re.lastIndex = el.selectionEnd;
    let m = re.exec(body);
    if (!m) {
      re.lastIndex = 0;
      m = re.exec(body);
    }
    if (m) {
      el.focus();
      el.setSelectionRange(m.index, m.index + m[0].length);
    }
  }

  const close = () => {
    if (dirty && !window.confirm("Bỏ các thay đổi chưa lưu? (Bản nháp vẫn được giữ trong trình duyệt này.)")) return;
    onClose();
  };

  return (
    <Drawer
      title={start.kind === "edit" ? `Sửa: ${start.doc.title}` : "Tài liệu mới"}
      onClose={close}
      width="wide"
      actions={
        <>
          <button type="button" className="ghost" onClick={close} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" onClick={() => void save()} disabled={busy || !body.trim() || !title.trim()} title="Ctrl/⌘ + S">
            {busy ? "Đang lưu…" : start.kind === "edit" ? `Lưu phiên bản ${start.doc.version + 1}` : "Tạo tài liệu"}
          </button>
        </>
      }
    >
      <div className="doc-editor">
        {error && <Alert tone="error" onClose={() => setError(null)}>{error}</Alert>}
        {draft && (
          <Alert tone="info" onClose={() => setDraft(null)}>
            Có bản nháp chưa lưu trong trình duyệt này ({new Date(draft.at).toLocaleString("vi-VN")}).{" "}
            <button
              type="button"
              className="link sm"
              onClick={() => {
                setBody(draft.body);
                setDraft(null);
              }}
            >
              Mở bản nháp
            </button>{" "}
            ·{" "}
            <button
              type="button"
              className="link sm"
              onClick={() => {
                writeDraft(key, null);
                setDraft(null);
              }}
            >
              Bỏ bản nháp
            </button>
          </Alert>
        )}
        <div className="doc-editor-fields">
          <label className="adm-field">
            <span className="small muted">Tiêu đề *</span>
            <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} required />
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
          <label className="adm-field doc-editor-summary">
            <span className="small muted">Mô tả ngắn (hiện dưới tiêu đề)</span>
            <input value={summary} maxLength={300} onChange={(e) => setSummary(e.target.value)} placeholder="Tài liệu này dành cho ai, trả lời câu hỏi gì" />
          </label>
        </div>
        <div className="doc-editor-bar">
          <div className="doc-editor-tools" role="toolbar" aria-label="Định dạng">
            {SNIPPETS.map((s) => (
              <button key={s.title} type="button" className="ghost sm" title={s.title} onClick={() => insert(s)} disabled={mode === "preview"}>
                {s.label}
              </button>
            ))}
          </div>
          <span className="spacer" />
          {left > 0 && (
            <button type="button" className="link sm" onClick={nextPlaceholder} title="Chọn chỗ <…> tiếp theo để điền">
              Còn {left} chỗ &lt;…&gt; cần điền
            </button>
          )}
          <Segmented<Mode>
            value={mode}
            onChange={setMode}
            options={[
              { value: "write", label: "Soạn" },
              { value: "split", label: "Song song" },
              { value: "preview", label: "Xem trước" },
            ]}
            label="Chế độ soạn"
          />
        </div>
        <div className={`doc-editor-panes ${mode}`}>
          {mode !== "preview" && (
            <textarea
              ref={area}
              className="doc-editor-text"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              spellCheck={false}
              aria-label="Nội dung Markdown"
              placeholder={"# Tiêu đề\n\nViết bằng Markdown: ## mục, - danh sách, | bảng |, ```code```, > [!NOTE] lưu ý…"}
            />
          )}
          {mode !== "write" && (
            <div className="doc-editor-preview" aria-label="Xem trước">
              {body.trim() ? <DocMarkdown text={body} /> : <p className="muted">Xem trước hiện ở đây.</p>}
            </div>
          )}
        </div>
        <div className="doc-editor-foot">
          <label className="adm-field" style={{ flex: 1 }}>
            <span className="small muted">Ghi chú thay đổi (hiện trong lịch sử)</span>
            <input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="Ví dụ: cập nhật bước rollback cho bản 2.3" />
          </label>
          <span className="muted small">{body.length.toLocaleString("vi-VN")} ký tự · Ctrl/⌘+S để lưu</span>
        </div>
      </div>
    </Drawer>
  );
}
