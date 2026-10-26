"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, EmptyState, ExternalLink, Loading, PageHeader, Pill, SeverityBadge } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import type { BoardFinding, Me, Page, Team } from "@/lib/types";

type Level = "" | "critical" | "high";

/**
 * What a dashboard count links here with (lib/api boardLink): one severity, a dự án, repo, team
 * or person, and for "new in the period" counts the period's [from, to) with status=any (dự án
 * numbers) or status=counted (team and person numbers).
 */
interface Scope {
  severity: Level;
  bp: string;
  bpName: string;
  repoId: string;
  repo: string;
  team: string;
  teamName: string;
  userId: string;
  userName: string;
  status: string;
  from: string;
  to: string;
  period: string;
}

const NO_SCOPE: Scope = {
  severity: "",
  bp: "",
  bpName: "",
  repoId: "",
  repo: "",
  team: "",
  teamName: "",
  userId: "",
  userName: "",
  status: "",
  from: "",
  to: "",
  period: "",
};

function readScope(): Scope {
  const p = new URLSearchParams(window.location.search);
  const severity = p.get("severity");
  const status = p.get("status");
  return {
    severity: severity === "critical" || severity === "high" ? severity : "",
    bp: p.get("bp") ?? "",
    bpName: p.get("bp_name") ?? "",
    repoId: p.get("repo_id") ?? "",
    repo: p.get("repo") ?? "",
    team: p.get("team") ?? "",
    teamName: p.get("team_name") ?? "",
    userId: p.get("user") ?? "",
    userName: p.get("user_name") ?? "",
    status: status === "any" || status === "counted" ? status : "",
    from: p.get("from") ?? "",
    to: p.get("to") ?? "",
    period: p.get("period") ?? "",
  };
}

const STATUS_LABEL: Record<string, string> = {
  fixed: "Đã sửa",
  disputed: "Đang khiếu nại",
  accepted_risk: "Chấp nhận rủi ro",
  false_positive: "Không phải lỗi",
};

/** Every open critical/high finding (docs/specs/10 "Critical Board"). */
export default function CriticalBoard() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [team, setTeam] = useState("");
  const [person, setPerson] = useState("");
  const [project, setProject] = useState("");
  // The link's filters are read once on arrival (not with useSearchParams: no Suspense boundary).
  useEffect(() => {
    const read = readScope();
    setScope(read);
    setProject(read.repo);
    setTeam(read.team);
    setPerson(read.userId);
  }, []);
  const { data: teams } = useApi<Team[]>("/teams/visible");
  const { data: me } = useApi<Me>("/auth/me");
  const severities = scope?.severity ? [scope.severity] : ["critical", "high"];
  const path =
    scope &&
    `/findings${qs({
      team_id: team,
      limit: 200,
      business_project_id: scope.bp,
      project_id: scope.repoId,
      user_id: scope.userId,
      status: scope.status,
      found_from: scope.from,
      found_to: scope.to,
    })}${severities.map((s) => `&severity=${s}`).join("")}`;
  const { data, error } = useApi<Page<BoardFinding>>(path, { keepPreviousData: true });
  // Pages after the first 200, for the query they were loaded with.
  const [more, setMore] = useState<{ path: string; items: BoardFinding[]; cursor: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const extra = more && more.path === path ? more : null;
  const cursor = extra ? extra.cursor : (data?.next_cursor ?? null);
  const loadMore = async () => {
    if (!path || !cursor) return;
    setLoadingMore(true);
    try {
      const page = await api.get<Page<BoardFinding>>(`${path}&cursor=${encodeURIComponent(cursor)}`);
      setMore({ path, items: [...(extra?.items ?? []), ...page.items], cursor: page.next_cursor });
    } finally {
      setLoadingMore(false);
    }
  };

  const rows = [...(data?.items ?? []), ...(extra?.items ?? [])]
    .filter((f) => !person || String(f.author_user_id) === person)
    .filter((f) => !project || f.project === project)
    .sort((a, b) => b.age_days - a.age_days);
  const people = [
    ...new Map([
      // The person a link came for stays selectable even with nothing listed.
      ...(scope?.userId ? [[Number(scope.userId), scope.userName || null] as const] : []),
      ...(data?.items ?? []).filter((f) => f.author_user_id !== null).map((f) => [f.author_user_id, f.author] as const),
    ]).entries(),
  ];
  const projects = [...new Set([...(data?.items ?? []).map((f) => f.project), ...(project ? [project] : [])])].sort();
  const critical = rows.filter((f) => f.severity === "critical").length;
  const scoped = !!scope && (scope.bp !== "" || scope.team !== "" || scope.userId !== "" || scope.from !== "" || scope.severity !== "");
  const filtering = !!(team || person || project) || scoped;
  const found = scope?.status === "any" || scope?.status === "counted";
  const level = scope?.severity || "critical / high";
  // The URL keeps the level, so a reload or a copied link shows the same list.
  const setLevel = (severity: Level) => {
    const url = new URL(window.location.href);
    if (severity) url.searchParams.set("severity", severity);
    else url.searchParams.delete("severity");
    window.history.replaceState(null, "", url);
    setScope((s) => (s ? { ...s, severity } : s));
  };
  const setRepo = (repo: string) => {
    // The link's repo filter is applied by the server; picking another one is local.
    setProject(repo);
    setScope((s) => (s && s.repoId ? { ...s, repoId: "" } : s));
  };
  const setUser = (userId: string) => {
    // Like the repo: the link's person is filtered by the server, another pick is local.
    setPerson(userId);
    setScope((s) => (s && s.userId ? { ...s, userId: "" } : s));
  };
  const clear = () => {
    setTeam("");
    setPerson("");
    setProject("");
    setScope(NO_SCOPE);
    window.history.pushState(null, "", "/critical");
  };

  return (
    <>
      <PageHeader
        eyebrow="Critical board"
        title={found ? `Finding ${level} mới${scope?.period ? ` · ${scope.period}` : ""}` : `Finding ${level} đang mở`}
        subtitle={[
          scope?.bpName ? `Dự án ${scope.bpName}${project ? ` · repo ${project}` : ""}.` : null,
          scope?.teamName && scope.team !== "" && team === scope.team ? `Team ${scope.teamName}.` : null,
          scope?.userName && scope.userId !== "" && person === scope.userId ? `Của ${scope.userName}.` : null,
          scope?.status === "counted"
            ? "Tìm thấy trong kỳ, tính như chỉ số team/thành viên: bỏ finding đã duyệt là không phải lỗi, tính theo team của người viết vào ngày review. Chỉ số được tổng hợp định kỳ, danh sách thì cập nhật ngay nên có thể lệch nếu có thay đổi sau lần tổng hợp gần nhất."
            : found
            ? "Tìm thấy trong kỳ, kể cả đã sửa hoặc khiếu nại sau đó."
            : "Lâu nhất xếp trước. Bấm vào vấn đề để xem review và trích đoạn code.",
        ]
          .filter(Boolean)
          .join(" ")}
        actions={
          data && (
            <>
              {scope?.severity !== "high" && <Pill tone={critical ? "bad" : "good"}>{critical} critical</Pill>}
              {scope?.severity !== "critical" && (
                <Pill tone={rows.length - critical ? "warn" : "good"}>{rows.length - critical} high</Pill>
              )}
            </>
          )
        }
      />
      <div className="toolbar">
        <select value={scope?.severity ?? ""} onChange={(e) => setLevel(e.target.value as Level)} aria-label="Lọc theo mức">
          <option value="">Critical và high</option>
          <option value="critical">Chỉ critical</option>
          <option value="high">Chỉ high</option>
        </select>
        <select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Lọc theo team">
          <option value="">Mọi team</option>
          {teams?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={project} onChange={(e) => setRepo(e.target.value)} aria-label="Lọc theo repo">
          <option value="">Mọi repo</option>
          {projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <select value={person} onChange={(e) => setUser(e.target.value)} aria-label="Lọc theo người">
          <option value="">Mọi người</option>
          {people.map(([pid, name]) => (
            <option key={String(pid)} value={String(pid)}>
              {name ?? "—"}
            </option>
          ))}
        </select>
        {filtering && (
          <button className="ghost" onClick={clear}>
            Bỏ lọc
          </button>
        )}
      </div>

      {scoped && me && !me.is_admin && (
        <p className="muted small">
          Danh sách chỉ gồm finding bạn được quyền xem; số trên dashboard tính cho mọi người nên có thể lớn hơn.
        </p>
      )}
      {!data ? (
        <Loading error={error} rows={5} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState icon="checkCircle" tone="good" title={found ? `Không có finding ${level} nào trong kỳ` : `Không có finding ${level} nào đang mở`}>
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
                      {f.status && f.status !== "open" && (
                        <>
                          {" "}
                          <Pill tone={f.status === "fixed" ? "good" : "info"}>{STATUS_LABEL[f.status] ?? f.status}</Pill>
                        </>
                      )}
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
          {cursor && (
            <div className="muted small" style={{ padding: "10px 16px" }}>
              Đang hiện {rows.length} finding đầu tiên, còn nữa.{" "}
              <button className="ghost" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "Đang tải…" : "Tải thêm"}
              </button>
            </div>
          )}
        </Card>
      )}
    </>
  );
}
