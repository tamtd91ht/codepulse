"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { DocMarkdown } from "@/components/DocMarkdown";
import { IconButton } from "@/components/icons";
import { Alert, Loading, Message, Pill } from "@/components/ui";
import { UploadDrop, type UploadItem, UploadList, uploadAll } from "@/components/UploadDrop";
import { ApiError, api, useApi } from "@/lib/api";
import { relative } from "@/lib/format";
import type { BusinessProject } from "@/lib/types";

type RuleFile = {
  id: number;
  name: string;
  chars: number;
  enabled: boolean;
  position: number;
  clipped: boolean;
  updated_at: string | null;
  created_at: string | null;
};
type RuleFiles = {
  files: RuleFile[];
  typed_chars: number;
  files_chars: number;
  budget: number;
  file_max_chars: number;
  max_files: number;
  max_mb: number;
  kinds: string[];
};

const n = (v: number) => v.toLocaleString("vi-VN");
const errorText = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/**
 * Review rules imported as files (docs/specs/17 §Tệp quy tắc): drop several .md/.txt/.doc/.docx
 * files, each converted to Markdown and used in every review of the dự án next to the typed
 * rules. Same name again = a new version of that file. Switch a file off, reorder, preview, remove.
 */
export function RuleFilesPanel({ project, typedChars }: { project: BusinessProject; typedChars: number }) {
  const path = `/business-projects/${project.id}/rule-files`;
  const data = useApi<RuleFiles>(path);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [preview, setPreview] = useState<RuleFile | null>(null);
  const [removing, setRemoving] = useState<RuleFile | null>(null);
  const d = data.data;
  const kinds = d?.kinds ?? [".md", ".markdown", ".txt", ".doc", ".docx"];
  const maxMb = d?.max_mb ?? 10;

  async function upload(files: File[]) {
    setBusy(true);
    setMessage(null);
    const ok = await uploadAll(files, kinds, maxMb, setItems, async (file) => {
      const res = await api.postRaw<{ file: RuleFile; replaced: boolean }>(`${path}?filename=${encodeURIComponent(file.name)}`, file);
      return `${res.replaced ? "đã thay bản cũ" : "đã thêm"} · ${n(res.file.chars)} ký tự`;
    });
    await data.mutate();
    setBusy(false);
    if (ok) setMessage(`Đã nhập ${ok} file quy tắc. Review tiếp theo của dự án sẽ đối chiếu với chúng.`);
  }

  async function patch(f: RuleFile, body: Partial<Pick<RuleFile, "enabled" | "position">>) {
    setBusy(true);
    try {
      await api.patch(`${path}/${f.id}`, body);
      await data.mutate();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function move(index: number, to: number) {
    if (!d || to < 0 || to >= d.files.length) return;
    const order = [...d.files];
    const [f] = order.splice(index, 1);
    order.splice(to, 0, f);
    setBusy(true);
    try {
      // Positions rewritten 0..n-1 for the files whose place changed.
      await Promise.all(order.map((x, i) => (x.position !== i ? api.patch(`${path}/${x.id}`, { position: i }) : null)));
      await data.mutate();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function remove(f: RuleFile) {
    setBusy(true);
    try {
      await api.delete(`${path}/${f.id}`);
      await data.mutate();
      setMessage(`Đã xoá ${f.name}.`);
      setRemoving(null);
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const used = typedChars + (d?.files_chars ?? 0);
  const budget = d?.budget ?? 60000;
  const share = Math.min(100, Math.round((used / budget) * 100));
  const tone = used > budget ? "bad" : used > budget * 0.8 ? "warn" : "good";

  return (
    <section className="rule-files" aria-label="Tệp quy tắc">
      <div className="drawer-title-row" style={{ margin: "16px 0 8px" }}>
        <div>
          <h4 style={{ margin: 0 }}>
            Tệp quy tắc{" "}
            {d && (
              <span className="muted small">
                ({d.files.length}/{d.max_files})
              </span>
            )}
          </h4>
          <p className="muted small" style={{ margin: "4px 0 0" }}>
            Nhập nhiều file quy tắc có sẵn (.md, .txt, .doc, .docx). Mỗi file được chuyển thành văn bản và đưa vào mọi review
            của dự án như một nguồn riêng, finding vi phạm ghi tên file. Tải lại file cùng tên để cập nhật.
          </p>
        </div>
      </div>

      <UploadDrop kinds={kinds} maxMb={maxMb} disabled={busy || !d} onFiles={(f) => void upload(f)} compact />
      <UploadList items={items} onClear={() => setItems([])} />
      {message && <Message text={message} onClose={() => setMessage(null)} />}

      <div className="rule-budget" style={{ margin: "10px 0" }}>
        <div className="small" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
          <span>Dung lượng quy tắc đưa vào mỗi review</span>
          <span className="muted">
            {n(used)} / {n(budget)} ký tự
          </span>
        </div>
        <span className={`crit-score-bar ${tone}`} role="meter" aria-label="Dung lượng quy tắc" aria-valuemin={0} aria-valuemax={budget} aria-valuenow={used}>
          <span style={{ width: `${share}%` }} />
        </span>
        {used > budget && (
          <p className="small error" style={{ margin: "4px 0 0" }}>
            Vượt ngân sách: phần cuối (tệp xếp sau, quy tắc repo) bị cắt khỏi prompt. Tắt bớt tệp ít quan trọng hoặc rút gọn.
          </p>
        )}
      </div>

      {data.error ? (
        <Alert tone="error">
          Không tải được danh sách tệp: {data.error.message}{" "}
          <button type="button" className="link sm" onClick={() => void data.mutate()}>
            Thử lại
          </button>
        </Alert>
      ) : !d ? (
        <Loading rows={2} />
      ) : d.files.length === 0 ? (
        <p className="muted small">Chưa có tệp quy tắc nào. Kéo các file quy tắc của team vào ô trên.</p>
      ) : (
        <div className="table-wrap">
          <table className="rule-file-table">
            <thead>
              <tr>
                <th>Tệp</th>
                <th>Kích thước</th>
                <th>Cập nhật</th>
                <th>Dùng khi review</th>
                <th aria-label="Thao tác" />
              </tr>
            </thead>
            <tbody>
              {d.files.map((f, i) => (
                <tr key={f.id} className={f.enabled ? undefined : "muted"}>
                  <td>
                    <button type="button" className="link" onClick={() => setPreview(f)}>
                      {f.name}
                    </button>
                  </td>
                  <td className="small">
                    {n(f.chars)} ký tự{" "}
                    {f.clipped && (
                      <Pill tone="warn" title={`Chỉ ${n(d.file_max_chars)} ký tự đầu được dùng`}>
                        bị cắt
                      </Pill>
                    )}
                  </td>
                  <td className="small muted">{f.updated_at ? relative(f.updated_at) : "—"}</td>
                  <td>
                    <label className="inline-check">
                      <input type="checkbox" checked={f.enabled} disabled={busy} onChange={(e) => void patch(f, { enabled: e.target.checked })} />
                      <span className="small">{f.enabled ? "Đang dùng" : "Tắt"}</span>
                    </label>
                  </td>
                  <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                    <IconButton icon="chevronUp" label={`Đưa ${f.name} lên`} disabled={busy || i === 0} onClick={() => void move(i, i - 1)} size={14} />
                    <IconButton
                      icon="chevronDown"
                      label={`Đưa ${f.name} xuống`}
                      disabled={busy || i === d.files.length - 1}
                      onClick={() => void move(i, i + 1)}
                      size={14}
                    />
                    <IconButton icon="remove" tone="danger" label={`Xoá ${f.name}`} disabled={busy} onClick={() => setRemoving(f)} size={14} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {preview && <RulePreview path={`${path}/${preview.id}`} name={preview.name} onClose={() => setPreview(null)} />}
      {removing && (
        <Modal
          title={`Xoá tệp quy tắc ${removing.name}?`}
          onClose={() => setRemoving(null)}
          busy={busy}
          width={440}
          footer={
            <>
              <button type="button" className="ghost" onClick={() => setRemoving(null)} disabled={busy}>
                Huỷ
              </button>
              <button type="button" className="danger" onClick={() => void remove(removing)} disabled={busy}>
                Xoá tệp
              </button>
            </>
          }
        >
          <p>Review sau sẽ không còn đối chiếu với tệp này. Review đã chạy vẫn giữ nguyên căn cứ cũ.</p>
        </Modal>
      )}
    </section>
  );
}

function RulePreview({ path, name, onClose }: { path: string; name: string; onClose: () => void }) {
  const detail = useApi<RuleFile & { body: string }>(path);
  return (
    <Modal title={name} onClose={onClose} width={760}>
      {detail.error ? (
        <Alert tone="error">Lỗi: {detail.error.message}</Alert>
      ) : !detail.data ? (
        <Loading rows={6} />
      ) : (
        <>
          <p className="muted small" style={{ marginTop: 0 }}>
            Nội dung sau khi chuyển đổi, đúng như model đọc khi review ({n(detail.data.chars)} ký tự).
          </p>
          <div className="rule-preview">
            <DocMarkdown text={detail.data.body} offset={2} />
          </div>
        </>
      )}
    </Modal>
  );
}
