"use client";

import Link from "next/link";
import { use, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { IconButton } from "@/components/icons";
import { useRange } from "@/components/Evaluation";
import { MemberEvaluation } from "@/components/MemberEvaluation";
import { ReviewResults } from "@/components/ReviewResults";
import { Alert, Avatar, Card, ChartTip, EmptyState, Kpi, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { ApiError, api, qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, ratio, weekLabel } from "@/lib/format";
import type {
  BoardFinding,
  CategoryWeek,
  Me,
  MetricsSeries,
  Page,
  Person,
  Repo,
  TelegramAccount,
  TelegramLinkCode,
} from "@/lib/types";

function Heatmap({ cells }: { cells: CategoryWeek[] }) {
  const weeks = [...new Set(cells.map((c) => c.week))].sort();
  const categories = [...new Set(cells.map((c) => c.category))].sort();
  if (!weeks.length)
    return (
      <EmptyState icon="checkCircle" tone="good" title="Chưa có finding nào trong 12 tuần">
        Loại lỗi theo tuần xuất hiện ở đây khi có review.
      </EmptyState>
    );
  const max = Math.max(...cells.map((c) => c.count));
  const count = (w: string, c: string) => cells.find((x) => x.week === w && x.category === c)?.count ?? 0;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Loại lỗi theo tuần (cuộn ngang)">
      <div
        className="heatmap"
        role="table"
        aria-label="Số finding theo loại lỗi và tuần"
        style={{ gridTemplateColumns: `120px repeat(${weeks.length}, minmax(34px, 1fr))` }}
      >
        <div role="row" style={{ display: "contents" }}>
          <span role="columnheader" aria-label="Loại lỗi" />
          {weeks.map((w) => (
            <span key={w} className="col-label" role="columnheader">
              {weekLabel(w)}
            </span>
          ))}
        </div>
        {categories.map((c) => (
          <div key={c} role="row" style={{ display: "contents" }}>
            <span className="row-label" role="rowheader">
              {CATEGORY_LABEL[c] ?? c}
            </span>
            {weeks.map((w) => {
              const n = count(w, c);
              // A narrow tint range keeps the default text colour readable in both themes.
              const alpha = n ? 0.15 + (0.35 * n) / max : 0;
              return (
                <span
                  key={w}
                  className="cell"
                  role="cell"
                  aria-label={`${n} finding`}
                  title={`${CATEGORY_LABEL[c] ?? c}, tuần ${weekLabel(w)}: ${n} finding`}
                  style={n ? { background: `color-mix(in srgb, var(--primary) ${Math.round(alpha * 100)}%, var(--surface-2))` } : undefined}
                >
                  {n || ""}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Link this person's Telegram to CodePulse: a one-time code sent to the dự án's bot as /link CODE. */
function TelegramCard() {
  const [code, setCode] = useState<TelegramLinkCode | null>(null);
  // While a code is shown, poll so the list updates as soon as the person sends /link.
  const accounts = useApi<TelegramAccount[]>("/me/telegram", { refreshInterval: code ? 5000 : 0 });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [unlinking, setUnlinking] = useState<number | null>(null);
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null);

  async function copy(text: string) {
    try {
      if (!navigator.clipboard) throw new Error("no clipboard");
      await navigator.clipboard.writeText(text);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    window.setTimeout(() => setCopied(null), 2500);
  }

  async function issue() {
    setBusy(true);
    setError(null);
    try {
      setCode(await api.post<TelegramLinkCode>("/me/telegram/link-code"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Telegram" subtitle="Liên kết để gửi lệnh review cho bot của dự án (ví dụ /review mr 123, /review today).">
      {error && <Alert tone="error">Lỗi: {error}</Alert>}
      {accounts.error && <Alert tone="error">Không tải được liên kết: {accounts.error.message}</Alert>}
      {!accounts.data && !accounts.error && <Loading rows={1} />}
      {accounts.data && accounts.data.length > 0 && (
        <ul className="finding-list" style={{ marginBottom: 12 }}>
          {accounts.data.map((a) => (
            <li key={a.id}>
              <Pill tone="good" icon="check">
                Đã liên kết
              </Pill>
              <div className="body">
                {a.display_name ?? `Telegram ${a.external_key}`}
                <div className="meta">id {a.external_key}</div>
              </div>
              <IconButton
                icon="close"
                label="Gỡ liên kết"
                disabled={unlinking === a.id}
                onClick={async () => {
                  setUnlinking(a.id);
                  try {
                    await api.delete(`/me/telegram/${a.id}`);
                  } catch (e) {
                    // Already gone (double click, or unlinked from Telegram) is not an error here.
                    if (!(e instanceof ApiError && e.status === 404)) setError(e instanceof ApiError ? e.message : String(e));
                  } finally {
                    setUnlinking(null);
                    await accounts.mutate();
                  }
                }}
              />
            </li>
          ))}
        </ul>
      )}
      {code ? (
        <div className="link-code">
          <div className="code">{code.code}</div>
          <div className="small muted">
            Gửi cho bot: <code>/link {code.code}</code> · mã dùng một lần, hết hạn sau {Math.round(code.expires_in_s / 60)} phút.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="sm" onClick={() => void copy(`/link ${code.code}`)}>
              Chép lệnh
            </button>
            <button
              className="sm ghost"
              onClick={() => {
                setCode(null);
                void accounts.mutate();
              }}
            >
              Xong
            </button>
            <span className="small muted" aria-live="polite">
              {copied === "ok" ? "Đã chép." : copied === "fail" ? "Không chép được, hãy chọn mã và sao chép thủ công." : ""}
            </span>
          </div>
        </div>
      ) : (
        <button className="primary sm" onClick={issue} disabled={busy}>
          {busy ? "Đang tạo…" : accounts.data?.length ? "Liên kết thêm thiết bị" : "Tạo mã liên kết"}
        </button>
      )}
    </Card>
  );
}

export default function MemberDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: person, error } = useApi<Person>(`/people/${id}`);
  const { data: me } = useApi<Me>("/auth/me");
  const { data: series, error: seriesError } = useApi<MetricsSeries>(`/metrics/users/${id}${qs({ period: "week" })}`);
  const { data: heat, error: heatError } = useApi<CategoryWeek[]>(`/metrics/users/${id}/categories?weeks=12`);
  const { data: repos } = useApi<Repo[]>("/repos");
  // One period for the evaluation and the review results: changing either card moves both.
  const range = useRange("30");
  const { data: open, error: openError } = useApi<Page<BoardFinding>>(`/findings${qs({ user_id: id, limit: 50 })}`);

  if (!person) return <Loading error={error} rows={4} />;
  const latest = series?.series.at(-1) ?? null;
  const rank = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const findings = [...(open?.items ?? [])].sort((a, b) => rank[a.severity] - rank[b.severity]);
  const name = person.full_name ?? person.primary_email ?? `#${person.id}`;

  return (
    <>
      <PageHeader
        eyebrow="Member detail"
        title={name}
        subtitle={person.primary_email}
        avatar={<Avatar name={person.full_name} email={person.primary_email} size="lg" />}
      />

      {!series && <Loading error={seriesError} rows={3} />}
      {series && (
        <>
          <div className="grid kpis">
            <Kpi label="Quality Index (tuần gần nhất)" value={latest?.quality_index} change={series.delta.quality_index} />
            <Kpi label="Điểm TB" value={latest?.avg_score} change={series.delta.avg_score} />
            <Kpi
              label="Fix rate"
              unit="%"
              value={latest?.fix_rate === null || !latest ? null : Number(latest.fix_rate) * 100}
              digits={0}
              hint="Tỉ lệ lỗi critical/high đã sửa ở lần review sau"
            />
            <Kpi label="QI trung bình team" value={series.team_quality_index} />
          </div>
          <Card title="12 tuần gần nhất" subtitle="Điểm trung bình và Quality Index theo tuần">
            <div className="chart-box">
              <ResponsiveContainer>
                <LineChart
                  data={series.series.map((m) => ({
                    week: weekLabel(m.period_start),
                    score: m.avg_score === null ? null : Number(m.avg_score),
                    qi: m.quality_index === null ? null : Number(m.quality_index),
                  }))}
                  margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
                >
                  <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                  <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                  <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                  <Tooltip content={<ChartTip />} cursor={{ stroke: "var(--border-strong)" }} />
                  <Legend iconType="circle" iconSize={8} />
                  <Line dataKey="score" name="Điểm TB" stroke="var(--chart-1)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} connectNulls />
                  <Line
                    dataKey="qi"
                    name="Quality Index"
                    stroke="var(--chart-2)"
                    strokeWidth={2.5}
                    strokeDasharray="6 4"
                    dot={false}
                    activeDot={{ r: 4 }}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </>
      )}

      <MemberEvaluation person={person} range={range} />

      <Card title="Loại lỗi theo tuần" subtitle="Số finding của mỗi loại, 12 tuần gần nhất">
        {heat ? <Heatmap cells={heat} /> : <Loading error={heatError} rows={4} />}
      </Card>

      <ReviewResults person={person.id} repos={repos} range={range} />

      <Card title="Finding đang mở" subtitle="Critical và high xếp trước">
        {open ? (
          findings.length ? (
            <ul className="finding-list">
              {findings.map((f) => (
                <li key={f.id}>
                  <SeverityBadge severity={f.severity} />
                  <div className="body">
                    <Link href={`/reviews/${f.review_id}`}>{f.title}</Link>
                    <div className="meta">
                      <code>
                        {f.file_path}:{f.line_start}
                      </code>{" "}
                      · {f.age_days} ngày
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon="checkCircle" tone="good" title="Không có finding đang mở" />
          )
        ) : (
          <Loading error={openError} rows={4} />
        )}
      </Card>
      {me?.id === person.id && <TelegramCard />}
      <p className="muted small" style={{ marginTop: 12 }}>
        Fix rate {ratio(latest?.fix_rate ?? null)} = tỉ lệ lỗi critical/high đã sửa ở lần review sau.
      </p>
    </>
  );
}
