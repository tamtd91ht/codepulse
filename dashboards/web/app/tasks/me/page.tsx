"use client";

import { MyWork } from "@/components/tasks/MyWork";
import { TaskFrame } from "@/components/tasks/TaskFrame";
import { EmptyState, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/** "Việc của tôi" (docs/specs/31 §19.3): what needs me now, my tasks by state, my sprints. */
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
      help="Việc cần xử lý ngay, task của bạn theo trạng thái và tiến độ sprint; tự làm mới mỗi phút"
      menu={can(me, "tasks.manage") || can(me, "admin.projects") ? [{ key: "adoption", label: "Tình trạng bật Task", icon: "pulse", href: "/tasks/adoption" }] : undefined}
      mine="current"
      create={can(me, "tasks.create") ? { href: "/tasks?create=1" } : null}
    >
      <MyWork />
    </TaskFrame>
  );
}
