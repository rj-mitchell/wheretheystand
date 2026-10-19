/** A briefing as a Word document (.docx), built in the browser: no server work and no extra libraries. */
import { BRAND } from "./brand";
import { sourceLine } from "./export";
import { fmtDate } from "./render";
import type { Answer, SourceCard } from "./types";

// ---- a minimal zip writer (files stored uncompressed, which Word reads happily) -------------------------
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (const b of data) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export function zip(files: { name: string; text: string }[]): Uint8Array {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const data = enc.encode(f.text);
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true); // names are UTF-8
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, name.length, true);
    parts.push(new Uint8Array(local.buffer), name, data);
    const entry = new DataView(new ArrayBuffer(46));
    entry.setUint32(0, 0x02014b50, true);
    entry.setUint16(4, 20, true);
    entry.setUint16(6, 20, true);
    entry.setUint16(8, 0x0800, true);
    entry.setUint32(16, crc, true);
    entry.setUint32(20, data.length, true);
    entry.setUint32(24, data.length, true);
    entry.setUint16(28, name.length, true);
    entry.setUint32(42, offset, true);
    central.push(new Uint8Array(entry.buffer), name);
    offset += 30 + name.length + data.length;
  }
  const size = central.reduce((n, p) => n + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, size, true);
  end.setUint32(16, offset, true);
  const all = [...parts, ...central, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of all) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

// ---- WordprocessingML ---------------------------------------------------------------------------------
const esc = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    // characters XML 1.0 does not allow
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "");

function run(text: string, opts: { bold?: boolean; italic?: boolean; sup?: boolean } = {}): string {
  const props = [opts.bold && "<w:b/>", opts.italic && "<w:i/>", opts.sup && '<w:vertAlign w:val="superscript"/>']
    .filter(Boolean)
    .join("");
  return `<w:r>${props ? `<w:rPr>${props}</w:rPr>` : ""}<w:t xml:space="preserve">${esc(text)}</w:t></w:r>`;
}

/** Inline markdown: **bold** and [Source n] citations (as superscript numbers). */
function runs(text: string): string {
  const out: string[] = [];
  for (const piece of text.split(/(\*\*[^*]+\*\*|\[Source \d+\])/g)) {
    if (!piece) continue;
    const bold = piece.match(/^\*\*([^*]+)\*\*$/);
    const cite = piece.match(/^\[Source (\d+)\]$/);
    if (bold) out.push(run(bold[1], { bold: true }));
    else if (cite) out.push(run(`[${cite[1]}]`, { sup: true }));
    else out.push(run(piece));
  }
  return out.join("");
}

function para(content: string, style?: string): string {
  return `<w:p>${style ? `<w:pPr><w:pStyle w:val="${style}"/></w:pPr>` : ""}${content}</w:p>`;
}

export function documentXml(question: string, answer: Answer, sources: SourceCard[], link: string, today: Date): string {
  const body: string[] = [para(run(question), "Title")];
  body.push(
    para(
      run(
        `${BRAND.name} briefing, ${fmtDate(today.toISOString().slice(0, 10))}. Written by AI (Claude) from the cited ` +
          "Hansard records only. Check the sources before relying on it.",
        { italic: true },
      ),
    ),
  );
  for (const block of answer.markdown.split(/\n{2,}/)) {
    for (const line of block.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      const h = t.match(/^(#{1,4})\s+(.*)$/);
      if (h) body.push(para(runs(h[2]), h[1].length <= 2 ? "Heading1" : "Heading2"));
      else if (/^[-*]\s+/.test(t)) body.push(para(runs(t.replace(/^[-*]\s+/, "")), "ListBullet"));
      else body.push(para(runs(t)));
    }
  }
  const used = new Set(answer.segments.flatMap((s) => s.sources));
  const cited = sources.filter((s) => used.has(s.n));
  if (cited.length) {
    body.push(para(run("Sources"), "Heading1"));
    for (const s of cited) body.push(para(run(sourceLine(s)), "Source"));
  }
  body.push(
    para(
      run(
        `${BRAND.name}: ${link}. Briefing text licensed CC BY 4.0. Contains Parliamentary information licensed under ` +
          "the Open Parliament Licence v3.0.",
        { italic: true },
      ),
    ),
  );
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
    body.join("") +
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" ' +
    'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>'
  );
}

const STYLES =
  '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
  '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
  '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Georgia" w:hAnsi="Georgia" w:cs="Georgia"/>' +
  '<w:sz w:val="22"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>' +
  '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>' +
  '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>' +
  '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>' +
  '<w:rPr><w:sz w:val="36"/><w:color w:val="1B4D3E"/></w:rPr></w:style>' +
  '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>' +
  '<w:pPr><w:keepNext/><w:spacing w:before="240"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>' +
  '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>' +
  '<w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>' +
  '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>' +
  '<w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:style>' +
  '<w:style w:type="paragraph" w:styleId="Source"><w:name w:val="Source"/><w:basedOn w:val="Normal"/>' +
  '<w:pPr><w:ind w:left="360" w:hanging="360"/><w:spacing w:after="80"/></w:pPr><w:rPr><w:sz w:val="18"/></w:rPr></w:style>' +
  "</w:styles>";

export function briefingDocx(question: string, answer: Answer, sources: SourceCard[], link: string, today = new Date()) {
  // bullets are written as text so the document needs no numbering definitions
  const doc = documentXml(question, answer, sources, link, today).replace(
    /(<w:pStyle w:val="ListBullet"\/><\/w:pPr>)/g,
    '$1<w:r><w:t xml:space="preserve">•\t</w:t></w:r>',
  );
  return zip([
    {
      name: "[Content_Types].xml",
      text:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
        '<Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>' +
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' +
        "</Types>",
    },
    {
      name: "_rels/.rels",
      text:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>' +
        "</Relationships>",
    },
    {
      name: "word/_rels/document.xml.rels",
      text:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' +
        "</Relationships>",
    },
    { name: "word/document.xml", text: doc },
    { name: "word/styles.xml", text: STYLES },
  ]);
}

export function downloadName(question: string): string {
  const words = question.replace(/[^\p{L}\p{N} ]+/gu, " ").trim().split(/\s+/).slice(0, 8).join("-");
  return `${words || "briefing"}.docx`.toLowerCase();
}
