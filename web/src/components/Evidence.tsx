import { AGE_BAND_LABEL, GENDER_LABEL, MAJORITY_LABEL, TENURE_LABEL } from "../people";
import { fmtDate } from "../render";
import type { Evidence, Settings } from "../types";

const SEAT_LABEL: Record<string, string> = { city: "City", town: "Town", rural: "Rural" };

/** One labelled row of counts, e.g. Parties: Labour 7 · Conservative 4. Shown so readers can judge balance. */
function EvidenceRow({ label, counts }: { label: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  if (!entries.length) return null;
  return (
    <div className="evidence-row">
      <span className="evidence-label">{label}</span>
      <ul className="evidence-groups">
        {entries.map(([g, n]) => (
          <li key={g}>
            {g} <span className="count">{n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Deterministic summary of the evidence behind a briefing (computed from the data, not by the model). */
function relabel(counts: Record<string, number> | undefined, labels: Record<string, string>, order?: string[]) {
  const entries = Object.entries(counts ?? {});
  if (order) entries.sort(([a], [b]) => order.indexOf(a) - order.indexOf(b));
  return Object.fromEntries(entries.map(([k, n]) => [labels[k] ?? k, n]));
}

/** Gender and age rows appear only when the question is about them, so they never distract from the rest. */
/** What the search covered, in words, so a reader can see the devolved parliaments and committee evidence were
 * searched even when nothing from them was found. */
export function searchedText(scope: Settings["scope"]): string {
  const parts = ["Commons and Lords debates"];
  if (scope.written) parts.push("written answers and statements");
  if (scope.pbc) parts.push("Public Bill Committees");
  if (scope.votes) parts.push("votes");
  if (scope.edms ?? true) parts.push("early day motions");
  if (scope.devolved) parts.push("the Scottish Parliament, the Senedd and the NI Assembly");
  if (scope.evidence) parts.push("witnesses' evidence to committees");
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
}

export function EvidenceBar({
  evidence,
  demographics = false,
  seats = false,
  scope,
}: {
  evidence?: Evidence;
  demographics?: boolean;
  seats?: boolean;
  scope?: Settings["scope"];
}) {
  if (!evidence || evidence.records === 0) return null;
  const years =
    evidence.date_from && evidence.date_to
      ? evidence.date_from.slice(0, 4) === evidence.date_to.slice(0, 4)
        ? fmtDate(evidence.date_from).split(" ").slice(-2).join(" ")
        : `${evidence.date_from.slice(0, 4)}–${evidence.date_to.slice(0, 4)}`
      : "";
  return (
    <section className="evidence" aria-label="Evidence base">
      <p className="evidence-head">
        <strong>Evidence base:</strong> {evidence.records} contribution{evidence.records === 1 ? "" : "s"} from{" "}
        {evidence.speakers} speaker{evidence.speakers === 1 ? "" : "s"}
        {years ? `, ${years}` : ""}
        {evidence.divisions ? `, plus ${evidence.divisions} division${evidence.divisions === 1 ? "" : "s"}` : ""}
        {evidence.edms ? ` and ${evidence.edms} early day motion${evidence.edms === 1 ? "" : "s"}` : ""}
        {evidence.devolved ? `, and ${evidence.devolved} from the devolved parliaments` : ""}
        {evidence.committee_evidence
          ? `, and ${evidence.committee_evidence} witness answer${evidence.committee_evidence === 1 ? "" : "s"} to committees`
          : ""}
      </p>
      {scope && (
        <p className="evidence-searched muted small">
          Searched: {searchedText(scope)}. <a href="#settings">Change what is searched</a>
        </p>
      )}
      {evidence.imbalance && (
        <p className="evidence-warning">
          {evidence.imbalance.count} of these {evidence.imbalance.total} contributions are from {evidence.imbalance.party}{" "}
          members. The record found less from other parties on this question, so the briefing reflects that balance.
        </p>
      )}
      {evidence.devolution?.map((d) => (
        <p key={d.subject} className="evidence-note">
          <strong>Devolution:</strong> {d.text}{" "}
          <a href="/methodology#devolution">About these notes</a>
        </p>
      ))}
      <EvidenceRow label="Speakers" counts={evidence.groups} />
      {evidence.raised_most && evidence.raised_most.length > 0 && (
        <div className="evidence-row">
          <span className="evidence-label">Raised it most often</span>
          <ul className="evidence-groups">
            {evidence.raised_most.map((m) => (
              <li key={m.name}>{m.name}</li>
            ))}
          </ul>
        </div>
      )}
      <EvidenceRow label="Parties" counts={evidence.parties} />
      <EvidenceRow
        label="MPs' seats"
        counts={Object.fromEntries(Object.entries(evidence.seat_types).map(([k, n]) => [SEAT_LABEL[k] ?? k, n]))}
      />
      {seats && (
        <>
          <EvidenceRow label="MPs' terms" counts={relabel(evidence.tenure, TENURE_LABEL, Object.keys(TENURE_LABEL))} />
          <EvidenceRow
            label="Majority then"
            counts={relabel(evidence.majority_bands, MAJORITY_LABEL, Object.keys(MAJORITY_LABEL))}
          />
        </>
      )}
      {demographics && (
        <>
          <EvidenceRow label="Gender" counts={relabel(evidence.genders, GENDER_LABEL, ["F", "M", "unknown"])} />
          <EvidenceRow
            label="Age on the day"
            counts={relabel(evidence.age_bands, AGE_BAND_LABEL, Object.keys(AGE_BAND_LABEL))}
          />
        </>
      )}
    </section>
  );
}
