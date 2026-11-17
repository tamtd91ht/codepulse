"use client";

import Link from "next/link";

import { Icon } from "@/components/icons";
import { MyWork } from "@/components/tasks/MyWork";
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
    <>
      <header className="mw-head">
        <nav aria-label="Đường dẫn" className="mw-crumb">
          <Link href="/tasks">Task</Link>
          <span aria-hidden="true">/</span>
          <h1>Việc của tôi</h1>
        </nav>
        <span className="mw-help" tabIndex={0} role="note" aria-label={ABOUT} title={ABOUT}>
          <Icon name="info" size={14} />
        </span>
      </header>
      <MyWork />
    </>
  );
}
