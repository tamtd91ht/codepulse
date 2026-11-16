"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ClipboardEvent, type DragEvent, type TextareaHTMLAttributes } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { api } from "@/lib/api";

import { errorText, fileSize } from "./taskParts";

/**
 * Files of a task (docs/specs/31 §17): uploaded from disk, dropped, or pasted from the
 * clipboard (a screenshot). Images show in place and can sit inside a description or comment.
 */

export const MAX_FILE_MB = 20;

export interface UploadedFile {
  id: number;
  name: string;
  size: number;
  content_type: string | null;
  url: string;
}

/** Images the server shows in place (app/api/routers/tasks.py INLINE_IMAGES). */
const INLINE = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"]);

export function isInlineImage(type: string | null | undefined): boolean {
  return !!type && INLINE.has(type);
}

export function inlineUrl(id: number): string {
  return `/api/v1/tasks/attachments/${id}?inline=1`;
}

function stamp(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

/** A pasted screenshot comes as "image.png" (or nameless): give it a name worth keeping. */
export function fileName(file: File, i = 0): string {
  if (file.name && !/^image\.\w+$/i.test(file.name)) return file.name;
  const ext = (file.type.split("/")[1] || "png").replace("jpeg", "jpg").replace(/[^a-z0-9]/gi, "");
  return `anh-chup-${stamp()}${i ? `-${i + 1}` : ""}.${ext}`;
}

/** The files in a paste (a copied screenshot or files copied in the file manager). */
export function clipboardFiles(e: ClipboardEvent | globalThis.ClipboardEvent): File[] {
  const items = e.clipboardData?.items;
  if (!items) return [];
  const out: File[] = [];
  for (const item of items) {
    if (item.kind !== "file") continue;
    const f = item.getAsFile();
    if (f) out.push(f);
  }
  return out;
}

export async function uploadTaskFile(taskKey: string, file: File, i = 0): Promise<UploadedFile> {
  if (file.size > MAX_FILE_MB * 1024 * 1024) throw new Error(`${file.name || "file"} lớn hơn ${MAX_FILE_MB} MB`);
  const name = fileName(file, i);
  return api.postRaw<UploadedFile>(
    `/tasks/${encodeURIComponent(taskKey)}/attachments?filename=${encodeURIComponent(name)}`,
    file,
    file.type || "application/octet-stream",
  );
}

/** The Markdown that shows an uploaded file: the image itself, or a link to the file. */
export function fileMarkdown(f: UploadedFile): string {
  const label = f.name.replace(/[[\]]/g, "");
  return isInlineImage(f.content_type) ? `![${label}](${inlineUrl(f.id)})` : `[${label}](${f.url})`;
}

type AreaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> & {
  value: string;
  onChange: (next: string | ((prev: string) => string)) => void;
  taskKey: string;
  /** tasks.attach: without it the textarea takes text only. */
  canAttach?: boolean;
  /** Called after files were attached (the attachment list refreshes). */
  onUploaded?: () => void;
  /** How many uploads are still running (the form waits for them before sending). */
  onPendingChange?: (pending: number) => void;
};

let markSeq = 0;

/**
 * A Markdown textarea that takes files: paste a screenshot (Ctrl+V), drop files, or pick them
 * with the paperclip. Each is uploaded to the task and its Markdown goes where the caret is.
 */
export const FileTextarea = forwardRef<HTMLTextAreaElement, AreaProps>(function FileTextarea({ value, onChange, taskKey, canAttach = true, onUploaded, onPendingChange, className, ...rest }, ref) {
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState(0);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useImperativeHandle(ref, () => area.current as HTMLTextAreaElement);
  const pendingCb = useRef(onPendingChange);
  pendingCb.current = onPendingChange;
  useEffect(() => pendingCb.current?.(pending), [pending]);

  async function add(files: File[]) {
    if (!files.length || !canAttach) return;
    setError(null);
    const el = area.current;
    const at = el ? el.selectionEnd : value.length;
    const marks = files.map((f, i) => `[Đang tải ${fileName(f, i)} #${++markSeq}…]`);
    onChange((text) => {
      const pos = Math.min(at, text.length);
      const before = text.slice(0, pos);
      const lead = !before || before.endsWith("\n") ? "" : "\n";
      return `${before}${lead}${marks.join("\n")}\n${text.slice(pos)}`;
    });
    setPending((n) => n + files.length);
    const failed: string[] = [];
    await Promise.all(
      files.map(async (f, i) => {
        try {
          const got = await uploadTaskFile(taskKey, f, i);
          onChange((text) => text.replace(marks[i], fileMarkdown(got)));
        } catch (e) {
          failed.push(`${f.name || "ảnh"}: ${errorText(e)}`);
          onChange((text) => text.replace(`${marks[i]}\n`, "").replace(marks[i], ""));
        } finally {
          setPending((n) => n - 1);
        }
      }),
    );
    if (failed.length) setError(failed.join("; "));
    onUploaded?.();
  }

  return (
    <div className={`tk-file-area${over ? " is-over" : ""}`}>
      <textarea
        {...rest}
        ref={area}
        className={className}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onPaste={(e) => {
          const files = canAttach ? clipboardFiles(e) : [];
          if (!files.length) return;
          e.preventDefault();
          void add(files);
        }}
        onDragOver={(e: DragEvent) => {
          if (!canAttach || ![...e.dataTransfer.types].includes("Files")) return;
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e: DragEvent) => {
          setOver(false);
          if (!canAttach || !e.dataTransfer.files.length) return;
          e.preventDefault();
          void add([...e.dataTransfer.files]);
        }}
      />
      {canAttach && (
        <div className="tk-file-hint small muted">
        <button type="button" className="link sm" onClick={() => picker.current?.click()} disabled={pending > 0}>
          <Icon name="upload" size={13} /> Đính file
        </button>
        <span>{pending ? `Đang tải ${pending} file…` : "hoặc dán ảnh (Ctrl+V), kéo thả file vào đây"}</span>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            void add([...(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        </div>
      )}
      {error && (
        <p className="small tk-file-error" role="alert">
          Lỗi: {error}
        </p>
      )}
    </div>
  );
});

/** A full-size look at an image attachment. */
export function ImageViewer({ file, onClose }: { file: { id: number; name: string; size: number; url: string }; onClose: () => void }) {
  return (
    <Modal
      title={file.name}
      onClose={onClose}
      width={1100}
      footer={
        <>
          <span className="small muted">{fileSize(file.size)}</span>
          <span className="spacer" />
          <a className="button ghost sm" href={inlineUrl(file.id)} target="_blank" rel="noopener noreferrer">
            <Icon name="external" size={14} /> Mở tab mới
          </a>
          <a className="button sm" href={file.url} download={file.name}>
            <Icon name="download" size={14} /> Tải về
          </a>
        </>
      }
    >
      <div className="tk-image-view">
        <img src={inlineUrl(file.id)} alt={file.name} />
      </div>
    </Modal>
  );
}
