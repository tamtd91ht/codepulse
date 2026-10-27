"use client";

import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AttentionBadge, RangePicker, Score, SeverityRow, Stat, VerdictMix, rangeLabel, useRange } from "@/components/Evaluation";
import { Alert, Card, ChartTip, CountLink, EmptyState, Loading } from "@/components/ui";
import { boardLink, qs, useApi } from "@/lib/api";
import { CATEGORY_LABEL, num, relative, weekLabel } from "@/lib/format";
import type { MemberEvaluationDetail, Person } from "@/lib/types";

/**
 * "Đánh giá trong kỳ" on a person's page: how many reviews, the scores, the verdicts, which
 * repos, which kinds of findings, over a range the reader picks (the Thành viên page uses the
 * same numbers for everyone at once).
 */
export function MemberEvaluation({ person }: { person: Person }) {
  const [range, setRange] = useRange("30");
  const path = range && range.from && range.to ? `/members/${person.id}/evaluation${qs({ from: range.from, to: range.to })}` : null;
  const { data, error, isLoading } = useApi<MemberEvaluationDetail>(path, { keepPreviousData: true });
  const s = data?.summary;
  const name = person.full_name ?? person.primary_email ?? "";
  const period = data ? { range: data.range, label: rangeLabel(data.from, data.to), counted: false as const } : undefined;
  const link = (severity: "critical" | "high", n: number) => (n && period ? boardLink(severity, { user: { id: person.id, name }, period }) : null);

  return (
    <Card
      id="member-evaluation"
      title="Đánh giá trong kỳ"
      subtitle="Review hoàn tất, điểm theo LOC, kết luận và loại lỗi; theo repo và theo tuần."
      actions={<RangePicker value={range} onChange={setRange} />}
    >
      {!data && <Loading error={error} rows={3} />}
      {data && error && (
        <Alert tone="error">
          Không tải được khoảng mới: {error.message}. Đang hiện {rangeLabel(data.from, data.to)}.
        </Alert>
      )}
      {data && s && (
        <div className={isLoading ? "eval-dim" : undefined}>
          <div className="stats-strip">
            <Stat label="Review">
              {s.reviews}
              {s.reviews > 0 && (
                <span className="muted small">
                  {" "}
                  · {s.mrs} MR · {s.commits} commit
                </span>
              )}
            </Stat>
            <Stat label="Điểm TB (theo LOC)" hint="Review lớn nặng hơn review nhỏ">
              <Score value={s.avg_score} strong />
              {s.min_score !== null && s.reviews > 1 && <span className="muted small"> · thấp nhất {num(s.min_score, 0)}</span>}
            </Stat>
            <Stat label="Tỉ lệ approve">{s.pass_rate === null ? "—" : `${num(s.pass_rate, 0)}%`}</Stat>
            <Stat label="Critical / High">
              <span className={s.critical ? "bad" : undefined}>
                <CountLink n={s.critical} href={link("critical", s.critical)} />
              </span>
              {" / "}
              <span className={s.high ? "warn" : undefined}>
                <CountLink n={s.high} href={link("high", s.high)} />
              </span>
            </Stat>
            <Stat label="Lỗi / 100 LOC">{s.findings_per_100_loc === null ? "—" : num(s.findings_per_100_loc, 2)}</Stat>
            <Stat label="Repo">{s.repos}</Stat>
            <Stat label="Review gần nhất">{s.last_review_at ? relative(s.last_review_at) : "—"}</Stat>
          </div>
          {s.needs_attention && (
            <p className="small" style={{ margin: "0 0 12px" }}>
              <AttentionBadge b={s} /> <span className="muted">Có critical hoặc điểm TB dưới 65 trong kỳ: nên xem cùng nhau các finding bên dưới.</span>
            </p>
          )}

          {s.reviews === 0 ? (
            <EmptyState title="Không có review hoàn tất trong khoảng này">Đổi khoảng thời gian, hoặc yêu cầu review ở trang Yêu cầu review.</EmptyState>
          ) : (
            <>
              <div className="grid two" style={{ marginTop: 4 }}>
                <div>
                  <h3 className="muted">Kết luận review</h3>
                  <VerdictMix verdicts={s.verdicts} />
                  <dl className="eval-facts" style={{ marginTop: 12 }}>
                    <div>
                      <dt>Finding tính điểm</dt>
                      <dd>
                        <SeverityRow b={s} />
                      </dd>
                    </div>
                    <div>
                      <dt>Khiếu nại được chấp nhận</dt>
                      <dd>{s.disputed}</dd>
                    </div>
                    <div>
                      <dt>Bị kiểm chứng bác bỏ</dt>
                      <dd>{s.rejected}</dd>
                    </div>
                  </dl>
                </div>
                <div>
                  <h3 className="muted">Loại lỗi</h3>
                  {data.categories.length ? (
                    <ol className="rank-list">
                      {data.categories.map((c) => (
                        <li key={c.category}>
                          <span className="name">{CATEGORY_LABEL[c.category] ?? c.category}</span>
                          <span className="count">{c.count}</span>
                          <span className="bar">
                            <span style={{ width: `${(100 * c.count) / data.categories[0].count}%` }} />
                          </span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="muted small">Không có finding nào tính điểm.</p>
                  )}
                </div>
              </div>

              <h3 className="muted">Theo tuần</h3>
              <div className="chart-box" style={{ height: 200 }}>
                <ResponsiveContainer>
                  <ComposedChart
                    data={data.weeks.map((w, i) => ({ week: weekLabel(w), score: data.trend[i], reviews: data.trend_reviews[i] }))}
                    margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
                  >
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                    <YAxis yAxisId="score" domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                    <YAxis yAxisId="reviews" orientation="right" allowDecimals={false} hide />
                    <Tooltip content={<ChartTip digits={1} />} cursor={{ fill: "var(--surface-2)" }} />
                    <Bar yAxisId="reviews" dataKey="reviews" name="Review" fill="var(--primary-soft)" maxBarSize={28} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                    <Line yAxisId="score" dataKey="score" name="Điểm TB" stroke="var(--chart-1)" strokeWidth={2.5} dot={{ r: 3 }} connectNulls isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              <h3 className="muted">Theo repo</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Repo</th>
                      <th className="num">Review</th>
                      <th className="num">Điểm TB</th>
                      <th>Kết luận</th>
                      <th>Finding</th>
                      <th className="num">Lỗi / 100 LOC</th>
                      <th>Gần nhất</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_repo.map((r) => (
                      <tr key={r.project_id ?? "none"} className={r.needs_attention ? "attention" : undefined}>
                        <td>
                          <div className="cell-main">
                            {r.path ? <code>{r.path}</code> : <span className="muted">Repo đã gỡ</span>}
                            <AttentionBadge b={r} />
                          </div>
                        </td>
                        <td className="num">
                          {r.reviews}
                          <div className="sub muted small nowrap">
                            {r.mrs} MR · {r.commits} commit
                          </div>
                        </td>
                        <td className="num">
                          <Score value={r.avg_score} strong />
                        </td>
                        <td>
                          <VerdictMix verdicts={r.verdicts} compact />
                        </td>
                        <td>
                          <SeverityRow b={r} />
                        </td>
                        <td className="num">{r.findings_per_100_loc === null ? "—" : num(r.findings_per_100_loc, 2)}</td>
                        <td className="muted nowrap">{r.last_review_at ? relative(r.last_review_at) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
