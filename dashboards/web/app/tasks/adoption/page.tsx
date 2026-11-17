"use client";

import Link from "next/link";

import { Card, EmptyState, Loading, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Me, TaskAdoption } from "@/lib/types";

/*
 * Tình trạng bật Task (docs/specs/31 §20.4): one row per dự án — Task on or not, the template it
 * uses, tasks, open tasks, last activity (a task changed or commented) and how many people with
 * access to the dự án may see Task. Admins also see the dự án without Task, with a link to turn it
 * on; those who manage Task see the dự án they can view that have it on. Read-only.
 */

export default function TaskAdoptionPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const allowed = !me || can(me, "tasks.manage") || can(me, "admin.projects");
  const got = useApi<TaskAdoption>(allowed ? "/tasks/adoption" : null);

  if (!allowed)
    return (
      <>
        <PageHeader title="Tình trạng bật Task" />
        <EmptyState icon="alert" title="Cần quyền quản lý Task hoặc quản trị dự án" />
      </>
    );

  const rows = got.data?.rows ?? [];
  const on = rows.filter((r) => r.enabled).length;
  return (
    <>
      <PageHeader
        eyebrow={<Link href="/tasks">Task</Link>}
        title="Tình trạng bật Task"
        subtitle="Dự án nào đã dùng Task, theo mẫu nào, có bao nhiêu việc và hoạt động gần nhất khi nào."
      />
      <Card
        flush
        title="Dự án"
        subtitle={
          got.data
            ? got.data.all_projects
              ? `${on} / ${rows.length} dự án đã bật Task.`
              : `${on} dự án bạn xem được đã bật Task.`
            : undefined
        }
      >
        {got.error ? (
          <div className="tp-error">
            <Loading error={got.error} />
            <button type="button" className="ghost sm" onClick={() => void got.mutate()}>
              Thử lại
            </button>
          </div>
        ) : !got.data ? (
          <Loading rows={5} />
        ) : !rows.length ? (
          <EmptyState icon="inbox" title="Chưa có dự án nào dùng Task">
            Dự án bật Task và bạn xem được sẽ hiện ở đây.
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="tp-table">
              <thead>
                <tr>
                  <th>Dự án</th>
                  <th>Task</th>
                  <th>Mẫu</th>
                  <th className="num">Số task</th>
                  <th className="num">Đang mở</th>
                  <th>Hoạt động gần nhất</th>
                  <th className="num" title="Người có quyền vào dự án và giữ quyền Xem task">
                    Người xem được
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.business_project_id}>
                    <td>
                      {r.enabled ? (
                        <Link href={`/tasks?space=${r.business_project_id}`} className="tp-row-link">
                          {r.name} <span className="mono tp-muted">{r.key}</span>
                        </Link>
                      ) : (
                        r.name
                      )}
                    </td>
                    <td>
                      {r.enabled ? (
                        <span className="tp-badge tp-badge-on">Đã bật</span>
                      ) : (
                        <span className="tp-off">
                          <span className="tp-badge">Chưa bật</span>
                          <Link href={`/tasks?space=${r.business_project_id}`} className="tp-enable">
                            Bật Task
                          </Link>
                        </span>
                      )}
                    </td>
                    <td>{r.enabled ? (r.template_name ?? <span className="tp-muted" title="Cấu hình riêng của dự án (bật trước khi có mẫu hoặc tự chỉnh)">Tuỳ chỉnh</span>) : <span className="tp-muted">—</span>}</td>
                    <td className="num">{r.tasks}</td>
                    <td className="num">{r.open}</td>
                    <td>{r.last_activity ? dateTime(r.last_activity) : <span className="tp-muted">Chưa có</span>}</td>
                    <td className="num">{r.viewers}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
