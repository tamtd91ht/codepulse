"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import type { ReviewSetting } from "@/lib/types";

type Key = ReviewSetting["key"];

// Suggestions only: any id `claude --model` accepts can be typed under "Khác".
const MODELS: { id: string; label: string }[] = [
  { id: "claude-opus-5-5", label: "Claude Opus 5.5 (mặc định, kỹ nhất)" },
  { id: "claude-fable-5-1", label: "Claude Fable 5.1" },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5 (nhanh, rẻ hơn)" },
  { id: "claude-haiku-5-5", label: "Claude Haiku 5.5 (nhanh nhất)" },
];
const OTHER = "__other__";

// How much of the .claude configuration reaches the model (app/review/claude_kit.py).
const LIMITS: { key: Key; label: string; hint: string }[] = [
  {
    key: "claude_file_chars",
    label: "Ký tự tối đa mỗi file .claude",
    hint: "Một rule / skill / agent / CLAUDE.md dài hơn chỉ được đưa phần đầu (“chỉ đưa vào một phần”).",
  },
  {
    key: "fast_guidance_chars",
    label: "Tổng ký tự .claude ở lượt review đầu",
    hint: "Lặp lại ở mỗi phần khi diff lớn bị chia nhỏ. File chọn cho thay đổi vượt tổng này bị “không đưa vào”.",
  },
  {
    key: "deep_instructions_chars",
    label: "Tổng ký tự .claude ở lượt review sâu",
    hint: "Lượt sâu vẫn tự mở (Read) được file chưa đưa vào, nhưng đưa sẵn thì chắc chắn được áp dụng.",
  },
];

/** Admin card: review settings that override their .env default (MAX_REVIEW_LOC, the review
 * model, the base URL of the links sent out). */
export function ReviewSettings() {
  const settings = useApi<ReviewSetting[]>("/admin/review-settings");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [customModel, setCustomModel] = useState(false);
  const find = (key: Key) => settings.data?.find((s) => s.key === key);
  const loc = find("max_review_loc");
  const agentLoc = find("review_agent_min_loc");
  const model = find("llm_model");
  const link = find("public_url");

  async function save(key: Key, value: number | string | null) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await settings.mutate(await api.put<ReviewSetting[]>("/admin/review-settings", { [key]: value }), false);
      setNotice(value === null ? "Đã trả về giá trị mặc định." : "Đã lưu. Áp dụng cho review tiếp theo.");
      if (key === "llm_model") setCustomModel(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const field = (e: FormEvent<HTMLFormElement>, name: string) =>
    String(new FormData(e.currentTarget).get(name) ?? "").trim();

  const known = model ? MODELS.some((m) => m.id === model.value) : true;
  const showCustom = customModel || !known;

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

      {model && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const picked = field(e, "llm_model");
            save("llm_model", picked === OTHER ? field(e, "custom_model") : picked);
          }}
          style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 420 }}>
            <span className="small muted">Model dùng để review ({model.env})</span>
            <select
              key={String(model.value)}
              name="llm_model"
              defaultValue={known ? String(model.value) : OTHER}
              onChange={(e) => setCustomModel(e.target.value === OTHER)}
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
              <option value={OTHER}>Khác (nhập id model)…</option>
            </select>
          </label>
          {showCustom && (
            <label style={{ display: "grid", gap: 4, maxWidth: 420 }}>
              <span className="small muted">Id model, ví dụ claude-sonnet-5-5 hoặc opus</span>
              <input
                key={String(model.value)}
                name="custom_model"
                required
                maxLength={100}
                defaultValue={known ? "" : String(model.value)}
              />
            </label>
          )}
          <p className="muted small">
            Dùng cho cả lượt review nhanh, lượt review sâu và hồ sơ dự án. Model mạnh hơn bắt được nhiều lỗi hơn nhưng
            chậm hơn và tốn nhiều lượt dùng của gói hơn. Đang dùng: <code>{String(model.value)}</code>
            {model.overridden ? ` · mặc định: ${model.default}.` : " (mặc định)."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu model
            </button>
            {model.overridden && (
              <button type="button" onClick={() => save("llm_model", null)} disabled={busy}>
                Về mặc định ({String(model.default)})
              </button>
            )}
          </div>
        </form>
      )}

      {link && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("public_url", field(e, "public_url"));
          }}
          style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 420 }}>
            <span className="small muted">Địa chỉ trong link gửi đi (thay {link.env})</span>
            <input
              key={String(link.value)}
              name="public_url"
              required
              maxLength={300}
              placeholder="https://codepulse.company.vn"
              defaultValue={String(link.value)}
            />
          </label>
          <p className="muted small">
            Link xem kết quả review trong Telegram, webhook thông báo và email báo cáo dùng địa chỉ này, ví dụ{" "}
            <code>{String(link.value)}/reviews/42</code>. Nhập tên miền (abc.xyz) thì tự thêm https://. Đăng nhập và
            CORS vẫn theo {link.env}.{" "}
            {link.overridden ? `Mặc định: ${link.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu địa chỉ
            </button>
            {link.overridden && (
              <button type="button" onClick={() => save("public_url", null)} disabled={busy}>
                Về mặc định ({String(link.default)})
              </button>
            )}
          </div>
        </form>
      )}

      {LIMITS.map(({ key, label, hint }) => {
        const s = find(key);
        if (!s) return null;
        return (
          <form
            key={key}
            onSubmit={(e) => {
              e.preventDefault();
              save(key, Number(field(e, key)));
            }}
            style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}
          >
            <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
              <span className="small muted">
                {label} ({s.env})
              </span>
              <input
                key={String(s.value)}
                name={key}
                type="number"
                required
                min={s.minimum ?? undefined}
                max={s.maximum ?? undefined}
                step={1000}
                defaultValue={Number(s.value)}
              />
            </label>
            <p className="muted small">
              {hint} Khoảng 4 ký tự = 1 token; số lớn hơn thì mỗi review tốn nhiều lượt dùng của gói hơn.{" "}
              {s.overridden ? `Mặc định trong .env: ${s.default}.` : "Đang dùng giá trị trong .env."}
            </p>
            <div className="toolbar">
              <button type="submit" disabled={busy}>
                Lưu
              </button>
              {s.overridden && (
                <button type="button" onClick={() => save(key, null)} disabled={busy}>
                  Về mặc định ({String(s.default)})
                </button>
              )}
            </div>
          </form>
        );
      })}

      {loc && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("max_review_loc", Number(field(e, "max_review_loc")));
          }}
          style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Số dòng thay đổi tối đa mỗi MR/commit ({loc.env})</span>
            <input
              key={String(loc.value)}
              name="max_review_loc"
              type="number"
              required
              min={loc.minimum ?? undefined}
              max={loc.maximum ?? undefined}
              defaultValue={Number(loc.value)}
            />
          </label>
          <p className="muted small">
            Diff lớn hơn ngưỡng này bị bỏ qua với lý do “MR quá lớn”. Diff lớn được chia thành nhiều phần
            (MAX_TOKENS_PER_CHUNK) nên vẫn review được, nhưng tốn nhiều lượt dùng của gói hơn.{" "}
            {loc.overridden ? `Mặc định trong .env: ${loc.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu
            </button>
            {loc.overridden && (
              <button type="button" onClick={() => save("max_review_loc", null)} disabled={busy}>
                Về mặc định ({String(loc.default)})
              </button>
            )}
          </div>
        </form>
      )}

      {agentLoc && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("review_agent_min_loc", Number(field(e, "review_agent_min_loc")));
          }}
          style={{ display: "grid", gap: 10, padding: "0 18px 14px" }}
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Số dòng thay đổi tối thiểu để review sâu ({agentLoc.env})</span>
            <input
              key={String(agentLoc.value)}
              name="review_agent_min_loc"
              type="number"
              required
              min={agentLoc.minimum ?? undefined}
              max={agentLoc.maximum ?? undefined}
              defaultValue={Number(agentLoc.value)}
            />
          </label>
          <p className="muted small">
            Đếm dòng thêm + dòng xoá của các file được review. Diff nhỏ hơn chỉ review nhanh (đọc diff, không mở mã
            nguồn); từ ngưỡng này trở lên, repo bật cờ “Sâu” được review sâu (tốn thêm LLM). Đặt 0 để luôn review sâu.{" "}
            {agentLoc.overridden ? `Mặc định trong .env: ${agentLoc.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu
            </button>
            {agentLoc.overridden && (
              <button type="button" onClick={() => save("review_agent_min_loc", null)} disabled={busy}>
                Về mặc định ({String(agentLoc.default)})
              </button>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
