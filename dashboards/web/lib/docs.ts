/* Tài liệu dự án (docs/specs/29): API shapes and pure helpers of the documents screen. */

export type DocCategory = "overview" | "architecture" | "setup" | "api" | "deploy" | "runbook" | "conventions" | "decision" | "other";

export interface Person {
  id: number;
  name: string;
}

export interface DocItem {
  id: number;
  business_project_id: number;
  title: string;
  category: DocCategory;
  summary: string | null;
  position: number;
  pinned: boolean;
  archived: boolean;
  version: number;
  chars: number;
  file_kind: string | null;
  updated_at: string | null;
  updated_by: Person | null;
  stale: boolean;
}

export interface CategoryCount {
  key: DocCategory;
  label: string;
  count: number;
  core: boolean;
}

export interface DocList {
  business_project_id: number;
  business_project: string;
  can_edit: boolean;
  docs: DocItem[];
  categories: CategoryCount[];
  coverage: number;
  stale_days: number;
}

export interface DocProject {
  id: number;
  name: string;
  docs: number;
  can_edit: boolean;
}

export interface DocVersion {
  number: number;
  source: "upload" | "editor" | "template" | "restore" | "link";
  note: string | null;
  chars: number;
  file_name: string | null;
  file_kind: string | null;
  file_size: number | null;
  has_original: boolean;
  created_at: string | null;
  created_by: Person | null;
  meta: { pages?: number; sheets?: number; plain?: boolean; template?: string; links?: { url: string; title: string | null; via: string }[]; mode?: DraftMode } | null;
  body?: string;
}

export interface DocSource {
  /** null for an uploaded file (then `name`). */
  url: string | null;
  name?: string | null;
  title: string | null;
  via: "web" | "gitlab" | string;
  version: number;
  at: string | null;
}

export interface DocDetail extends DocItem {
  business_project: string;
  body: string;
  current: DocVersion;
  can_edit: boolean;
  created_at: string | null;
  created_by: Person | null;
  /** Every link the document was written from (Tổng hợp từ link), newest first. */
  sources: DocSource[];
}

/* Tổng hợp từ link (docs/specs/29 §12). */
export type DraftMode = "new" | "merge" | "upgrade";
export type DraftStatus = "queued" | "fetching" | "writing" | "ready" | "failed" | "applied" | "discarded";

export interface DraftSource {
  /** null for an uploaded file (then `name`). */
  url: string | null;
  name?: string | null;
  title: string | null;
  via: "web" | "gitlab" | "file";
  chars: number;
  truncated?: boolean | null;
  error: string | null;
}

/** An uploaded file's text, read by POST /docs/drafts/extract. */
export interface ExtractedFile {
  name: string;
  kind: string;
  size: number;
  chars: number;
  truncated: boolean;
  text: string;
  pages: number | null;
}

export interface DocDraft {
  id: number;
  business_project_id: number;
  document_id: number | null;
  document_title: string | null;
  mode: DraftMode;
  category: DocCategory;
  urls: string[];
  instruction: string | null;
  status: DraftStatus;
  stale: boolean;
  error: string | null;
  base_version: number | null;
  title: string | null;
  summary: string | null;
  result: { changes: string[]; gaps: string[]; conflicts: string[]; warnings: string[]; placeholders: number } | null;
  sources: DraftSource[] | null;
  files: { name: string; kind: string; size: number; chars: number }[];
  model: string | null;
  cost_usd: number | null;
  applied_version: number | null;
  created_at: string | null;
  created_by: Person | null;
  finished_at: string | null;
  body: string | null;
}

export interface DraftList {
  drafts: DocDraft[];
  max_links: number;
  per_day: number;
  used_today: number;
  url_hosts: boolean;
  file_kinds: string[];
  max_file_mb: number;
  source_chars: number;
}

/** "2 link, 1 file" of a draft. */
export function sourceCount(d: { urls: string[]; files?: unknown[] }): string {
  const parts = [d.urls.length ? `${d.urls.length} link` : "", d.files?.length ? `${d.files.length} file` : ""].filter(Boolean);
  return parts.join(", ") || "0 nguồn";
}

export const MODE_LABEL: Record<DraftMode, string> = {
  new: "Tạo tài liệu mới",
  merge: "Bổ sung vào tài liệu",
  upgrade: "Nâng cấp theo chuẩn",
};

export const DRAFT_ACTIVE: DraftStatus[] = ["queued", "fetching", "writing"];

export interface DocHit {
  document_id: number;
  title: string;
  category: DocCategory;
  business_project_id: number;
  business_project: string;
  heading: string | null;
  snippet: string;
}

export interface DocTemplate {
  key: string;
  title: string;
  category: DocCategory;
  summary: string;
  body: string;
}

export interface DocMeta {
  kinds: string[];
  max_mb: number;
  categories: CategoryCount[];
  templates: DocTemplate[];
}

export const CATEGORY_ORDER: DocCategory[] = ["overview", "architecture", "setup", "api", "deploy", "runbook", "conventions", "decision", "other"];

export const CATEGORY_LABEL: Record<DocCategory, string> = {
  overview: "Tổng quan",
  architecture: "Kiến trúc",
  setup: "Cài đặt & môi trường",
  api: "API & tích hợp",
  deploy: "Triển khai",
  runbook: "Vận hành & sự cố",
  conventions: "Quy ước & quy trình",
  decision: "Quyết định kỹ thuật (ADR)",
  other: "Khác",
};

export const SOURCE_LABEL: Record<DocVersion["source"], string> = {
  upload: "Tải file lên",
  editor: "Sửa trên web",
  template: "Tạo từ mẫu",
  restore: "Khôi phục",
  link: "Tổng hợp từ link (AI)",
};

export const KIND_LABEL: Record<string, string> = {
  ".md": "Markdown",
  ".markdown": "Markdown",
  ".txt": "Văn bản",
  ".doc": "Word 97-2003",
  ".docx": "Word",
  ".xlsx": "Excel",
  ".pdf": "PDF",
};

/** Minutes to read (≈ 1 000 characters a minute), at least 1. */
export function readMinutes(chars: number): number {
  return Math.max(1, Math.round(chars / 1000));
}

/** How many `<…>` placeholders of a template are still in the text. */
export function placeholders(text: string): number {
  return (text.match(/<[^<>\n]{2,120}>/g) ?? []).filter((m) => !/^<https?:/.test(m)).length;
}

export type DiffLine = { op: "same" | "add" | "del"; text: string };

/**
 * Line diff of two versions (longest common subsequence). Past MAX_CELLS the table would be too
 * big for a browser tab: then only the common head and tail are matched and the middle is shown
 * as removed + added.
 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const head: DiffLine[] = a.slice(0, start).map((text) => ({ op: "same", text }));
  const tail: DiffLine[] = a.slice(endA).map((text) => ({ op: "same", text }));
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const MAX_CELLS = 4_000_000;
  if (midA.length * midB.length > MAX_CELLS) {
    return [...head, ...midA.map((text) => ({ op: "del" as const, text })), ...midB.map((text) => ({ op: "add" as const, text })), ...tail];
  }
  const n = midA.length;
  const m = midB.length;
  const table = new Uint32Array((n + 1) * (m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      table[i * (m + 1) + j] = midA[i] === midB[j] ? table[(i + 1) * (m + 1) + j + 1] + 1 : Math.max(table[(i + 1) * (m + 1) + j], table[i * (m + 1) + j + 1]);
  const mid: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (midA[i] === midB[j]) {
      mid.push({ op: "same", text: midA[i] });
      i++;
      j++;
    } else if (table[(i + 1) * (m + 1) + j] >= table[i * (m + 1) + j + 1]) mid.push({ op: "del", text: midA[i++] });
    else mid.push({ op: "add", text: midB[j++] });
  }
  while (i < n) mid.push({ op: "del", text: midA[i++] });
  while (j < m) mid.push({ op: "add", text: midB[j++] });
  return [...head, ...mid, ...tail];
}

/** Lines added and removed. */
export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  return { added: lines.filter((l) => l.op === "add").length, removed: lines.filter((l) => l.op === "del").length };
}
