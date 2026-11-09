"use client";

import { type FormEvent, useState } from "react";

import { Alert, Loading } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { LlmHealth, ReviewSetting } from "@/lib/types";

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
  const budget = find("monthly_llm_budget_usd");
  const autoClose = find("auto_close_findings");
  const autoDays = find("auto_close_days");
  const listMax = find("review_commits_list_max");
  const resume = find("llm_auto_resume");
  const probeEvery = find("llm_probe_interval_min");
  const accountRetry = find("llm_account_retry_min");
  const closeQuota = find("close_requests_per_day");

  async function save(key: Key, value: number | string | boolean | null) {
    await saveAll({ [key]: value });
    if (key === "llm_model") setCustomModel(false);
  }

  async function saveAll(values: Partial<Record<Key, number | string | boolean | null>>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await settings.mutate(await api.put<ReviewSetting[]>("/admin/review-settings", values), false);
      setNotice(Object.values(values).every((v) => v === null) ? "Đã trả về giá trị mặc định." : "Đã lưu. Áp dụng cho review tiếp theo.");
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
      {!settings.data && !settings.error && <Loading rows={4} />}
      <div className="settings-grid">

      {model && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const picked = field(e, "llm_model");
            save("llm_model", picked === OTHER ? field(e, "custom_model") : picked);
          }}
          className="setting-tile"
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

      {budget && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("monthly_llm_budget_usd", Number(field(e, "monthly_llm_budget_usd")));
          }}
          className="setting-tile"
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Ngân sách LLM mỗi tháng, USD ({budget.env})</span>
            <input
              key={String(budget.value)}
              name="monthly_llm_budget_usd"
              type="number"
              required
              min={budget.minimum ?? undefined}
              max={budget.maximum ?? undefined}
              step={1}
              defaultValue={Number(budget.value)}
            />
          </label>
          <p className="muted small">
            Tổng chi trong tháng (review + dựng review knowledge + chạy lệnh .claude) chạm mức này thì review dừng với
            lỗi “monthly LLM budget reached”, chạy lại được khi tăng ngân sách hoặc sang tháng mới. Cảnh báo từ 80%. Đặt
            0 để không giới hạn.{" "}
            {budget.overridden ? `Mặc định trong .env: ${budget.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu ngân sách
            </button>
            {budget.overridden && (
              <button type="button" onClick={() => save("monthly_llm_budget_usd", null)} disabled={busy}>
                Về mặc định ({String(budget.default)})
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
          className="setting-tile"
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
            className="setting-tile"
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
          className="setting-tile"
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

      {listMax && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("review_commits_list_max", Number(field(e, "review_commits_list_max")));
          }}
          className="setting-tile"
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Số commit tối đa mỗi repo ở Yêu cầu review ({listMax.env})</span>
            <input
              key={String(listMax.value)}
              name="review_commits_list_max"
              type="number"
              required
              min={listMax.minimum ?? undefined}
              max={listMax.maximum ?? undefined}
              defaultValue={Number(listMax.value)}
            />
          </label>
          <p className="muted small">
            Danh sách commit lấy thẳng từ GitLab; số lớn hơn thì mở danh sách chậm hơn (GitLab trả 100 commit mỗi trang).{" "}
            {listMax.overridden ? `Mặc định trong .env: ${listMax.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu
            </button>
            {listMax.overridden && (
              <button type="button" onClick={() => save("review_commits_list_max", null)} disabled={busy}>
                Về mặc định ({String(listMax.default)})
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
          className="setting-tile"
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

      {resume && probeEvery && (
        <LlmResumeForm
          key={`${String(resume.value)}-${String(probeEvery.value)}`}
          enabled={resume.value === true}
          minutes={Number(probeEvery.value)}
          min={probeEvery.minimum ?? 5}
          max={probeEvery.maximum ?? 240}
          env={`${resume.env}, ${probeEvery.env}`}
          busy={busy}
          onSave={(on, minutes) => saveAll(on ? { llm_auto_resume: true, llm_probe_interval_min: minutes } : { llm_auto_resume: false })}
        />
      )}

      {accountRetry && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("llm_account_retry_min", Number(field(e, "llm_account_retry_min")));
          }}
          className="setting-tile"
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Thử lại tài khoản Claude bị lỗi sau (phút) ({accountRetry.env})</span>
            <input
              key={String(accountRetry.value)}
              name="llm_account_retry_min"
              type="number"
              required
              min={accountRetry.minimum ?? undefined}
              max={accountRetry.maximum ?? undefined}
              defaultValue={Number(accountRetry.value)}
            />
          </label>
          <p className="muted small">
            Khi có nhiều tài khoản (Admin › LLM): tài khoản hết hạn mức mà không báo giờ reset được thử lại sau từng này phút, mỗi lần
            còn lỗi thì gấp đôi, tối đa 8 lần; báo giờ reset thì thử đúng giờ đó; lỗi đăng nhập thử lại sau 8 lần. Không gửi lệnh thử:
            lần review kế tiếp sau mốc đó tự thử lại tài khoản mặc định.{" "}
            {accountRetry.overridden ? `Mặc định trong .env: ${accountRetry.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu
            </button>
            {accountRetry.overridden && (
              <button type="button" onClick={() => save("llm_account_retry_min", null)} disabled={busy}>
                Về mặc định ({String(accountRetry.default)})
              </button>
            )}
          </div>
        </form>
      )}

      {autoClose && autoDays && (
        <AutoCloseForm
          key={`${String(autoClose.value)}-${String(autoDays.value)}`}
          enabled={autoClose.value === true}
          days={Number(autoDays.value)}
          env={`${autoClose.env}, ${autoDays.env}`}
          busy={busy}
          onSave={(on, days) => saveAll(on ? { auto_close_findings: true, auto_close_days: days } : { auto_close_findings: false })}
        />
      )}

      {closeQuota && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save("close_requests_per_day", Number(field(e, "close_requests_per_day")));
          }}
          className="setting-tile"
        >
          <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
            <span className="small muted">Yêu cầu đóng finding tối đa mỗi người trong 24 giờ ({closeQuota.env})</span>
            <input
              key={String(closeQuota.value)}
              name="close_requests_per_day"
              type="number"
              required
              min={closeQuota.minimum ?? 1}
              max={closeQuota.maximum ?? 1000}
              defaultValue={Number(closeQuota.value)}
            />
          </label>
          <p className="muted small">
            Tính chung yêu cầu gửi trên web và qua MCP; mỗi yêu cầu có thể tốn một lượt kiểm bằng model. Đây là mức mặc định:
            đặt số riêng cho một người ở Admin › Người &amp; Team › hồ sơ thành viên.{" "}
            {closeQuota.overridden ? `Mặc định trong .env: ${closeQuota.default}.` : "Đang dùng giá trị trong .env."}
          </p>
          <div className="toolbar">
            <button type="submit" disabled={busy}>
              Lưu
            </button>
            {closeQuota.overridden && (
              <button type="button" onClick={() => save("close_requests_per_day", null)} disabled={busy}>
                Về mặc định ({String(closeQuota.default)})
              </button>
            )}
          </div>
        </form>
      )}
      </div>
    </section>
  );
}

/** AUTO_CLOSE_FINDINGS: a switch, and once on, how many days back each review looks (required). */
function AutoCloseForm({
  enabled,
  days,
  env,
  busy,
  onSave,
}: {
  enabled: boolean;
  days: number;
  env: string;
  busy: boolean;
  onSave: (on: boolean, days: number) => Promise<void>;
}) {
  const [on, setOn] = useState(enabled);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const value = Number(new FormData(e.currentTarget).get("auto_close_days") ?? 0);
        void onSave(on, value);
      }}
      className="setting-tile"
    >
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
        <span>Tự đóng critical/high khi commit sau đã sửa ({env})</span>
      </label>
      {on && (
        <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
          <span className="small muted">Số ngày N: rà finding critical/high còn mở trong N ngày trước thay đổi</span>
          <input name="auto_close_days" type="number" required min={1} max={90} defaultValue={days >= 1 ? days : undefined} />
        </label>
      )}
      <p className="muted small">
        Mỗi review xong, CodePulse lấy các finding critical/high còn mở của repo trong N ngày (từ lịch sử, không review
        lại code cũ) và hỏi model chúng đã được sửa chưa, tốn thêm một lượt gọi nhỏ khi có finding cần rà. Model xem diff
        mới và code hiện tại trên nhánh mặc định của các file chứa finding (tối đa 6 file): lỗi đã sửa ở commit khác, dòng
        lỗi không còn trong diff nào, vẫn được đóng. Chỉ đóng khi model chỉ ra được chỗ sửa trong diff hoặc trong code hiện
        tại; finding vẫn giữ mức critical/high và vẫn tính điểm. Finding quá N ngày thì tác giả của thay đổi dùng “Yêu cầu
        đóng” ở trang review.
      </p>
      <div className="toolbar">
        <button type="submit" disabled={busy}>
          Lưu
        </button>
      </div>
    </form>
  );
}

const LLM_STATE: Record<LlmHealth["status"], { label: string; tone: string }> = {
  ok: { label: "Bình thường", tone: "ok" },
  down: { label: "Đang lỗi", tone: "bad" },
  recovering: { label: "Đã hoạt động lại, đang xếp review chờ", tone: "warn" },
};

/** LLM_AUTO_RESUME: a review that failed because the LLM was down (connection, quota, rate or
 * usage limit) is reviewed again by itself once a probe sees the LLM answer. */
function LlmResumeForm({
  enabled,
  minutes,
  min,
  max,
  env,
  busy,
  onSave,
}: {
  enabled: boolean;
  minutes: number;
  min: number;
  max: number;
  env: string;
  busy: boolean;
  onSave: (on: boolean, minutes: number) => Promise<void>;
}) {
  const [on, setOn] = useState(enabled);
  const health = useApi<LlmHealth>("/admin/llm-health", { refreshInterval: 30_000 });
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const h = health.data;
  const state = h ? LLM_STATE[h.status] : null;

  async function probe() {
    setProbing(true);
    setProbeError(null);
    try {
      await health.mutate(await api.post<LlmHealth>("/admin/llm-health/probe"), false);
    } catch (e) {
      setProbeError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setProbing(false);
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const value = Number(new FormData(e.currentTarget).get("llm_probe_interval_min") ?? 0);
        void onSave(on, value);
      }}
      className="setting-tile"
    >
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
        <span>Tự review lại khi LLM hoạt động lại ({env})</span>
      </label>
      {on && (
        <label style={{ display: "grid", gap: 4, maxWidth: 320 }}>
          <span className="small muted">Chu kỳ kiểm tra LLM khi đang lỗi (phút)</span>
          <input name="llm_probe_interval_min" type="number" required min={min} max={max} defaultValue={minutes} />
        </label>
      )}
      <p className="muted small">
        Khi LLM lỗi kết nối, hết hạn mức (rate/usage limit, quota) hoặc quá tải, review đó được đánh dấu “chờ LLM” và các review mới
        không gọi LLM nữa mà xếp hàng chờ. Cứ mỗi chu kỳ CodePulse gửi một lệnh rất nhỏ để thử; khi LLM trả lời được, mọi review đang chờ
        được đưa lại vào hàng đợi đúng một lần. Lỗi do câu trả lời hỏng hay hết ngân sách tháng không tính là LLM lỗi.
      </p>
      {h && state && (
        <div className="llm-health" data-tone={state.tone}>
          <div className="toolbar" style={{ alignItems: "center" }}>
            <strong>LLM: {state.label}</strong>
            {h.status === "down" && (
              <button type="button" className="sm" disabled={probing || !enabled} onClick={() => void probe()}>
                {probing ? "Đang đặt lịch…" : "Kiểm tra ngay"}
              </button>
            )}
          </div>
          <ul className="small">
            {h.status === "down" && h.down_since && <li>Lỗi từ {dateTime(h.down_since)}</li>}
            {h.status === "down" && h.next_probe_at && (
              <li>
                Lần kiểm tra kế tiếp: {dateTime(h.next_probe_at)} (đã thử {h.probes} lần)
              </li>
            )}
            {h.status !== "ok" && h.last_error && <li className="muted">Lỗi gần nhất: {h.last_error}</li>}
            <li>Review đang chờ LLM: {h.waiting_reviews}</li>
            {h.recovered_at && (
              <li className="muted">
                Phục hồi gần nhất: {dateTime(h.recovered_at)}
                {h.requeued_last !== null ? `, đã xếp lại ${h.requeued_last} review` : ""}
              </li>
            )}
          </ul>
          {probeError && <Alert tone="error">{probeError}</Alert>}
        </div>
      )}
      <div className="toolbar">
        <button type="submit" disabled={busy}>
          Lưu
        </button>
      </div>
    </form>
  );
}

