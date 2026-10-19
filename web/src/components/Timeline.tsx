import { useEffect, useState } from "react";
import { fmtDate } from "../render";
import type { VoteType } from "../types";
import { VoteKind } from "./VoteKind";

export interface TimelineItem {
  kind: "said" | "voted";
  date: string;
  // said
  record_id?: string;
  heading?: string | null;
  excerpt?: string;
  speaker?: string | null;
  party?: string | null;
  role?: string | null;
  record_type?: string | null;
  // voted
  title?: string;
  house?: string;
  number?: number;
  ayes?: number;
  noes?: number;
  vote?: string;
  against_party?: boolean;
  party_ayes?: number;
  party_noes?: number;
  url?: string | null;
  debate_path?: string | null;
  vote_type?: VoteType;
}

interface TimelineData {
  topic: string;
  items: TimelineItem[];
  vote_summary?: { aye: number; no: number; against_party: number; procedural: number } | null;
  said: number;
  votes: number;
  note: string;
}

const VOTE: Record<string, string> = { aye: "Aye", no: "No" };

/** A member's or a party's contributions on a topic, and their votes, newest first. Free: no AI. */
export function Timeline({ query, showSpeaker }: { query: string; showSpeaker: boolean }) {
  const [data, setData] = useState<TimelineData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    fetch(`/api/timeline?${query}`)
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The timeline could not be made.");
        setData(body as TimelineData);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "The timeline could not be made."));
  }, [query]);

  if (error) return <p className="form-error" role="alert">{error}</p>;
  if (!data) return <p className="muted" role="status">Building the timeline…</p>;
  if (!data.items.length) return <p className="muted">Nothing found on “{data.topic}”. Try other words.</p>;
  return (
    <section className="timeline" aria-label={`Timeline: ${data.topic}`}>
      <p className="muted small">
        {data.said} contribution{data.said === 1 ? "" : "s"} and {data.votes} vote{data.votes === 1 ? "" : "s"}.{" "}
        {data.note} Votes are matched by the titles of divisions, so some relevant votes may not appear.
      </p>
      {data.vote_summary && (
        <p className="timeline-summary">
          On the {data.votes} vote{data.votes === 1 ? "" : "s"} found: {data.vote_summary.aye} aye, {data.vote_summary.no} no
          {data.vote_summary.against_party > 0
            ? `, ${data.vote_summary.against_party} against the majority of their party`
            : ", none against the majority of their party"}
          .{data.vote_summary.procedural > 0 &&
            ` ${data.vote_summary.procedural} ${data.vote_summary.procedural === 1 ? "was" : "were"} on the timetable or procedure rather than the substance.`}{" "}
          Many votes are on amendments: see what each was on below.
        </p>
      )}
      <ol className="timeline-list">
        {data.items.map((i, k) => (
          <li key={i.record_id ?? `${i.date}-${i.number}-${k}`} className={`timeline-item ${i.kind}`}>
            <p className="timeline-date">
              <time dateTime={i.date}>{fmtDate(i.date)}</time>
            </p>
            {i.kind === "said" ? (
              <div>
                <p className="timeline-head">
                  {showSpeaker && i.speaker && <strong>{i.speaker}{i.role ? `, ${i.role}` : ""}. </strong>}
                  {!showSpeaker && i.role && <strong>As {i.role}. </strong>}
                  {i.heading}
                </p>
                <p className="timeline-excerpt">{i.excerpt}</p>
                {i.debate_path ? (
                  <a className="small" href={i.debate_path}>
                    Read it in the debate
                  </a>
                ) : (
                  i.url && (
                    <a className="small" href={i.url} target="_blank" rel="noreferrer">
                      Read it in full
                    </a>
                  )
                )}
              </div>
            ) : (
              <div>
                <p className="timeline-head">
                  <span className="vote-badge">Vote</span> {i.title}
                </p>
                <p className="timeline-excerpt">
                  {i.vote
                    ? `Voted ${VOTE[i.vote] ?? i.vote}${i.against_party ? ", against the majority of their party" : ""}.`
                    : `The party's members: ${i.party_ayes ?? 0} Aye, ${i.party_noes ?? 0} No.`}{" "}
                  Result: Ayes {i.ayes}, Noes {i.noes} ({i.house === "lords" ? "Lords" : "Commons"} division {i.number}).
                </p>
                {i.vote_type && (
                  <p className="small">
                    <VoteKind vt={i.vote_type} detail />
                  </p>
                )}
                {i.url && (
                  <a className="small" href={i.url} target="_blank" rel="noreferrer">
                    The division
                  </a>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
