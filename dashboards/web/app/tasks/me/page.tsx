"use client";

import Link from "next/link";

import { MyWork } from "@/components/tasks/MyWork";
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
    <>
      <PageHeader
        eyebrow={<Link href="/tasks">Task</Link>}
        title="Việc của tôi"
        subtitle="Việc cần xử lý ngay, task của bạn theo trạng thái và tiến độ sprint; tự làm mới mỗi phút"
      />
      <MyWork />
    </>
  );
}
