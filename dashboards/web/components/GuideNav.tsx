"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const PAGES = [
  { href: "/guide", label: "Hướng dẫn sử dụng" },
  { href: "/guide/mcp", label: "Tích hợp MCP" },
];

/** The Guide menu's pages: the user guide and the Claude Code (MCP) integration document. */
export function GuideNav() {
  const pathname = usePathname();
  return (
    <nav className="guide-nav" aria-label="Tài liệu">
      {PAGES.map((p) => {
        const active = pathname === p.href;
        return (
          <Link key={p.href} href={p.href} className={active ? "active" : undefined} aria-current={active ? "page" : undefined}>
            {p.label}
          </Link>
        );
      })}
    </nav>
  );
}
