"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { DocMarkdown, type ImageAlign, ImageResize, imageLine, imageSize } from "@/components/DocMarkdown";
import { Icon } from "@/components/icons";
import { Alert, Drawer, Segmented } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { CATEGORY_LABEL, CATEGORY_ORDER, type DocCategory, type DocDetail, placeholders } from "@/lib/docs";
import { type PastedImage, htmlToMarkdown, textOf } from "@/lib/htmlpaste";
import {
  type ColumnAlign,
  type MdTable,
  type TableAt,
  alignCol,
  cellOffset,
  deleteCol,
  deleteRow,
  findTable,
  formatTable,
  fromSpreadsheet,
  insertCol,
  insertRow,
  newTable,
} from "@/lib/mdtable";

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
  const picker = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(0);
  const projectId = start.kind === "edit" ? start.doc.business_project_id : start.projectId;
  // The image line the caret is on (docs/specs/29 §13.1): its size bar shows.
  const [onImage, setOnImage] = useState<{
    start: number;
    end: number;
    name: string;
    size: string | null;
    align: ImageAlign | null;
    wrap: boolean;
    src: string;
  } | null>(null);
  const [customSize, setCustomSize] = useState("");
  // Tables (docs/specs/29 §14): the one the caret is in, and the size picker.
  const [onTable, setOnTable] = useState<TableAt | null>(null);
  const [picking, setPicking] = useState(false);
  const [pick, setPick] = useState<[number, number]>([3, 3]);
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

  // Images (docs/specs/29 §13): pasted, dropped or picked, stored by the API and written in
  // the text as ![name](asset:<id>). A placeholder marks the spot while it uploads, so typing
  // on meanwhile is fine.
  const isImage = (f: File) => f.type.startsWith("image/");
  /** Put `md` at the caret as a block of its own (blank lines around); select `[from, to)` of it. */
  function insertBlock(md: string, select?: [number, number]) {
    const el = area.current;
    const text = el ? el.value : body;
    const at = el ? el.selectionStart : text.length;
    const before = text.slice(0, at);
    const after = text.slice(at);
    const lead = !before || before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
    const tail = after.startsWith("\n\n") ? "" : after.startsWith("\n") ? "\n" : "\n\n";
    setBody(before + lead + md + tail + after);
    const base = before.length + lead.length;
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      el.setSelectionRange(base + (select?.[0] ?? md.length), base + (select?.[1] ?? md.length));
      trackCaret();
    });
  }

  /** Replace the selection with `md` (one line of text: no blank lines added). */
  function insertInline(md: string) {
    const el = area.current;
    if (!el) return;
    const { selectionStart: a, selectionEnd: b, value } = el;
    setBody(value.slice(0, a) + md + value.slice(b));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(a + md.length, a + md.length);
      trackCaret();
    });
  }

  function insertTable(rows: number, cols: number) {
    setPicking(false);
    const md = formatTable(newTable(rows, cols));
    const first = cellOffset(md, -1, 0);
    insertBlock(md, [first, first + "Cột 1".length]); // typing replaces the first heading
  }

  /** Change the table under the caret and write it back lined up; the caret goes to (row, col). */
  function withTable(change: (at: TableAt) => { table: MdTable; row: number; col: number }) {
    const el = area.current;
    if (!el) return;
    const at = findTable(el.value, el.selectionStart);
    if (!at) return;
    const next = change(at);
    const md = formatTable(next.table);
    setBody(el.value.slice(0, at.start) + md + el.value.slice(at.end));
    const caret = at.start + cellOffset(md, next.row, next.col);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
      trackCaret();
    });
  }

  /** The pictures of pasted rich text (Google Docs, web pages): each placeholder becomes the
   * document's own image once uploaded (an address is fetched by the server: the browser may not
   * read it and Google's expire); one that fails becomes a link to where it was. */
  async function resolvePasted(images: PastedImage[]) {
    setUploading((n) => n + images.length);
    let failed = 0;
    for (const img of images) {
      try {
        let id: number;
        if (img.src.startsWith("data:")) {
          const blob = await (await fetch(img.src)).blob();
          if (blob.size > 5 * 1024 * 1024) throw new Error("ảnh lớn hơn 5 MB");
          id = (await api.postRaw<{ id: number }>(`/docs/assets?business_project_id=${projectId}&filename=${encodeURIComponent(img.alt)}`, blob)).id;
        } else {
          id = (await api.post<{ id: number }>("/docs/assets/fetch", { business_project_id: projectId, url: img.src, name: img.alt })).id;
        }
        setBody((text) => text.split(`(${img.token})`).join(`(asset:${id})`));
      } catch {
        failed++;
        const link = img.src.startsWith("http") ? `[Ảnh không tải được${img.alt ? `: ${img.alt}` : ""}](${img.src})` : "";
        setBody((text) => text.replace(new RegExp(`!\\[[^\\]]*\\]\\(${img.token.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&")}\\)`, "g"), link));
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (failed) setError(`${failed}/${images.length} ảnh không tải được (ảnh Google Docs riêng tư hoặc link đã hết hạn): đã để lại link tới ảnh gốc, có thể tải ảnh về rồi dán lại.`);
  }

  async function addImages(files: File[]) {
    const el = area.current;
    const shots = files.filter(isImage);
    if (!shots.length) return;
    let at = el ? el.selectionEnd : body.length;
    const marks = shots.map((_, i) => `![Đang tải ảnh ${Date.now()}-${i}…]()`);
    setBody((text) => {
      at = Math.min(at, text.length);
      const before = text.slice(0, at);
      const lead = !before || before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
      return `${before}${lead}${marks.join("\n\n")}\n\n${text.slice(at)}`;
    });
    setUploading((n) => n + shots.length);
    await Promise.all(
      shots.map(async (file, i) => {
        // A pasted screenshot is "image.png": no useful name, the server calls it "Ảnh".
        const name = /^image\.\w+$/i.test(file.name) ? "" : file.name;
        try {
          if (file.size > 5 * 1024 * 1024) throw new Error(`${file.name || "ảnh"} lớn hơn 5 MB`);
          const got = await api.postRaw<{ markdown: string }>(`/docs/assets?business_project_id=${projectId}&filename=${encodeURIComponent(name)}`, file);
          setBody((text) => text.replace(marks[i], got.markdown));
        } catch (e) {
          setBody((text) => text.replace(`${marks[i]}\n\n`, "").replace(marks[i], ""));
          setError(e instanceof ApiError ? e.message : String(e instanceof Error ? e.message : e));
        } finally {
          setUploading((n) => n - 1);
        }
      }),
    );
  }

  const IMAGE_LINE = /^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$/;
  function trackCaret() {
    const el = area.current;
    if (!el) return;
    const text = el.value;
    const pos = el.selectionStart;
    setOnTable(findTable(text, pos));
    const start = text.lastIndexOf("\n", pos - 1) + 1;
    const nl = text.indexOf("\n", pos);
    const end = nl < 0 ? text.length : nl;
    const m = IMAGE_LINE.exec(text.slice(start, end));
    if (!m) return setOnImage(null);
    const sized = imageSize(m[1]);
    setOnImage({ start, end, name: sized.alt, size: sized.size, align: sized.align, wrap: sized.wrap, src: m[2] });
    setCustomSize(sized.size && !sized.size.endsWith("%") ? sized.size : "");
  }

  /** Rewrite the image line under the caret with `size` ("50%", "480", none) and its place. */
  function resize(size: string | null, align: ImageAlign | null = onImage?.align ?? null, wrap: boolean = onImage?.wrap ?? false) {
    const el = area.current;
    if (!el || !onImage) return;
    const flows = wrap && (align === "left" || align === "right");
    const line = imageLine(onImage.name, size, align, onImage.src, flows);
    const text = el.value;
    setBody(text.slice(0, onImage.start) + line + text.slice(onImage.end));
    setOnImage({ ...onImage, end: onImage.start + line.length, size, align, wrap: flows });
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(onImage.start + line.length, onImage.start + line.length);
    });
  }

  /** The corner handle of an image in the preview was dragged: write its new size. */
  function resizeSrc(src: string, size: string) {
    const el = area.current;
    const text = el ? el.value : body;
    const at = text.indexOf(`](${src})`);
    if (at < 0) return;
    const start = text.lastIndexOf("\n", at) + 1;
    const nl = text.indexOf("\n", at);
    const end = nl < 0 ? text.length : nl;
    const m = IMAGE_LINE.exec(text.slice(start, end));
    if (!m) return;
    const { alt: name, align, wrap } = imageSize(m[1]);
    const line = imageLine(name, size, align, m[2], wrap);
    setBody(text.slice(0, start) + line + text.slice(end));
    setOnImage({ start, end: start + line.length, name, size, align, wrap, src: m[2] });
    setCustomSize("");
  }

  /** A click on an image in the preview puts the caret on its line (and shows its size bar). */
  function pickFromPreview(target: EventTarget) {
    const img = target instanceof HTMLImageElement ? target : null;
    const src = img?.dataset.mdSrc;
    const el = area.current;
    if (!src || !el) return;
    const at = el.value.indexOf(`](${src})`);
    if (at < 0) return;
    el.focus();
    el.setSelectionRange(at, at);
    const lineTop = el.value.slice(0, at).split("\n").length - 1;
    el.scrollTop = Math.max(0, lineTop * 22 - el.clientHeight / 3);
    trackCaret();
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
            <span
              className="doc-table-pick"
              onKeyDown={(e) => {
                if (e.key === "Escape") setPicking(false);
              }}
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setPicking(false);
              }}
            >
              <button
                type="button"
                className={`ghost sm${picking ? " active" : ""}`}
                title="Chèn bảng (dán ô từ Excel / Google Sheets cũng thành bảng)"
                aria-expanded={picking}
                aria-haspopup="grid"
                onClick={() => setPicking(!picking)}
                disabled={mode === "preview"}
              >
                Bảng ▾
              </button>
              {picking && (
                <div className="doc-table-grid" role="grid" aria-label="Chọn số hàng và cột">
                  <div className="doc-table-cells" onMouseLeave={() => setPick([3, 3])}>
                    {Array.from({ length: 8 }, (_, r) =>
                      Array.from({ length: 8 }, (_, c) => (
                        <button
                          key={`${r}-${c}`}
                          type="button"
                          className={r < pick[0] && c < pick[1] ? "on" : undefined}
                          aria-label={`${r + 1} hàng × ${c + 1} cột`}
                          onMouseEnter={() => setPick([r + 1, c + 1])}
                          onFocus={() => setPick([r + 1, c + 1])}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => insertTable(r + 1, c + 1)}
                        />
                      )),
                    )}
                  </div>
                  <div className="small muted doc-table-size">
                    {pick[0]} hàng × {pick[1]} cột · hàng đầu là tiêu đề
                  </div>
                </div>
              )}
            </span>
            <button
              type="button"
              className="ghost sm"
              title="Chèn ảnh (hoặc dán Ctrl+V / kéo thả ảnh vào ô soạn)"
              onClick={() => picker.current?.click()}
              disabled={mode === "preview"}
            >
              <Icon name="upload" size={14} /> Ảnh
            </button>
            <input
              ref={picker}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              multiple
              hidden
              onChange={(e) => {
                void addImages(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
            {uploading > 0 && <span className="small muted">Đang tải {uploading} ảnh…</span>}
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
        {onTable && !onImage && mode !== "preview" && (
          <div className="doc-editor-imagebar doc-editor-tablebar" role="toolbar" aria-label="Bảng">
            <span className="small muted">
              Bảng {onTable.table.rows.length + 1} × {onTable.table.head.length} · {onTable.row < 0 ? "hàng tiêu đề" : `hàng ${onTable.row + 1}`}, cột {onTable.col + 1}:
            </span>
            {(
              [
                { label: "+ Hàng trên", run: (a: TableAt) => ({ table: insertRow(a.table, Math.max(0, a.row)), row: Math.max(0, a.row), col: a.col }), off: false },
                { label: "+ Hàng dưới", run: (a: TableAt) => ({ table: insertRow(a.table, a.row + 1), row: a.row + 1, col: a.col }), off: false },
                { label: "+ Cột trái", run: (a: TableAt) => ({ table: insertCol(a.table, a.col), row: a.row, col: a.col }), off: false },
                { label: "+ Cột phải", run: (a: TableAt) => ({ table: insertCol(a.table, a.col + 1), row: a.row, col: a.col + 1 }), off: false },
                {
                  label: "Xoá hàng",
                  run: (a: TableAt) => ({ table: deleteRow(a.table, a.row), row: Math.min(a.row, a.table.rows.length - 2), col: a.col }),
                  off: onTable.row < 0,
                },
                {
                  label: "Xoá cột",
                  run: (a: TableAt) => ({ table: deleteCol(a.table, a.col), row: a.row, col: Math.max(0, a.col - 1) }),
                  off: onTable.table.head.length <= 1,
                },
              ] as { label: string; run: (a: TableAt) => { table: MdTable; row: number; col: number }; off: boolean }[]
            ).map((o) => (
              <button
                key={o.label}
                type="button"
                className={`sm ghost${o.label.startsWith("Xoá") ? " danger" : ""}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => withTable(o.run)}
                disabled={o.off}
                title={o.label.startsWith("Xoá") && o.off ? (onTable.row < 0 ? "Hàng tiêu đề không xoá được" : "Bảng cần ít nhất một cột") : undefined}
              >
                {o.label}
              </button>
            ))}
            <span className="doc-editor-sep" aria-hidden="true" />
            <span className="doc-editor-align" role="group" aria-label="Căn cột">
              {(
                [
                  { label: "Trái", a: "left" },
                  { label: "Giữa", a: "center" },
                  { label: "Phải", a: "right" },
                ] as { label: string; a: ColumnAlign }[]
              ).map((o) => {
                const now = onTable.table.align[onTable.col] ?? null;
                const on = now === o.a || (o.a === "left" && now === null);
                return (
                  <button
                    key={o.label}
                    type="button"
                    className={`sm${on ? " active" : " ghost"}`}
                    aria-pressed={on}
                    title={`Căn cột ${onTable.col + 1} ${o.label.toLowerCase()}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => withTable((a) => ({ table: alignCol(a.table, a.col, o.a === "left" ? null : o.a), row: a.row, col: a.col }))}
                  >
                    <span className={`doc-align-icon ${o.a}`} aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    {o.label}
                  </button>
                );
              })}
            </span>
            <span className="doc-editor-sep" aria-hidden="true" />
            <button
              type="button"
              className="sm ghost"
              title="Dàn thẳng các cột trong ô soạn (nội dung không đổi)"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => withTable((a) => ({ table: a.table, row: a.row, col: a.col }))}
            >
              Dàn thẳng
            </button>
          </div>
        )}
        {onImage && mode !== "preview" && (
          <div className="doc-editor-imagebar" role="toolbar" aria-label="Cỡ và vị trí ảnh">
            <span className="small muted">
              Ảnh “{onImage.name || "Ảnh"}”:
            </span>
            <span className="doc-editor-align" role="group" aria-label="Căn ảnh">
              {(
                [
                  { label: "Trái", align: "left" },
                  { label: "Giữa", align: "center" },
                  { label: "Phải", align: "right" },
                ] as { label: string; align: ImageAlign }[]
              ).map((o) => {
                const on = (onImage.align ?? "center") === o.align;
                return (
                  <button
                    key={o.align}
                    type="button"
                    className={`sm${on ? " active" : " ghost"}`}
                    aria-pressed={on}
                    title={`Căn ${o.label.toLowerCase()}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => resize(onImage.size, o.align)}
                  >
                    <span className={`doc-align-icon ${o.align}`} aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    {o.label}
                  </button>
                );
              })}
            </span>
            <button
              type="button"
              className={`sm${onImage.wrap ? " active" : " ghost"}`}
              aria-pressed={onImage.wrap}
              title={
                onImage.wrap
                  ? "Bỏ chữ bao quanh: ảnh thành một khối riêng"
                  : "Chữ chảy quanh ảnh (ảnh đang ở giữa sẽ chuyển sang trái); màn hình hẹp vẫn hiện ảnh riêng"
              }
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                if (onImage.wrap) return resize(onImage.size, onImage.align, false);
                const side = onImage.align === "right" ? "right" : "left";
                resize(onImage.size, side, true);
              }}
            >
              <span className="doc-wrap-icon" aria-hidden="true">
                <b />
                <i />
                <i />
                <i />
              </span>
              Chữ bao quanh
            </button>
            <span className="doc-editor-sep" aria-hidden="true" />
            {[
              { label: "Nhỏ", size: "25%" },
              { label: "Vừa", size: "50%" },
              { label: "Lớn", size: "75%" },
              { label: "Rộng hết", size: "100%" },
              { label: "Cỡ gốc", size: null },
            ].map((o) => (
              <button
                key={o.label}
                type="button"
                className={`sm${onImage.size === o.size ? " active" : " ghost"}`}
                aria-pressed={onImage.size === o.size}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => resize(o.size)}
              >
                {o.label}
              </button>
            ))}
            <label className="doc-editor-px">
              <input
                type="number"
                min={16}
                max={2000}
                step={10}
                value={customSize}
                onChange={(e) => setCustomSize(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    const n = Number(customSize);
                    if (n >= 16 && n <= 2000) resize(String(Math.round(n)));
                  }
                }}
                placeholder="px"
                aria-label="Chiều rộng ảnh (px), Enter để áp dụng"
              />
              <span className="small muted">px</span>
            </label>
            <button
              type="button"
              className="sm ghost"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                const n = Number(customSize);
                if (n >= 16 && n <= 2000) resize(String(Math.round(n)));
              }}
              disabled={!(Number(customSize) >= 16 && Number(customSize) <= 2000)}
            >
              Áp dụng
            </button>
          </div>
        )}
        <div className={`doc-editor-panes ${mode}`}>
          {mode !== "preview" && (
            <textarea
              ref={area}
              className="doc-editor-text"
              value={body}
              onChange={(e) => {
                setBody(e.target.value);
                trackCaret();
              }}
              onSelect={trackCaret}
              onClick={trackCaret}
              onKeyUp={trackCaret}
              onPaste={(e) => {
                // Rich text from Google Docs / Sheets, Word, web pages: its HTML keeps the links,
                // headings, lists and tables the plain text loses (docs/specs/29 §18).
                const rich = htmlToMarkdown(e.clipboardData.getData("text/html"));
                const files = Array.from(e.clipboardData.files).filter(isImage);
                // A copied picture alone (its file is on the clipboard): taken as the file below.
                if (rich && !(files.length && !textOf(rich.markdown))) {
                  e.preventDefault();
                  if (/\n|^(#|\||-\s|\d+\.\s|>|```|!\[)/.test(rich.markdown)) insertBlock(rich.markdown);
                  else insertInline(rich.markdown);
                  if (rich.images.length) void resolvePasted(rich.images);
                  return;
                }
                // Cells copied from Excel / Google Sheets become a table (Excel also puts a
                // picture of them on the clipboard: the cells win).
                const grid = fromSpreadsheet(e.clipboardData.getData("text/plain"));
                if (grid) {
                  e.preventDefault();
                  insertBlock(formatTable(grid));
                  return;
                }
                const shots = Array.from(e.clipboardData.files).filter(isImage);
                if (!shots.length) return; // text pastes as usual
                e.preventDefault();
                void addImages(shots);
              }}
              onDragOver={(e) => {
                if (Array.from(e.dataTransfer.items).some((i) => i.kind === "file")) e.preventDefault();
              }}
              onDrop={(e) => {
                const shots = Array.from(e.dataTransfer.files).filter(isImage);
                if (!shots.length) return;
                e.preventDefault();
                void addImages(shots);
              }}
              spellCheck={false}
              aria-label="Nội dung Markdown"
              placeholder={"# Tiêu đề\n\nViết bằng Markdown: ## mục, - danh sách, | bảng |, ```code```, > [!NOTE] lưu ý…\nDán ảnh chụp màn hình (Ctrl+V) hoặc kéo thả ảnh vào đây."}
            />
          )}
          {mode !== "write" && (
            <div className="doc-editor-preview" aria-label="Xem trước" onClick={(e) => pickFromPreview(e.target)}>
              {body.trim() ? (
                <ImageResize.Provider value={resizeSrc}>
                  <DocMarkdown text={body} />
                </ImageResize.Provider>
              ) : (
                <p className="muted">Xem trước hiện ở đây.</p>
              )}
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
