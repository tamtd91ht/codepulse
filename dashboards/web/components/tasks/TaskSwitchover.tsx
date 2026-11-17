"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Alert, Card, Loading, Message, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { dateTime, relative } from "@/lib/format";
import type { TaskAliasUse, TaskChecklistItem, TaskDemoCreated, TaskDemoDeleted, TaskSpace, TaskSwitchoverState } from "@/lib/types";

import { errorText, refreshTasks, useConfirm } from "./taskParts";
import { MembersStep } from "./TaskTemplates";

/* Chuyển đổi (docs/specs/31 §21.6, tasks.manage): the Lead checklist, old Jira keys still used in git,
 * "Ngừng dùng Jira" and demo data for a training session. Mounted at the end of Task › Cài đặt;
 * SwitchoverBoardNote shows the unfinished checklist on the Board (dismissible per browser). */

const path = (bp: number) => `/tasks/spaces/${bp}/switchover`;

/** Whether the dự án stopped using Jira (the Jira import card hides importing). Managers only. */
export function useJiraRetired(space: TaskSpace): boolean {
  const got = useApi<TaskSwitchoverState>(space.enabled && space.can_manage ? path(space.business_project_id) : null);
  return !!got.data?.jira_retired_at;
}

const FIX_ANCHOR: Partial<Record<TaskChecklistItem["fix"], string>> = {
  statuses: "tk-set-statuses",
  dod: "tk-set-dod",
  automation: "tk-set-automation",
  jira: "tk-set-jira-import",
};

function scrollTo(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.querySelector<HTMLElement>("input, select, button, textarea")?.focus({ preventScroll: true });
}

export function TaskSwitchover({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const got = useApi<TaskSwitchoverState>(path(bp));
  if (!got.data)
    return (
      <Card title="Chuyển đổi khỏi Jira" id="sw-loading">
        <Loading error={got.error} rows={3} />
        {got.error && (
          <button type="button" className="ghost sm" onClick={() => void got.mutate()}>
            Thử lại
          </button>
        )}
      </Card>
    );
  const reload = async (next?: TaskSwitchoverState) => {
    await got.mutate(next, { revalidate: !next });
  };
  return (
    <>
      <ChecklistCard space={space} state={got.data} onChange={reload} />
      <JiraCard bp={bp} state={got.data} onChange={reload} />
      <DemoCard space={space} state={got.data} onChange={reload} />
    </>
  );
}

type Reload = (next?: TaskSwitchoverState) => Promise<void>;

/* ---------- Lead checklist ---------- */

function ChecklistCard({ space, state, onChange }: { space: TaskSpace; state: TaskSwitchoverState; onChange: Reload }) {
  const bp = space.business_project_id;
  const [members, setMembers] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { checklist } = state;

  async function recommended() {
    setBusy(true);
    setMessage(null);
    try {
      const next = await api.post<TaskSwitchoverState>(`${path(bp)}/recommended-rules`, {});
      await onChange(next);
      await refreshTasks();
      setMessage("Đã bật 5 quy tắc gợi ý.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card
      title="Checklist bật Task cho Lead"
      id="tk-set-checklist"
      subtitle="Mỗi mục tự tích từ dữ liệu thật của dự án."
      actions={
        <Pill tone={checklist.complete ? "good" : "warn"}>
          {checklist.done}/{checklist.total} xong
        </Pill>
      }
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <ul className="sw-check">
        {checklist.items
          .filter((i) => i.applies)
          .map((item) => (
            <li key={item.key} className={item.done ? "sw-done" : undefined}>
              <span className="sw-check-icon" aria-hidden>
                <Icon name={item.done ? "checkCircle" : "alertCircle"} size={16} />
              </span>
              <span className="sw-check-text">
                <span className="sw-check-label">
                  {item.label}
                  <span className="sr-only">{item.done ? " (đã xong)" : " (chưa xong)"}</span>
                </span>
                <span className="small muted">{item.detail}</span>
              </span>
              {!item.done && (
                <span className="sw-check-fix">
                  {item.key === "automation" && (
                    <button type="button" className="ghost sm" disabled={busy} onClick={() => void recommended()}>
                      Bật 5 quy tắc gợi ý
                    </button>
                  )}
                  {item.fix === "members" ? (
                    <button type="button" className="ghost sm" onClick={() => setMembers(true)}>
                      Cấp quyền
                    </button>
                  ) : item.fix === "backlog" ? (
                    <Link className="button ghost sm" href={`/tasks?space=${bp}&tab=backlog`}>
                      Mở Backlog
                    </Link>
                  ) : FIX_ANCHOR[item.fix] ? (
                    <button type="button" className="ghost sm" onClick={() => scrollTo(FIX_ANCHOR[item.fix] as string)}>
                      Sửa
                    </button>
                  ) : null}
                </span>
              )}
            </li>
          ))}
      </ul>
      {members && (
        <Modal
          title="Thành viên dự án"
          onClose={() => {
            setMembers(false);
            void onChange();
          }}
          footer={
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setMembers(false);
                void onChange();
              }}
            >
              Đóng
            </button>
          }
        >
          <MembersStep bpId={bp} />
        </Modal>
      )}
    </Card>
  );
}

/** The unfinished checklist on the Board, for managers; "Ẩn" hides it in this browser. */
export function SwitchoverBoardNote({ space }: { space: TaskSpace }) {
  const bp = space.business_project_id;
  const storeKey = `tk-sw-checklist-hidden:${bp}`;
  const [hidden, setHidden] = useState(true);
  useEffect(() => {
    try {
      setHidden(localStorage.getItem(storeKey) === "1");
    } catch {
      setHidden(false);
    }
  }, [storeKey]);
  const got = useApi<TaskSwitchoverState>(space.can_manage && !hidden ? path(bp) : null);
  const list = got.data?.checklist;
  if (hidden || !list || list.complete) return null;
  const left = list.items.filter((i) => i.applies && !i.done);
  return (
    <div className="sw-board-note">
      <Alert
        tone="info"
        onClose={() => {
          try {
            localStorage.setItem(storeKey, "1");
          } catch {
            /* private mode: hidden until reload */
          }
          setHidden(true);
        }}
      >
        <strong>
          Checklist bật Task: {list.done}/{list.total} bước.
        </strong>{" "}
        Còn: {left.map((i) => i.label.toLowerCase()).join(", ")}.{" "}
        <Link href={`/tasks?space=${bp}&tab=settings`}>Mở Cài đặt</Link>
      </Alert>
    </div>
  );
}

/* ---------- Jira: old keys, "Ngừng dùng Jira" ---------- */

function whereOf(u: TaskAliasUse): string {
  const what = u.kind === "commit" ? `commit ${(u.sha ?? "").slice(0, 8)}` : u.kind === "mr" ? `MR !${u.mr_iid}` : `nhánh ${u.branch}`;
  return u.repo ? `${u.repo} · ${what}` : what;
}

function UsesTable({ items }: { items: TaskAliasUse[] }) {
  return (
    <div className="sw-uses">
      <table className="sw-table">
        <thead>
          <tr>
            <th scope="col">Mã cũ → mã mới</th>
            <th scope="col">Ở đâu</th>
            <th scope="col">Ai</th>
            <th scope="col">Lúc</th>
          </tr>
        </thead>
        <tbody>
          {items.map((u) => (
            <UseRow key={`${u.kind}:${u.external_key}:${u.sha ?? u.mr_iid ?? u.branch}`} u={u} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UseRow({ u }: { u: TaskAliasUse }) {
  const where = whereOf(u);
  return (
    <tr>
      <td>
        <span className="tk-key sw-old">{u.external_key}</span> →{" "}
        <Link className="tk-key" href={`/tasks?task=${u.task_key}`}>
          {u.task_key}
        </Link>
      </td>
      <td className="sw-where">{where}</td>
      <td>{u.author?.full_name ?? u.author?.email ?? u.author_text ?? <span className="faint">Không rõ</span>}</td>
      <td className="small muted" title={dateTime(u.at)}>
        {relative(u.at)}
      </td>
    </tr>
  );
}

function JiraCard({ bp, state, onChange }: { bp: number; state: TaskSwitchoverState; onChange: Reload }) {
  const [confirmEl, ask] = useConfirm();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const { readiness, uses } = state;
  const retired = !!state.jira_retired_at;
  const keys = readiness.sources.map((s) => s.project_key);
  const needReason = !readiness.ready;
  const reasonOk = reason.trim().length >= 10;

  async function retire() {
    const ok = await ask({
      title: "Ngừng dùng Jira cho dự án?",
      body: (
        <p>
          Bug của testcase luôn tạo thành task CodePulse, thẻ Nhập từ Jira thôi nhập. CodePulse không khoá được Jira: sau bước này, nhờ admin Jira đặt
          project {keys.length ? keys.join(", ") : "Jira"} ở chế độ chỉ đọc.
        </p>
      ),
      ok: "Ngừng dùng Jira",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    try {
      const next = await api.post<TaskSwitchoverState>(`${path(bp)}/retire-jira`, needReason ? { reason: reason.trim() } : {});
      await onChange(next);
      await refreshTasks();
      setReason("");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  async function restore() {
    const ok = await ask({ title: "Dùng lại Jira?", body: <p>Jira lại chọn được làm nơi nhận bug của testcase, thẻ Nhập từ Jira nhập được lại.</p>, ok: "Dùng lại Jira" });
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    try {
      const next = await api.delete<TaskSwitchoverState>(`${path(bp)}/retire-jira`);
      await onChange(next);
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card
      title="Chuyển đổi khỏi Jira"
      id="tk-set-switchover"
      subtitle="Mã Jira cũ vẫn trỏ về task mới; ở đây thấy ai còn dùng chúng và ngừng dùng Jira khi đã sẵn sàng."
      actions={retired ? <Pill tone="good">Đã ngừng dùng Jira</Pill> : null}
    >
      {confirmEl}
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <h3 className="sw-h">
        Mã Jira cũ vẫn được dùng ({uses.days} ngày qua)
        {uses.total > 0 && (
          <span className="small muted">
            {" "}
            · {uses.total} lần{uses.authors ? `, ${uses.authors} người` : ""}
          </span>
        )}
      </h3>
      {uses.items.length ? (
        <>
          <UsesTable items={uses.items} />
          <p className="small muted sw-note">Người viết được nhắc trong hộp thư (mỗi ngày một lần): “Dùng mã mới thay cho mã Jira cũ”.</p>
        </>
      ) : (
        <p className="small faint">Không thấy mã Jira cũ trong commit, MR hay tên nhánh {uses.days} ngày qua.</p>
      )}

      <h3 className="sw-h">Ngừng dùng Jira</h3>
      {retired ? (
        <>
          <Alert tone="warning">
            Đã ngừng dùng Jira từ {dateTime(state.jira_retired_at)}
            {state.jira_retired_by?.full_name ? ` (${state.jira_retired_by.full_name})` : ""}. <strong>CodePulse không khoá được Jira:</strong> nhờ admin Jira đặt
            project {keys.length ? keys.join(", ") : "Jira của dự án"} ở chế độ chỉ đọc (Jira › Project settings › Permissions: bỏ quyền tạo / sửa issue).
          </Alert>
          <div className="adm-form-foot">
            <button type="button" className="ghost" disabled={busy} onClick={() => void restore()}>
              Dùng lại Jira
            </button>
          </div>
        </>
      ) : (
        <div className="adm-form tk-form">
          <ul className="sw-check sw-ready">
            <li className={readiness.not_imported === 0 ? "sw-done" : undefined}>
              <span className="sw-check-icon" aria-hidden>
                <Icon name={readiness.not_imported === 0 ? "checkCircle" : "alertCircle"} size={16} />
              </span>
              <span className="sw-check-text">
                <span className="sw-check-label">Đã nhập hết issue Jira đã đồng bộ</span>
                <span className="small muted">
                  {readiness.sources.length
                    ? readiness.sources.map((s) => `${s.project_key}: ${s.imported}/${s.issues}`).join(" · ")
                    : "Dự án không có issue Jira nào đồng bộ."}
                </span>
              </span>
            </li>
            <li className={readiness.alias_uses === 0 ? "sw-done" : undefined}>
              <span className="sw-check-icon" aria-hidden>
                <Icon name={readiness.alias_uses === 0 ? "checkCircle" : "alertCircle"} size={16} />
              </span>
              <span className="sw-check-text">
                <span className="sw-check-label">Không còn dùng mã Jira cũ {readiness.days} ngày qua</span>
                <span className="small muted">{readiness.alias_uses ? `${readiness.alias_uses} lần dùng` : "Không lần nào"}</span>
              </span>
            </li>
          </ul>
          <p className="small muted">
            Sau khi ngừng: bug của testcase luôn tạo thành task CodePulse (không chọn Jira được), thẻ Nhập từ Jira chỉ còn xem lịch sử. Nên chạy song song một
            sprint trước.
          </p>
          {needReason && (
            <label className="rq-field">
              <span className="rq-label">Lý do vẫn ngừng khi chưa sẵn sàng (ghi vào nhật ký)</span>
              <textarea rows={2} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="Ví dụ: đã chạy song song 2 sprint, phần còn lại là issue cũ không cần nhập" />
              {reason && !reasonOk && <span className="small error">Ít nhất 10 ký tự.</span>}
            </label>
          )}
          <div className="adm-form-foot">
            <button type="button" className="ghost" disabled={busy || (needReason && !reasonOk)} onClick={() => void retire()}>
              {busy ? "Đang lưu…" : "Ngừng dùng Jira"}
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

/* ---------- demo data ---------- */

function DemoCard({ space, state, onChange }: { space: TaskSpace; state: TaskSwitchoverState; onChange: Reload }) {
  const bp = space.business_project_id;
  const [confirmEl, ask] = useConfirm();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const has = state.demo_tasks > 0;

  async function make() {
    setBusy(true);
    setMessage(null);
    try {
      const out = await api.post<TaskDemoCreated>(`/tasks/spaces/${bp}/demo`, {});
      setMessage(`Đã tạo ${out.created} task mẫu (${out.keys[0]}…), sprint và phiên bản “[Mẫu]”.`);
      await refreshTasks();
      await onChange();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  async function remove() {
    const ok = await ask({
      title: "Xoá dữ liệu demo?",
      body: (
        <p>
          Xoá {state.demo_tasks} task có nhãn <code>mau-demo</code> trong dự án (kèm bình luận, lịch sử, file của chúng), và sprint / phiên bản “[Mẫu]” khi
          không còn task nào. Task khác không bị đụng tới. Không hoàn tác được.
        </p>
      ),
      ok: "Xoá dữ liệu demo",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    try {
      const out = await api.delete<TaskDemoDeleted>(`/tasks/spaces/${bp}/demo`);
      const extra = [out.sprints ? "sprint demo" : "", out.versions ? "phiên bản demo" : ""].filter(Boolean).join(", ");
      setMessage(`Đã xoá ${out.deleted} task mẫu${extra ? `, ${extra}` : ""}.`);
      await refreshTasks();
      await onChange();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card title="Dữ liệu demo" id="tk-set-demo" subtitle="Cho buổi hướng dẫn 30 phút: tạo bộ task mẫu, học xong thì xoá.">
      {confirmEl}
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      <p className="small muted">
        Một epic, story, task, sub-task, bug, sprint “[Mẫu] Sprint demo”, phiên bản “[Mẫu] v1.0” và vài bình luận. Mọi task mang nhãn <code>mau-demo</code>, tiêu đề
        bắt đầu bằng “[Mẫu]”, giao cho bạn.
      </p>
      {has && (
        <p className="small" role="status">
          Đang có <strong>{state.demo_tasks}</strong> task mẫu.
        </p>
      )}
      <div className="adm-form-foot">
        {has ? (
          <button type="button" className="ghost danger" disabled={busy} onClick={() => void remove()}>
            {busy ? "Đang xoá…" : "Xoá dữ liệu demo"}
          </button>
        ) : (
          <button type="button" className="ghost" disabled={busy} onClick={() => void make()}>
            {busy ? "Đang tạo…" : "Tạo dữ liệu demo"}
          </button>
        )}
      </div>
    </Card>
  );
}


