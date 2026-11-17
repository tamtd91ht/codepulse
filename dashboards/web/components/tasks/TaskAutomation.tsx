"use client";

import { useEffect, useMemo, useState } from "react";

import { Card, Loading, Message, Pill, Segmented } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { relative } from "@/lib/format";
import type { TaskRuleDryRun, TaskRuleRunRow, TaskSpace } from "@/lib/types";

import { useSettingsDraft } from "./settingsDraft";
import { errorText } from "./taskParts";

/* Quy tắc tự động (docs/specs/31 §19.7, §20.5): a fixed library of rules, each on/off per dự án, some with one
 * number; "Chạy thử" shows what a scheduled rule would do now; "Nhật ký" lists what the rules did. */

type RuleParam = { key: string; label: string; value: number; default: number; min: number; max: number; unit: string };
type Rule = {
  key: string;
  title: string;
  hint: string;
  trigger: "ui" | "event" | "schedule";
  on: boolean;
  params: RuleParam[];
  can_dry_run?: boolean;
};
type AutomationOut = { rules: Rule[]; can_manage: boolean };
type Draft = Record<string, { on: boolean } & Record<string, number | boolean>>;

const TRIGGER: Record<Rule["trigger"], { label: string; tone: "info" | "good" | "warn" }> = {
  ui: { label: "Hỏi khi thao tác", tone: "info" },
  event: { label: "Khi có sự kiện", tone: "good" },
  schedule: { label: "Hằng ngày", tone: "warn" },
};

function draftOf(rules: Rule[]): Draft {
  const out: Draft = {};
  for (const r of rules) {
    const row: { on: boolean } & Record<string, number | boolean> = { on: r.on };
    for (const p of r.params) row[p.key] = p.value;
    out[r.key] = row;
  }
  return out;
}

const ACTION_LABEL: Record<string, string> = {
  notify: "Báo",
  assign: "Giao người làm",
  move: "Chuyển trạng thái",
  set_version: "Gắn phiên bản",
  skipped: "Bỏ qua",
  failed: "Lỗi",
  dry_run: "Chạy thử",
};

function detailText(action: string, detail: Record<string, unknown>): string {
  if (action === "dry_run") return `${Number(detail.count ?? 0)} tác động`;
  if (action === "move" && detail.to) return `→ ${String(detail.to)}`;
  if (action === "set_version" && detail.version) return `${String(detail.version)} (${String(detail.branch ?? "")})`;
  if (action === "skipped" && detail.why) return String(detail.why);
  if (detail.title) return String(detail.title);
  if (detail.sprint) return String(detail.sprint);
  if (Array.isArray(detail.users)) return `${detail.users.length} người`;
  return "";
}

function RuleLog({ path, rules }: { path: string; rules: Rule[] }) {
  const { data, error, mutate } = useApi<{ items: TaskRuleRunRow[] }>(`${path}/log?limit=100`);
  const [rule, setRule] = useState("");
  if (!data) return <Loading error={error} rows={4} />;
  const items = rule ? data.items.filter((i) => i.rule === rule) : data.items;
  return (
    <div className="nr-log">
      <div className="nr-log-head">
        <label className="nr-log-filter">
          <span className="small muted">Quy tắc</span>
          <select value={rule} onChange={(e) => setRule(e.target.value)} aria-label="Lọc theo quy tắc">
            <option value="">Tất cả</option>
            {rules.map((r) => (
              <option key={r.key} value={r.key}>
                {r.title}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="ghost sm" onClick={() => void mutate()}>
          Tải lại
        </button>
      </div>
      {items.length === 0 ? (
        <p className="small muted">Chưa có lần chạy nào{rule ? " của quy tắc này" : ""}.</p>
      ) : (
        <div className="table-wrap">
          <table className="nr-log-table">
            <thead>
              <tr>
                <th>Lúc</th>
                <th>Quy tắc</th>
                <th>Task</th>
                <th>Làm gì</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} className={i.dry_run ? "nr-dry" : undefined}>
                  <td className="small" title={i.at ?? undefined}>
                    {i.at ? relative(i.at) : "—"}
                  </td>
                  <td>{i.rule_title}</td>
                  <td>{i.task_key ? <a href={`/tasks?task=${i.task_key}`}>{i.task_key}</a> : "—"}</td>
                  <td>
                    <Pill tone={i.dry_run ? "info" : i.action === "skipped" || i.action === "failed" ? "warn" : "good"}>
                      {ACTION_LABEL[i.action] ?? i.action}
                    </Pill>{" "}
                    <span className="small muted">{detailText(i.action, i.detail)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DryRunResult({ result, onClose }: { result: TaskRuleDryRun; onClose: () => void }) {
  return (
    <div className="nr-dry-result" role="status">
      <div className="nr-dry-head">
        <strong className="small">
          {result.count ? `Chạy thử: ${result.count} tác động nếu chạy bây giờ` : "Chạy thử: bây giờ quy tắc không tác động tới task nào"}
        </strong>
        <button type="button" className="ghost sm" onClick={onClose}>
          Đóng
        </button>
      </div>
      {result.items.length > 0 && (
        <ul className="nr-dry-list small">
          {result.items.map((item, n) => (
            <li key={n}>
              <strong>{item.task_key ?? "—"}</strong> · {ACTION_LABEL[item.action] ?? item.action}{" "}
              <span className="muted">{detailText(item.action, item)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="small muted">Không đổi gì, không gửi gì; chỉ ghi một dòng "Chạy thử" vào nhật ký.</p>
    </div>
  );
}

export function TaskAutomation({ space }: { space: TaskSpace }) {
  const path = `/tasks/spaces/${space.business_project_id}/automation`;
  const [view, setView] = useState<"rules" | "log">("rules");
  const [dry, setDry] = useState<Record<string, TaskRuleDryRun>>({});
  const [trying, setTrying] = useState<string | null>(null);
  const { data, error, mutate } = useApi<AutomationOut>(path);
  const initial = useMemo(() => (data ? draftOf(data.rules) : {}), [data]);
  const [draft, setDraft] = useState<Draft>({});
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => setDraft(initial), [initial]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const canManage = !!data?.can_manage;

  function patch(key: string, change: Record<string, number | boolean>) {
    setDraft((d) => ({ ...d, [key]: { ...d[key], ...change } }));
  }

  async function tryRule(key: string) {
    setTrying(key);
    setMessage(null);
    try {
      const result = await api.post<TaskRuleDryRun>(`${path}/${key}/dry-run`, {});
      setDry((d) => ({ ...d, [key]: result }));
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setTrying(null);
  }

  const invalid = (data?.rules ?? []).flatMap((r) =>
    r.params
      .filter((p) => {
        const v = Number(draft[r.key]?.[p.key]);
        return !Number.isInteger(v) || v < p.min || v > p.max;
      })
      .map((p) => `${r.title}: ${p.label} từ ${p.min} đến ${p.max}`),
  );

  async function save(): Promise<boolean> {
    if (!data) return false;
    if (invalid.length) {
      setMessage(`Lỗi: ${invalid.join("; ")}`);
      return false;
    }
    setMessage(null);
    try {
      const fresh = await api.put<AutomationOut>(path, draft);
      await mutate(fresh, { revalidate: false });
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
      return false;
    }
  }
  // Saved through Cài đặt's bottom bar (§22.7); only managers can change rules.
  useSettingsDraft("st-automation", "Quy tắc tự động", 7, { dirty: canManage && dirty, valid: !invalid.length, save, reset: () => setDraft(initial) });

  return (
    <Card
      title="Quy tắc tự động"
      id="tk-set-automation"
      subtitle="Quy tắc có sẵn, bật theo dự án; chạy dưới tên hệ thống, theo luồng trạng thái, nhắc việc mỗi task tối đa một lần mỗi ngày."
      actions={
        <Segmented
          label="Xem"
          value={view}
          onChange={setView}
          options={[
            { value: "rules", label: "Quy tắc" },
            { value: "log", label: "Nhật ký" },
          ]}
        />
      }
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!data ? (
        <Loading error={error} rows={4} />
      ) : view === "log" ? (
        <RuleLog path={path} rules={data.rules} />
      ) : (
        <>
          <ul className="st-auto-list">
            {data.rules.map((r) => {
              const row = draft[r.key] ?? { on: r.on };
              return (
                <li key={r.key} className={`st-auto${row.on ? " on" : ""}`}>
                  <div className="st-auto-row">
                    <input
                      type="checkbox"
                      role="switch"
                      checked={!!row.on}
                      disabled={!canManage}
                      onChange={(e) => patch(r.key, { on: e.target.checked })}
                      aria-labelledby={`ta-name-${r.key}`}
                      aria-describedby={`ta-hint-${r.key}`}
                    />
                    <span className="st-auto-name">
                      <strong id={`ta-name-${r.key}`}>{r.title}</strong>
                      <span id={`ta-hint-${r.key}`} className="small muted st-sub" title={r.hint}>
                        {r.hint}
                      </span>
                    </span>
                    <span className="st-auto-when">
                      <Pill tone={TRIGGER[r.trigger].tone}>{TRIGGER[r.trigger].label}</Pill>
                    </span>
                    <span className="st-auto-params">
                      {r.params.map((p) => (
                        <label key={p.key} className="ta-param" title={`${p.label} (${p.min}–${p.max} ${p.unit})`}>
                          <span className="small muted">{p.label}</span>
                          <input
                            type="number"
                            min={p.min}
                            max={p.max}
                            step={1}
                            value={String(row[p.key] ?? p.value)}
                            disabled={!canManage || !row.on}
                            onChange={(e) => patch(r.key, { [p.key]: e.target.value === "" ? Number.NaN : Number(e.target.value) })}
                          />
                          <span className="small muted">{p.unit}</span>
                        </label>
                      ))}
                    </span>
                    <span className="st-auto-try">
                      {canManage && r.can_dry_run && (
                        <button
                          type="button"
                          className="ghost sm"
                          disabled={trying !== null}
                          onClick={() => void tryRule(r.key)}
                          title="Xem quy tắc sẽ tác động tới những task nào nếu chạy bây giờ (không đổi gì)"
                        >
                          {trying === r.key ? "Đang chạy thử…" : "Chạy thử"}
                        </button>
                      )}
                    </span>
                  </div>
                  {dry[r.key] && (
                    <DryRunResult
                      result={dry[r.key]}
                      onClose={() =>
                        setDry((d) => {
                          const { [r.key]: _gone, ...rest } = d;
                          return rest;
                        })
                      }
                    />
                  )}
                </li>
              );
            })}
          </ul>
          {!canManage && <p className="small muted">Chỉ người quản lý Task của dự án đổi được quy tắc.</p>}
        </>
      )}
    </Card>
  );
}
