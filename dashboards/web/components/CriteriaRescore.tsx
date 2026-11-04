"use client";

import { useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { DateRangePicker } from "@/components/DateRangePicker";
import { Icon } from "@/components/icons";
import { Alert, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import { localDay } from "@/lib/format";
import type { Person, ProjectCriteriaSet, Repo } from "@/lib/types";

/* POST /business-projects/{bp}/criteria-rescore(/preview), GET …/criteria-rescore (docs/specs/22 §10) */

interface Preview {
  reviews: number;
  findings: number;
  sets: {
    set_id: number;
    name: string;
    version: number;
    is_primary: boolean;
    never_scored: number;
    older_version: number;
    will_change: number;
    avg_change: number;
  }[];
  unplaced_findings: number;
  reviews_to_classify: number;
  classify_calls: number;
  max_classify_calls: number;
  thin: { reason: string; label: string; count: number }[];
  thin_total: number;
  thin_sample: { review_id: number; reason: string; loc: number | null }[];
  est_classify_usd: number;
  may_rereview: boolean;
}

interface RunStatus {
  status: "none" | "queued" | "started" | "finished" | "failed" | "deferred" | "scheduled" | "canceled" | "stopped";
  progress?: RunProgress | null;
  /** Jobs ahead of it on the reviews queue (while queued). */
  queue_position?: number | null;
  /** Started, but no step for 5 minutes: the worker may have stopped. */
  stale?: boolean;
  server_time?: string;
  enqueued_at?: string | null;
  ended_at?: string | null;
  result?: {
    reviews: number;
    scored: number;
    classified_findings: number;
    llm_calls: number;
    cost_usd: number;
    budget_stopped: boolean;
    provider_stopped?: boolean;
    cap_stopped?: boolean;
    failed_calls?: number;
    rereview_queued: number;
    rereview_skipped: number;
    error?: string;
  } | null;
  error?: string | null;
}

const RUNNING = new Set(["queued", "started", "deferred", "scheduled"]);
type Step = "filters" | "confirm" | "progress";

interface RunProgress {
  phase?: "scoring" | "classifying" | "saving" | "finished";
  done: number;
  total: number;
  llm_calls?: number;
  failed_calls?: number;
  cost_usd?: number;
  classified_findings?: number;
  rereview_queued?: number;
  started_at?: string;
  updated_at?: string;
}

const PHASE: Record<NonNullable<RunProgress["phase"]>, string> = {
  scoring: "Đang chấm điểm",
  classifying: "Claude đang xếp finding vào thành phần",
  saving: "Đang lưu một lô (100 review)",
  finished: "Đang hoàn tất",
};

const n = (v: number) => v.toLocaleString("vi-VN");

/** "2 phút 05 giây" from seconds. */
function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} giây`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} phút ${String(s % 60).padStart(2, "0")} giây`;
  return `${Math.floor(m / 60)} giờ ${String(m % 60).padStart(2, "0")} phút`;
}

/** Seconds between two ISO times (the server's clock on both sides: no skew). */
function between(from?: string | null, to?: string | null): number | null {
  if (!from || !to) return null;
  const d = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  return Number.isFinite(d) ? d : null;
}

/**
 * "Chấm lại điểm" of one dự án: recompute the criteria scores of finished reviews from the
 * findings they already have, under the sets' current versions. Filters first, then a dry run
 * with what will change and a warning to confirm, then the progress of the background job.
 * Claude places the findings never placed in a component (optional, paid); thin results may be
 * reviewed again for real (optional, priced like any review).
 */
export function CriteriaRescore({ projectId, projectName, sets }: { projectId: number; projectName: string; sets: ProjectCriteriaSet[] }) {
  // Which step the dialog opens on: the chip shows the last run, the button starts a new one.
  const [open, setOpen] = useState<Step | null>(null);
  const base = `/business-projects/${projectId}/criteria-rescore`;
  const status = useApi<RunStatus>(base, {
    refreshInterval: (latest) => (latest && RUNNING.has(latest.status) ? 2000 : 0),
  });
  const running = status.data ? RUNNING.has(status.data.status) : false;
  return (
    <>
      <span className="rescore-entry">
        <button type="button" className="sm" onClick={() => setOpen("filters")} disabled={!sets.length} title={sets.length ? undefined : "Gán ít nhất một bộ tiêu chí trước"}>
          <Icon name="sync" size={14} /> {running ? "Đang chấm lại…" : "Chấm lại điểm…"}
        </button>
        <RunChip status={status.data} onOpen={() => setOpen("progress")} />
      </span>
      {open !== null && (
        <RescoreDialog
          base={base}
          projectId={projectId}
          projectName={projectName}
          sets={sets}
          status={status.data}
          onStarted={() => void status.mutate()}
          initialStep={open}
          onClose={() => setOpen(null)}
        />
      )}
    </>
  );
}

function RescoreDialog({
  base,
  projectId,
  projectName,
  sets,
  status,
  onStarted,
  onClose,
  initialStep,
}: {
  base: string;
  projectId: number;
  projectName: string;
  sets: ProjectCriteriaSet[];
  status?: RunStatus;
  onStarted: () => void;
  onClose: () => void;
  initialStep: Step;
}) {
  const running = status ? RUNNING.has(status.status) : false;
  const [step, setStep] = useState<Step>(running ? "progress" : initialStep);
  const [setIds, setSetIds] = useState<number[]>(sets.map((s) => s.set_id));
  const [repo, setRepo] = useState("");
  const [people, setPeople] = useState<number[]>([]);
  const [range, setRange] = useState({ from: "", to: "" });
  const [classify, setClassify] = useState(true);
  const [rereview, setRereview] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const repos = useApi<Repo[]>("/repos");
  const persons = useApi<Person[]>("/people");
  const ownRepos = (repos.data ?? []).filter((r) => r.business_project_id === projectId);

  const body = {
    set_ids: setIds.length === sets.length ? [] : setIds,
    project_id: repo ? Number(repo) : null,
    user_ids: people,
    ...(range.from ? { from: range.from } : {}),
    ...(range.to ? { to: range.to } : {}),
  };

  async function runPreview() {
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.post<Preview>(`${base}/preview`, body));
      setUnderstood(false);
      setStep("confirm");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await api.post(base, { ...body, classify, rereview_thin: rereview, confirm: true });
      onStarted();
      setStep("progress");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const changing = preview?.sets.reduce((n, s) => n + s.will_change + s.never_scored, 0) ?? 0;
  const footer =
    step === "filters" ? (
      <>
        <button type="button" className="ghost" onClick={onClose}>
          Đóng
        </button>
        <button type="button" className="primary" onClick={() => void runPreview()} disabled={busy || setIds.length === 0}>
          {busy ? "Đang tính…" : "Xem trước"}
        </button>
      </>
    ) : step === "confirm" ? (
      <>
        <button type="button" className="ghost" onClick={() => setStep("filters")} disabled={busy}>
          Quay lại
        </button>
        <button type="button" className="danger" onClick={() => void start()} disabled={busy || !understood || !preview?.reviews}>
          {busy ? "Đang xếp hàng…" : `Chấm lại ${preview?.reviews ?? 0} review`}
        </button>
      </>
    ) : (
      <>
        {!running && (
          <button type="button" className="ghost" onClick={() => setStep("filters")}>
            Chấm lại lượt mới
          </button>
        )}
        <button type="button" className="primary" onClick={onClose}>
          Đóng
        </button>
      </>
    );

  return (
    <Modal title={`Chấm lại điểm theo tiêu chí · ${projectName}`} onClose={onClose} busy={busy} width={680} footer={footer}>
      <div className="adm-form rescore">
        {error && <Alert tone="error" onClose={() => setError(null)}>{error}</Alert>}

        {step === "filters" && (
          <>
            <p className="muted small" style={{ margin: 0 }}>
              Tính lại điểm theo tiêu chí <b>từ kết quả review đã có</b> (các finding), theo phiên bản hiện tại của bộ tiêu chí.
              Code <b>không</b> được review lại, trừ khi bạn chọn review lại các kết quả quá sơ sài bên dưới.
            </p>
            <fieldset className="rescore-sets">
              <legend className="small muted">Bộ tiêu chí</legend>
              {sets.map((s) => (
                <label key={s.set_id} className="inline-check">
                  <input
                    type="checkbox"
                    checked={setIds.includes(s.set_id)}
                    onChange={(e) => setSetIds(e.target.checked ? [...setIds, s.set_id] : setIds.filter((x) => x !== s.set_id))}
                  />
                  {s.name} {s.version ? `v${s.version}` : ""} {s.is_primary && <Pill tone="primary">bộ chính</Pill>}
                </label>
              ))}
            </fieldset>
            <div className="rescore-grid">
              <label className="adm-field">
                <span className="small muted">Repo</span>
                <select value={repo} onChange={(e) => setRepo(e.target.value)}>
                  <option value="">Mọi repo của dự án</option>
                  {ownRepos.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.path_with_namespace}
                    </option>
                  ))}
                </select>
              </label>
              <label className="adm-field">
                <span className="small muted">Người (giữ Ctrl để chọn nhiều)</span>
                <select
                  multiple
                  size={4}
                  value={people.map(String)}
                  onChange={(e) => setPeople([...e.target.selectedOptions].map((o) => Number(o.value)))}
                >
                  {(persons.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.full_name ?? p.primary_email ?? `#${p.id}`}
                    </option>
                  ))}
                </select>
              </label>
              <div className="adm-field">
                <span className="small muted">Thời gian (bỏ trống: mọi lúc)</span>
                <DateRangePicker label="Thời gian" value={range} max={localDay()} maxSpan={3660} onChange={setRange} />
              </div>
            </div>
            <fieldset className="rescore-options">
              <legend className="small muted">Tuỳ chọn</legend>
              <label className="inline-check">
                <input type="checkbox" checked={classify} onChange={(e) => setClassify(e.target.checked)} />
                <span>
                  <b>Dùng Claude xếp các finding chưa được xếp vào thành phần</b> (review cũ, hoặc bộ gán sau): một lượt gọi nhỏ
                  mỗi review, đọc nội dung finding đã có, tính vào ngân sách LLM. Bỏ chọn: các finding đó đi theo loại lỗi.
                </span>
              </label>
              <label className="inline-check">
                <input type="checkbox" checked={rereview} onChange={(e) => setRereview(e.target.checked)} />
                <span>
                  <b>Review lại các kết quả quá sơ sài</b> (thay đổi từ 30 dòng mà không có finding và gần như không có nhận
                  xét): chạy review thật, tốn như một review mới, mỗi thay đổi tối đa một lần. MR chỉ review lại được khi còn
                  mở và chưa có commit mới.
                </span>
              </label>
            </fieldset>
          </>
        )}

        {step === "confirm" && preview && (
          <>
            {preview.reviews === 0 ? (
              <Alert tone="info">Không có review nào đã xong khớp bộ lọc này.</Alert>
            ) : (
              <Alert tone="warning">
                <b>Điểm theo tiêu chí của {preview.reviews} review sẽ được ghi đè</b> bằng điểm tính theo phiên bản hiện tại
                (khoảng {changing} điểm thay đổi hoặc mới có). Chỉ số và báo cáo dựa trên điểm tiêu chí sẽ đổi theo. Điểm review
                0–100 và kết luận merge <b>không</b> đổi.
              </Alert>
            )}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Bộ tiêu chí</th>
                    <th className="num">Chưa có điểm</th>
                    <th className="num">Phiên bản cũ</th>
                    <th className="num">Sẽ đổi điểm</th>
                    <th className="num">Đổi TB</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.sets.map((s) => (
                    <tr key={s.set_id}>
                      <td>
                        {s.name} v{s.version}
                      </td>
                      <td className="num">{s.never_scored}</td>
                      <td className="num">{s.older_version}</td>
                      <td className="num">{s.will_change}</td>
                      <td className="num">{s.avg_change > 0 ? "+" : ""}{s.avg_change}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="rescore-facts small">
              <li>
                {preview.findings} finding; <b>{preview.unplaced_findings}</b> finding ở {preview.reviews_to_classify} review chưa được xếp
                vào thành phần
                {classify
                  ? ` → Claude sẽ xếp: ${preview.classify_calls} lượt gọi, ước tính ~${preview.est_classify_usd.toFixed(2)} USD; dừng khi chạm ngân sách tháng.`
                  : " → đi theo loại lỗi (chọn tuỳ chọn Claude để xếp chính xác hơn)."}
                {classify && preview.classify_calls > preview.max_classify_calls && (
                  <b>
                    {" "}
                    Mỗi lần chạy tối đa {preview.max_classify_calls} lượt gọi: phần còn lại đi theo loại lỗi; thu hẹp bộ lọc rồi
                    chạy thêm lượt nữa.
                  </b>
                )}
              </li>
              <li>
                <b>{preview.thin_total}</b> review quá sơ sài
                {preview.thin.length > 0 && `: ${preview.thin.map((t) => `${t.count} ${t.label}`).join("; ")}`}
                {preview.thin_total > 0 &&
                  (rereview
                    ? preview.may_rereview
                      ? " → sẽ được review lại (tốn như review mới)."
                      : " → bạn không có quyền chạy lại review ở dự án này; bỏ tuỳ chọn review lại."
                    : " → chỉ chấm lại từ kết quả hiện có.")}
              </li>
            </ul>
            {preview.reviews > 0 && (
              <label className="inline-check rescore-ack">
                <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
                <span>
                  Tôi hiểu điểm theo tiêu chí của các review trên sẽ bị ghi đè
                  {classify && preview.reviews_to_classify > 0 ? ", Claude sẽ được gọi (tốn chi phí)" : ""}
                  {rereview && preview.thin_total > 0 ? `, và ${preview.thin_total} review sẽ chạy lại` : ""}.
                </span>
              </label>
            )}
          </>
        )}

        {step === "progress" && <Progress status={status} />}
      </div>
    </Modal>
  );
}

/** Beside the button, dialog closed or not: is a run going, and how far (docs/specs/22 §10). */
function RunChip({ status, onOpen }: { status?: RunStatus; onOpen: () => void }) {
  if (!status || status.status === "none") return null;
  const p = status.progress;
  const pct = p && p.total ? Math.round((100 * p.done) / p.total) : 0;
  let text: string;
  let tone = "";
  if (status.status === "queued") {
    text = status.queue_position ? `Đang chờ hàng đợi · ${status.queue_position} việc phía trước` : "Đang chờ worker nhận việc";
  } else if (RUNNING.has(status.status)) {
    text = p ? `Đang chấm lại ${n(p.done)}/${n(p.total)} review · ${pct}%` : "Đang bắt đầu…";
    if (status.stale) {
      text = `Không thấy tiến triển · ${n(p?.done ?? 0)}/${n(p?.total ?? 0)}`;
      tone = " warn";
    }
  } else if (status.status === "failed" || status.result?.error) {
    text = "Lượt chấm lại gần nhất bị lỗi";
    tone = " bad";
  } else if (status.status === "finished" && status.result) {
    const at = status.ended_at ? new Date(status.ended_at).toLocaleString("vi-VN", { hour12: false }) : "";
    text = `Đã chấm lại ${n(status.result.scored)} review${at ? ` · ${at}` : ""}`;
    tone = " good";
  } else {
    return null;
  }
  const live = RUNNING.has(status.status);
  return (
    <button type="button" className={`rescore-chip${tone}`} onClick={onOpen} title="Mở để xem chi tiết tiến độ">
      {live && !status.stale && <span className="spinner inline" aria-hidden="true" />}
      <span aria-live="polite">{text}</span>
      {live && status.status !== "queued" && (
        <span className="rescore-bar mini" aria-hidden="true">
          <span style={{ width: `${pct}%` }} />
        </span>
      )}
    </button>
  );
}

function Progress({ status }: { status?: RunStatus }) {
  if (!status || status.status === "none") return <p className="muted">Đang gửi việc cho worker…</p>;
  const p = status.progress;
  const pct = p && p.total ? Math.round((100 * p.done) / p.total) : 0;
  if (RUNNING.has(status.status)) {
    const queued = status.status === "queued";
    const elapsed = between(p?.started_at, status.server_time);
    const quiet = between(p?.updated_at, status.server_time);
    // Remaining time from the pace so far; only once a tenth is done (the start is uneven).
    const eta = p && elapsed && p.done >= Math.max(1, p.total / 10) && p.done < p.total ? (elapsed / p.done) * (p.total - p.done) : null;
    const steps: { key: string; label: string; state: "done" | "now" | "todo" }[] = [
      { key: "queued", label: "Chờ worker", state: queued ? "now" : "done" },
      {
        key: "run",
        label: p?.phase ? PHASE[p.phase] : "Chấm điểm",
        state: queued ? "todo" : p?.phase === "finished" ? "done" : "now",
      },
      { key: "done", label: "Xong", state: p?.phase === "finished" ? "now" : "todo" },
    ];
    return (
      <div className="rescore-progress">
        <ol className="rescore-steps" aria-label="Các bước">
          {steps.map((s) => (
            <li key={s.key} className={s.state} aria-current={s.state === "now" ? "step" : undefined}>
              {s.state === "done" ? <Icon name="check" size={12} /> : s.state === "now" ? <span className="spinner inline" aria-hidden="true" /> : null}
              {s.label}
            </li>
          ))}
        </ol>
        {queued ? (
          <p style={{ margin: "0 0 8px" }}>
            Đang chờ trong hàng đợi review
            {status.queue_position ? ` (${status.queue_position} việc phía trước, chạy theo thứ tự đến trước)` : ""}…
          </p>
        ) : (
          <>
            <p style={{ margin: "0 0 8px" }}>
              <strong>
                {p ? `${n(p.done)} / ${n(p.total)} review` : "Đang bắt đầu…"} · {pct}%
              </strong>
            </p>
            <div className="rescore-bar" role="progressbar" aria-label="Tiến độ chấm lại" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
              <span style={{ width: `${pct}%` }} />
            </div>
            {p && (
              <dl className="rescore-stats">
                <div>
                  <dt>Đã chạy</dt>
                  <dd>{elapsed !== null ? duration(elapsed) : "—"}</dd>
                </div>
                <div>
                  <dt>Còn lại (ước tính)</dt>
                  <dd>{eta !== null ? `~${duration(eta)}` : "—"}</dd>
                </div>
                <div>
                  <dt>Claude</dt>
                  <dd>
                    {n(p.llm_calls ?? 0)} lượt · {(p.cost_usd ?? 0).toFixed(2)} USD
                    {p.failed_calls ? <span className="tone-bad-text"> · {p.failed_calls} lỗi</span> : null}
                  </dd>
                </div>
                <div>
                  <dt>Finding đã xếp</dt>
                  <dd>{n(p.classified_findings ?? 0)}</dd>
                </div>
                {(p.rereview_queued ?? 0) > 0 && (
                  <div>
                    <dt>Review lại</dt>
                    <dd>{n(p.rereview_queued ?? 0)} đã xếp hàng</dd>
                  </div>
                )}
              </dl>
            )}
            <p className="muted small" style={{ margin: "8px 0 0" }}>
              {quiet !== null ? `Cập nhật ${duration(quiet)} trước. ` : ""}
              Có thể đóng hộp thoại; việc vẫn chạy nền và tiến độ hiện cạnh nút "Chấm lại điểm".
            </p>
          </>
        )}
        {status.stale && (
          <Alert tone="warning">
            Không thấy tiến triển {quiet !== null ? `trong ${duration(quiet)}` : "một lúc"}: worker có thể đã dừng (khởi động lại?).
            Phần đã lưu theo lô vẫn được giữ; nếu vẫn đứng yên, đợi lượt này hết thời gian rồi chạy lại.
          </Alert>
        )}
      </div>
    );
  }
  if (status.status === "failed")
    return <Alert tone="error">Lượt chấm lại bị lỗi{status.error ? `: ${status.error}` : ""}. Phần đã chấm (theo lô 100 review) vẫn được giữ; thử lại sau.</Alert>;
  const r = status.result;
  if (!r) return <Alert tone="info">Lượt chấm lại gần nhất: {status.status}.</Alert>;
  if (r.error) return <Alert tone="error">{r.error}</Alert>;
  return (
    <Alert tone={r.budget_stopped ? "warning" : "success"}>
      Đã chấm lại {r.scored} / {r.reviews} review. Claude xếp {r.classified_findings} finding ({r.llm_calls} lượt gọi, {r.cost_usd.toFixed(2)} USD).
      {r.budget_stopped && " Dừng gọi Claude vì chạm ngân sách tháng: các finding còn lại đi theo loại lỗi."}
      {r.provider_stopped && ` Dừng gọi Claude sau ${r.failed_calls ?? 0} lượt lỗi liên tiếp: các finding còn lại đi theo loại lỗi.`}
      {r.cap_stopped && " Chạm giới hạn lượt gọi của một lần chạy: chạy lại để xếp tiếp."}
      {r.rereview_queued > 0 && ` Đã xếp hàng review lại ${r.rereview_queued} review sơ sài.`}
      {r.rereview_skipped > 0 && ` ${r.rereview_skipped} review sơ sài không review lại được (MR đã đóng / có commit mới, hoặc repo tắt review).`}
    </Alert>
  );
}
