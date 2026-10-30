"use client";

import { ReviewResults } from "@/components/ReviewResults";
import { Alert, Loading, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Repo } from "@/lib/types";

/**
 * Every review the viewer may see, across the dự án they are granted: one place to filter by
 * dự án, repo, member, status (done, in progress, failed…) and fast or deep, and to re-run.
 */
export default function ReviewsPage() {
  const repos = useApi<Repo[]>("/repos");
  // The dự án of the repos the viewer sees (repos outside every dự án have none to pick).
  const projects = [
    ...new Map(
      (repos.data ?? [])
        .filter((r) => r.business_project_id !== null)
        .map((r) => [r.business_project_id as number, { id: r.business_project_id as number, name: r.business_project ?? `#${r.business_project_id}` }]),
    ).values(),
  ].sort((a, b) => a.name.localeCompare(b.name, "vi"));

  return (
    <>
      <PageHeader
        eyebrow="Review"
        title="Kết quả review"
        subtitle="Mọi review bạn được xem, lọc theo dự án, repo, thành viên, trạng thái và kiểu review; chọn nhiều để review lại."
      />
      {repos.error && <Alert tone="error">Không tải được danh sách repo: {repos.error.message}</Alert>}
      {!repos.data && !repos.error ? (
        <Loading rows={4} />
      ) : (
        <ReviewResults projects={projects} repos={repos.data ?? []} id="review-results-all" title="Danh sách review" />
      )}
    </>
  );
}
