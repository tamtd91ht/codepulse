"use client";

import { type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode, createContext, useContext, useRef, useState } from "react";

/**
 * The document renderer (docs/specs/29 §5): Markdown as project documents use it, built as React
 * elements, never raw HTML, so an uploaded file cannot inject markup or script.
 *
 * Blocks: headings (# … ######, with anchors for the table of contents), paragraphs, nested
 * lists (- * + and 1.), task lists (- [ ] / - [x]), quotes, callouts (> [!NOTE] / [!TIP] /
 * [!IMPORTANT] / [!WARNING] / [!CAUTION]), fenced code (``` and ~~~, language label, copy
 * button), tables with alignment (| a | b | / |:--|--:|), rules (---), images (![alt](https…))
 * and the page markers of an imported PDF ("--- Trang N ---").
 * Inline: `code`, **bold**, *italic* / _italic_, ~~strike~~, [links](…) to http(s), mailto, an
 * anchor (#…) or a path of this site, bare https links. A search term is highlighted.
 */

export type OutlineItem = { level: number; text: string; id: string };

type ListItem = { text: string; task: boolean | null; children: Block[] };
type Block =
  | { t: "h"; level: number; text: string; id: string }
  | { t: "p"; text: string }
  | { t: "list"; ordered: boolean; start: number; items: ListItem[] }
  | { t: "quote"; kind: CalloutKind | null; blocks: Block[] }
  | { t: "code"; lang: string; text: string }
  | { t: "table"; head: string[]; align: ("left" | "center" | "right" | null)[]; rows: string[][] }
  | { t: "hr" }
  | { t: "page"; n: number }
  | { t: "img"; alt: string; src: string };

type CalloutKind = "note" | "tip" | "important" | "warning" | "caution";
const CALLOUT_LABEL: Record<CalloutKind, string> = {
  note: "Ghi chú",
  tip: "Mẹo",
  important: "Quan trọng",
  warning: "Cảnh báo",
  caution: "Thận trọng",
};

const UL = /^(\s*)[-*+]\s+(.*)$/;
const OL = /^(\s*)(\d+)[.)]\s+(.*)$/;
const FENCE = /^\s*(```+|~~~+)\s*([\w+#.-]*)/;
const PAGE = /^-{3}\s*Trang\s+(\d+)\s*-{3}$/;
const IMAGE = /^!\[([^\]]*)\]\((https?:\/\/[^\s)]+|\/[^\s)]*|asset:\d+)\)\s*$/;

/** A heading's anchor: accent-free, lower case, words joined by "-"; unique within the page. */
export function slugify(text: string, seen?: Map<string, number>): string {
  const base =
    text
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/đ/g, "d")
      .replace(/Đ/g, "D")
      .toLowerCase()
      .replace(/[`*_~[\]()]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "muc";
  if (!seen) return base;
  const n = seen.get(base) ?? 0;
  seen.set(base, n + 1);
  return n ? `${base}-${n}` : base;
}

function isTableSep(line: string): boolean {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line) && line.includes("-");
}

function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out: string[] = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") {
      cur += "|";
      i++;
    } else if (s[i] === "|") {
      out.push(cur.trim());
      cur = "";
    } else cur += s[i];
  }
  out.push(cur.trim());
  return out;
}

function startsBlock(line: string): boolean {
  return (
    FENCE.test(line) ||
    /^#{1,6}\s/.test(line) ||
    /^\s*>/.test(line) ||
    UL.test(line) ||
    OL.test(line) ||
    /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line) ||
    PAGE.test(line.trim())
  );
}

function parse(lines: string[], seen: Map<string, number>): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence) {
      const close = fence[1][0];
      const body: string[] = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s*${close === "`" ? "```" : "~~~"}`).test(lines[i])) body.push(lines[i++]);
      i++;
      out.push({ t: "code", lang: fence[2].toLowerCase(), text: body.join("\n") });
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const page = PAGE.exec(line.trim());
    if (page) {
      out.push({ t: "page", n: Number(page[1]) });
      i++;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) {
      out.push({ t: "h", level: heading[1].length, text: heading[2], id: slugify(heading[2], seen) });
      i++;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push({ t: "hr" });
      i++;
      continue;
    }
    const img = IMAGE.exec(line.trim());
    if (img) {
      out.push({ t: "img", alt: img[1], src: img[2] });
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      const marker = /^\[!(note|tip|important|warning|caution)\]\s*$/i.exec(body[0]?.trim() ?? "");
      const kind = marker ? (marker[1].toLowerCase() as CalloutKind) : null;
      out.push({ t: "quote", kind, blocks: parse(marker ? body.slice(1) : body, seen) });
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) =>
        c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null,
      );
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(cells(lines[i++]));
      out.push({ t: "table", head, align, rows });
      continue;
    }
    if (UL.test(line) || OL.test(line)) {
      const [block, next] = parseList(lines, i, seen);
      out.push(block);
      i = next;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && (para.length === 0 || !startsBlock(lines[i]))) {
      if (para.length && lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) break;
      para.push(lines[i++]);
    }
    out.push({ t: "p", text: para.join("\n") });
  }
  return out;
}

function indentOf(line: string): number {
  return (/^(\s*)/.exec(line)?.[1] ?? "").replace(/\t/g, "    ").length;
}

/** A list starting at `start`; items deeper than the first one's indent become nested blocks. */
function parseList(lines: string[], start: number, seen: Map<string, number>): [Block, number] {
  const first = lines[start];
  const ordered = !UL.test(first) && OL.test(first);
  const base = indentOf(first);
  const items: ListItem[] = [];
  let i = start;
  const startNum = ordered ? Number(OL.exec(first)?.[2] ?? 1) : 1;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      // A blank line ends the list unless the next line continues it (same or deeper indent).
      const next = lines[i + 1];
      if (next && (indentOf(next) > base || ((ordered ? OL : UL).test(next) && indentOf(next) === base))) {
        i++;
        continue;
      }
      break;
    }
    const m = ordered ? OL.exec(line) : UL.exec(line);
    const indent = indentOf(line);
    if (m && indent === base) {
      let text = ordered ? m[3] : m[2];
      let task: boolean | null = null;
      const box = /^\[([ xX])\]\s+(.*)$/.exec(text);
      if (box) {
        task = box[1] !== " ";
        text = box[2];
      }
      items.push({ text, task, children: [] });
      i++;
      continue;
    }
    if (indent > base && items.length) {
      // Deeper lines belong to the last item: a nested list, or a continuation of its text.
      const sub: string[] = [];
      while (i < lines.length && (indentOf(lines[i]) > base || !lines[i].trim())) {
        if (!lines[i].trim() && !(lines[i + 1] && indentOf(lines[i + 1]) > base)) break;
        sub.push(lines[i].slice(Math.min(indentOf(lines[i]), base + 2)));
        i++;
      }
      const last = items[items.length - 1];
      if (sub.length && !UL.test(sub[0]) && !OL.test(sub[0]) && !FENCE.test(sub[0]) && last.children.length === 0) {
        last.text += `\n${sub.shift()!.trim()}`;
      }
      last.children.push(...parse(sub, seen));
      continue;
    }
    break;
  }
  return [{ t: "list", ordered, start: startNum, items }, i];
}

export function parseDoc(src: string): Block[] {
  return parse(src.replace(/\r\n?/g, "\n").split("\n"), new Map());
}

/** The headings of a document, for its table of contents (same anchors as the rendering). */
export function docOutline(src: string): OutlineItem[] {
  const out: OutlineItem[] = [];
  const walk = (blocks: Block[]) => {
    for (const b of blocks) {
      if (b.t === "h") out.push({ level: b.level, text: plainText(b.text), id: b.id });
      else if (b.t === "quote") walk(b.blocks);
    }
  };
  walk(parseDoc(src));
  return out;
}

/** Inline Markdown reduced to its words (TOC entries, search snippets). */
export function plainText(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/~~([^~]+)~~/g, "$1")
    .replace(/(\*|_)([^*_]+)\1/g, "$2")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1");
}

/**
 * Where an image may be loaded from (docs/specs/29 §13): the document's own images
 * (`asset:<id>`, served by the API to whoever may read the dự án) and same-site paths. An
 * image on another site is not loaded (it would tell that site who reads the document, and a
 * crafted address could carry its text away): it is shown as a link to open by hand.
 */
function imageSrc(src: string): string | null {
  const asset = /^asset:(\d+)$/.exec(src.trim());
  if (asset) return `/api/v1/docs/assets/${asset[1]}`;
  if (src.startsWith("/") && !src.startsWith("//")) return src;
  return null;
}

/**
 * An image's size, written after its name (docs/specs/29 §13.1): `![Sơ đồ|50%](asset:12)` is
 * half the page width, `![Sơ đồ|480](asset:12)` 480 px (never wider than the page). Without
 * one the image keeps its own size. Other Markdown readers just show "Sơ đồ|50%" as its text.
 */
export function imageSize(alt: string): { alt: string; size: string | null; style?: CSSProperties } {
  const m = /^(.*?)\|\s*(\d{1,4})\s*(px|%)?\s*$/.exec(alt);
  if (!m) return { alt, size: null };
  const name = m[1].trim();
  const n = Number(m[2]);
  if (m[3] === "%") return n >= 5 && n <= 100 ? { alt: name, size: `${n}%`, style: { width: `${n}%` } } : { alt: name, size: null };
  return n >= 16 && n <= 2000 ? { alt: name, size: `${n}`, style: { width: `${n}px` } } : { alt: name, size: null };
}

/**
 * The editor's preview gives images a corner handle (docs/specs/29 §13.2): dragging it resizes
 * the image live; on release the width, as a share of the page rounded to 5%, is written back
 * into the image's line through `onResize`. Outside the editor there is no handle.
 */
export const ImageResize = createContext<((mdSrc: string, size: string) => void) | null>(null);

const STEP = 5; // %

function ResizableImage({ src, alt, style, mdSrc }: { src: string; alt: string; style?: CSSProperties; mdSrc: string }) {
  const onResize = useContext(ImageResize);
  const img = useRef<HTMLImageElement>(null);
  const [live, setLive] = useState<number | null>(null); // % while dragging
  const drag = useRef<{ x: number; width: number; box: number; centered: boolean } | null>(null);
  if (!onResize) return <img src={src} alt={alt} style={style} data-md-src={mdSrc} loading="lazy" referrerPolicy="no-referrer" />;

  const box = () => img.current?.closest("figure")?.clientWidth || img.current?.parentElement?.clientWidth || 1;
  const percent = (width: number, of: number) => Math.min(100, Math.max(STEP, Math.round((width / of) * 100 / STEP) * STEP));
  const current = () => (img.current ? percent(img.current.getBoundingClientRect().width, box()) : 50);

  const down = (e: PointerEvent<HTMLSpanElement>) => {
    if (!img.current) return;
    e.preventDefault();
    e.stopPropagation();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* a pointer the browser does not track: moves still reach the handle while over it */
    }
    const figure = img.current.closest("figure");
    drag.current = {
      x: e.clientX,
      width: img.current.getBoundingClientRect().width,
      box: box(),
      // A centred image grows on both sides: the handle moves half as far as the width grows.
      centered: figure ? getComputedStyle(figure).textAlign === "center" : false,
    };
    setLive(current());
  };
  const move = (e: PointerEvent<HTMLSpanElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = (e.clientX - d.x) * (d.centered ? 2 : 1);
    const width = Math.min(d.box, Math.max(40, d.width + dx));
    setLive(Math.min(100, Math.max(STEP, Math.round((width / d.box) * 100))));
  };
  const up = (e: PointerEvent<HTMLSpanElement>) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* not captured */
    }
    const dx = (e.clientX - d.x) * (d.centered ? 2 : 1);
    const width = Math.min(d.box, Math.max(40, d.width + dx));
    setLive(null);
    if (Math.abs(dx) > 2) onResize(mdSrc, `${percent(width, d.box)}%`);
  };
  const key = (e: KeyboardEvent<HTMLSpanElement>) => {
    const by = e.key === "ArrowRight" || e.key === "ArrowUp" ? STEP : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -STEP : 0;
    if (!by) return;
    e.preventDefault();
    onResize(mdSrc, `${Math.min(100, Math.max(STEP, current() + by))}%`);
  };

  return (
    <span className={`doc-img-resize${live !== null ? " dragging" : ""}`} style={live !== null ? { width: `${live}%` } : style ? { width: style.width } : undefined}>
      <img ref={img} src={src} alt={alt} style={live !== null || style ? { width: "100%" } : undefined} data-md-src={mdSrc} loading="lazy" referrerPolicy="no-referrer" />
      {live !== null && <span className="doc-img-size">{live}%</span>}
      <span
        className="doc-img-handle"
        role="slider"
        tabIndex={0}
        aria-label={`Kéo để đổi cỡ ảnh ${alt}`.trim()}
        aria-valuemin={STEP}
        aria-valuemax={100}
        aria-valuenow={live ?? undefined}
        aria-valuetext={live !== null ? `${live}%` : "kéo hoặc dùng phím mũi tên"}
        title="Kéo để đổi cỡ (mũi tên trái/phải: ±5%)"
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={() => {
          drag.current = null;
          setLive(null);
        }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={key}
      />
    </span>
  );
}

function ImageLink({ src, alt }: { src: string; alt: string }) {
  const href = safeHref(src);
  return href && /^https?:/i.test(href) ? (
    <a href={href} target="_blank" rel="noopener noreferrer" className="doc-image-link" title="Ảnh ở trang khác: không tự tải, bấm để mở">
      🖼 {alt || "Ảnh"}
    </a>
  ) : (
    <>{alt}</>
  );
}

function safeHref(href: string): string | null {
  if (/^(https?:|mailto:)/i.test(href)) return href;
  if (href.startsWith("#")) return href;
  if (href.startsWith("/") && !href.startsWith("//")) return href;
  return null;
}

/** Accent-free lower case, as the server's search folds text (đ -> d). */
export function fold(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "d").toLowerCase();
}

function Highlight({ text, term }: { text: string; term?: string }) {
  const q = term?.trim();
  if (!q || q.length < 2) return <>{text}</>;
  // Each character folded on its own (accents dropped, đ -> d, lower case) so a match in the
  // folded text maps back to the same characters of the original.
  const chars = Array.from(text);
  const hay = chars.map((ch) => fold(ch).slice(0, 1) || ch).join("");
  const needle = fold(q);
  if (!needle) return <>{text}</>;
  const parts: ReactNode[] = [];
  let from = 0;
  let key = 0;
  for (let at = hay.indexOf(needle); at !== -1; at = hay.indexOf(needle, at + needle.length)) {
    if (at > from) parts.push(chars.slice(from, at).join(""));
    parts.push(<mark key={key++}>{chars.slice(at, at + needle.length).join("")}</mark>);
    from = at + needle.length;
  }
  if (from < chars.length) parts.push(chars.slice(from).join(""));
  return <>{parts}</>;
}

const INLINE =
  /(`+)([\s\S]*?[^`])\1(?!`)|\*\*([^*]+?)\*\*|__([^_]+?)__|~~([^~]+?)~~|(?<![\w*])\*([^*\s][^*]*?)\*(?!\w)|(?<![\w_])_([^_\s][^_]*?)_(?!\w)|!\[([^\]]*)\]\(([^)\s]+)\)|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|(\n)/g;

export function Inline({ text, term }: { text: string; term?: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  let key = 0;
  INLINE.lastIndex = 0;
  for (let m = INLINE.exec(text); m; m = INLINE.exec(text)) {
    if (m.index > last) parts.push(<Highlight key={key++} text={text.slice(last, m.index)} term={term} />);
    if (m[2] !== undefined) parts.push(<code key={key++}>{m[2].trim()}</code>);
    else if (m[3] !== undefined || m[4] !== undefined)
      parts.push(
        <strong key={key++}>
          <Inline text={m[3] ?? m[4]} term={term} />
        </strong>,
      );
    else if (m[5] !== undefined)
      parts.push(
        <del key={key++}>
          <Inline text={m[5]} term={term} />
        </del>,
      );
    else if (m[6] !== undefined || m[7] !== undefined)
      parts.push(
        <em key={key++}>
          <Inline text={m[6] ?? m[7]} term={term} />
        </em>,
      );
    else if (m[9] !== undefined) {
      const src = imageSrc(m[9]);
      const sized = imageSize(m[8]);
      parts.push(
        src ? (
          <img key={key++} src={src} alt={sized.alt} style={sized.style} data-md-src={m[9]} loading="lazy" referrerPolicy="no-referrer" />
        ) : (
          <ImageLink key={key++} src={m[9]} alt={sized.alt} />
        ),
      );
    } else if (m[11] !== undefined) {
      const href = safeHref(m[11]);
      const external = href ? /^https?:/i.test(href) : false;
      parts.push(
        href ? (
          <a key={key++} href={href} {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
            <Inline text={m[10]} term={term} />
          </a>
        ) : (
          <Inline key={key++} text={m[10]} term={term} />
        ),
      );
    } else if (m[12] !== undefined)
      parts.push(
        <a key={key++} href={m[12]} target="_blank" rel="noopener noreferrer">
          {m[12]}
        </a>,
      );
    else parts.push(<br key={key++} />);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<Highlight key={key++} text={text.slice(last)} term={term} />);
  return <>{parts}</>;
}

function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="doc-code">
      <div className="doc-code-bar">
        <span>{lang || "text"}</span>
        <button
          type="button"
          className="ghost sm"
          onClick={() => {
            void navigator.clipboard?.writeText(text).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? "Đã chép" : "Chép"}
        </button>
      </div>
      <pre>
        <code>{text}</code>
      </pre>
    </div>
  );
}

function Blocks({ blocks, term, offset }: { blocks: Block[]; term?: string; offset: number }) {
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.t) {
          case "h": {
            const level = Math.min(6, b.level + offset) as 1 | 2 | 3 | 4 | 5 | 6;
            const Tag = `h${level}` as const;
            return (
              <Tag key={i} id={b.id} className="doc-h">
                <Inline text={b.text} term={term} />
                <a className="doc-anchor" href={`#${b.id}`} aria-label="Liên kết tới mục này">
                  #
                </a>
              </Tag>
            );
          }
          case "p":
            return (
              <p key={i}>
                <Inline text={b.text} term={term} />
              </p>
            );
          case "list": {
            const Tag = b.ordered ? "ol" : "ul";
            const tasks = b.items.some((it) => it.task !== null);
            return (
              <Tag key={i} start={b.ordered && b.start !== 1 ? b.start : undefined} className={tasks ? "doc-tasks" : undefined}>
                {b.items.map((it, j) => (
                  <li key={j} className={it.task !== null ? "doc-task" : undefined}>
                    {it.task !== null && <input type="checkbox" checked={it.task} readOnly disabled aria-label={it.task ? "Đã xong" : "Chưa xong"} />}
                    <Inline text={it.text} term={term} />
                    {it.children.length > 0 && <Blocks blocks={it.children} term={term} offset={offset} />}
                  </li>
                ))}
              </Tag>
            );
          }
          case "quote":
            return b.kind ? (
              <div key={i} className={`doc-callout ${b.kind}`} role="note">
                <div className="doc-callout-title">{CALLOUT_LABEL[b.kind]}</div>
                <Blocks blocks={b.blocks} term={term} offset={offset} />
              </div>
            ) : (
              <blockquote key={i}>
                <Blocks blocks={b.blocks} term={term} offset={offset} />
              </blockquote>
            );
          case "code":
            return <CodeBlock key={i} lang={b.lang} text={b.text} />;
          case "table":
            return (
              <div key={i} className="table-wrap doc-table">
                <table>
                  <thead>
                    <tr>
                      {b.head.map((c, j) => (
                        <th key={j} style={b.align[j] ? { textAlign: b.align[j]! } : undefined}>
                          <Inline text={c} term={term} />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>
                        {b.head.map((_, k) => (
                          <td key={k} style={b.align[k] ? { textAlign: b.align[k]! } : undefined}>
                            <Inline text={r[k] ?? ""} term={term} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={i} />;
          case "page":
            return (
              <div key={i} className="doc-page" id={`trang-${b.n}`} role="separator" aria-label={`Trang ${b.n}`}>
                <span>Trang {b.n}</span>
              </div>
            );
          case "img": {
            const src = imageSrc(b.src);
            const sized = imageSize(b.alt);
            return src ? (
              <figure key={i} className={`doc-figure${sized.size ? " sized" : ""}`}>
                <ResizableImage src={src} alt={sized.alt} style={sized.style} mdSrc={b.src} />
                {sized.alt && <figcaption>{sized.alt}</figcaption>}
              </figure>
            ) : (
              <p key={i}>
                <ImageLink src={b.src} alt={sized.alt} />
              </p>
            );
          }
        }
      })}
    </>
  );
}

/**
 * A rendered document. `offset` lowers every heading level (1 inside a page whose own title is
 * the h1); `term` highlights a search.
 */
export function DocMarkdown({
  text,
  term,
  offset = 1,
  className,
  dropTitle,
}: {
  text: string;
  term?: string;
  offset?: number;
  className?: string;
  /** The page's own title: a first "# heading" saying the same is not shown again. */
  dropTitle?: string;
}) {
  let blocks = parseDoc(text);
  const first = blocks[0];
  if (dropTitle && first?.t === "h" && first.level === 1 && fold(plainText(first.text)) === fold(dropTitle)) blocks = blocks.slice(1);
  return (
    <div className={`doc-body${className ? ` ${className}` : ""}`}>
      <Blocks blocks={blocks} term={term} offset={offset} />
    </div>
  );
}
