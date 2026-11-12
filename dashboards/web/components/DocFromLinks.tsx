"use client";

import { useEffect, useMemo, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { TextDiff } from "@/components/DocHistory";
import { DocMarkdown } from "@/components/DocMarkdown";
import { Icon, IconButton } from "@/components/icons";
import { Alert, Loading, Pill, Segmented } from "@/components/ui";
import { UploadDrop, type UploadItem, UploadList, fileSize, uploadAll } from "@/components/UploadDrop";
import { ApiError, api, useApi } from "@/lib/api";
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  DRAFT_ACTIVE,
  type DocCategory,
  type DocDetail,
  type DocDraft,
  type DocItem,
  type DraftList,
  type DraftSource,
  type DraftMode,
  type ExtractedFile,
  MODE_LABEL,
  PASTE_KIND,
  placeholders,
  sourceCount,
} from "@/lib/docs";
import { relative } from "@/lib/format";

/*
 * Tổng hợp bằng AI (docs/specs/29 §12, user 2026-11-12). Someone who may edit a dự án's documents
 * gives a few sources: links (web pages, or files / README / wiki of the dự án's GitLab repos)
 * and uploaded files (Word, PDF, Markdown, Text, Excel: only their text is kept); the AI
 * reads them and writes a document to the dự án's standard: a new one, the current one with the
 * new facts added, or the current one upgraded to the standard. The result is a draft: it is
 * read, compared with the current version, edited if needed, and only then saved as a version.
 */

const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

const MODE_HINT: Record<DraftMode, string> = {
  new: "AI viết một tài liệu mới theo mẫu chuẩn của nhóm bạn chọn, chỉ từ nội dung các nguồn.",
  merge: "Giữ nguyên tài liệu đang có; thêm thông tin mới vào đúng mục, không xoá gì. Chỗ nguồn nói khác tài liệu được đánh dấu để bạn quyết.",
  upgrade: "Sắp xếp lại tài liệu theo đúng mẫu chuẩn, viết lại cho rõ, giữ mọi thông tin cũ và gộp thêm thông tin từ nguồn.",
};

const STEPS: { key: "queued" | "fetching" | "writing"; label: string }[] = [
  { key: "queued", label: "Xếp hàng" },
  { key: "fetching", label: "Đọc nguồn" },
  { key: "writing", label: "AI đang viết" },
];

/** The dự án's drafts, polled while one is being written. */
export function useDrafts(projectId: number | null, enabled: boolean) {
  return useApi<DraftList>(enabled && projectId ? `/docs/drafts?business_project_id=${projectId}` : null, {
    refreshInterval: (latest) => (latest?.drafts.some((d) => DRAFT_ACTIVE.includes(d.status)) ? 4000 : 0),
  });
}

function lines(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** What to read and what to write: links and files, new / merge / upgrade, focus. */
export function LinkForm({
  projectId,
  docs,
  preset,
  limits,
  onQueued,
  onCancel,
}: {
  projectId: number;
  docs: DocItem[];
  preset?: { mode: DraftMode; documentId: number };
  limits?: DraftList;
  onQueued: (draft: DocDraft) => void;
  onCancel?: () => void;
}) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<DraftMode>(preset?.mode ?? "new");
  const [documentId, setDocumentId] = useState<number | "">(preset?.documentId ?? "");
  const [category, setCategory] = useState<DocCategory>("other");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<ExtractedFile[]>([]);
  const [reading, setReading] = useState<UploadItem[]>([]);
  // A text pasted as a source (meeting notes, a chat thread, a page that needs a login).
  const [pasting, setPasting] = useState(false);
  const [pasteName, setPasteName] = useState("");
  const [pasteText, setPasteText] = useState("");
  const max = limits?.max_links ?? 5;
  const kinds = limits?.file_kinds ?? [".md", ".markdown", ".txt", ".doc", ".docx", ".xlsx", ".pdf"];
  const maxMb = limits?.max_file_mb ?? 20;
  const urls = lines(text);
  const bad = urls.filter((u) => !/^https?:\/\/[^\s/]+/i.test(u));
  const total = urls.length + files.length;
  const active = docs.filter((d) => !d.archived);
  const left = limits ? Math.max(0, limits.per_day - limits.used_today) : null;
  const uploading = reading.some((r) => r.state === "waiting" || r.state === "uploading");
  const ready = total > 0 && total <= max && !bad.length && !uploading && (mode === "new" || documentId !== "") && left !== 0;

  const sourceChars = limits?.source_chars ?? 60_000;
  const pasteDefault = `Văn bản dán ${files.filter((f) => f.kind === PASTE_KIND).length + 1}`;
  const pasteClash = files.some((f) => f.name === (pasteName.trim() || pasteDefault));
  const addPaste = () => {
    const text = pasteText.trim();
    if (!text) return;
    const name = pasteName.trim() || pasteDefault;
    setFiles((old) => [
      ...old,
      {
        name,
        kind: PASTE_KIND,
        size: new Blob([text]).size,
        chars: text.length,
        truncated: text.length > sourceChars,
        text: text.slice(0, sourceChars),
        pages: null,
      },
    ]);
    setPasteName("");
    setPasteText("");
    setPasting(false);
  };

  // Each file is read on the server now (its text shown here); the draft keeps that text only.
  const addFiles = async (picked: File[]) => {
    const room = Math.max(0, max - total);
    const fresh = picked.filter((f) => !files.some((x) => x.name === f.name));
    if (fresh.length > room) setError(`Tối đa ${max} nguồn (link và file) mỗi lần: bỏ bớt ${fresh.length - room} file.`);
    await uploadAll(fresh.slice(0, room), kinds, maxMb, setReading, async (f) => {
      const got = await api.postRaw<ExtractedFile>(`/docs/drafts/extract?business_project_id=${projectId}&filename=${encodeURIComponent(f.name)}`, f);
      setFiles((old) => [...old.filter((x) => x.name !== got.name), got]);
      return `${got.chars.toLocaleString("vi-VN")} ký tự${got.truncated ? ` · chỉ gửi ${got.text.length.toLocaleString("vi-VN")} ký tự đầu` : ""}`;
    });
    window.setTimeout(() => setReading((items) => items.filter((i) => i.state === "error")), 2500);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const draft = await api.post<DocDraft>("/docs/drafts", {
        business_project_id: projectId,
        mode,
        document_id: mode === "new" ? null : documentId,
        category: mode === "new" ? category : null,
        urls,
        files: files.map(({ name, kind, size, text, truncated }) => ({ name, kind, size, text, truncated })),
        instruction: instruction.trim() || null,
      });
      onQueued(draft);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="adm-form docs-link-form">
      <label className="adm-field">
        <span className="small muted">
          Link nguồn (mỗi dòng một link) · link, file và văn bản dán: tối đa {max} nguồn
        </span>
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"https://wiki.congty.vn/thanh-toan/hoan-tien\nhttps://gitlab.congty.vn/nhom/repo/-/blob/main/docs/api.md"}
          aria-invalid={bad.length > 0 || total > max}
          disabled={busy}
        />
        <span className={`small ${bad.length || total > max ? "bad-text" : "muted"}`}>
          {bad.length
            ? `Không phải link http/https: ${bad[0]}`
            : total > max
              ? `Nhiều hơn ${max} nguồn: tách thành nhiều lần`
              : "Trang công khai, hoặc file / README / wiki của repo trong dự án này trên GitLab (đọc qua kết nối GitLab, không cần công khai)."}
        </span>
      </label>

      <div className="adm-field">
        <span className="small muted">File hoặc văn bản nguồn (không bắt buộc)</span>
        <UploadDrop
          kinds={kinds}
          maxMb={maxMb}
          compact
          disabled={busy || total >= max}
          onFiles={(f) => void addFiles(f)}
          title={total >= max ? `Đã đủ ${max} nguồn` : "Kéo thả file vào đây hoặc bấm để chọn"}
          hint={`Word, PDF, Markdown, Text, Excel · tối đa ${maxMb} MB mỗi file · chỉ phần chữ được gửi cho AI, file không được lưu`}
        />
        <UploadList items={reading} onClear={() => setReading([])} />
        {pasting ? (
          <div className="docs-paste">
            <input
              value={pasteName}
              onChange={(e) => setPasteName(e.target.value)}
              maxLength={120}
              placeholder={`Tên nguồn, ví dụ: Ghi chú họp 12/11 (mặc định “${pasteDefault}”)`}
              aria-label="Tên của văn bản dán"
              disabled={busy}
            />
            <textarea
              rows={6}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="Dán nội dung vào đây: ghi chú họp, trao đổi trên chat, nội dung trang cần đăng nhập…"
              aria-label="Nội dung văn bản dán"
              autoFocus
              disabled={busy}
            />
            <div className="docs-paste-foot">
              <span className={`small ${pasteText.length > sourceChars ? "bad-text" : "muted"}`}>
                {pasteText.trim().length.toLocaleString("vi-VN")} ký tự
                {pasteText.length > sourceChars ? ` · AI chỉ đọc ${sourceChars.toLocaleString("vi-VN")} ký tự đầu` : ""}
                {pasteClash ? " · trùng tên một nguồn khác" : ""}
              </span>
              <span className="toolbar">
                <button type="button" className="ghost sm" onClick={() => setPasting(false)} disabled={busy}>
                  Huỷ
                </button>
                <button type="button" className="sm" onClick={addPaste} disabled={busy || !pasteText.trim() || pasteClash || total >= max}>
                  <Icon name="plus" size={14} /> Thêm làm nguồn
                </button>
              </span>
            </div>
          </div>
        ) : (
          <button type="button" className="sm docs-paste-open" onClick={() => setPasting(true)} disabled={busy || total >= max}>
            <Icon name="edit" size={14} /> Dán văn bản làm nguồn
          </button>
        )}
        {files.length > 0 && (
          <ul className="docs-src-files" aria-label="File và văn bản nguồn">
            {files.map((f) => (
              <li key={f.name}>
                <Icon name={f.kind === PASTE_KIND ? "edit" : "file"} size={14} />
                <span className="docs-src-file-name">{f.name}</span>
                <span className="small muted">
                  {f.kind === PASTE_KIND ? "Văn bản dán" : fileSize(f.size)} · {f.chars.toLocaleString("vi-VN")} ký tự{f.pages ? ` · ${f.pages} trang` : ""}
                  {f.truncated ? ` · AI đọc ${f.text.length.toLocaleString("vi-VN")} ký tự đầu` : ""}
                </span>
                <IconButton icon="close" label={`Bỏ ${f.name}`} size={14} onClick={() => setFiles((old) => old.filter((x) => x.name !== f.name))} disabled={busy} />
              </li>
            ))}
          </ul>
        )}
        <span className="small muted">
          {total} / {max} nguồn{urls.length ? ` · ${urls.length} link` : ""}
          {files.some((f) => f.kind !== PASTE_KIND) ? ` · ${files.filter((f) => f.kind !== PASTE_KIND).length} file` : ""}
          {files.some((f) => f.kind === PASTE_KIND) ? ` · ${files.filter((f) => f.kind === PASTE_KIND).length} văn bản dán` : ""}
        </span>
      </div>

      <div className="docs-mode-cards" role="radiogroup" aria-label="Kết quả">
        {(["new", "merge", "upgrade"] as DraftMode[]).map((m) => (
          <label key={m} className={`docs-mode-card${mode === m ? " active" : ""}${m !== "new" && !active.length ? " disabled" : ""}`}>
            <input type="radio" name="mode" value={m} checked={mode === m} disabled={busy || (m !== "new" && !active.length)} onChange={() => setMode(m)} />
            <strong>
              <Icon name={m === "new" ? "plus" : m === "merge" ? "edit" : "sparkle"} size={14} /> {MODE_LABEL[m]}
            </strong>
            <span className="small muted">{MODE_HINT[m]}</span>
          </label>
        ))}
      </div>

      {mode === "new" ? (
        <label className="adm-field">
          <span className="small muted">Loại tài liệu (quyết định mẫu chuẩn AI dùng)</span>
          <select value={category} onChange={(e) => setCategory(e.target.value as DocCategory)} disabled={busy}>
            {CATEGORY_ORDER.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABEL[c]}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label className="adm-field">
          <span className="small muted">Tài liệu cần {mode === "merge" ? "bổ sung" : "nâng cấp"}</span>
          <select value={documentId} onChange={(e) => setDocumentId(e.target.value ? Number(e.target.value) : "")} disabled={busy || Boolean(preset)}>
            <option value="">— Chọn tài liệu —</option>
            {active.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title} ({CATEGORY_LABEL[d.category]})
              </option>
            ))}
          </select>
        </label>
      )}

      <label className="adm-field">
        <span className="small muted">Yêu cầu thêm (không bắt buộc)</span>
        <input
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          maxLength={1000}
          placeholder="Ví dụ: chỉ lấy phần API hoàn tiền; viết ngắn gọn cho người mới"
          disabled={busy}
        />
      </label>

      <p className="muted small" style={{ margin: 0 }}>
        Nội dung các link, file và văn bản dán được gửi tới AI (Claude) để viết. Kết quả là <strong>bản nháp</strong>: bạn xem, so sánh và sửa trước khi lưu thành phiên bản;
        tài liệu không thay đổi cho tới khi bạn bấm lưu.
        {left !== null && ` Còn ${left}/${limits?.per_day} lần trong 24 giờ.`}
        {limits && !limits.url_hosts && " Wiki nội bộ (địa chỉ IP nội bộ) chỉ đọc được khi admin thêm tên máy vào ASSISTANT_URL_HOSTS."}
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="toolbar" style={{ justifyContent: "flex-end" }}>
        {onCancel && (
          <button type="button" className="ghost" onClick={onCancel} disabled={busy}>
            Huỷ
          </button>
        )}
        <button type="button" className="primary" onClick={() => void submit()} disabled={busy || !ready}>
          <Icon name="sparkle" size={14} /> {busy ? "Đang gửi…" : "Tổng hợp"}
        </button>
      </div>
    </div>
  );
}

function Progress({ draft }: { draft: DocDraft }) {
  const at = STEPS.findIndex((s) => s.key === draft.status);
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  const seconds = draft.created_at ? Math.max(0, Math.round((Date.now() - new Date(draft.created_at).getTime()) / 1000)) : 0;
  return (
    <div className="docs-draft-progress" role="status" aria-live="polite">
      <ol className="docs-steps">
        {STEPS.map((s, i) => (
          <li key={s.key} className={i < at ? "done" : i === at ? "now" : undefined}>
            <span className="docs-step-dot" aria-hidden="true">
              {i < at ? <Icon name="check" size={12} /> : i + 1}
            </span>
            {s.label}
          </li>
        ))}
        <li>
          <span className="docs-step-dot" aria-hidden="true">
            4
          </span>
          Sẵn sàng duyệt
        </li>
      </ol>
      <p className="muted small">
        {draft.status === "writing"
          ? "AI đang đọc nguồn và viết theo chuẩn; tài liệu dài có thể mất vài phút."
          : draft.status === "fetching"
            ? "Đang đọc các nguồn."
            : "Đang chờ tới lượt."}{" "}
        Đã {seconds < 60 ? `${seconds} giây` : `${Math.floor(seconds / 60)} phút ${seconds % 60} giây`}. Bạn có thể đóng cửa sổ này: bản nháp nằm trong mục
        “Tổng hợp bằng AI” của dự án.
      </p>
      <Loading rows={3} />
    </div>
  );
}

const VIA_LABEL: Record<string, string> = { web: "Web", gitlab: "GitLab", file: "File tải lên", paste: "Văn bản dán" };

function SourceList({ draft }: { draft: DocDraft }) {
  const items: DraftSource[] = draft.sources ?? [
    ...draft.files.map((f) => ({ url: null, name: f.name, title: f.name, via: "file" as const, chars: f.chars, error: null })),
    ...draft.urls.map((url) => ({ url, title: null, via: "web" as const, chars: 0, error: null })),
  ];
  return (
    <ul className="docs-draft-sources">
      {items.map((s, i) => (
        <li key={s.url ?? `file:${s.name}`} className={s.error ? "bad" : undefined}>
          <span className="docs-src-n">{s.error ? <Icon name="alertCircle" size={12} /> : i + 1 - items.slice(0, i).filter((x) => x.error).length}</span>
          <span style={{ minWidth: 0 }}>
            {s.url ? (
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="docs-src-link">
                {s.title || s.url}
              </a>
            ) : (
              <span className="docs-src-link">
                <Icon name={s.via === "paste" ? "edit" : "file"} size={12} /> {s.name ?? s.title}
              </span>
            )}
            <span className="small muted">
              {VIA_LABEL[s.via] ?? s.via}
              {s.error ? ` · ${s.error}` : s.chars ? ` · ${s.chars.toLocaleString("vi-VN")} ký tự` : ""}
              {s.truncated ? " (đã cắt bớt)" : ""}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Bullets({ title, items, tone }: { title: string; items: string[]; tone?: "warn" }) {
  if (!items.length) return null;
  return (
    <section className={`docs-draft-notes${tone ? ` ${tone}` : ""}`}>
      <h4>{title}</h4>
      <ul>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </section>
  );
}

/** Read, compare, edit and apply (or drop) one draft. */
export function DraftReview({
  draftId,
  onClose,
  onApplied,
  onChanged,
}: {
  draftId: number;
  onClose: () => void;
  onApplied: (doc: DocDetail, draft: DocDraft) => Promise<void> | void;
  onChanged?: () => void;
}) {
  const res = useApi<DocDraft>(`/docs/drafts/${draftId}`, {
    refreshInterval: (latest) => (latest && DRAFT_ACTIVE.includes(latest.status) ? 2500 : 0),
  });
  const draft = res.data;
  const current = useApi<DocDetail>(draft && draft.mode !== "new" && draft.document_id ? `/docs/${draft.document_id}` : null);
  const [view, setView] = useState<"preview" | "diff" | "edit">("preview");
  const [body, setBody] = useState<string | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [category, setCategory] = useState<DocCategory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = draft?.status;
  useEffect(() => {
    if (status && !DRAFT_ACTIVE.includes(status)) onChanged?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the status moves
  }, [status]);

  const text = body ?? draft?.body ?? "";
  const left = useMemo(() => placeholders(text), [text]);
  const newer = Boolean(draft && current.data && draft.base_version !== null && current.data.version !== draft.base_version);

  async function act(run: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await run();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const apply = () =>
    act(async () => {
      if (!draft) return;
      const doc = await api.post<DocDetail>(`/docs/drafts/${draft.id}/apply`, {
        body: body !== null && body !== draft.body ? body : null,
        title: title ?? null,
        summary: summary ?? null,
        category: draft.mode === "new" ? category : null,
      });
      await onApplied(doc, draft);
    });
  const retry = () =>
    act(async () => {
      await api.post(`/docs/drafts/${draftId}/retry`);
      setBody(null);
      await res.mutate();
      onChanged?.();
    });
  const discard = () =>
    act(async () => {
      await api.delete(`/docs/drafts/${draftId}`);
      onChanged?.();
      onClose();
    });

  const heading = draft
    ? draft.mode === "new"
      ? `Bản nháp: tài liệu mới (${CATEGORY_LABEL[draft.category]})`
      : `Bản nháp: ${MODE_LABEL[draft.mode].toLowerCase()} “${draft.document_title ?? "…"}”`
    : "Bản nháp tổng hợp bằng AI";

  const ready = draft?.status === "ready";
  return (
    <Modal
      title={heading}
      onClose={onClose}
      busy={busy}
      width={1120}
      footer={
        <>
          {draft && draft.status !== "applied" && (
            <button type="button" className="ghost danger" onClick={() => void discard()} disabled={busy} style={{ marginRight: "auto" }}>
              Bỏ bản nháp
            </button>
          )}
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Đóng
          </button>
          {draft && (ready || draft.status === "failed") && (
            <button type="button" onClick={() => void retry()} disabled={busy}>
              <Icon name="sync" size={14} /> {draft.status === "failed" ? "Thử lại" : "Viết lại"}
            </button>
          )}
          {ready && (
            <button type="button" className="primary" onClick={() => void apply()} disabled={busy || !text.trim() || newer}>
              <Icon name="check" size={14} /> {draft?.mode === "new" ? "Tạo tài liệu" : `Lưu thành phiên bản ${(current.data?.version ?? 0) + 1}`}
            </button>
          )}
        </>
      }
    >
      {res.error ? (
        <Alert tone="error">
          Không tải được bản nháp: {res.error.message}{" "}
          <button type="button" className="link sm" onClick={() => void res.mutate()}>
            Thử lại
          </button>
        </Alert>
      ) : !draft ? (
        <Loading rows={6} />
      ) : DRAFT_ACTIVE.includes(draft.status) ? (
        <div className="docs-draft-grid">
          <Progress draft={draft} />
          <aside className="docs-draft-side">
            <h4>Nguồn</h4>
            <SourceList draft={draft} />
            {draft.instruction && <p className="small muted">Yêu cầu thêm: {draft.instruction}</p>}
          </aside>
        </div>
      ) : draft.status === "failed" ? (
        <>
          <Alert tone="error">{draft.error ?? "Tổng hợp không thành công."}</Alert>
          <h4>Nguồn</h4>
          <SourceList draft={draft} />
          <p className="muted small">
            Link cần đăng nhập (Confluence, Google Docs riêng tư…) thì máy chủ không đọc được: xuất ra file (Word, PDF) rồi tải lên làm nguồn, hoặc dùng link
            GitLab của repo trong dự án.
          </p>
        </>
      ) : draft.status === "applied" ? (
        <Alert tone="success">Bản nháp đã được lưu thành phiên bản {draft.applied_version} của tài liệu.</Alert>
      ) : draft.status === "discarded" ? (
        <Alert tone="info">Bản nháp đã bị bỏ.</Alert>
      ) : (
        <div className="docs-draft-grid">
          <div style={{ minWidth: 0 }}>
            {newer && (
              <Alert tone="warning">
                Tài liệu đã có phiên bản {current.data?.version} sau khi bản nháp được viết (từ phiên bản {draft.base_version}). Bấm <strong>Viết lại</strong>{" "}
                để AI bổ sung trên bản mới nhất, tránh ghi đè thay đổi đó.
              </Alert>
            )}
            {(draft.result?.warnings ?? []).map((w) => (
              <Alert key={w} tone="warning">
                {w}
              </Alert>
            ))}
            <div className="docs-draft-fields">
              <label className="adm-field">
                <span className="small muted">Tiêu đề</span>
                <input value={title ?? (draft.mode === "new" ? (draft.title ?? "") : (current.data?.title ?? draft.document_title ?? ""))} onChange={(e) => setTitle(e.target.value)} maxLength={200} disabled={busy} />
              </label>
              {draft.mode === "new" && (
                <label className="adm-field">
                  <span className="small muted">Nhóm</span>
                  <select value={category ?? draft.category} onChange={(e) => setCategory(e.target.value as DocCategory)} disabled={busy}>
                    {CATEGORY_ORDER.map((c) => (
                      <option key={c} value={c}>
                        {CATEGORY_LABEL[c]}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="adm-field docs-draft-summary">
                <span className="small muted">Mô tả ngắn</span>
                <input
                  value={summary ?? (draft.mode === "new" ? (draft.summary ?? "") : (current.data?.summary ?? draft.summary ?? ""))}
                  onChange={(e) => setSummary(e.target.value)}
                  maxLength={300}
                  disabled={busy}
                />
              </label>
            </div>
            <div className="docs-draft-tabs">
              <Segmented<"preview" | "diff" | "edit">
                value={view}
                onChange={setView}
                label="Cách xem bản nháp"
                options={[
                  { value: "preview", label: "Xem trước" },
                  ...(draft.mode !== "new" ? [{ value: "diff" as const, label: "So sánh với bản hiện tại" }] : []),
                  { value: "edit", label: body !== null && body !== draft.body ? "Sửa Markdown •" : "Sửa Markdown" },
                ]}
              />
              {left > 0 && (
                <Pill tone="warn" title="Chỗ AI không tìm thấy thông tin trong nguồn">
                  {left} chỗ cần bổ sung
                </Pill>
              )}
            </div>
            {view === "edit" ? (
              <textarea className="docs-draft-editor" value={text} onChange={(e) => setBody(e.target.value)} spellCheck={false} disabled={busy} aria-label="Nội dung Markdown của bản nháp" />
            ) : view === "diff" ? (
              current.data ? (
                <TextDiff before={current.data.body} after={text} label="Thay đổi của bản nháp" against={`phiên bản ${current.data.version} (hiện tại)`} />
              ) : current.error ? (
                <Alert tone="error">Không tải được tài liệu hiện tại: {current.error.message}</Alert>
              ) : (
                <Loading rows={6} />
              )
            ) : (
              <div className="docs-draft-preview">
                <DocMarkdown text={text} />
              </div>
            )}
            {error && <Alert tone="error">{error}</Alert>}
          </div>
          <aside className="docs-draft-side">
            <Bullets title="AI đã làm" items={draft.result?.changes ?? []} />
            <Bullets title="Mâu thuẫn cần bạn quyết" items={draft.result?.conflicts ?? []} tone="warn" />
            <Bullets title="Nguồn chưa có (cần bổ sung)" items={draft.result?.gaps ?? []} />
            <h4>Nguồn</h4>
            <SourceList draft={draft} />
            {draft.instruction && <p className="small muted">Yêu cầu thêm: {draft.instruction}</p>}
            <p className="small muted">
              {draft.created_by?.name ?? "—"} · {draft.created_at ? relative(draft.created_at) : "—"}
              {draft.cost_usd ? ` · ~$${draft.cost_usd.toFixed(2)}` : ""}
            </p>
            <p className="small muted">AI có thể sai: đối chiếu với nguồn (số [n] trong bài) trước khi lưu.</p>
          </aside>
        </div>
      )}
    </Modal>
  );
}

const STATUS_PILL: Record<DocDraft["status"], { label: string; tone?: "good" | "bad" | "warn" | "info" | "primary" }> = {
  queued: { label: "Đang chờ", tone: "info" },
  fetching: { label: "Đọc nguồn", tone: "info" },
  writing: { label: "AI đang viết", tone: "info" },
  ready: { label: "Chờ duyệt", tone: "primary" },
  failed: { label: "Lỗi", tone: "bad" },
  applied: { label: "Đã lưu", tone: "good" },
  discarded: { label: "Đã bỏ" },
};

export function DraftPill({ status }: { status: DocDraft["status"] }) {
  const s = STATUS_PILL[status];
  return <Pill tone={s.tone}>{s.label}</Pill>;
}

/** The AI drafts of a dự án (its editors only): open one to review it. */
export function DraftsCard({ drafts, onOpen, onNew }: { drafts?: DraftList; onOpen: (id: number) => void; onNew: () => void }) {
  const open = (drafts?.drafts ?? []).filter((d) => d.status !== "applied");
  const done = (drafts?.drafts ?? []).filter((d) => d.status === "applied").slice(0, 3);
  return (
    <div className="card docs-doc">
      <div className="docs-card-head">
        <h3 style={{ margin: 0 }}>
          <Icon name="sparkle" size={16} /> Tổng hợp bằng AI
        </h3>
        <button type="button" className="sm" onClick={onNew}>
          <Icon name="plus" size={14} /> Thêm nguồn
        </button>
      </div>
      {!drafts ? (
        <Loading rows={2} />
      ) : !open.length && !done.length ? (
        <p className="muted small" style={{ marginBottom: 0 }}>
          Dán link (wiki, bài viết, README hay file trên GitLab), tải file lên (Word, PDF, Markdown…) hoặc dán văn bản: AI đọc và viết thành tài liệu theo chuẩn của
          dự án, hoặc bổ sung vào tài liệu đang có. Bạn duyệt trước khi lưu.
        </p>
      ) : (
        <ul className="docs-drafts">
          {[...open, ...done].map((d) => (
            <li key={d.id}>
              <button type="button" className="docs-draft-row" onClick={() => onOpen(d.id)}>
                <span className="docs-draft-row-main">
                  <strong>{d.mode === "new" ? (d.title ?? `Tài liệu mới · ${CATEGORY_LABEL[d.category]}`) : (d.document_title ?? "Tài liệu")}</strong>
                  <span className="small muted">
                    {MODE_LABEL[d.mode]} · {sourceCount(d)} · {d.created_by?.name ?? "—"} · {d.created_at ? relative(d.created_at) : "—"}
                  </span>
                </span>
                <DraftPill status={d.status} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
