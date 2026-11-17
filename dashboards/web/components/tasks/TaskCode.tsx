"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { ExternalLink, Loading, Message, Pill } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";
import { VERDICT_LABEL, dateTime, num } from "@/lib/format";
import type { TaskCommitCandidate, TaskDetail, TaskGit, TaskRelation, TaskReviewRef } from "@/lib/types";

import { errorText, refreshTasks, useConfirm, useSpaceRepos } from "./taskParts";

/* The "Code" of a task (§7): the lists of branches, MRs and commits git knows about, each with its
 * review. The summary (counts, score, critical, pipeline, deploy) is in the Sức khoẻ block (§21.1). */

const MR_STATE: Record<string, { label: string; tone?: "good" | "bad" | "info" }> = {
  opened: { label: "Đang mở", tone: "info" },
  merged: { label: "Đã merge", tone: "good" },
  closed: { label: "Đã đóng" },
};

function ReviewCell({ review }: { review: TaskReviewRef | null }) {
  if (!review) return <span className="small faint">Chưa review</span>;
  return (
    <Link href={`/reviews/${review.id}`} className="tk-review-link" title="Mở review">
      <strong>{num(review.score === null ? null : String(review.score), 0)}</strong>
      {review.verdict && <span className={`verdict ${review.verdict}`}>{VERDICT_LABEL[review.verdict] ?? review.verdict}</span>}
      {review.open_critical > 0 && <span className="tk-badge bad">{review.open_critical} critical</span>}
      {review.open_high > 0 && <span className="tk-badge warn">{review.open_high} high</span>}
    </Link>
  );
}

export function TaskCode({ task, bp }: { task: TaskDetail; bp: number | null }) {
  const git: TaskGit = task.git ?? {
    branches: [],
    commits: [],
    mrs: [],
    summary: { commits: 0, mrs: 0, open_critical: 0, open_high: 0, avg_score: null, pipelines_failed: 0, deployed: null },
  };
  const [dialog, setDialog] = useState<"commit" | "branch" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmEl, ask] = useConfirm();
  const canEdit = task.can.link;
  const canUnlink = (manual?: boolean) => task.can.manage || (canEdit && !!manual);

  async function unlink(id: number, what: string) {
    if (!(await ask({ title: `Gỡ ${what} khỏi task?`, body: <p>Git có thể nối lại nếu commit / MR vẫn ghi mã task.</p>, ok: "Gỡ", danger: true }))) return;
    try {
      await api.delete(`/tasks/${encodeURIComponent(task.key)}/git/${id}`);
      await refreshTasks();
    } catch (e) {
      setMessage(`Lỗi: ${errorText(e)}`);
    }
  }

  const empty = !git.branches.length && !git.commits.length && !git.mrs.length;
  return (
    <section className="tk-section" aria-labelledby="tk-code-title">
      {confirmEl}
      <div className="tk-section-head">
        <h3 id="tk-code-title">
          <Icon name="code" size={16} /> Code
        </h3>
        {canEdit && (
          <span className="actions">
            <button type="button" className="sm ghost" onClick={() => setDialog("commit")}>
              <Icon name="link" size={14} /> Liên kết commit
            </button>
            <button type="button" className="sm ghost" onClick={() => setDialog("branch")}>
              <Icon name="plus" size={14} /> Tạo nhánh
            </button>
          </span>
        )}
      </div>
      {message && <Message text={message} onClose={() => setMessage(null)} />}
      {empty ? (
        <p className="small faint">
          Chưa có nhánh, commit hay MR nào. Đặt mã <code>{task.key}</code> trong tên nhánh, commit hoặc tiêu đề MR để nối tự động.
        </p>
      ) : (
        <div className="tk-code-lists">
          {git.branches.length > 0 && (
            <div>
              <h4 className="tk-sub">Nhánh</h4>
              <ul className="tk-git-list">
                {git.branches.map((b) => (
                  <li key={b.id}>
                    <Icon name="code" size={14} />
                    <code className="tk-ellipsis">{b.branch}</code>
                    <span className="small muted tk-ellipsis">{b.repo}</span>
                    <span className="spacer" />
                    {b.web_url && <ExternalLink href={b.web_url}>GitLab</ExternalLink>}
                    {canUnlink(b.manual) && <IconButton icon="close" label="Gỡ nhánh" onClick={() => void unlink(b.id, "nhánh")} />}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {git.mrs.length > 0 && (
            <div>
              <h4 className="tk-sub">Merge request</h4>
              <ul className="tk-git-list">
                {git.mrs.map((m) => {
                  const st = MR_STATE[m.state ?? ""] ?? { label: m.state ?? "—" };
                  return (
                    <li key={m.id}>
                      <span className="tk-git-main">
                        <span className="tk-git-title">
                          <strong>!{m.mr_iid}</strong> {m.title}
                        </span>
                        <span className="small muted">
                          {m.repo} · {m.relation === "fixes" ? "fixes" : "refs"}
                        </span>
                      </span>
                      <span className="tk-git-side">
                        <Pill tone={st.tone}>{st.label}</Pill>
                        {m.pipeline && (
                          <Pill tone={m.pipeline.status === "success" ? "good" : m.pipeline.status === "failed" ? "bad" : undefined} title="Pipeline của head">
                            CI {m.pipeline.status}
                          </Pill>
                        )}
                        <ReviewCell review={m.review} />
                        {m.web_url && <ExternalLink href={m.web_url}>GitLab</ExternalLink>}
                        {canUnlink(m.manual) && <IconButton icon="close" label="Gỡ MR" onClick={() => void unlink(m.id, `MR !${m.mr_iid}`)} />}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {git.commits.length > 0 && (
            <div>
              <h4 className="tk-sub">Commit</h4>
              <ul className="tk-git-list">
                {git.commits.map((c) => (
                  <li key={c.id}>
                    <span className="tk-git-main">
                      <span className="tk-git-title">
                        <code>{c.sha.slice(0, 8)}</code> {c.title}
                      </span>
                      <span className="small muted">
                        {c.repo} · {c.relation === "fixes" ? "fixes" : "refs"}
                      </span>
                    </span>
                    <span className="tk-git-side">
                      <ReviewCell review={c.review} />
                      {c.web_url && <ExternalLink href={c.web_url}>GitLab</ExternalLink>}
                      {canUnlink(c.manual) && <IconButton icon="close" label="Gỡ commit" onClick={() => void unlink(c.id, `commit ${c.sha.slice(0, 8)}`)} />}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {dialog === "commit" && <LinkCommitModal taskKey={task.key} bp={bp} onClose={() => setDialog(null)} />}
      {dialog === "branch" && <BranchModal task={task} bp={bp} onClose={() => setDialog(null)} onDone={(m) => setMessage(m)} />}
    </section>
  );
}

function LinkCommitModal({ taskKey, bp, onClose }: { taskKey: string; bp: number | null; onClose: () => void }) {
  const [mode, setMode] = useState<"commit" | "mr">("commit");
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [relation, setRelation] = useState<TaskRelation>("refs");
  const [repo, setRepo] = useState("");
  const [iid, setIid] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const repos = useSpaceRepos(mode === "mr" ? bp : null);
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  const candidates = useApi<TaskCommitCandidate[]>(mode === "commit" ? `/tasks/${encodeURIComponent(taskKey)}/commit-candidates${qs({ q: term })}` : null, { keepPreviousData: true });

  async function link(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/tasks/${encodeURIComponent(taskKey)}/git`, { ...body, relation });
      await refreshTasks();
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal title="Liên kết commit / MR" onClose={onClose} busy={busy} width={680}>
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <div className="rq-grid tk-grid-2">
          <label className="rq-field">
            <span className="rq-label">Liên kết</span>
            <select value={mode} onChange={(e) => setMode(e.target.value as "commit" | "mr")}>
              <option value="commit">Commit</option>
              <option value="mr">Merge request</option>
            </select>
          </label>
          <label className="rq-field">
            <span className="rq-label">Quan hệ</span>
            <select value={relation} onChange={(e) => setRelation(e.target.value as TaskRelation)}>
              <option value="refs">refs: có liên quan</option>
              <option value="fixes">fixes: làm xong task</option>
            </select>
          </label>
        </div>
        {mode === "commit" ? (
          <>
            <label className="rq-field">
              <span className="rq-label">Tìm commit</span>
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="SHA (≥ 4 ký tự) hoặc từ trong message" autoFocus />
            </label>
            {!candidates.data ? (
              <Loading error={candidates.error} rows={3} />
            ) : !candidates.data.length ? (
              <p className="small faint">Không có commit nào khớp.</p>
            ) : (
              <ul className="tk-git-list tk-candidates">
                {candidates.data.map((c) => (
                  <li key={`${c.project_id}:${c.sha}`}>
                    <span className="tk-git-main">
                      <span className="tk-git-title">
                        <code>{c.sha.slice(0, 8)}</code> {c.title}
                      </span>
                      <span className="small muted">
                        {[c.repo, c.author, c.authored_at ? dateTime(c.authored_at) : null].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    {c.linked ? (
                      <span className="small muted">Đã nối</span>
                    ) : (
                      <button type="button" className="sm" disabled={busy} onClick={() => void link({ kind: "commit", sha: c.sha, project_id: c.project_id })}>
                        Liên kết
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <>
            <div className="rq-grid tk-grid-2">
              <label className="rq-field">
                <span className="rq-label">Repo</span>
                <select value={repo} onChange={(e) => setRepo(e.target.value)} disabled={!repos.data}>
                  <option value="">Chọn repo…</option>
                  {repos.list.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.path_with_namespace}
                    </option>
                  ))}
                </select>
              </label>
              <label className="rq-field">
                <span className="rq-label">Số MR (!iid)</span>
                <input type="number" min={1} value={iid} onChange={(e) => setIid(e.target.value)} />
              </label>
            </div>
            <div className="adm-form-foot">
              <button
                type="button"
                className="primary"
                disabled={busy || !repo || !iid}
                onClick={() => void link({ kind: "mr", project_id: Number(repo), mr_iid: Number(iid) })}
              >
                Liên kết MR
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

function BranchModal({ task, bp, onClose, onDone }: { task: TaskDetail; bp: number | null; onClose: () => void; onDone: (m: string) => void }) {
  const repos = useSpaceRepos(bp);
  const [repo, setRepo] = useState("");
  const [from, setFrom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!repo && repos.list.length === 1) setRepo(String(repos.list[0].id));
  }, [repos.list, repo]);
  const chosen = repos.list.find((r) => String(r.id) === repo);
  async function create() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ branch?: string } | null>(`/tasks/${encodeURIComponent(task.key)}/branch`, {
        project_id: Number(repo),
        ...(from.trim() ? { from_ref: from.trim() } : {}),
      });
      await refreshTasks();
      onDone(`Đã tạo nhánh${res?.branch ? ` ${res.branch}` : ""}.`);
      onClose();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`Tạo nhánh cho ${task.key}`}
      onClose={onClose}
      busy={busy}
      width={520}
      footer={
        <>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Huỷ
          </button>
          <button type="button" className="primary" disabled={busy || !repo} onClick={() => void create()}>
            {busy ? "Đang tạo…" : "Tạo nhánh"}
          </button>
        </>
      }
    >
      <div className="adm-form tk-form">
        {error && <Message text={`Lỗi: ${error}`} onClose={() => setError(null)} />}
        <p className="small muted">
          Tạo nhánh <code>{task.type === "bug" ? "bugfix" : "feature"}/{task.key}-…</code> trên GitLab và nối vào task.
        </p>
        {!repos.data ? (
          <Loading error={repos.error} rows={2} />
        ) : !repos.list.length ? (
          <p className="small faint">Dự án chưa có repo nào được theo dõi.</p>
        ) : (
          <div className="rq-grid tk-grid-2">
            <label className="rq-field">
              <span className="rq-label">Repo</span>
              <select value={repo} onChange={(e) => setRepo(e.target.value)}>
                <option value="">Chọn repo…</option>
                {repos.list.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.path_with_namespace}
                  </option>
                ))}
              </select>
            </label>
            <label className="rq-field">
              <span className="rq-label">Từ nhánh</span>
              <input value={from} onChange={(e) => setFrom(e.target.value)} placeholder={chosen?.default_branch ?? "nhánh mặc định"} />
            </label>
          </div>
        )}
      </div>
    </Modal>
  );
}

