"use client";

/**
 * DPI scorecard of one person and month (docs/specs/25 §8 R2, R6) and the month's cycle (§5):
 * self review, the lead's 30 points, calibration, disputes, finalize after the 1:1.
 */
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { Modal } from "@/components/CriteriaParts";
import { ConfidencePill, MonthPicker, RatingPill, ScoreBar, ShadowBanner, useMonth } from "@/components/DpiParts";
import { Icon } from "@/components/icons";
import { Alert, Card, ChartTip, EmptyState, Loading, Message, PageHeader, Pill } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/api";
import {
  type Evidence,
  GROUPS,
  GROUP_LABEL,
  METRIC_HELP,
  type MetricView,
  RATING_LABEL,
  STATUS_LABEL,
  STATUS_STEPS,
  type ScorecardView,
  monthLabel,
  pct,
} from "@/lib/dpi";
import { date } from "@/lib/format";

type Tab = "card" | "trend" | "cycle";

function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) return <p className="muted small">Không có mục nào.</p>;
  return (
    <ul className="dpi-evidence">
      {items.slice(0, 30).map((e, i) => (
        <li key={`${e.kind}-${e.id ?? e.ref ?? i}`}>
          {e.ok === false && <Icon name="alertCircle" size={12} />}
          {e.ok === true && <Icon name="check" size={12} />}
          <span className="muted small">{e.ref ?? e.kind}</span>{" "}
          {e.url ? (
            <a href={e.url} target="_blank" rel="noreferrer">
              {e.label || e.ref || e.kind}
            </a>
          ) : (
            <span>{e.label || e.ref || e.kind}</span>
          )}
          {e.reworked !== undefined && (
            <span className="muted small">
              {" "}
              · thêm {e.added} dòng, xoá lại {e.reworked}
            </span>
          )}
          {e.share !== undefined && <span className="muted small"> · trách nhiệm {Math.round(e.share * 100)}%</span>}
        </li>
      ))}
    </ul>
  );
}

function detailText(key: string, d: Record<string, unknown>): string {
  const n = (k: string) => (d[k] === undefined || d[k] === null ? "—" : String(d[k]));
  switch (key) {
    case "A1":
      return `${n("points")} / ${n("target")} điểm · ${n("issues_with_complexity")}/${n("issues_closed")} issue có complexity · nghỉ ${n("leave_days")} ngày`;
    case "A2":
      return `${n("contributions")} đóng góp đã xác nhận`;
    case "B1":
      return `Review TB ${n("review_avg")} · gate ${n("quality_gate_pass_rate")}% · pipeline ${n("pipeline_pass_rate")}% (${n("reviews")} review)`;
    case "B2":
      return `${n("defects")} lỗi production · phạt ${n("penalty")}`;
    case "B3":
      return `Coverage ${n("coverage")}% · ${n("testing_per_100_loc")} finding testing / 100 LOC`;
    case "B4":
      return `Rework ${n("rework_rate")}% (${n("reworked")}/${n("added")} dòng)`;
    case "C1":
      return `Trung vị ${n("author_hours_median")} giờ / MR · ${n("vs_repo")}× repo (${n("mrs")} MR)`;
    case "C2":
      return `${n("on_time")}/${n("issues")} issue đúng hạn`;
    case "C3":
      return `${n("compliant")}/${n("units")} đạt · ${n("direct_commits")} commit đẩy thẳng · ${n("missing_issue")} MR thiếu issue · ${n("not_reviewed")} chưa review`;
    case "D1":
      return `${n("counted")} comment được tính (trần ${n("cap")}) · ${n("reviewed_mrs")} MR đã review`;
    case "D2":
      return `${n("outside_reviews")} review ngoài module · ${n("shared_items")} chia sẻ`;
    default:
      return "";
  }
}

function MetricRow({ k, m, onDispute }: { k: string; m: MetricView; onDispute?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dpi-metric">
      <button type="button" className="dpi-metric-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="dpi-key">{k}</span>
        <span className="dpi-metric-label">
          {m.label}
          {m.source === "manager" && <span className="muted small"> · lead chấm</span>}
          {m.overridden && (
            <Pill tone="info" title="Giá trị đổi sau khiếu nại">
              đã điều chỉnh
            </Pill>
          )}
        </span>
        <span className="dpi-points">
          {m.points === null ? <span className="muted">—</span> : m.points.toFixed(1)}
          <span className="muted small"> / {m.max}</span>
        </span>
        <span className="dpi-chev" aria-hidden="true">
          <Icon name={open ? "chevronUp" : "chevronDown"} size={14} />
        </span>
        <span className="dpi-metric-bar">
          <ScoreBar value={m.value === null ? null : m.value * 100} label={m.label} />
          {m.value === null ? (
            <span className="muted small">không có dữ liệu</span>
          ) : (
            m.confidence !== "high" && <span className="muted small">{m.confidence === "low" ? "ít dữ liệu" : "tin cậy vừa"}</span>
          )}
        </span>
      </button>
      {open && (
        <div className="dpi-metric-body">
          <p className="muted small">{METRIC_HELP[k]}</p>
          {m.source === "auto" && <p className="small">{m.value === null ? "Không có dữ liệu tháng này: không tính, làm giảm độ phủ." : detailText(k, m.detail)}</p>}
          <EvidenceList items={m.evidence} />
          {onDispute && (
            <button type="button" className="link sm" onClick={onDispute}>
              Khiếu nại chỉ số này
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Stepper({ status, overdue, deadlines }: { status: ScorecardView["review"]["status"]; overdue: boolean; deadlines: ScorecardView["review"]["deadlines"] }) {
  const at = STATUS_STEPS.indexOf(status === "draft" ? "self_review" : status);
  return (
    <ol className="dpi-steps" aria-label="Quy trình tháng">
      {STATUS_STEPS.map((s, i) => (
        <li key={s} className={i < at ? "done" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>
          {STATUS_LABEL[s]}
          {deadlines[s] && <span className="muted small"> · hạn {date(deadlines[s] ?? null)}</span>}
          {i === at && overdue && <Pill tone="warn">quá hạn</Pill>}
        </li>
      ))}
    </ol>
  );
}

export default function DpiScorecard({ params }: { params: Promise<{ userId: string }> }) {
  const { userId } = use(params);
  const [month, setMonth] = useMonth();
  const [tab, setTab] = useState<Tab>("card");
  const { data, error, mutate } = useApi<ScorecardView>(month ? `/dpi/people/${userId}?month=${month}` : null, {
    keepPreviousData: true,
    // While the AI suggests the lead's points (a background job), poll until it is in.
    refreshInterval: (latest) => (latest?.ai_suggestion?.status === "queued" || latest?.ai_suggestion?.status === "running" ? 3000 : 0),
  });
  const [message, setMessage] = useState<string | null>(null);
  const [disputeFor, setDisputeFor] = useState<string | null>(null);
  // A dispute form belongs to the month it was opened on.
  useEffect(() => setDisputeFor(null), [month]);

  if (error) return <Loading error={error} />;
  if (!data || !month) return <Loading rows={6}>Đang tính scorecard…</Loading>;
  const c = data.card;
  const tabs: SubTab<Tab>[] = [
    { key: "card", label: "Scorecard" },
    { key: "trend", label: "Xu hướng 6 tháng" },
    { key: "cycle", label: "Quy trình tháng", count: data.disputes.filter((d) => !d.decision).length || undefined },
  ];
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      setMessage(ok);
      await mutate();
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  };
  const dataMonth = data.month.slice(0, 7);
  const base = `/dpi/people/${userId}/months/${dataMonth}`;

  return (
    <div className="dpi-page stack">
      <PageHeader
        eyebrow={<Link href="/dpi">DPI hàng tháng</Link>}
        title={data.user.name ?? `#${userId}`}
        subtitle={`${monthLabel(month)} · profile ${c.profile_label}${c.profile_assigned === false ? " (chưa gán level, tạm dùng Middle)" : ""} · rubric ${c.rubric_version}`}
        actions={<MonthPicker value={month} onChange={setMonth} />}
      />
      <ShadowBanner on={data.shadow} />
      <Message text={message} onClose={() => setMessage(null)} />

      <section className="card dpi-hero" aria-label="Tổng điểm">
        <div className="dpi-total" aria-label="Tổng điểm">
          <span className="dpi-total-value">{c.total === null ? "—" : c.total.toFixed(1)}</span>
          <span className="muted small">/ 100</span>
        </div>
        <div className="dpi-hero-meta">
          <div className="dpi-row">
            <RatingPill rating={c.rating} />
            <ConfidencePill value={c.confidence} />
            {c.provisional && <Pill tone="warn" title="30 điểm của quản lý chưa chấm">Tạm tính</Pill>}
            {c.adjustment !== 0 && <Pill tone="info" title={data.review.adjustment_reason ?? undefined}>Hiệu chỉnh {c.adjustment > 0 ? "+" : ""}{c.adjustment}</Pill>}
          </div>
          <p className="muted small">
            Độ phủ dữ liệu {pct(c.coverage)} · tự động {c.split.auto.toFixed(1)}/{c.split.auto_max.toFixed(0)} · quản lý {c.split.manager.toFixed(1)}/
            {c.split.manager_max.toFixed(0)} · rolling 3 tháng {data.rolling.value === null ? "—" : data.rolling.value.toFixed(1)}
            {data.rolling.partial && " (thiếu tháng)"}
          </p>
          <Stepper status={data.review.status} overdue={data.review.overdue} deadlines={data.review.deadlines} />
        </div>
        <div className="dpi-groups">
          {GROUPS.map((g) => (
            <div key={g} className="dpi-group-mini">
              <span className="small dpi-ellipsis" title={GROUP_LABEL[g]}>
                {g} · {GROUP_LABEL[g]}
              </span>
              <ScoreBar value={c.groups[g]?.ratio === null || c.groups[g]?.ratio === undefined ? null : (c.groups[g].ratio ?? 0) * 100} label={GROUP_LABEL[g]} />
              <span className="small muted">
                {c.groups[g]?.score === null ? "—" : c.groups[g]?.score?.toFixed(1)} / {c.groups[g]?.weight}
              </span>
            </div>
          ))}
        </div>
      </section>
      {c.flags?.split_mrs && c.flags.split_mrs.length > 0 && (
        <Alert tone="warning">
          Có {c.flags.split_mrs.length} trường hợp nhiều MR (≥ 3) cho cùng một issue trong một ngày: lead nên xem có tách MR bất thường không.
        </Alert>
      )}

      <SubTabs id="dpi-card" label="Scorecard" tabs={tabs} value={tab} onChange={setTab} variant="bar" />
      {tab === "card" && (
        <SubPanel id="dpi-card" tab="card">
          <div className="stack">
          {c.contribution && c.contribution.new_work + c.contribution.refactor + c.contribution.help_others > 0 && (
            <Contribution c={c.contribution} />
          )}
          <div className="dpi-group-grid">
            {GROUPS.map((g) => (
              <Card key={g} title={`${g} · ${GROUP_LABEL[g]}`} collapsible={false} subtitle={`Trọng số ${c.groups[g]?.weight ?? 0} với profile ${c.profile_label}`}>
                {(c.groups[g]?.metrics ?? [])
                  .filter((k) => c.metrics[k])
                  .map((k) => (
                    <MetricRow key={k} k={k} m={c.metrics[k]} onDispute={data.can.dispute ? () => setDisputeFor(k) : undefined} />
                  ))}
              </Card>
            ))}
          </div>
          </div>
        </SubPanel>
      )}
      {tab === "trend" && (
        <SubPanel id="dpi-card" tab="trend">
          <div className="stack">
          <Growth g={data.growth} />
          <Card title="Xu hướng (R6 Competency & Growth)" collapsible={false} subtitle="Tổng điểm và tỉ lệ đạt của 5 nhóm, 6 tháng gần nhất">
            {data.trend.every((t) => t.total === null) ? (
              <EmptyState title="Chưa có điểm các tháng trước." />
            ) : (
              <div style={{ height: 300 }}>
                <ResponsiveContainer>
                  <LineChart
                    data={data.trend.map((t) => ({
                      month: t.month.slice(0, 7),
                      total: t.total,
                      ...Object.fromEntries(GROUPS.map((g) => [g, t.groups[g] === null || t.groups[g] === undefined ? null : Math.round((t.groups[g] ?? 0) * 100)])),
                    }))}
                    margin={{ top: 8, right: 16, bottom: 4, left: 0 }}
                  >
                    <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                    <XAxis dataKey="month" fontSize={12} tickLine={false} />
                    <YAxis domain={[0, 100]} fontSize={12} tickLine={false} axisLine={false} />
                    <Tooltip content={<ChartTip />} />
                    <Legend />
                    <Line dataKey="total" name="Tổng" stroke="var(--primary)" strokeWidth={3} connectNulls />
                    {GROUPS.map((g, i) => (
                      <Line key={g} dataKey={g} name={`${g} ${GROUP_LABEL[g]}`} stroke={`var(--chart-${i + 1})`} strokeDasharray="4 3" dot={false} connectNulls />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            <table className="table compact">
              <thead>
                <tr>
                  <th>Tháng</th>
                  <th className="num">Tổng</th>
                  <th>Xếp loại</th>
                  <th>Bản</th>
                </tr>
              </thead>
              <tbody>
                {data.trend.map((t) => (
                  <tr key={t.month}>
                    <td>{monthLabel(t.month)}</td>
                    <td className="num">{t.total === null ? "—" : t.total.toFixed(1)}</td>
                    <td>{t.rating ? RATING_LABEL[t.rating] : "—"}</td>
                    <td className="muted small">{t.kind === "final" ? "đã chốt" : t.kind ? "tạm tính" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          </div>
        </SubPanel>
      )}
      {tab === "cycle" && (
        <SubPanel id="dpi-card" tab="cycle">
          {/* Keyed by person and month: the forms start from that month's data, never another's. */}
          <Cycle key={`${userId}-${dataMonth}-${data.review.id ?? "new"}`} data={data} base={base} month={dataMonth} userId={userId} run={run} />
        </SubPanel>
      )}
      {disputeFor && data.can.dispute && (
        <DisputeModal
          metric={disputeFor}
          onClose={() => setDisputeFor(null)}
          onSend={(reason) =>
            run(() => api.post(`${base}/disputes`, { metric: disputeFor, reason }), "Đã gửi khiếu nại; lead sẽ trả lời trước khi chốt.").then(() => setDisputeFor(null))
          }
        />
      )}
    </div>
  );
}

type Suggested = NonNullable<NonNullable<ScorecardView["ai_suggestion"]>["result"]>["metrics"][string];

/** A form row from the AI's suggestion: the lead still reads, edits and saves it. */
function fromSuggestion(g: Suggested): { score: string; comment: string; evidence: string } {
  return {
    score: g.score === null ? "" : String(g.score),
    comment: g.reason,
    evidence: g.evidence.map((e) => e.url ?? e.label).join("\n"),
  };
}

function AiSuggestBar({
  s,
  onAsk,
  onApplyAll,
}: {
  s: ScorecardView["ai_suggestion"];
  onAsk: () => void;
  onApplyAll: () => void;
}) {
  const running = s?.status === "queued" || s?.status === "running";
  return (
    <div className="dpi-ai-bar">
      <div className="dpi-row">
        <button type="button" className="sm" disabled={running} onClick={onAsk}>
          {running ? (
            <>
              <span className="spinner inline" aria-hidden="true" /> AI đang đọc bằng chứng…
            </>
          ) : s?.status === "done" ? (
            "AI gợi ý lại"
          ) : (
            "AI gợi ý điểm"
          )}
        </button>
        {s?.status === "done" && s.result && (
          <button type="button" className="ghost sm" onClick={onApplyAll}>
            Dùng tất cả gợi ý (vẫn phải bấm Lưu)
          </button>
        )}
        <span className="muted small">
          AI đọc MR, issue, finding, comment review, tự khai và phản hồi đồng nghiệp của tháng rồi đề xuất điểm kèm lý do và bằng chứng; lead
          quyết định. Tốn ngân sách LLM.
        </span>
      </div>
      {s?.status === "failed" && <Alert tone="error">AI gợi ý lỗi{s.error ? `: ${s.error}` : ""}.</Alert>}
      {s?.status === "done" && s.result?.summary && <p className="small">{s.result.summary}</p>}
    </div>
  );
}

const READINESS: Record<ScorecardView["growth"]["promotion_readiness"], { label: string; tone: "good" | "info" | "warn" | undefined }> = {
  ready: { label: "Sẵn sàng cân nhắc thăng tiến", tone: "good" },
  near: { label: "Đang tiến tới", tone: "info" },
  not_yet: { label: "Chưa", tone: "warn" },
  unknown: { label: "Chưa đủ tháng đã chốt", tone: undefined },
};

function Growth({ g }: { g: ScorecardView["growth"] }) {
  const r = READINESS[g.promotion_readiness];
  return (
    <Card title="Năng lực theo trục" collapsible={false} subtitle="Mức đạt trung bình 3 tháng gần nhất có dữ liệu và xu hướng trong 6 tháng (R6)">
      <div className="dpi-axes">
        {g.axes.map((a) => (
          <div key={a.group} className="dpi-axis">
            <span className="small">{a.label}</span>
            <ScoreBar value={a.level === null ? null : a.level * 100} label={a.label} />
            <span className="small muted">
              {a.level === null ? "—" : `${Math.round(a.level * 100)}%`}
              {a.trend !== null && a.trend !== 0 && (
                <span className={a.trend > 0 ? "tone-good" : "tone-bad"}>
                  {" "}
                  {a.trend > 0 ? "▲" : "▼"} {Math.abs(Math.round(a.trend * 100))}
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
      <p className="small">
        Sẵn sàng thăng tiến: <Pill tone={r.tone}>{r.label}</Pill> <span className="muted">{g.why}</span>
      </p>
    </Card>
  );
}

function Contribution({ c }: { c: NonNullable<ScorecardView["card"]["contribution"]> }) {
  const total = c.new_work + c.refactor + c.help_others;
  const parts = [
    { key: "new_work", label: "Việc mới", value: c.new_work, color: "var(--chart-1)" },
    { key: "refactor", label: "Refactor", value: c.refactor, color: "var(--chart-4)" },
    { key: "help_others", label: "Sửa code người khác vừa viết", value: c.help_others, color: "var(--chart-2)" },
  ];
  return (
    <Card title="Loại đóng góp" collapsible={false} subtitle="Dòng code thêm theo loại, trong cửa sổ đo rework (không tính điểm)">
      <div className="dpi-split" role="img" aria-label={parts.map((p) => `${p.label} ${p.value} dòng`).join(", ")}>
        {parts.map((p) => (
          <span key={p.key} style={{ flexGrow: p.value, background: p.color }} />
        ))}
      </div>
      <p className="small">
        {parts.map((p) => (
          <span key={p.key} style={{ marginRight: 12 }}>
            <span className="chart-legend-swatch" style={{ background: p.color }} /> {p.label}: {p.value.toLocaleString("vi-VN")} ({total ? Math.round((100 * p.value) / total) : 0}%)
          </span>
        ))}
        <span className="muted">· Churn (tự xoá lại trong 21 ngày): {c.churn.toLocaleString("vi-VN")} dòng</span>
      </p>
    </Card>
  );
}

function DisputeModal({ metric, onClose, onSend }: { metric: string; onClose: () => void; onSend: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <Modal
      title={`Khiếu nại chỉ số ${metric}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose}>
            Huỷ
          </button>
          <button type="button" className="primary" disabled={reason.trim().length < 10} onClick={() => onSend(reason.trim())}>
            Gửi khiếu nại
          </button>
        </>
      }
    >
      <textarea
        rows={3}
        aria-label={`Lý do khiếu nại ${metric}`}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Vì sao chỉ số này chưa đúng? (ít nhất 10 ký tự)"
        style={{ width: "100%" }}
      />
    </Modal>
  );
}

function Cycle({
  data,
  base,
  month,
  userId,
  run,
}: {
  data: ScorecardView;
  base: string;
  month: string;
  userId: string;
  run: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const r = data.review;
  const [leave, setLeave] = useState(String(r.leave_days));
  const given = Object.fromEntries(data.assessments.map((a) => [a.metric, a]));
  const [scores, setScores] = useState<Record<string, { score: string; comment: string; evidence: string }>>(() =>
    Object.fromEntries(
      data.manager_metrics.map((m) => [
        m.key,
        { score: given[m.key] ? String(given[m.key].score) : "", comment: given[m.key]?.comment ?? "", evidence: (given[m.key]?.evidence ?? []).join("\n") },
      ]),
    ),
  );
  const [adjust, setAdjust] = useState({ value: String(r.adjustment), reason: r.adjustment_reason ?? "" });
  const [final, setFinal] = useState({ day: r.one_on_one_at ?? "", plan: r.action_plan ?? "" });
  const [answers, setAnswers] = useState<Record<number, { answer: string; value: string }>>({});
  const filled = data.manager_metrics.filter((m) => scores[m.key]?.score !== "");

  return (
    <div className="dpi-cycle">
      <Card title="1. Tự khai" collapsible={false} subtitle="Đóng góp tiêu biểu, việc ngoài Git, blocker: ghi trong Hồ sơ tháng. Số ngày nghỉ điều chỉnh target A1.">
        <p className="small">
          <Link href={`/members/${userId}/evidence?month=${month}`}>Mở Hồ sơ tháng {monthLabel(month)}</Link> — mỗi mục được tính vào A2 / D2 khi lead duyệt mục đó.
        </p>
        {data.self_items.filter((i) => i.counted).length > 0 && (
          <ul className="dpi-items">
            {data.self_items
              .filter((i) => i.counted)
              .map((i) => (
                <li key={i.id}>
                  <span className="muted small">{i.kind}</span> {i.title}
                  {i.note && <span className="muted small"> — {i.note}</span>}{" "}
                  {i.approved === true ? <Pill tone="good">đã duyệt</Pill> : i.approved === false ? <Pill tone="bad">không tính</Pill> : <Pill>chờ duyệt</Pill>}
                  {data.can.assess && (
                    <span className="dpi-item-actions">
                      <button type="button" className="sm" disabled={i.approved === true} onClick={() => run(() => api.post(`/dpi/items/${i.id}/approve`, { approved: true }), "Đã duyệt mục tự khai.")}>
                        Duyệt
                      </button>
                      <button type="button" className="ghost sm" disabled={i.approved === false} onClick={() => run(() => api.post(`/dpi/items/${i.id}/approve`, { approved: false }), "Mục này không được tính.")}>
                        Không tính
                      </button>
                    </span>
                  )}
                </li>
              ))}
          </ul>
        )}
        {r.submitted_at ? (
          <p className="muted small">Đã gửi {date(r.submitted_at)} · nghỉ {r.leave_days} ngày.</p>
        ) : data.can.self ? (
          <div className="dpi-row">
            <label>
              Ngày nghỉ trong tháng <input type="number" min={0} max={23} step={0.5} value={leave} onChange={(e) => setLeave(e.target.value)} style={{ width: 80 }} />
            </label>
            <button type="button" className="primary" onClick={() => run(() => api.post(`${base}/submit`, { leave_days: Number(leave) || 0 }), "Đã gửi phần tự khai.")}>
              Gửi phần tự khai
            </button>
          </div>
        ) : (
          <p className="muted small">Chưa gửi.</p>
        )}
      </Card>

      <Card title="2. Lead chấm 30 điểm" collapsible={false} subtitle="Mỗi chỉ số: điểm, nhận xét và ít nhất một bằng chứng (link MR, issue, tài liệu).">
        {data.b5_suggestions.length > 0 && (
          <div className="small">
            MR gợi ý để chấm B5: <EvidenceList items={data.b5_suggestions} />
          </div>
        )}
        <p className="small muted">
          Phản hồi đồng nghiệp (D3): {data.peer.count} phản hồi{data.peer.average !== null && `, TB ${data.peer.average}/5`}
          {data.peer.hidden && " (dưới 3 phản hồi: nội dung ẩn để giữ ẩn danh)"}
        </p>
        {data.peer.items.length > 0 && (
          <ul className="small">
            {data.peer.items.map((p, i) => (
              <li key={i}>
                {p.score}/5{p.comment ? ` — ${p.comment}` : ""}
                {p.from ? <span className="muted"> ({p.from})</span> : null}
              </li>
            ))}
          </ul>
        )}
        {data.can.assess && (
          <AiSuggestBar
            s={data.ai_suggestion}
            onAsk={() => run(() => api.post(`${base}/suggest`), "Đã nhờ AI gợi ý; kết quả hiện ở từng chỉ số sau ít phút.")}
            onApplyAll={() => {
              const res = data.ai_suggestion?.result;
              if (!res) return;
              const next = { ...scores };
              for (const m of data.manager_metrics) {
                const g = res.metrics[m.key];
                if (g && g.score !== null) next[m.key] = fromSuggestion(g);
              }
              setScores(next);
            }}
          />
        )}
        <div className="dpi-assess">
          {data.manager_metrics.map((m) => {
            const v = scores[m.key];
            const read = !data.can.assess;
            const g = data.ai_suggestion?.result?.metrics[m.key];
            return (
              <fieldset key={m.key} className="dpi-assess-item" disabled={read}>
                <legend>
                  {m.key} · {m.label} <span className="muted small">(0–{m.max})</span>
                </legend>
                <input
                  type="number"
                  min={0}
                  max={m.max}
                  step={0.5}
                  value={v.score}
                  aria-label={`Điểm ${m.key}`}
                  onChange={(e) => setScores({ ...scores, [m.key]: { ...v, score: e.target.value } })}
                  style={{ width: 80 }}
                />
                <textarea rows={2} aria-label={`Nhận xét ${m.key}`} placeholder="Nhận xét" value={v.comment} onChange={(e) => setScores({ ...scores, [m.key]: { ...v, comment: e.target.value } })} />
                <textarea rows={2} aria-label={`Bằng chứng ${m.key}`} placeholder="Bằng chứng: mỗi dòng một link MR / issue / tài liệu" value={v.evidence} onChange={(e) => setScores({ ...scores, [m.key]: { ...v, evidence: e.target.value } })} />
                {given[m.key]?.by && <span className="muted small">Chấm bởi {given[m.key].by}</span>}
                {g && data.can.assess && (
                  <div className="dpi-ai-hint">
                    <span>
                      <Pill tone="info">AI gợi ý: {g.score === null ? "chưa đủ bằng chứng" : `${g.score}/${g.max}`}</Pill>{" "}
                      <span className="muted small">tin cậy {g.confidence === "high" ? "cao" : g.confidence === "medium" ? "vừa" : "thấp"}</span>
                    </span>
                    {g.reason && <span className="small">{g.reason}</span>}
                    {g.evidence.length > 0 && (
                      <span className="small muted">
                        Dựa trên:{" "}
                        {g.evidence.map((e, i) => (
                          <span key={e.ref}>
                            {i > 0 && ", "}
                            {e.url ? (
                              <a href={e.url} target="_blank" rel="noreferrer">
                                {e.label}
                              </a>
                            ) : (
                              e.label
                            )}
                          </span>
                        ))}
                      </span>
                    )}
                    {g.score !== null && (
                      <button type="button" className="link sm" onClick={() => setScores({ ...scores, [m.key]: fromSuggestion(g) })}>
                        Dùng gợi ý cho {m.key}
                      </button>
                    )}
                  </div>
                )}
              </fieldset>
            );
          })}
        </div>
        {data.can.assess && (
          <button
            type="button"
            className="primary"
            disabled={!filled.length}
            onClick={() =>
              run(
                () =>
                  api.put(`${base}/assessment`, {
                    items: filled.map((m) => ({
                      metric: m.key,
                      score: Number(scores[m.key].score),
                      comment: scores[m.key].comment.trim(),
                      evidence: scores[m.key].evidence.split("\n").map((s) => s.trim()).filter(Boolean),
                    })),
                  }),
                "Đã lưu phần chấm của lead.",
              )
            }
          >
            Lưu phần chấm ({filled.length}/{data.manager_metrics.length})
          </button>
        )}
      </Card>

      <Card title="3. Hiệu chỉnh" collapsible={false} subtitle="Người có quyền hiệu chỉnh điều chỉnh ±10 điểm trên tổng, bắt buộc ghi lý do.">
        {data.can.calibrate ? (
          <div className="dpi-row">
            <input type="number" min={-10} max={10} step={0.5} value={adjust.value} onChange={(e) => setAdjust({ ...adjust, value: e.target.value })} style={{ width: 80 }} aria-label="Điểm hiệu chỉnh" />
            <input value={adjust.reason} onChange={(e) => setAdjust({ ...adjust, reason: e.target.value })} placeholder="Lý do (≥ 10 ký tự)" style={{ flex: 1, minWidth: 200 }} />
            <button type="button" disabled={adjust.reason.trim().length < 10} onClick={() => run(() => api.post(`${base}/calibrate`, { adjustment: Number(adjust.value) || 0, reason: adjust.reason.trim() }), "Đã hiệu chỉnh.")}>
              Lưu hiệu chỉnh
            </button>
          </div>
        ) : (
          <p className="small muted">{r.adjustment ? `Đã hiệu chỉnh ${r.adjustment}: ${r.adjustment_reason}` : "Chưa hiệu chỉnh."}</p>
        )}
      </Card>

      <Card title="Khiếu nại chỉ số" collapsible={false}>
        {data.disputes.length === 0 ? (
          <p className="small muted">Không có khiếu nại. Người được đánh giá khiếu nại từ tab Scorecard (mở một chỉ số).</p>
        ) : (
          data.disputes.map((d) => (
            <div key={d.id} className="dpi-dispute">
              <p className="small">
                <strong>{d.metric}</strong> · {d.reason}
              </p>
              {d.decision ? (
                <p className="small muted">
                  {d.decision === "accepted" ? "Chấp nhận" : "Từ chối"}: {d.answer}
                </p>
              ) : data.can.decide ? (
                <div className="dpi-row">
                  <input aria-label={`Trả lời khiếu nại ${d.metric}`} placeholder="Trả lời" value={answers[d.id]?.answer ?? ""} onChange={(e) => setAnswers({ ...answers, [d.id]: { answer: e.target.value, value: answers[d.id]?.value ?? "" } })} style={{ flex: 1, minWidth: 180 }} />
                  <input aria-label={`Giá trị mới ${d.metric}`} placeholder="Giá trị mới 0–1" value={answers[d.id]?.value ?? ""} onChange={(e) => setAnswers({ ...answers, [d.id]: { answer: answers[d.id]?.answer ?? "", value: e.target.value } })} style={{ width: 120 }} />
                  <button type="button" onClick={() => run(() => api.post(`/dpi/disputes/${d.id}/decide`, { decision: "accepted", answer: answers[d.id]?.answer ?? "", value: answers[d.id]?.value ? Number(answers[d.id].value) : null }), "Đã chấp nhận khiếu nại.")}>
                    Chấp nhận
                  </button>
                  <button type="button" className="ghost" onClick={() => run(() => api.post(`/dpi/disputes/${d.id}/decide`, { decision: "rejected", answer: answers[d.id]?.answer ?? "" }), "Đã từ chối khiếu nại.")}>
                    Từ chối
                  </button>
                </div>
              ) : (
                <p className="small muted">Đang chờ trả lời.</p>
              )}
            </div>
          ))
        )}
      </Card>

      <Card title="4. Chốt sau 1:1" collapsible={false} subtitle="Ngày 1:1 và kế hoạch hành động tháng sau. Bản chốt tự khoá sau 7 ngày.">
        {r.finalized_at ? (
          <div className="small">
            <p>
              Chốt {date(r.finalized_at)} · 1:1 ngày {date(r.one_on_one_at)}
              {r.locked_at && ` · khoá ${date(r.locked_at)}`}
            </p>
            <p style={{ whiteSpace: "pre-wrap" }}>{r.action_plan}</p>
          </div>
        ) : data.can.finalize ? (
          <div className="dpi-final">
            <label>
              Ngày 1:1 <input type="date" value={final.day} onChange={(e) => setFinal({ ...final, day: e.target.value })} />
            </label>
            <textarea rows={3} aria-label="Action plan tháng sau" placeholder="Action plan tháng sau (≥ 10 ký tự)" value={final.plan} onChange={(e) => setFinal({ ...final, plan: e.target.value })} />
            <button type="button" className="primary" disabled={!final.day || final.plan.trim().length < 10} onClick={() => run(() => api.post(`${base}/finalize`, { one_on_one_at: final.day, action_plan: final.plan.trim() }), "Đã chốt kỳ đánh giá.")}>
              Chốt kỳ đánh giá
            </button>
          </div>
        ) : (
          <p className="small muted">Chốt được khi lead đã chấm đủ 30 điểm và không còn khiếu nại chờ trả lời.</p>
        )}
        {data.can.lock && (
          <button type="button" className="ghost" onClick={() => run(() => api.post(`${base}/lock`), "Đã khoá.")}>
            Khoá ngay
          </button>
        )}
      </Card>
    </div>
  );
}
