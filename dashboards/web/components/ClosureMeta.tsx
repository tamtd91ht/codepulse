"use client";

import { Expander, Pill } from "@/components/ui";
import type { ClosureTest, FindingClosure } from "@/lib/types";

const TEST: Record<ClosureTest["result"], { label: string; tone?: "good" | "bad" }> = {
  passed: { label: "Đạt", tone: "good" },
  failed: { label: "Lỗi", tone: "bad" },
  not_run: { label: "Chưa chạy" },
};

/** "Claude Code (MCP)" next to a request sent through the MCP server (docs/specs/26 §8). */
export function ViaMcpBadge({ closure }: { closure: Pick<FindingClosure, "via" | "meta"> }) {
  if (closure.via !== "mcp") return null;
  const token = closure.meta?.token_name;
  return (
    <Pill tone="primary" icon="code" title={token ? `Gửi qua MCP bằng token “${token}”` : "Gửi qua MCP"}>
      Claude Code (MCP)
    </Pill>
  );
}

/**
 * What an MCP close request carried: the token and client, the files changed (folded) and the
 * tests it ran, each with its result. Nothing for a request from the web form.
 */
export function ClosureMcpMeta({ closure }: { closure: Pick<FindingClosure, "via" | "meta"> }) {
  if (closure.via !== "mcp") return null;
  const meta = closure.meta ?? {};
  const files = meta.files_changed ?? [];
  const tests = meta.tests ?? [];
  const failed = tests.filter((t) => t.result === "failed").length;
  const passed = tests.filter((t) => t.result === "passed").length;
  return (
    <div className="mcp-meta small">
      <div className="mcp-meta-head">
        <ViaMcpBadge closure={closure} />
        <span className="muted">
          {meta.token_name ? <>token “{meta.token_name}”</> : "token đã xoá"}
          {meta.client ? <> · {meta.client}</> : null}
        </span>
      </div>
      {files.length > 0 && (
        <Expander summary={`${files.length} file đã sửa`}>
          <ul className="mcp-files">
            {files.map((f) => (
              <li key={f}>
                <code>{f}</code>
              </li>
            ))}
          </ul>
        </Expander>
      )}
      {tests.length > 0 ? (
        <div className="mcp-tests">
          <span className="muted">
            Test: {passed} đạt{failed ? `, ${failed} lỗi` : ""}
            {tests.length - passed - failed ? `, ${tests.length - passed - failed} chưa chạy` : ""}
          </span>
          <ul>
            {tests.map((t, i) => {
              const r = TEST[t.result] ?? { label: t.result };
              return (
                <li key={`${t.command}-${i}`}>
                  <Pill tone={r.tone} icon={t.result === "passed" ? "check" : t.result === "failed" ? "close" : "minus"}>
                    {r.label}
                  </Pill>
                  <code>{t.command}</code>
                  {t.summary && <span className="muted">{t.summary}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        files.length > 0 && <span className="muted">Không kèm kết quả test.</span>
      )}
    </div>
  );
}
