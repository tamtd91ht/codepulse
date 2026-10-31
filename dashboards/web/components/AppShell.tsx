"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { Icon, type IconName, IconButton } from "@/components/icons";
import { ReviewActivity } from "@/components/ReviewActivity";
import { Stetho } from "@/components/Stetho";
import { RAIL_KEY, ThemeToggle } from "@/components/theme";
import { Avatar } from "@/components/ui";
import { api, useApi } from "@/lib/api";
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
  const [logoutFailed, setLogoutFailed] = useState(false);
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
  const role = !me ? "" : isAdmin ? "Admin" : me.lead_teams.length ? "Lead" : me.managed_teams.length ? "Manager" : "Thành viên";

  const primary: Item[] = me
    ? [
        { href: "/overview", label: "Tổng quan", icon: "activity" },
        { href: "/teams", label: "Team", icon: "members" },
        // Per-person evaluation: a lead sees their team, admin everyone (a developer only
        // themselves, which the "Của tôi" page already shows).
        ...(isAdmin || me.lead_teams.length > 0 ? [{ href: "/members", label: "Thành viên", icon: "pulse" } as Item] : []),
        { href: `/members/${me.id}`, label: "Của tôi", icon: "user" },
        { href: "/reviews", label: "Kết quả review", icon: "inbox" },
        { href: "/reviews/request", label: "Yêu cầu review", icon: "review" },
        { href: "/critical", label: "Critical Board", icon: "alert" },
        { href: "/disputes", label: isLead ? "Duyệt khiếu nại" : "Khiếu nại", icon: "scale" },
      ]
    : [];
  const secondary: Item[] = [
    ...(isAdmin ? [{ href: "/admin", label: "Admin", icon: "settings" } as Item]
      : []),
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
          {me && (
            <div className="user-chip" title={me.email ?? undefined}>
              <Avatar name={me.full_name} email={me.email} />
              <div className="who">
                <div className="name">{me.full_name ?? me.email}</div>
                <div className="role">{role}</div>
              </div>
            </div>
          )}
          <div className="sidebar-actions">
            <ThemeToggle className={rail ? "tip-right" : undefined} />
            <IconButton
              icon="panelLeft"
              label={rail ? "Mở rộng thanh bên" : "Thu gọn thanh bên"}
              onClick={toggleRail}
              className={`rail-toggle${rail ? " tip-right" : ""}`}
            />
            {me && (
              <IconButton
                icon="logout"
                label="Đăng xuất"
                className={rail ? "tip-right" : undefined}
                onClick={async () => {
                  try {
                    await api.post("/auth/logout");
                    window.location.href = "/login";
                  } catch {
                    // The session is still live; say so instead of pretending to be signed out.
                    setLogoutFailed(true);
                  }
                }}
              />
            )}
          </div>
          {logoutFailed && (
            <span className="error small" role="alert">
              Đăng xuất thất bại, thử lại.
            </span>
          )}
        </div>
      </aside>
      {drawer && <div className="drawer-backdrop" onClick={() => setDrawer(false)} aria-hidden="true" />}

      <div style={{ minWidth: 0 }}>
        <header className="topbar">
          <IconButton
            icon="menu"
            label="Mở menu"
            onClick={() => setDrawer(true)}
            aria-expanded={drawer}
            aria-controls="sidebar"
            ref={menuRef}
          />
          <Link href="/" className="brand">
            <span className="brand-mark">
              {/* The project logo (also the browser tab icon, app/icon.svg). */}
              <img src="/icon.svg" alt="" width={30} height={30} />
            </span>
            <span className="brand-text">CodePulse</span>
          </Link>
          <span className="spacer" />
          <ThemeToggle />
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
  );
}
