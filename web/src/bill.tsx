/** One bill: its journey through Parliament sitting by sitting, its divisions, and a way to ask about it. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { VoteKind, VoteKindKey } from "./components/VoteKind";
import { ELSEWHERE, Elsewhere } from "./elsewhere";
import { fmtDate } from "./render";
import { stageQuestion } from "./stage";
import type { VoteType } from "./types";

interface Sitting {
  date: string;
  house: string;
  source: string;
  stages: string[];
  contributions: number;
  url: string | null;
  path?: string | null;
}
interface Division {
  date: string;
  house: string;
  number: number;
  title: string;
  ayes: number;
  noes: number;
  url: string | null;
  vote_type?: VoteType;
}
interface BillData {
  slug: string;
  name: string;
  first: string;
  last: string;
  houses: string[];
  sittings: Sitting[];
  divisions: Division[];
}

const HOUSE: Record<string, string> = { commons: "Commons", lords: "Lords" };


function Bill({ slug }: { slug: string }) {
  const [b, setB] = useState<BillData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/bills/${slug}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "There is no bill at this address." : "The bill could not be loaded.");
        return r.json() as Promise<BillData>;
      })
      .then((d) => {
        setB(d);
        document.title = `${d.name} — Where MPs Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "The bill could not be loaded."));
  }, [slug]);

  if (error) return <p role="alert">{error} <a href="/bills">All bills</a></p>;
  if (!b) return <p className="muted">Loading…</p>;
  const range = `between ${fmtDate(b.first)} and ${fmtDate(b.last)}`;
  const question = `What arguments were made for and against the ${b.name} ${range}?`;
  return (
    <article className="bill">
      <p className="eyebrow">
        <a href="/bills">Bills</a> · {b.houses.map((h) => HOUSE[h]).join(" and ")}
      </p>
      <h1>{b.name}</h1>
      <p className="lede">
        Debated {range}: {b.sittings.length} sitting{b.sittings.length === 1 ? "" : "s"} and {b.divisions.length} vote
        {b.divisions.length === 1 ? "" : "s"} in the record.
      </p>

      <form className="ask-about" action="/" method="get">
        <label htmlFor="bill-q">Ask about this bill</label>
        <div className="ask-row">
          <input id="bill-q" name="q" defaultValue={question} />
          <button type="submit" className="primary">
            Ask
          </button>
        </div>
        <p className="muted small">Opens the question on the main page, where you can change it before asking.</p>
      </form>

      <h2 id="h-journey">Its journey through Parliament</h2>
      <ol className="timeline-list">
        {b.sittings.map((s) => (
          <li key={`${s.date}-${s.house}-${s.source}`} className="timeline-item">
            <p className="timeline-date">
              <time dateTime={s.date}>{fmtDate(s.date)}</time> · {HOUSE[s.house] ?? s.house}
            </p>
            <p className="timeline-head">{s.stages.join("; ")}</p>
            <p className="timeline-excerpt small">
              {s.contributions} contribution{s.contributions === 1 ? "" : "s"}
              {s.contributions > 0 && (
                <>
                  {" · "}
                  <a href={`/?q=${encodeURIComponent(stageQuestion(b.name, s))}`}>Ask about this stage</a>
                </>
              )}
              {s.path ? (
                <>
                  {" · "}
                  <a href={s.path}>Read the debate</a>
                </>
              ) : (
                s.url && (
                  <>
                    {" · "}
                    <a href={s.url} target="_blank" rel="noreferrer">
                      Read the record
                    </a>
                  </>
                )
              )}
            </p>
          </li>
        ))}
      </ol>
      <p className="muted small">
        Stages are read from the record's headings and the standard wording of motions, so a few sittings are labelled
        only "Debate".
      </p>
      <Elsewhere
        links={[
          { href: ELSEWHERE.bills(b.name), text: "Parliament's bills site", note: "the bill's text, amendments, stages and briefings" },
          { href: ELSEWHERE.commonsLibrary(b.name), text: "House of Commons Library", note: "impartial briefings on the bill" },
          {
            href: ELSEWHERE.legislation(b.name.replace(/\s*\[(HL|Lords)\]\s*/gi, " ").replace(/\bBill\b/, "Act").trim()),
            text: "legislation.gov.uk",
            note: "the Act as passed and amended, if it became law",
          },
        ]}
      />

      {b.divisions.length > 0 && (
        <section aria-labelledby="h-divisions">
          <h2 id="h-divisions">Votes</h2>
          <p className="muted small">
            Many votes are on single clauses, amendments or the timetable rather than on the bill as a whole. The kind of
            each vote is read from the motion recorded just before it; open the record to see exactly what was decided.
          </p>
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">House</th>
                  <th scope="col">Kind of vote</th>
                  <th scope="col">On</th>
                  <th scope="col">Ayes</th>
                  <th scope="col">Noes</th>
                </tr>
              </thead>
              <tbody>
                {b.divisions.map((d) => (
                  <tr key={`${d.date}-${d.house}-${d.number}`}>
                    <td>{fmtDate(d.date)}</td>
                    <td>{HOUSE[d.house] ?? d.house}</td>
                    <td>{d.vote_type && d.vote_type.kind !== "other" ? <VoteKind vt={d.vote_type} /> : "Not stated"}</td>
                    <td>{d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.title}</a> : d.title}</td>
                    <td>{d.ayes}</td>
                    <td>{d.noes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>What these kinds of vote mean</h3>
          <VoteKindKey types={b.divisions.map((d) => d.vote_type)} />
        </section>
      )}
    </article>
  );
}

const slug = window.location.pathname.match(/^\/bills\/([a-z0-9-]+)\/?$/)?.[1];
createRoot(document.getElementById("bill-root")!).render(
  <StrictMode>{slug ? <Bill slug={slug} /> : <p role="alert">No bill was given.</p>}</StrictMode>,
);
