"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useUndo } from "@/components/UndoToast";
import { api } from "@/lib/api";

import { errorText, refreshTasks } from "./taskParts";

/*
 * "Đã tạo OCX-12 · Mở · Hoàn tác" for 10 seconds after any task is created (docs/specs/31 §22.10):
 * the create form (C, N, "+ Tạo", sub-task from the palette) and the Board's quick-create announce
 * the key with `announceTaskCreated`; one host, mounted with the command palette on every page,
 * shows it. "Mở" opens the task in the drawer on /tasks (?task=KEY), its page elsewhere;
 * "Hoàn tác" asks POST /tasks/{key}/undo-create (the creator, within 2 minutes, nothing added).
 */

export const TASK_CREATED_EVENT = "cp:task-created";

export function announceTaskCreated(key: string) {
  window.dispatchEvent(new CustomEvent<{ key: string }>(TASK_CREATED_EVENT, { detail: { key } }));
}

function openTask(router: ReturnType<typeof useRouter>, key: string) {
  if (window.location.pathname === "/tasks") {
    const q = new URLSearchParams(window.location.search);
    q.set("task", key);
    router.push(`/tasks?${q.toString()}`);
  } else router.push(`/tasks/${encodeURIComponent(key)}`);
}

/** After an undo, nothing on screen still points at the task. */
function leaveTask(router: ReturnType<typeof useRouter>, key: string) {
  const { pathname, search } = window.location;
  if (pathname === `/tasks/${encodeURIComponent(key)}` || pathname === `/tasks/${key}`) {
    router.replace("/tasks");
    return;
  }
  const q = new URLSearchParams(search);
  if (pathname === "/tasks" && q.get("task") === key) {
    q.delete("task");
    router.replace(`/tasks${q.size ? `?${q.toString()}` : ""}`);
  }
}

export function TaskCreatedToast() {
  const router = useRouter();
  const [element, offer] = useUndo();
  useEffect(() => {
    const onCreated = (e: Event) => {
      const key = (e as CustomEvent<{ key?: string }>).detail?.key;
      if (!key) return;
      offer({
        text: `Đã tạo ${key}`,
        action: { label: "Mở", run: () => openTask(router, key) },
        undo: async () => {
          try {
            await api.post(`/tasks/${encodeURIComponent(key)}/undo-create`, {});
          } catch (err) {
            throw new Error(errorText(err));
          }
          leaveTask(router, key);
          await refreshTasks();
        },
      });
    };
    window.addEventListener(TASK_CREATED_EVENT, onCreated);
    return () => window.removeEventListener(TASK_CREATED_EVENT, onCreated);
  }, [offer, router]);
  return <div className="tc-created">{element}</div>;
}
