"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { TaskBacklog } from "@/components/tasks/TaskBacklog";
import { TaskBoard } from "@/components/tasks/TaskBoard";
import { TaskCreate } from "@/components/tasks/TaskCreate";
import { TaskDetail } from "@/components/tasks/TaskDetail";
import { TaskHealth } from "@/components/tasks/TaskHealth";
import { TaskList } from "@/components/tasks/TaskList";
import { TaskReports } from "@/components/tasks/TaskReports";
import { EnableSpace, TaskSettings } from "@/components/tasks/TaskSettings";
import { TaskVersions } from "@/components/tasks/TaskVersions";
import { useTaskSpaces } from "@/components/tasks/taskParts";
import { Drawer, EmptyState, Loading, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/*
 * Task (docs/specs/31 §13): a Jira-like tracker per dự án whose tasks know their git — branches,
 * commits, MRs, review scores, deployments. The URL keeps the dự án, the tab and the open task
 * (?space=&tab=&task=KEY), so every view can be linked; the last dự án is remembered per browser.
 */

type Tab = "board" | "backlog" | "list" | "reports" | "health" | "versions" | "settings";
const TABS: { key: Tab; label: string; icon: "board" | "inbox" | "file" | "pulse" | "stethoscope" | "flag" | "settings" }[] = [
  { key: "board", label: "Board", icon: "board" },
  { key: "backlog", label: "Backlog", icon: "inbox" },
  { key: "list", label: "Danh sách", icon: "file" },
  { key: "reports", label: "Báo cáo", icon: "pulse" },
  { key: "health", label: "Sức khoẻ sprint", icon: "stethoscope" },
  { key: "versions", label: "Phiên bản", icon: "flag" },
  { key: "settings", label: "Cài đặt", icon: "settings" },
];
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
    const tab = TABS.find((t) => t.key === params.get("tab"))?.key ?? "board";
    const task = params.get("task");
    return { space: s && /^\d+$/.test(s) ? Number(s) : null, tab, task: task && /^[A-Za-z][A-Za-z0-9]*-\d+$/.test(task) ? task.toUpperCase() : null };
  }, [params]);
  const go = useCallback(
    (next: Partial<Where>) => {
      const w = { ...where, ...next };
      const p = new URLSearchParams();
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

  const tabs = TABS.filter((t) => t.key !== "settings" || !!space?.can_manage);
  const tab = tabs.some((t) => t.key === where.tab) ? where.tab : "board";
  const openTask = (key: string) => go({ task: key });

  return (
    <>
      <PageHeader
        title="Task"
        subtitle="Việc của dự án gắn với nhánh, commit, MR và điểm review: trạng thái chạy theo git"
        actions={
          <>
            <label className="adm-field tc-project">
              <span className="small muted">Dự án</span>
              <select value={spaceId ?? ""} onChange={(e) => go({ space: Number(e.target.value), task: null })} disabled={!spaces.data}>
                {(spaces.data ?? []).map((s) => (
                  <option key={s.business_project_id} value={s.business_project_id}>
                    {s.name}
                    {s.key ? ` (${s.key})` : " · chưa bật"}
                  </option>
                ))}
              </select>
            </label>
            {space?.enabled && space.can_create && (
              <button type="button" className="primary tk-create-btn" onClick={() => setCreating(true)}>
                <Icon name="plus" size={16} /> Tạo task
              </button>
            )}
          </>
        }
      />
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
          <nav className="tabs page-tabs" aria-label="Task">
            {tabs.map((t) => (
              <button key={t.key} type="button" className={tab === t.key ? "active" : undefined} aria-current={tab === t.key ? "page" : undefined} onClick={() => go({ tab: t.key })}>
                <Icon name={t.icon} size={16} />
                {t.label}
              </button>
            ))}
          </nav>
          {tab === "board" ? (
            <TaskBoard key={space.business_project_id} space={space} me={me} onOpen={openTask} />
          ) : tab === "backlog" ? (
            <TaskBacklog key={space.business_project_id} space={space} onOpen={openTask} />
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
      {where.task && (
        <Drawer
          width="wide"
          title={where.task}
          onClose={() => go({ task: null })}
          actions={
            <Link href={`/tasks/${encodeURIComponent(where.task)}`} className="icon-btn" aria-label="Mở trang riêng" data-tip="Mở trang riêng">
              <Icon name="external" size={16} />
            </Link>
          }
        >
          <TaskDetail key={where.task} taskKey={where.task} onOpenTask={openTask} onDeleted={() => go({ task: null })} />
        </Drawer>
      )}
    </>
  );
}

