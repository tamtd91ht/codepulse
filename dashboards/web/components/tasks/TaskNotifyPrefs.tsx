"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Card, Loading, Message } from "@/components/ui";
import { api, can, useApi } from "@/lib/api";
import type { Me, TaskNotifyChannel, TaskNotifyPrefsData, TaskNotifySummary } from "@/lib/types";

import { errorText } from "./taskParts";

/* Thông báo Task (docs/specs/31 §19.7, §20.5, §21.2): bảng sự kiện × kênh (hộp thư / Telegram / tắt),
 * ngay hoặc gộp mỗi ngày, giờ yên lặng và cuối tuần, tóm tắt cuối ngày (có việc ngày mai).
 * `scope="me"`: của tôi (GET/PUT/DELETE /me/task-notify); `scope="org"`: mặc định của tổ chức
 * (Admin, /task-notify/defaults) cho người chưa tự chỉnh. */

type Prefs = TaskNotifyPrefsData;

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
const CHANNELS: { key: TaskNotifyChannel; label: string }[] = [
  { key: "inbox", label: "Hộp thư" },
  { key: "telegram", label: "Telegram" },
  { key: "off", label: "Tắt" },
];
const DAYS: { day: number; label: string }[] = [
  { day: 5, label: "Thứ Bảy" },
  { day: 6, label: "Chủ nhật" },
];

export function TaskNotifyPrefs({ scope = "me" }: { scope?: "me" | "org" }) {
  const org = scope === "org";
  const { data: me } = useApi<Me>("/auth/me");
  const show = org ? !!me?.is_admin : can(me, "tasks.view");
  const path = org ? "/task-notify/defaults" : "/me/task-notify";
  const { data, error, mutate } = useApi<Prefs>(show ? path : null);
  const [mode, setMode] = useState<Prefs["mode"]>("immediate");
  const [quiet, setQuiet] = useState<[number, number] | null>(null);
  const [days, setDays] = useState<number[]>([]);
  const [channels, setChannels] = useState<Record<string, TaskNotifyChannel>>({});
  const [summary, setSummary] = useState<TaskNotifySummary>({ on: false, time: "17:30", channel: "inbox" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!data) return;
    setMode(data.mode);
    setQuiet(data.quiet_hours);
    setDays(data.quiet_days ?? []);
    setChannels(data.channels);
    setSummary(data.summary);
  }, [data]);
  const dirty = useMemo(
    () =>
      !!data &&
      (mode !== data.mode ||
        JSON.stringify(quiet) !== JSON.stringify(data.quiet_hours) ||
        JSON.stringify(days) !== JSON.stringify(data.quiet_days ?? []) ||
        JSON.stringify(channels) !== JSON.stringify(data.channels) ||
        JSON.stringify(summary) !== JSON.stringify(data.summary)),
    [data, mode, quiet, days, channels, summary],
  );
  if (!show) return null;
  // The organisation's defaults may pick Telegram: it reaches those who linked an account.
  const linked = org || !!data?.telegram_linked;
  const badTime = !/^([01]\d|2[0-3]):[0-5]\d$/.test(summary.time);
  const id = org ? "ns-org" : "ta-notify";

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const fresh = await api.put<Prefs>(path, { mode, quiet_hours: quiet, quiet_days: days, channels, summary });
      await mutate(fresh, { revalidate: false });
      setMessage(org ? "Đã lưu mặc định của tổ chức." : "Đã lưu cài đặt thông báo.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  async function reset() {
    setBusy(true);
    setMessage(null);
    try {
      const fresh = await api.delete<Prefs>("/me/task-notify");
      await mutate(fresh, { revalidate: false });
      setMessage("Đã dùng lại mặc định của tổ chức.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  const toggleDay = (day: number, on: boolean) =>
    setDays((d) => (on ? [...new Set([...d, day])].sort() : d.filter((x) => x !== day)));

  return (
    <Card
      title={org ? "Mặc định của tổ chức" : "Thông báo Task"}
      id={id}
      subtitle={
        org
          ? "Áp dụng cho người chưa tự chỉnh cài đặt của mình. Ai đã lưu cài đặt riêng thì giữ nguyên."
          : "Chọn kênh cho từng sự kiện về task. Thông báo review code không đổi."
      }
    >
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!data ? (
        error ? (
          <div className="ns-error">
            <Loading error={error} />
            <button type="button" className="ghost sm" onClick={() => void mutate()}>
              Thử lại
            </button>
          </div>
        ) : (
          <Loading rows={6} />
        )
      ) : (
        <div className="ta-prefs">
          {!org && (
            <p className="small muted ns-source">
              {data.customised
                ? "Bạn đang dùng cài đặt riêng."
                : data.org_defaults
                  ? "Bạn đang theo mặc định của tổ chức. Lưu để dùng cài đặt riêng."
                  : "Bạn đang dùng cài đặt mặc định."}
            </p>
          )}
          <fieldset className="ta-field">
            <legend className="rq-label">Kênh theo sự kiện</legend>
            <div className="table-wrap ns-table-wrap">
              <table className="ns-table">
                <thead>
                  <tr>
                    <th scope="col">Sự kiện</th>
                    {CHANNELS.map((c) => (
                      <th key={c.key} scope="col" className="ns-col">
                        {c.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.kind_labels).map(([kind, label]) => {
                    const current = channels[kind] ?? "inbox";
                    return (
                      <tr key={kind}>
                        <th scope="row" className="ns-event">
                          {label}
                          {kind === data.urgent_kind && <span className="small muted ns-hint">Vẫn báo trong giờ yên lặng</span>}
                        </th>
                        {CHANNELS.map((c) => {
                          const tgAllowed = data.telegram_kinds.includes(kind);
                          if (c.key === "telegram" && !tgAllowed)
                            return (
                              <td key={c.key} className="ns-col ns-na" aria-label="Không có Telegram cho sự kiện này">
                                –
                              </td>
                            );
                          const disabled = c.key === "telegram" && !linked && current !== "telegram";
                          return (
                            <td key={c.key} className="ns-col">
                              <input
                                type="radio"
                                name={`${id}-${kind}`}
                                aria-label={`${label}: ${c.label}`}
                                checked={current === c.key}
                                disabled={disabled}
                                onChange={() => setChannels((all) => ({ ...all, [kind]: c.key }))}
                              />
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="small muted nr-note">
              Telegram: thông báo vẫn nằm trong hộp thư, kèm một tin nhắn riêng có nút Mở / Bắt đầu / Đã xem / Ẩn 1 ngày.{" "}
              {!linked && (
                <>
                  Chưa liên kết Telegram: <Link href={me ? `/members/${me.id}` : "/"}>liên kết ở trang Của tôi</Link>.
                </>
              )}
            </p>
          </fieldset>
          <fieldset className="ta-field">
            <legend className="rq-label">Cách nhận</legend>
            <label className="inline-check">
              <input type="radio" name={`${id}-mode`} checked={mode === "immediate"} onChange={() => setMode("immediate")} />
              Ngay khi có
            </label>
            <label className="inline-check">
              <input type="radio" name={`${id}-mode`} checked={mode === "digest"} onChange={() => setMode("digest")} />
              Gộp thành một bản tóm tắt mỗi ngày (từ {hh(data.send_hour)})
            </label>
            <p className="small muted nr-note">Nhiều cập nhật trên cùng một task trong 10 phút được gộp thành một thông báo.</p>
          </fieldset>
          <fieldset className="ta-field">
            <legend className="rq-label">Giờ yên lặng</legend>
            <label className="inline-check">
              <input type="checkbox" checked={quiet !== null} onChange={(e) => setQuiet(e.target.checked ? [21, 8] : null)} />
              Giữ thông báo trong khoảng giờ này, gửi gộp khi hết giờ
            </label>
            {quiet && (
              <span className="ta-hours">
                <select aria-label="Từ giờ" value={quiet[0]} onChange={(e) => setQuiet([Number(e.target.value), quiet[1]])}>
                  {HOURS.map((h) => (
                    <option key={h} value={h}>
                      {hh(h)}
                    </option>
                  ))}
                </select>
                <span className="small muted">đến</span>
                <select aria-label="Đến giờ" value={quiet[1]} onChange={(e) => setQuiet([quiet[0], Number(e.target.value)])}>
                  {HOURS.map((h) => (
                    <option key={h} value={h}>
                      {hh(h)}
                    </option>
                  ))}
                </select>
                {quiet[0] === quiet[1] && <span className="small ta-bad">Giờ bắt đầu và kết thúc phải khác nhau.</span>}
              </span>
            )}
            <span className="ns-days">
              <span className="small muted">Yên lặng cả ngày:</span>
              {DAYS.map((d) => (
                <label key={d.day} className="inline-check">
                  <input type="checkbox" checked={days.includes(d.day)} onChange={(e) => toggleDay(d.day, e.target.checked)} />
                  {d.label}
                </label>
              ))}
            </span>
            <p className="small muted nr-note">Bug ưu tiên Cao nhất, code còn critical và sự cố production vẫn báo ngay.</p>
          </fieldset>
          <fieldset className="ta-field">
            <legend className="rq-label">Tóm tắt cuối ngày</legend>
            <label className="inline-check">
              <input type="checkbox" checked={summary.on} onChange={(e) => setSummary((s) => ({ ...s, on: e.target.checked }))} />
              Gửi tóm tắt: việc xong hôm nay, việc còn quá hạn, MR chờ review, việc tới hạn ngày mai
            </label>
            {summary.on && (
              <span className="ta-hours">
                <span className="small muted">lúc</span>
                <input
                  type="time"
                  aria-label="Giờ gửi tóm tắt"
                  step={900}
                  value={summary.time}
                  onChange={(e) => setSummary((s) => ({ ...s, time: e.target.value }))}
                />
                <select
                  aria-label="Kênh gửi tóm tắt"
                  value={summary.channel}
                  onChange={(e) => setSummary((s) => ({ ...s, channel: e.target.value as TaskNotifySummary["channel"] }))}
                >
                  <option value="inbox">Hộp thư</option>
                  <option value="telegram" disabled={!linked && summary.channel !== "telegram"}>
                    Telegram
                  </option>
                </select>
                {badTime && <span className="small ta-bad">Giờ dạng HH:MM.</span>}
              </span>
            )}
          </fieldset>
          <div className="adm-form-foot ns-foot">
            {!org && data.customised && data.org_defaults && (
              <button type="button" className="ghost" disabled={busy} onClick={() => void reset()}>
                Dùng mặc định của tổ chức
              </button>
            )}
            <button
              type="button"
              className="primary"
              disabled={!dirty || busy || (quiet !== null && quiet[0] === quiet[1]) || (summary.on && badTime)}
              onClick={() => void save()}
            >
              Lưu
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
