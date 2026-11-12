"use client";

import { type ClipboardEvent, Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { Modal } from "@/components/CriteriaParts";
import { Icon, IconButton } from "@/components/icons";
import { type UndoNote, UndoNotice, bulkNote } from "@/components/TestcaseBulk";
import { SectionOptions } from "@/components/TestcaseSections";
import { Alert, Loading, Pill } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import {
  type AiRun,
  type BddCheck,
  type BulkOut,
  type CaseList,
  CONVERT_LABEL,
  type ConvertDirection,
  type ConvertProposal,
  type FeatureImportOut,
  PARAM_MAX_COLS,
  PARAM_MAX_ROWS,
  PARAM_MAX_VALUE,
  PARAM_NAME,
  type ParamTable,
  type Section,
  type StepItem,
  type Suite,
  TEMPLATE_LABEL,
  type TestCase,
  caseKey,
  countOf,
  paramProblems,
} from "@/lib/testcases";
import { errorText } from "@/lib/testruns";

/*
 * BDD / Gherkin and parameters of a case (docs/specs/30 §15.12, §15.15, API §15.16): the scenario
 * editor with a live syntax check, the Gherkin view (keywords bold, tables as tables, docstrings as
 * pre), the parameter grid (paste from a spreadsheet), .feature import, and the AI conversion
 * between BDD and steps (proposals with before / after, applied with undo).
 */

/* ---------------- Gherkin: a light reader for display ---------------- */

const SECTION_KEYWORDS = ["Scenario Outline", "Scenario Template", "Background", "Scenarios", "Examples", "Scenario", "Example", "Feature", "Rule"];
const STEP_KEYWORDS = ["Given", "When", "Then", "And", "But"];

type GBlock =
  | { kind: "tags"; tags: string[] }
  | { kind: "section"; keyword: string; text: string }
  | { kind: "step"; keyword: string; text: string }
  | { kind: "table"; rows: string[][] }
  | { kind: "doc"; text: string }
  | { kind: "comment"; text: string }
  | { kind: "text"; text: string };

/** "| a | b\|c |" -> ["a", "b|c"]. */
function tableCells(line: string): string[] {
  const inner = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return inner.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

export function parseGherkin(text: string): GBlock[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: GBlock[] = [];
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const t = raw.trim();
    if (!t) continue;
    if (t.startsWith('"""') || t.startsWith("```")) {
      const fence = t.slice(0, 3);
      const body: string[] = [];
      const indent = raw.length - raw.trimStart().length;
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) {
        body.push(lines[i].slice(Math.min(indent, lines[i].length - lines[i].trimStart().length)));
        i++;
      }
      out.push({ kind: "doc", text: body.join("\n") });
      continue;
    }
    if (t.startsWith("|")) {
      const rows: string[][] = [tableCells(t)];
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith("|")) rows.push(tableCells(lines[++i]));
      out.push({ kind: "table", rows });
      continue;
    }
    if (t.startsWith("#")) {
      out.push({ kind: "comment", text: t });
      continue;
    }
    if (t.startsWith("@")) {
      out.push({ kind: "tags", tags: t.split(/\s+/).filter((x) => x.startsWith("@")) });
      continue;
    }
    const sec = SECTION_KEYWORDS.find((k) => t.startsWith(`${k}:`));
    if (sec) {
      out.push({ kind: "section", keyword: sec, text: t.slice(sec.length + 1).trim() });
      continue;
    }
    const step = STEP_KEYWORDS.find((k) => t.startsWith(`${k} `)) ?? (t.startsWith("* ") ? "*" : undefined);
    if (step) {
      out.push({ kind: "step", keyword: step, text: t.slice(step.length).trim() });
      continue;
    }
    out.push({ kind: "text", text: t });
  }
  return out;
}

/** The Examples table of a Scenario Outline as the case's parameter table; null when none. */
export function examplesTable(text: string | null | undefined): ParamTable | null {
  if (!text) return null;
  const blocks = parseGherkin(text);
  let inExamples = false;
  let found: ParamTable | null = null;
  for (const b of blocks) {
    if (b.kind === "section") inExamples = b.keyword === "Examples" || b.keyword === "Scenarios";
    else if (b.kind === "table" && inExamples && b.rows.length) {
      const [head, ...rows] = b.rows;
      if (!found) found = { columns: head, rows };
      else if (found.columns.join("\u0000") === head.join("\u0000")) found = { columns: found.columns, rows: [...found.rows, ...rows] };
      inExamples = false;
    }
  }
  return found;
}

/** `<name>` and `{{name}}` marked as variables. */
function withVars(text: string): ReactNode {
  const parts = text.split(/(<[^<>\s]+>|\{\{\s*[^{}\s]+\s*\}\})/g);
  return parts.map((p, i) =>
    i % 2 ? (
      <span key={i} className="bdd-var">
        {p}
      </span>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    ),
  );
}

/** A scenario as read: keywords bold, data tables as tables, docstrings as pre, tags as chips. */
export function GherkinView({ text }: { text: string | null | undefined }) {
  const blocks = useMemo(() => parseGherkin(text ?? ""), [text]);
  if (!blocks.length) return <span className="muted">—</span>;
  return (
    <div className="bdd-view">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "tags":
            return (
              <div key={i} className="bdd-tags">
                {b.tags.map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
            );
          case "section":
            return (
              <div key={i} className={`bdd-section${b.keyword === "Feature" ? " feature" : ""}`}>
                <strong className="bdd-kw">{b.keyword}:</strong> {b.text}
              </div>
            );
          case "step":
            return (
              <div key={i} className="bdd-step">
                <strong className="bdd-kw">{b.keyword}</strong> {withVars(b.text)}
              </div>
            );
          case "table": {
            const [head, ...rows] = b.rows;
            return (
              <div key={i} className="table-wrap bdd-table">
                <table>
                  <thead>
                    <tr>
                      {head.map((c, j) => (
                        <th key={j}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, k) => (
                      <tr key={k}>
                        {r.map((c, j) => (
                          <td key={j}>{withVars(c)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }
          case "doc":
            return (
              <pre key={i} className="bdd-doc">
                {b.text}
              </pre>
            );
          case "comment":
            return (
              <div key={i} className="bdd-comment">
                {b.text}
              </div>
            );
          default:
            return (
              <div key={i} className="bdd-text">
                {withVars(b.text)}
              </div>
            );
        }
      })}
    </div>
  );
}

const BDD_PLACEHOLDER = `Scenario Outline: Đăng nhập với <role>
  Given người dùng mở trang đăng nhập
  When nhập email "<email>" và mật khẩu đúng
  Then thấy trang chủ của <role>

  Examples:
    | role  | email           |
    | admin | admin@vd.vn     |
    | agent | agent@vd.vn     |`;

/** Offset of the start of line `n` (1-based). */
function lineOffset(text: string, n: number): number {
  let at = 0;
  for (let i = 1; i < n; i++) {
    const nl = text.indexOf("\n", at);
    if (nl < 0) return text.length;
    at = nl + 1;
  }
  return at;
}

/** The scenario editor: monospace text, checked by the server 500 ms after the last keystroke. */
export function BddEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [check, setCheck] = useState<BddCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    const text = value;
    if (!text.trim()) {
      setCheck(null);
      setCheckError(null);
      setChecking(false);
      return;
    }
    const mine = ++seq.current;
    setChecking(true);
    const t = setTimeout(() => {
      api
        .post<BddCheck>("/testcases/bdd/check", { text })
        .then((got) => {
          if (seq.current !== mine) return;
          setCheck(got);
          setCheckError(null);
        })
        .catch((e) => {
          if (seq.current !== mine) return;
          setCheck(null);
          setCheckError(errorText(e));
        })
        .finally(() => {
          if (seq.current === mine) setChecking(false);
        });
    }, 500);
    return () => clearTimeout(t);
  }, [value]);

  const goTo = (line: number | null) => {
    const el = box.current;
    if (!el || !line) return;
    const start = lineOffset(value, line);
    const end = value.indexOf("\n", start);
    el.focus();
    el.setSelectionRange(start, end < 0 ? value.length : end);
  };

  const scenarios = check ? countOf(check.scenarios) : 0;
  return (
    <div className="bdd-editor">
      <div className="bdd-editor-bar">
        <span className="small muted">Gherkin tiếng Anh: Feature, Background, Scenario, Scenario Outline + Examples, Given / When / Then / And / But, bảng | … |, docstring """, tag @…</span>
        <button type="button" className="ghost sm" aria-pressed={preview} onClick={() => setPreview((p) => !p)}>
          <Icon name={preview ? "edit" : "book"} size={14} /> {preview ? "Soạn" : "Xem trước"}
        </button>
      </div>
      {preview ? (
        <div className="bdd-preview">
          <GherkinView text={value} />
        </div>
      ) : (
        <textarea
          ref={box}
          className="bdd-input"
          rows={12}
          value={value}
          spellCheck={false}
          aria-label="Kịch bản BDD (Gherkin)"
          aria-describedby="bdd-check-status"
          aria-invalid={!!check && !check.ok}
          placeholder={BDD_PLACEHOLDER}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // Tab indents inside the scenario instead of leaving the field (Shift+Tab still leaves).
            if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
              e.preventDefault();
              const el = e.currentTarget;
              const { selectionStart: a, selectionEnd: b } = el;
              const next = `${value.slice(0, a)}  ${value.slice(b)}`;
              onChange(next);
              requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
            }
          }}
        />
      )}
      <div id="bdd-check-status" className="bdd-check small" role="status" aria-live="polite">
        {!value.trim() ? (
          <span className="muted">Chưa có kịch bản.</span>
        ) : checking ? (
          <span className="muted">
            <span className="spinner inline" aria-hidden="true" /> Đang kiểm tra cú pháp…
          </span>
        ) : checkError ? (
          <span className="warn">
            <Icon name="alert" size={12} /> Không kiểm tra được: {checkError}
          </span>
        ) : check?.ok ? (
          <span className="good">
            <Icon name="checkCircle" size={12} /> Cú pháp hợp lệ{scenarios ? ` · ${scenarios} kịch bản` : ""}
            {check.outline_columns?.length ? ` · tham số: ${check.outline_columns.join(", ")}` : ""}
          </span>
        ) : check ? (
          <ul className="bdd-errors">
            {check.errors.map((er, i) => (
              <li key={i}>
                <Icon name="alertCircle" size={12} />{" "}
                {er.line ? (
                  <button type="button" className="link sm" onClick={() => goTo(er.line)}>
                    Dòng {er.line}
                  </button>
                ) : null}
                {er.line ? ": " : ""}
                {er.message}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------- Parameters (§15.15) ---------------- */

export const emptyParams = (): ParamTable => ({ columns: [], rows: [] });

/** A free column name: col1, col2, … */
function freeName(cols: string[]): string {
  for (let n = cols.length + 1; ; n++) if (!cols.includes(`col${n}`)) return `col${n}`;
}

/** Tab-separated text (a spreadsheet copy) as rows of cells. */
function parseTsv(text: string): string[][] {
  const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
  return lines.map((l) => l.split("\t"));
}

/** The table read-only: # and one column per parameter. */
export function ParamsView({ table, caption }: { table: ParamTable; caption?: ReactNode }) {
  if (!table?.columns?.length) return <span className="muted">—</span>;
  return (
    <div className="table-wrap prm-view">
      <table className="prm-table">
        {caption && <caption className="small muted">{caption}</caption>}
        <thead>
          <tr>
            <th className="num">#</th>
            {table.columns.map((c) => (
              <th key={c}>
                <code>{c}</code>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r, i) => (
            <tr key={i}>
              <td className="num">{i + 1}</td>
              {table.columns.map((c, j) => (
                <td key={c}>{r[j] ?? ""}</td>
              ))}
            </tr>
          ))}
          {!table.rows.length && (
            <tr>
              <td colSpan={table.columns.length + 1} className="small muted">
                Chưa có dòng nào.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The editable grid: rename / add / remove columns and rows; a block copied from a spreadsheet is
 * pasted from the cell it lands in (into a header cell: its first line names the columns).
 * `missing`: {{names}} used in the case that have no column (shown as errors).
 */
export function ParamsEditor({ value, onChange, missing }: { value: ParamTable; onChange: (t: ParamTable) => void; missing: string[] }) {
  const [note, setNote] = useState<string | null>(null);
  const problems = paramProblems(value);
  const width = value.columns.length;

  const setCell = (r: number, c: number, v: string) => onChange({ ...value, rows: value.rows.map((row, i) => (i === r ? row.map((x, j) => (j === c ? v : x)) : row)) });
  const setName = (c: number, v: string) => onChange({ ...value, columns: value.columns.map((x, j) => (j === c ? v : x)) });
  const addColumn = (name?: string) => {
    if (width >= PARAM_MAX_COLS) return;
    onChange({ columns: [...value.columns, name ?? freeName(value.columns)], rows: value.rows.map((r) => [...r, ""]) });
  };
  const removeColumn = (c: number) => onChange({ columns: value.columns.filter((_, j) => j !== c), rows: value.rows.map((r) => r.filter((_, j) => j !== c)) });
  const addRow = () => {
    if (value.rows.length >= PARAM_MAX_ROWS) return;
    onChange({ ...value, rows: [...value.rows, value.columns.map(() => "")] });
  };
  const removeRow = (r: number) => onChange({ ...value, rows: value.rows.filter((_, i) => i !== r) });

  /** Put `cells` at (r0, c0), growing the table within its limits. */
  const paste = (cells: string[][], r0: number, c0: number, header: string[] | null) => {
    const columns = [...value.columns];
    if (header) header.forEach((h, j) => (c0 + j < PARAM_MAX_COLS ? (columns[c0 + j] = h.trim().replace(/\s+/g, "_")) : null));
    const needCols = Math.min(PARAM_MAX_COLS, Math.max(columns.length, c0 + Math.max(0, ...cells.map((r) => r.length))));
    while (columns.length < needCols) columns.push(freeName(columns));
    const rows = value.rows.map((r) => [...r, ...Array(Math.max(0, needCols - r.length)).fill("")]);
    let cut = false;
    cells.forEach((line, i) => {
      const r = r0 + i;
      if (r >= PARAM_MAX_ROWS) {
        cut = true;
        return;
      }
      while (rows.length <= r) rows.push(columns.map(() => ""));
      line.forEach((v, j) => {
        const c = c0 + j;
        if (c >= PARAM_MAX_COLS) cut = true;
        else rows[r][c] = v.slice(0, PARAM_MAX_VALUE);
      });
    });
    onChange({ columns, rows });
    setNote(cut ? `Đã dán, phần vượt ${PARAM_MAX_COLS} cột / ${PARAM_MAX_ROWS} dòng bị bỏ.` : `Đã dán ${cells.length} dòng.`);
  };

  const onCellPaste = (r: number, c: number) => (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text/plain");
    if (!/[\t\n]/.test(text.replace(/\n+$/, ""))) return;
    e.preventDefault();
    paste(parseTsv(text), r, c, null);
  };
  const onHeadPaste = (c: number) => (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text/plain");
    if (!/[\t\n]/.test(text.replace(/\n+$/, ""))) return;
    e.preventDefault();
    const [head, ...rest] = parseTsv(text);
    paste(rest, 0, c, head);
  };
  const startFromPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const text = e.clipboardData.getData("text/plain");
    if (!text.trim()) return;
    e.preventDefault();
    const [head, ...rest] = parseTsv(text);
    paste(rest, 0, 0, head);
  };

  const badName = (c: string, j: number) => !PARAM_NAME.test(c) || value.columns.indexOf(c) !== j;
  return (
    <div className="prm-editor">
      <p className="small muted">
        Mỗi dòng tham số là một test khi đưa vào test run. Dùng <code>{"{{tên_cột}}"}</code> trong tiêu đề, tiền điều kiện, bước, kết quả mong đợi, dữ liệu test hoặc kịch bản BDD; tên cột gồm chữ không dấu, số, <code>_</code> (tối đa {PARAM_MAX_COLS} cột, {PARAM_MAX_ROWS} dòng). Dán một vùng copy từ Excel / Google Sheet vào ô bất kỳ để điền nhiều ô.
      </p>
      {!width ? (
        <div className="prm-start">
          <button type="button" className="sm" onClick={() => addColumn()}>
            <Icon name="plus" size={14} /> Thêm cột tham số
          </button>
          <label className="prm-paste">
            <span className="sr-only">Dán bảng từ bảng tính (dòng đầu là tên cột)</span>
            <textarea rows={1} placeholder="…hoặc dán bảng từ bảng tính vào đây (dòng đầu là tên cột)" value="" onChange={() => undefined} onPaste={startFromPaste} />
          </label>
        </div>
      ) : (
        <>
          <div className="table-wrap">
            <table className="prm-table prm-grid">
              <thead>
                <tr>
                  <th className="num" aria-label="Dòng">
                    #
                  </th>
                  {value.columns.map((c, j) => (
                    <th key={j}>
                      <span className="prm-head">
                        <input
                          value={c}
                          aria-label={`Tên cột ${j + 1}`}
                          aria-invalid={badName(c, j)}
                          className={badName(c, j) ? "invalid" : undefined}
                          maxLength={64}
                          onChange={(e) => setName(j, e.target.value)}
                          onPaste={onHeadPaste(j)}
                        />
                        <IconButton icon="close" size={12} label={`Xoá cột ${c || j + 1}`} onClick={() => removeColumn(j)} />
                      </span>
                    </th>
                  ))}
                  <th className="prm-actions" aria-label="Thao tác" />
                </tr>
              </thead>
              <tbody>
                {value.rows.map((row, i) => (
                  <tr key={i}>
                    <td className="num">{i + 1}</td>
                    {value.columns.map((c, j) => (
                      <td key={j}>
                        <input
                          value={row[j] ?? ""}
                          aria-label={`Dòng ${i + 1}, cột ${c || j + 1}`}
                          maxLength={PARAM_MAX_VALUE}
                          onChange={(e) => setCell(i, j, e.target.value)}
                          onPaste={onCellPaste(i, j)}
                        />
                      </td>
                    ))}
                    <td className="prm-actions">
                      <IconButton icon="remove" tone="danger" size={14} label={`Xoá dòng ${i + 1}`} className="tip-left" onClick={() => removeRow(i)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="tc-inline">
            <button type="button" className="sm" onClick={addRow} disabled={value.rows.length >= PARAM_MAX_ROWS}>
              <Icon name="plus" size={14} /> Thêm dòng
            </button>
            <button type="button" className="ghost sm" onClick={() => addColumn()} disabled={width >= PARAM_MAX_COLS}>
              <Icon name="plus" size={14} /> Thêm cột
            </button>
            <button type="button" className="ghost sm" onClick={() => onChange(emptyParams())}>
              Bỏ bảng tham số
            </button>
            <span className="small muted">
              {value.rows.length}/{PARAM_MAX_ROWS} dòng · {width}/{PARAM_MAX_COLS} cột
            </span>
          </div>
        </>
      )}
      {note && <p className="small muted" role="status">{note}</p>}
      {(problems.length > 0 || missing.length > 0) && (
        <ul className="prm-problems small" role="alert">
          {problems.map((p) => (
            <li key={p}>
              <Icon name="alertCircle" size={12} /> {p}
            </li>
          ))}
          {missing.map((m) => (
            <li key={m}>
              <Icon name="alertCircle" size={12} /> Biến <code>{`{{${m}}}`}</code> chưa có cột tham số
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------- .feature import (§15.12) ---------------- */

type FileOutcome = { name: string; result?: FeatureImportOut; error?: string };

const MAX_FEATURE = 2 * 1024 * 1024;

function errorLine(e: NonNullable<FeatureImportOut["errors"]>[number]): string {
  if (typeof e === "string") return e;
  return `${e.scenario ? `${e.scenario}: ` : ""}${e.line ? `dòng ${e.line}: ` : ""}${e.message ?? ""}`;
}

/** Upload .feature files into a suite: one request per file, then what each made. */
export function FeatureImportModal({
  suite,
  sections,
  initialSection,
  onClose,
  onDone,
}: {
  suite: Suite;
  sections: Section[] | undefined;
  initialSection: number | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [section, setSection] = useState(initialSection ? String(initialSection) : "");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<FileOutcome[] | null>(null);
  const [progress, setProgress] = useState(0);

  const run = async () => {
    setBusy(true);
    const out: FileOutcome[] = [];
    for (const [i, f] of files.entries()) {
      setProgress(i + 1);
      if (f.size > MAX_FEATURE) {
        out.push({ name: f.name, error: "File lớn hơn 2 MB" });
        continue;
      }
      const form = new FormData();
      form.append("file", f, f.name);
      form.append("suite_id", String(suite.id));
      if (section) form.append("section_id", section);
      try {
        out.push({ name: f.name, result: await api.postForm<FeatureImportOut>("/testcases/import/feature", form) });
      } catch (e) {
        out.push({ name: f.name, error: errorText(e) });
      }
    }
    setDone(out);
    setBusy(false);
    if (out.some((o) => o.result)) onDone();
  };

  const total = (k: "created" | "updated" | "sections") => (done ?? []).reduce((n, o) => n + countOf(o.result?.[k]), 0);
  return (
    <Modal
      title={`Nhập .feature vào bộ “${suite.name}”`}
      onClose={onClose}
      busy={busy}
      width={640}
      footer={
        done ? (
          <button type="button" className="primary" onClick={onClose}>
            Xong
          </button>
        ) : (
          <>
            <button type="button" className="ghost" onClick={onClose} disabled={busy}>
              Huỷ
            </button>
            <button type="button" className="primary" onClick={() => void run()} disabled={busy || !files.length}>
              {busy ? `Đang nhập ${progress}/${files.length}…` : `Nhập ${files.length || ""} file`}
            </button>
          </>
        )
      }
    >
      {!done ? (
        <div className="adm-form">
          <label className="tc-file">
            <Icon name="upload" size={16} />
            <span>{files.length ? `${files.length} file đã chọn` : "Chọn file .feature (nhiều file, mỗi file tối đa 2 MB)"}</span>
            <input
              type="file"
              accept=".feature,text/plain"
              multiple
              onChange={(e) => {
                setFiles(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
          </label>
          {files.length > 0 && (
            <ul className="tc-tags">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="tag">
                  {f.name}
                  {f.size > MAX_FEATURE ? " (quá 2 MB)" : ""}
                  <button type="button" className="link sm" aria-label={`Bỏ ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label className="adm-field">
            <span className="small muted">Đặt thư mục Feature vào</span>
            <select value={section} onChange={(e) => setSection(e.target.value)} disabled={!sections}>
              <SectionOptions sections={sections ?? []} />
            </select>
          </label>
          <p className="small muted">
            Mỗi <code>Feature</code> thành một thư mục (tạo nếu chưa có), mỗi <code>Scenario</code> / <code>Scenario Outline</code> một testcase BDD. Tag <code>@C12</code> cập nhật đúng testcase C12 của bộ; tag khác thành nhãn; <code>Background</code> chép vào đầu kịch bản.
          </p>
        </div>
      ) : (
        <div className="bdd-import-sum">
          <p>
            Tạo <strong>{total("created")}</strong> · cập nhật <strong>{total("updated")}</strong> testcase
            {total("sections") ? ` · ${total("sections")} thư mục` : ""} từ {done.length} file.
          </p>
          <ul className="bdd-import-files">
            {done.map((o, i) => (
              <li key={i}>
                <div className="tc-inline">
                  <strong>{o.name}</strong>
                  {o.error ? (
                    <Pill tone="bad">Lỗi</Pill>
                  ) : (
                    <span className="small muted">
                      tạo {countOf(o.result?.created)} · cập nhật {countOf(o.result?.updated)}
                    </span>
                  )}
                </div>
                {o.error && <div className="small bad">{o.error}</div>}
                {(o.result?.errors ?? []).length > 0 && (
                  <ul className="prm-problems small">
                    {(o.result?.errors ?? []).map((e, k) => (
                      <li key={k}>
                        <Icon name="alertCircle" size={12} /> {errorLine(e)}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}

/** The .feature export of a suite (or of a section): one file, or a zip of one per section. */
export const featureExportHref = (suiteId: number, sectionId?: number | null) =>
  `/api/v1/testcases/export/feature?suite_id=${suiteId}${sectionId ? `&section_id=${sectionId}` : ""}`;

/* ---------------- AI conversion (kind "convert") ---------------- */

export async function askConvert(projectId: number, suiteId: number, caseIds: number[], direction: ConvertDirection): Promise<AiRun> {
  return api.post<AiRun>("/testcases/ai", { kind: "convert", business_project_id: projectId, suite_id: suiteId, case_ids: caseIds, direction });
}

/** A case's scenario / steps as shown in a before / after comparison. */
function CaseBody({
  template,
  bdd,
  stepsList,
  steps,
  expected,
  preconditions,
}: {
  template: string | undefined;
  bdd?: string | null;
  stepsList?: (StepItem | { content: string; expected?: string | null })[] | null;
  steps?: string | null;
  expected?: string | null;
  preconditions?: string | null;
}) {
  return (
    <div className="bdd-body">
      {preconditions ? (
        <div className="small">
          <span className="muted">Tiền điều kiện:</span> {preconditions}
        </div>
      ) : null}
      {template === "bdd" ? (
        <GherkinView text={bdd} />
      ) : stepsList && stepsList.length ? (
        <ol className="bdd-steps small">
          {stepsList.map((s, i) =>
            "shared" in s ? (
              <li key={i} className="muted">
                Bước dùng chung SS{s.shared}
              </li>
            ) : (
              <li key={i}>
                {s.content}
                {s.expected ? <span className="muted"> → {s.expected}</span> : null}
              </li>
            ),
          )}
        </ol>
      ) : (
        <>
          {steps ? <div className="tc-lines small">{steps}</div> : <span className="muted">—</span>}
          {expected ? (
            <div className="small">
              <span className="muted">Kết quả mong đợi:</span> {expected}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

/** Proposals of a "convert" run: current form beside the proposed one; tick and apply (with undo). */
export function ConvertReview({ run, onApplied }: { run: AiRun; onApplied: () => void }) {
  const proposals = useMemo<ConvertProposal[]>(() => run.result?.proposals ?? [], [run]);
  const cases = useApi<CaseList>(proposals.length && run.suite_id ? `/testcases?business_project_id=${run.business_project_id}&suite_id=${run.suite_id}&limit=500` : null);
  const byId = useMemo(() => new Map<number, TestCase>((cases.data?.items ?? []).map((c) => [c.id, c])), [cases.data]);
  const applied = useMemo(() => new Set(run.result?.applied ?? []), [run]);
  const done = run.status === "applied";
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<UndoNote | null>(null);
  const direction = run.result?.direction ?? run.input.direction;

  useEffect(() => {
    setPicked(new Set(proposals.map((p) => p.case_id).filter((id) => !applied.has(id))));
  }, [proposals, applied]);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await api.post<BulkOut>(`/testcases/ai/${run.id}/apply-convert`, { case_ids: [...picked] });
      setNote(bulkNote(got, `Đã chuyển ${got.changed} testcase`));
      setPicked(new Set());
      onApplied();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="bdd-convert-title">
      <div className="tc-proposals-head">
        <h3 className="tc-h3" id="bdd-convert-title">
          {direction ? CONVERT_LABEL[direction] : "Đề xuất chuyển dạng"} ({proposals.length})
        </h3>
        {proposals.length > 0 && !done && (
          <>
            <button
              type="button"
              className="link sm"
              onClick={() => setPicked(picked.size ? new Set() : new Set(proposals.map((p) => p.case_id).filter((id) => !applied.has(id))))}
            >
              {picked.size ? "Bỏ chọn tất cả" : "Chọn tất cả"}
            </button>
            <button type="button" className="primary sm" onClick={() => void apply()} disabled={busy || !picked.size}>
              {busy ? "Đang áp dụng…" : `Áp dụng ${picked.size} testcase`}
            </button>
          </>
        )}
      </div>
      {error && (
        <Alert tone="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <UndoNotice note={note} onClose={() => setNote(null)} onUndone={onApplied} />
      {done && !note && <Alert tone="info">Đã áp dụng lượt này. Hoàn tác ở “Lịch sử thao tác hàng loạt” (trong 24 giờ).</Alert>}
      {cases.error && <Alert tone="warning">Không tải được nội dung hiện tại ({cases.error.message}); vẫn áp dụng được.</Alert>}
      {!proposals.length ? (
        <p className="small muted">AI chưa đề xuất chuyển testcase nào.</p>
      ) : (
        <ol className="bdd-convert-list">
          {proposals.map((p) => {
            const c = byId.get(p.case_id);
            const isDone = applied.has(p.case_id);
            return (
              <li key={p.case_id} className={isDone ? "done" : undefined}>
                <label className="inline-check bdd-convert-head">
                  <input
                    type="checkbox"
                    disabled={done || isDone}
                    checked={isDone || picked.has(p.case_id)}
                    onChange={(e) => {
                      const next = new Set(picked);
                      if (e.target.checked) next.add(p.case_id);
                      else next.delete(p.case_id);
                      setPicked(next);
                    }}
                  />
                  <span className="tc-key">{c ? caseKey(c) : `C${p.case_id}`}</span>
                  <span>{c?.title ?? ""}</span>
                  {isDone && <Pill tone="good">Đã chuyển</Pill>}
                </label>
                <div className="bdd-diff">
                  <div>
                    <div className="small muted bdd-diff-label">Hiện tại · {c?.template ? TEMPLATE_LABEL[c.template] ?? c.template : "…"}</div>
                    {c ? (
                      <CaseBody template={c.template} bdd={c.bdd} stepsList={c.steps_list} steps={c.steps} expected={c.expected} preconditions={c.preconditions} />
                    ) : cases.data || cases.error ? (
                      <span className="small muted">Không thấy testcase trong bộ.</span>
                    ) : (
                      <Loading rows={2} />
                    )}
                  </div>
                  <div>
                    <div className="small muted bdd-diff-label">Đề xuất · {TEMPLATE_LABEL[p.template] ?? p.template}</div>
                    <CaseBody template={p.template} bdd={p.bdd} stepsList={p.steps_list} preconditions={p.preconditions} />
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
