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

/** This server's MCP address, copyable (the integration document). */
export function McpUrl() {
  const url = useMcpUrl();
  return <CopyBlock label="Địa chỉ MCP" text={url} />;
}

/** One `claude mcp add` per scope (local / user), with this server's real address. */
export function McpAddScoped({ scope }: { scope: "local" | "user" }) {
  const url = useMcpUrl();
  const text = `claude mcp add --transport http --scope ${scope} codepulse ${url} --header "Authorization: Bearer cpat_…"`;
  return <CopyBlock label={`Lệnh claude mcp add (scope ${scope})`} text={text} />;
}

/** `.mcp.json` for a repo, the token read from CODEPULSE_TOKEN. */
export function McpJsonSample() {
  const url = useMcpUrl();
  return <CopyBlock label="Nội dung .mcp.json" text={mcpJson(url)} />;
}

const COMMAND_FILE = "/downloads/fix-findings.md";

/** The /fix-findings slash command, read from the file people download (one source). */
export function FixFindingsFile() {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    fetch(COMMAND_FILE)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => live && setText(t))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [attempt]);
  return (
    <>
      <p>
        <a href={COMMAND_FILE} download="fix-findings.md">
          Tải fix-findings.md
        </a>{" "}
        rồi lưu vào <code>.claude/commands/fix-findings.md</code> trong repo (chỉ repo đó) hoặc{" "}
        <code>~/.claude/commands/fix-findings.md</code> (mọi repo trên máy bạn).
      </p>
      {failed ? (
        <p className="error small" role="alert">
          Không tải được nội dung lệnh.{" "}
          <button type="button" className="link sm" onClick={() => setAttempt((n) => n + 1)}>
            Thử lại
          </button>
        </p>
      ) : text === null ? (
        <div className="skeleton" style={{ height: 160 }} role="status" aria-label="Đang tải" />
      ) : (
        <div className="copy-tall">
          <CopyBlock label="Nội dung fix-findings.md" text={text} />
        </div>
      )}
    </>
  );
}
