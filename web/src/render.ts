import DOMPurify from "dompurify";
import { marked } from "marked";
import type { Segment } from "./types";

marked.setOptions({ gfm: true, breaks: false });

const CITE = (nums: number[]) => `⁣⟦S:${nums.join(",")}⟧⁣`;
const U_OPEN = "⁣⟦U⟧⁣";
const U_CLOSE = "⁣⟦/U⟧⁣";
const L_OPEN = "⁣⟦L⟧⁣";
const L_CLOSE = "⁣⟦/L⟧⁣";

const Q_OPEN = "⁣⟦Q⟧⁣";
const Q_CLOSE = "⁣⟦/Q⟧⁣";

/** Mark judgemental words in the briefing's own voice, and quotations not found in their sources (offsets into
 * the segment text, from the server). Loaded words are only flagged outside quotations, so the two never nest. */
function markLoaded(s: Segment): string {
  let text = s.text;
  const marks = [
    ...(s.loaded ?? []).map((f) => ({ ...f, open: L_OPEN, close: L_CLOSE })),
    ...(s.misquotes ?? []).map((f) => ({ ...f, open: Q_OPEN, close: Q_CLOSE })),
  ].sort((a, b) => b.start - a.start);
  for (const f of marks) {
    text = text.slice(0, f.start) + f.open + text.slice(f.start, f.end) + f.close + text.slice(f.end);
  }
  return text;
}

/** Markdown for a verified answer, with placeholders for citations and unsupported spans. */
export function segmentsToMarkdown(segments: Segment[]): string {
  return segments
    .map((s) => {
      const text = markLoaded(s);
      if (s.status === "cited") return text + CITE(s.sources);
      if (s.status === "unsupported" || s.status === "invalid") {
        // keep leading/trailing whitespace outside the marker so markdown structure survives
        const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
        return m && m[2] ? `${m[1]}${U_OPEN}${m[2]}${U_CLOSE}${m[3]}` : text;
      }
      return text;
    })
    .join("");
}

/** The question is the page's h2, so a briefing's own headings start at h3 (keeps the outline in order). */
export function demoteHeadings(html: string): string {
  return html.replace(/<(\/?)h([1-6])\b/g, (_m, close: string, level: string) => `<${close}h${Math.min(Number(level) + 2, 6)}`);
}

/** Render answer markdown to sanitised HTML with clickable [Source n] badges. */
export function renderAnswer(md: string): string {
  let html = demoteHeadings(marked.parse(md, { async: false }) as string);
  html = html.replace(/⁣⟦S:([\d,]+)⟧⁣/g, (_m, nums: string) =>
    nums
      .split(",")
      .map((n) => `<button type="button" class="cite" data-source="${n}" aria-label="Open source ${n}">${n}</button>`)
      .join(""),
  );
  html = html
    .replace(/⁣⟦U⟧⁣/g, '<span class="unsupported" title="Not directly supported by a cited source">')
    .replace(/⁣⟦\/U⟧⁣/g, "</span>")
    .replace(/⁣⟦L⟧⁣/g, '<span class="loaded" title="A judgemental word in the briefing\'s own voice, not a quotation">')
    .replace(/⁣⟦\/L⟧⁣/g, "</span>")
    .replace(/⁣⟦Q⟧⁣/g, '<span class="misquote" title="These words were not found word for word in the cited source">')
    .replace(/⁣⟦\/Q⟧⁣/g, "</span>");
  return typographic(DOMPurify.sanitize(html, { ADD_ATTR: ["data-source", "aria-label"] }));
}

/** Plain streaming text (no citations yet). */
export function renderPlain(md: string): string {
  return typographic(DOMPurify.sanitize(demoteHeadings(marked.parse(md, { async: false }) as string)));
}

/** Printers' quotation marks and apostrophes (“ ” ‘ ’) in place of typewriter ones, in the text only (never inside
 * tags). For display: the briefing's own text, its copies and the quotation checks keep the original characters. */
export function typographic(html: string): string {
  let prev = " "; // the character before the current one, carried across tags
  return html
    .split(/(<[^>]*>)/)
    .map((part) => {
      if (part.startsWith("<")) {
        // a new paragraph, item or heading starts fresh; inline tags (a citation, emphasis) carry on
        if (/^<\/?(p|li|h[1-6]|ul|ol|blockquote|br|div)\b/i.test(part)) prev = " ";
        return part;
      }
      const text = part.replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      let out = "";
      for (const ch of text) {
        const opening = /[\s(\[{\u2014\u2013-]/.test(prev);
        if (ch === '"') out += opening ? "\u201c" : "\u201d";
        else if (ch === "'") out += opening ? "\u2018" : "\u2019";
        else out += ch;
        prev = ch;
      }
      return out;
    })
    .join("");
}

/** Split text into parts, marking exact quotes to highlight. */
export function highlightParts(text: string, quotes: string[]): { text: string; hit: boolean }[] {
  const norm = (s: string) => s.replace(/\s+/g, " ").trim();
  const ranges: [number, number][] = [];
  for (const q of quotes.map(norm).filter((q) => q.length > 3)) {
    const i = text.indexOf(q);
    if (i >= 0) ranges.push([i, i + q.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const parts: { text: string; hit: boolean }[] = [];
  let pos = 0;
  for (const [s, e] of ranges) {
    if (s < pos) continue;
    if (s > pos) parts.push({ text: text.slice(pos, s), hit: false });
    parts.push({ text: text.slice(s, e), hit: true });
    pos = e;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), hit: false });
  return parts;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/** Lists on member pages are shown open; one longer than this starts folded, with its count in the heading. */
export const LONG = 25;
