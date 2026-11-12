/*
 * Markdown tables for the document editor (docs/specs/29 §14): find the table around the caret,
 * change it (rows, columns, column alignment) and write it back with its columns lined up; build
 * a new one; turn cells copied from Excel / Google Sheets (tab-separated) into one. Cells are
 * kept as written (an escaped `\|` stays escaped), so rewriting never changes their text.
 */

export type ColumnAlign = "left" | "center" | "right" | null;

export interface MdTable {
  head: string[];
  align: ColumnAlign[];
  rows: string[][];
}

/** Where the caret is: the table's lines in the text, and the cell (row -1 = the header). */
export interface TableAt {
  start: number;
  end: number;
  table: MdTable;
  row: number;
  col: number;
}

const SEP_CELL = /^:?-{1,}:?$/;

/** The cells of a table line, as written (the same split as the renderer's). */
export function splitCells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") {
      cur += "\\|";
      i++;
    } else if (s[i] === "|") {
      out.push(cur.trim());
      cur = "";
    } else cur += s[i];
  }
  out.push(cur.trim());
  return out;
}

function isSep(line: string): boolean {
  const c = splitCells(line);
  return line.includes("-") && c.length > 0 && c.every((x) => SEP_CELL.test(x.replace(/\s/g, "")));
}

function alignOf(cell: string): ColumnAlign {
  const c = cell.replace(/\s/g, "");
  return c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null;
}

/** Display width: combining marks (Vietnamese tones written apart) take no column. */
function width(s: string): number {
  return s.normalize("NFC").replace(/[̀-ͯ]/g, "").length;
}

function pad(s: string, w: number, a: ColumnAlign): string {
  const gap = Math.max(0, w - width(s));
  if (a === "right") return " ".repeat(gap) + s;
  if (a === "center") return " ".repeat(Math.floor(gap / 2)) + s + " ".repeat(Math.ceil(gap / 2));
  return s + " ".repeat(gap);
}

/** The table as Markdown, columns lined up (every row as many cells as the header). */
export function formatTable(t: MdTable): string {
  const n = Math.max(1, t.head.length);
  const rows = [t.head, ...t.rows].map((r) => Array.from({ length: n }, (_, i) => r[i] ?? ""));
  const w = Array.from({ length: n }, (_, i) => Math.max(3, ...rows.map((r) => width(r[i]))));
  const line = (r: string[]) => `| ${r.map((c, i) => pad(c, w[i], t.align[i] ?? null)).join(" | ")} |`;
  const sep = `| ${w
    .map((x, i) => {
      const a = t.align[i] ?? null;
      const dashes = "-".repeat(Math.max(1, x - (a === "center" ? 2 : a ? 1 : 0)));
      return a === "center" ? `:${dashes}:` : a === "right" ? `${dashes}:` : a === "left" ? `:${dashes}` : dashes;
    })
    .join(" | ")} |`;
  return [line(rows[0]), sep, ...rows.slice(1).map(line)].join("\n");
}

/** The table the caret is in (header, separator or a row), or null. */
export function findTable(text: string, pos: number): TableAt | null {
  const lines = text.split("\n");
  const starts: number[] = [];
  let off = 0;
  for (const l of lines) {
    starts.push(off);
    off += l.length + 1;
  }
  let li = 0;
  while (li + 1 < lines.length && starts[li + 1] <= pos) li++;
  const isRow = (k: number) => k >= 0 && k < lines.length && lines[k].trim() !== "" && lines[k].includes("|");
  if (!isRow(li)) return null;
  let top = li;
  while (isRow(top - 1)) top--;
  // The block may hold text lines with "|" above the table: the table starts where a header is
  // followed by its separator.
  let head = -1;
  for (let k = top; k < li + 1 && k + 1 < lines.length; k++) {
    if (isRow(k) && isSep(lines[k + 1])) head = k;
  }
  if (head < 0 && li + 1 < lines.length && isSep(lines[li + 1])) head = li;
  if (head < 0) return null;
  let bottom = head + 1;
  while (isRow(bottom + 1)) bottom++;
  if (li < head || li > bottom) return null;
  const table: MdTable = {
    head: splitCells(lines[head]),
    align: splitCells(lines[head + 1]).map(alignOf),
    rows: lines.slice(head + 2, bottom + 1).map(splitCells),
  };
  // The cell: unescaped pipes before the caret on its line.
  const line = lines[li];
  const before = line.slice(0, Math.max(0, pos - starts[li]));
  const lead = line.trimStart().startsWith("|") ? 1 : 0;
  const pipes = (before.match(/(?<!\\)\|/g) ?? []).length;
  const cols = Math.max(1, table.head.length);
  return {
    start: starts[head],
    end: starts[bottom] + lines[bottom].length,
    table,
    row: li <= head + 1 ? -1 : li - head - 2,
    col: Math.min(cols - 1, Math.max(0, pipes - lead)),
  };
}

/** Where cell (row, col) of `formatted` starts (row -1 = header), for the caret. */
export function cellOffset(formatted: string, row: number, col: number): number {
  const lines = formatted.split("\n");
  const li = Math.min(lines.length - 1, row < 0 ? 0 : row + 2);
  let at = 0;
  for (let k = 0; k < li; k++) at += lines[k].length + 1;
  const line = lines[li];
  let seen = 0;
  for (let i = 0; i < line.length; i++) {
    if (line[i] === "|" && line[i - 1] !== "\\") {
      if (seen === col) return at + Math.min(line.length, i + 2);
      seen++;
    }
  }
  return at + line.length;
}

export function newTable(rows: number, cols: number): MdTable {
  return {
    head: Array.from({ length: cols }, (_, i) => `Cột ${i + 1}`),
    align: Array.from({ length: cols }, () => null),
    rows: Array.from({ length: Math.max(1, rows - 1) }, () => Array.from({ length: cols }, () => "")),
  };
}

export function insertRow(t: MdTable, at: number): MdTable {
  const rows = [...t.rows];
  rows.splice(Math.max(0, Math.min(rows.length, at)), 0, t.head.map(() => ""));
  return { ...t, rows };
}

export function deleteRow(t: MdTable, at: number): MdTable {
  return { ...t, rows: t.rows.filter((_, i) => i !== at) };
}

export function insertCol(t: MdTable, at: number): MdTable {
  const i = Math.max(0, Math.min(t.head.length, at));
  const put = <T,>(r: T[], v: T) => [...r.slice(0, i), v, ...r.slice(i)];
  return { head: put(t.head, "Cột mới"), align: put(t.align, null), rows: t.rows.map((r) => put(r, "")) };
}

export function deleteCol(t: MdTable, at: number): MdTable {
  if (t.head.length <= 1) return t;
  const drop = <T,>(r: T[]) => r.filter((_, k) => k !== at);
  return { head: drop(t.head), align: drop(t.align), rows: t.rows.map(drop) };
}

export function alignCol(t: MdTable, at: number, a: ColumnAlign): MdTable {
  return { ...t, align: t.head.map((_, i) => (i === at ? a : (t.align[i] ?? null))) };
}

/**
 * Cells copied from a spreadsheet (rows on lines, cells split by tabs) as a table, the first row
 * its header; null when the text is not that (fewer than 2 rows or 2 columns).
 */
export function fromSpreadsheet(text: string): MdTable | null {
  const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
  if (lines.length < 2 || !lines.every((l) => l.includes("\t"))) return null;
  const grid = lines.map((l) => l.split("\t").map((c) => c.trim().replace(/\|/g, "\\|")));
  const cols = Math.max(...grid.map((r) => r.length));
  if (cols < 2) return null;
  const fill = (r: string[]) => Array.from({ length: cols }, (_, i) => r[i] ?? "");
  return { head: fill(grid[0]).map((c, i) => c || `Cột ${i + 1}`), align: Array.from({ length: cols }, () => null), rows: grid.slice(1).map(fill) };
}
