import type { NextConfig } from "next";

// The dashboard proxies /api (and /mcp) to the FastAPI service, so the session cookie and the GitLab
// OAuth callback live on the dashboard's own origin (set APP_BASE_URL to the dashboard URL).
const apiUrl = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  output: "standalone",
  // Next 16 writes an AGENTS.md into this folder on `next dev`; the project keeps its agent rules in docs/specs/.
  agentRules: false,
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
      // The MCP server for Claude Code (docs/specs/26) lives in the API too; developers point
      // `claude mcp add` at <dashboard>/mcp, so the reverse proxy in front of `web` needs no change.
      { source: "/mcp", destination: `${apiUrl}/mcp` },
      { source: "/mcp/:path*", destination: `${apiUrl}/mcp/:path*` },
    ];
  },
};

export default config;
