import type { SourceCard, Speaker } from "./types";

/** Party abbreviations as printed after a member's name in Hansard, e.g. "(Lab)". */
const PARTY_ABBR: Record<string, string> = {
  Labour: "Lab",
  "Labour/Co-operative": "Lab/Co-op",
  Conservative: "Con",
  "Liberal Democrat": "LD",
  "Scottish National Party": "SNP",
  "Plaid Cymru": "PC",
  DUP: "DUP",
  "Sinn Féin": "SF",
  "Social Democratic and Labour Party": "SDLP",
  UUP: "UUP",
  Alliance: "Alliance",
  Green: "Green",
  "Reform UK": "Reform",
  Independent: "Ind",
  Crossbench: "CB",
  Bishop: "Bp",
  "Non-affiliated": "Non-Afl",
  "Conservative Independent": "Ind Con",
  "Independent Labour": "Ind Lab",
  "Traditional Unionist Voice": "TUV",
};

export function partyAbbr(party: string | null | undefined): string | null {
  if (!party) return null;
  return PARTY_ABBR[party] ?? party;
}

/** Hansard-style attribution: name, then (constituency) and (party), e.g. "Hilary Benn (Leeds Central) (Lab)". */
export function attribution(sp: Speaker | null | undefined, raw?: string | null): { name: string; tail: string } {
  if (!sp || !sp.name) return { name: raw || "Unattributed", tail: "" };
  const bits = [sp.constituency, partyAbbr(sp.party)].filter(Boolean).map((b) => `(${b})`);
  return { name: sp.name, tail: bits.join(" ") };
}

export function roleTitles(sp: Speaker | null | undefined): string[] {
  return (sp?.offices ?? []).filter((o) => ["gov", "shadow", "chair"].includes(o.kind)).map((o) => o.title);
}

export const GENDER_LABEL: Record<string, string> = { F: "Women", M: "Men", unknown: "Not known" };
export const AGE_BAND_LABEL: Record<string, string> = {
  "under-40": "Under 40",
  "40-54": "40 to 54",
  "55-69": "55 to 69",
  "70-plus": "70 and over",
  unknown: "Not known",
};

export const TENURE_LABEL: Record<string, string> = {
  "first-term": "First-term MPs",
  returning: "Re-elected MPs",
  unknown: "Not known",
};
export const MAJORITY_LABEL: Record<string, string> = {
  marginal: "Marginal (under 10%)",
  middle: "10% to 20%",
  safe: "Safe (over 20%)",
  unknown: "Not known",
};

/** e.g. "4,937 (11.8%) at the 2019 general election". */
export function majorityText(m: { majority: number; pct: number | null; election: string } | null | undefined): string | null {
  if (!m) return null;
  return `${m.majority.toLocaleString("en-GB")}${m.pct != null ? ` (${m.pct}% of votes cast)` : ""} at the ${m.election}`;
}

/** Age on the date of the record: "54", or "about 54" when only the year of birth is known. */
export function ageLabel(sp: Speaker | null | undefined): string | null {
  if (sp?.age == null) return null;
  return sp.age_approximate ? `about ${sp.age}` : String(sp.age);
}

export function people(n: number): string {
  return n >= 1_000_000 ? `${(n / 1e6).toFixed(1)}m` : (Math.round(n / 1000) * 1000).toLocaleString("en-GB");
}

/** Mirrors the server's label: seat type is by the size of the urban area, not official city status. */
export function seatTypeLabel(sp: Speaker | null | undefined): string | null {
  const kind = sp?.settlement;
  if (!kind) return null;
  const pop = sp?.urban_area_pop;
  if (kind === "city") {
    const detail = [sp?.urban_area ? `${sp.urban_area} urban area` : null, pop ? `${people(pop)} people` : null]
      .filter(Boolean)
      .join(", ");
    return detail ? `city seat (${detail})` : "city seat";
  }
  if (kind === "town" && pop) return `town seat (urban area of ${people(pop)})`;
  return `${kind} seat`;
}

/** Which House a source belongs to, for the Commons-green / Lords-red colouring of the printed volumes. */
export function houseOf(source: Pick<SourceCard, "chamber" | "speaker">): "commons" | "lords" {
  if (/lords/i.test(source.chamber)) return "lords";
  if (source.speaker?.house === "lords" && !/commons|westminster hall/i.test(source.chamber)) return "lords";
  return "commons";
}

/** "col. 827" for spoken debates, the question number for written answers. */
export function reference(source: Pick<SourceCard, "colnum" | "uin">): string | null {
  if (source.colnum) return `Col. ${source.colnum}`;
  if (source.uin) return `UIN ${source.uin}`;
  return null;
}
