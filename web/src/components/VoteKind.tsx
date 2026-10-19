import type { VoteType } from "../types";

/** The kind of vote (amendment, second reading…) as a link to its glossary entry, with what it usually means. */
/** Whether there is anything to say about a vote's kind (callers wrap it in brackets only then). */
export function hasVoteKind(vt?: VoteType): vt is VoteType {
  return !!vt && (vt.kind !== "other" || !!vt.aye_means || !!vt.whip_text);
}

export function VoteKind({ vt, detail = false }: { vt?: VoteType; detail?: boolean }) {
  if (!hasVoteKind(vt)) return null;
  return (
    <span className="vote-kind">
      {vt.kind !== "other" && (
        <a className="vote-kind-label" href={`/glossary#vote-${vt.kind.replace(/_/g, "-")}`}>
          {vt.label}
        </a>
      )}
      {vt.aye_means && (
        <span className="vote-kind-means">
          {vt.kind !== "other" ? ". " : ""}Voting aye: {vt.aye_means}
          {detail && vt.no_means ? ` Voting no: ${vt.no_means}` : ""}{" "}
          <span className="muted small">(TheyWorkForYou Votes)</span>
        </span>
      )}
      {vt.whip_text && (
        <span className="vote-kind-whip">
          {vt.kind !== "other" || vt.aye_means ? ". " : ""}Whipping: {vt.whip_text}{" "}
          <span className="muted small">(TheyWorkForYou Votes)</span>
        </span>
      )}
      {detail && vt.kind !== "other" && (
        <>
          {vt.question && (
            <span className="vote-kind-question">
              {" "}
              The question put: “{vt.question}”.
            </span>
          )}
          {vt.outcome && <span className="vote-kind-outcome"> Recorded outcome: {vt.outcome}</span>}
          <span className="vote-kind-note"> {vt.note}</span>
        </>
      )}
    </span>
  );
}

/** The kinds present in a list of votes, each with its note: a key shown once under a table. */
export function VoteKindKey({ types }: { types: (VoteType | undefined)[] }) {
  const seen = new Map<string, VoteType>();
  for (const t of types) if (t && t.kind !== "other" && !seen.has(t.kind)) seen.set(t.kind, t);
  if (seen.size === 0) return null;
  return (
    <dl className="vote-kind-key small">
      {[...seen.values()].map((t) => (
        <div key={t.kind}>
          <dt>{t.label}</dt>
          <dd>{t.note}</dd>
        </div>
      ))}
    </dl>
  );
}
