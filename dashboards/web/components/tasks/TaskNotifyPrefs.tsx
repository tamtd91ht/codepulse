"use client";

import { useEffect, useMemo, useState } from "react";

import { Card, Loading, Message } from "@/components/ui";
import { api, can, useApi } from "@/lib/api";
import type { Me } from "@/lib/types";

import { errorText } from "./taskParts";

/* Thông báo Task của tôi (docs/specs/31 §19.7): ngay hoặc gộp mỗi ngày, giờ yên lặng, tắt từng loại. */

type Prefs = {
  mode: "immediate" | "digest";
  quiet_hours: [number, number] | null;
  kinds: string[];
  kind_labels: Record<string, string>;
  send_hour: number;
};

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

export function TaskNotifyPrefs() {
  const { data: me } = useApi<Me>("/auth/me");
  const show = can(me, "tasks.view");
  const { data, error, mutate } = useApi<Prefs>(show ? "/me/task-notify" : null);
  const [mode, setMode] = useState<Prefs["mode"]>("immediate");
  const [quiet, setQuiet] = useState<[number, number] | null>(null);
  const [muted, setMuted] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!data) return;
    setMode(data.mode);
    setQuiet(data.quiet_hours);
    setMuted(data.kinds);
  }, [data]);
  const dirty = useMemo(
    () => !!data && (mode !== data.mode || JSON.stringify(quiet) !== JSON.stringify(data.quiet_hours) || [...muted].sort().join() !== [...data.kinds].sort().join()),
    [data, mode, quiet, muted],
  );
  if (!show) return null;

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const fresh = await api.put<Prefs>("/me/task-notify", { mode, quiet_hours: quiet, kinds: muted });
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
            <legend className="rq-label">Tắt loại thông báo</legend>
            <div className="ta-kinds">
              {Object.entries(data.kind_labels).map(([kind, label]) => (
                <label key={kind} className="inline-check">
                  <input
                    type="checkbox"
                    checked={muted.includes(kind)}
                    onChange={(e) => setMuted((list) => (e.target.checked ? [...list, kind] : list.filter((k) => k !== kind)))}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="adm-form-foot">
            <button type="button" disabled={!dirty || busy || (quiet !== null && quiet[0] === quiet[1])} onClick={() => void save()}>
              Lưu
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
