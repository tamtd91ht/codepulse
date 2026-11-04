"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CartesianGrid, ComposedChart, Bar, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { FormulaHelp } from "@/components/CriteriaScores";
import { Score, SeverityRow, VerdictMix, RangePicker, scoreTone, useUrlRange } from "@/components/Evaluation";
import { Icon } from "@/components/icons";
import { type TeamReport, TeamReportView } from "@/components/TeamReport";
import { Alert, Card, ChartTip, EmptyState, Loading, PageHeader, Pill, Segmented, StatStrip, StripStat } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { toNum } from "@/lib/criteria";
import { VERDICT_LABEL, dateTime, num, weekLabel } from "@/lib/format";
import type { CriteriaSummary, EvaluationBucket, Me, Person, Repo, Team, Verdict } from "@/lib/types";

/* ---- API shapes (GET /members/{id}/report, docs/specs/23) ---- */

type Bucket = EvaluationBucket & { members?: number };

interface ReviewRow {
  id: number;
  target_type: "mr" | "commit";
  repo: string | null;
  counted_at: string;
  score: number | null;
  verdict: string | null;
  loc: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  ref: string;
  title: string;
}

interface MemberReport {
  user: Person;
  from: string;
  to: string;
  filters: {
    business_project_id: number | null;
    business_project_name: string | null;
    project_id: number | null;
    project_path: string | null;
  };
  summary: Bucket;
  previous: { from: string; to: string; summary: Bucket };
  peers: Bucket & { members: number };
  assessment: { level: "good" | "fair" | "support" | "none"; label: string; reasons: string[]; thin: boolean };
  weeks: string[];
  trend: (number | null)[];
  trend_reviews: number[];
  peer_trend: (number | null)[];
  by_repo: (Bucket & { project_id: number | null; path: string | null })[];
  categories: { category: string; label: string; count: number; share: number; peer_share: number }[];
  lowest_reviews: ReviewRow[];
  severe_reviews: ReviewRow[];
  criteria_sets: { set_id: number; name: string; version: number | null; is_primary: boolean; reviews: number }[];
}

type Tab = "overview" | "criteria";
const LEVEL_TONE = { good: "good", fair: "warn", support: "bad", none: undefined } as const;

/* ---- URL state: ?user=&bp=&repo=&tab= (the range is useUrlRange's) ---- */

function readParam(name: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(name) ?? "";
}

function useUrlParam(name: string): [string, (v: string) => void] {
  const [value, setValue] = useState("");
  useEffect(() => setValue(readParam(name)), [name]);
  const update = useCallback(
    (next: string) => {
      setValue(next);
      const url = new URL(window.location.href);
      if (next) url.searchParams.set(name, next);
      else url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    },
    [name],
  );
  return [value, update];
}

const diff = (a: number | null | undefined, b: number | null | undefined) =>
  a === null || a === undefined || b === null || b === undefined ? null : Math.round((a - b) * 10) / 10;

/**
 * Báo cáo thành viên: for a lead or manager judging how good one person's code is. Filters on
 * top (person, period, dự án, repo) live in the URL so a report can be shared; the verdict with
 * its reasons comes first, the evidence (trend, levels, kinds of mistakes, repos, reviews to
 * open) after; the criteria tab compares each component with everyone in scope.
 */
export default function MemberReportPage() {
  const [range, setRange] = useUrlRange("30");
  const [user, setUser] = useUrlParam("user");
  const [bp, setBp] = useUrlParam("bp");
  const [repo, setRepo] = useUrlParam("repo");
  const [tabParam, setTabParam] = useUrlParam("tab");
  const tab: Tab = tabParam === "criteria" ? "criteria" : "overview";
  // ?mode=team: several people side by side (docs/specs/23 §8).
  const [modeParam, setModeParam] = useUrlParam("mode");
  const me = useApi<Me>("/auth/me");
  // Comparing people is the Thành viên page's job: only who sees that page gets the switch.
  const canCompare = !!me.data?.members_page;
  const team = modeParam === "team" && canCompare;
  const [teamId, setTeamId] = useUrlParam("team");
  const [picked, setPicked] = useUrlParam("people");
  const [setParam, setSetParam] = useUrlParam("set");
  const pickedIds = picked.split(",").filter(Boolean);
  const teams = useApi<Team[]>(team ? "/teams/visible" : null);

  const people = useApi<Person[]>("/people");
  const repos = useApi<Repo[]>("/repos");
  const projects = useMemo(() => {
    const seen = new Map<number, string>();
    for (const r of repos.data ?? []) if (r.business_project_id !== null) seen.set(r.business_project_id, r.business_project ?? `#${r.business_project_id}`);
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]));
  }, [repos.data]);
  const repoOptions = (repos.data ?? [])
    .filter((r) => !bp || String(r.business_project_id) === bp)
    .sort((a, b) => a.path_with_namespace.localeCompare(b.path_with_namespace));

  const filters = range ? { from: range.from, to: range.to, business_project_id: bp || null, project_id: repo || null } : null;
  const path = !team && user && filters ? `/members/${user}/report${qs(filters)}` : null;
  const report = useApi<MemberReport>(path, { keepPreviousData: true });
  const data = report.data && String(report.data.user.id) === user ? report.data : undefined;
  const name = data ? (data.user.full_name ?? data.user.primary_email ?? "") : "";
  const exportHref = path ? `/api/v1/members/${user}/report.xlsx${qs(filters ?? {})}` : undefined;

  const teamFilters = filters
    ? { ...filters, team_id: teamId || null, user_ids: picked || null, set_id: setParam || null }
    : null;
  const teamPath = team && teamFilters ? `/members/team-report${qs(teamFilters)}` : null;
  const teamReport = useApi<TeamReport>(teamPath, { keepPreviousData: true });
  const teamExport = teamPath ? `/api/v1/members/team-report.xlsx${qs(teamFilters ?? {})}` : undefined;
  const personHref = (id: number) =>
    `/members/report?user=${id}${range ? (range.preset === "custom" ? `&from=${range.from}&to=${range.to}` : `&range=${range.preset}`) : ""}${bp ? `&bp=${bp}` : ""}${repo ? `&repo=${repo}` : ""}`;
  const teamTitle =
    teamReport.data?.filters.team_name ?? (pickedIds.length ? `${pickedIds.length} người được chọn` : "mọi người trong phạm vi");

  const tabs: SubTab<Tab>[] = [
    { key: "overview", label: "Tổng quan", hint: "Đánh giá chung, xu hướng, mức lỗi, loại lỗi, repo, review cần xem" },
    {
      key: "criteria",
      label: "Theo tiêu chí",
      count: data?.criteria_sets.length || undefined,
      hint: "Điểm từng thành phần của bộ tiêu chí, so với mặt bằng",
    },
  ];

  return (
    <>
      <PageHeader
        eyebrow={<Link href="/members">Thành viên</Link>}
        title={team ? `So sánh nhóm: ${teamTitle}` : data ? `Báo cáo: ${name}` : "Báo cáo thành viên"}
        subtitle={
          team
            ? "Nhiều người cạnh nhau trong cùng khoảng thời gian: ai cần chú ý, ai hay mắc lỗi gì, ai yếu ở thành phần nào."
            : "Chất lượng code của một người trong khoảng thời gian, so với kỳ trước và với mặt bằng những người bạn xem được cùng bộ lọc."
        }
        foldable={false}
        actions={
          <span className="no-print report-actions">
            <button type="button" onClick={() => window.print()} disabled={team ? !teamReport.data : !data}>
              <Icon name="printer" size={14} /> In / PDF
            </button>
            {team && teamExport && teamReport.data ? (
              <a className="button primary" href={teamExport} download>
                <Icon name="download" size={14} /> Xuất Excel
              </a>
            ) : !team && exportHref && data ? (
              <a className="button primary" href={exportHref} download>
                <Icon name="download" size={14} /> Xuất Excel
              </a>
            ) : (
              <button type="button" className="primary" disabled>
                <Icon name="download" size={14} /> Xuất Excel
              </button>
            )}
          </span>
        }
      />

      {canCompare && (
      <div className="report-mode no-print">
        <Segmented<"one" | "team">
          label="Kiểu báo cáo"
          value={team ? "team" : "one"}
          onChange={(m) => setModeParam(m === "team" ? "team" : "")}
          options={[
            { value: "one", label: "Một người" },
            { value: "team", label: "So sánh nhóm" },
          ]}
        />
      </div>
      )}
      <section className="card report-filters no-print" aria-label="Bộ lọc báo cáo">
        {team ? (
          <>
            <label className="adm-field">
              <span className="small muted">Team</span>
              <select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Chọn team">
                <option value="">{teams.data ? "Mọi người trong phạm vi" : "Đang tải…"}</option>
                {(teams.data ?? []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <PeoplePicker people={people.data ?? []} value={pickedIds} onChange={(ids) => setPicked(ids.join(","))} />
          </>
        ) : (
          <label className="adm-field">
            <span className="small muted">Thành viên</span>
            <select value={user} onChange={(e) => setUser(e.target.value)} aria-label="Chọn thành viên">
              <option value="">{people.data ? "Chọn người…" : "Đang tải…"}</option>
              {(people.data ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.full_name ?? p.primary_email ?? `#${p.id}`}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="adm-field">
          <span className="small muted">Dự án</span>
          <select
            value={bp}
            onChange={(e) => {
              setBp(e.target.value);
              setRepo("");
            }}
            aria-label="Lọc theo dự án"
          >
            <option value="">Mọi dự án</option>
            {projects.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="adm-field">
          <span className="small muted">Repo</span>
          <select value={repo} onChange={(e) => setRepo(e.target.value)} aria-label="Lọc theo repo">
            <option value="">{bp ? "Mọi repo của dự án" : "Mọi repo"}</option>
            {repoOptions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.path_with_namespace}
              </option>
            ))}
          </select>
        </label>
        <div className="adm-field report-range">
          <span className="small muted">Thời gian</span>
          <RangePicker value={range} onChange={setRange} />
        </div>
      </section>

      {team ? (
        !teamReport.data ? (
          <Card collapsible={false}>
            <Loading error={teamReport.error} rows={6} />
          </Card>
        ) : (
          <div className={teamReport.isLoading ? "eval-dim" : undefined}>
            {teamReport.error && <Alert tone="error">Không tải được bộ lọc mới: {teamReport.error.message}.</Alert>}
            <p className="muted small report-scope">
              {teamReport.data.from} → {teamReport.data.to} · {teamReport.data.filters.business_project_name ?? "Mọi dự án"}
              {teamReport.data.filters.project_path ? ` / ${teamReport.data.filters.project_path}` : ""} · kỳ trước{" "}
              {teamReport.data.previous.from} → {teamReport.data.previous.to}
            </p>
            <TeamReportView
              data={teamReport.data}
              personHref={personHref}
              setId={setParam ? Number(setParam) : null}
              onSetId={(id) => setSetParam(String(id))}
            />
          </div>
        )
      ) : !user ? (
        <Card collapsible={false}>
          <EmptyState icon="user" title="Chọn một thành viên để xem báo cáo">
            Báo cáo cho biết người đó viết code tốt tới đâu: điểm review, tỉ lệ được duyệt, lỗi nghiêm trọng, loại lỗi hay
            gặp và điểm theo bộ tiêu chí, so với kỳ trước và với mặt bằng.
          </EmptyState>
        </Card>
      ) : !data ? (
        <Card collapsible={false}>
          <Loading error={report.error} rows={6} />
        </Card>
      ) : (
        <div className={report.isLoading ? "eval-dim" : undefined}>
          {report.error && <Alert tone="error">Không tải được bộ lọc mới: {report.error.message}.</Alert>}
          <p className="muted small report-scope">
            {data.from} → {data.to} · {data.filters.business_project_name ?? "Mọi dự án"}
            {data.filters.project_path ? ` / ${data.filters.project_path}` : ""} · kỳ trước {data.previous.from} → {data.previous.to}
            {" · "}mặt bằng: {data.peers.members} người
          </p>
          <SubTabs<Tab> id="mr-tabs" label="Phần của báo cáo" variant="bar" tabs={tabs} value={tab} onChange={setTabParam} />
          {tab === "overview" ? (
            <SubPanel id="mr-tabs" tab="overview">
              <Overview data={data} />
            </SubPanel>
          ) : (
            <SubPanel id="mr-tabs" tab="criteria">
              <CriteriaTab data={data} />
            </SubPanel>
          )}
        </div>
      )}
    </>
  );
}

/* ---- Tổng quan ---- */

function Overview({ data }: { data: MemberReport }) {
  const s = data.summary;
  const p = data.previous.summary;
  const peers = data.peers;
  const a = data.assessment;
  if (!s.reviews) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="inbox" title="Không có review nào trong khoảng này">
          Đổi khoảng thời gian, dự án hoặc repo. Kỳ trước có {p.reviews} review.
        </EmptyState>
      </Card>
    );
  }
  const weeks = data.weeks.map((w, i) => ({
    week: weekLabel(w),
    "Điểm TB": data.trend[i],
    "Mặt bằng": data.peer_trend[i],
    Review: data.trend_reviews[i],
  }));
  return (
    <>
      <section className={`card report-verdict tone-${LEVEL_TONE[a.level] ?? "none"}`} aria-labelledby="report-verdict">
        <div className="report-verdict-head">
          <span className="muted small">Đánh giá chung</span>
          <h2 id="report-verdict">
            <Pill tone={LEVEL_TONE[a.level]}>{a.label}</Pill>
            {a.thin && <span className="muted small"> · ít dữ liệu, chỉ tham khảo</span>}
          </h2>
        </div>
        <ul className="report-reasons">
          {a.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <p className="muted small" style={{ margin: 0 }}>
          Tốt: điểm TB ≥ 80, không critical và ≥ 60% được APPROVE · Cần hỗ trợ: có critical hoặc điểm TB dưới 65 · còn lại: Cần
          cải thiện.
        </p>
      </section>

      <StatStrip label="Chỉ số trong kỳ, so với kỳ trước">
        <StripStat label="Review" value={s.reviews} change={p.reviews ? s.reviews - p.reviews : null} sub={`${s.mrs} MR · ${s.commits} commit`} />
        <StripStat
          label="Điểm TB"
          value={s.avg_score}
          digits={1}
          tone={scoreTone(s.avg_score)}
          change={diff(s.avg_score, p.avg_score)}
          sub={peers.avg_score !== null ? `mặt bằng ${num(peers.avg_score, 1)}` : undefined}
          title="Trung bình theo số dòng code: review lớn nặng hơn review nhỏ"
        />
        <StripStat
          label="Tỉ lệ APPROVE"
          unit="%"
          value={s.pass_rate}
          change={diff(s.pass_rate, p.pass_rate)}
          sub={peers.pass_rate !== null ? `mặt bằng ${num(peers.pass_rate, 0)}%` : undefined}
        />
        <StripStat
          label="Critical + High"
          value={s.critical + s.high}
          tone={s.critical ? "bad" : s.high ? "warn" : undefined}
          change={p.reviews ? s.critical + s.high - (p.critical + p.high) : null}
          higherIsBetter={false}
          sub={`${s.critical} critical · ${s.high} high`}
        />
        <StripStat
          label="Lỗi / 100 LOC"
          value={s.findings_per_100_loc}
          digits={2}
          change={diff(s.findings_per_100_loc, p.findings_per_100_loc)}
          higherIsBetter={false}
          sub={peers.findings_per_100_loc !== null ? `mặt bằng ${num(peers.findings_per_100_loc, 2)}` : undefined}
        />
        <StripStat label="Dòng code" value={s.loc} sub={`${s.repos} repo`} />
      </StatStrip>

      <div className="grid two">
        <Card title="Điểm theo tuần" id="report-trend" subtitle="Cột: số review · đường: điểm TB của người này và của mặt bằng">
          <div className="chart-box" role="img" aria-label={`Điểm TB theo tuần: ${weeks.map((w) => `${w.week} ${num(w["Điểm TB"], 0)}`).join(", ")}`}>
            <ResponsiveContainer>
              <ComposedChart data={weeks} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                <YAxis yAxisId="score" domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                <YAxis yAxisId="n" orientation="right" hide />
                <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
                <Bar yAxisId="n" dataKey="Review" fill="var(--chart-grid)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                <Line yAxisId="score" dataKey="Mặt bằng" stroke="var(--chart-3)" strokeDasharray="4 4" dot={false} connectNulls isAnimationActive={false} />
                <Line yAxisId="score" dataKey="Điểm TB" stroke="var(--chart-1)" strokeWidth={2.5} dot={{ r: 2 }} connectNulls isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Kết luận và mức lỗi" id="report-levels">
          <h3 className="muted small">Kết luận review</h3>
          <VerdictMix verdicts={s.verdicts as Record<Verdict, number>} />
          <h3 className="muted small" style={{ marginTop: 14 }}>
            Finding được tính điểm
          </h3>
          <SeverityRow b={s} />
          <p className="muted small" style={{ marginBottom: 0 }}>
            Khiếu nại được chấp nhận: {s.disputed} · bị kiểm chứng bác bỏ: {s.rejected}
          </p>
        </Card>
      </div>

      <div className="grid two">
        <Card title="Loại lỗi hay gặp" id="report-categories" subtitle="Tỉ trọng trong lỗi của người này, so với mặt bằng: cột cao hơn mặt bằng là điểm cần kèm">
          {data.categories.length === 0 ? (
            <p className="muted small">Không có finding nào được tính điểm.</p>
          ) : (
            <ul className="report-cats">
              {data.categories.slice(0, 8).map((c) => {
                const more = c.share > c.peer_share * 1.5 && c.count >= 2;
                return (
                  <li key={c.category}>
                    <span className="report-cat-name">
                      {c.label}
                      {more && <Pill tone="warn">nhiều hơn mặt bằng</Pill>}
                    </span>
                    <span className="report-cat-bar" aria-hidden="true">
                      <span className="me" style={{ width: `${Math.min(c.share, 100)}%` }} />
                      <span className="peer" style={{ left: `${Math.min(c.peer_share, 100)}%` }} />
                    </span>
                    <span className="small">
                      {c.count} · {num(c.share, 0)}% <span className="muted">(mặt bằng {num(c.peer_share, 0)}%)</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <Card title="Theo repo" id="report-repos" flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Repo</th>
                  <th className="num">Review</th>
                  <th className="num">Điểm TB</th>
                  <th className="num">APPROVE</th>
                  <th className="num">Crit/High</th>
                </tr>
              </thead>
              <tbody>
                {data.by_repo.map((r) => (
                  <tr key={r.project_id ?? "none"}>
                    <td>
                      <code>{r.path ?? "(không rõ repo)"}</code>
                    </td>
                    <td className="num">{r.reviews}</td>
                    <td className="num">
                      <Score value={r.avg_score} />
                    </td>
                    <td className="num">{r.pass_rate === null ? "—" : `${num(r.pass_rate, 0)}%`}</td>
                    <td className="num">
                      <span className={r.critical ? "tone-bad" : undefined}>{r.critical}</span>/<span className={r.high ? "tone-warn" : undefined}>{r.high}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <div className="grid two">
        <ReviewList title="Review có critical / high" rows={data.severe_reviews} empty="Không có review nào có critical hoặc high." />
        <ReviewList title="Review điểm thấp nhất" rows={data.lowest_reviews} empty="Không có review nào có điểm." />
      </div>
    </>
  );
}

function ReviewList({ title, rows, empty }: { title: string; rows: ReviewRow[]; empty: string }) {
  return (
    <Card title={title} id={`report-${title}`} flush>
      {rows.length === 0 ? (
        <p className="muted small adm-pad">{empty}</p>
      ) : (
        <ul className="report-reviews">
          {rows.map((r) => (
            <li key={r.id}>
              <Link href={`/reviews/${r.id}`} className="report-review-link">
                <span className="report-review-main">
                  <code>{r.ref}</code> {r.title || (r.target_type === "mr" ? "Merge request" : "Commit")}
                </span>
                <span className="muted small">
                  {r.repo ?? "?"} · {dateTime(r.counted_at)} · {VERDICT_LABEL[r.verdict ?? ""] ?? "—"}
                  {r.critical ? ` · ${r.critical} critical` : ""}
                  {r.high ? ` · ${r.high} high` : ""}
                </span>
              </Link>
              <Score value={r.score} strong />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* ---- Theo tiêu chí ---- */

function CriteriaTab({ data }: { data: MemberReport }) {
  const sets = data.criteria_sets;
  const [setId, setSetId] = useState<number | null>(null);
  const chosen = sets.find((s) => s.set_id === setId) ?? sets[0];
  const scope = {
    from: data.from,
    to: data.to,
    business_project_id: data.filters.business_project_id,
    project_id: data.filters.project_id,
    set_id: chosen?.set_id ?? null,
  };
  const mine = useApi<CriteriaSummary>(chosen ? `/criteria/summary${qs({ ...scope, user_id: data.user.id })}` : null);
  const all = useApi<CriteriaSummary>(chosen ? `/criteria/summary${qs(scope)}` : null);
  if (!sets.length) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="scale" title="Chưa có review nào được chấm theo bộ tiêu chí">
          Dự án của các repo này chưa gán bộ tiêu chí, hoặc review trong khoảng này có trước khi gán. Admin gán bộ ở Admin › Dự án
          &amp; Repo › Tiêu chí chấm; review cũ được tính lại trong nền.
        </EmptyState>
      </Card>
    );
  }
  if (!mine.data || !all.data) return <Card collapsible={false}><Loading error={mine.error ?? all.error} rows={4} /></Card>;
  const peer = new Map(all.data.components.map((c) => [c.key, toNum(c.score)]));
  const parts = mine.data.components.map((c) => ({ ...c, me: toNum(c.score), peer: peer.get(c.key) ?? null }));
  const ranked = parts.filter((c) => c.me !== null).sort((a, b) => (a.me ?? 0) - (a.peer ?? a.me ?? 0) - ((b.me ?? 0) - (b.peer ?? b.me ?? 0)));
  const weak = ranked.slice(0, 2).filter((c) => c.me !== null && c.me < 80);
  const strong = [...ranked].reverse().slice(0, 2).filter((c) => c.me !== null && c.me >= 80);
  const final = toNum(mine.data.score);
  const peerFinal = toNum(all.data.score);
  return (
    <>
      <Card
        title="Điểm theo tiêu chí"
        id="report-criteria"
        collapsible={false}
        subtitle={`${mine.data.reviews} review được chấm · trung bình theo LOC · mặt bằng: mọi người trong cùng bộ lọc`}
        actions={
          sets.length > 1 && (
            <select className="compact" value={chosen?.set_id ?? ""} onChange={(e) => setSetId(Number(e.target.value))} aria-label="Chọn bộ tiêu chí">
              {sets.map((s) => (
                <option key={s.set_id} value={s.set_id}>
                  {s.name}
                  {s.is_primary ? " (bộ chính)" : ""} · {s.reviews} review
                </option>
              ))}
            </select>
          )
        }
      >
        <div className="report-crit-head">
          <div>
            <span className="muted small">
              {chosen?.name} {chosen?.version ? `v${chosen.version}` : ""}
            </span>
            <div className="crit-head-score">
              <span className={`crit-big ${scoreTone(final) ?? ""}`}>
                {num(final, 1)}
                <span className="unit">/ 100</span>
              </span>
              <span className="muted small">
                mặt bằng {num(peerFinal, 1)}
                {final !== null && peerFinal !== null && ` (${final >= peerFinal ? "+" : ""}${num(final - peerFinal, 1)})`}
              </span>
              <FormulaHelp />
            </div>
          </div>
          <div className="report-crit-notes">
            {strong.length > 0 && (
              <p>
                <Pill tone="good">Điểm mạnh</Pill> {strong.map((c) => `${c.name} ${num(c.me, 0)}`).join(", ")}
              </p>
            )}
            {weak.length > 0 && (
              <p>
                <Pill tone="warn">Cần cải thiện</Pill> {weak.map((c) => `${c.name} ${num(c.me, 0)}`).join(", ")}
              </p>
            )}
          </div>
        </div>
        <div className="table-wrap">
          <table className="report-crit-table">
            <thead>
              <tr>
                <th>Thành phần</th>
                <th className="num">Trọng số</th>
                <th>Điểm (vạch: mặt bằng)</th>
                <th className="num">Điểm</th>
                <th className="num">Mặt bằng</th>
                <th className="num">Chênh</th>
              </tr>
            </thead>
            <tbody>
              {parts.map((c) => {
                const d = diff(c.me, c.peer);
                return (
                  <tr key={c.key}>
                    <td>{c.name}</td>
                    <td className="num">{c.weight}%</td>
                    <td>
                      <span className="report-cat-bar wide" aria-hidden="true">
                        <span className={`me tone-${scoreTone(c.me) ?? "none"}`} style={{ width: `${c.me ?? 0}%` }} />
                        {c.peer !== null && <span className="peer" style={{ left: `${c.peer}%` }} />}
                      </span>
                    </td>
                    <td className="num">
                      <Score value={c.me} />
                    </td>
                    <td className="num">{num(c.peer, 1)}</td>
                    <td className={`num ${d === null ? "" : d >= 0 ? "tone-good" : d <= -5 ? "tone-bad" : "tone-warn"}`}>
                      {d === null ? "—" : `${d > 0 ? "+" : ""}${num(d, 1)}`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Điểm cuối theo tuần" id="report-criteria-trend">
        {mine.data.weeks.filter((w) => toNum(w.score) !== null).length < 2 ? (
          <p className="muted small">Cần ít nhất hai tuần có review để vẽ xu hướng.</p>
        ) : (
          <div className="chart-box" role="img" aria-label="Điểm cuối theo tuần">
            <ResponsiveContainer>
              <ComposedChart
                data={mine.data.weeks.map((w) => ({
                  week: weekLabel(w.week_start),
                  "Điểm cuối": toNum(w.score),
                  "Mặt bằng": toNum(all.data?.weeks.find((x) => x.week_start === w.week_start)?.score ?? null),
                }))}
                margin={{ top: 8, right: 8, bottom: 0, left: -20 }}
              >
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                <Tooltip content={<ChartTip />} />
                <Line dataKey="Mặt bằng" stroke="var(--chart-3)" strokeDasharray="4 4" dot={false} connectNulls isAnimationActive={false} />
                <Line dataKey="Điểm cuối" stroke="var(--chart-1)" strokeWidth={2.5} dot={{ r: 2 }} connectNulls isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>
    </>
  );
}

/** Several people to compare: a filterable checklist in a dropdown; none = the whole team / scope. */
function PeoplePicker({ people, value, onChange }: { people: Person[]; value: string[]; onChange: (ids: string[]) => void }) {
  const [query, setQuery] = useState("");
  const chosen = new Set(value);
  const q = query.trim().toLowerCase();
  const shown = people.filter((p) => !q || `${p.full_name ?? ""} ${p.primary_email ?? ""}`.toLowerCase().includes(q));
  const label = value.length
    ? people
        .filter((p) => chosen.has(String(p.id)))
        .map((p) => p.full_name ?? p.primary_email)
        .slice(0, 3)
        .join(", ") + (value.length > 3 ? ` +${value.length - 3}` : "")
    : "Cả team / mọi người";
  return (
    <div className="adm-field">
      <span className="small muted">Người so sánh</span>
      <details className="people-picker">
        <summary>{label}</summary>
        <div className="people-picker-pop">
          <input type="search" placeholder="Tìm người…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Tìm người" />
          <ul>
            {shown.map((p) => {
              const id = String(p.id);
              return (
                <li key={p.id}>
                  <label className="inline-check">
                    <input
                      type="checkbox"
                      checked={chosen.has(id)}
                      onChange={(e) => onChange(e.target.checked ? [...value, id] : value.filter((x) => x !== id))}
                    />
                    {p.full_name ?? p.primary_email ?? `#${p.id}`}
                  </label>
                </li>
              );
            })}
          </ul>
          {value.length > 0 && (
            <button type="button" className="ghost sm" onClick={() => onChange([])}>
              Bỏ chọn ({value.length})
            </button>
          )}
        </div>
      </details>
    </div>
  );
}
