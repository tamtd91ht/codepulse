"use client";

import Link from "next/link";

import { Icon } from "@/components/icons";
import { qs, useApi } from "@/lib/api";
import type { Repo, ReviewDetail, TaskRow } from "@/lib/types";

import { StatusPill, TypeIcon } from "./taskParts";

/**
 * The "Task" strip of a review (§7, §13): the tasks linked to the review's commit or MR.
 * Nothing at all while loading, when there are none, or when the viewer may not see tasks (403).
 */
export function TaskStrip({ review }: { review: ReviewDetail }) {
  // The detail names the repo by path and links the MR on GitLab; the repo id comes from /repos
  // and the MR's iid from its web URL (unless the API already gives them).
  const extra = review as ReviewDetail & { project_id?: number | null; mr_iid?: number | null; commit_sha?: string | null };
  const repos = useApi<Repo[]>(extra.project_id ? null : review.project ? "/repos" : null);
  const projectId = extra.project_id ?? repos.data?.find((r) => r.path_with_namespace === review.project)?.id ?? null;
  const iid = extra.mr_iid ?? (review.web_url ? Number(/\/merge_requests\/(\d+)/.exec(review.web_url)?.[1]) || null : null);
  const sha = extra.commit_sha ?? review.reviewed_sha;
  const target = review.target_type === "mr" ? (iid ? { mr_iid: iid } : null) : sha ? { sha } : null;
  const linked = useApi<TaskRow[] | { items: TaskRow[] }>(projectId && target ? `/tasks/linked${qs({ project_id: projectId, ...target })}` : null);
  const rows = Array.isArray(linked.data) ? linked.data : (linked.data?.items ?? []);
  if (linked.error || !rows.length) return null;
  return (
    <section className="card tk-strip" aria-label="Task liên kết">
      <span className="tk-strip-label">
        <Icon name="clipboardCheck" size={16} /> Task
      </span>
      <ul>
        {rows.map((t) => (
          <li key={t.key}>
            <Link href={`/tasks?task=${encodeURIComponent(t.key)}`} className="tk-strip-item">
              <TypeIcon type={t.type} />
              <span className="tk-key">{t.key}</span>
              <span className="tk-strip-title">{t.title}</span>
              <StatusPill status={t.status} resolution={t.resolution} />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
