/*
 * What the command palette offers when its input is empty (docs/specs/31 §21.1): the 5 tasks and
 * the 3 pages opened last, in this browser only (localStorage; a per-viewer convenience, so every
 * read and write may fail quietly).
 */

export type RecentTask = { key: string; title: string };
export type RecentPage = { href: string; label: string };

const TASKS_KEY = "cp:palette:recent-tasks";
const PAGES_KEY = "cp:palette:recent-pages";
const TASKS_MAX = 5;
const PAGES_MAX = 3;

function read<T>(key: string): T[] {
  try {
    const raw = window.localStorage.getItem(key);
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? (v as T[]) : [];
  } catch {
    return [];
  }
}

function write<T>(key: string, items: T[]) {
  try {
    window.localStorage.setItem(key, JSON.stringify(items));
  } catch {
    /* storage blocked: nothing to remember */
  }
}

export function recentTasks(): RecentTask[] {
  return read<RecentTask>(TASKS_KEY).filter((t) => t && typeof t.key === "string" && typeof t.title === "string");
}

export function recentPages(): RecentPage[] {
  return read<RecentPage>(PAGES_KEY).filter((p) => p && typeof p.href === "string" && typeof p.label === "string");
}

export function rememberTask(t: RecentTask) {
  write(TASKS_KEY, [t, ...recentTasks().filter((x) => x.key !== t.key)].slice(0, TASKS_MAX));
}

export function rememberPage(p: RecentPage) {
  write(PAGES_KEY, [p, ...recentPages().filter((x) => x.href !== p.href)].slice(0, PAGES_MAX));
}

/** The pages worth remembering, by path prefix (longest first); task detail pages are tasks. */
const PAGE_LABELS: [string, string][] = [
  ["/tasks/me", "Việc của tôi"],
  ["/tasks", "Task"],
  ["/overview", "Tổng quan"],
  ["/insights", "Luồng làm việc"],
  ["/teams", "Team"],
  ["/members/report", "Báo cáo thành viên"],
  ["/members", "Thành viên"],
  ["/mcp-report", "Tích hợp MCP"],
  ["/reviews", "Review"],
  ["/jira", "Task Jira"],
  ["/docs", "Tài liệu dự án"],
  ["/testcases", "Testcase"],
  ["/dpi", "DPI hàng tháng"],
  ["/project-admin", "Quản trị dự án"],
  ["/admin", "Admin"],
  ["/guide", "Hướng dẫn"],
  ["/notifications", "Thông báo"],
  ["/profile", "Hồ sơ"],
];

/** The label of a page path, or null when it is not one to remember (a task's own page, login…). */
export function pageLabel(pathname: string): string | null {
  if (/^\/tasks\/[A-Za-z][A-Za-z0-9]*-\d+\/?$/.test(pathname)) return null;
  if (pathname === "/") return "Tổng quan";
  for (const [prefix, label] of PAGE_LABELS) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return label;
  }
  return null;
}
