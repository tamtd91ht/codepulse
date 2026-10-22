"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { api, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

export function Nav() {
  const pathname = usePathname();
  const { data: me } = useApi<Me>(pathname === "/login" ? null : "/auth/me");
  const isLead = !!me && (me.is_admin || me.lead_teams.length > 0);

  const link = (href: string, label: string) => (
    <Link href={href} className={pathname.startsWith(href) ? "active" : undefined}>
      {label}
    </Link>
  );

  return (
    <header className="nav">
      <Link href="/" className="brand">
        CodePulse
      </Link>
      {me && (
        <>
          {link("/teams", "Team")}
          {link(`/members/${me.id}`, "Của tôi")}
          {link("/critical", "Critical Board")}
          {link("/disputes", isLead ? "Duyệt khiếu nại" : "Khiếu nại")}
          {me.is_admin && link("/admin", "Admin")}
        </>
      )}
      <span className="spacer" />
      {me && (
        <>
          <span className="muted">{me.full_name ?? me.email}</span>
          <button
            onClick={async () => {
              await api.post("/auth/logout");
              window.location.href = "/login";
            }}
          >
            Đăng xuất
          </button>
        </>
      )}
    </header>
  );
}
