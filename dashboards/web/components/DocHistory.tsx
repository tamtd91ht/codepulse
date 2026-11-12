"use client";

import { useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { fileSize } from "@/components/UploadDrop";
import { Alert, Drawer, Loading, Pill } from "@/components/ui";
import { useApi } from "@/lib/api";
import { type DiffLine, type DocDetail, type DocVersion, KIND_LABEL, SOURCE_LABEL, diffStats, lineDiff } from "@/lib/docs";
import { dateTimeMin, relative } from "@/lib/format";

/** Collapses long runs of unchanged lines, keeping CONTEXT lines around every change. */
function hunks(lines: DiffLine[], context = 3): (DiffLine | { op: "gap"; count: number })[] {
  const keep = new Set<number>();
  lines.forEach((l, i) => {
    if (l.op !== "same") for (let k = Math.max(0, i - context); k <= Math.min(lines.length - 1, i + context); k++) keep.add(k);
  });
  const out: (DiffLine | { op: "gap"; count: number })[] = [];
  let gap = 0;
  lines.forEach((l, i) => {
    if (keep.has(i)) {
      if (gap) out.push({ op: "gap", count: gap });
      gap = 0;
      out.push(l);
    } else gap++;
  });
  if (gap) out.push({ op: "gap", count: gap });
  return out;
}

/** The changed lines between two texts, unchanged runs folded. */
export function TextDiff({ before, after, label, against }: { before: string; after: string; label: string; against: string }) {
  const lines = useMemo(() => lineDiff(before, after), [before, after]);
  const { added, removed } = diffStats(lines);
  if (!added && !removed) return <p className="muted small">Nội dung giống hệt {against}.</p>;
  return (
    <div className="doc-diff" aria-label={label}>
      <div className="small doc-diff-stats">
        <span className="good">+{added}</span> <span className="bad-text">−{removed}</span> dòng so với {against}
      </div>
      <pre>
        {hunks(lines).map((l, i) =>
          l.op === "gap" ? (
            <div key={i} className="doc-diff-gap">
              … {l.count} dòng không đổi
            </div>
          ) : (
            <div key={i} className={`doc-diff-line ${l.op}`}>
              <span aria-hidden="true">{l.op === "add" ? "+" : l.op === "del" ? "−" : " "}</span>
              {l.text || " "}
            </div>
          ),
        )}
      </pre>
    </div>
  );
}

function Diff({ docId, number }: { docId: number; number: number }) {
  const now = useApi<DocVersion>(`/docs/${docId}/versions/${number}`);
  const before = useApi<DocVersion>(number > 1 ? `/docs/${docId}/versions/${number - 1}` : null);
  const lines = useMemo(
    () => (now.data?.body !== undefined && (number === 1 || before.data?.body !== undefined) ? lineDiff(before.data?.body ?? "", now.data.body) : null),
    [now.data, before.data, number],
  );
  if (now.error || before.error) return <Alert tone="error">Lỗi: {(now.error ?? before.error)?.message}</Alert>;
  if (!lines) return <Loading rows={4} />;
  const { added, removed } = diffStats(lines);
  if (!added && !removed) return <p className="muted small">Nội dung giống hệt phiên bản trước.</p>;
  return (
    <div className="doc-diff" aria-label={`Thay đổi của phiên bản ${number}`}>
      <div className="small doc-diff-stats">
        <span className="good">+{added}</span> <span className="bad-text">−{removed}</span> dòng so với phiên bản {number - 1 || "trống"}
      </div>
      <pre>
        {hunks(lines).map((l, i) =>
          l.op === "gap" ? (
            <div key={i} className="doc-diff-gap">
              … {l.count} dòng không đổi
            </div>
          ) : (
            <div key={i} className={`doc-diff-line ${l.op}`}>
              <span aria-hidden="true">{l.op === "add" ? "+" : l.op === "del" ? "−" : " "}</span>
              {l.text || " "}
            </div>
          ),
        )}
      </pre>
    </div>
  );
}

/**
 * Lịch sử phiên bản (docs/specs/29 §4): every version newest first, who saved it, how, its note
 * and file; what changed compared with the one before; view an old version, restore it (as a
 * new version: history is never rewritten), download its original file.
 */
export function DocHistory({
  doc,
  onClose,
  onView,
  onRestore,
  busy,
}: {
  doc: DocDetail;
  onClose: () => void;
  onView: (number: number) => void;
  onRestore: (number: number) => void;
  busy: boolean;
}) {
  const versions = useApi<DocVersion[]>(`/docs/${doc.id}/versions?v=${doc.version}`);
  const [open, setOpen] = useState<number | null>(doc.version > 1 ? doc.version : null);
  return (
    <Drawer title={`Lịch sử: ${doc.title}`} onClose={onClose} width={760}>
      {versions.error ? (
        <Alert tone="error">
          Không tải được lịch sử: {versions.error.message}{" "}
          <button type="button" className="link sm" onClick={() => void versions.mutate()}>
            Thử lại
          </button>
        </Alert>
      ) : !versions.data ? (
        <Loading rows={5} />
      ) : (
        <ol className="doc-versions">
          {versions.data.map((v) => {
            const current = v.number === doc.version;
            return (
              <li key={v.number} className={current ? "current" : undefined}>
                <div className="doc-version-head">
                  <strong>Phiên bản {v.number}</strong>
                  {current && <Pill tone="good">Hiện tại</Pill>}
                  <Pill>{SOURCE_LABEL[v.source]}</Pill>
                  <span className="muted small" title={v.created_at ? dateTimeMin(v.created_at) : undefined}>
                    {v.created_by?.name ?? "—"} · {v.created_at ? relative(v.created_at) : "—"} · {v.chars.toLocaleString("vi-VN")} ký tự
                  </span>
                </div>
                {v.note && <p className="small" style={{ margin: "4px 0" }}>{v.note}</p>}
                {v.file_name && (
                  <p className="small muted" style={{ margin: "4px 0" }}>
                    <Icon name="file" size={12} /> {v.file_name}
                    {v.file_kind ? ` · ${KIND_LABEL[v.file_kind] ?? v.file_kind}` : ""}
                    {v.file_size ? ` · ${fileSize(v.file_size)}` : ""}
                    {v.has_original && (
                      <>
                        {" · "}
                        <a href={`/api/v1/docs/${doc.id}/versions/${v.number}/original`} download>
                          Tải file gốc
                        </a>
                      </>
                    )}
                  </p>
                )}
                <div className="toolbar" style={{ margin: "6px 0 0" }}>
                  <button type="button" className="sm" aria-expanded={open === v.number} onClick={() => setOpen(open === v.number ? null : v.number)}>
                    {open === v.number ? "Ẩn thay đổi" : "Xem thay đổi"}
                  </button>
                  {!current && (
                    <button type="button" className="sm" onClick={() => onView(v.number)}>
                      Xem bản này
                    </button>
                  )}
                  {!current && doc.can_edit && (
                    <button type="button" className="sm" onClick={() => onRestore(v.number)} disabled={busy}>
                      Khôi phục bản này
                    </button>
                  )}
                </div>
                {open === v.number && <Diff docId={doc.id} number={v.number} />}
              </li>
            );
          })}
        </ol>
      )}
    </Drawer>
  );
}
