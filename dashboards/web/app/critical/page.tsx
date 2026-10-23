"use client";

import Link from "next/link";
import { useState } from "react";

import { Card, EmptyState, ExternalLink, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import type { BoardFinding, Page, Team } from "@/lib/types";

/** Every open critical/high finding (docs/specs/10 "Critical Board"). */
export default function CriticalBoard() {
  const [team, setTeam] = useState("");
  const [person, setPerson] = useState("");
  const [project, setProject] = useState("");
  const { data: teams } = useApi<Team[]>("/teams/visible");
  const { data, error } = useApi<Page<BoardFinding>>(`/findings${qs({ team_id: team, limit: 200 })}&severity=critical&severity=high`, {
    keepPreviousData: true,
  });

  const rows = (data?.items ?? [])
    .filter((f) => !person || String(f.author_user_id) === person)
    .filter((f) => !project || f.project === project)
    .sort((a, b) => b.age_days - a.age_days);
  const people = [
    ...new Map((data?.items ?? []).filter((f) => f.author_user_id !== null).map((f) => [f.author_user_id, f.author])).entries(),
  ];
  const projects = [...new Set((data?.items ?? []).map((f) => f.project))].sort();
  const critical = rows.filter((f) => f.severity === "critical").length;
  const filtering = !!(team || person || project);

  return (
    <>
      <PageHeader
        eyebrow="Critical board"
        title="Finding critical / high đang mở"
        subtitle="Lâu nhất xếp trước. Bấm vào vấn đề để xem review và trích đoạn code."
        actions={
          data && (
            <>
              <Pill tone={critical ? "bad" : "good"}>{critical} critical</Pill>
              <Pill tone={rows.length - critical ? "warn" : "good"}>{rows.length - critical} high</Pill>
            </>
          )
        }
      />
      <div className="toolbar">
        <select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Lọc theo team">
          <option value="">Mọi team</option>
          {teams?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={project} onChange={(e) => setProject(e.target.value)} aria-label="Lọc theo repo">
          <option value="">Mọi repo</option>
          {projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <select value={person} onChange={(e) => setPerson(e.target.value)} aria-label="Lọc theo người">
          <option value="">Mọi người</option>
          {people.map(([pid, name]) => (
            <option key={String(pid)} value={String(pid)}>
              {name ?? "—"}
            </option>
          ))}
        </select>
        {filtering && (
          <button
            className="ghost"
            onClick={() => {
              setTeam("");
              setPerson("");
              setProject("");
            }}
          >
            Bỏ lọc
          </button>
        )}
      </div>

      {!data ? (
        <Loading error={error} rows={5} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState icon="checkCircle" tone="good" title="Không có finding critical/high nào đang mở">
            {filtering ? "Thử bỏ bớt bộ lọc." : "Mọi vấn đề nghiêm trọng đã được xử lý hoặc khiếu nại."}
          </EmptyState>
        </Card>
      ) : (
        <Card flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Mức</th>
                  <th>Vấn đề</th>
                  <th>Vị trí</th>
                  <th>Người</th>
                  <th>Thay đổi</th>
                  <th className="num">Tồn tại</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td style={{ whiteSpace: "normal", minWidth: 240 }}>
                      <Link href={`/reviews/${f.review_id}`}>{f.title}</Link>
                    </td>
                    <td>
                      <code>
                        {f.project}/{f.file_path}:{f.line_start}
                      </code>
                    </td>
                    <td>{f.author_user_id ? <Link href={`/members/${f.author_user_id}`}>{f.author ?? "—"}</Link> : (f.author ?? "—")}</td>
                    <td>{f.web_url ? <ExternalLink href={f.web_url}>{f.target}</ExternalLink> : f.target}</td>
                    <td className={`num${f.age_days >= 14 ? " bad" : ""}`}>{f.age_days} ngày</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );
}
