"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import { ShortcutHelpModal } from "@/components/tasks/ShortcutHelp";
import { HELP_EVENT, PALETTE_EVENT, type PaletteMode } from "@/components/tasks/shortcutEvents";
import { StatusPill, errorText, refreshTasks, spaceOf } from "@/components/tasks/taskParts";
import { useGlobalShortcuts } from "@/components/tasks/useTaskShortcuts";
import { api, can, useApi } from "@/lib/api";
import type { Me, Sprint, TaskDetail, TaskFilter, TaskRow, TaskSpace, TaskStatusRef } from "@/lib/types";

/*
 * Command palette (docs/specs/31 §19.3, §20.2): Ctrl+K / Cmd+K, `/`, `.` or the top bar button.
 * Type a task key to open it, any text to search tasks, or a page name to go there. On an open task
 * (`/tasks/KEY` or `/tasks?task=KEY`) it also runs commands on it: assign to me, change status, add
 * to a sprint, copy its link. "Chuyển dự án…" and "Bộ lọc đã lưu…" open sub-lists. Mounted once in
 * AppShell for signed-in users, with the global shortcuts and the "?" overlay.
 */

type Stage = "root" | "status" | "sprint" | "space" | "filter";

type Option = {
  id: string;
  icon: IconName;
  label: string;
  hint?: string;
  task?: TaskRow;
  /** Navigate there. */
  href?: string;
  /** Run it; a string back is an error to show. */
  action?: () => Promise<string | void>;
  /** Open a sub-list. */
  stage?: Stage;
  /** Ask a second Enter first (the text says why). */
  confirm?: string;
};

const COMMANDS: { label: string; href: string; icon: IconName; needs?: string; words?: string }[] = [
  { label: "Việc của tôi", href: "/tasks/me", icon: "user", needs: "tasks.view", words: "my work viec cua toi" },
  { label: "Task", href: "/tasks", icon: "board", needs: "tasks.view", words: "board backlog" },
  { label: "Tạo task", href: "/tasks?create=1", icon: "plus", needs: "tasks.create", words: "tao task create new" },
  { label: "Tổng quan", href: "/", icon: "activity", words: "tong quan overview home" },
  { label: "Review", href: "/reviews", icon: "review", words: "finding mr" },
  { label: "Testcase", href: "/testcases", icon: "clipboardCheck", needs: "tests.view", words: "test case" },
];

const STAGE_TITLE: Record<Exclude<Stage, "root">, string> = {
  status: "Đổi trạng thái",
  sprint: "Thêm vào sprint",
  space: "Chuyển dự án",
  filter: "Bộ lọc đã lưu",
};

const KEY_RE = /^([a-z][a-z0-9]*)-(\d+)$/i;

/** Lower-case, accents removed: "Tạo" matches "tao". */
const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();

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

export function CommandPalette({ me }: { me: Me }) {
  const [open, setOpen] = useState<PaletteMode | null>(null);
  const [help, setHelp] = useState(false);
  const toggleHelp = useCallback(() => setHelp((v) => !v), []);
  useGlobalShortcuts(me, toggleHelp, help);

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
      {open && <Palette key={open} me={me} mode={open} onClose={() => setOpen(null)} />}
      {help && !open && <ShortcutHelpModal onClose={() => setHelp(false)} />}
    </>
  );
}

function Palette({ me, mode, onClose }: { me: Me; mode: PaletteMode; onClose: () => void }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [stage, setStage] = useState<Stage>("root");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();
  const titleId = useId();
  const tasks = can(me, "tasks.view");
  const commands = mode === "all";

  // The task on this page, read once when the palette opens.
  const [taskKey] = useState(() => (tasks && commands ? openTaskKey() : null));
  const detail = useApi<TaskDetail>(taskKey ? `/tasks/${encodeURIComponent(taskKey)}` : null);
  const task = detail.data;
  const bp = task ? spaceOf(task) : null;
  // The lists load only for what is shown (one request each, shared with the Task pages' cache).
  const spaces = useApi<TaskSpace[]>(tasks && commands && (taskKey || stage === "space") ? "/tasks/spaces" : null);
  const space = spaces.data?.find((s) => s.business_project_id === bp) ?? null;
  const sprints = useApi<Sprint[]>(stage === "sprint" && bp ? `/tasks/spaces/${bp}/sprints` : null);
  const filters = useApi<TaskFilter[]>(stage === "filter" ? "/tasks/filters" : null);

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

  // Search 200 ms after the last keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(text.trim()), 200);
    return () => window.clearTimeout(t);
  }, [text]);

  const trimmed = text.trim();
  const search = stage === "root" && tasks && query.length >= 2 ? `/tasks?${new URLSearchParams({ q: query, limit: "8", sort: "updated", order: "desc" })}` : null;
  const found = useApi<{ items: TaskRow[]; total: number }>(search, { keepPreviousData: true });

  const enter = (next: Stage) => {
    setStage(next);
    setText("");
    setQuery("");
    setNote(null);
    setArmed(null);
    input.current?.focus();
  };

  const options = useMemo<Option[]>(() => {
    const words = fold(trimmed);
    const match = (label: string, extra = "") => !words || fold(`${label} ${extra}`).includes(words);
    const out: Option[] = [];
    if (stage === "status" && task) {
      const critical = task.git?.summary.open_critical ?? 0;
      for (const s of task.transitions ?? []) {
        if (s.id === task.status.id || !match(s.name)) continue;
        out.push({
          id: `st-${s.id}`,
          icon: s.category === "done" ? "checkCircle" : "chevronRight",
          label: s.name,
          hint: s.category === "done" ? "Đóng: Xong" : undefined,
          confirm: s.category === "done" && critical > 0 ? `Còn ${critical} finding critical. Enter lần nữa để vẫn chuyển sang “${s.name}”.` : undefined,
          action: () => transition(task.key, s),
        });
      }
      return out;
    }
    if (stage === "sprint" && task) {
      for (const s of sprints.data ?? []) {
        if (s.state === "closed" || s.id === task.sprint_id || !match(s.name)) continue;
        out.push({
          id: `sp-${s.id}`,
          icon: "flag",
          label: s.name,
          hint: s.state === "active" ? "Đang chạy" : "Sắp tới",
          action: () => moveTo(task.key, s.id),
        });
      }
      if (task.sprint_id && match("Backlog bỏ khỏi sprint")) out.push({ id: "sp-none", icon: "inbox", label: "Backlog (bỏ khỏi sprint)", action: () => moveTo(task.key, null) });
      return out;
    }
    if (stage === "space") {
      for (const s of spaces.data ?? []) {
        if (!match(s.name, s.key ?? "")) continue;
        out.push({ id: `sc-${s.business_project_id}`, icon: "folder", label: s.name, hint: s.enabled ? (s.key ?? undefined) : "Chưa bật Task", href: `/tasks?space=${s.business_project_id}` });
      }
      return out;
    }
    if (stage === "filter") {
      for (const f of filters.data ?? []) {
        if (!match(f.name)) continue;
        const p = new URLSearchParams();
        if (f.space) p.set("space", String(f.space));
        p.set("tab", "list");
        p.set("filter", String(f.id));
        for (const [k, v] of Object.entries(f.query ?? {})) if (typeof v === "string" && v && k !== "space") p.set(k, v);
        out.push({ id: `fl-${f.id}`, icon: "filter", label: f.name, hint: f.shared ? "Chia sẻ" : undefined, href: `/tasks?${p}` });
      }
      return out;
    }

    // Root.
    const m = KEY_RE.exec(trimmed);
    const typedKey = m ? `${m[1].toUpperCase()}-${m[2]}` : null;
    if (typedKey && tasks) out.push({ id: `key-${typedKey}`, href: `/tasks/${encodeURIComponent(typedKey)}`, icon: "external", label: `Mở ${typedKey}` });
    if (commands && task) {
      const k = task.key;
      if (task.can.assign && task.assignee?.id !== me.id && match("Giao cho tôi", "assign me"))
        out.push({ id: "t-assign", icon: "user", label: "Giao cho tôi", hint: k, action: () => patch(k, { assignee_id: me.id }) });
      if (task.can.transition && (task.transitions ?? []).some((s) => s.id !== task.status.id) && match("Đổi trạng thái", "status chuyen"))
        out.push({ id: "t-status", icon: "chevronRight", label: "Đổi trạng thái…", hint: k, stage: "status" });
      if (task.can.edit && space && space.board_mode !== "kanban" && task.type !== "epic" && match("Thêm vào sprint", "sprint"))
        out.push({ id: "t-sprint", icon: "flag", label: "Thêm vào sprint…", hint: k, stage: "sprint" });
      if (match("Sao chép link", "copy link url"))
        out.push({
          id: "t-copy",
          icon: "copy",
          label: "Sao chép link",
          hint: k,
          action: async () => {
            try {
              await navigator.clipboard.writeText(`${window.location.origin}/tasks/${encodeURIComponent(k)}`);
            } catch {
              return "Trình duyệt chặn sao chép.";
            }
          },
        });
    }
    if (commands) {
      for (const c of COMMANDS) {
        if (c.needs && !can(me, c.needs)) continue;
        if (!match(c.label, c.words)) continue;
        out.push({ id: `cmd-${c.href}`, href: c.href, icon: c.icon, label: c.label, hint: "Trang" });
      }
      if (tasks && match("Chuyển dự án", "project space du an")) out.push({ id: "go-space", icon: "folder", label: "Chuyển dự án…", hint: "Task", stage: "space" });
      if (tasks && match("Bộ lọc đã lưu", "saved filter loc")) out.push({ id: "go-filter", icon: "filter", label: "Bộ lọc đã lưu…", hint: "Danh sách", stage: "filter" });
    }
    if (search && trimmed.length >= 2) {
      for (const t of found.data?.items ?? []) {
        if (t.key === typedKey) continue;
        out.push({ id: `task-${t.key}`, href: `/tasks/${encodeURIComponent(t.key)}`, icon: "board", label: t.title, task: t });
      }
    }
    return out;
    // The actions close over helpers that only call the API; the lists are what change.
  }, [stage, trimmed, tasks, commands, me, task, space, sprints.data, spaces.data, filters.data, search, found.data]);

  useEffect(() => {
    setActive(0);
    setArmed(null);
  }, [trimmed, stage]);
  const current = Math.min(active, Math.max(options.length - 1, 0));

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  async function patch(key: string, body: Record<string, unknown>): Promise<string | void> {
    await api.patch(`/tasks/${encodeURIComponent(key)}`, body);
  }
  async function transition(key: string, s: TaskStatusRef): Promise<string | void> {
    // Into "done" from here closes it as done (the detail asks for another reason when wanted).
    await api.post(`/tasks/${encodeURIComponent(key)}/transition`, { status_id: s.id, resolution: s.category === "done" ? "done" : undefined });
  }
  async function moveTo(key: string, sprintId: number | null): Promise<string | void> {
    await api.post(`/tasks/${encodeURIComponent(key)}/move`, { sprint_id: sprintId });
  }

  const run = async (o: Option | undefined) => {
    if (!o || busy) return;
    if (o.stage) return enter(o.stage);
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
    setBusy(true);
    setNote(null);
    try {
      const err = await o.action();
      if (err) {
        setNote(err);
        setBusy(false);
        return;
      }
      await refreshTasks();
      onClose();
    } catch (e) {
      setNote(`Lỗi: ${errorText(e)}`);
      setBusy(false);
    }
  };

  const back = () => enter("root");

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

  const searching = !!search && (found.isLoading || query !== trimmed);
  const firstTask = options.findIndex((o) => o.task);
  const firstPage = options.findIndex((o) => o.hint === "Trang");
  const subLoading =
    (stage === "sprint" && !sprints.data && !sprints.error) || (stage === "filter" && !filters.data && !filters.error) || (stage === "space" && !spaces.data && !spaces.error);
  const subError = stage === "sprint" ? sprints.error : stage === "filter" ? filters.error : stage === "space" ? spaces.error : undefined;

  const placeholder =
    stage !== "root"
      ? `${STAGE_TITLE[stage]}: gõ để lọc…`
      : !commands
        ? "Tìm task theo mã (CRM-12) hoặc tiêu đề…"
        : tasks
          ? task
            ? `Lệnh trên ${task.key}, mã task, tìm task hoặc tên trang…`
            : "Gõ mã task (CRM-12), tìm task hoặc tên trang…"
          : "Gõ tên trang…";

  const foot = note
    ? note
    : busy
      ? "Đang chạy…"
      : subError
        ? `Lỗi tải: ${subError.message}`
        : subLoading
          ? "Đang tải…"
          : stage !== "root"
            ? options.length === 0
              ? stage === "sprint"
                ? "Không có sprint nào đang mở khác sprint của task."
                : stage === "filter"
                  ? "Chưa có bộ lọc đã lưu (lưu ở tab Danh sách)."
                  : "Không có mục khớp."
              : "Enter để chọn · Backspace / Esc để quay lại"
            : found.error && search
              ? `Lỗi tìm task: ${found.error.message}`
              : searching
                ? "Đang tìm task…"
                : search && found.data && found.data.items.length === 0 && !options.length
                  ? `Không có task khớp “${query}”`
                  : options.length === 0
                    ? commands
                      ? "Không có lệnh khớp. Thử mã task như CRM-12."
                      : "Gõ ít nhất 2 ký tự để tìm task."
                    : "↑ ↓ để chọn · Enter để mở · Esc để đóng";

  return (
    <div className="cmdk-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown} aria-busy={busy}>
        <h2 id={titleId} className="visually-hidden">
          {stage === "root" ? (commands ? "Bảng lệnh" : "Tìm task") : STAGE_TITLE[stage]}
        </h2>
        <div className="cmdk-field">
          {stage !== "root" ? (
            <button type="button" className="qk-cmdk-back" onClick={back} aria-label="Quay lại">
              <Icon name="chevronLeft" size={16} />
              <span>{STAGE_TITLE[stage]}</span>
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
            aria-label={stage === "root" ? "Tìm task hoặc lệnh" : STAGE_TITLE[stage]}
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
        <ul ref={list} id={listId} role="listbox" aria-label="Kết quả" className="cmdk-list">
          {options.map((o, i) => (
            <li
              key={o.id}
              id={`${listId}-${i}`}
              data-index={i}
              role="option"
              aria-selected={i === current}
              className={`cmdk-item${i === current ? " active" : ""}${i > 0 && (i === firstTask || i === firstPage) ? " cmdk-sep" : ""}${armed === o.id ? " qk-armed" : ""}`}
              onMouseMove={() => i !== current && setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => void run(o)}
            >
              {o.task ? (
                <>
                  <span className="cmdk-key">{o.task.key}</span>
                  <span className="cmdk-label">{o.task.title}</span>
                  <StatusPill status={o.task.status} resolution={o.task.resolution} />
                </>
              ) : (
                <>
                  <Icon name={o.icon} size={16} />
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
