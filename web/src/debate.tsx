/** One debate as recorded: every contribution in order, with each speaker as they were on the day. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { VoteKind } from "./components/VoteKind";
import { fmtDate } from "./render";
import type { VoteType } from "./types";

interface Speaker {
  name: string | null;
  party: string | null;
  constituency: string | null;
  roles: string[];
  number: number | null;
  slug?: string;
}
type Item =
  | { kind: "heading"; id: string; text: string }
  | {
      kind: "said" | "procedural";
      id: string;
      record_id: string;
      time: string | null;
      colnum: string | null;
      speaker: Speaker | null;
      speaker_name_raw: string | null;
      paragraphs: string[];
      official_url: string | null;
      twfy_url: string | null;
    }
  | {
      kind: "division";
      id: string;
      number: number;
      title: string;
      ayes: number;
      noes: number;
      url: string | null;
      vote_type?: VoteType;
    };
interface DebateData {
  title: string;
  date: string;
  chamber: string;
  chamber_name: string;
  kind?: "debate" | "statement";
  department?: string | null;
  house: string;
  official_url: string | null;
  contributions: number;
  speakers: number;
  items: Item[];
  same_day: { title: string; path: string }[];
}

function SpeakerLine({ s }: { s: Speaker }) {
  const name = s.number && s.slug ? <a href={`/people/${s.number}/${s.slug}`}>{s.name}</a> : s.name;
  const where = [s.party, s.constituency].filter(Boolean).join(", ");
  return (
    <p className="contrib-speaker">
      <strong>{name}</strong>
      {where && <span className="muted"> ({where})</span>}
      {s.roles.length > 0 && <span className="contrib-role">{s.roles.join("; ")}</span>}
    </p>
  );
}

function Debate({ path }: { path: string }) {
  const [d, setD] = useState<DebateData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api${path}`)
      .then(async (r) => {
        if (!r.ok)
          throw new Error(r.status === 404 ? "There is no debate at this address." : "The debate could not be loaded.");
        return r.json() as Promise<DebateData>;
      })
      .then((x) => {
        setD(x);
        document.title = `${x.title}, ${fmtDate(x.date)} — Where They Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "The debate could not be loaded."));
  }, [path]);

  // the address may point at one contribution: show it once the debate has loaded
  useEffect(() => {
    if (!d || !window.location.hash) return;
    const el = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    if (el) {
      el.classList.add("is-target");
      el.scrollIntoView({ block: "start" });
    }
  }, [d]);

  if (error)
    return (
      <p role="alert">
        {error} <a href="/">Ask a question</a>
      </p>
    );
  if (!d) return <p className="muted">Loading…</p>;
  const headings = d.items.filter((i) => i.kind === "heading");
  const statement = d.kind === "statement";
  const question = statement
    ? `What did the government say in its written statement on ${d.title} on ${fmtDate(d.date)}?`
    : `What was said in the ${d.chamber_name} debate on ${d.title} on ${fmtDate(d.date)}?`;
  return (
    <article className="debate">
      <p className="eyebrow">
        {statement ? `Written statement to the ${d.chamber_name}` : d.chamber_name}
        {d.department && ` · ${d.department}`} · <time dateTime={d.date}>{fmtDate(d.date)}</time>
      </p>
      <h1>{d.title}</h1>
      {statement ? (
        <p className="lede">
          A written ministerial statement: how the government tells Parliament about a decision or development without
          a debate. The minister is shown with their party, seat and role on the day.
        </p>
      ) : (
        <p className="lede">
          {d.contributions} contribution{d.contributions === 1 ? "" : "s"} from {d.speakers} member
          {d.speakers === 1 ? "" : "s"}, as recorded in Hansard. Each speaker is shown with their party, seat and role
          on the day.
        </p>
      )}
      <p className="small">
        {d.official_url && (
          <a href={d.official_url} target="_blank" rel="noreferrer">
            {statement ? "The statement on Parliament's website" : "The official record for this day"}
          </a>
        )}
      </p>

      <form className="ask-about" action="/" method="get">
        <label htmlFor="debate-q">Ask about this {statement ? "statement" : "debate"}</label>
        <div className="ask-row">
          <input id="debate-q" name="q" defaultValue={question} />
          <button type="submit" className="primary">
            Ask
          </button>
        </div>
        <p className="muted small">Opens the question on the main page, where you can change it before asking.</p>
      </form>

      {headings.length > 1 && (
        <nav className="toc" aria-label="In this debate">
          <ol>
            {headings.map((h) => (
              <li key={h.id}>
                <a href={`#${h.id}`}>{h.text}</a>
              </li>
            ))}
          </ol>
        </nav>
      )}

      <div className="debate-record">
        {d.items.map((i) => {
          if (i.kind === "heading")
            return (
              <h2 key={i.id} id={i.id} className="debate-section">
                {i.text}
              </h2>
            );
          if (i.kind === "division")
            return (
              <section key={i.id} id={i.id} className="debate-division" aria-label={`Division ${i.number}`}>
                <p>
                  <span className="vote-badge">Vote</span> <strong>Division {i.number}</strong>: Ayes {i.ayes}, Noes{" "}
                  {i.noes}.{" "}
                  {i.url && (
                    <a href={i.url} target="_blank" rel="noreferrer">
                      The division
                    </a>
                  )}
                </p>
                {i.vote_type && (
                  <p className="small">
                    <VoteKind vt={i.vote_type} detail />
                  </p>
                )}
              </section>
            );
          if (i.kind === "procedural")
            return (
              <div key={i.id} id={i.id} className="contrib procedural">
                {i.paragraphs.map((p, k) => (
                  <p key={k}>{p}</p>
                ))}
              </div>
            );
          return (
            <div key={i.id} id={i.id} className="contrib">
              {i.speaker && <SpeakerLine s={i.speaker} />}
              {i.paragraphs.map((p, k) => (
                <p key={k}>{p}</p>
              ))}
              <p className="contrib-ref muted small">
                {[i.time, i.colnum ? `col. ${i.colnum}` : null].filter(Boolean).join(" · ")}
                {" · "}
                <a href={`#${i.id}`} aria-label={`Link to this contribution by ${i.speaker?.name ?? "the speaker"}`}>
                  Link
                </a>
                {i.twfy_url && (
                  <>
                    {" · "}
                    <a href={i.twfy_url} target="_blank" rel="noreferrer">
                      TheyWorkForYou
                    </a>
                  </>
                )}
              </p>
            </div>
          );
        })}
      </div>

      {d.same_day.length > 0 && (
        <details className="same-day">
          <summary>
            {statement ? "Other written statements" : `Also in the ${d.chamber_name}`} on {fmtDate(d.date)} (
            {d.same_day.length})
          </summary>
          <ul>
            {d.same_day.map((o) => (
              <li key={o.path}>
                <a href={o.path}>{o.title}</a>
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

const path = window.location.pathname.match(/^\/debates\/\d{4}-\d{2}-\d{2}\/[a-z0-9-]+\/[a-z0-9-]+\/?$/)?.[0];
createRoot(document.getElementById("debate-root")!).render(
  <StrictMode>{path ? <Debate path={path.replace(/\/$/, "")} /> : <p role="alert">No debate was given.</p>}</StrictMode>,
);
