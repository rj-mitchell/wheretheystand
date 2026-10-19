import type { Turn } from "./types";

/**
 * Suggested follow-up questions under a briefing, from what it searched: the other House, how members voted,
 * a more recent period, ministers or other members. Plain wording that the question reader resolves against the
 * earlier question, so "this" carries the topic over.
 */
export function followUpsFor(t: Turn): string[] {
  const plan = t.plan;
  if (!plan || !t.answer || t.answer.no_records) return [];
  const f = plan.filters;
  const out: string[] = [];
  const lordsOnly = f.houses.length === 1 && f.houses[0] === "lords";
  if (lordsOnly) out.push("What have MPs said about this?");
  else if (!f.regions.length && !(f.seat_types ?? []).length) out.push("What have peers said about this?");
  if (!plan.wants_votes && !lordsOnly) out.push("How have MPs voted on this?");
  if (!f.date_from || f.date_from < "2024-07-04") out.push("What has been said since the 2024 general election?");
  if (plan.speakers.length) out.push("What have other MPs said about this?");
  else if (!f.offices.includes("gov")) out.push("What have ministers said about this?");
  return out.slice(0, 3);
}
