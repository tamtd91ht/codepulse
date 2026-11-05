"use client";

/**
 * DPI hàng tháng (docs/specs/25): R1 overview of the people one may see, the managers'
 * distribution, calibration, production defects, peer feedback, settings and data readiness.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { ConfidencePill, MonthPicker, RatingPill, ShadowBanner, useMonth } from "@/components/DpiParts";
import { Alert, Card, ChartTip, EmptyState, Loading, Message, PageHeader, Pill, StatStrip, StripStat } from "@/components/ui";
import { ApiError, api, can, useApi } from "@/lib/api";
import {
  type Aggregates,
  GROUPS,
  GROUP_LABEL,
  type Overview,
  RATINGS,
  RATING_LABEL,
  type Rating,
  STATUS_LABEL,
  monthLabel,
  pct,
} from "@/lib/dpi";
import { date } from "@/lib/format";
import type { BusinessProject, Me, Person, Team } from "@/lib/types";

type Tab = "overview" | "distribution" | "calibration" | "quality" | "delivery" | "defects" | "peer" | "settings" | "readiness";
const RATING_COLOR: Record<Rating, string> = {
  excellent: "var(--good)",
  good: "var(--chart-2)",
  meets: "var(--chart-1)",
  improve: "var(--warning)",
  below: "var(--danger)",
};

function Summary({ s }: { s: Aggregates }) {
  return (
    <StatStrip label="Tổng quan tháng">
      <StripStat label="Người có scorecard" value={s.people} />
      <StripStat label="Điểm DPI TB" value={s.avg_total} digits={1} />
      <StripStat label="Đúng hạn (C2) TB" value={s.avg_delivery_predictability} digits={0} unit="%" />
      <StripStat label="Pipeline thành công TB" value={s.avg_pipeline_pass_rate} digits={0} unit="%" />
      <StripStat label="Thời gian tác giả / MR" value={s.median_author_hours} digits={1} unit=" giờ" title="Trung vị (C1)" />
      <StripStat label="Độ phủ dữ liệu TB" value={s.avg_coverage} digits={0} unit="%" />
    </StatStrip>
  );
}

function RatingChart({ s }: { s: Aggregates }) {
  const data = [
    ...RATINGS.map((r) => ({ key: r, name: RATING_LABEL[r], value: s.ratings[r] ?? 0, color: RATING_COLOR[r] })),
    { key: "none", name: "Chưa đủ dữ liệu", value: s.unrated, color: "var(--faint)" },
  ];
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
          <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
          <XAxis type="number" allowDecimals={false} fontSize={12} />
          <YAxis type="category" dataKey="name" width={130} fontSize={12} tickLine={false} axisLine={false} />
          <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
          <Bar dataKey="value" name="Người" radius={[0, 4, 4, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.key} fill={d.color} />
            ))}
            <LabelList dataKey="value" position="right" fontSize={12} fill="var(--text)" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

interface RebuildStatus {
  status: "none" | "queued" | "started" | "finished" | "failed" | "deferred" | "scheduled" | "canceled" | "stopped";
  progress?: { phase: "sync" | "score" | "done"; done: number; total: number; current: string | null; updated_at: string } | null;
  result?: { synced: number; sync_failed: string[]; months: { month: string; scorecards?: { people: number; created: number; scored: number } }[] } | null;
  ended_at?: string | null;
  error?: string | null;
}

const LIVE = new Set(["queued", "started", "deferred", "scheduled"]);

/** "Chạy lại dữ liệu DPI": re-collect GitLab (optional), then compute the last months. */
function Rebuild({ onDone }: { onDone: () => void }) {
  const status = useApi<RebuildStatus>("/dpi/rebuild", {
    refreshInterval: (latest) => (latest && LIVE.has(latest.status) ? 3000 : 0),
  });
  const [months, setMonths] = useState(3);
  const [resync, setResync] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const s = status.data;
  const live = !!s && LIVE.has(s.status);
  const p = s?.progress;
  const pctDone = p && p.total ? Math.round((100 * p.done) / p.total) : 0;
  // A run that just finished: the overview reads the new scorecards.
  const ended = s?.status === "finished" ? (s.ended_at ?? null) : null;
  const [seenEnd, setSeenEnd] = useState<string | null>(null);
  useEffect(() => {
    if (ended && ended !== seenEnd) {
      if (seenEnd !== null) onDone();
      setSeenEnd(ended);
    }
  }, [ended, seenEnd, onDone]);
  const start = async () => {
    try {
      await api.post("/dpi/rebuild", { months, resync });
      setMessage(null);
      await status.mutate();
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  };
  return (
    <Card
      title="Dữ liệu DPI"
      collapsible={false}
      subtitle="Scorecard được tính tự động ngày 1 hằng tháng. Lần đầu (hoặc sau khi đổi quy trình, label, CI) hãy chạy lại: đồng bộ lại GitLab để lấy issue, checklist MR, coverage, test report của dữ liệu cũ, rồi tính các tháng gần nhất."
    >
      <Message text={message} onClose={() => setMessage(null)} />
      {live ? (
        <div>
          <p style={{ margin: "0 0 6px" }}>
            {s?.status === "queued" ? "Đang chờ worker nhận việc…" : p?.phase === "sync" ? `Đang đồng bộ GitLab: ${p.done}/${p.total} repo` : p?.phase === "score" ? `Đang tính scorecard: tháng ${p.current} (${p.done}/${p.total})` : "Đang bắt đầu…"}
            {p?.phase === "sync" && p.current && <span className="muted small"> · {p.current}</span>}
          </p>
          <div className="rescore-bar" role="progressbar" aria-label="Tiến độ chạy lại dữ liệu" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pctDone}>
            <span style={{ width: `${pctDone}%` }} />
          </div>
          <p className="muted small">Có thể rời trang; việc chạy nền. Đồng bộ lại toàn bộ repo có thể mất vài chục phút.</p>
        </div>
      ) : (
        <div className="dpi-row">
          <label>
            Tính lại{" "}
            <select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
              {[1, 3, 6].map((n) => (
                <option key={n} value={n}>
                  {n} tháng gần nhất
                </option>
              ))}
            </select>
          </label>
          <label className="small">
            <input type="checkbox" checked={resync} onChange={(e) => setResync(e.target.checked)} /> Đồng bộ lại GitLab trước (lâu hơn, cần cho lần đầu)
          </label>
          <button type="button" className="primary ink" onClick={() => void start()}>
            Chạy lại dữ liệu DPI
          </button>
        </div>
      )}
      {!live && s?.status === "finished" && s.result && (
        <p className="small muted">
          Lần gần nhất xong {s.ended_at ? new Date(s.ended_at).toLocaleString("vi-VN", { hour12: false }) : ""}: đồng bộ {s.result.synced} repo
          {s.result.sync_failed.length > 0 && `, lỗi ${s.result.sync_failed.length} (${s.result.sync_failed.slice(0, 3).join(", ")})`}; tính{" "}
          {s.result.months.map((m) => `${m.month.slice(0, 7)}: ${m.scorecards?.scored ?? 0} người`).join(" · ")}.
        </p>
      )}
      {!live && s?.status === "failed" && <Alert tone="error">Lần chạy lại gần nhất bị lỗi{s.error ? `: ${s.error}` : ""}.</Alert>}
    </Card>
  );
}

function OverviewTab({ month, me }: { month: string; me: Me }) {
  const [team, setTeam] = useState("");
  const [profile, setProfile] = useState("");
  const [bp, setBp] = useState("");
  const { data: teams } = useApi<Team[]>("/teams/visible");
  const { data: projects } = useApi<BusinessProject[]>("/business-projects");
  const { data: settings } = useApi<{ profiles: { key: string; label: string }[] }>("/dpi/settings");
  const query = new URLSearchParams({ month });
  if (team) query.set("team_id", team);
  if (profile) query.set("profile", profile);
  if (bp) query.set("business_project_id", bp);
  const { data, error, mutate } = useApi<Overview>(`/dpi/overview?${query}`, { keepPreviousData: true });
  if (error) return error.status === 403 ? <Alert tone="info">Bạn xem được scorecard của chính mình: <Link href={`/dpi/${me.id}?month=${month}`}>mở scorecard</Link>.</Alert> : <Loading error={error} />;
  if (!data) return <Loading rows={5} />;
  return (
    <>
      <div className="dpi-row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <label>
          Team{" "}
          <select value={team} onChange={(e) => setTeam(e.target.value)}>
            <option value="">Mọi người trong phạm vi</option>
            {(teams ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Dự án{" "}
          <select value={bp} onChange={(e) => setBp(e.target.value)}>
            <option value="">Mọi dự án</option>
            {(projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Level{" "}
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="">Mọi level</option>
            {(settings?.profiles ?? []).map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <span className="dpi-spacer" />
        <span className="dpi-row">
          <Link href={`/dpi/${me.id}?month=${month}`} className="button">
            Scorecard của tôi
          </Link>
        </span>
      </div>
      <ShadowBanner on={data.shadow} />
      {can(me, "dpi.admin") && <Rebuild onDone={() => void mutate()} />}
      <Summary s={data.summary} />
      <div className="dpi-two">
        <Card title="Phân bổ xếp loại" collapsible={false} subtitle="Không xếp loại khi dữ liệu không đủ tin cậy">
          <RatingChart s={data.summary} />
        </Card>
        <Card title="Theo người" collapsible={false} subtitle="Bản mới nhất mỗi người (đã chốt ưu tiên); bấm tên để mở scorecard">
          {data.rows.length === 0 ? (
            <EmptyState title="Chưa có scorecard tháng này.">
              Job ngày 1 tính tự động. Admin bấm “Chạy lại dữ liệu DPI” ở trên để đồng bộ và tính ngay.
            </EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Người</th>
                    <th>Profile</th>
                    <th className="num">Tổng</th>
                    <th>Xếp loại</th>
                    {GROUPS.map((g) => (
                      <th key={g} className="num" title={GROUP_LABEL[g]}>
                        {g}
                      </th>
                    ))}
                    <th>Bước</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.user.id}>
                      <td>
                        <Link href={`/dpi/${r.user.id}?month=${month}`}>{r.user.name ?? `#${r.user.id}`}</Link>
                        {r.provisional && (
                          <span className="muted small" title="Chưa có 30 điểm của lead">
                            {" "}
                            · tạm tính
                          </span>
                        )}
                      </td>
                      <td className="small">{r.profile}</td>
                      <td className="num">{r.total === null ? "—" : r.total.toFixed(1)}</td>
                      <td>
                        <RatingPill rating={r.rating} /> <ConfidencePill value={r.confidence} />
                      </td>
                      {GROUPS.map((g) => (
                        <td key={g} className="num small">
                          {pct(r.groups[g] ?? null)}
                        </td>
                      ))}
                      <td className="small">{r.status ? STATUS_LABEL[r.status] : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function DistributionTab({ month }: { month: string }) {
  const { data, error } = useApi<{ teams: ({ team: Team; hidden: boolean } & Partial<Aggregates>)[] }>(`/dpi/distribution?month=${month}`);
  if (error) return <Loading error={error} />;
  if (!data) return <Loading />;
  return (
    <Card title="Phân phối điểm theo team" collapsible={false} subtitle="Chỉ số tổng hợp, không theo từng người; không tính chính bạn; team dưới 5 người được ẩn">
      <table className="table compact">
        <thead>
          <tr>
            <th>Team</th>
            <th className="num">Người</th>
            <th className="num">DPI TB</th>
            {RATINGS.map((r) => (
              <th key={r} className="num">
                {RATING_LABEL[r]}
              </th>
            ))}
            <th className="num">Chưa đủ dữ liệu</th>
          </tr>
        </thead>
        <tbody>
          {data.teams.map((t) => (
            <tr key={t.team.id}>
              <td>{t.team.name}</td>
              <td className="num">{t.hidden ? "—" : t.people}</td>
              {t.hidden ? (
                <td colSpan={RATINGS.length + 2} className="muted small">
                  Ẩn (dưới 5 người, không tính bạn)
                </td>
              ) : (
                <>
                  <td className="num">{t.avg_total ?? "—"}</td>
                  {RATINGS.map((r) => (
                    <td key={r} className="num">
                      {t.ratings?.[r] ?? 0}
                    </td>
                  ))}
                  <td className="num">{t.unrated ?? 0}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

interface Calibration {
  overall: { people: number; avg_total: number | null; stdev: number };
  teams: { team: { id: number; name: string | null }; people: number; avg_total: number | null; off: boolean }[];
  profiles: Record<string, { people: number; avg_total: number }>;
  rows: {
    user: { id: number; name: string | null };
    profile: string;
    total: number | null;
    rating: Rating | null;
    status: string | null;
    adjustment: number;
    adjustment_reason: string | null;
    gap_vs_lead: number | null;
  }[];
}

function CalibrationTab({ month }: { month: string }) {
  const { data, error } = useApi<Calibration>(`/dpi/calibration?month=${month}`);
  if (error) return <Loading error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <StatStrip label="Mặt bằng chung">
        <StripStat label="Người" value={data.overall.people} />
        <StripStat label="DPI TB" value={data.overall.avg_total} digits={1} />
        <StripStat label="Độ lệch chuẩn" value={data.overall.stdev} digits={1} />
      </StatStrip>
      <div className="dpi-two">
        <Card title="Team" collapsible={false} subtitle="Cảnh báo khi TB team lệch TB chung > 10 điểm hoặc > 1 độ lệch chuẩn">
          <table className="table compact">
            <tbody>
              {data.teams.map((t) => (
                <tr key={t.team.id}>
                  <td>{t.team.name}</td>
                  <td className="num">{t.people} người</td>
                  <td className="num">{t.avg_total ?? "—"}</td>
                  <td>{t.off && <Pill tone="warn">lệch chuẩn</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4>Theo profile</h4>
          <ul className="small">
            {Object.entries(data.profiles).map(([k, v]) => (
              <li key={k}>
                {k}: {v.people} người, TB {v.avg_total}
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Từng người" collapsible={false} subtitle="“Lệch so với lead” = % phần tự động − % phần lead chấm; lệch lớn đáng xem lại">
          <div className="table-wrap">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Người</th>
                  <th className="num">Tổng</th>
                  <th>Xếp loại</th>
                  <th className="num">Lệch so với lead</th>
                  <th className="num">Hiệu chỉnh</th>
                  <th>Bước</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.user.id}>
                    <td>
                      <Link href={`/dpi/${r.user.id}?month=${month}`}>{r.user.name}</Link>
                    </td>
                    <td className="num">{r.total ?? "—"}</td>
                    <td>
                      <RatingPill rating={r.rating} />
                    </td>
                    <td className={`num${r.gap_vs_lead !== null && Math.abs(r.gap_vs_lead) >= 20 ? " tone-warn" : ""}`}>
                      {r.gap_vs_lead === null ? "—" : `${r.gap_vs_lead > 0 ? "+" : ""}${r.gap_vs_lead}`}
                    </td>
                    <td className="num" title={r.adjustment_reason ?? undefined}>
                      {r.adjustment || "—"}
                    </td>
                    <td className="small">{r.status ? STATUS_LABEL[r.status as keyof typeof STATUS_LABEL] : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  );
}

interface Defect {
  id: number;
  severity: string;
  issue: { id: number; iid: number; title: string | null; url: string | null; created_at: string | null; state: string | null };
  rca_status: "pending" | "confirmed" | "process";
  responsible: { user_id: number; share: number; name?: string | null }[];
  process_gaps: string[];
  note: string | null;
  suggestions: { kind: string; ref?: string; commit?: string; title?: string | null; author?: { user_id: number; name: string | null } | null }[];
}

const GAPS = [
  ["missing_test", "Thiếu test"],
  ["review_missed", "Review bỏ sót"],
  ["no_staging", "Thiếu môi trường kiểm thử"],
  ["unclear_requirement", "Yêu cầu chưa rõ"],
  ["monitoring", "Thiếu giám sát"],
] as const;

function DefectRow({ d, onSaved }: { d: Defect; onSaved: (msg: string) => void }) {
  const authors = d.suggestions.map((s) => s.author).filter((a): a is { user_id: number; name: string | null } => !!a);
  const [who, setWho] = useState<number | "">(d.responsible[0]?.user_id ?? authors[0]?.user_id ?? "");
  const [gaps, setGaps] = useState<string[]>(d.process_gaps);
  const [note, setNote] = useState(d.note ?? "");
  const save = async (status: "confirmed" | "process") => {
    try {
      await api.put(`/dpi/defects/${d.id}/rca`, {
        status,
        responsible: status === "confirmed" && who ? [{ user_id: who, share: 1 }] : [],
        process_gaps: gaps,
        note,
      });
      onSaved(status === "confirmed" ? "Đã xác nhận RCA." : "Đã ghi nhận là lỗi quy trình (không trừ cá nhân).");
    } catch (e) {
      onSaved(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  };
  return (
    <Card
      title={
        <>
          <Pill tone={d.severity === "P1" ? "bad" : "warn"}>{d.severity}</Pill>{" "}
          {d.issue.url ? (
            <a href={d.issue.url} target="_blank" rel="noreferrer">
              #{d.issue.iid} {d.issue.title}
            </a>
          ) : (
            `#${d.issue.iid} ${d.issue.title}`
          )}
        </>
      }
      subtitle={`Báo ${date(d.issue.created_at)} · RCA: ${d.rca_status === "pending" ? "chưa xác nhận" : d.rca_status === "confirmed" ? "đã xác nhận" : "lỗi quy trình"}`}
      collapsible={false}
    >
      {d.suggestions.length > 0 && (
        <ul className="small">
          {d.suggestions.map((s, i) => (
            <li key={i}>
              {s.kind === "revert" ? `Commit ${s.commit} bị revert` : `MR ${s.ref} ${s.title ?? ""} sửa cùng file trước khi lỗi`}
              {s.author?.name && <span className="muted"> · {s.author.name}</span>}
            </li>
          ))}
        </ul>
      )}
      <div className="dpi-row">
        <label>
          Người chịu trách nhiệm{" "}
          <select value={who} onChange={(e) => setWho(e.target.value ? Number(e.target.value) : "")}>
            <option value="">—</option>
            {authors.map((a) => (
              <option key={a.user_id} value={a.user_id}>
                {a.name}
              </option>
            ))}
            {d.responsible
              .filter((r) => !authors.some((a) => a.user_id === r.user_id))
              .map((r) => (
                <option key={r.user_id} value={r.user_id}>
                  {r.name}
                </option>
              ))}
          </select>
        </label>
        {GAPS.map(([key, label]) => (
          <label key={key} className="small">
            <input type="checkbox" checked={gaps.includes(key)} onChange={(e) => setGaps(e.target.checked ? [...gaps, key] : gaps.filter((g) => g !== key))} /> {label}
          </label>
        ))}
      </div>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ghi chú RCA" style={{ width: "100%", marginTop: 8 }} />
      <div className="dpi-row">
        <button type="button" className="primary" disabled={!who} onClick={() => void save("confirmed")}>
          Xác nhận RCA
        </button>
        <button type="button" onClick={() => void save("process")}>
          Lỗi quy trình, không quy cá nhân
        </button>
      </div>
    </Card>
  );
}

function DefectsTab() {
  const [status, setStatus] = useState("pending");
  const { data, error, mutate } = useApi<{ items: Defect[] }>(`/dpi/defects${status ? `?status=${status}` : ""}`);
  const [message, setMessage] = useState<string | null>(null);
  if (error) return <Loading error={error} />;
  return (
    <>
      <div className="dpi-row">
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Trạng thái RCA">
          <option value="pending">Chưa xác nhận</option>
          <option value="confirmed">Đã xác nhận</option>
          <option value="process">Lỗi quy trình</option>
          <option value="">Tất cả</option>
        </select>
        <span className="muted small">Issue gắn label prod-bug::P1/P2/P3. Chỉ lỗi đã xác nhận RCA mới trừ điểm B2.</span>
      </div>
      <Message text={message} onClose={() => setMessage(null)} />
      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <EmptyState title="Không có lỗi production nào ở trạng thái này." />
      ) : (
        data.items.map((d) => (
          <DefectRow
            key={d.id}
            d={d}
            onSaved={(m) => {
              setMessage(m);
              void mutate();
            }}
          />
        ))
      )}
    </>
  );
}

function PeerTab({ month }: { month: string }) {
  const { data, error, mutate } = useApi<{ people: { id: number; name: string | null; given: { score: number; comment: string | null } | null }[] }>(`/dpi/peer-feedback?month=${month}`);
  const [draft, setDraft] = useState<Record<number, { score: number; comment: string }>>({});
  const [message, setMessage] = useState<string | null>(null);
  if (error) return <Loading error={error} />;
  if (!data) return <Loading />;
  return (
    <Card title={`Phản hồi đồng nghiệp ${monthLabel(month)}`} collapsible={false} subtitle="Tuỳ chọn. Người được đánh giá không thấy tên bạn; lead thấy để tổng hợp vào D3.">
      <Message text={message} onClose={() => setMessage(null)} />
      {data.people.length === 0 ? (
        <EmptyState title="Bạn chưa ở team nào có người khác." />
      ) : (
        <table className="table compact">
          <tbody>
            {data.people.map((p) => {
              const d = draft[p.id] ?? { score: p.given?.score ?? 0, comment: p.given?.comment ?? "" };
              return (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>
                    <select value={d.score} onChange={(e) => setDraft({ ...draft, [p.id]: { ...d, score: Number(e.target.value) } })} aria-label={`Điểm cho ${p.name}`}>
                      <option value={0}>—</option>
                      {[1, 2, 3, 4, 5].map((n) => (
                        <option key={n} value={n}>
                          {n}/5
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={{ width: "50%" }}>
                    <input value={d.comment} onChange={(e) => setDraft({ ...draft, [p.id]: { ...d, comment: e.target.value } })} placeholder="Nhận xét (tuỳ chọn)" style={{ width: "100%" }} />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="sm"
                      disabled={!d.score}
                      onClick={async () => {
                        try {
                          await api.post("/dpi/peer-feedback", { to_user_id: p.id, month, score: d.score, comment: d.comment });
                          setMessage(`Đã lưu phản hồi cho ${p.name}.`);
                          await mutate();
                        } catch (e) {
                          setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
                        }
                      }}
                    >
                      {p.given ? "Cập nhật" : "Gửi"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

interface Quality {
  repos: number;
  findings_new: Record<string, number>;
  findings_old_open: Record<string, number>;
  coverage: { project: string | null; coverage: number; delta: number | null }[];
  hotspots: { project: string | null; path: string; changes: number; findings: number; score: number }[];
  debt: { project: string | null; module: string; critical: number; high: number; medium: number; low: number; weight: number }[];
}

const SEV = ["critical", "high", "medium", "low"] as const;

function ProjectFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data } = useApi<BusinessProject[]>("/business-projects");
  return (
    <label>
      Dự án{" "}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Mọi dự án trong phạm vi</option>
        {(data ?? []).map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function QualityTab({ month }: { month: string }) {
  const [bp, setBp] = useState("");
  const { data, error } = useApi<Quality>(`/dpi/quality?month=${month}${bp ? `&business_project_id=${bp}` : ""}`, { keepPreviousData: true });
  if (error) return <Loading error={error} />;
  if (!data) return <Loading rows={5} />;
  return (
    <>
      <div className="dpi-row">
        <ProjectFilter value={bp} onChange={setBp} />
        <span className="muted small">{data.repos} repo</span>
      </div>
      <div className="dpi-two">
        <Card title="Lỗi mới và lỗi cũ còn mở" collapsible={false} subtitle="Mới: finding của các review trong tháng. Cũ: finding trước tháng vẫn mở.">
          <table className="table compact">
            <thead>
              <tr>
                <th />
                {SEV.map((s) => (
                  <th key={s} className="num">
                    {s}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Mới trong tháng</td>
                {SEV.map((s) => (
                  <td key={s} className="num">
                    {data.findings_new[s] ?? 0}
                  </td>
                ))}
              </tr>
              <tr>
                <td>Cũ còn mở</td>
                {SEV.map((s) => (
                  <td key={s} className="num">
                    {data.findings_old_open[s] ?? 0}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
          <h4>Coverage trên MR của tháng</h4>
          {data.coverage.length === 0 ? (
            <p className="muted small">Chưa có pipeline báo coverage.</p>
          ) : (
            <table className="table compact">
              <tbody>
                {data.coverage.map((c) => (
                  <tr key={c.project}>
                    <td>{c.project}</td>
                    <td className="num">{c.coverage}%</td>
                    <td className={`num small ${c.delta !== null && c.delta < 0 ? "tone-bad" : "tone-good"}`}>{c.delta === null ? "" : `${c.delta > 0 ? "+" : ""}${c.delta}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Hotspot" collapsible={false} subtitle="File sửa nhiều × nhiều finding trong 90 ngày: nơi đáng refactor / viết test trước">
          {data.hotspots.length === 0 ? (
            <EmptyState title="Không có hotspot." />
          ) : (
            <table className="table compact">
              <thead>
                <tr>
                  <th>File</th>
                  <th className="num">Lần sửa</th>
                  <th className="num">Finding</th>
                </tr>
              </thead>
              <tbody>
                {data.hotspots.map((h) => (
                  <tr key={`${h.project}-${h.path}`}>
                    <td>
                      <code className="small">{h.path}</code>
                      <div className="muted small">{h.project}</div>
                    </td>
                    <td className="num">{h.changes}</td>
                    <td className="num">{h.findings}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
      <Card title="Nợ kỹ thuật theo module" collapsible={false} subtitle="Finding còn mở, trọng số critical 8 · high 4 · medium 2 · low 1">
        {data.debt.length === 0 ? (
          <EmptyState title="Không còn finding mở." tone="good" />
        ) : (
          <div className="table-wrap">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Module</th>
                  {SEV.map((s) => (
                    <th key={s} className="num">
                      {s}
                    </th>
                  ))}
                  <th className="num">Trọng số</th>
                </tr>
              </thead>
              <tbody>
                {data.debt.map((d) => (
                  <tr key={`${d.project}-${d.module}`}>
                    <td>
                      <code className="small">{d.module}</code>
                      <div className="muted small">{d.project}</div>
                    </td>
                    {SEV.map((s) => (
                      <td key={s} className="num">
                        {d[s] || ""}
                      </td>
                    ))}
                    <td className="num">{d.weight}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

interface Delivery {
  wip_mrs: number;
  open_issues: number;
  overdue_issues: number;
  aging: { bucket: string; issues: number }[];
  blockers: { reason: string; issues: number }[];
  repos: { project: string | null; wip_mrs: number; open_issues: number; overdue: number; blocked: number }[];
}

function DeliveryTab() {
  const [bp, setBp] = useState("");
  const { data, error } = useApi<Delivery>(`/dpi/delivery${bp ? `?business_project_id=${bp}` : ""}`, { keepPreviousData: true });
  if (error) return <Loading error={error} />;
  if (!data) return <Loading rows={4} />;
  return (
    <>
      <div className="dpi-row">
        <ProjectFilter value={bp} onChange={setBp} />
        <span className="muted small">
          Hiện tại. DORA và các chặng của luồng (coding / pickup / review / deploy): xem <Link href="/insights">Luồng làm việc</Link>.
        </span>
      </div>
      <StatStrip label="Delivery hiện tại">
        <StripStat label="MR đang mở (WIP)" value={data.wip_mrs} />
        <StripStat label="Issue đang mở" value={data.open_issues} />
        <StripStat label="Issue quá hạn" value={data.overdue_issues} tone={data.overdue_issues ? "warn" : undefined} />
      </StatStrip>
      <div className="dpi-two">
        <Card title="Tuổi backlog" collapsible={false} subtitle="Issue đang mở theo thời gian kể từ khi tạo">
          <table className="table compact">
            <tbody>
              {data.aging.map((a) => (
                <tr key={a.bucket}>
                  <td>{a.bucket}</td>
                  <td className="num">{a.issues}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4>Blocker theo nguyên nhân</h4>
          {data.blockers.length === 0 ? (
            <p className="muted small">Không có issue gắn blocked. Dùng label blocked::lý-do (ví dụ blocked::chờ-QA) để thấy nguyên nhân.</p>
          ) : (
            <ul className="small">
              {data.blockers.map((b) => (
                <li key={b.reason}>
                  {b.reason}: {b.issues}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Theo repo" collapsible={false}>
          <table className="table compact">
            <thead>
              <tr>
                <th>Repo</th>
                <th className="num">WIP</th>
                <th className="num">Issue mở</th>
                <th className="num">Quá hạn</th>
                <th className="num">Blocked</th>
              </tr>
            </thead>
            <tbody>
              {data.repos.map((r) => (
                <tr key={r.project}>
                  <td>{r.project}</td>
                  <td className="num">{r.wip_mrs}</td>
                  <td className="num">{r.open_issues}</td>
                  <td className="num">{r.overdue || ""}</td>
                  <td className="num">{r.blocked || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}

interface SettingsView {
  shadow: boolean;
  rubric: { version: string; metrics: { key: string; group: string; max: number; source: string; label: string }[] };
  versions: { version: string; active: boolean }[];
  profiles: { key: string; label: string; weights: Record<string, number>; a1_target: number }[];
  user_profiles?: { user: { id: number; name: string | null }; profile: string; effective_from: string }[];
  can_admin: boolean;
}

type RubricMetric = SettingsView["rubric"]["metrics"][number];

/** A new rubric version from the current one: labels and points editable (keys, groups and
 * who scores stay: the formulas are tied to them). */
function RubricEditor({ current, onSaved }: { current: SettingsView["rubric"]; onSaved: (version: string) => void }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<RubricMetric[]>(current.metrics);
  const [version, setVersion] = useState(() => {
    const m = /^(.*?)(\d+)$/.exec(current.version);
    return m ? `${m[1]}${Number(m[2]) + 1}` : `${current.version}-2`;
  });
  const [error, setError] = useState<string | null>(null);
  const auto = rows.filter((r) => r.source === "auto").reduce((n, r) => n + Number(r.max || 0), 0);
  const manager = rows.filter((r) => r.source === "manager").reduce((n, r) => n + Number(r.max || 0), 0);
  if (!open)
    return (
      <button type="button" className="sm" onClick={() => setOpen(true)}>
        Sửa rubric…
      </button>
    );
  const save = async () => {
    try {
      await api.post("/dpi/settings/rubric", { version, metrics: rows.map((r) => ({ ...r, max: Number(r.max) })) });
      setOpen(false);
      onSaved(version);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <div className="dpi-rubric-editor">
      <label>
        Phiên bản mới <input value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Tên phiên bản rubric" />
      </label>
      <table className="table compact">
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.key}>
              <td>{r.key}</td>
              <td>
                <input value={r.label} aria-label={`Tên ${r.key}`} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
              </td>
              <td>
                <input
                  type="number"
                  min={0.5}
                  step={0.5}
                  value={r.max}
                  aria-label={`Điểm tối đa ${r.key}`}
                  onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, max: Number(e.target.value) } : x)))}
                  style={{ width: 70 }}
                />
              </td>
              <td className="muted small">{r.source === "manager" ? "lead chấm" : "tự động"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small">
        Tự động {auto} · quản lý {manager} · tổng {auto + manager}
        {auto + manager !== 100 && <span className="tone-warn"> (nên là 100)</span>}
      </p>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="dpi-row">
        <button type="button" className="primary" onClick={() => void save()}>
          Tạo phiên bản
        </button>
        <button type="button" className="ghost" onClick={() => setOpen(false)}>
          Huỷ
        </button>
      </div>
    </div>
  );
}

function SettingsTab() {
  const { data, error, mutate } = useApi<SettingsView>("/dpi/settings");
  const people = useApi<Person[]>("/people");
  const [message, setMessage] = useState<string | null>(null);
  const [edit, setEdit] = useState<Record<string, { weights: Record<string, string>; target: string }>>({});
  const [level, setLevel] = useState({ user: "", profile: "middle", from: `${new Date().toISOString().slice(0, 7)}-01` });
  if (error) return <Loading error={error} />;
  if (!data) return <Loading />;
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      setMessage(ok);
      await mutate();
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  };
  return (
    <>
      <Message text={message} onClose={() => setMessage(null)} />
      <Card title="Chạy thử (shadow)" collapsible={false} subtitle="Khi bật, mọi scorecard ghi “Chạy thử — không dùng cho lương thưởng”. Tắt là quyết định của quản lý.">
        <p>
          Đang <strong>{data.shadow ? "bật" : "tắt"}</strong>.{" "}
          {data.can_admin && (
            <button type="button" onClick={() => run(() => api.put("/dpi/settings/shadow", { shadow: !data.shadow }), data.shadow ? "Đã tắt chạy thử." : "Đã bật chạy thử.")}>
              {data.shadow ? "Tắt chạy thử" : "Bật lại chạy thử"}
            </button>
          )}
        </p>
      </Card>
      <Card
        title={`Rubric ${data.rubric.version}`}
        collapsible={false}
        subtitle="70 điểm tự động + 30 điểm quản lý. Sửa tạo một phiên bản mới, áp dụng cho các kỳ mở sau đó; bản cũ giữ cho snapshot."
        actions={data.can_admin && <RubricEditor current={data.rubric} onSaved={(v) => run(async () => v, `Đã tạo rubric ${v}.`)} />}
      >
        <div className="dpi-rubric">
          {GROUPS.map((g) => (
            <div key={g}>
              <strong>
                {g} · {GROUP_LABEL[g]}
              </strong>
              <ul className="small">
                {data.rubric.metrics
                  .filter((m) => m.group === g)
                  .map((m) => (
                    <li key={m.key}>
                      {m.key} {m.label} — {m.max} điểm{m.source === "manager" ? " (lead chấm)" : ""}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Profile theo level" collapsible={false} subtitle="Trọng số 5 nhóm (tổng 100) và target A1 (điểm complexity / tháng).">
        <table className="table compact">
          <thead>
            <tr>
              <th>Profile</th>
              {GROUPS.map((g) => (
                <th key={g} className="num">
                  {g}
                </th>
              ))}
              <th className="num">Target A1</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.profiles.map((p) => {
              const e = edit[p.key];
              return (
                <tr key={p.key}>
                  <td>{p.label}</td>
                  {GROUPS.map((g) => (
                    <td key={g} className="num">
                      {e ? (
                        <input type="number" value={e.weights[g]} onChange={(ev) => setEdit({ ...edit, [p.key]: { ...e, weights: { ...e.weights, [g]: ev.target.value } } })} style={{ width: 60 }} aria-label={`${p.label} ${g}`} />
                      ) : (
                        p.weights[g]
                      )}
                    </td>
                  ))}
                  <td className="num">
                    {e ? <input type="number" value={e.target} onChange={(ev) => setEdit({ ...edit, [p.key]: { ...e, target: ev.target.value } })} style={{ width: 70 }} aria-label={`${p.label} target`} /> : p.a1_target}
                  </td>
                  <td>
                    {data.can_admin &&
                      (e ? (
                        <button
                          type="button"
                          className="sm"
                          onClick={() =>
                            run(
                              () =>
                                api.put(`/dpi/settings/profiles/${p.key}`, {
                                  label: p.label,
                                  weights: Object.fromEntries(GROUPS.map((g) => [g, Number(e.weights[g]) || 0])),
                                  a1_target: Number(e.target) || p.a1_target,
                                }),
                              `Đã lưu profile ${p.label}.`,
                            ).then(() => setEdit(({ [p.key]: _drop, ...rest }) => rest))
                          }
                        >
                          Lưu
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => setEdit({ ...edit, [p.key]: { weights: Object.fromEntries(GROUPS.map((g) => [g, String(p.weights[g] ?? 0)])), target: String(p.a1_target) } })}
                        >
                          Sửa
                        </button>
                      ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
      {data.can_admin && (
        <Card title="Level của từng người" collapsible={false} subtitle="Có hiệu lực từ đầu tháng chọn; giữ lịch sử. Người chưa gán dùng Middle.">
          <div className="dpi-row">
            <select value={level.user} onChange={(e) => setLevel({ ...level, user: e.target.value })} aria-label="Người">
              <option value="">Chọn người…</option>
              {(people.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.full_name ?? p.primary_email}
                </option>
              ))}
            </select>
            <select value={level.profile} onChange={(e) => setLevel({ ...level, profile: e.target.value })} aria-label="Profile">
              {data.profiles.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
            <input type="date" value={level.from} onChange={(e) => setLevel({ ...level, from: e.target.value })} aria-label="Hiệu lực từ" />
            <button
              type="button"
              disabled={!level.user}
              onClick={() => run(() => api.put("/dpi/user-profiles", { user_id: Number(level.user), profile: level.profile, effective_from: level.from }), "Đã gán level.")}
            >
              Gán
            </button>
          </div>
          <table className="table compact">
            <tbody>
              {(data.user_profiles ?? []).map((u, i) => (
                <tr key={i}>
                  <td>{u.user.name}</td>
                  <td>{u.profile}</td>
                  <td className="muted small">từ {date(u.effective_from)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}

interface Readiness {
  days: number;
  acceptance: { through_mr_pct: number | null; repos_with_pipeline_pct: number; issues_with_complexity_pct: number | null; targets: Record<string, number> };
  repos: {
    project: { id: number; path: string };
    business_project: string | null;
    criteria_assigned: boolean;
    commits: number;
    through_mr_pct: number | null;
    merged_mrs: number;
    checklist_pct: number | null;
    pipelines: number;
    pipeline_pass_pct: number | null;
    coverage_pct: number | null;
    avg_coverage: number | null;
    issues_closed: number;
    complexity_pct: number | null;
  }[];
}

function Mark({ value, target }: { value: number | null; target: number }) {
  if (value === null) return <span className="muted">—</span>;
  return <span className={value >= target ? "tone-good" : "tone-warn"}>{value}%</span>;
}

function ReadinessTab({ me }: { me: Me }) {
  const { data, error } = useApi<Readiness>("/dpi/readiness?days=30");
  const [checks, setChecks] = useState<Record<number, { default_protected?: boolean; missing_labels?: string[]; error?: string; created?: string[]; note?: string }>>({});
  const [confirm, setConfirm] = useState<{ id: number; path: string; action: "protect" | "mr-template" } | null>(null);
  if (error) return <Loading error={error} />;
  if (!data) return <Loading />;
  const a = data.acceptance;
  const check = async (id: number) => setChecks({ ...checks, [id]: await api.get(`/dpi/readiness/${id}/gitlab`) });
  const labels = async (id: number) => setChecks({ ...checks, [id]: { ...checks[id], ...(await api.post<{ created: string[]; error?: string }>(`/dpi/readiness/${id}/labels`)) } });
  const act = async () => {
    if (!confirm) return;
    const result = await api.post<{ protected?: string; created?: string; exists?: boolean; error?: string }>(`/dpi/readiness/${confirm.id}/${confirm.action}`);
    setChecks({
      ...checks,
      [confirm.id]: {
        ...checks[confirm.id],
        ...(result.error ? { error: result.error } : {}),
        ...(result.protected ? { default_protected: true } : {}),
        note: result.created ? "Đã thêm mẫu MR" : result.exists ? "Repo đã có mẫu MR" : result.protected ? `Đã bảo vệ ${result.protected}` : undefined,
      },
    });
    setConfirm(null);
  };
  return (
    <>
      <StatStrip label="Nghiệm thu P0 (30 ngày)">
        <StripStat label="Thay đổi đi qua MR" value={a.through_mr_pct} unit="%" sub={`mục tiêu ≥ ${a.targets.through_mr_pct}%`} tone={(a.through_mr_pct ?? 0) >= 80 ? "good" : "warn"} />
        <StripStat label="Repo có pipeline + coverage" value={a.repos_with_pipeline_pct} unit="%" sub={`mục tiêu ≥ ${a.targets.repos_with_pipeline_pct}%`} tone={a.repos_with_pipeline_pct >= 90 ? "good" : "warn"} />
        <StripStat label="Issue có complexity" value={a.issues_with_complexity_pct} unit="%" sub={`mục tiêu ≥ ${a.targets.issues_with_complexity_pct}%`} tone={(a.issues_with_complexity_pct ?? 0) >= 70 ? "good" : "warn"} />
      </StatStrip>
      {confirm && (
        <Alert tone="warning">
          {confirm.action === "protect" ? (
            <>
              Bảo vệ nhánh chính của <strong>{confirm.path}</strong> trên GitLab: <strong>không ai push thẳng</strong> được nữa, chỉ merge qua MR (Maintainer).
              Báo đội trước khi bật.
            </>
          ) : (
            <>
              Commit file <code>.gitlab/merge_request_templates/Default.md</code> (Closes # + checklist) lên nhánh chính của <strong>{confirm.path}</strong>.
            </>
          )}{" "}
          <button type="button" className="primary sm" onClick={() => void act()}>
            Xác nhận
          </button>{" "}
          <button type="button" className="ghost sm" onClick={() => setConfirm(null)}>
            Huỷ
          </button>
        </Alert>
      )}
      <Card title="Từng repo" collapsible={false} subtitle="Bấm Kiểm tra để hỏi GitLab: nhánh chính có bảo vệ chưa, đủ label chưa. Coverage trong CI: xem Hướng dẫn › DPI.">
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Repo</th>
                <th>Bộ tiêu chí</th>
                <th className="num">Qua MR</th>
                <th className="num">MR có checklist</th>
                <th className="num">Pipeline</th>
                <th className="num">Có coverage</th>
                <th className="num">Issue có complexity</th>
                <th>GitLab</th>
              </tr>
            </thead>
            <tbody>
              {data.repos.map((r) => {
                const c = checks[r.project.id];
                return (
                  <tr key={r.project.id}>
                    <td>
                      {r.project.path}
                      <div className="muted small">{r.business_project ?? "chưa thuộc dự án"}</div>
                    </td>
                    <td>{r.criteria_assigned ? <Pill tone="good">đã gán</Pill> : <Pill tone="warn">chưa gán</Pill>}</td>
                    <td className="num">
                      <Mark value={r.through_mr_pct} target={80} />
                      <div className="muted small">{r.commits} commit</div>
                    </td>
                    <td className="num">
                      <Mark value={r.checklist_pct} target={80} />
                    </td>
                    <td className="num">
                      {r.pipelines}
                      <div className="muted small">{r.pipeline_pass_pct ?? "—"}% xanh</div>
                    </td>
                    <td className="num">
                      <Mark value={r.coverage_pct} target={90} />
                      {r.avg_coverage !== null && <div className="muted small">TB {r.avg_coverage}%</div>}
                    </td>
                    <td className="num">
                      <Mark value={r.complexity_pct} target={70} />
                      <div className="muted small">{r.issues_closed} issue</div>
                    </td>
                    <td className="small">
                      {c?.error ? (
                        <span className="tone-bad">{c.error}</span>
                      ) : c ? (
                        <>
                          {c.default_protected === undefined ? null : c.default_protected ? <Pill tone="good">nhánh chính có bảo vệ</Pill> : <Pill tone="bad">nhánh chính chưa bảo vệ</Pill>}{" "}
                          {c.created ? `Đã tạo ${c.created.length} label` : c.missing_labels?.length ? `Thiếu ${c.missing_labels.length} label` : "Đủ label"}
                          {c.missing_labels && c.missing_labels.length > 0 && can(me, "admin.projects") && (
                            <button type="button" className="link sm" onClick={() => void labels(r.project.id)}>
                              Tạo label chuẩn
                            </button>
                          )}
                          {c.note && <div className="muted">{c.note}</div>}
                          {can(me, "admin.projects") && (
                            <div className="dpi-row">
                              {c.default_protected === false && (
                                <button type="button" className="sm" onClick={() => setConfirm({ id: r.project.id, path: r.project.path, action: "protect" })}>
                                  Bảo vệ nhánh chính
                                </button>
                              )}
                              <button type="button" className="ghost sm" onClick={() => setConfirm({ id: r.project.id, path: r.project.path, action: "mr-template" })}>
                                Thêm mẫu MR
                              </button>
                            </div>
                          )}
                        </>
                      ) : (
                        <button type="button" className="ghost sm" onClick={() => void check(r.project.id)}>
                          Kiểm tra
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

export default function DpiPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const [month, setMonth] = useMonth();
  const [tab, setTab] = useState<Tab>("overview");
  if (!me || !month) return <Loading rows={4} />;
  const tabs: SubTab<Tab>[] = [
    ...(can(me, "dpi.view") || can(me, "dpi.final_view") ? [{ key: "overview", label: "Tổng quan" } as SubTab<Tab>] : []),
    ...(can(me, "dpi.distribution") ? [{ key: "distribution", label: "Phân phối" } as SubTab<Tab>] : []),
    ...(can(me, "dpi.calibrate") ? [{ key: "calibration", label: "Hiệu chỉnh" } as SubTab<Tab>] : []),
    ...(can(me, "dpi.distribution") || can(me, "dpi.final_view") || ["team", "project", "all"].includes(me.permissions?.["dpi.view"] ?? "") || me.is_admin
      ? [{ key: "quality", label: "Chất lượng & nợ" } as SubTab<Tab>, { key: "delivery", label: "Delivery" } as SubTab<Tab>]
      : []),
    ...(can(me, "dpi.defects") ? [{ key: "defects", label: "Lỗi production" } as SubTab<Tab>] : []),
    { key: "peer", label: "Phản hồi đồng nghiệp" },
    ...(can(me, "dpi.admin") || can(me, "dpi.view") ? [{ key: "settings", label: "Cấu hình" } as SubTab<Tab>] : []),
    ...(can(me, "dpi.admin") || can(me, "admin.projects") ? [{ key: "readiness", label: "Sẵn sàng dữ liệu" } as SubTab<Tab>] : []),
  ];
  const current = tabs.some((t) => t.key === tab) ? tab : tabs[0].key;
  return (
    <div className="dpi-page stack">
      <PageHeader
        eyebrow="DPI"
        title="Đánh giá hiệu suất hàng tháng"
        subtitle="70 điểm chỉ số kỹ thuật tự động + 30 điểm đánh giá của lead, theo level. Không có bảng xếp hạng toàn công ty."
        foldable={false}
        actions={<MonthPicker value={month} onChange={setMonth} />}
      />
      <SubTabs id="dpi" label="DPI" tabs={tabs} value={current} onChange={setTab} variant="bar" />
      <SubPanel id="dpi" tab={current}>
        <div className="stack">
        {current === "overview" && <OverviewTab month={month} me={me} />}
        {current === "distribution" && <DistributionTab month={month} />}
        {current === "calibration" && <CalibrationTab month={month} />}
        {current === "quality" && <QualityTab month={month} />}
        {current === "delivery" && <DeliveryTab />}
        {current === "defects" && <DefectsTab />}
        {current === "peer" && <PeerTab key={month} month={month} />}
        {current === "settings" && <SettingsTab />}
        {current === "readiness" && <ReadinessTab me={me} />}
        </div>
      </SubPanel>
    </div>
  );
}

