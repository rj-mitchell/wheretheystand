import { BRAND } from "./brand";
import { attribution, roleTitles } from "./people";
import { fmtDate } from "./render";
import type { Answer, SourceCard } from "./types";

/** A formal reference in the style used to cite Hansard, e.g. "HC Deb 9 March 2023, col 412". */
export function formalReference(s: SourceCard): string {
  const date = fmtDate(s.date);
  if (s.kind === "devolved") return `${s.chamber} Official Report, ${date}`;
  if (s.kind === "evidence") return `Oral evidence to the ${s.committee ?? "committee"}, ${date}`.replace("the The ", "the ");
  if (s.kind === "edm") return `EDM ${s.uin ?? ""}, tabled ${date}`.replace("EDM ,", "Early day motion,");
  if (s.kind === "division") {
    const house = /lords/i.test(s.chamber) ? "HL Deb" : "HC Deb";
    return `${house} ${date}, Division ${s.number ?? ""}`.trim();
  }
  if (s.record_type === "question" || s.record_type === "answer") {
    return `Written question${s.uin ? ` ${s.uin}` : ""}, ${s.record_type === "answer" ? "answered" : "tabled"} ${date}`;
  }
  if (s.record_type === "statement") return `Written statement${s.uin ? ` ${s.uin}` : ""}, ${date}`;
  const col = s.colnum ? `, col ${s.colnum}` : "";
  if (/public bill committee/i.test(s.chamber)) return `PBC Deb ${date}${col}`;
  if (/westminster hall/i.test(s.chamber)) return `HC Deb ${date}${col} (Westminster Hall)`;
  return `${/lords/i.test(s.chamber) ? "HL" : "HC"} Deb ${date}${col}`;
}

/** One line per source: who, their role then, the formal reference and the links. */
export function sourceLine(s: SourceCard): string {
  if (s.kind === "division") {
    return `[${s.n}] ${formalReference(s)}: ${s.major_heading ?? s.title}. ${s.official_url ?? s.url}`;
  }
  if (s.kind === "devolved") {
    const m = s.member;
    const who = [m?.name, m?.party || m?.seat ? `(${[m?.party, m?.seat].filter(Boolean).join(", ")})` : ""].filter(Boolean).join(" ");
    return `[${s.n}] ${who}. ${formalReference(s)}${s.heading ? `, "${s.heading}"` : ""}. ${s.official_url ?? s.url}`;
  }
  if (s.kind === "evidence") {
    const w = s.witness;
    const who = [w?.name, w?.role || w?.organisation ? `(${[w?.role, w?.organisation].filter(Boolean).join(", ")})` : ""]
      .filter(Boolean)
      .join(" ");
    return `[${s.n}] ${who}. ${formalReference(s)}${s.inquiry ? `, "${s.inquiry}"` : ""}. ${s.official_url ?? s.url}`;
  }
  if (s.kind === "edm") {
    const by = s.sponsor?.name ? `, tabled by ${s.sponsor.name}${s.sponsor.party ? ` (${s.sponsor.party})` : ""}` : "";
    return `[${s.n}] ${formalReference(s)}: "${s.edm_title ?? s.title}"${by}. ${s.official_url ?? s.url}`;
  }
  const { name, tail } = attribution(s.speaker, s.speaker_name_raw);
  const roles = roleTitles(s.speaker);
  const who = [name, tail].filter(Boolean).join(" ") + (roles.length ? `, ${roles.join("; ")}` : "");
  const links = [s.official_url ?? s.url, s.twfy_url].filter(Boolean).join(" ; ");
  return `[${s.n}] ${who}. ${formalReference(s)}${s.heading ? `, "${s.heading}"` : ""}. ${links}`;
}

function plain(markdown: string): string {
  return markdown
    .replace(/\[Source (\d+)\]/g, "[$1]")
    .replace(/^#+\s*/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** The briefing as plain text with numbered citations, followed by the full source list. */
export function briefingWithSources(
  question: string,
  answer: Answer,
  sources: SourceCard[],
  today = new Date(),
  link?: string,
): string {
  const used = new Set(answer.segments.flatMap((s) => s.sources));
  const cited = sources.filter((s) => used.has(s.n));
  return [
    `Question: ${question}`,
    "",
    plain(answer.markdown),
    "",
    "Sources",
    ...cited.map(sourceLine),
    "",
    `${BRAND.name} (${link ?? "wheretheystand.uk"}), ${fmtDate(today.toISOString().slice(0, 10))}. Written by AI from the cited ` +
      "Hansard records; check the sources. Briefing text licensed CC BY 4.0. Contains Parliamentary information " +
      "licensed under the Open Parliament Licence v3.0.",
  ].join("\n");
}

/** Just the references, for pasting into notes or a bibliography. */
export function referencesOnly(answer: Answer, sources: SourceCard[]): string {
  const used = new Set(answer.segments.flatMap((s) => s.sources));
  return sources.filter((s) => used.has(s.n)).map(sourceLine).join("\n");
}
