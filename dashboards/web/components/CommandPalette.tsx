"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import { matchScore } from "@/components/paletteMatch";
import { pageLabel, recentPages, recentTasks, rememberPage } from "@/components/paletteRecent";
import { ShortcutHelpModal } from "@/components/tasks/ShortcutHelp";
import { TaskCreate } from "@/components/tasks/TaskCreate";
import { selectedCard } from "@/components/tasks/cardSelection";
import { HELP_EVENT, PALETTE_EVENT, type PaletteMode } from "@/components/tasks/shortcutEvents";
import { PersonAvatar, RESOLUTIONS, RESOLUTION_LABEL, StatusPill, errorText, personName, priorityList, refreshTasks, spaceOf } from "@/components/tasks/taskParts";
import { branchName } from "@/components/tasks/taskSignal";
import { useGlobalShortcuts } from "@/components/tasks/useTaskShortcuts";
import { ApiError, api, can, useApi } from "@/lib/api";
import type { Me, Sprint, TaskDetail, TaskFilter, TaskPage, TaskPerson, TaskResolution, TaskRow, TaskSpace, TaskStatusRef, TaskVersion } from "@/lib/types";

/*
 * Command palette (docs/specs/31 §19.3, §20.2, §21.1): Ctrl+K / Cmd+K, `/`, `.` or the top bar button.
 * Context = the task open on this page (`/tasks/KEY`, `/tasks?task=KEY`) or else the selected Board
 * card: its commands come first (assign to me / to…, move to… (valid transitions; a "done" status asks
 * the reason, "Đã xong" first), sprint, version, priority, label, copy link / branch name, sub-task).
 * Matching is fuzzy and blind to Vietnamese accents. Text also searches tasks by title (key, status,
 * assignee). Empty input: the context commands, then the 5 tasks and 3 pages opened last. Prefixes:
 * `>` commands only, `#` tasks only, `@` people (open one to see their open tasks). Task search
 * starts 120 ms after typing stops and drops stale answers. Mounted once in AppShell with the global
 * shortcuts and the "?" overlay.
 */

type Stage = "root" | "status" | "resolution" | "sprint" | "space" | "filter" | "assign" | "version" | "priority" | "label" | "person";

type Option = {
  id: string;
  icon: IconName;
  label: string;
  hint?: string;
  /** Heading of the group this option starts (shown above its first option). */
  group?: string;
  task?: TaskRow;
  person?: TaskPerson;
  /** Navigate there. */
  href?: string;
  /** Run it; a string back is an error to show. */
  action?: () => Promise<string | void>;
  /** Open a sub-list (`then` runs first, e.g. to remember which status was picked). */
  stage?: Stage;
  then?: () => void;
  /** Ask a second Enter first (the text says why). */
  confirm?: string;
  /** Close the palette after it ran without refreshing the task lists (copy). */
  quiet?: boolean;
};

const PAGES: { label: string; href: string; icon: IconName; needs?: string; words?: string }[] = [
  { label: "Việc của tôi", href: "/tasks/me", icon: "user", needs: "tasks.view", words: "my work" },
  { label: "Task", href: "/tasks", icon: "board", needs: "tasks.view", words: "board backlog" },
  { label: "Tạo task", href: "/tasks?create=1", icon: "plus", needs: "tasks.create", words: "create new" },
  { label: "Tổng quan", href: "/", icon: "activity", words: "overview home" },
  { label: "Review", href: "/reviews", icon: "review", words: "finding mr" },
  { label: "Testcase", href: "/testcases", icon: "clipboardCheck", needs: "tests.view", words: "test case" },
];

const STAGE_TITLE: Record<Exclude<Stage, "root">, string> = {
  status: "Chuyển sang",
  resolution: "Lý do đóng",
  sprint: "Thêm vào sprint",
  space: "Chuyển dự án",
  filter: "Bộ lọc đã lưu",
  assign: "Giao cho",
  version: "Gắn phiên bản",
  priority: "Đổi mức ưu tiên",
  label: "Thêm nhãn",
  person: "Việc đang mở",
};

const KEY_RE = /^([a-z][a-z0-9]*)-(\d+)$/i;
const LAST_SPACE = "cp:tasks:space";

/** The task open on this page: `/tasks/KEY` or `/tasks?task=KEY`. */
function openTaskKey(): string | null {
  const { pathname, search } = window.location;
  const m = /^\/tasks\/([^/]+)\/?$/.exec(pathname);
  if (m) {
    const k = decodeURIComponent(m[1]);
    return KEY_RE.test(k) ? k.toUpperCase() : null;
  }
  if (pathname === "/tasks" || pathname === "/tasks/") {
    const t = new URLSearchParams(search).get("task");
    return t && KEY_RE.test(t) ? t.toUpperCase() : null;
  }
  return null;
}

/** The dự án to look people up in when no task is in context: the Task page's, then the last one. */
function pageSpace(): number | null {
  const s = new URLSearchParams(window.location.search).get("space");
  if (s && /^\d+$/.test(s)) return Number(s);
  try {
    const v = window.localStorage.getItem(LAST_SPACE);
    return v && /^\d+$/.test(v) ? Number(v) : null;
  } catch {
    return null;
  }
}

/** Tasks by title or key, 120 ms after the last keystroke; a newer query aborts the older request. */
function useTaskSearch(query: string | null): { items: TaskRow[] | null; loading: boolean; error: string | null } {
  const [state, setState] = useState<{ q: string | null; items: TaskRow[] | null; error: string | null }>({ q: null, items: null, error: null });
  const cache = useRef(new Map<string, TaskRow[]>());
  useEffect(() => {
    if (!query) return;
    const hit = cache.current.get(query);
    if (hit) {
      setState({ q: query, items: hit, error: null });
      return;
    }
    const ctrl = new AbortController();
    const t = window.setTimeout(() => {
      const p = new URLSearchParams({ q: query, limit: "8", sort: "updated", order: "desc" });
      fetch(`/api/v1/tasks?${p}`, { credentials: "same-origin", signal: ctrl.signal })
        .then(async (r) => {
          if (!r.ok) throw new ApiError(r.status, r.statusText);
          const page = (await r.json()) as TaskPage;
          cache.current.set(query, page.items);
          setState({ q: query, items: page.items, error: null });
        })
        .catch((e: unknown) => {
          if (ctrl.signal.aborted) return;
          setState({ q: query, items: null, error: errorText(e) });
        });
    }, 120);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [query]);
  if (!query) return { items: null, loading: false, error: null };
  // While a newer query runs, the previous answer stays on screen (no flicker between keystrokes).
  return { items: state.items, loading: state.q !== query, error: state.q === query ? state.error : null };
}

export function CommandPalette({ me }: { me: Me }) {
  const [open, setOpen] = useState<PaletteMode | null>(null);
  const [help, setHelp] = useState(false);
  const [subtask, setSubtask] = useState<{ space: TaskSpace; parent: string } | null>(null);
  const router = useRouter();
  const pathname = usePathname();
  const toggleHelp = useCallback(() => setHelp((v) => !v), []);
  useGlobalShortcuts(me, toggleHelp, help);

  // "Trang gần đây" (§21.1).
  useEffect(() => {
    const label = pathname ? pageLabel(pathname) : null;
    if (label && pathname) rememberPage({ href: pathname, label });
  }, [pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => (v ? null : "all"));
      }
    };
    const onOpen = (e: Event) => {
      setHelp(false);
      setOpen((e as CustomEvent<{ mode?: PaletteMode }>).detail?.mode ?? "all");
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener(PALETTE_EVENT, onOpen);
    window.addEventListener(HELP_EVENT, toggleHelp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(PALETTE_EVENT, onOpen);
      window.removeEventListener(HELP_EVENT, toggleHelp);
    };
  }, [toggleHelp]);

  return (
    <>
      {open && (
        <Palette
          key={open}
          me={me}
          mode={open}
          onClose={() => setOpen(null)}
          onSubtask={(space, parent) => {
            setOpen(null);
            setSubtask({ space, parent });
          }}
        />
      )}
      {help && !open && <ShortcutHelpModal onClose={() => setHelp(false)} />}
      {subtask && (
        <TaskCreate
          space={subtask.space}
          parentKey={subtask.parent}
          onClose={() => setSubtask(null)}
          onCreated={(key) => {
            setSubtask(null);
            router.push(`/tasks/${encodeURIComponent(key)}`);
          }}
        />
      )}
    </>
  );
}

function Palette({ me, mode, onClose, onSubtask }: { me: Me; mode: PaletteMode; onClose: () => void; onSubtask: (space: TaskSpace, parent: string) => void }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const [stage, setStage] = useState<Stage>("root");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [closing, setClosing] = useState<TaskStatusRef | null>(null);
  const [person, setPerson] = useState<TaskPerson | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();
  const titleId = useId();
  const tasks = can(me, "tasks.view");
  const commands = mode === "all";

  // The context, read once when the palette opens: the open task, else the selected Board card.
  const [taskKey] = useState(() => (tasks && commands ? (openTaskKey() ?? selectedCard()) : null));
  const [recent] = useState(() => ({ tasks: tasks ? recentTasks() : [], pages: commands ? recentPages() : [] }));
  const detail = useApi<TaskDetail>(taskKey ? `/tasks/${encodeURIComponent(taskKey)}` : null);
  const task = detail.data;
  const trimmed = text.trim();
  const prefix = stage === "root" && /^[>#@]/.test(trimmed) ? (trimmed[0] as ">" | "#" | "@") : null;
  const words = prefix ? trimmed.slice(1).trim() : trimmed;
  const [fallbackSpace] = useState(() => (tasks ? pageSpace() : null));
  const bp = task ? spaceOf(task) : null;
  const peopleBp = bp ?? fallbackSpace;

  // Each list loads only when it is shown (one request each, shared with the Task pages' cache).
  const spaces = useApi<TaskSpace[]>(tasks && (taskKey || stage === "space" || prefix === "@") ? "/tasks/spaces" : null);
  const space = spaces.data?.find((s) => s.business_project_id === bp) ?? null;
  const pplSpace = peopleBp ?? spaces.data?.find((s) => s.enabled)?.business_project_id ?? null;
  const sprints = useApi<Sprint[]>(stage === "sprint" && bp ? `/tasks/spaces/${bp}/sprints` : null);
  const versions = useApi<TaskVersion[]>(stage === "version" && bp ? `/tasks/spaces/${bp}/versions` : null);
  const filters = useApi<TaskFilter[]>(stage === "filter" ? "/tasks/filters" : null);
  const people = useApi<TaskPerson[]>((stage === "assign" && bp) || (prefix === "@" && pplSpace) ? `/tasks/people?space=${stage === "assign" ? bp : pplSpace}` : null);
  const recentLabels = useApi<TaskPage>(stage === "label" && bp ? `/tasks?space=${bp}&limit=200&sort=updated&order=desc` : null);
  const theirs = useApi<TaskPage>(
    stage === "person" && person && pplSpace ? `/tasks?space=${pplSpace}&assignee=${person.id}&category=todo&category=doing&sort=updated&order=desc&limit=20` : null,
  );

  // Focus the input on open; give focus back to whatever had it on close.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    input.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      opener?.focus();
    };
  }, []);

  const wantTasks = stage === "root" && tasks && prefix !== ">" && prefix !== "@" && (prefix === "#" || !commands ? words.length >= 1 : words.length >= 2);
  const found = useTaskSearch(wantTasks ? words : null);

  const enter = (next: Stage) => {
    setStage(next);
    setText("");
    setNote(null);
    setArmed(null);
    input.current?.focus();
  };

  const options = useMemo<Option[]>(() => {
    const q = stage === "root" ? words : trimmed;
    /** Keep and rank by fuzzy score (stable for ties). */
    const ranked = (items: (Option & { words?: string })[]) =>
      items
        .map((o, i) => ({ o, i, s: matchScore(q, o.label, `${o.hint ?? ""} ${o.words ?? ""}`) }))
        .filter((x) => x.s >= 0)
        .sort((a, b) => (q ? b.s - a.s : 0) || a.i - b.i)
        .map((x) => x.o);
    const grouped = (items: Option[], group: string) => items.map((o, i) => (i === 0 ? { ...o, group } : o));

    if (stage === "status" && task) {
      return ranked((task.transitions ?? []).filter((s) => s.id !== task.status.id).map((s) => statusOption(s)));
    }
    if (stage === "resolution" && task && closing) {
      const critical = task.git?.summary.open_critical ?? 0;
      return ranked(
        RESOLUTIONS.map((r) => ({
          id: `res-${r}`,
          icon: r === "done" ? "checkCircle" : "close",
          label: RESOLUTION_LABEL[r],
          hint: r === "done" ? "Mặc định" : undefined,
          confirm: critical > 0 ? `Còn ${critical} finding critical. Enter lần nữa để vẫn chuyển sang “${closing.name}”.` : undefined,
          action: () => transition(task.key, closing, r),
        })),
      );
    }
    if (stage === "sprint" && task) {
      const out: Option[] = (sprints.data ?? [])
        .filter((s) => s.state !== "closed" && s.id !== task.sprint_id)
        .map((s) => ({ id: `sp-${s.id}`, icon: "flag", label: s.name, hint: s.state === "active" ? "Đang chạy" : "Sắp tới", action: () => moveTo(task.key, s.id) }));
      if (task.sprint_id) out.push({ id: "sp-none", icon: "inbox", label: "Backlog (bỏ khỏi sprint)", action: () => moveTo(task.key, null) });
      return ranked(out);
    }
    if (stage === "version" && task) {
      const out: Option[] = (versions.data ?? [])
        .filter((v) => !v.released_at && v.id !== task.version_id)
        .map((v) => ({ id: `ver-${v.id}`, icon: "flag", label: v.name, hint: v.release_date ?? undefined, action: () => patch(task.key, { version_id: v.id }) }));
      if (task.version_id) out.push({ id: "ver-none", icon: "close", label: "Bỏ phiên bản", action: () => patch(task.key, { version_id: null }) });
      return ranked(out);
    }
    if (stage === "priority" && task && space) {
      return ranked(
        priorityList(space)
          .filter((p) => p.key !== task.priority)
          .map((p) => ({ id: `pr-${p.key}`, icon: "flag", label: p.name, action: () => patch(task.key, { priority: p.key }) })),
      );
    }
    if (stage === "label" && task) {
      const mine = new Set(task.labels ?? []);
      const known = [...new Set((recentLabels.data?.items ?? []).flatMap((r) => r.labels ?? []))].filter((l) => !mine.has(l)).sort();
      const out = ranked(known.map((l) => ({ id: `lb-${l}`, icon: "plus", label: l, action: () => patch(task.key, { labels: [...mine, l] }) })));
      const typed = trimmed.replace(/\s+/g, "-");
      if (typed && !mine.has(typed) && !known.includes(typed))
        out.unshift({ id: "lb-new", icon: "plus", label: `Tạo nhãn “${typed}”`, action: () => patch(task.key, { labels: [...mine, typed] }) });
      return out;
    }
    if (stage === "assign" && task) {
      const out: Option[] = (people.data ?? [])
        .filter((p) => p.id !== task.assignee?.id)
        .map((p) => ({ id: `as-${p.id}`, icon: "user", label: personName(p), hint: p.primary_email ?? undefined, person: p, action: () => patch(task.key, { assignee_id: p.id }) }));
      if (task.assignee) out.push({ id: "as-none", icon: "close", label: "Bỏ giao", action: () => patch(task.key, { assignee_id: null }) });
      return ranked(out);
    }
    if (stage === "person" && person) {
      const out: Option[] = [];
      if (pplSpace)
        out.push({ id: "pp-list", icon: "filter", label: `Mở Danh sách lọc theo ${personName(person)}`, href: `/tasks?space=${pplSpace}&tab=list&assignee=${person.id}` });
      for (const t of theirs.data?.items ?? []) out.push({ id: `pt-${t.key}`, icon: "board", label: t.title, task: t, href: `/tasks/${encodeURIComponent(t.key)}`, group: out.length === 1 ? "Việc đang mở" : undefined });
      return out.filter((o) => !trimmed || o.task === undefined || matchScore(trimmed, `${o.task.key} ${o.task.title}`) >= 0);
    }
    if (stage === "space") {
      return ranked(
        (spaces.data ?? []).map((s) => ({ id: `sc-${s.business_project_id}`, icon: "folder", label: s.name, words: s.key ?? "", hint: s.enabled ? (s.key ?? undefined) : "Chưa bật Task", href: `/tasks?space=${s.business_project_id}` })),
      );
    }
    if (stage === "filter") {
      return ranked(
        (filters.data ?? []).map((f) => {
          const p = new URLSearchParams();
          if (f.space) p.set("space", String(f.space));
          p.set("tab", "list");
          p.set("filter", String(f.id));
          for (const [k, v] of Object.entries(f.query ?? {})) if (typeof v === "string" && v && k !== "space") p.set(k, v);
          return { id: `fl-${f.id}`, icon: "filter" as IconName, label: f.name, hint: f.shared ? "Chia sẻ" : undefined, href: `/tasks?${p}` };
        }),
      );
    }

    // Root.
    const out: Option[] = [];
    const onlyCommands = prefix === ">";
    const onlyTasks = prefix === "#" || !commands;
    if (prefix === "@") {
      const ppl = ranked((people.data ?? []).map((p) => ({ id: `pe-${p.id}`, icon: "user", label: personName(p), words: p.primary_email ?? "", person: p, stage: "person", then: () => setPerson(p) })));
      return grouped(ppl, "Người");
    }
    const m = KEY_RE.exec(words);
    const typedKey = m ? `${m[1].toUpperCase()}-${m[2]}` : null;
    if (typedKey && tasks && !onlyCommands) out.push({ id: `key-${typedKey}`, href: `/tasks/${encodeURIComponent(typedKey)}`, icon: "external", label: `Mở ${typedKey}` });

    if (commands && !onlyTasks && task) out.push(...grouped(ranked(contextCommands(task, !!words)), `Lệnh trên ${task.key}`));
    if (!words && !prefix) {
      const rt = recent.tasks.filter((t) => t.key !== task?.key);
      out.push(...grouped(rt.map((t) => ({ id: `rt-${t.key}`, icon: "history", label: t.title, hint: t.key, href: `/tasks/${encodeURIComponent(t.key)}` })), "Task gần đây"));
      out.push(...grouped(recent.pages.map((p) => ({ id: `rp-${p.href}`, icon: "history", label: p.label, hint: "Trang", href: p.href })), "Trang gần đây"));
    }
    if (commands && !onlyTasks) {
      const nav: (Option & { words?: string })[] = PAGES.filter((c) => !c.needs || can(me, c.needs)).map((c) => ({ id: `cmd-${c.href}`, href: c.href, icon: c.icon, label: c.label, hint: "Trang", words: c.words }));
      if (tasks) {
        nav.push({ id: "go-space", icon: "folder", label: "Chuyển dự án…", hint: "Task", words: "project space", stage: "space" });
        nav.push({ id: "go-filter", icon: "filter", label: "Bộ lọc đã lưu…", hint: "Danh sách", words: "saved filter", stage: "filter" });
        nav.push({ id: "go-people", icon: "members", label: "Tìm người…", hint: "@", words: "people person nguoi", action: async () => setText("@") });
      }
      out.push(...grouped(ranked(nav), "Lệnh"));
    }
    if (!onlyCommands) {
      const items = (found.items ?? []).filter((t) => t.key !== typedKey);
      out.push(...grouped(items.map((t) => ({ id: `task-${t.key}`, href: `/tasks/${encodeURIComponent(t.key)}`, icon: "board" as IconName, label: t.title, task: t })), "Task"));
    }
    return out;
    // The actions close over helpers that only call the API; the lists are what change.
  }, [stage, trimmed, words, prefix, tasks, commands, me, task, space, closing, person, sprints.data, versions.data, spaces.data, filters.data, people.data, recentLabels.data, theirs.data, found.items, recent, pplSpace]);

  /** A valid status to move the context task to; a "done" one first asks why (resolution stage). */
  function statusOption(s: TaskStatusRef, prefixText = ""): Option {
    const key = task?.key ?? "";
    if (s.category === "done")
      return { id: `st-${s.id}`, icon: "checkCircle", label: `${prefixText}${s.name}`, hint: "Hỏi lý do đóng", stage: "resolution", then: () => setClosing(s) };
    return { id: `st-${s.id}`, icon: "chevronRight", label: `${prefixText}${s.name}`, action: () => transition(key, s) };
  }

  /** The commands on the context task, allowed by its rights and its dự án. With `typing`, each
   * valid status is also a command of its own ("chuyen review" → "Chuyển sang Đang review"). */
  function contextCommands(t: TaskDetail, typing: boolean): (Option & { words?: string })[] {
    const k = t.key;
    const out: (Option & { words?: string })[] = [];
    if (t.can.assign && t.assignee?.id !== me.id) out.push({ id: "t-assign-me", icon: "user", label: "Giao cho tôi", hint: k, words: "assign me", action: () => patch(k, { assignee_id: me.id }) });
    if (t.can.assign) out.push({ id: "t-assign", icon: "members", label: "Giao cho…", hint: k, words: "assign nguoi", stage: "assign" });
    const moves = (t.transitions ?? []).filter((s) => s.id !== t.status.id);
    if (t.can.transition && moves.length) {
      out.push({ id: "t-status", icon: "chevronRight", label: "Chuyển sang…", hint: k, words: "doi trang thai status move", stage: "status" });
      if (typing) for (const s of moves) out.push({ ...statusOption(s, "Chuyển sang "), id: `t-st-${s.id}`, hint: k, words: "doi trang thai status move" });
    }
    if (t.can.edit && space && space.board_mode !== "kanban" && t.type !== "epic") out.push({ id: "t-sprint", icon: "flag", label: "Thêm vào sprint…", hint: k, words: "sprint", stage: "sprint" });
    if (t.can.edit) out.push({ id: "t-version", icon: "flag", label: "Gắn phiên bản…", hint: k, words: "version release fix", stage: "version" });
    if (t.can.edit && space && priorityList(space).length) out.push({ id: "t-priority", icon: "arrowUp", label: "Đổi mức ưu tiên…", hint: k, words: "priority uu tien", stage: "priority" });
    if (t.can.edit) out.push({ id: "t-label", icon: "plus", label: "Thêm nhãn…", hint: k, words: "label tag nhan", stage: "label" });
    out.push({ id: "t-copy", icon: "copy", label: "Sao chép link", hint: k, words: "copy link url", quiet: true, action: () => copy(`${window.location.origin}/tasks/${encodeURIComponent(k)}`) });
    out.push({ id: "t-branch", icon: "code", label: "Sao chép tên nhánh", hint: branchName(k, t.title, t.type), words: "copy branch git", quiet: true, action: () => copy(branchName(k, t.title, t.type)) });
    if (space?.can_create && t.type !== "subtask" && (t.type === "epic" || space.types.includes("subtask")))
      out.push({ id: "t-sub", icon: "plus", label: "Tạo sub-task", hint: k, words: "subtask con create", quiet: true, action: async () => onSubtask(space, k) });
    return out;
  }

  useEffect(() => {
    setActive(0);
    setArmed(null);
  }, [trimmed, stage]);
  const current = Math.min(active, Math.max(options.length - 1, 0));

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  async function copy(value: string): Promise<string | void> {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      return "Trình duyệt chặn sao chép.";
    }
  }
  async function patch(key: string, body: Record<string, unknown>): Promise<string | void> {
    await api.patch(`/tasks/${encodeURIComponent(key)}`, body);
  }
  async function transition(key: string, s: TaskStatusRef, resolution?: TaskResolution): Promise<string | void> {
    await api.post(`/tasks/${encodeURIComponent(key)}/transition`, { status_id: s.id, resolution: s.category === "done" ? (resolution ?? "done") : undefined });
  }
  async function moveTo(key: string, sprintId: number | null): Promise<string | void> {
    await api.post(`/tasks/${encodeURIComponent(key)}/move`, { sprint_id: sprintId });
  }

  const run = async (o: Option | undefined) => {
    if (!o || busy) return;
    if (o.stage) {
      o.then?.();
      return enter(o.stage);
    }
    if (o.href) {
      onClose();
      router.push(o.href);
      return;
    }
    if (!o.action) return;
    if (o.confirm && armed !== o.id) {
      setArmed(o.id);
      setNote(o.confirm);
      return;
    }
    if (o.id === "go-people") {
      void o.action();
      input.current?.focus();
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const err = await o.action();
      if (err) {
        setNote(err);
        setBusy(false);
        return;
      }
      if (!o.quiet) await refreshTasks();
      onClose();
    } catch (e) {
      setNote(`Lỗi: ${errorText(e)}`);
      setBusy(false);
    }
  };

  const back = () => {
    if (stage === "resolution") return enter("status");
    enter("root");
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      if (stage !== "root") back();
      else onClose();
    } else if (e.key === "Backspace" && stage !== "root" && !text) {
      e.preventDefault();
      back();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (options.length) setActive((current + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (options.length) setActive((current - 1 + options.length) % options.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      void run(options[current]);
    } else if (e.key === "Tab") {
      // The input is the only stop: focus stays in the dialog.
      e.preventDefault();
      input.current?.focus();
    }
  };

  const stageList =
    stage === "sprint" ? sprints : stage === "filter" ? filters : stage === "space" ? spaces : stage === "version" ? versions : stage === "assign" ? people : stage === "label" ? recentLabels : stage === "person" ? theirs : null;
  const subLoading = !!stageList && !stageList.data && !stageList.error;
  const subError = stageList?.error;
  const title = stage === "root" ? (commands ? "Bảng lệnh" : "Tìm task") : stage === "person" && person ? `Việc đang mở của ${personName(person)}` : STAGE_TITLE[stage];

  const placeholder =
    stage !== "root"
      ? stage === "label"
        ? "Gõ tên nhãn mới hoặc chọn nhãn có sẵn…"
        : `${title}: gõ để lọc…`
      : !commands
        ? "Tìm task theo mã (CRM-12) hoặc tiêu đề…"
        : tasks
          ? task
            ? `Lệnh trên ${task.key}, tìm task… (> lệnh · # task · @ người)`
            : "Gõ lệnh, mã task hoặc tiêu đề… (> lệnh · # task · @ người)"
          : "Gõ tên trang…";

  const searching = wantTasks && found.loading;
  const foot = note
    ? note
    : busy
      ? "Đang chạy…"
      : subError
        ? `Lỗi tải: ${subError.message}`
        : subLoading || (prefix === "@" && !people.data && !people.error && !!pplSpace)
          ? "Đang tải…"
          : stage !== "root"
            ? options.length === 0
              ? stage === "sprint"
                ? "Không có sprint nào đang mở khác sprint của task."
                : stage === "filter"
                  ? "Chưa có bộ lọc đã lưu (lưu ở tab Danh sách)."
                  : stage === "version"
                    ? "Dự án chưa có phiên bản nào chưa phát hành."
                    : "Không có mục khớp."
              : "Enter để chọn · Backspace / Esc để quay lại"
            : prefix === "@" && !pplSpace
              ? "Chọn một dự án ở trang Task trước để tìm người."
              : found.error && wantTasks
                ? `Lỗi tìm task: ${found.error}`
                : searching
                  ? "Đang tìm task…"
                  : options.length === 0
                    ? wantTasks && found.items
                      ? `Không có task khớp “${words}”`
                      : commands
                        ? "Không có lệnh khớp. Thử mã task như CRM-12."
                        : "Gõ để tìm task."
                    : "↑ ↓ để chọn · Enter để chạy · Esc để đóng";

  return (
    <div className="cmdk-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown} aria-busy={busy}>
        <h2 id={titleId} className="visually-hidden">
          {title}
        </h2>
        <div className="cmdk-field">
          {stage !== "root" ? (
            <button type="button" className="qk-cmdk-back" onClick={back} aria-label="Quay lại">
              <Icon name="chevronLeft" size={16} />
              <span>{title}</span>
            </button>
          ) : (
            <Icon name="search" size={18} />
          )}
          <input
            ref={input}
            type="text"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setNote(null);
            }}
            placeholder={placeholder}
            aria-label={stage === "root" ? "Tìm task hoặc lệnh" : title}
            role="combobox"
            aria-expanded={options.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={options[current] ? `${listId}-${current}` : undefined}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
          <kbd className="cmdk-kbd">Esc</kbd>
        </div>
        {stage === "root" && taskKey && !task && !detail.error && <div className="cp-cmdk-context small muted">Đang tải lệnh cho {taskKey}…</div>}
        <ul ref={list} id={listId} role="listbox" aria-label="Kết quả" className="cmdk-list">
          {options.map((o, i) => (
            <li
              key={o.id}
              id={`${listId}-${i}`}
              data-index={i}
              data-group={o.group}
              role="option"
              aria-selected={i === current}
              className={`cmdk-item${i === current ? " active" : ""}${o.group ? " cp-cmdk-group" : ""}${o.group && i > 0 ? " cmdk-sep" : ""}${armed === o.id ? " qk-armed" : ""}`}
              onMouseMove={() => i !== current && setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => void run(o)}
            >
              {o.task ? (
                <>
                  <span className="cmdk-key">{o.task.key}</span>
                  <span className="cmdk-label">{o.task.title}</span>
                  <StatusPill status={o.task.status} resolution={o.task.resolution} />
                  <span className="cp-cmdk-person small muted" title={personName(o.task.assignee)}>
                    <PersonAvatar person={o.task.assignee} />
                    <span className="cp-cmdk-person-name">{o.task.assignee ? personName(o.task.assignee) : "Chưa giao"}</span>
                  </span>
                </>
              ) : (
                <>
                  {o.person ? <PersonAvatar person={o.person} /> : <Icon name={o.icon} size={16} />}
                  <span className="cmdk-label">{o.label}</span>
                  {o.hint && <span className="cmdk-hint small muted">{o.hint}</span>}
                  {o.stage && <Icon name="chevronRight" size={14} />}
                </>
              )}
            </li>
          ))}
        </ul>
        <div className={`cmdk-foot small${note && !armed ? " qk-cmdk-error" : " muted"}`} role="status" aria-live="polite">
          {foot}
        </div>
      </div>
    </div>
  );
}
