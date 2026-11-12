"use client";

import Link from "next/link";

import { Icon, type IconName } from "@/components/icons";
import { can } from "@/lib/api";
import type { Me } from "@/lib/types";

type Tab = { href: string; label: string; icon: IconName; show: (me: Me) => boolean };

/** The Stetho menu's two pages (docs/specs/28): what it knows, and what people asked it. */
const TABS: Tab[] = [
  // Tri thức: whoever may teach Stetho (admins by default).
  { href: "/stetho", label: "Tri thức", icon: "stethoscope", show: (me) => can(me, "assistant.train") },
  // Lịch sử (§9): admins only (ADMIN_EMAILS).
  { href: "/stetho/history", label: "Lịch sử", icon: "inbox", show: (me) => me.is_admin === true },
];

/** The Stetho page one may open first (the side menu's link), or null when none. */
export function stethoHome(me: Me): string | null {
  return TABS.find((t) => t.show(me))?.href ?? null;
}

/** Page-level tabs between Tri thức and Lịch sử; a tab one may not open is not shown. */
export function StethoTabs({ me, current }: { me: Me | undefined; current: string }) {
  const tabs = me ? TABS.filter((t) => t.show(me)) : TABS.filter((t) => t.href === current);
  return (
    <nav className="tabs page-tabs" aria-label="Stetho">
      {tabs.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          className={t.href === current ? "active" : undefined}
          aria-current={t.href === current ? "page" : undefined}
        >
          <Icon name={t.icon} size={16} />
          {t.label}
        </Link>
      ))}
    </nav>
  );
}
