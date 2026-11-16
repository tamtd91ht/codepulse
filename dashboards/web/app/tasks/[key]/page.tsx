"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use } from "react";

import { TaskDetail } from "@/components/tasks/TaskDetail";
import { EmptyState, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/** One task on its own page (docs/specs/31 §13): the same view as the drawer of /tasks?task=KEY. */
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
      <PageHeader eyebrow={<Link href="/tasks">Task</Link>} title={key} />
      <div className="card tk-page-card">
        <TaskDetail key={key} taskKey={key} onOpenTask={(k) => router.push(`/tasks/${encodeURIComponent(k)}`)} onDeleted={() => router.push("/tasks")} />
      </div>
    </>
  );
}
