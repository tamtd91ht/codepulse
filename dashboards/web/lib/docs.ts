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
  source: "upload" | "editor" | "template" | "restore";
  note: string | null;
  chars: number;
  file_name: string | null;
  file_kind: string | null;
  file_size: number | null;
  has_original: boolean;
  created_at: string | null;
  created_by: Person | null;
  meta: { pages?: number; sheets?: number; plain?: boolean; template?: string } | null;
  body?: string;
}

export interface DocDetail extends DocItem {
  business_project: string;
  body: string;
  current: DocVersion;
  can_edit: boolean;
  created_at: string | null;
  created_by: Person | null;
}

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
