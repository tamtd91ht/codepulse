"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/icons";
import { StatusPill } from "@/components/tasks/taskParts";
import { can, useApi } from "@/lib/api";
import type { Me, TaskRow } from "@/lib/types";

/*
 * Command palette (docs/specs/31 §19.3): Ctrl+K / Cmd+K from anywhere. Type a task key to open it,
 * any text to search tasks, or a page name to go there. Mounted once in AppShell for signed-in users.
 */

type Option = { id: string; href: string; icon: IconName; label: string; hint?: string; task?: TaskRow };

const COMMANDS: { label: string; href: string; icon: IconName; needs?: string; words?: string }[] = [
  { label: "Việc của tôi", href: "/tasks/me", icon: "user", needs: "tasks.view", words: "my work viec cua toi" },
  { label: "Task", href: "/tasks", icon: "board", needs: "tasks.view", words: "board backlog" },
  { label: "Tạo task", href: "/tasks?create=1", icon: "plus", needs: "tasks.create", words: "tao task create new" },
  { label: "Tổng quan", href: "/", icon: "activity", words: "tong quan overview home" },
  { label: "Review", href: "/reviews", icon: "review", words: "finding mr" },
  { label: "Testcase", href: "/testcases", icon: "clipboardCheck", needs: "tests.view", words: "test case" },
];

const KEY_RE = /^([a-z][a-z0-9]*)-(\d+)$/i;

/** Lower-case, accents removed: "Tạo" matches "tao". */
const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();

export function CommandPalette({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return open ? <Palette me={me} onClose={() => setOpen(false)} /> : null;
}

function Palette({ me, onClose }: { me: Me; onClose: () => void }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();
  const titleId = useId();
  const tasks = can(me, "tasks.view");

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

  const search = tasks && query.length >= 2 ? `/tasks?${new URLSearchParams({ q: query, limit: "8", sort: "updated", order: "desc" })}` : null;
  const found = useApi<{ items: TaskRow[]; total: number }>(search, { keepPreviousData: true });

  const trimmed = text.trim();
  const options = useMemo<Option[]>(() => {
    const out: Option[] = [];
    const m = KEY_RE.exec(trimmed);
    const typedKey = m ? `${m[1].toUpperCase()}-${m[2]}` : null;
    if (typedKey && tasks) out.push({ id: `key-${typedKey}`, href: `/tasks/${encodeURIComponent(typedKey)}`, icon: "external", label: `Mở ${typedKey}` });
    const words = fold(trimmed);
    for (const c of COMMANDS) {
      if (c.needs && !can(me, c.needs)) continue;
      if (words && !fold(`${c.label} ${c.words ?? ""}`).includes(words)) continue;
      out.push({ id: `cmd-${c.href}`, href: c.href, icon: c.icon, label: c.label, hint: "Trang" });
    }
    if (search && trimmed.length >= 2) {
      for (const t of found.data?.items ?? []) {
        if (t.key === typedKey) continue;
        out.push({ id: `task-${t.key}`, href: `/tasks/${encodeURIComponent(t.key)}`, icon: "board", label: t.title, task: t });
      }
    }
    return out;
  }, [trimmed, tasks, me, search, found.data]);

  useEffect(() => setActive(0), [trimmed]);
  const current = Math.min(active, Math.max(options.length - 1, 0));

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const run = (o: Option | undefined) => {
    if (!o) return;
    onClose();
    router.push(o.href);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (options.length) setActive((current + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (options.length) setActive((current - 1 + options.length) % options.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(options[current]);
    } else if (e.key === "Tab") {
      // The input is the only stop: focus stays in the dialog.
      e.preventDefault();
      input.current?.focus();
    }
  };

  const searching = !!search && (found.isLoading || query !== trimmed);
  const firstTask = options.findIndex((o) => o.task);

  return (
    <div className="cmdk-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown}>
        <h2 id={titleId} className="visually-hidden">
          Bảng lệnh
        </h2>
        <div className="cmdk-field">
          <Icon name="search" size={18} />
          <input
            ref={input}
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={tasks ? "Gõ mã task (CRM-12), tìm task hoặc tên trang…" : "Gõ tên trang…"}
            aria-label="Tìm task hoặc lệnh"
            role="combobox"
            aria-expanded={options.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={options[current] ? `${listId}-${current}` : undefined}
            autoComplete="off"
            spellCheck={false}
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
              className={`cmdk-item${i === current ? " active" : ""}${i === firstTask ? " cmdk-sep" : ""}`}
              onMouseMove={() => i !== current && setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => run(o)}
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
                </>
              )}
            </li>
          ))}
        </ul>
        <div className="cmdk-foot small muted" role="status" aria-live="polite">
          {found.error && search
            ? `Lỗi tìm task: ${found.error.message}`
            : searching
              ? "Đang tìm task…"
              : search && found.data && found.data.items.length === 0
                ? `Không có task khớp “${query}”`
                : options.length === 0
                  ? "Không có lệnh khớp. Thử mã task như CRM-12."
                  : "↑ ↓ để chọn · Enter để mở · Esc để đóng"}
        </div>
      </div>
    </div>
  );
}
