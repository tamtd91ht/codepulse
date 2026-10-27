"use client";

import { useMemo, useState } from "react";
import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { AttentionBadge, RangePicker, Score, VerdictMix, rangeLabel, rangeSearch, scoreTone, useUrlRange } from "@/components/Evaluation";
import { Icon } from "@/components/icons";
import { Alert, Card, ChartTip, CountLink, EmptyState, Kpi, Loading, PageHeader, PersonLink, Sparkline, SortTh, useSort } from "@/components/ui";
import { boardLink, qs, useApi } from "@/lib/api";
import { num, relative } from "@/lib/format";
import type { Me, MemberEvaluationRow, MembersEvaluation, Repo, Team } from "@/lib/types";

const cell = (m: MemberEvaluationRow, key: string): number | string | null => {
  switch (key) {
    case "name":
      return m.user.full_name ?? m.user.primary_email ?? "";
    case "reviews":
      return m.reviews;
    case "score":
      return m.avg_score;
    case "min":
      return m.min_score;
    case "pass":
      return m.pass_rate;
    case "critical":
      return m.critical;
    case "high":
      return m.high;
    case "other":
      return m.medium + m.low;
    case "density":
      return m.findings_per_100_loc;
    case "last":
      return m.last_review_at;
    default:
      return null;
  }
};

function MembersTable({
  rows,
  link,
  search,
}: {
  rows: MemberEvaluationRow[];
  link: (severity: "critical" | "high", m: MemberEvaluationRow) => string;
  /** The period, carried to each person's page. */
  search: string;
}) {
  const { sorted, sort, toggle } = useSort(rows, { key: "score", dir: "asc" }, cell);
  return (
    <div className="table-wrap">
      <table className="eval-table">
        <thead>
          <tr>
            <SortTh id="name" sort={sort} onSort={toggle}>
              Thành viên
            </SortTh>
            <SortTh id="reviews" sort={sort} onSort={toggle} num>
              Review
            </SortTh>
            <SortTh id="score" sort={sort} onSort={toggle} num>
              Điểm TB
            </SortTh>
            <SortTh id="pass" sort={sort} onSort={toggle}>
              Kết luận
            </SortTh>
            <SortTh id="critical" sort={sort} onSort={toggle} num>
              Critical
            </SortTh>
            <SortTh id="high" sort={sort} onSort={toggle} num>
              High
            </SortTh>
            <SortTh id="other" sort={sort} onSort={toggle} num>
              Medium + Low
            </SortTh>
            <SortTh id="density" sort={sort} onSort={toggle} num>
              Lỗi / 100 LOC
            </SortTh>
            <th>Theo tuần</th>
            <SortTh id="last" sort={sort} onSort={toggle}>
              Review gần nhất
            </SortTh>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => (
            <tr key={m.user_id} className={m.needs_attention ? "attention" : undefined}>
              <td>
                <div className="cell-main">
                  <PersonLink id={m.user_id} name={m.user.full_name} email={m.user.primary_email} search={search} />
                  <AttentionBadge b={m} />
                </div>
                {m.teams.length > 0 && <div className="sub muted small">{m.teams.join(" · ")}</div>}
              </td>
              <td className="num">
                {m.reviews ? (
                  <>
                    <strong>{m.reviews}</strong>
                    <div className="sub muted small nowrap">
                      {m.mrs} MR · {m.commits} commit
                    </div>
                  </>
                ) : (
                  <span className="faint">0</span>
                )}
              </td>
              <td className="num">
                <Score value={m.avg_score} strong />
                {m.min_score !== null && m.reviews > 1 && (
                  <div className="sub muted small nowrap">thấp nhất {num(m.min_score, 0)}</div>
                )}
              </td>
              <td>
                <VerdictMix verdicts={m.verdicts} compact />
              </td>
              <td className={`num${m.critical ? " bad" : ""}`}>
                <CountLink n={m.critical} href={link("critical", m)} />
              </td>
              <td className={`num${m.high ? " warn" : ""}`}>
                <CountLink n={m.high} href={link("high", m)} />
              </td>
              <td className="num">{m.medium + m.low || <span className="faint">0</span>}</td>
              <td className="num">{m.findings_per_100_loc === null ? <span className="faint">—</span> : num(m.findings_per_100_loc, 2)}</td>
              <td>
                <Sparkline values={m.trend.map((v) => (v === null ? null : String(v)))} />
              </td>
              <td className="muted nowrap">{m.last_review_at ? relative(m.last_review_at) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Average score per person, lowest first, so who needs help reads from the top. */
function ScoreChart({ rows }: { rows: MemberEvaluationRow[] }) {
  const data = rows
    .filter((m) => m.avg_score !== null)
    .sort((a, b) => (a.avg_score ?? 0) - (b.avg_score ?? 0))
    .slice(0, 25);
  // Two people may share a name: the axis needs distinct categories.
  const names = new Map<string, number>();
  for (const m of data) {
    const n = m.user.full_name ?? m.user.primary_email ?? "";
    names.set(n, (names.get(n) ?? 0) + 1);
  }
  const bars = data.map((m) => {
    const n = m.user.full_name ?? m.user.primary_email ?? `#${m.user_id}`;
    return {
      id: m.user_id,
      name: (names.get(n) ?? 0) > 1 ? `${n} (#${m.user_id})` : n,
      score: m.avg_score ?? 0,
      reviews: m.reviews,
      tone: scoreTone(m.avg_score),
    };
  });
  if (!bars.length) return <EmptyState title="Chưa có điểm trong khoảng này">Điểm xuất hiện khi có review hoàn tất.</EmptyState>;
  const color = (tone?: string) => (tone === "good" ? "var(--chart-2)" : tone === "warn" ? "var(--chart-3)" : "var(--sev-critical)");
  return (
    <div className="chart-box" style={{ height: Math.max(160, 28 * bars.length + 24) }}>
      <ResponsiveContainer>
        <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 36, bottom: 0, left: 8 }} barCategoryGap="22%">
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis
            type="category"
            dataKey="name"
            width={112}
            tickLine={false}
            axisLine={false}
            fontSize={12}
            tickFormatter={(v: string) => (v.length > 16 ? `${v.slice(0, 15)}…` : v)}
          />
          <Tooltip content={<ChartTip digits={1} />} cursor={{ fill: "var(--surface-2)" }} />
          <Bar dataKey="score" name="Điểm TB" radius={[0, 4, 4, 0]} maxBarSize={18} isAnimationActive={false}>
            {bars.map((d) => (
              <Cell key={d.id} fill={color(d.tone)} />
            ))}
            <LabelList dataKey="score" position="right" formatter={(v) => num(typeof v === "number" ? v : Number(v), 1)} fontSize={12} fill="var(--text-2)" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function MembersEvaluationPage() {
  // In the URL: a person's page opened from the table shows the same period.
  const [range, setRange] = useUrlRange("30");
  const [teamId, setTeamId] = useState("");
  const [bpId, setBpId] = useState("");
  const [repoId, setRepoId] = useState("");
  const [search, setSearch] = useState("");
  const [onlyAttention, setOnlyAttention] = useState(false);
  const [reviewedOnly, setReviewedOnly] = useState(false);

  const { data: me } = useApi<Me>("/auth/me");
  const { data: teams } = useApi<Team[]>("/teams/visible");
  const { data: repos } = useApi<Repo[]>("/repos");
  const path =
    range && range.from && range.to
      ? `/members/evaluation${qs({ from: range.from, to: range.to, team_id: teamId, business_project_id: bpId, project_id: repoId })}`
      : null;
  const { data, error, isLoading } = useApi<MembersEvaluation>(path, { keepPreviousData: true });

  const projects = useMemo(() => {
    const seen = new Map<number, string>();
    for (const r of repos ?? []) if (r.business_project_id !== null && r.business_project) seen.set(r.business_project_id, r.business_project);
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1], "vi"));
  }, [repos]);
  const repoOptions = useMemo(() => (repos ?? []).filter((r) => !bpId || String(r.business_project_id) === bpId), [repos, bpId]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.members ?? []).filter(
      (m) =>
        (!onlyAttention || m.needs_attention) &&
        (!reviewedOnly || m.reviews > 0) &&
        (!q || (m.user.full_name ?? "").toLowerCase().includes(q) || (m.user.primary_email ?? "").toLowerCase().includes(q)),
    );
  }, [data, search, onlyAttention, reviewedOnly]);

  const isLead = !!me && (me.is_admin || me.lead_teams.length > 0);
  // Links pair the response's own range with its own label, also while the next one loads.
  const period = data ? { range: data.range, label: rangeLabel(data.from, data.to), counted: false as const } : undefined;
  const link = (severity: "critical" | "high", m: MemberEvaluationRow) =>
    boardLink(severity, { user: { id: m.user_id, name: m.user.full_name ?? m.user.primary_email ?? "" }, period });
  const t = data?.totals;
  const reviewed = data?.members.filter((m) => m.reviews > 0).length ?? 0;
  const attention = data?.members.filter((m) => m.needs_attention).length ?? 0;

  return (
    <>
      <PageHeader
        eyebrow="Đánh giá"
        title="Thành viên"
        subtitle="Mỗi người đã được review bao nhiêu lần, điểm ra sao và lỗi gì, trong khoảng thời gian và phạm vi đã chọn. Bấm tên để xem chi tiết."
        actions={<RangePicker value={range} onChange={setRange} />}
      />

      <div className="toolbar filters">
        <label className="filter">
          <span className="small muted">Team</span>
          <select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Lọc theo team">
            <option value="">Mọi team</option>
            {(teams ?? []).map((tm) => (
              <option key={tm.id} value={tm.id}>
                {tm.name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Dự án</span>
          <select
            value={bpId}
            onChange={(e) => {
              setBpId(e.target.value);
              setRepoId("");
            }}
            aria-label="Lọc theo dự án"
          >
            <option value="">Mọi dự án</option>
            {projects.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="small muted">Repo</span>
          <select value={repoId} onChange={(e) => setRepoId(e.target.value)} aria-label="Lọc theo repo">
            <option value="">Mọi repo</option>
            {repoOptions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.path_with_namespace}
              </option>
            ))}
          </select>
        </label>
        <label className="filter search">
          <span className="small muted">Tìm</span>
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Tên hoặc email" aria-label="Tìm thành viên" />
        </label>
        <span className="spacer" />
        <label className="check">
          <input type="checkbox" checked={reviewedOnly} onChange={(e) => setReviewedOnly(e.target.checked)} />
          Chỉ người có review
        </label>
        <label className="check">
          <input type="checkbox" checked={onlyAttention} onChange={(e) => setOnlyAttention(e.target.checked)} />
          Chỉ người cần hỗ trợ{attention ? ` (${attention})` : ""}
        </label>
      </div>

      {!data && <Loading error={error} rows={4} />}
      {data && error && (
        <Alert tone="error">
          Không tải được số liệu cho bộ lọc mới: {error.message}. Đang hiện số liệu {rangeLabel(data.from, data.to)}.
        </Alert>
      )}
      {data && t && (
        <>
          <div className={`grid kpis${isLoading ? " eval-dim" : ""}`}>
            <Kpi label="Người có review" value={reviewed} digits={0} change={false} hint={`trên ${data.members.length} người trong phạm vi`} />
            <Kpi label="Review hoàn tất" value={t.reviews} digits={0} change={false} hint={`${t.mrs} MR · ${t.commits} commit · ${t.loc.toLocaleString("vi")} LOC`} />
            <Kpi label="Điểm TB (theo LOC)" value={t.avg_score} change={false} hint="Review lớn nặng hơn review nhỏ" />
            <Kpi label="Tỉ lệ approve" unit="%" value={t.pass_rate} digits={0} change={false} hint={`${t.verdicts.needs_human} cần người xem · ${t.verdicts.block} block`} />
            <Kpi label="Critical" value={t.critical} digits={0} change={false} higherIsBetter={false} href={t.critical && period ? boardLink("critical", { period }) : undefined} />
            <Kpi label="High" value={t.high} digits={0} change={false} higherIsBetter={false} href={t.high && period ? boardLink("high", { period }) : undefined} />
          </div>

          <div className="grid two eval-grid">
            <Card title="Điểm trung bình theo người" subtitle="Thấp nhất ở trên. Xanh ≥ 80, vàng 65–79, đỏ < 65.">
              <ScoreChart rows={rows} />
            </Card>
            <Card title="Kết luận review của cả nhóm" subtitle="Tỉ lệ approve / cần người xem / fix required / block">
              <VerdictMix verdicts={t.verdicts} />
              <dl className="eval-facts">
                <div>
                  <dt>Finding tính điểm</dt>
                  <dd>
                    {t.findings} <span className="muted small">({t.critical} critical · {t.high} high · {t.medium} medium · {t.low} low)</span>
                  </dd>
                </div>
                <div>
                  <dt>Khiếu nại được chấp nhận</dt>
                  <dd>{t.disputed}</dd>
                </div>
                <div>
                  <dt>Bị kiểm chứng bác bỏ</dt>
                  <dd>{t.rejected}</dd>
                </div>
                <div>
                  <dt>Mật độ lỗi</dt>
                  <dd>{t.findings_per_100_loc === null ? "—" : `${num(t.findings_per_100_loc, 2)} / 100 LOC`}</dd>
                </div>
              </dl>
            </Card>
          </div>

          <Card
            title={`Bảng thành viên (${rows.length})`}
            subtitle="Bấm tiêu đề cột để sắp xếp. Dòng đỏ: có critical hoặc điểm TB dưới 65. Số critical / high mở danh sách finding."
            flush
          >
            {rows.length ? (
              <MembersTable rows={rows} link={link} search={rangeSearch(range)} />
            ) : (
              <EmptyState icon="members" title="Không có ai khớp bộ lọc">
                {data.members.length === 0
                  ? isLead
                    ? "Chưa có thành viên nào trong phạm vi. Thêm người vào team ở Admin › Người & Team."
                    : "Trang này chỉ hiện người trong phạm vi của bạn: bản thân, hoặc thành viên team bạn lead."
                  : "Nới bộ lọc hoặc khoảng thời gian."}
              </EmptyState>
            )}
          </Card>
          {!isLead && (
            <p className="muted small" style={{ marginTop: 12 }}>
              <Icon name="info" size={14} /> Điểm cá nhân chỉ bản thân và lead xem được (quyết định Phase 0). Lead sẽ thấy cả team ở đây.
            </p>
          )}
        </>
      )}
    </>
  );
}
