"use client";

import { Suspense } from "react";

import { MyWork } from "@/components/tasks/MyWork";
import { UrlTaskDrawer } from "@/components/tasks/TaskDrawer";
import { TaskFrame } from "@/components/tasks/TaskFrame";
import { EmptyState, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

const ABOUT = "Việc cần xử lý ngay, task của bạn theo trạng thái và tiến độ sprint; tự làm mới mỗi phút";

/** "Việc của tôi" (docs/specs/31 §19.3, §22.7): a 48px breadcrumb header, no subtitle. */
export default function MyWorkPage() {
  const { data: me } = useApi<Me>("/auth/me");
  if (me && !can(me, "tasks.view"))
    return (
      <>
        <PageHeader title="Việc của tôi" />
        <EmptyState icon="alert" title="Bạn chưa có quyền xem task">
          Nhờ admin cấp quyền “Task › Xem task” trong bộ quyền.
        </EmptyState>
      </>
    );
  return (
    <TaskFrame
      crumb="Việc của tôi"
      help={ABOUT}
      menu={can(me, "tasks.manage") || can(me, "admin.projects") ? [{ key: "adoption", label: "Tình trạng bật Task", icon: "pulse", href: "/tasks/adoption" }] : undefined}
      mine="current"
      create={can(me, "tasks.create") ? { href: "/tasks?create=1" } : null}
    >
      <MyWork />
      {/* §22.6: a task opened from here (?task=KEY) shows in the right drawer, the list stays. */}
      <Suspense fallback={null}>
        <UrlTaskDrawer />
      </Suspense>
    </TaskFrame>
  );
}
