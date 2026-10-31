import { Fragment, type ReactNode } from "react";

/**
 * A small Markdown renderer for Stetho's answers. It builds React elements, never raw HTML, so an
 * answer cannot inject markup. Headings, paragraphs, lists (- * + / 1.), quotes (>), ``` blocks,
 * `code`, **bold**, *italic*, [links](http…) and rules (---); tables show as preformatted text.
 * Enough for an assistant's answer; not CommonMark.
 */

type Block =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string }
  | { t: "ul" | "ol"; items: string[] }
  | { t: "quote"; text: string }
  | { t: "code"; lang: string; text: string }
  | { t: "hr" };

const LIST_UL = /^\s*[-*+]\s+(.*)$/;
const LIST_OL = /^\s*\d+[.)]\s+(.*)$/;

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r/g, "").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = /^\s*```(\S*)/.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++; // the closing ``` (or the end of the text while it streams)
      out.push({ t: "code", lang: fence[1], text: body.join("\n") });
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      out.push({ t: "h", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      out.push({ t: "hr" });
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ t: "quote", text: body.join("\n") });
      continue;
    }
    if (/^\s*\|/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) body.push(lines[i++]);
      out.push({ t: "code", lang: "table", text: body.join("\n") });
      continue;
    }
    const kind = LIST_UL.test(line) ? "ul" : LIST_OL.test(line) ? "ol" : null;
    if (kind) {
      const re = kind === "ul" ? LIST_UL : LIST_OL;
      const items: string[] = [];
      while (i < lines.length) {
        const m = re.exec(lines[i]);
        if (m) {
          items.push(m[1]);
          i++;
        } else if (items.length && /^\s{2,}\S/.test(lines[i])) {
          items[items.length - 1] += `\n${lines[i].trim()}`; // an indented continuation line
          i++;
        } else break;
      }
      out.push({ t: kind, items });
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*(```|#{1,6}\s|>|\|)/.test(lines[i]) &&
      !LIST_UL.test(lines[i]) &&
      !LIST_OL.test(lines[i])
    )
      para.push(lines[i++]);
    out.push({ t: "p", text: para.join("\n") });
  }
  return out;
}

/** Inline: `code`, **bold**, *italic* / _italic_, [text](http…), line breaks. */
function Inline({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))|(\n)/g;
  let last = 0;
  let key = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const token = m[0];
    if (m[1]) parts.push(<code key={key++}>{token.slice(1, -1)}</code>);
    else if (m[2]) parts.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    else if (m[3]) parts.push(<em key={key++}>{token.slice(1, -1)}</em>);
    else if (m[4])
      parts.push(
        <a key={key++} href={m[5]} target="_blank" rel="noopener noreferrer">
          {token.slice(1, token.indexOf("]("))}
        </a>,
      );
    else parts.push(<br key={key++} />);
    last = m.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      {parseBlocks(text).map((b, i) => {
        switch (b.t) {
          case "h": {
            // # -> h3: the answer sits inside a chat panel, no page-sized headings.
            const Tag = `h${Math.min(6, b.level + 2)}` as "h3";
            return (
              <Tag key={i}>
                <Inline text={b.text} />
              </Tag>
            );
          }
          case "p":
            return (
              <p key={i}>
                <Inline text={b.text} />
              </p>
            );
          case "ul":
          case "ol": {
            const Tag = b.t;
            return (
              <Tag key={i}>
                {b.items.map((item, j) => (
                  <li key={j}>
                    <Inline text={item} />
                  </li>
                ))}
              </Tag>
            );
          }
          case "quote":
            return (
              <blockquote key={i}>
                <Inline text={b.text} />
              </blockquote>
            );
          case "code":
            return (
              <Fragment key={i}>
                <pre className={b.lang === "table" ? "md-table" : undefined}>
                  <code>{b.text}</code>
                </pre>
              </Fragment>
            );
          case "hr":
            return <hr key={i} />;
        }
      })}
    </div>
  );
}
