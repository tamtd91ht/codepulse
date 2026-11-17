"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { TaskBacklog } from "@/components/tasks/TaskBacklog";
import { TaskBoard } from "@/components/tasks/TaskBoard";
import { TaskCreate } from "@/components/tasks/TaskCreate";
import { TaskDrawer } from "@/components/tasks/TaskDrawer";
import { TaskHealth } from "@/components/tasks/TaskHealth";
import { TaskList } from "@/components/tasks/TaskList";
import { TaskReports } from "@/components/tasks/TaskReports";
import { TaskTimeline } from "@/components/tasks/TaskTimeline";
import { EnableSpace, TaskSettings } from "@/components/tasks/TaskSettings";
import { type FrameMenuItem, type FrameTab, ProjectSwitcher, TaskFrame } from "@/components/tasks/TaskFrame";
import { TaskVersions } from "@/components/tasks/TaskVersions";
import { useTaskSpaces } from "@/components/tasks/taskParts";
import { EmptyState, Loading, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/*
 * Task (docs/specs/31 §13): a Jira-like tracker per dự án whose tasks know their git — branches,
 * commits, MRs, review scores, deployments. The URL keeps the dự án, the tab and the open task
 * (?space=&tab=&task=KEY), so every view can be linked; the last dự án is remembered per browser.
 */

type Tab = "board" | "backlog" | "timeline" | "list" | "reports" | "health" | "versions" | "settings";
// The frame's order and digits (UI/UX §3.1, §7: 1…6); Timeline, not in that list, comes last on 7.
// Cài đặt is the ⚙ menu at the end of the row, not a tab (§3.2).
const TABS: (FrameTab & { key: Tab })[] = [
  { key: "board", label: "Board", icon: "board", digit: 1 },
  { key: "backlog", label: "Backlog", icon: "inbox", digit: 2 },
  { key: "list", label: "Danh sách", icon: "file", digit: 3 },
  { key: "reports", label: "Báo cáo", icon: "pulse", digit: 4 },
  { key: "health", label: "Sức khoẻ", icon: "stethoscope", digit: 5 },
  { key: "versions", label: "Phiên bản", icon: "flag", digit: 6 },
  { key: "timeline", label: "Timeline", icon: "calendar", digit: 7 },
];
const TAB_KEYS: Tab[] = [...TABS.map((t) => t.key), "settings"];
const HELP = "Việc của dự án gắn với nhánh, commit, MR và điểm review: trạng thái chạy theo git";
const LAST_SPACE = "cp:tasks:space";

type Where = { space: number | null; tab: Tab; task: string | null };

function remembered(): number | null {
  try {
    const v = window.localStorage.getItem(LAST_SPACE);
    return v && /^\d+$/.test(v) ? Number(v) : null;
  } catch {
    return null;
  }
}

export default function TasksPage() {
  return (
    <Suspense fallback={<Loading rows={6} />}>
      <Tasks />
    </Suspense>
  );
}

function Tasks() {
  const params = useSearchParams();
  const where: Where = useMemo(() => {
    const s = params.get("space");
    const tab = TAB_KEYS.find((t) => t === params.get("tab")) ?? "board";
    const task = params.get("task");
    return { space: s && /^\d+$/.test(s) ? Number(s) : null, tab, task: task && /^[A-Za-z][A-Za-z0-9]*-\d+$/.test(task) ? task.toUpperCase() : null };
  }, [params]);
  const go = useCallback(
    (next: Partial<Where>) => {
      const w = { ...where, ...next };
      // The FilterBar's own params (f, fq, v: docs/specs/31 §22.2) stay while the screen does.
      const p = new URLSearchParams();
      if (w.space === where.space && w.tab === where.tab) {
        const now = new URLSearchParams(window.location.search);
        for (const k of ["f", "fq", "v"]) {
          const v = now.get(k);
          if (v !== null) p.set(k, v);
        }
      }
      if (w.space) p.set("space", String(w.space));
      if (w.tab !== "board") p.set("tab", w.tab);
      if (w.task) p.set("task", w.task);
      window.history.replaceState(null, "", `${window.location.pathname}${p.size ? `?${p}` : ""}`);
    },
    [where],
  );

  const { data: me } = useApi<Me>("/auth/me");
  const allowed = me ? can(me, "tasks.view") : false;
  const spaces = useTaskSpaces();
  const [mounted, setMounted] = useState(false);
  const [creating, setCreating] = useState(false);
  useEffect(() => setMounted(true), []);

  const spaceId = useMemo(() => {
    const list = spaces.data ?? [];
    if (!list.length) return null;
    const ok = (id: number | null) => (id && list.some((s) => s.business_project_id === id) ? id : null);
    return ok(where.space) ?? ok(mounted ? remembered() : null) ?? (list.find((s) => s.enabled) ?? list[0]).business_project_id;
  }, [spaces.data, where.space, mounted]);
  const space = spaces.data?.find((s) => s.business_project_id === spaceId) ?? null;
  // `?create=1` (the Ctrl+K "Tạo task" command, §19.3): open the create form once, then drop it.
  const wantCreate = params.get("create") === "1";
  useEffect(() => {
    if (!wantCreate || !space) return;
    if (space.enabled && space.can_create) setCreating(true);
    go({});
  }, [wantCreate, space, go]);

  useEffect(() => {
    if (!spaceId) return;
    try {
      window.localStorage.setItem(LAST_SPACE, String(spaceId));
    } catch {
      /* per-viewer convenience only */
    }
  }, [spaceId]);

  if (me && !allowed)
    return (
      <>
        <PageHeader title="Task" />
        <EmptyState icon="alert" title="Bạn chưa có quyền xem task">
          Nhờ admin cấp quyền “Task › Xem task” trong bộ quyền.
        </EmptyState>
      </>
    );

  // Kanban (§19.6) has no Backlog tab: the Board already shows every open task.
  const tabs = TABS.filter((t) => t.key !== "backlog" || space?.board_mode !== "kanban");
  const tab: Tab = (where.tab === "settings" ? !!space?.can_manage : tabs.some((t) => t.key === where.tab)) ? where.tab : "board";
  const openTask = (key: string) => go({ task: key });
  // ⚙ (§3.2, §4.10): the dự án's settings and Tình trạng bật Task, both rarely used.
  const gear: FrameMenuItem[] = [
    ...(space?.enabled && space.can_manage
      ? [{ key: "settings", label: "Cài đặt Task của dự án", icon: "settings" as const, active: tab === "settings", run: () => go({ tab: "settings" }) }]
      : []),
    ...(can(me, "tasks.manage") || can(me, "admin.projects") ? [{ key: "adoption", label: "Tình trạng bật Task", icon: "pulse" as const, href: "/tasks/adoption" }] : []),
  ];

  return (
    <>
    <TaskFrame
      crumb={<ProjectSwitcher spaces={spaces.data} value={space} onChange={(id) => go({ space: id, task: null })} />}
      help={HELP}
      tabs={space?.enabled ? tabs : undefined}
      tab={tab}
      onTab={(key) => go({ tab: key as Tab })}
      menu={gear}
      settingsActive={tab === "settings"}
      create={space?.enabled && space.can_create ? { run: () => setCreating(true) } : null}
    >
      {!spaces.data ? (
        <Loading error={spaces.error} rows={6} />
      ) : !space ? (
        <EmptyState icon="inbox" title="Chưa có dự án nào dùng Task">
          Quản lý dự án bật Task cho dự án của mình tại đây.
        </EmptyState>
      ) : !space.enabled ? (
        space.can_manage ? (
          <EnableSpace key={space.business_project_id} space={space} onDone={() => go({ tab: "board" })} />
        ) : (
          <EmptyState icon="inbox" title={`${space.name} chưa bật Task`} />
        )
      ) : (
        <>
          {tab === "board" ? (
            <TaskBoard key={space.business_project_id} space={space} me={me} onOpen={openTask} />
          ) : tab === "backlog" ? (
            <TaskBacklog key={space.business_project_id} space={space} onOpen={openTask} />
          ) : tab === "timeline" ? (
            <TaskTimeline key={space.business_project_id} space={space} onOpen={openTask} />
          ) : tab === "list" ? (
            <TaskList key={space.business_project_id} space={space} me={me} onOpen={openTask} />
          ) : tab === "reports" ? (
            <TaskReports key={space.business_project_id} space={space} />
          ) : tab === "health" ? (
            <TaskHealth key={space.business_project_id} space={space} />
          ) : tab === "versions" ? (
            <TaskVersions key={space.business_project_id} space={space} />
          ) : (
            <TaskSettings space={space} />
          )}
        </>
      )}
    </TaskFrame>
      {creating && space && (
        <TaskCreate
          space={space}
          onClose={() => setCreating(false)}
          onCreated={(key) => {
            setCreating(false);
            go({ task: key });
          }}
        />
      )}
      {where.task && <TaskDrawer taskKey={where.task} onOpenTask={openTask} onClose={() => go({ task: null })} onDeleted={() => go({ task: null })} />}
    </>
  );
}

