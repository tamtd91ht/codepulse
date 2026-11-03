"use client";

import { useId, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { SubTabs } from "@/components/AdminTabs";
import { InfoTip, ScoreBar, Swatch } from "@/components/CriteriaParts";
import { Icon } from "@/components/icons";
import { Card, ChartTip, Pill, SeverityBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import { score1, toNum } from "@/lib/criteria";
import { localDay, shiftDays, weekLabel } from "@/lib/format";
import type { CriteriaBreakdown, CriteriaSummary, ReviewCriteriaScore, Severity } from "@/lib/types";

export interface FindingRef {
  title: string | null;
  severity: Severity;
  category?: string;
}

/** The text of the (i) next to a criteria score: how it is computed and capped (docs/specs/22 §2). */
export function FormulaHelp({ capped }: { capped?: boolean }) {
  return (
    <InfoTip label="Cách tính điểm theo tiêu chí">
      <strong>Cách tính</strong>
      <span>
        Mỗi finding thuộc một thành phần: thành phần AI chọn khi review, nếu không thì thành phần chứa loại của finding.
      </span>
      <span>
        Điểm thành phần dùng đúng công thức điểm review (mức độ, cỡ thay đổi, kiểm chứng) trên các finding của nó; không có
        finding thì được 100.
      </span>
      <span>
        <b>Điểm cuối = Σ (trọng số × điểm thành phần) / 100.</b>
      </span>
      <span>
        Trần: khi bộ bật trần, có critical thì tối đa 50, có high thì tối đa 80, như điểm review.
        {capped ? " Điểm này đã bị chặn trần." : ""}
      </span>
    </InfoTip>
  );
}

/** One row per component: weight, score bar, findings; a row opens the findings it holds. */
export function Breakdown({
  breakdown,
  finding,
  onJump,
}: {
  breakdown: CriteriaBreakdown;
  finding: (id: number) => FindingRef | null;
  /** Scroll to the finding on the page (the review page). */
  onJump?: (id: number) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const base = useId();
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <ul className="crit-bd">
      {breakdown.components.map((c, i) => {
        const panel = `${base}-${c.key}`;
        const expanded = open.has(c.key);
        const score = toNum(c.score);
        return (
          <li key={c.key} className={expanded ? "open" : undefined}>
            <button
              type="button"
              className="crit-bd-head"
              aria-expanded={expanded}
              aria-controls={panel}
              onClick={() => toggle(c.key)}
            >
              <Icon name="chevronRight" size={14} className="crit-bd-chev" />
              <Swatch index={i} />
              <span className="crit-bd-name">{c.name}</span>
              <span className="crit-bd-weight" title="Trọng số">
                {c.weight}%
              </span>
              <ScoreBar value={score} label={`Điểm ${c.name}`} />
              <span className="crit-bd-score">{score1(score)}</span>
              <span className={`crit-bd-count${c.findings.length ? "" : " none"}`} title="Số finding thuộc thành phần">
                {c.findings.length} finding
              </span>
            </button>
            {expanded && (
              <div id={panel} className="crit-bd-panel">
                {c.findings.length === 0 ? (
                  <p className="muted small">Không có finding nào thuộc thành phần này: 100 điểm.</p>
                ) : (
                  <>
                    {c.penalty > 0 && (
                      <p className="muted small">
                        Bị trừ {score1(c.penalty)} điểm phạt trước khi tính theo cỡ thay đổi.
                      </p>
                    )}
                    <ul className="crit-bd-findings">
                      {c.findings.map((f) => {
                        const info = finding(f.id);
                        return (
                          <li key={f.id}>
                            {info && <SeverityBadge severity={info.severity} />}
                            {onJump ? (
                              <button type="button" className="link" onClick={() => onJump(f.id)}>
                                {info?.title ?? `Finding #${f.id}`}
                              </button>
                            ) : (
                              <span>{info?.title ?? `Finding #${f.id}`}</span>
                            )}
                            <span
                              className={`crit-src ${f.source}`}
                              title={
                                f.source === "ai"
                                  ? "AI xếp finding vào thành phần này khi review"
                                  : "Xếp theo loại finding (AI không chọn thành phần)"
                              }
                            >
                              {f.source === "ai" ? "AI xếp" : "Theo loại"}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Scrolls to a finding of the review page and flashes it. */
export function jumpToFinding(id: number) {
  const el = document.getElementById(`finding-${id}`);
  if (!el) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
  el.focus({ preventScroll: true });
  el.classList.remove("flash");
  // Restart the animation when the same finding is picked twice.
  void el.offsetWidth;
  el.classList.add("flash");
  window.setTimeout(() => el.classList.remove("flash"), 2400);
}

/**
 * Review page: "Điểm theo tiêu chí" (docs/specs/22 §7.4). The primary set first, the others in
 * small tabs; nothing at all when the dự án has no set.
 */
export function ReviewCriteriaCard({
  scores,
  findings,
}: {
  scores: ReviewCriteriaScore[] | undefined;
  findings: { id: number; title: string | null; severity: Severity; category: string }[];
}) {
  const sorted = useMemo(
    () => [...(scores ?? [])].sort((a, b) => Number(b.is_primary) - Number(a.is_primary)),
    [scores],
  );
  const [picked, setPicked] = useState<string | null>(null);
  const byId = useMemo(() => new Map(findings.map((f) => [f.id, f])), [findings]);
  if (!sorted.length) return null;
  const current = sorted.find((s) => String(s.set_id) === picked) ?? sorted[0];
  const score = toNum(current.score);
  const tone = score === null ? "" : score >= 80 ? "good" : score >= 65 ? "warn" : "bad";
  return (
    <Card
      title="Điểm theo tiêu chí"
      subtitle="Điểm của thay đổi này theo bộ tiêu chí của dự án; điểm 0–100 và kết luận merge ở trên không đổi"
      id="review-criteria"
    >
      {sorted.length > 1 && (
        <SubTabs
          id="crit-review"
          label="Bộ tiêu chí"
          variant="mini"
          tabs={sorted.map((s) => ({
            key: String(s.set_id),
            label: (
              <>
                {s.set_name}
                {s.is_primary && <span className="crit-primary-dot" title="Bộ chính" aria-label="(bộ chính)" />}
              </>
            ),
          }))}
          value={String(current.set_id)}
          onChange={setPicked}
        />
      )}
      <div role={sorted.length > 1 ? "tabpanel" : undefined} id={`crit-review-panel-${current.set_id}`} aria-labelledby={sorted.length > 1 ? `crit-review-tab-${current.set_id}` : undefined}>
        <div className="crit-head-score">
          <span className={`crit-big ${tone}`}>
            {score1(score)}
            <span className="unit">/ 100</span>
          </span>
          <span className="crit-head-meta">
            <strong>{current.set_name}</strong>
            <span className="muted small">
              phiên bản {current.version}
              {current.is_primary ? " · bộ chính" : ""}
            </span>
          </span>
          {current.breakdown.capped && (
            <Pill tone="warn" icon="alert" title="Có finding critical/high: điểm cuối bị chặn trần">
              Bị chặn trần
            </Pill>
          )}
          <FormulaHelp capped={current.breakdown.capped} />
        </div>
        <Breakdown
          key={current.set_id}
          breakdown={current.breakdown}
          finding={(id) => byId.get(id) ?? null}
          onJump={jumpToFinding}
        />
      </div>
    </Card>
  );
}

/**
 * Member page and dự án overview: "Theo tiêu chí" over the last 12 weeks to `to` (docs/specs/22
 * §7.4). Hidden while loading and when the scope has no set; `onConfigure` (a manager who can
 * assign sets) shows a short prompt instead of hiding.
 */
export function CriteriaSummaryBlock({
  scope,
  to,
  onConfigure,
}: {
  scope: { business_project_id?: number; user_id?: number };
  /** Last day of the window (YYYY-MM-DD); today when absent. */
  to?: string;
  onConfigure?: () => void;
}) {
  const [setId, setSetId] = useState<number | null>(null);
  const end = to ?? localDay();
  const from = shiftDays(end, -83);
  const { data, error } = useApi<CriteriaSummary>(
    `/criteria/summary${qs({ ...scope, from, to: end, set_id: setId })}`,
    { keepPreviousData: true },
  );
  const configure = onConfigure && (
    <button type="button" className="sm" onClick={onConfigure}>
      <Icon name="settings" size={14} />
      Bộ tiêu chí
    </button>
  );
  if (error || !data) return null;
  if (!data.set_id || !data.components?.length) {
    if (!onConfigure) return null;
    return (
      <Card title="Theo tiêu chí" id="criteria-summary" actions={configure}>
        <p className="muted small" style={{ margin: 0 }}>
          Dự án chưa có bộ tiêu chí. Gán một bộ (ví dụ &ldquo;Chuẩn chung&rdquo;) để chấm mỗi thay đổi theo các thành phần
          có trọng số; review cũ được tính lại trong nền.
        </p>
      </Card>
    );
  }
  const sets = data.sets ?? [];
  const final = toNum(data.score);
  const tone = final === null ? "" : final >= 80 ? "good" : final >= 65 ? "warn" : "bad";
  const weeks = (data.weeks ?? []).map((w) => ({ week: weekLabel(w.week_start), score: toNum(w.score), reviews: w.reviews }));
  return (
    <Card
      title="Theo tiêu chí"
      id="criteria-summary"
      subtitle={`${data.set_name ?? "Bộ chính"} · ${data.reviews.toLocaleString("vi-VN")} review trong 12 tuần · trung bình theo LOC`}
      actions={
        <>
          {sets.length > 1 && (
            <select
              className="compact"
              value={data.set_id ?? ""}
              aria-label="Chọn bộ tiêu chí"
              onChange={(e) => setSetId(Number(e.target.value))}
            >
              {sets.map((s) => (
                <option key={s.set_id} value={s.set_id}>
                  {s.name}
                  {s.is_primary ? " (bộ chính)" : ""}
                </option>
              ))}
            </select>
          )}
          {configure}
        </>
      }
    >
      {data.reviews === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          Chưa có review nào trong 12 tuần qua được chấm theo bộ này.
        </p>
      ) : (
        <div className="crit-sum">
          <div>
            <div className="crit-head-score">
              <span className={`crit-big ${tone}`}>
                {score1(final)}
                <span className="unit">/ 100</span>
              </span>
              <span className="muted small">Điểm cuối trung bình</span>
              <FormulaHelp />
            </div>
            <ul className="crit-sum-list" aria-label="Điểm trung bình từng thành phần">
              {data.components.map((c, i) => {
                const v = toNum(c.score);
                return (
                  <li key={c.key}>
                    <span className="crit-sum-name">
                      <Swatch index={i} />
                      {c.name}
                      <span className="muted small">{c.weight}%</span>
                    </span>
                    <span className="crit-sum-score">{score1(v)}</span>
                    <ScoreBar value={v} label={`Điểm trung bình ${c.name}`} />
                  </li>
                );
              })}
            </ul>
          </div>
          <div className="crit-sum-trend">
            <div className="muted small">Điểm cuối theo tuần</div>
            {weeks.filter((w) => w.score !== null).length < 2 ? (
              <p className="muted small">Cần ít nhất hai tuần có review để vẽ xu hướng.</p>
            ) : (
              <div className="chart-box crit-chart" role="img" aria-label={`Điểm cuối theo tuần: ${weeks.map((w) => `${w.week} ${score1(w.score)}`).join(", ")}`}>
                <ResponsiveContainer>
                  <LineChart data={weeks} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                    <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                    <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={12} />
                    <YAxis domain={[0, 100]} tickLine={false} axisLine={false} fontSize={12} />
                    <Tooltip content={<ChartTip />} cursor={{ stroke: "var(--border-strong)" }} />
                    <Line
                      dataKey="score"
                      name="Điểm cuối"
                      stroke="var(--chart-1)"
                      strokeWidth={2.5}
                      dot={{ r: 2 }}
                      activeDot={{ r: 4 }}
                      connectNulls
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
