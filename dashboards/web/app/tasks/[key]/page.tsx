"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use } from "react";

import { TaskDetail } from "@/components/tasks/TaskDetail";
import { EmptyState, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/**
 * One task on its own page (docs/specs/31 §13, §22.6): the same view as the drawer of /tasks?task=KEY,
 * in two columns. No big "Task / KEY" title: the key sits in the breadcrumb, the task's title is the
 * page's h1, pinned with its health block; the field column stays in view while scrolling.
 */
export default function TaskPage({ params }: { params: Promise<{ key: string }> }) {
  const { key: raw } = use(params);
  const key = decodeURIComponent(raw).toUpperCase();
  const router = useRouter();
  const { data: me } = useApi<Me>("/auth/me");
  if (me && !can(me, "tasks.view"))
    return (
      <>
        <PageHeader title="Task" />
        <EmptyState icon="alert" title="Bạn chưa có quyền xem task" />
      </>
    );
  return (
    <>
      <nav className="td-crumbs small" aria-label="Đường dẫn">
        <Link href="/tasks">Task</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page" className="td-crumbs-key">
          {key}
        </span>
      </nav>
      <div className="card tk-page-card">
        <TaskDetail key={key} taskKey={key} mode="page" onOpenTask={(k) => router.push(`/tasks/${encodeURIComponent(k)}`)} onDeleted={() => router.push("/tasks")} />
      </div>
    </>
  );
}
