import type { Answer } from "./types";

/** One row of "Where they stand": a group of speakers and their position, as the briefing's own section headings put
 * it ("Ministers: acknowledging pressure and setting out measures"), with how many sources that section cites. */
export interface Stance {
  heading: string;
  group: string;
  position: string;
  sources: number;
}

// a heading on a line of its own: **Group: position** or ## Group: position
const HEADING = /^\s*(?:\*\*(.+?)\*\*|#{2,4}\s+(.+?))\s*$/;

/** The briefing's sections, from its headings. The first heading is the briefing's title when it comes before any
 * text, and is left out. Only headings of the form "Group: position" count; with fewer than two there is no table. */
export function stancesFrom(answer: Answer | null | undefined): Stance[] {
  if (!answer || answer.no_records) return [];
  const sections: { heading: string; sources: Set<number> }[] = [];
  let seenText = false;
  let titleSkipped = false;
  for (const seg of answer.segments) {
    for (const line of seg.text.split("\n")) {
      const m = line.match(HEADING);
      if (m) {
        const heading = (m[1] ?? m[2]).trim();
        if (!seenText && !titleSkipped) {
          titleSkipped = true; // the briefing's title
          continue;
        }
        sections.push({ heading, sources: new Set() });
      } else if (line.trim()) {
        seenText = true;
      }
    }
    const current = sections.at(-1);
    if (current) for (const c of seg.cited) current.sources.add(c.source);
  }
  const rows = sections
    .map(({ heading, sources }) => {
      const at = heading.indexOf(": ");
      return at > 0
        ? { heading, group: heading.slice(0, at), position: heading.slice(at + 2), sources: sources.size }
        : null;
    })
    .filter((r): r is Stance => r !== null && r.sources > 0);
  return rows.length >= 2 ? rows : [];
}
