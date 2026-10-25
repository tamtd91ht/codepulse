"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { ReviewSetting } from "@/lib/types";

/** Admin card: review settings that override their .env default (today MAX_REVIEW_LOC). */
export function ReviewSettings() {
  const settings = useApi<ReviewSetting[]>("/admin/review-settings");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const loc = settings.data?.find((s) => s.key === "max_review_loc");

  async function save(value: number | null) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await settings.mutate(await api.put<ReviewSetting[]>("/admin/review-settings", { max_review_loc: value }), false);
      setNotice(value === null ? "Đã trả về giá trị trong .env." : "Đã lưu. Áp dụng cho review tiếp theo.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    save(Number(new FormData(e.currentTarget).get("max_review_loc")));
  };

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Cài đặt review</h2>
          <div className="sub">Ghi đè giá trị trong .env; review tiếp theo dùng ngay, không cần khởi động lại.</div>
        </div>
      </div>
      {settings.error && <Alert tone="error">Lỗi: {settings.error.message}</Alert>}
      {error && <Alert tone="error">Lỗi: {error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      {loc && (
        <form onSubmit={submit} style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}>
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Số dòng thay đổi tối đa mỗi MR/commit ({loc.env})</span>
            <input
              key={loc.value}
              name="max_review_loc"
              type="number"
              required
              min={loc.minimum}
              max={loc.maximum}
              defaultValue={loc.value}
            />
          </label>
          <p className="muted small">
            Diff lớn hơn ngưỡng này bị bỏ qua với lý do “MR quá lớn”. Diff lớn được chia thành nhiều phần
            (MAX_TOKENS_PER_CHUNK) nên vẫn review được, nhưng tốn nhiều lượt dùng của gói hơn.{" "}
            {loc.overridden ? `Mặc định trong .env: ${loc.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button className="primary" type="submit" disabled={busy}>
              Lưu
            </button>
            {loc.overridden && (
              <button type="button" onClick={() => save(null)} disabled={busy}>
                Về mặc định ({loc.default})
              </button>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
