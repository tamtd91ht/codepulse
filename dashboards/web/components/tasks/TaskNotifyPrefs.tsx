"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Card, Loading, Message } from "@/components/ui";
import { api, can, useApi } from "@/lib/api";
import type { Me, TaskNotifyChannel, TaskNotifySummary } from "@/lib/types";

import { errorText } from "./taskParts";

/* Thông báo Task của tôi (docs/specs/31 §19.7, §20.5): ngay hoặc gộp mỗi ngày, giờ yên lặng, kênh cho
 * từng loại (hộp thư / Telegram / tắt), tóm tắt cuối ngày. */

type Prefs = {
  mode: "immediate" | "digest";
  quiet_hours: [number, number] | null;
  kinds: string[];
  kind_labels: Record<string, string>;
  send_hour: number;
  channels: Record<string, TaskNotifyChannel>;
  urgent_kind: string;
  summary: TaskNotifySummary;
  telegram_linked: boolean;
};

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
const CHANNEL_LABEL: Record<TaskNotifyChannel, string> = { inbox: "Hộp thư", telegram: "Telegram", off: "Tắt" };

export function TaskNotifyPrefs() {
  const { data: me } = useApi<Me>("/auth/me");
  const show = can(me, "tasks.view");
  const { data, error, mutate } = useApi<Prefs>(show ? "/me/task-notify" : null);
  const [mode, setMode] = useState<Prefs["mode"]>("immediate");
  const [quiet, setQuiet] = useState<[number, number] | null>(null);
  const [channels, setChannels] = useState<Record<string, TaskNotifyChannel>>({});
  const [summary, setSummary] = useState<TaskNotifySummary>({ on: false, time: "17:30", channel: "inbox" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!data) return;
    setMode(data.mode);
    setQuiet(data.quiet_hours);
    setChannels(data.channels);
    setSummary(data.summary);
  }, [data]);
  const dirty = useMemo(
    () =>
      !!data &&
      (mode !== data.mode ||
        JSON.stringify(quiet) !== JSON.stringify(data.quiet_hours) ||
        JSON.stringify(channels) !== JSON.stringify(data.channels) ||
        JSON.stringify(summary) !== JSON.stringify(data.summary)),
    [data, mode, quiet, channels, summary],
  );
  if (!show) return null;
  const linked = !!data?.telegram_linked;
  const badTime = !/^([01]\d|2[0-3]):[0-5]\d$/.test(summary.time);

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const fresh = await api.put<Prefs>("/me/task-notify", { mode, quiet_hours: quiet, channels, summary });
      await mutate(fresh, { revalidate: false });
      setMessage("Đã lưu cài đặt thông báo Task.");
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
    setBusy(false);
  }

  return (
    <Card title="Thông báo Task" id="ta-notify" subtitle="Áp dụng cho thông báo về task (được giao, đổi trạng thái, bình luận, nhắc việc). Thông báo review không đổi.">
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {!data ? (
        <Loading error={error} rows={3} />
      ) : (
        <div className="ta-prefs">
          <fieldset className="ta-field">
            <legend className="rq-label">Cách nhận</legend>
            <label className="inline-check">
              <input type="radio" name="ta-mode" checked={mode === "immediate"} onChange={() => setMode("immediate")} />
              Ngay khi có
            </label>
            <label className="inline-check">
              <input type="radio" name="ta-mode" checked={mode === "digest"} onChange={() => setMode("digest")} />
              Gộp thành một bản tóm tắt mỗi ngày (từ {hh(data.send_hour)})
            </label>
            <p className="small muted nr-note">Nhiều cập nhật trên cùng một task trong 10 phút được gộp thành một thông báo.</p>
          </fieldset>
          <fieldset className="ta-field">
            <legend className="rq-label">Giờ yên lặng</legend>
            <label className="inline-check">
              <input type="checkbox" checked={quiet !== null} onChange={(e) => setQuiet(e.target.checked ? [22, 7] : null)} />
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
          </fieldset>
          <fieldset className="ta-field">
            <legend className="rq-label">Kênh theo loại thông báo</legend>
            <div className="nr-channels">
              {Object.entries(data.kind_labels).map(([kind, label]) => (
                <label key={kind} className="nr-channel">
                  <span>
                    {label}
                    {kind === data.urgent_kind && <span className="small muted nr-note"> · vẫn gửi trong giờ yên lặng và chế độ gộp</span>}
                  </span>
                  <select
                    aria-label={`Kênh: ${label}`}
                    value={channels[kind] ?? "inbox"}
                    onChange={(e) => setChannels((c) => ({ ...c, [kind]: e.target.value as TaskNotifyChannel }))}
                  >
                    {(Object.keys(CHANNEL_LABEL) as TaskNotifyChannel[]).map((c) => (
                      <option key={c} value={c} disabled={c === "telegram" && !linked && channels[kind] !== "telegram"}>
                        {CHANNEL_LABEL[c]}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            <p className="small muted nr-note">
              Telegram: thông báo vẫn nằm trong hộp thư, kèm một tin nhắn riêng có nút Mở / Đã xem / Ẩn 1 ngày.{" "}
              {!linked && (
                <>
                  Chưa liên kết Telegram: <Link href={me ? `/members/${me.id}` : "/"}>liên kết ở trang Của tôi</Link>.
                </>
              )}
            </p>
          </fieldset>
          <fieldset className="ta-field">
            <legend className="rq-label">Tóm tắt cuối ngày</legend>
            <label className="inline-check">
              <input type="checkbox" checked={summary.on} onChange={(e) => setSummary((s) => ({ ...s, on: e.target.checked }))} />
              Gửi tóm tắt: việc xong hôm nay, việc còn quá hạn, MR chờ tôi review
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
          <div className="adm-form-foot">
            <button
              type="button"
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
