"use client";

import { useEffect, useState } from "react";

import { IconButton } from "@/components/icons";

/** Shown until the page knows its own address (server render, first paint). */
const PLACEHOLDER_URL = "https://<địa chỉ CodePulse>/mcp";

/**
 * The MCP endpoint (docs/specs/26 §1): the dashboard's own origin + /mcp. The dashboard proxies
 * /mcp to the API like /api (next.config.ts), so the address people open in the browser is the
 * one Claude Code connects to.
 */
export function useMcpUrl(): string {
  const [url, setUrl] = useState(PLACEHOLDER_URL);
  useEffect(() => {
    setUrl(`${window.location.origin}/mcp`);
  }, []);
  return url;
}

/** The one-line `claude mcp add` that registers CodePulse in Claude Code. */
export function mcpAddCommand(url: string, token: string): string {
  return `claude mcp add --transport http codepulse ${url} --header "Authorization: Bearer ${token}"`;
}

/** A `.mcp.json` that reads the token from CODEPULSE_TOKEN, so the file can be committed. */
export function mcpJson(url: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        codepulse: {
          type: "http",
          url,
          headers: { Authorization: "Bearer ${CODEPULSE_TOKEN}" },
        },
      },
    },
    null,
    2,
  );
}

/** A monospace block with a copy button; says whether the copy worked (clipboard may be blocked). */
export function CopyBlock({ text, label, secret }: { text: string; label: string; secret?: boolean }) {
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(null), 2500);
    return () => window.clearTimeout(t);
  }, [copied]);
  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
  }
  return (
    <div className={`copy-block${secret ? " secret" : ""}`}>
      <pre tabIndex={0} aria-label={label}>
        <code>{text}</code>
      </pre>
      <IconButton icon={copied === "ok" ? "check" : "copy"} label={copied === "ok" ? "Đã chép" : `Chép ${label.toLowerCase()}`} onClick={() => void copy()} />
      <span className="visually-hidden" aria-live="polite">
        {copied === "ok" ? "Đã chép." : copied === "fail" ? "Không chép được, hãy chọn và sao chép thủ công." : ""}
      </span>
      {copied === "fail" && <span className="copy-fail small error">Không chép được, hãy chọn và sao chép thủ công.</span>}
    </div>
  );
}

/** The guide's copyable samples, with this server's real address. */
export function McpGuideSamples() {
  const url = useMcpUrl();
  return (
    <>
      <p>Cách nhanh, mỗi máy một lần (thay <code>cpat_…</code> bằng token vừa tạo):</p>
      <CopyBlock label="Lệnh claude mcp add" text={mcpAddCommand(url, "cpat_…")} />
      <p>
        Hoặc dùng chung cho cả repo bằng <code>.mcp.json</code> ở thư mục gốc. Token đọc từ biến môi trường{" "}
        <code>CODEPULSE_TOKEN</code> (Claude Code thay <code>{"${VAR}"}</code> trong header), nên file commit lên repo không
        chứa token:
      </p>
      <CopyBlock label="Nội dung .mcp.json" text={mcpJson(url)} />
      <p className="muted small">
        Đặt <code>export CODEPULSE_TOKEN=cpat_…</code> trong shell profile (hoặc biến môi trường người dùng trên Windows),
        mở lại terminal rồi chạy <code>claude</code>; gõ <code>/mcp</code> để xem kết nối.
      </p>
    </>
  );
}
