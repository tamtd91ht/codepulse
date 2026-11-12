"use client";

import { useState } from "react";

import { Card, Pill } from "@/components/ui";
import type { StaticBasis, StaticFinding } from "@/lib/types";

/*
 * Kiểm tra tĩnh (docs/specs/07, user 2026-11-12): what Gitleaks and Semgrep found on the lines the
 * change adds. Hints for the reviewer and the model, not counted in the score while their false
 * positives are measured; "AI đồng ý" marks one an AI finding of the review also reports.
 */

const TOOL_LABEL: Record<string, string> = { gitleaks: "Gitleaks", semgrep: "Semgrep" };
const SEVERITY_TONE: Record<string, "bad" | "warn" | "info" | undefined> = {
  critical: "bad",
  high: "bad",
  medium: "warn",
  low: "info",
};
const SEVERITY_LABEL: Record<string, string> = { critical: "Nghiêm trọng", high: "Cao", medium: "Trung bình", low: "Thấp", info: "Thông tin" };

function ToolLine({ basis }: { basis?: StaticBasis }) {
  if (!basis) return null;
  const parts = Object.entries(basis.tools).map(([name, t]) => {
    const label = TOOL_LABEL[name] ?? name;
    if (t.ran) return `${label}: ${t.found} vấn đề (${t.seconds}s)`;
    return `${label}: ${t.error ?? "không chạy"}`;
  });
  return (
    <p className="muted small" style={{ margin: "0 0 10px" }}>
      Đã kiểm {basis.files} file thay đổi · {parts.join(" · ")}
    </p>
  );
}

export function StaticFindingsCard({ findings, basis }: { findings?: StaticFinding[]; basis?: StaticBasis }) {
  const [open, setOpen] = useState<number | null>(null);
  const list = findings ?? [];
  if (!list.length && !basis) return null;
  const agreed = list.filter((f) => f.confirmed_by_ai).length;
  return (
    <Card
      title={`Kiểm tra tĩnh${list.length ? ` (${list.length})` : ""}`}
      subtitle="Gitleaks (secret lộ) và Semgrep (lỗi bảo mật, bug) trên các dòng thay đổi thêm vào. Chỉ là gợi ý: chưa tính vào điểm; AI review đã đọc và tự quyết lỗi nào thật."
    >
      <ToolLine basis={basis} />
      {!list.length ? (
        <p className="muted" style={{ margin: 0 }}>
          Không phát hiện vấn đề nào trên các dòng thay đổi.
        </p>
      ) : (
        <>
          {agreed > 0 && (
            <p className="small" style={{ margin: "0 0 8px" }}>
              AI review cũng báo {agreed}/{list.length} vấn đề ở cùng chỗ.
            </p>
          )}
          <ul className="static-list">
            {list.map((f) => (
              <li key={f.id} className={f.tool === "gitleaks" ? "secret" : undefined}>
                <button type="button" className="static-row" onClick={() => setOpen(open === f.id ? null : f.id)} aria-expanded={open === f.id}>
                  <Pill tone={SEVERITY_TONE[f.severity]}>{SEVERITY_LABEL[f.severity] ?? f.severity}</Pill>
                  <span className="static-title">{f.title}</span>
                  <code className="small">
                    {f.file_path}:{f.line_start}
                    {f.line_end > f.line_start ? `-${f.line_end}` : ""}
                  </code>
                  <Pill>{TOOL_LABEL[f.tool] ?? f.tool}</Pill>
                  {f.confirmed_by_ai && <Pill tone="good">AI đồng ý</Pill>}
                </button>
                {open === f.id && (
                  <div className="static-detail">
                    {f.message && <p>{f.message}</p>}
                    {f.code_excerpt && <pre className="static-code">{f.code_excerpt}</pre>}
                    <p className="muted small" style={{ margin: 0 }}>
                      Luật: <code>{f.rule_id}</code>
                    </p>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
