"use client";

import Link from "next/link";

import { TaskNotifyPrefs } from "@/components/tasks/TaskNotifyPrefs";
import { EmptyState, Loading, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

/*
 * Cài đặt thông báo (docs/specs/31 §21.2), from the account menu: one's own channels per event,
 * quiet hours and weekends, the daily summary; admins also set the organisation's defaults (used by
 * everyone who has not saved their own).
 */
export default function NotificationSettingsPage() {
  const { data: me, error } = useApi<Me>("/auth/me");
  return (
    <>
      <PageHeader
        eyebrow={<Link href="/notifications">Thông báo</Link>}
        title="Cài đặt thông báo"
        subtitle="Nhận gì, qua kênh nào, khi nào: cho từng sự kiện về task."
      />
      {!me ? (
        <Loading error={error} rows={6} />
      ) : !can(me, "tasks.view") && !me.is_admin ? (
        <EmptyState icon="bell" title="Chưa có thông báo Task để chỉnh">
          Cài đặt này dành cho người dùng Task. Thông báo review code luôn vào hộp thư.
        </EmptyState>
      ) : (
        <div className="ns-page">
          <TaskNotifyPrefs scope="me" />
          {me.is_admin && <TaskNotifyPrefs scope="org" />}
        </div>
      )}
    </>
  );
}
