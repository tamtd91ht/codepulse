"use client";

import { type ReactNode, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubPanel, type SubTab, SubTabs } from "@/components/AdminTabs";
import { Score, scoreTone } from "@/components/Evaluation";
import { Card, ChartTip, EmptyState, Pill, Segmented, SortTh, useSort } from "@/components/ui";
import { num } from "@/lib/format";
import type { EvaluationBucket, Person } from "@/lib/types";

/* GET /members/team-report (docs/specs/23 §8–9) */

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
const LEVEL_ORDER: Level[] = ["support", "fair", "good", "none"];

const diff = (a: number | null, b: number | null) => (a === null || b === null ? null : Math.round((a - b) * 10) / 10);
const nameOf = (p: Person) => p.full_name ?? p.primary_email ?? `#${p.id}`;

/* ---- the chart: one horizontal bar per member, the number printed on it ---- */

type Metric = "score" | "pass" | "critical" | "high" | "density" | "reviews" | "criteria";

interface MetricDef {
  label: string;
  get: (r: TeamReportRow) => number | null;
  digits: number;
  unit?: string;
  /** Lower is better: sorted ascending, coloured by the person's level instead of the value. */
  lowerBetter?: boolean;
  max?: number;
  group: (d: TeamReport) => number | null;
  hint: string;
  /** A count shown as a second bar beside the rate (critical / high): the pair of columns. */
  pair?: (r: TeamReportRow) => number;
}

const METRICS: Record<Metric, MetricDef> = {
  score: {
    label: "Điểm TB",
    get: (r) => r.avg_score,
    digits: 1,
    max: 100,
    group: (d) => d.summary.avg_score,
    hint: "Điểm review trung bình theo số dòng code (0–100)",
  },
  pass: {
    label: "APPROVE %",
    get: (r) => r.pass_rate,
    digits: 0,
    unit: "%",
    max: 100,
    group: (d) => d.summary.pass_rate,
    hint: "Phần trăm thay đổi được kết luận APPROVE",
  },
  critical: {
    label: "Critical",
    get: (r) => (r.reviews ? (r.critical_rate ?? null) : null),
    digits: 0,
    unit: "%",
    lowerBetter: true,
    max: 100,
    group: (d) => d.summary.critical_rate ?? null,
    hint: "Cặp thanh: số finding critical, và % review có critical (khách quan hơn khi số review khác nhau)",
    pair: (r) => r.critical,
  },
  high: {
    label: "High",
    get: (r) => (r.reviews ? (r.high_rate ?? null) : null),
    digits: 0,
    unit: "%",
    lowerBetter: true,
    max: 100,
    group: (d) => d.summary.high_rate ?? null,
    hint: "Cặp thanh: số finding high, và % review có high (khách quan hơn khi số review khác nhau)",
    pair: (r) => r.high,
  },
  density: {
    label: "Lỗi / 100 LOC",
    get: (r) => r.findings_per_100_loc,
    digits: 2,
    lowerBetter: true,
    group: (d) => d.summary.findings_per_100_loc,
    hint: "Số lỗi được tính điểm trên 100 dòng code",
  },
  reviews: {
    label: "Số review",
    get: (r) => (r.reviews ? r.reviews : null),
    digits: 0,
    group: () => null,
    hint: "Số MR và commit đã review xong",
  },
  criteria: {
    label: "Điểm tiêu chí",
    get: (r) => r.criteria?.score ?? null,
    digits: 1,
    max: 100,
    group: (d) => d.criteria?.group.score ?? null,
    hint: "Điểm cuối theo bộ tiêu chí (tab Tiêu chí để đổi bộ)",
  },
};

function barColor(metric: Metric, row: TeamReportRow, value: number): string {
  const def = METRICS[metric];
  if (metric === "reviews") return "var(--chart-1)";
  if (def.lowerBetter) return `var(--${LEVEL_TONE[row.assessment.level] ?? "chart-1"})`;
  const tone = scoreTone(metric === "pass" ? (value >= 60 ? 80 : value >= 40 ? 70 : 0) : value);
  return `var(--${tone ?? "chart-1"})`;
}

function MemberChart({ data, onOpen }: { data: TeamReport; onOpen: (userId: number) => void }) {
  const [metric, setMetric] = useState<Metric>("score");
  const def = METRICS[metric];
  const rows = data.members
    .map((r) => ({ row: r, value: def.get(r) }))
    .filter((x): x is { row: TeamReportRow; value: number } => x.value !== null)
    .sort((a, b) => (def.lowerBetter ? b.value - a.value : a.value - b.value)); // weakest on top
  const chart = rows.map(({ row, value }) => ({
    id: row.user.id,
    name: nameOf(row.user),
    value,
    count: def.pair ? def.pair(row) : null,
    row,
  }));
  const paired = Boolean(def.pair);
  const missing = data.members.length - chart.length;
  const group = def.group(data);
  const options = (Object.keys(METRICS) as Metric[])
    .filter((k) => k !== "criteria" || data.criteria)
    .map((k) => ({ value: k, label: METRICS[k].label }));
  return (
    <Card
      title={`${def.label} từng người`}
      id="report-chart"
      collapsible={false}
      subtitle={`${def.hint} · người yếu nhất ở trên · bấm một thanh để xem chi tiết người đó${group !== null ? ` · vạch đứt: cả nhóm ${num(group, def.digits)}${def.unit ?? ""}` : ""}`}
      actions={<Segmented<Metric> label="Chỉ số" value={metric} onChange={setMetric} options={options} />}
    >
      {chart.length === 0 ? (
        <p className="muted small">Không ai có số liệu này trong khoảng đã chọn.</p>
      ) : (
        <div className="member-chart" style={{ height: Math.max(140, chart.length * (paired ? 46 : 32) + 44) }}>
          <ResponsiveContainer>
            <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 56, bottom: 4, left: 4 }} barCategoryGap={6}>
              <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
              <XAxis xAxisId="value" type="number" domain={[0, def.max ?? "auto"]} tickLine={false} axisLine={false} fontSize={12} unit={def.unit} />
              {paired && <XAxis xAxisId="count" type="number" orientation="top" allowDecimals={false} tickLine={false} axisLine={false} fontSize={12} />}
              <YAxis type="category" dataKey="name" width={150} tickLine={false} axisLine={false} fontSize={12} interval={0} />
              <Tooltip content={<ChartTip />} cursor={{ fill: "var(--surface-2)" }} />
              {group !== null && <ReferenceLine xAxisId="value" x={group} stroke="var(--text)" strokeDasharray="4 4" />}
              {paired && (
                <Bar xAxisId="count" dataKey="count" name={`Số ${def.label}`} fill="var(--chart-grid)" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  <LabelList dataKey="count" position="right" fontSize={12} fill="var(--muted)" formatter={(v: unknown) => `${num(Number(v), 0)} lỗi`} />
                </Bar>
              )}
              <Bar
                xAxisId="value"
                dataKey="value"
                name={paired ? `% review có ${def.label}` : def.label}
                radius={[0, 4, 4, 0]}
                isAnimationActive={false}
                cursor="pointer"
                onClick={(entry) => {
                  // The bar item carries its data row as `payload` (the member's id).
                  const id = (entry as { payload?: { id?: number } }).payload?.id;
                  if (id !== undefined) onOpen(id);
                }}
              >
                {chart.map((c) => (
                  <Cell key={c.id} fill={barColor(metric, c.row, c.value)} />
                ))}
                <LabelList
                  dataKey="value"
                  position="right"
                  fontSize={12}
                  fill="var(--text)"
                  formatter={(v: unknown) => `${num(Number(v), def.digits)}${def.unit ?? ""}`}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      {missing > 0 && (
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          {missing} người không có số liệu này trong khoảng đã chọn (xem tab Bảng).
        </p>
      )}
    </Card>
  );
}

/** The side panel: who needs help, with the reason, one click away. */
function Attention({ data, onOpen }: { data: TeamReport; onOpen: (userId: number) => void }) {
  const list = data.members.filter((m) => m.assessment.level === "support" || m.assessment.level === "fair").slice(0, 8);
  const s = data.summary;
  const p = data.previous.summary;
  const d = diff(s.avg_score, p.avg_score);
  return (
    <div className="report-side">
      <Card title="Cả nhóm" id="report-group" collapsible={false}>
        <dl className="report-facts">
          <div>
            <dt>Điểm TB</dt>
            <dd>
              <Score value={s.avg_score} strong />
              {d !== null && <span className={`small ${d >= 0 ? "tone-good" : "tone-bad-text"}`}> {d > 0 ? "+" : ""}{num(d, 1)}</span>}
            </dd>
          </div>
          <div>
            <dt>APPROVE</dt>
            <dd>{s.pass_rate === null ? "—" : `${num(s.pass_rate, 0)}%`}</dd>
          </div>
          <div>
            <dt>Review</dt>
            <dd>
              {s.reviews} <span className="muted small">({s.mrs} MR · {s.commits} commit)</span>
            </dd>
          </div>
          <div>
            <dt>Critical</dt>
            <dd>
              <span className={s.critical ? "tone-bad-text" : undefined}>{s.critical}</span>{" "}
              <span className="muted small">· {num(s.critical_rate ?? null, 0)}% review</span>
            </dd>
          </div>
          <div>
            <dt>High</dt>
            <dd>
              {s.high} <span className="muted small">· {num(s.high_rate ?? null, 0)}% review</span>
            </dd>
          </div>
          <div>
            <dt>Lỗi / 100 LOC</dt>
            <dd>{num(s.findings_per_100_loc, 2)}</dd>
          </div>
        </dl>
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Số so với kỳ trước {data.previous.from} → {data.previous.to}.
        </p>
      </Card>
      <Card title="Cần chú ý" id="report-attention" collapsible={false} flush>
        {list.length === 0 ? (
          <p className="muted small adm-pad">Không ai ở mức Cần hỗ trợ hay Cần cải thiện.</p>
        ) : (
          <ul className="report-attention">
            {list.map((m) => (
              <li key={m.user.id}>
                <button type="button" className="link" onClick={() => onOpen(m.user.id)}>
                  {nameOf(m.user)}
                </button>
                <Pill tone={LEVEL_TONE[m.assessment.level]}>{m.assessment.label}</Pill>
                <span className="muted small report-attention-why">{m.assessment.reasons[0]}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** A cell shaded by its value: the heat maps of mistakes and criteria. */
function Heat({ value, tone, children }: { value: number; tone: "bad" | "good" | "warn"; children: ReactNode }) {
  const strength = Math.max(0, Math.min(value, 100));
  return (
    <td className="num heat" style={{ background: `color-mix(in srgb, var(--${tone}) ${Math.round(strength * 0.45)}%, transparent)` }}>
      {children}
    </td>
  );
}

type Tab = "chart" | "table" | "categories" | "criteria";

/**
 * Tổng quan: everyone in one view. A row of level counts, then one panel at a time: the bar
 * chart (one bar per member, the number on it, weakest on top) beside the group's numbers and
 * who needs attention; the sortable table; who makes which mistake; who is weak where under a
 * criteria set. Clicking a name or a bar opens that member's Chi tiết.
 */
export function TeamReportView({
  data,
  onOpen,
  setId,
  onSetId,
}: {
  data: TeamReport;
  onOpen: (userId: number) => void;
  setId: number | null;
  onSetId: (id: number) => void;
}) {
  const [tab, setTab] = useState<Tab>("chart");
  const tabs: SubTab<Tab>[] = [
    { key: "chart", label: "Biểu đồ", hint: "Mỗi người một thanh, số trên thanh" },
    { key: "table", label: "Bảng", count: data.members.length, hint: "Mỗi người một dòng, sắp được theo mọi cột" },
    { key: "categories", label: "Loại lỗi", hint: "Ai hay mắc loại lỗi nào, so với cả nhóm" },
    { key: "criteria", label: "Theo tiêu chí", count: data.criteria_sets.length || undefined, hint: "Điểm từng thành phần của từng người" },
  ];
  if (!data.members.length) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="members" title="Không có ai trong phạm vi này">
          Đổi dự án, team hoặc khoảng thời gian; hoặc chọn người cần xem.
        </EmptyState>
      </Card>
    );
  }
  return (
    <>
      <div className="team-levels" aria-label="Số người theo mức đánh giá">
        {LEVEL_ORDER.map((k) => (
          <div key={k} className={`card team-level tone-${LEVEL_TONE[k] ?? "none"}`} title={k === "good" ? "Điểm TB ≥ 80, không critical, ≥ 60% APPROVE" : k === "support" ? "Có critical hoặc điểm TB dưới 65" : undefined}>
            <span className="team-level-n">{data.levels[k] ?? 0}</span>
            <span className="muted small">{LEVEL_LABEL[k]}</span>
          </div>
        ))}
      </div>
      <SubTabs<Tab> id="ov-tabs" label="Phần của tổng quan" variant="mini" tabs={tabs} value={tab} onChange={setTab} />
      {tab === "chart" && (
        <SubPanel id="ov-tabs" tab="chart">
          <div className="report-split">
            <MemberChart data={data} onOpen={onOpen} />
            <Attention data={data} onOpen={onOpen} />
          </div>
        </SubPanel>
      )}
      {tab === "table" && (
        <SubPanel id="ov-tabs" tab="table">
          <Compare data={data} onOpen={onOpen} />
        </SubPanel>
      )}
      {tab === "categories" && (
        <SubPanel id="ov-tabs" tab="categories">
          <Categories data={data} onOpen={onOpen} />
        </SubPanel>
      )}
      {tab === "criteria" && (
        <SubPanel id="ov-tabs" tab="criteria">
          <Criteria data={data} onOpen={onOpen} setId={setId} onSetId={onSetId} />
        </SubPanel>
      )}
    </>
  );
}

function PersonButton({ person, onOpen }: { person: Person; onOpen: (id: number) => void }) {
  return (
    <button type="button" className="link" onClick={() => onOpen(person.id)} title="Mở báo cáo chi tiết">
      {nameOf(person)}
    </button>
  );
}

function Compare({ data, onOpen }: { data: TeamReport; onOpen: (id: number) => void }) {
  const cell = (r: TeamReportRow, key: string): number | string | null => {
    switch (key) {
      case "name":
        return nameOf(r.user);
      case "level":
        return LEVEL_ORDER.indexOf(r.assessment.level);
      case "reviews":
        return r.reviews;
      case "score":
        return r.avg_score;
      case "delta":
        return diff(r.avg_score, r.previous_avg_score);
      case "pass":
        return r.pass_rate;
      case "critical":
        return r.critical;
      case "critical_rate":
        return r.critical_rate ?? null;
      case "high":
        return r.high;
      case "high_rate":
        return r.high_rate ?? null;
      case "density":
        return r.findings_per_100_loc;
      default:
        return null;
    }
  };
  const { sorted, sort, toggle } = useSort(data.members, { key: "level", dir: "asc" }, cell);
  return (
    <Card title="So sánh thành viên" id="team-compare" flush collapsible={false} subtitle="Rê chuột vào mức để xem lý do; bấm tên để xem chi tiết">
      <div className="table-wrap report-table">
        <table>
          <thead>
            <tr>
              <SortTh id="name" sort={sort} onSort={toggle}>Thành viên</SortTh>
              <SortTh id="level" sort={sort} onSort={toggle}>Mức</SortTh>
              <SortTh id="reviews" sort={sort} onSort={toggle} num>Review</SortTh>
              <SortTh id="score" sort={sort} onSort={toggle} num>Điểm TB</SortTh>
              <SortTh id="delta" sort={sort} onSort={toggle} num>So kỳ trước</SortTh>
              <SortTh id="pass" sort={sort} onSort={toggle} num>APPROVE</SortTh>
              <SortTh id="critical" sort={sort} onSort={toggle} num>Critical</SortTh>
              <SortTh id="critical_rate" sort={sort} onSort={toggle} num>% review có critical</SortTh>
              <SortTh id="high" sort={sort} onSort={toggle} num>High</SortTh>
              <SortTh id="high_rate" sort={sort} onSort={toggle} num>% review có high</SortTh>
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
                    <PersonButton person={r.user} onOpen={onOpen} />
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
                    <span className={r.critical ? "tone-bad-text" : undefined}>{r.critical}</span>
                  </td>
                  <td className="num">{r.reviews ? `${num(r.critical_rate ?? null, 0)}%` : "—"}</td>
                  <td className="num">{r.high}</td>
                  <td className="num">{r.reviews ? `${num(r.high_rate ?? null, 0)}%` : "—"}</td>
                  <td className="num">{num(r.findings_per_100_loc, 2)}</td>
                  <td className="small">{r.top_category ?? <span className="faint">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Categories({ data, onOpen }: { data: TeamReport; onOpen: (id: number) => void }) {
  const cats = data.categories;
  const rows = data.members.filter((m) => m.findings > 0);
  if (!cats.length) {
    return (
      <Card collapsible={false}>
        <EmptyState icon="checkCircle" title="Không có finding nào được tính điểm">Trong khoảng này không có lỗi nào được tính.</EmptyState>
      </Card>
    );
  }
  return (
    <Card
      title="Ai hay mắc loại lỗi nào"
      id="team-categories"
      flush
      collapsible={false}
      subtitle="Mỗi ô: tỉ trọng loại lỗi đó trong lỗi của người đó. Ô đậm hơn hẳn dòng Cả nhóm = điểm cần kèm riêng."
    >
      <div className="table-wrap report-table">
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
                  <PersonButton person={m.user} onOpen={onOpen} /> <span className="muted small">{m.findings} lỗi</span>
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
  onOpen,
  setId,
  onSetId,
}: {
  data: TeamReport;
  onOpen: (id: number) => void;
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
      collapsible={false}
      subtitle="Mỗi ô: điểm TB (theo LOC) của thành phần. Xanh ≥ 80, vàng 65–80, đỏ dưới 65."
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
      <div className="table-wrap report-table">
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
                  <PersonButton person={m.user} onOpen={onOpen} />
                </td>
                <td className="num">{m.criteria?.reviews}</td>
                <td className="num">
                  <Score value={m.criteria?.score ?? null} strong />
                </td>
                {crit.components.map((c) => {
                  const v = m.criteria?.components[c.key] ?? null;
                  const tone = scoreTone(v);
                  return v === null ? (
                    <td key={c.key} className="num faint">
                      —
                    </td>
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
