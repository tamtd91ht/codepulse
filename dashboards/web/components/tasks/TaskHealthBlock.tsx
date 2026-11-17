"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { rememberTask } from "@/components/paletteRecent";
import { useApi } from "@/lib/api";
import { dateTimeMin } from "@/lib/format";
import type { TaskCoverCase, TaskCriterion, TaskDetail, TaskReviewRef } from "@/lib/types";

import { spaceOf, today } from "./taskParts";

/*
 * Khối Sức khoẻ (docs/specs/31 §21.1, roadmap đợt 3 §3.1), always under the title of a task:
 * - one overall colour: red when blocked or open critical findings, amber when something else needs
 *   a look (DoD missing, testcase Failed, pipeline failed, MR closed without merge, overdue), green
 *   otherwise;
 * - three rows, items that do not apply hidden: what blocks it; its code (each MR to GitLab, the
 *   review to its findings, pipeline, deploy); its quality (AC with testcase, testcase results to
 *   their run, DoD with "Xem thiếu gì" inline);
 * - nothing applies: one muted line.
 * The Board card shows the most important of the same signals (taskSignal.ts).
 */

type Coverage = {
  total: number;
  passed: number;
  failed: number;
  hidden?: boolean;
  cases?: TaskCoverCase[];
  criteria?: TaskCriterion[];
};

type Tone = "good" | "bad" | "warn" | "muted";
type Item = { key: string; text: string; tone: Tone; title: string } & ({ href: string; external?: boolean } | { onClick: () => void } | { static: true });

const MR_GLYPH: Record<string, string> = { merged: "✓ đã merge", opened: "đang mở", closed: "✕ đã đóng" };
const SHOWN_MRS = 3;

function jump(id: string) {
  const el = document.getElementById(id);
  (el?.closest("section") ?? el)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** The review worth opening: the one with the most open critical findings, else the most high,
 * else an MR's review, else a commit's. */
function pickReview(task: TaskDetail): TaskReviewRef | null {
  const all = [...(task.git?.mrs ?? []).map((m) => m.review), ...(task.git?.commits ?? []).map((c) => c.review)].filter((r): r is TaskReviewRef => !!r);
  if (!all.length) return null;
  return [...all].sort((a, b) => b.open_critical - a.open_critical || b.open_high - a.open_high)[0];
}

export function TaskHealth({ task, onOpenTask }: { task: TaskDetail; onOpenTask: (key: string) => void }) {
  const tests = useApi<Coverage>(`/tasks/${encodeURIComponent(task.key)}/tests`);
  const [showMissing, setShowMissing] = useState(false);
  const bp = spaceOf(task);

  // The palette's "recent tasks" (§21.1): every task opened, wherever it was opened from.
  useEffect(() => {
    rememberTask({ key: task.key, title: task.title });
  }, [task.key, task.title]);

  const g = task.git?.summary;
  const mrs = task.git?.mrs ?? [];
  const hasCode = !!g && (g.mrs > 0 || g.commits > 0);
  const score = !g || g.avg_score === null || g.avg_score === undefined ? null : Number(g.avg_score);
  const closedMr = mrs.length > 0 && mrs.every((m) => m.state === "closed");
  const critical = g?.open_critical ?? 0;
  const review = pickReview(task);
  const blockedBy = task.blocked_by ?? [];
  const blocked = blockedBy.length > 0 || !!task.blocked;
  const done = task.status.category === "done";
  const overdue = !done && !!task.due_date && task.due_date < today();
  const dod = task.dod ?? [];
  const missing = dod.filter((d) => !d.ok);
  const cov = tests.data && !tests.data.hidden ? tests.data : null;
  const t = cov && cov.total > 0 ? cov : null;
  const criteria = cov?.criteria ?? [];
  const covered = criteria.filter((c) => c.cases.length > 0).length;

  const blockRow: Item[] = blockedBy.length
    ? blockedBy.map((b) => ({ key: `blocked-${b.key}`, text: `⛔ Bị chặn bởi ${b.key}${b.status ? ` (${b.status})` : ""}`, tone: "bad", title: `Mở ${b.key}: ${b.title}`, onClick: () => onOpenTask(b.key) }))
    : task.blocked && !done
      ? [{ key: "blocked", text: "⛔ Bị chặn", tone: "bad", title: "Một task bạn không xem được đang chặn task này", static: true }]
      : [];

  const codeRow: Item[] = [];
  if (hasCode && g) {
    for (const m of mrs.slice(0, SHOWN_MRS)) {
      const text = `MR !${m.mr_iid} ${MR_GLYPH[m.state ?? ""] ?? m.state ?? ""}`.trim();
      const tone: Tone = m.state === "merged" ? "good" : m.state === "closed" ? "warn" : "muted";
      const title = `${m.repo ?? ""} !${m.mr_iid}${m.title ? `: ${m.title}` : ""}`;
      codeRow.push(m.web_url ? { key: `mr-${m.id}`, text, tone, title: `${title} (mở GitLab)`, href: m.web_url, external: true } : { key: `mr-${m.id}`, text, tone, title, onClick: () => jump("tk-code-title") });
    }
    if (mrs.length > SHOWN_MRS) codeRow.push({ key: "mr-more", text: `+${mrs.length - SHOWN_MRS} MR`, tone: "muted", title: "Xem mọi MR ở phần Code", onClick: () => jump("tk-code-title") });
    if (!mrs.length) codeRow.push({ key: "commits", text: `${g.commits} commit`, tone: "muted", title: "Commit nối với task", onClick: () => jump("tk-code-title") });
    if (review) {
      const href = `/reviews/${review.id}`;
      if (score !== null && !Number.isNaN(score))
        codeRow.push({ key: "score", text: `Review ${score.toFixed(1)}`, tone: score >= 7 ? "good" : "warn", title: "Điểm review trung bình; mở review", href });
      codeRow.push({ key: "crit", text: `${critical} critical`, tone: critical ? "bad" : "good", title: critical ? "Mở review có finding critical" : "Không còn finding critical mở", href });
      if (g.open_high) codeRow.push({ key: "high", text: `${g.open_high} high`, tone: "warn", title: "Finding high còn mở; mở review", href });
    } else if (critical) {
      codeRow.push({ key: "crit", text: `${critical} critical`, tone: "bad", title: "Finding critical còn mở", onClick: () => jump("tk-code-title") });
    }
    if (mrs.length) {
      const failed = mrs.find((m) => m.pipeline?.status === "failed");
      const any = failed ?? mrs.find((m) => m.pipeline?.web_url);
      const text = g.pipelines_failed ? `${g.pipelines_failed} pipeline lỗi` : "Pipeline ✓";
      const tone: Tone = g.pipelines_failed ? "bad" : "good";
      codeRow.push(any?.pipeline?.web_url ? { key: "pipe", text, tone, title: "Mở pipeline trên GitLab", href: any.pipeline.web_url, external: true } : { key: "pipe", text, tone, title: "Pipeline của MR", onClick: () => jump("tk-code-title") });
    }
    codeRow.push({
      key: "deploy",
      text: g.deployed ? `Đã lên ${g.deployed.environment}` : "Chưa deploy",
      tone: g.deployed ? "good" : "muted",
      title: g.deployed ? `Deploy lúc ${dateTimeMin(g.deployed.at)}` : "Chưa thấy deploy production",
      onClick: () => jump("tk-code-title"),
    });
    if (closedMr) codeRow.push({ key: "mr-closed", text: "🔁 MR bị đóng không merge", tone: "warn", title: "Mọi MR của task đã đóng mà không merge", onClick: () => jump("tk-code-title") });
  }

  const qualityRow: Item[] = [];
  if (overdue) qualityRow.push({ key: "due", text: `Quá hạn ${task.due_date}`, tone: "warn", title: "Đã qua ngày hạn; xem trường Hạn", onClick: () => document.querySelector(".tk-detail-side")?.scrollIntoView({ behavior: "smooth", block: "start" }) });
  if (criteria.length)
    qualityRow.push({
      key: "ac",
      text: `AC ${covered}/${criteria.length} có testcase`,
      tone: covered === criteria.length ? "good" : "warn",
      title: "Tiêu chí chấp nhận có testcase gắn vào",
      onClick: () => jump("qa-criteria-title"),
    });
  if (t) {
    const cases = t.cases ?? [];
    const target = cases.find((c) => c.status === "failed" && c.run_id) ?? cases.find((c) => c.run_id);
    const text = `Testcase ${t.passed}/${t.total} Passed`;
    const tone: Tone = t.failed ? "bad" : t.passed === t.total ? "good" : "muted";
    const title = t.failed ? `${t.failed} testcase Failed; mở test run` : "Mở test run gần nhất";
    qualityRow.push(
      target?.run_id && bp
        ? { key: "tests", text, tone, title, href: `/testcases?project=${bp}&tab=runs&run=${target.run_id}${target.test_id ? `&test=${target.test_id}` : ""}` }
        : { key: "tests", text, tone, title: "Testcase phủ task", onClick: () => jump("tk-tests-title") },
    );
  }
  if (dod.length)
    qualityRow.push({
      key: "dod",
      text: missing.length ? `DoD ${dod.length - missing.length}/${dod.length} · ${showMissing ? "Ẩn" : "Xem thiếu gì"}` : `DoD ${dod.length}/${dod.length}`,
      tone: missing.length ? "warn" : "good",
      title: missing.length ? `Definition of Done${task.dod_enforced ? " (bắt buộc trước khi Xong)" : ""}: còn thiếu ${missing.length}` : "Đủ Definition of Done",
      onClick: () => (missing.length ? setShowMissing((v) => !v) : jump("tk-dod-title")),
    });

  const rows = [blockRow, codeRow, qualityRow].filter((r) => r.length);
  const red = (blocked && !done) || critical > 0;
  const amber = missing.length > 0 || !!t?.failed || !!g?.pipelines_failed || closedMr || overdue;
  const tone = red ? "red" : amber ? "amber" : "green";
  const state = red ? "Có vấn đề" : amber ? "Cần chú ý" : "Ổn";

  if (!rows.length)
    return (
      <div className="hb-block hb-none" role="group" aria-label="Sức khoẻ task">
        <p className="small muted hb-empty">Chưa có code, DoD hay testcase cho task này</p>
      </div>
    );

  return (
    <div className={`hb-block hb-${tone}`} role="group" aria-label={`Sức khoẻ task: ${state}`}>
      <div className="hb-head">
        <span className="hb-dot" aria-hidden />
        <strong>Sức khoẻ</strong>
        <span className="hb-state small">{state}</span>
      </div>
      {rows.map((row, i) => (
        <div key={i} className="hb-row">
          {row.map((it) => (
            <HealthItem key={it.key} item={it} expanded={it.key === "dod" && missing.length > 0 ? showMissing : undefined} />
          ))}
        </div>
      ))}
      {showMissing && missing.length > 0 && (
        <ul className="hb-missing small" aria-label="DoD còn thiếu">
          {missing.map((d) => (
            <li key={d.key}>
              <span aria-hidden>✕</span> <strong>{d.label}</strong>
              {d.detail && <span className="muted"> · {d.detail}</span>}
            </li>
          ))}
          {task.dod_enforced && <li className="muted">Dự án bắt buộc đủ DoD trước khi chuyển sang Xong.</li>}
        </ul>
      )}
    </div>
  );
}

function HealthItem({ item, expanded }: { item: Item; expanded?: boolean }): ReactNode {
  const cls = `tk-health-item tk-health-${item.tone} hb-item`;
  if ("href" in item)
    return item.external ? (
      <a className={cls} href={item.href} target="_blank" rel="noopener noreferrer" title={item.title}>
        {item.text}
      </a>
    ) : (
      <Link className={cls} href={item.href} title={item.title}>
        {item.text}
      </Link>
    );
  if ("onClick" in item)
    return (
      <button type="button" className={cls} title={item.title} onClick={item.onClick} aria-expanded={expanded}>
        {item.text}
      </button>
    );
  return (
    <span className={cls} title={item.title}>
      {item.text}
    </span>
  );
}
