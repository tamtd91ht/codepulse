"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { Score, scoreTone } from "@/components/Evaluation";
import { Card, ChartTip, EmptyState, Pill, SortTh, StatStrip, StripStat, useSort } from "@/components/ui";
import { num } from "@/lib/format";
import type { EvaluationBucket, Person } from "@/lib/types";

/* GET /members/team-report (docs/specs/23 §8) */

type Level = "good" | "fair" | "support" | "none";

export interface TeamReportRow extends EvaluationBucket {
  user: Person;
  previous_avg_score: number | null;
  assessment: { level: Level; label: string; reasons: string[]; thin: boolean };
  trend: (number | null)[];
  categories: Record<string, number>;
  top_category: string | null;
  criteria: { reviews: number; score: number | null; components: Record<string, number | null> } | null;
}

export interface TeamReport {
  from: string;
  to: string;
  filters: {
    team_id: number | null;
    team_name: string | null;
    business_project_name: string | null;
    project_path: string | null;
  };
  previous: { from: string; to: string; summary: EvaluationBucket & { members: number } };
  summary: EvaluationBucket & { members: number };
  levels: Record<Level, number>;
  weeks: string[];
  trend: (number | null)[];
  members: TeamReportRow[];
  categories: { category: string; label: string; count: number; share: number }[];
  criteria_sets: { set_id: number; name: string; version: number | null; is_primary: boolean; reviews: number }[];
  criteria: {
    set_id: number;
    name: string;
    version: number | null;
    components: { key: string; name: string; weight: number }[];
    group: { reviews: number; score: number | null; components: Record<string, number | null> };
  } | null;
}

const LEVEL_TONE: Record<Level, "good" | "warn" | "bad" | undefined> = { good: "good", fair: "warn", support: "bad", none: undefined };
const LEVEL_LABEL: Record<Level, string> = { good: "Tốt", fair: "Cần cải thiện", support: "Cần hỗ trợ", none: "Chưa có dữ liệu" };
type Tab = "overview" | "categories" | "criteria";

const diff = (a: number | null, b: number | null) => (a === null || b === null ? null : Math.round((a - b) * 10) / 10);
const nameOf = (p: Person) => p.full_name ?? p.primary_email ?? `#${p.id}`;

/** A cell shaded by how far it sits from the group: the heat maps of mistakes and criteria. */
function Heat({ value, tone, children }: { value: number; tone: "bad" | "good" | "warn"; children: ReactNode }) {
  const strength = Math.max(0, Math.min(value, 100));
  return (
    <td className="num heat" style={{ background: `color-mix(in srgb, var(--${tone}) ${Math.round(strength * 0.45)}%, transparent)` }}>
      {children}
    </td>
  );
}

/**
 * Báo cáo nhóm: everyone side by side. The order answers "who needs attention" (weakest first);
 * the chart puts each person against the group average; the mistakes heat map says who makes
 * which kind of mistake more than the group; the criteria heat map says who is weak where.
 */
export function TeamReportView({
  data,
  personHref,
  setId,
  onSetId,
}: {
  data: TeamReport;
  personHref: (userId: number) => string;
  setId: number | null;
  onSetId: (id: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const s = data.summary;
  const p = data.previous.summary;
  const tabs: SubTab<Tab>[] = [
    { key: "overview", label: "So sánh", count: data.members.length, hint: "Mỗi người một dòng, người cần chú ý trước" },
    { key: "categories", label: "Loại lỗi", hint: "Ai hay mắc loại lỗi nào, so với cả nhóm" },
    { key: "criteria", label: "Theo tiêu chí", count: data.criteria_sets.length || undefined, hint: "Điểm từng thành phần của từng người" },
  ];
  return (
    <>
      <div className="team-levels" aria-label="Số người theo mức đánh giá">
        {(["support", "fair", "good", "none"] as Level[]).map((k) => (
          <div key={k} className={`card team-level tone-${LEVEL_TONE[k] ?? "none"}`}>
            <span className="team-level-n">{data.levels[k] ?? 0}</span>
            <span className="muted small">{LEVEL_LABEL[k]}</span>
          </div>
        ))}
      </div>
      <StatStrip label="Cả nhóm trong kỳ, so với kỳ trước">
        <StripStat label="Người có review" value={s.members} />
        <StripStat label="Review" value={s.reviews} change={p.reviews ? s.reviews - p.reviews : null} sub={`${s.mrs} MR · ${s.commits} commit`} />
        <StripStat label="Điểm TB" value={s.avg_score} digits={1} tone={scoreTone(s.avg_score)} change={diff(s.avg_score, p.avg_score)} />
        <StripStat label="Tỉ lệ APPROVE" unit="%" value={s.pass_rate} change={diff(s.pass_rate, p.pass_rate)} />
        <StripStat label="Critical + High" value={s.critical + s.high} tone={s.critical ? "bad" : s.high ? "warn" : undefined} change={p.reviews ? s.critical + s.high - (p.critical + p.high) : null} higherIsBetter={false} />
        <StripStat label="Lỗi / 100 LOC" value={s.findings_per_100_loc} digits={2} change={diff(s.findings_per_100_loc, p.findings_per_100_loc)} higherIsBetter={false} />
      </StatStrip>
      <SubTabs<Tab> id="team-tabs" label="Phần của báo cáo nhóm" variant="mini" tabs={tabs} value={tab} onChange={setTab} />
      {tab === "overview" && (
        <SubPanel id="team-tabs" tab="overview">
          <Compare data={data} personHref={personHref} />
        </SubPanel>
      )}
      {tab === "categories" && (
        <SubPanel id="team-tabs" tab="categories">
          <Categories data={data} personHref={personHref} />
        </SubPanel>
      )}
      {tab === "criteria" && (
        <SubPanel id="team-tabs" tab="criteria">
          <Criteria data={data} personHref={personHref} setId={setId} onSetId={onSetId} />
        </SubPanel>
      )}
    </>
  );
}

function Compare({ data, personHref }: { data: TeamReport; personHref: (id: number) => string }) {
  const rows = data.members;
  const cell = (r: TeamReportRow, key: string): number | string | null => {
    switch (key) {
      case "name":
        return nameOf(r.user);
      case "level":
        return { support: 0, fair: 1, good: 2, none: 3 }[r.assessment.level];
      case "reviews":
        return r.reviews;
      case "score":
        return r.avg_score;
      case "delta":
        return diff(r.avg_score, r.previous_avg_score);
      case "pass":
        return r.pass_rate;
      case "severe":
        return r.critical * 1000 + r.high;
      case "density":
        return r.findings_per_100_loc;
      default:
        return null;
    }
  };
  const { sorted, sort, toggle } = useSort(rows, { key: "level", dir: "asc" }, cell);
  const chart = rows
    .filter((r) => r.avg_score !== null)
    .map((r) => ({ name: nameOf(r.user), "Điểm TB": r.avg_score, level: r.assessment.level }))
    .sort((a, b) => (b["Điểm TB"] ?? 0) - (a["Điểm TB"] ?? 0));
  if (!rows.length) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="members" title="Không có ai trong phạm vi này">
          Chọn team khác, hoặc chọn người cần so sánh.
        </EmptyState>
      </Card>
    );
  }
  return (
    <>
      {chart.length > 0 && (
        <Card title="Điểm TB từng người" id="team-chart" subtitle={`Vạch: điểm TB cả nhóm ${num(data.summary.avg_score, 1)} · màu theo mức đánh giá`}>
          <div className="chart-box" style={{ height: Math.max(160, chart.length * 30 + 40) }} role="img" aria-label={chart.map((c) => `${c.name} ${num(c["Điểm TB"], 0)}`).join(", ")}>
            <ResponsiveContainer>
              <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }}>
                <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
                <XAxis type="number" domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                <YAxis type="category" dataKey="name" width={140} tickLine={false} axisLine={false} fontSize={12} />
                <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
                {data.summary.avg_score !== null && <ReferenceLine x={data.summary.avg_score} stroke="var(--text)" strokeDasharray="4 4" />}
                <Bar dataKey="Điểm TB" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  {chart.map((c) => (
                    <Cell key={c.name} fill={`var(--${LEVEL_TONE[c.level as Level] ?? "chart-1"})`} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}
      <Card title="So sánh thành viên" id="team-compare" flush subtitle="Bấm tên để mở báo cáo riêng của người đó với cùng bộ lọc">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <SortTh id="name" sort={sort} onSort={toggle}>Thành viên</SortTh>
                <SortTh id="level" sort={sort} onSort={toggle}>Mức</SortTh>
                <SortTh id="reviews" sort={sort} onSort={toggle} num>Review</SortTh>
                <SortTh id="score" sort={sort} onSort={toggle} num>Điểm TB</SortTh>
                <SortTh id="delta" sort={sort} onSort={toggle} num>So kỳ trước</SortTh>
                <SortTh id="pass" sort={sort} onSort={toggle} num>APPROVE</SortTh>
                <SortTh id="severe" sort={sort} onSort={toggle} num>Crit / High</SortTh>
                <SortTh id="density" sort={sort} onSort={toggle} num>Lỗi/100 LOC</SortTh>
                <th>Hay mắc</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const d = diff(r.avg_score, r.previous_avg_score);
                return (
                  <tr key={r.user.id}>
                    <td>
                      <Link href={personHref(r.user.id)}>{nameOf(r.user)}</Link>
                    </td>
                    <td title={r.assessment.reasons.join(" ")}>
                      <Pill tone={LEVEL_TONE[r.assessment.level]}>{r.assessment.label}</Pill>
                      {r.assessment.thin && r.reviews > 0 && <span className="muted small"> ít dữ liệu</span>}
                    </td>
                    <td className="num">{r.reviews}</td>
                    <td className="num">
                      <Score value={r.avg_score} strong />
                    </td>
                    <td className={`num ${d === null ? "" : d >= 0 ? "tone-good" : "tone-bad-text"}`}>{d === null ? "—" : `${d > 0 ? "+" : ""}${num(d, 1)}`}</td>
                    <td className="num">{r.pass_rate === null ? "—" : `${num(r.pass_rate, 0)}%`}</td>
                    <td className="num">
                      <span className={r.critical ? "tone-bad-text" : undefined}>{r.critical}</span> / {r.high}
                    </td>
                    <td className="num">{num(r.findings_per_100_loc, 2)}</td>
                    <td className="small">{r.top_category ?? <span className="faint">—</span>}</td>
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

function Categories({ data, personHref }: { data: TeamReport; personHref: (id: number) => string }) {
  const cats = data.categories;
  const rows = data.members.filter((m) => m.findings > 0);
  if (!cats.length) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="checkCircle" title="Không có finding nào được tính điểm">Trong khoảng này nhóm không có lỗi nào được tính.</EmptyState>
      </Card>
    );
  }
  return (
    <Card
      title="Ai hay mắc loại lỗi nào"
      id="team-categories"
      flush
      subtitle="Mỗi ô: tỉ trọng loại lỗi đó trong lỗi của người đó. Đậm hơn hẳn dòng Cả nhóm = điểm cần kèm riêng người đó."
    >
      <div className="table-wrap">
        <table className="heat-table">
          <thead>
            <tr>
              <th>Thành viên</th>
              {cats.map((c) => (
                <th key={c.category} className="num">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.user.id}>
                <td>
                  <Link href={personHref(m.user.id)}>{nameOf(m.user)}</Link> <span className="muted small">{m.findings} lỗi</span>
                </td>
                {cats.map((c) => {
                  const v = m.categories[c.category] ?? 0;
                  return (
                    <Heat key={c.category} value={v} tone="bad">
                      {v ? `${num(v, 0)}%` : <span className="faint">·</span>}
                    </Heat>
                  );
                })}
              </tr>
            ))}
            <tr className="heat-total">
              <td>Cả nhóm</td>
              {cats.map((c) => (
                <td key={c.category} className="num">
                  {num(c.share, 0)}%
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Criteria({
  data,
  personHref,
  setId,
  onSetId,
}: {
  data: TeamReport;
  personHref: (id: number) => string;
  setId: number | null;
  onSetId: (id: number) => void;
}) {
  const crit = data.criteria;
  if (!crit) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="scale" title="Chưa có review nào được chấm theo bộ tiêu chí">
          Dự án của các repo này chưa gán bộ tiêu chí, hoặc review trong khoảng này có trước khi gán.
        </EmptyState>
      </Card>
    );
  }
  const rows = data.members.filter((m) => m.criteria && m.criteria.reviews > 0);
  const g = crit.group;
  return (
    <Card
      title={`Theo tiêu chí · ${crit.name}${crit.version ? ` v${crit.version}` : ""}`}
      id="team-criteria"
      flush
      subtitle="Mỗi ô: điểm TB (theo LOC) của thành phần đó. Màu theo mức: xanh ≥ 80, vàng 65–80, đỏ dưới 65."
      actions={
        data.criteria_sets.length > 1 && (
          <select className="compact" value={setId ?? crit.set_id} onChange={(e) => onSetId(Number(e.target.value))} aria-label="Chọn bộ tiêu chí">
            {data.criteria_sets.map((s) => (
              <option key={s.set_id} value={s.set_id}>
                {s.name}
                {s.is_primary ? " (bộ chính)" : ""} · {s.reviews} review
              </option>
            ))}
          </select>
        )
      }
    >
      <div className="table-wrap">
        <table className="heat-table">
          <thead>
            <tr>
              <th>Thành viên</th>
              <th className="num">Review</th>
              <th className="num">Điểm cuối</th>
              {crit.components.map((c) => (
                <th key={c.key} className="num" title={`Trọng số ${c.weight}%`}>
                  {c.name} <span className="muted small">{c.weight}%</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.user.id}>
                <td>
                  <Link href={personHref(m.user.id)}>{nameOf(m.user)}</Link>
                </td>
                <td className="num">{m.criteria?.reviews}</td>
                <td className="num">
                  <Score value={m.criteria?.score ?? null} strong />
                </td>
                {crit.components.map((c) => {
                  const v = m.criteria?.components[c.key] ?? null;
                  const tone = scoreTone(v);
                  return v === null ? (
                    <td key={c.key} className="num faint">—</td>
                  ) : (
                    <Heat key={c.key} value={tone === "good" ? 35 : tone === "warn" ? 55 : 70} tone={tone ?? "bad"}>
                      {num(v, 0)}
                    </Heat>
                  );
                })}
              </tr>
            ))}
            <tr className="heat-total">
              <td>Cả nhóm</td>
              <td className="num">{g.reviews}</td>
              <td className="num">
                <Score value={g.score} strong />
              </td>
              {crit.components.map((c) => (
                <td key={c.key} className="num">
                  {num(g.components[c.key] ?? null, 0)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
  );
}
