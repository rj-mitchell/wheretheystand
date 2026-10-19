import { fmtDate } from "./render";

/** A question about one sitting of the bill, for the main page (which reads the date and the stage from it). */
export function stageQuestion(name: string, s: { date: string; house: string; stages: string[] }): string {
  const stage = s.stages
    .filter((x) => x !== "Debate")
    .map((x) => (/^(Public|Westminster)/.test(x) ? x : x[0].toLowerCase() + x.slice(1)))
    .join(" and ");
  const where = s.house === "lords" ? "in the House of Lords" : "in the House of Commons";
  return stage
    ? `What arguments were made at the ${stage} of the ${name} ${where} on ${fmtDate(s.date)}?`
    : `What arguments were made on the ${name} ${where} on ${fmtDate(s.date)}?`;
}
