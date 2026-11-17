"use client";

import Link from "next/link";

import { Card, EmptyState, Loading, PageHeader } from "@/components/ui";
import { can, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Me, TaskAdoption, UsageSummary } from "@/lib/types";

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
      {me?.is_admin && <UsageSection />}
    </>
  );
}

/* -- Số liệu sử dụng (docs/specs/31 §21.2): admins only, anonymous (role, event, surface). -------- */

const EVENT_LABEL: Record<string, string> = {
  page_open: "Mở trang",
  status_change: "Đổi trạng thái",
  task_create: "Tạo task",
  palette: "Ctrl+K",
  shortcut: "Phím tắt",
  rule_undone: "Quy tắc bị hoàn tác",
};
const SURFACE_LABEL: Record<string, string> = {
  my_work: "Việc của tôi",
  board: "Board",
  backlog: "Backlog",
  timeline: "Timeline",
  list: "Danh sách",
  reports: "Báo cáo",
  task_detail: "Chi tiết task",
  health: "Sức khoẻ",
  versions: "Phiên bản",
  overview: "Tổng quan",
  drag: "Kéo thả",
  palette: "Ctrl+K",
  detail: "Chi tiết task",
  bulk: "Sửa hàng loạt",
  git: "Git",
  rule: "Quy tắc tự động",
  mcp: "MCP",
  ai: "AI",
  telegram: "Telegram",
  web: "Web (khác)",
  button: "Nút Tạo / tạo nhanh",
  ai_breakdown: "AI tách việc",
  failed_test: "Từ test Failed",
  jira_import: "Nhập Jira",
  open: "Mở bảng lệnh",
  search_task: "Tìm task",
  navigate: "Điều hướng",
  command: "Chạy lệnh",
  key: "Phím một chữ",
};
const ROLE_LABEL: Record<string, string> = {
  member: "Thành viên",
  lead: "Lead",
  manager: "Manager",
  project_manager: "Quản lý dự án",
  manage_all: "Quản lý mọi dự án",
  admin: "Admin",
  system: "Hệ thống (git, quy tắc)",
};

const pct = (share: number | null) => (share === null ? "—" : `${Math.round(share * 100)}%`);
const dayMonth = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;

function UsageSection() {
  const got = useApi<UsageSummary>("/usage/summary?weeks=8");
  const data = got.data;
  const recent = data ? data.weeks.slice(-4) : [];
  const offset = data ? data.weeks.length - recent.length : 0;
  return (
    <Card
      flush
      title="Số liệu sử dụng"
      subtitle={`Ẩn danh: chỉ vai trò, sự kiện, nơi dùng và thời điểm; không lưu người hay nội dung task. Giữ ${data?.keep_days ?? 180} ngày.`}
    >
      {got.error ? (
        <div className="tp-error">
          <Loading error={got.error} />
          <button type="button" className="ghost sm" onClick={() => void got.mutate()}>
            Thử lại
          </button>
        </div>
      ) : !data ? (
        <Loading rows={5} />
      ) : !data.events.length ? (
        <EmptyState icon="inbox" title="Chưa có số liệu">
          Số liệu xuất hiện khi mọi người mở Task, đổi trạng thái, tạo task hoặc dùng Ctrl+K.
        </EmptyState>
      ) : (
        <div className="us-body">
          <div className="us-kpis">
            <div className={`us-kpi${data.automation.share !== null && data.automation.share >= data.automation.target ? " us-kpi-good" : ""}`}>
              <span className="us-kpi-label">Đổi trạng thái bởi git + quy tắc</span>
              <strong className="us-kpi-value">{pct(data.automation.share)}</strong>
              <span className="small muted">
                {data.automation.automated} / {data.automation.total} lần · mục tiêu ≥ {pct(data.automation.target)}
              </span>
            </div>
            <div className="us-kpi">
              <span className="us-kpi-label">Dùng Ctrl+K / phím tắt</span>
              <strong className="us-kpi-value">{pct(data.keyboard.share)}</strong>
              <span className="small muted">
                {data.keyboard.keyboard} / {data.keyboard.total} thao tác (so với số lần mở trang)
              </span>
            </div>
          </div>
          <div className="table-wrap">
            <table className="tp-table us-table">
              <caption className="visually-hidden">Số lần theo sự kiện và nơi dùng, 4 tuần gần nhất</caption>
              <thead>
                <tr>
                  <th scope="col">Sự kiện</th>
                  <th scope="col">Nơi</th>
                  {recent.map((w) => (
                    <th key={w} scope="col" className="num" title={`Tuần từ ${w}`}>
                      {dayMonth(w)}
                    </th>
                  ))}
                  <th scope="col" className="num">
                    8 tuần
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={`${e.event}:${e.surface}`}>
                    <td>{EVENT_LABEL[e.event] ?? e.event}</td>
                    <td>{SURFACE_LABEL[e.surface] ?? e.surface}</td>
                    {e.counts.slice(offset).map((n, i) => (
                      <td key={i} className="num">
                        {n || <span className="tp-muted">0</span>}
                      </td>
                    ))}
                    <td className="num">
                      <strong>{e.total}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table className="tp-table us-table">
              <caption className="visually-hidden">Số sự kiện theo vai trò, 4 tuần gần nhất</caption>
              <thead>
                <tr>
                  <th scope="col">Vai trò</th>
                  {recent.map((w) => (
                    <th key={w} scope="col" className="num" title={`Tuần từ ${w}`}>
                      {dayMonth(w)}
                    </th>
                  ))}
                  <th scope="col" className="num">
                    8 tuần
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.roles.map((r) => (
                  <tr key={r.role}>
                    <td>{ROLE_LABEL[r.role] ?? r.role}</td>
                    {r.counts.slice(offset).map((n, i) => (
                      <td key={i} className="num">
                        {n || <span className="tp-muted">0</span>}
                      </td>
                    ))}
                    <td className="num">
                      <strong>{r.total}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}
