/*
 * Rich text pasted into the document editor (docs/specs/29 §18): Google Docs, Google Sheets, web
 * pages and Word put HTML on the clipboard next to the plain text, and only the HTML keeps the
 * links. It becomes Markdown: links (Google's redirect wrapper removed), headings, nested lists,
 * bold / italic (Google Docs writes them as styles), tables (links in cells kept), quotes, code.
 * Images are left out (their addresses point at Google's servers): paste them as images.
 *
 * DOMParser builds an inert document: no script runs, no image or style is fetched.
 */

/** Whether the HTML is worth converting: it holds structure the plain text loses. Code copied
 * from an editor (whitespace: pre) is left to the plain text. */
export function richEnough(html: string): boolean {
  if (!html || /white-space:\s*pre/i.test(html)) return false;
  return /<a\s[^>]*href|<table|<h[1-6][\s>]|<li[\s>]/i.test(html);
}

function cleanUrl(raw: string | null): string | null {
  if (!raw) return null;
  let url = raw.trim();
  try {
    const u = new URL(url);
    if (/(^|\.)google\.com$/.test(u.hostname) && u.pathname === "/url") url = u.searchParams.get("q") ?? "";
  } catch {
    return null;
  }
  return /^(https?:\/\/|mailto:)/i.test(url) ? url : null;
}

const esc = (s: string) => s.replace(/([\\[\]])/g, "\\$1");
const target = (url: string) => url.replace(/ /g, "%20").replace(/\(/g, "%28").replace(/\)/g, "%29");

function styled(el: Element, prop: string): string {
  return (el as HTMLElement).style?.getPropertyValue(prop) ?? "";
}

/** Inline Markdown of a node's content (links, bold, italic, code), whitespace collapsed. */
function inline(node: Node, inLink = false): string {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? "").replace(/ /g, " ").replace(/\s+/g, " ");
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (tag === "br") return "\n";
  if (tag === "img" || tag === "style" || tag === "script") return "";
  const inner = () => Array.from(el.childNodes, (c) => inline(c, inLink || tag === "a")).join("");
  if (tag === "a" && !inLink) {
    const url = cleanUrl(el.getAttribute("href"));
    const text = inner().trim();
    return url && text ? `[${esc(text)}](${target(url)})` : text;
  }
  let text = inner();
  const weight = styled(el, "font-weight");
  // Google Docs wraps everything in <b style="font-weight:normal">: not bold.
  const bold = (tag === "strong" || (tag === "b" && weight !== "normal" && weight !== "400")) || Number(weight) >= 600 || weight === "bold";
  const italic = tag === "em" || tag === "i" || styled(el, "font-style") === "italic";
  const mono = tag === "code" || /monospace|courier|consolas/i.test(styled(el, "font-family"));
  const core = text.trim();
  if (!core) return text;
  const lead = text.match(/^\s*/)?.[0] ?? "";
  const tail = text.match(/\s*$/)?.[0] ?? "";
  if (mono) text = `\`${core}\``;
  else {
    let t = core;
    if (italic) t = `*${t}*`;
    if (bold) t = `**${t}**`;
    text = t;
  }
  return lead + text + tail;
}

function cell(el: Element): string {
  return inline(el).replace(/\n+/g, " ").trim().replace(/\|/g, "\\|");
}

function table(el: Element): string {
  const rows = Array.from(el.querySelectorAll("tr")).map((tr) => Array.from(tr.querySelectorAll("th,td")).map(cell));
  const kept = rows.filter((r) => r.some((c) => c));
  if (!kept.length) return "";
  const n = Math.max(...kept.map((r) => r.length));
  const fill = (r: string[]) => Array.from({ length: n }, (_, i) => r[i] ?? "");
  const line = (r: string[]) => `| ${fill(r).join(" | ")} |`;
  return [line(kept[0]), `|${" --- |".repeat(n)}`, ...kept.slice(1).map(line)].join("\n");
}

/** List depth of an item: nested lists, or Google Docs' margin-left / aria-level. */
function depth(li: Element, nested: number): number {
  const aria = Number(li.getAttribute("aria-level"));
  if (aria > 1) return aria - 1;
  const margin = parseFloat(styled(li, "margin-left")) || 0;
  return Math.max(nested, Math.round(margin / 36) - 1, 0);
}

const INLINE = new Set(["a", "span", "b", "strong", "em", "i", "u", "code", "font", "small", "sup", "sub", "mark", "s", "del", "br", "abbr", "label"]);

function blocks(parent: Element, out: string[], nested = 0): void {
  // Text and inline elements side by side are one paragraph ("Xem <a>trang này</a>").
  const run: string[] = [];
  const flush = () => {
    const t = run.join("").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").trim();
    run.length = 0;
    if (t) out.push(t);
  };
  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      run.push(inline(node));
      continue;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) continue;
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    if (INLINE.has(tag) && !hasBlock(el)) {
      run.push(inline(el));
      continue;
    }
    flush();
    const heading = /^h([1-6])$/.exec(tag);
    if (heading) {
      const t = inline(el).trim();
      if (t) out.push(`${"#".repeat(Math.min(4, Number(heading[1])))} ${t.replace(/\*\*/g, "")}`);
    } else if (tag === "ul" || tag === "ol") {
      let n = 0;
      const items: string[] = [];
      for (const li of Array.from(el.children)) {
        if (li.tagName.toLowerCase() !== "li") continue;
        n++;
        const own = Array.from(li.childNodes)
          .filter((c) => !(c.nodeType === Node.ELEMENT_NODE && /^(ul|ol)$/i.test((c as Element).tagName)))
          .map((c) => inline(c))
          .join("")
          .trim();
        const pad = "   ".repeat(depth(li, nested));
        if (own) items.push(`${pad}${tag === "ol" ? `${n}.` : "-"} ${own}`);
        for (const sub of Array.from(li.children).filter((c) => /^(ul|ol)$/i.test(c.tagName))) {
          const inner: string[] = [];
          blocks(wrap(sub), inner, nested + 1);
          items.push(...inner);
        }
      }
      if (items.length) out.push(items.join("\n"));
    } else if (tag === "table") {
      const t = table(el);
      if (t) out.push(t);
    } else if (tag === "pre") {
      const t = el.textContent ?? "";
      if (t.trim()) out.push("```\n" + t.replace(/\n$/, "") + "\n```");
    } else if (tag === "blockquote") {
      const inner: string[] = [];
      blocks(el, inner, nested);
      if (inner.length) out.push(inner.join("\n\n").split("\n").map((l) => `> ${l}`).join("\n"));
    } else if (tag === "hr") {
      out.push("---");
    } else if (tag === "p" || tag === "li") {
      const t = inline(el).trim();
      if (t) out.push(t);
    } else if (/^(div|section|article|main|body|b|span|font|google-sheets-html-origin|colgroup|tbody)$/.test(tag) && hasBlock(el)) {
      blocks(el, out, nested);
    } else if (!/^(style|script|meta|title|head|img)$/.test(tag)) {
      const t = inline(el).trim();
      if (t) out.push(t);
    }
  }
  flush();
}

function wrap(el: Element): Element {
  const box = el.ownerDocument.createElement("div");
  box.appendChild(el.cloneNode(true));
  return box;
}

function hasBlock(el: Element): boolean {
  return !!el.querySelector("p,div,h1,h2,h3,h4,h5,h6,ul,ol,table,pre,blockquote,hr,li");
}

/** Markdown of pasted HTML, or null when it is not worth it (see `richEnough`). */
export function htmlToMarkdown(html: string): string | null {
  if (!richEnough(html)) return null;
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out: string[] = [];
  blocks(doc.body, out);
  const md = out
    .join("\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return md || null;
}
