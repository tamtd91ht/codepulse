import type { ReactNode } from "react";
import { cx } from "./cx";

export interface NavItem {
  label: string;
  href: string;
  icon?: ReactNode;
  active?: boolean;
}
export interface NavGroup {
  label: string;
  items: NavItem[];
}
export interface ModuleItem {
  label: string;
  href: string;
  icon: ReactNode;
  active?: boolean;
}
export interface Crumb {
  label: string;
  href?: string;
}

/**
 * Top bar 56 px, module rail 40 px, contextual sidebar 180 px, content in a rounded panel on
 * the canvas. `brand` / `actions` fill the top bar (logo + workspace switcher on the left;
 * upgrade, theme toggle, notifications, settings, language, avatar on the right).
 * Pass `link` to render navigation with the router's link component (Next.js `Link`, …).
 */
export function AppShell({
  brand,
  actions,
  modules,
  moduleTitle,
  nav,
  crumbs,
  link: Link = DefaultLink,
  children,
}: {
  brand: ReactNode;
  actions?: ReactNode;
  modules: ModuleItem[];
  moduleTitle: string;
  nav: NavGroup[];
  crumbs: Crumb[];
  link?: LinkComponent;
  children: ReactNode;
}) {
  return (
    <div className="flex h-dvh flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center justify-between gap-2.5 px-4">
        <div className="flex min-w-0 items-center gap-3">{brand}</div>
        <div className="flex items-center gap-2">{actions}</div>
      </header>

      <div className="flex min-h-0 flex-1 gap-2 px-2 pb-2">
        <nav aria-label="Modules" className="flex w-10 shrink-0 flex-col items-center gap-2 pt-2">
          {modules.map((m) => (
            <Link
              key={m.href}
              href={m.href}
              aria-label={m.label}
              aria-current={m.active ? "page" : undefined}
              className={cx(
                "flex size-8 items-center justify-center rounded-medium transition-colors duration-150",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                m.active ? "bg-primary text-primary-foreground" : "text-secondary hover:bg-quaternary",
              )}
            >
              {m.icon}
            </Link>
          ))}
        </nav>

        <aside className="hidden w-45 shrink-0 flex-col overflow-y-auto rounded-large border border-tertiary bg-surface px-2 py-4 md:flex">
          <p className="px-2 pb-2 font-semibold text-primary">{moduleTitle}</p>
          {nav.map((group, i) => (
            <div key={group.label} className={cx("py-2", i > 0 && "border-t border-tertiary")}>
              <p className="px-2 pb-1 text-xs text-secondary">{group.label}</p>
              <ul className="flex flex-col gap-0.5">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={item.active ? "page" : undefined}
                      className={cx(
                        "flex h-8 items-center gap-2 rounded-medium px-2 text-sm transition-colors duration-150",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                        item.active ? "bg-primary text-primary-foreground" : "text-primary hover:bg-quaternary",
                      )}
                    >
                      <span aria-hidden className="size-5 shrink-0 [&>svg]:size-5">
                        {item.icon}
                      </span>
                      <span className="truncate">{item.label}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </aside>

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-large border border-tertiary bg-surface">
          <nav aria-label="Breadcrumb" className="flex h-12 shrink-0 items-center gap-2 px-6 text-sm">
            {crumbs.map((c, i) => {
              const last = i === crumbs.length - 1;
              return (
                <span key={`${c.label}-${i}`} className="flex items-center gap-2">
                  {i > 0 && <span aria-hidden className="text-secondary">›</span>}
                  {last || !c.href ? (
                    <span aria-current={last ? "page" : undefined} className={last ? "font-semibold text-primary" : "text-secondary"}>
                      {c.label}
                    </span>
                  ) : (
                    <Link href={c.href} className="text-secondary hover:text-primary">
                      {c.label}
                    </Link>
                  )}
                </span>
              );
            })}
          </nav>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">{children}</div>
        </main>
      </div>
    </div>
  );
}

export type LinkComponent = (props: {
  href: string;
  className?: string;
  children: ReactNode;
  "aria-label"?: string;
  "aria-current"?: "page";
}) => ReactNode;

const DefaultLink: LinkComponent = ({ href, children, ...rest }) => (
  <a href={href} {...rest}>
    {children}
  </a>
);
