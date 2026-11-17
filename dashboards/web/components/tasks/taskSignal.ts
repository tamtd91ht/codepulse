import type { TaskRow } from "@/lib/types";

import { today } from "./taskParts";

/*
 * One logic for "what is wrong with this task" (docs/specs/31 §21.1): the Board card shows the most
 * important signal, the Sức khoẻ block of the task shows all of them. Order: blocked > open critical
 * findings > overdue > too long in its column > MR closed without merge > enforced DoD missing.
 * A task in a "done" status shows none.
 */

export type SignalKind = "blocked" | "critical" | "overdue" | "age" | "mr_closed" | "dod";
export type Signal = { kind: SignalKind; tone: "bad" | "warn"; text: string; title: string };

function hoursIn(t: Pick<TaskRow, "status_changed_at">): number | null {
  if (!t.status_changed_at) return null;
  return (Date.now() - new Date(t.status_changed_at).getTime()) / 3_600_000;
}

/** Every signal of a Board row, most important first. `dodMissing`: the labels of the enforced
 * DoD checks it fails (GET /tasks/spaces/{bp}/dod-gaps), null when not enforced or unknown. */
export function taskSignals(task: TaskRow, ageWarnHours: number | null, dodMissing?: string[] | null): Signal[] {
  if (task.status.category === "done") return [];
  const out: Signal[] = [];
  if (task.blocked) out.push({ kind: "blocked", tone: "bad", text: "⛔ Bị chặn", title: "Một task chưa xong đang chặn task này" });
  const crit = task.git?.open_critical ?? 0;
  if (crit > 0) out.push({ kind: "critical", tone: "bad", text: `${crit} critical`, title: "Code của task còn finding critical mở" });
  if (task.due_date && task.due_date < today()) out.push({ kind: "overdue", tone: "bad", text: "Quá hạn", title: `Hạn ${task.due_date}` });
  const h = hoursIn(task);
  if (ageWarnHours && h !== null && h > ageWarnHours) {
    const days = Math.floor(h / 24);
    out.push({ kind: "age", tone: "warn", text: days >= 1 ? `${days} ngày ở đây` : `${Math.floor(h)} giờ ở đây`, title: `Ở “${task.status.name}” quá ngưỡng ${ageWarnHours} giờ của dự án` });
  }
  if (task.mr_closed) out.push({ kind: "mr_closed", tone: "warn", text: "🔁 MR bị đóng", title: "MR của task đã đóng mà không merge" });
  if (dodMissing && dodMissing.length)
    out.push({ kind: "dod", tone: "warn", text: `DoD thiếu ${dodMissing.length}`, title: `Definition of Done bắt buộc còn thiếu: ${dodMissing.join(", ")}` });
  return out;
}

/** The one signal a Board card shows. */
export function cardSignal(task: TaskRow, ageWarnHours: number | null, dodMissing?: string[] | null): Signal | null {
  return taskSignals(task, ageWarnHours, dodMissing)[0] ?? null;
}

/** The branch "Tạo nhánh" makes for a task, as app/tasks/keys.py `branch_name`:
 * feature/CRM-12-dang-nhap-bang-otp (bug → bugfix/), ASCII, at most 60 characters. */
export function branchName(key: string, title: string, type: string): string {
  const plain = title
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "")
    .toLowerCase();
  const words = plain.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const prefix = type === "bug" ? "bugfix" : "feature";
  return `${prefix}/${key}-${words}`.slice(0, 60).replace(/-+$/, "");
}
