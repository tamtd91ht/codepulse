"use client";

import Link from "next/link";
import { useState } from "react";

import { Loading, SeverityBadge } from "@/components/ui";
import { qs, useApi } from "@/lib/api";
import type { BoardFinding, Page, Team } from "@/lib/types";

/** Every open critical/high finding (docs/specs/10 "Critical Board"). */
export default function CriticalBoard() {
  const [team, setTeam] = useState("");
  const [person, setPerson] = useState("");
  const [project, setProject] = useState("");
  const { data: teams } = useApi<Team[]>("/teams/visible");
  const { data, error } = useApi<Page<BoardFinding>>(
    `/findings${qs({ team_id: team, limit: 200 })}&severity=critical&severity=high`,
  );

  const rows = (data?.items ?? [])
    .filter((f) => !person || String(f.author_user_id) === person)
    .filter((f) => !project || f.project === project)
    .sort((a, b) => b.age_days - a.age_days);
  const people = [...new Map((data?.items ?? []).map((f) => [f.author_user_id, f.author])).entries()];
  const projects = [...new Set((data?.items ?? []).map((f) => f.project))].sort();

  return (
    <>
      <h1>Critical Board</h1>
      <p className="muted">Finding critical/high đang mở, lâu nhất trước.</p>
      <div className="toolbar">
        <select value={team} onChange={(e) => setTeam(e.target.value)}>
          <option value="">Mọi team</option>
          {teams?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">Mọi repo</option>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={person} onChange={(e) => setPerson(e.target.value)}>
          <option value="">Mọi người</option>
          {people.map(([pid, name]) => <option key={String(pid)} value={String(pid)}>{name ?? "—"}</option>)}
        </select>
      </div>
      {!data ? (
        <Loading error={error} />
      ) : rows.length === 0 ? (
        <p className="muted">Không có finding critical/high nào đang mở. 🎉</p>
      ) : (
        <div className="card table-wrap">
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
                  <td><SeverityBadge severity={f.severity} /></td>
                  <td><Link href={`/reviews/${f.review_id}`}>{f.title}</Link></td>
                  <td><code>{f.project}/{f.file_path}:{f.line_start}</code></td>
                  <td>{f.author ?? "—"}</td>
                  <td>{f.web_url ? <a href={f.web_url} target="_blank" rel="noreferrer">{f.target} ↗</a> : f.target}</td>
                  <td className="num">{f.age_days} ngày</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
