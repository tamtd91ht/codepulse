"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { Icon, type IconName, IconButton } from "@/components/icons";
import { InboxProvider, NotificationBell } from "@/components/Inbox";
import { ReviewActivity } from "@/components/ReviewActivity";
import { Stetho } from "@/components/Stetho";
import { stethoHome } from "@/components/StethoTabs";
import { RAIL_KEY, ThemeToggle } from "@/components/theme";
import { UserMenu } from "@/components/UserMenu";
import { can, managesAnyProject, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

const NARROW = "(max-width: 1023px)";

type Item = { href: string; label: string; icon: IconName };

/**
 * Sidebar + content shell. On phones and tablets the sidebar is a drawer behind a top bar; on
 * desktops it can collapse to an icon rail (remembered per browser).
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const onLogin = pathname === "/login";
  const { data: me } = useApi<Me>(onLogin ? null : "/auth/me", {
    redirect: !pathname.startsWith("/guide"),
  });
  const [rail, setRail] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const asideRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    // The boot script already applied data-rail before paint; the state only mirrors it.
    setRail(document.documentElement.hasAttribute("data-rail"));
    const media = window.matchMedia(NARROW);
    const sync = () => {
      setNarrow(media.matches);
      if (!media.matches) setDrawer(false);
    };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  // Route changes close the drawer; the dependency is the route itself.
  useEffect(() => {
    setDrawer(false);
  }, [pathname]);
  // Open drawer: lock scroll, move focus in, Escape closes; closing returns focus to the menu button.
  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    asideRef.current?.querySelector<HTMLElement>("a, button")?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      menuRef.current?.focus();
    };
  }, [drawer]);

  if (onLogin) return <>{children}</>;

  const isAdmin = !!me?.is_admin;
  const isLead = !!me && (isAdmin || me.lead_teams.length > 0);
  // Stetho, the AI assistant: admins and managers only (app/api/routers/assistant.py).
  const assistant = !!me && (isAdmin || me.managed_teams.length > 0);
  const managesProject = managesAnyProject(me);
  const role = !me
    ? ""
    : isAdmin
      ? "Admin"
      : me.manage_all
        ? "Quản lý tổng"
        : me.lead_teams.length
        ? "Lead"
        : me.managed_teams.length || managesProject
          ? "Manager"
          : "Thành viên";

  const primary: Item[] = me
    ? [
        { href: "/overview", label: "Tổng quan", icon: "activity" },
        // Luồng làm việc: lead and above only (app/api/security.require_insights).
        ...(me.insights ? [{ href: "/insights", label: "Luồng làm việc", icon: "trendUp" } as Item] : []),
        { href: "/teams", label: "Team", icon: "members" },
        // Per-person evaluation: lead and above (app/api/security.require_members_page); a member
        // sees only themselves, on "Của tôi".
        ...(me.members_page ? [{ href: "/members", label: "Thành viên", icon: "pulse" } as Item] : []),
        // Báo cáo thành viên (docs/specs/23 §9): whoever holds report.member (a member: their own).
        ...(can(me, "report.member") ? [{ href: "/members/report", label: "Báo cáo thành viên", icon: "book" } as Item] : []),
        // Tích hợp MCP (docs/specs/26 §13.2): whoever holds report.mcp (a member: their own).
        ...(can(me, "report.mcp") ? [{ href: "/mcp-report", label: "Tích hợp MCP", icon: "terminal" } as Item] : []),
        { href: `/members/${me.id}`, label: "Của tôi", icon: "user" },
        { href: "/reviews", label: "Kết quả review", icon: "inbox" },
        { href: "/reviews/request", label: "Yêu cầu review", icon: "review" },
        // Task Jira (docs/specs/27 §18): once Jira is connected.
        ...(me.jira_tasks ? [{ href: "/jira", label: "Task Jira", icon: "flag" } as Item] : []),
        // Tài liệu dự án (docs/specs/29): everyone holding docs.view (every member by default).
        ...(can(me, "docs.view") ? [{ href: "/docs", label: "Tài liệu dự án", icon: "file" } as Item] : []),
        // docs/specs/30: the test cases use the documents' permissions.
        ...(can(me, "docs.view") ? [{ href: "/testcases", label: "Testcase", icon: "clipboardCheck" } as Item] : []),
        { href: "/critical", label: "Critical Board", icon: "alert" },
        { href: "/closures", label: "Đóng finding", icon: "checkCircle" },
        { href: "/disputes", label: isLead ? "Duyệt khiếu nại" : "Khiếu nại", icon: "scale" },
        // DPI hàng tháng (docs/specs/25): everyone with a scorecard, the managers' distribution, HR.
        ...(can(me, "dpi.view") || can(me, "dpi.distribution") || can(me, "dpi.final_view") || can(me, "jira.admin")
          ? [{ href: "/dpi", label: "DPI hàng tháng", icon: "trendUp" } as Item]
          : []),
      ]
    : [];
  const secondary: Item[] = [
    ...(isAdmin ? [{ href: "/admin", label: "Admin", icon: "settings" } as Item]
      : []),
    // Stetho (docs/specs/28): one menu, tabs Tri thức | Lịch sử inside (components/StethoTabs);
    // the link opens the first tab one may see.
    ...(me && stethoHome(me) ? [{ href: stethoHome(me) as string, label: "Stetho", icon: "stethoscope" } as Item] : []),
    { href: "/guide", label: "Hướng dẫn", icon: "book" },
  ];

  const toggleRail = () => {
    const next = !rail;
    setRail(next);
    document.documentElement.toggleAttribute("data-rail", next);
    try {
      localStorage.setItem(RAIL_KEY, next ? "rail" : "full");
    } catch {
      /* per-viewer convenience only */
    }
  };

  const navLink = (item: Item) => {
    const under = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
    // Stetho is one menu for both of its tabs.
    if (item.label === "Stetho") {
      const on = under("/stetho");
      return (
        <Link
          key={item.href}
          href={item.href}
          className={`nav-link${on ? " active" : ""}`}
          aria-current={on ? "page" : undefined}
          title={rail ? item.label : undefined}
        >
          <Icon name={item.icon} size={18} />
          <span className="label">{item.label}</span>
        </Link>
      );
    }
    // The most specific item wins: /reviews/request is "Yêu cầu review", not "Kết quả review".
    const active =
      pathname === item.href ||
      (item.href !== "/members" &&
        under(item.href) &&
        !primary.some((other) => other.href.length > item.href.length && under(other.href)));
    return (
      <Link
        key={item.href}
        href={item.href}
        className={`nav-link${active ? " active" : ""}`}
        aria-current={active ? "page" : undefined}
        title={rail ? item.label : undefined}
      >
        <Icon name={item.icon} size={18} />
        <span className="label">{item.label}</span>
      </Link>
    );
  };

  return (
    <InboxProvider enabled={!!me}>
    <div className={`shell${rail ? " rail" : ""}${drawer ? " drawer-open" : ""}${assistant ? " has-stetho" : ""}`}>
      <a href="#main" className="skip-link">
        Bỏ qua điều hướng
      </a>

      <aside id="sidebar" className="sidebar" aria-label="Điều hướng chính" ref={asideRef} inert={narrow && !drawer}>
        <div className="sidebar-head">
          <Link href="/" className="brand" aria-label="CodePulse, trang chính">
            <span className="brand-mark">
              {/* The project logo (also the browser tab icon, app/icon.svg). */}
              <img src="/icon.svg" alt="" width={30} height={30} />
            </span>
            <span className="brand-text">CodePulse</span>
          </Link>
          <span style={{ flex: 1 }} />
          <IconButton
            icon="close"
            label="Đóng menu"
            onClick={() => setDrawer(false)}
            style={{ display: drawer ? undefined : "none" }}
          />
        </div>

        <nav className="sidebar-nav">
          {primary.length > 0 && <div className="nav-section">Theo dõi</div>}
          {primary.map(navLink)}
          <div className="nav-section">Khác</div>
          {secondary.map(navLink)}
          {!me && (
            <Link href="/login" className="nav-link">
              <Icon name="logout" size={18} />
              <span className="label">Đăng nhập</span>
            </Link>
          )}
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-actions">
            <IconButton
              icon="panelLeft"
              label={rail ? "Mở rộng thanh bên" : "Thu gọn thanh bên"}
              onClick={toggleRail}
              className={`rail-toggle${rail ? " tip-right" : ""}`}
            />
          </div>
        </div>
      </aside>
      {drawer && <div className="drawer-backdrop" onClick={() => setDrawer(false)} aria-hidden="true" />}

      <div style={{ minWidth: 0 }}>
        <header className="topbar">
          <IconButton
            icon="menu"
            label="Mở menu"
            className="topbar-menu"
            onClick={() => setDrawer(true)}
            aria-expanded={drawer}
            aria-controls="sidebar"
            ref={menuRef}
          />
          <Link href="/" className="brand topbar-brand">
            <span className="brand-mark">
              {/* The project logo (also the browser tab icon, app/icon.svg). */}
              <img src="/icon.svg" alt="" width={30} height={30} />
            </span>
            <span className="brand-text">CodePulse</span>
          </Link>
          <span className="spacer" />
          {/* Global actions sit top right on every width (omicx-ui app shell). */}
          <div className="topbar-actions">
            {me && <NotificationBell />}
            <ThemeToggle />
            {me && <UserMenu me={me} role={role} />}
          </div>
        </header>

        <main id="main" tabIndex={-1}>
          <div className="page">{children}</div>
          {/* docs/specs/11: scores are a coaching signal, stated on the dashboard. */}
          <footer className="notice">
            Điểm là tín hiệu coaching, không phải căn cứ duy nhất để đánh giá hiệu suất. Mỗi người xem được toàn bộ dữ
            liệu về mình và có thể khiếu nại (dispute) từng finding.
          </footer>
        </main>
      </div>
      {me && <ReviewActivity />}
      {assistant && <Stetho />}
    </div>
    </InboxProvider>
  );
}
