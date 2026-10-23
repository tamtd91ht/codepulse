"use client";

import { useSyncExternalStore } from "react";

import { IconButton } from "@/components/icons";

const KEY = "codepulse.theme";
export const RAIL_KEY = "codepulse.sidebar";
type Theme = "light" | "dark";

/**
 * Runs before paint so the stored theme and sidebar width apply without a flash. Storage can
 * be unreadable (private mode, blocked); then the system preference stays in charge.
 */
export const THEME_BOOT_SCRIPT =
  `(function(){try{var d=document.documentElement;var t=localStorage.getItem(${JSON.stringify(KEY)});` +
  `if(t==="light"||t==="dark"){d.setAttribute("data-theme",t);}` +
  `if(localStorage.getItem(${JSON.stringify(RAIL_KEY)})==="rail"){d.setAttribute("data-rail","");}}catch(e){}})();`;

/** Effective theme, read from the document so every toggle on the page agrees. */
function readTheme(): Theme {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", onChange);
  window.addEventListener("storage", onChange);
  return () => {
    observer.disconnect();
    media.removeEventListener("change", onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Light / dark switch; the choice is per browser (localStorage). Several instances stay in sync. */
export function ThemeToggle({ className }: { className?: string }) {
  // null on the server and during hydration, so the markup matches.
  const theme = useSyncExternalStore<Theme | null>(subscribe, readTheme, () => null);

  function toggle() {
    const next: Theme = readTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* per-viewer convenience only */
    }
  }

  const dark = theme === "dark";
  return (
    <IconButton
      icon={theme === null ? "sun" : dark ? "sun" : "moon"}
      label={theme === null ? "Đổi giao diện sáng/tối" : dark ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối"}
      onClick={toggle}
      className={className}
    />
  );
}
