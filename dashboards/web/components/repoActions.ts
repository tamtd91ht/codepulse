import { type Flag, type RepoActions, repoName, UNASSIGNED } from "@/components/RepoTable";
import { ApiError, api } from "@/lib/api";
import type { Project } from "@/lib/types";

/**
 * The repo row actions (sync, webhook, branch, flags, rules, tracking, dự án) shared by Admin ›
 * Dự án & Repo and the Lead / Manager's "Quản trị dự án" page (docs/specs/21 §10): the same
 * endpoints, which check on the server who may act on which repo.
 */
export function makeRepoActions({
  run,
  refreshRepos,
  refreshAll,
  setMessage,
  onRemove,
  onTracked,
}: {
  /** Runs an action and reports it; resolves true on success. */
  run: (action: () => Promise<unknown>, done?: string) => Promise<boolean>;
  /** Reloads the repo list. */
  refreshRepos: () => Promise<unknown>;
  /** Reloads repos and dự án (counts change). */
  refreshAll: () => Promise<unknown>;
  setMessage: (text: string | null) => void;
  /** Opens the hard-removal confirmation (admins only). */
  onRemove?: (items: Project[]) => void;
  /** After tracking changed (e.g. clear a selection). */
  onTracked?: () => void;
}): RepoActions {
  async function registerWebhook(p: Project): Promise<string> {
    const hook = await api.post<{ url: string; created: boolean }>(`/projects/${p.id}/webhook`);
    return hook.created ? `Đã đăng ký webhook ${hook.url}.` : `Webhook đã có sẵn: ${hook.url}.`;
  }

  return {
    assign: (ids, value, label) =>
      run(async () => {
        await api.put("/projects/business-project", {
          project_ids: ids,
          business_project_id: value === UNASSIGNED ? null : Number(value),
        });
        await refreshAll();
      }, label),
    setTracking: async (items, track) => {
      const targets = items.filter((p) => p.tracked !== track);
      if (!targets.length) return;
      if (!track && !window.confirm(`Bỏ theo dõi ${targets.length} repo? Lịch sử review vẫn giữ.`)) return;
      await run(async () => {
        await Promise.all(targets.map((p) => api.post(`/projects/${p.id}/${track ? "track" : "untrack"}`)));
        onTracked?.();
        await refreshAll();
      }, `${track ? "Đã theo dõi lại" : "Đã bỏ theo dõi"} ${targets.length} repo.`);
    },
    remove: (items) => onRemove?.(items),
    refreshProfile: (p) =>
      run(
        () => api.post(`/projects/${p.id}/profile/refresh`),
        `Đã đưa việc dựng lại hồ sơ của ${repoName(p)} vào hàng đợi; xem ở trang dự án sau vài phút.`,
      ),
    setRules: (p, rules, paths) =>
      run(async () => {
        await api.patch(`/projects/${p.id}`, { review_rules: rules, review_rules_paths: paths });
        await refreshRepos();
      }, `Đã lưu quy tắc review của ${repoName(p)}.`),
    setBranchMany: (items, branch) =>
      run(async () => {
        const result = await api.put<{ updated: number; missing: string[]; checked: boolean }>(
          "/projects/review-branch",
          { project_ids: items.map((p) => p.id), review_branch: branch },
        );
        await refreshRepos();
        const what = branch === "" ? "nhánh mặc định" : branch === "*" ? "mọi nhánh" : `nhánh ${branch}`;
        const missing = result.missing.length
          ? ` Không thấy nhánh này trên GitLab ở: ${result.missing.join(", ")} (vẫn lưu; tạo nhánh hoặc chọn lại cho các repo đó).`
          : "";
        return `Đã đặt ${what} cho ${result.updated} repo.${missing}`;
      }),
    setBranch: (p, branch) =>
      run(async () => {
        await api.patch(`/projects/${p.id}`, { review_branch: branch });
        await refreshRepos();
      }, `Đã đổi nhánh review của ${repoName(p)}.`),
    setFlag: (p: Project, flag: Flag, value: boolean) =>
      run(async () => {
        await api.patch(`/projects/${p.id}`, { [flag]: value });
        await refreshRepos();
      }, `Đã cập nhật ${repoName(p)}.`),
    sync: (p) => run(() => api.post(`/projects/${p.id}/sync`), "Đã đưa vào hàng đợi sync."),
    webhook: (p) => run(() => registerWebhook(p)),
    backfill: async (p, f) => {
      const params = new URLSearchParams({
        from: String(f.get("from")),
        to: String(f.get("to")),
        include_open: f.get("include_open") ? "true" : "false",
      });
      const text = await (async () => {
        const result = await api.post<{ queued: number }>(`/projects/${p.id}/backfill-reviews?${params}`);
        return result.queued
          ? `Đã đưa ${result.queued} MR của ${repoName(p)} vào hàng đợi review; kết quả hiện ở trang review sau vài phút.`
          : "Không có MR nào chưa được review trong khoảng này (cần Sync trước nếu repo mới thêm).";
      })().catch((e: unknown) => `Lỗi: ${e instanceof ApiError ? e.message : String(e)}`);
      setMessage(text);
      return text;
    },
  };
}
