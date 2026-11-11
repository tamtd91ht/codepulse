"use client";

import { useRef, useState } from "react";

import { Icon } from "@/components/icons";

export type UploadState = "waiting" | "uploading" | "done" | "error";
export type UploadItem = { key: string; file: File; state: UploadState; note?: string };

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Why a picked file is refused before it is sent (kind, size), or null. */
export function refuse(file: File, kinds: string[], maxMb: number): string | null {
  const lower = file.name.toLowerCase();
  if (!kinds.some((k) => lower.endsWith(k))) return `chỉ nhận ${kinds.join(", ")}`;
  if (file.size > maxMb * 1024 * 1024) return `lớn hơn ${maxMb} MB`;
  if (file.size === 0) return "file rỗng";
  return null;
}

/**
 * A drop zone for several files: drag them in or click to pick; keyboard reachable (Enter /
 * Space open the picker). It only hands the files over: the caller uploads them.
 */
export function UploadDrop({
  kinds,
  maxMb,
  disabled,
  onFiles,
  title = "Kéo thả file vào đây hoặc bấm để chọn",
  hint,
  compact,
}: {
  kinds: string[];
  maxMb: number;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
  title?: string;
  hint?: string;
  compact?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const open = () => !disabled && input.current?.click();
  return (
    <div
      className={`dropzone${over ? " over" : ""}${disabled ? " disabled" : ""}${compact ? " compact" : ""}`}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      aria-label={`${title}. Nhận ${kinds.join(", ")}, tối đa ${maxMb} MB mỗi file`}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled && e.dataTransfer.files.length) onFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept={kinds.join(",")}
        onChange={(e) => {
          if (e.target.files?.length) onFiles(Array.from(e.target.files));
          e.target.value = "";
        }}
      />
      <span className="dropzone-icon" aria-hidden="true">
        <Icon name="upload" size={compact ? 16 : 22} />
      </span>
      <span className="dropzone-text">
        <strong>{title}</strong>
        <span className="muted small">
          {hint ?? `${kinds.join(", ")} · tối đa ${maxMb} MB mỗi file · chọn được nhiều file cùng lúc`}
        </span>
      </span>
    </div>
  );
}

/** Per-file progress of a batch upload. */
export function UploadList({ items, onClear }: { items: UploadItem[]; onClear?: () => void }) {
  if (!items.length) return null;
  const busy = items.some((i) => i.state === "waiting" || i.state === "uploading");
  const failed = items.filter((i) => i.state === "error").length;
  return (
    <div className="upload-list" aria-live="polite">
      <div className="upload-list-head small">
        <span>
          {busy
            ? `Đang tải ${items.filter((i) => i.state === "done").length + 1}/${items.length}…`
            : failed
              ? `Xong, ${failed} file lỗi`
              : `Đã tải ${items.length} file`}
        </span>
        {!busy && onClear && (
          <button type="button" className="link sm" onClick={onClear}>
            Ẩn
          </button>
        )}
      </div>
      <ul>
        {items.map((it) => (
          <li key={it.key} className={`upload-item ${it.state}`}>
            <Icon name={it.state === "done" ? "checkCircle" : it.state === "error" ? "alertCircle" : "file"} size={14} />
            <span className="upload-name" title={it.file.name}>
              {it.file.name}
            </span>
            <span className="muted small">{fileSize(it.file.size)}</span>
            <span className={`small upload-note${it.state === "error" ? " error" : ""}`}>
              {it.state === "uploading" ? "đang tải…" : it.state === "waiting" ? "chờ" : it.note ?? ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Uploads files one after another, updating each item's state; returns how many succeeded. */
export async function uploadAll(
  files: File[],
  kinds: string[],
  maxMb: number,
  setItems: (fn: (items: UploadItem[]) => UploadItem[]) => void,
  send: (file: File) => Promise<string | undefined>,
): Promise<number> {
  const stamp = Date.now();
  const batch: UploadItem[] = files.map((file, i) => {
    const why = refuse(file, kinds, maxMb);
    return { key: `${stamp}-${i}-${file.name}`, file, state: why ? "error" : "waiting", note: why ?? undefined };
  });
  setItems((old) => [...old.filter((o) => o.state !== "done" && o.state !== "error"), ...batch]);
  let ok = 0;
  for (const item of batch) {
    if (item.state === "error") continue;
    setItems((old) => old.map((o) => (o.key === item.key ? { ...o, state: "uploading" } : o)));
    try {
      const note = await send(item.file);
      ok++;
      setItems((old) => old.map((o) => (o.key === item.key ? { ...o, state: "done", note } : o)));
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setItems((old) => old.map((o) => (o.key === item.key ? { ...o, state: "error", note: message } : o)));
    }
  }
  return ok;
}
