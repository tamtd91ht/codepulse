"use client";

import { useCallback, useEffect, useState } from "react";

import { ProjectDrawer, ProjectTiles, type TileKey } from "@/components/ProjectTiles";
import { makeRepoActions } from "@/components/repoActions";
import { addRepos, repoFieldsOf } from "@/components/AddRepoFields";
import { UNASSIGNED } from "@/components/RepoTable";
import { EmptyState, Loading, Message, PageHeader } from "@/components/ui";
import { api, setupProjects, useApi } from "@/lib/api";
import type { BusinessProject, Connection, Me, Project } from "@/lib/types";

/**
 * "Quản trị dự án" (docs/specs/21 §10): the dự án an admin gave this person as Lead / Manager.
 * They add the repos, configure them, the dự án's .claude repo, rule files and review rules;
 * the Manager also decides who sees the dự án. The same drawer as Admin › Dự án & Repo, without
 * the admin-only parts (rename, delete, bulk import, hard removal, the external-LLM switch).
 */
export default function ProjectAdminPage() {
  const { data: me } = useApi<Me>("/auth/me");
  const mine = setupProjects(me);
  const allowed = !!me && (mine === null || mine.length > 0);
  const projects = useApi<BusinessProject[]>(allowed ? "/business-projects" : null);
  const repos = useApi<Project[]>(allowed ? "/projects" : null);
  const connections = useApi<Connection[]>(allowed ? "/connections" : null);
  const [openTile, setOpenTile] = useState<TileKey | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(0);

  // A link from a dự án's dashboard (?open=<id>) opens its drawer once the list is there.
  useEffect(() => {
    if (!projects.data) return;
    const wanted = Number(new URLSearchParams(window.location.search).get("open"));
    if (wanted && projects.data.some((b) => b.id === wanted)) setOpenTile(wanted);
  }, [projects.data]);

  const run = useCallback(async (action: () => Promise<unknown>, done?: string): Promise<boolean> => {
    setPending((n) => n + 1);
    try {
      const result = await action();
      setMessage(done ?? String(result));
      return true;
    } catch (e) {
      setMessage(`Lỗi: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      setPending((n) => n - 1);
    }
  }, []);
  const refresh = () => Promise.all([repos.mutate(), projects.mutate()]);
  const actions = makeRepoActions({ run, refreshRepos: () => repos.mutate(), refreshAll: refresh, setMessage });
  const closeDrawer = useCallback(() => setOpenTile(null), []);

  async function addReposToProject(f: FormData, b: BusinessProject): Promise<boolean> {
    return run(async () => {
      const fields = repoFieldsOf(f);
      const result = await addRepos(fields.text, { ...fields, businessProjectId: b.id }).finally(() => void refresh());
      if (result.failed) throw new Error(result.text);
      return result.text;
    });
  }
  async function saveRules(b: BusinessProject, rules: string): Promise<boolean> {
    return run(async () => {
      await api.patch(`/business-projects/${b.id}`, { name: b.name, description: b.description, review_rules: rules });
      await projects.mutate();
    }, "Đã lưu quy tắc review của dự án; áp dụng cho review tiếp theo.");
  }

  const list = projects.data ?? [];
  const projectOptions = (
    <>
      <option value={UNASSIGNED}>Gỡ khỏi dự án</option>
      {list.map((b) => (
        <option key={b.id} value={b.id}>
          {b.name}
        </option>
      ))}
    </>
  );
  const notice = <Message text={message} onClose={() => setMessage(null)} />;
  const open = openTile !== null && openTile !== UNASSIGNED ? list.find((b) => b.id === openTile) : undefined;

  return (
    <>
      <PageHeader
        title="Quản trị dự án"
        subtitle="Dự án admin giao cho bạn làm Lead / Manager: thêm repo, cấu hình repo, repo .claude, quy tắc review."
      />
      {!me ? (
        <Loading rows={3} />
      ) : !allowed ? (
        <EmptyState icon="folder" title="Bạn chưa quản trị dự án nào">
          {me.permissions?.["project.setup"] === "all"
            ? "Bạn có quyền Quản trị dự án trên mọi dự án mình được xem, nhưng chưa được cấp quyền vào dự án nào."
            : me.permissions?.["project.setup"]
              ? "Bạn có quyền Quản trị dự án trên các dự án mình quản lý (quyền Quản lý), nhưng chưa quản lý dự án nào."
              : null}{" "}
          Admin giao dự án cho bạn ở Admin › Dự án &amp; Repo › Quyền truy cập: chọn vai trò Lead dự án / Manager dự án
          (hoặc quyền Quản lý).
        </EmptyState>
      ) : !projects.data || !repos.data ? (
        <Loading error={projects.error ?? repos.error} rows={3} />
      ) : (
        <section className="card">
          {openTile === null && notice}
          {list.length === 0 ? (
            <EmptyState icon="folder" title="Chưa có dự án nào" />
          ) : (
            <ProjectTiles projects={list} repos={repos.data.filter((p) => p.business_project_id !== null)} onOpen={setOpenTile} />
          )}
        </section>
      )}
      {open && repos.data && (
        <ProjectDrawer
          key={open.id}
          tile={open.id}
          projects={list}
          repos={repos.data}
          actions={actions}
          projectOptions={projectOptions}
          onRename={async () => false}
          onDelete={async () => false}
          onSaveRules={saveRules}
          connections={connections.data ?? []}
          onAddRepos={addReposToProject}
          onClose={closeDrawer}
          busy={pending > 0}
          notice={notice}
          limited
          staff={mine === null || me?.project_admin?.[String(open.id)] === "manager"}
        />
      )}
    </>
  );
}
