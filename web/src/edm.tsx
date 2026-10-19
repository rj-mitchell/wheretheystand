/** One early day motion: its text, who tabled it and who signed it, with their party on the day they signed. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import type { EdmMember } from "./components/EdmList";
import { fmtDate } from "./render";

interface Sponsor extends EdmMember {
  sponsoring_order: number | null;
  signed: string | null;
  withdrawn: string | null;
}
interface EdmData {
  id: number;
  uin: string | null;
  title: string;
  text: string | null;
  date_tabled: string | null;
  status: string | null;
  sponsors_count: number | null;
  amendment_to: number | null;
  prayer: number;
  sponsor: EdmMember;
  sponsors: Sponsor[];
  signatures_known: boolean;
  party_counts: [string, number][];
  amendments: { id: number; uin: string | null; title: string; sponsors_count: number | null }[];
  official_url: string;
}

function Member({ m }: { m: EdmMember }) {
  const name = m.number && m.slug ? <a href={`/people/${m.number}/${m.slug}`}>{m.name}</a> : m.name;
  return (
    <>
      {name}
      {m.party && <span className="muted"> ({m.party})</span>}
    </>
  );
}

function Edm({ id }: { id: string }) {
  const [e, setE] = useState<EdmData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/edms/${id}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "There is no early day motion with that number." : "Could not load.");
        return r.json() as Promise<EdmData>;
      })
      .then((x) => {
        setE(x);
        document.title = `${x.title} (early day motion) — Where They Stand`;
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load."));
  }, [id]);

  if (error)
    return (
      <p role="alert">
        {error} <a href="/edms">All early day motions</a>
      </p>
    );
  if (!e) return <p className="muted">Loading…</p>;
  const supporters = e.sponsors.filter((s) => !s.withdrawn);
  const withdrawn = e.sponsors.filter((s) => s.withdrawn);
  const question = `What have MPs said in Parliament about ${e.title.replace(/\.$/, "")}?`;
  return (
    <article className="edm">
      <p className="eyebrow">
        <a href="/edms">Early day motions</a>
        {e.uin ? ` · EDM ${e.uin}` : ""}
        {e.date_tabled ? ` · tabled ${fmtDate(e.date_tabled)}` : ""}
      </p>
      <h1>{e.title}</h1>
      <p className="lede">
        Tabled by <Member m={e.sponsor} />
        {e.sponsors_count != null && `, with ${e.sponsors_count} signature${e.sponsors_count === 1 ? "" : "s"}`}.
      </p>
      {e.amendment_to && (
        <p className="small">
          This is an amendment to <a href={`/edms/${e.amendment_to}`}>another motion</a>.
        </p>
      )}
      {e.prayer === 1 && (
        <p className="small">
          This motion prays against regulations: it asks for them to be annulled. See{" "}
          <a href="/glossary#affirmative-negative">the glossary</a>.
        </p>
      )}
      {e.text && <blockquote className="edm-text">{e.text}</blockquote>}
      <p className="small">
        <a href={e.official_url} target="_blank" rel="noreferrer">
          The motion on Parliament's website
        </a>
      </p>

      <form className="ask-about" action="/" method="get">
        <label htmlFor="edm-ask">Ask about this subject</label>
        <div className="ask-row">
          <input id="edm-ask" name="q" defaultValue={question} />
          <button type="submit" className="primary">
            Ask
          </button>
        </div>
        <p className="muted small">Opens the question on the main page, where you can change it before asking.</p>
      </form>

      <h2 id="h-signed">Who signed</h2>
      {!e.signatures_known ? (
        <p className="muted">The list of signatures has not been collected yet; it will appear after an update.</p>
      ) : (
        <>
          {e.party_counts.length > 0 && (
            <p>
              By party, on the day they signed:{" "}
              {e.party_counts.map(([p, n], k) => (
                <span key={p}>
                  {k > 0 && "; "}
                  {p} {n}
                </span>
              ))}
              .
            </p>
          )}
          <ul className="edm-sponsors">
            {supporters.map((s) => (
              <li key={`${s.name}-${s.signed}`}>
                <Member m={s} />
                {s.sponsoring_order ? <span className="week-kind"> Sponsor</span> : null}
                {s.signed && <span className="muted small"> {fmtDate(s.signed)}</span>}
              </li>
            ))}
          </ul>
          {withdrawn.length > 0 && (
            <details>
              <summary>
                {withdrawn.length} signature{withdrawn.length === 1 ? "" : "s"} later withdrawn
              </summary>
              <ul>
                {withdrawn.map((s) => (
                  <li key={`${s.name}-${s.withdrawn}`}>
                    <Member m={s} /> <span className="muted small">withdrawn {fmtDate(s.withdrawn!)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {e.amendments.length > 0 && (
        <>
          <h2 id="h-amendments">Amendments to this motion</h2>
          <ul>
            {e.amendments.map((a) => (
              <li key={a.id}>
                <a href={`/edms/${a.id}`}>{a.title}</a>
                {a.sponsors_count != null && <span className="muted small"> · {a.sponsors_count} signatures</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="muted small">
        From the UK Parliament early day motions service. Party is each member's party on the day they signed.
        Ministers, whips and the Speaker do not sign early day motions by convention.
      </p>
    </article>
  );
}

const id = window.location.pathname.match(/^\/edms\/(\d+)\/?$/)?.[1];
createRoot(document.getElementById("edm-root")!).render(
  <StrictMode>{id ? <Edm id={id} /> : <p role="alert">No motion was given.</p>}</StrictMode>,
);
