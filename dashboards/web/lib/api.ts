import useSWR, { type SWRResponse } from "swr";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, redirectOn401 = true): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (response.status === 401 && redirectOn401 && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, typeof detail === "string" ? detail : describeDetail(detail));
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/** Pydantic's 422 body is a list of {loc, msg}; show "field: message" instead of raw JSON. */
function describeDetail(detail: unknown): string {
  if (Array.isArray(detail) && detail.every((d) => d && typeof d === "object" && "msg" in d)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc ?? []).filter((p) => p !== "body").join(".");
        return field ? `${field}: ${d.msg}` : String(d.msg);
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/**
 * GET with caching; pass null to skip (e.g. while a dependency is loading).
 * `redirect: false` keeps a signed-out visitor on public pages instead of sending them to /login.
 */
export function useApi<T>(
  path: string | null,
  {
    redirect = true,
    keepPreviousData = false,
    refreshInterval = 0,
  }: {
    redirect?: boolean;
    keepPreviousData?: boolean;
    /** ms between polls; a function decides from the latest data (0 = no polling). */
    refreshInterval?: number | ((latest?: T) => number);
  } = {},
): SWRResponse<T, ApiError> {
  return useSWR<T, ApiError>(path, (p: string) => request<T>(p, undefined, redirect), {
    keepPreviousData,
    refreshInterval,
    // A 4xx is an answer (not allowed, not found), not a transient failure: do not retry it.
    shouldRetryOnError: (err: ApiError) => !err.status || err.status >= 500,
  });
}

export function qs(params: Record<string, string | number | null | undefined>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "");
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}` : "";
}

/**
 * What a dashboard count is about: a dự án, one of its repos, a team or a person, the period it
 * was found in. `counted`: team and person numbers, which leave out false positives and count a
 * member's findings in the team they were in that day (status=counted on /findings).
 */
export interface BoardScope {
  project?: { id: number; name: string } | null;
  repo?: { id: number; path: string };
  team?: { id: number; name: string };
  user?: { id: number; name: string };
  period?: { range: [string, string]; label: string; counted?: boolean };
}

/**
 * The Critical Board showing what a dashboard count counts: one severity, open now, or found in
 * `period` ([from, to) instants and a label) when given, narrowed to the rest of `scope`.
 */
export function boardLink(severity: "critical" | "high", scope: BoardScope = {}): string {
  const { project, repo, team, user, period } = scope;
  return `/critical${qs({
    severity,
    bp: project?.id,
    bp_name: project?.name,
    repo_id: repo?.id,
    repo: repo?.path,
    team: team?.id,
    team_name: team?.name,
    user: user?.id,
    user_name: user?.name,
    ...(period
      ? { status: period.counted ? "counted" : "any", from: period.range[0], to: period.range[1], period: period.label }
      : {}),
  })}`;
}
