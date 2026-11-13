"use client";

import Link from "next/link";

import { Icon, type IconName } from "@/components/icons";
import { useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/** The side menu's name for the five pages below (one menu, tabs inside). */
export const REVIEW_MENU = "Review & Finding";

type Tab = { href: string; label: (lead: boolean) => string; icon: IconName };

/** The review lifecycle, in the order one meets it: results, ask for more, the open
 * critical/high findings, closing them, disputes. */
export const REVIEW_TABS: Tab[] = [
  { href: "/reviews", label: () => "Kết quả review", icon: "inbox" },
  { href: "/reviews/request", label: () => "Yêu cầu review", icon: "review" },
  { href: "/critical", label: () => "Critical Board", icon: "alert" },
  { href: "/closures", label: () => "Đóng finding", icon: "checkCircle" },
  { href: "/disputes", label: (lead) => (lead ? "Duyệt khiếu nại" : "Khiếu nại"), icon: "scale" },
];

/** True when the path belongs to the Review & Finding menu (review details included). */
export function underReviewMenu(pathname: string): boolean {
  return ["/reviews", "/critical", "/closures", "/disputes"].some((h) => pathname === h || pathname.startsWith(`${h}/`));
}

/** Page-level tabs of the Review & Finding menu. */
export function ReviewTabs({ current }: { current: string }) {
  const { data: me } = useApi<Me>("/auth/me");
  const lead = !!me && (me.is_admin || me.lead_teams.length > 0);
  return (
    <nav className="tabs page-tabs" aria-label={REVIEW_MENU}>
      {REVIEW_TABS.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          className={t.href === current ? "active" : undefined}
          aria-current={t.href === current ? "page" : undefined}
        >
          <Icon name={t.icon} size={16} />
          {t.label(lead)}
        </Link>
      ))}
    </nav>
  );
}
