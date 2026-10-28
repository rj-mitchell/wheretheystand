/** In Parliament this week: each sitting day's debates, votes and statements, from the record. Free: no AI. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { VoteKind } from "./components/VoteKind";
import { ELSEWHERE, Elsewhere } from "./elsewhere";
import { fmtDate } from "./render";
import type { VoteType } from "./types";

interface Debate {
  title: string;
  kind: "Questions" | "Statement" | null;
  said: number;
  path: string | null;
}
interface Division {
  division_id: string;
  house: string;
  number: number;
  title: string;
  ayes: number;
  noes: number;
  path: string | null;
  vote_type?: VoteType;
}
interface Day {
  date: string;
  chambers: { chamber: string; name: string; debates: Debate[] }[];
  short_items: number;
  divisions: Division[];
}
interface WeekData {
  start: string;
  end: string;
  days: Day[];
  divisions: number;
  votes_loaded_to?: string | null;
  written_questions: number;
  written_statements: { date: string; title: string; department: string | null; path: string | null }[];
  lords_written_statements: number;
  previous: string | null;
  next: string | null;
  pause?: Pause | null;
}
interface Pause {
  last_sat: string;
  commons_last_sat: string | null;
  lords_last_sat: string | null;
  record_to: string;
  recess: boolean;
  written_answers_since: number;
  written_statements_since: number;
}

/** Shown on "this week" when neither House has sat for over a week. */
function PauseNote({ p }: { p: Pause }) {
  const houses =
    p.commons_last_sat && p.lords_last_sat && p.commons_last_sat !== p.lords_last_sat
      ? `the Commons last sat on ${fmtDate(p.commons_last_sat)} and the Lords on ${fmtDate(p.lords_last_sat)}`
      : `it last sat on ${fmtDate(p.last_sat)}`;
  const since = [
    p.written_answers_since > 0 &&
      `${p.written_answers_since.toLocaleString("en-GB")} written answer${p.written_answers_since === 1 ? " has" : "s have"} been published`,
    p.written_statements_since > 0 &&
      `${p.written_statements_since} written statement${p.written_statements_since === 1 ? "" : "s"} made`,
  ].filter(Boolean);
  return (
    <p className="shared-note" role="status" style={{ marginBlockEnd: "var(--space-m)" }}>
      {p.recess ? (
        <>
          <strong>Parliament is in recess:</strong> {houses}. Here is the last week it sat.
          {since.length > 0 && ` Since then, ${since.join(" and ")}.`} Sittings appear here the morning after they
          happen.
        </>
      ) : (
        <>
          <strong>No sittings since {fmtDate(p.last_sat)}</strong>, and the latest record is from {fmtDate(p.record_to)}.
          The record may not have been updated: see the <a href="/status">status page</a>. Here is the last week
          Parliament sat.
        </>
      )}
    </p>
  );
}

const WEEKDAY = new Intl.DateTimeFormat("en-GB", { weekday: "long" });
const KIND: Record<string, string> = { Questions: "Questions", Statement: "Statement" };

function Week({ start }: { start: string | null }) {
  const [w, setW] = useState<WeekData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(start ? `/api/week?start=${start}` : "/api/week")
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "Parliament did not sit that week." : "The week could not be loaded.");
        return r.json() as Promise<WeekData>;
      })
      .then((x) => {
        setW(x);
        document.title = `In Parliament, week of ${fmtDate(x.start)} — Where MPs Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "The week could not be loaded."));
  }, [start]);

  if (error)
    return (
      <p role="alert">
        {error} <a href="/this-week">The latest week</a>
      </p>
    );
  if (!w) return <p className="muted">Loading…</p>;
  const debates = w.days.reduce((n, d) => n + d.chambers.reduce((m, c) => m + c.debates.length, 0), 0);
  return (
    <article className="week">
      {w.pause && <PauseNote p={w.pause} />}
      <p className="eyebrow">In Parliament</p>
      <h1>
        Week of {fmtDate(w.start)}
      </h1>
      <p className="lede">
        {w.days.length} sitting day{w.days.length === 1 ? "" : "s"}, {debates} debates and statements,{" "}
        {votesText(w)} and {w.written_questions.toLocaleString("en-GB")} written answers, from the official record.
        Every debate can be read in full.
      </p>
      <p className="muted small">
        This page is usually a day or two behind, because it is built from the published record. For today's
        business, Parliament's own sites will be quicker:{" "}
        <a href="https://hansard.parliament.uk/" rel="noreferrer">
          Hansard
        </a>{" "}
        publishes each debate within hours, and{" "}
        <a href="https://whatson.parliament.uk/" rel="noreferrer">
          What's on
        </a>{" "}
        lists what is coming up.
      </p>
      <nav className="week-nav" aria-label="Other weeks">
        {w.previous ? <a href={`/weeks/${w.previous}`}>← Week of {fmtDate(w.previous)}</a> : <span />}
        {w.next && <a href={`/weeks/${w.next}`}>Week of {fmtDate(w.next)} →</a>}
      </nav>

      {w.days.map((d) => (
        <section key={d.date} aria-labelledby={`h-${d.date}`} className="week-day">
          <h2 id={`h-${d.date}`}>
            {WEEKDAY.format(new Date(`${d.date}T12:00:00`))} {fmtDate(d.date)}
          </h2>
          {d.chambers.map((c) => (
            <div key={c.chamber} className="week-chamber">
              <h3>{c.name}</h3>
              <ul className="week-debates">
                {c.debates.map((x) => (
                  <li key={x.title}>
                    {x.kind && <span className="week-kind">{KIND[x.kind]}</span>}
                    {x.path ? <a href={x.path}>{x.title}</a> : x.title}{" "}
                    <span className="muted small">
                      ({x.said} contribution{x.said === 1 ? "" : "s"})
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {d.divisions.length > 0 && (
            <div className="week-chamber">
              <h3>Votes</h3>
              <ul className="week-votes">
                {d.divisions.map((v) => (
                  <li key={v.division_id}>
                    <span className="week-kind">{v.house === "lords" ? "Lords" : "Commons"}</span>
                    {v.path ? <a href={v.path}>{v.title}</a> : v.title}: Ayes {v.ayes}, Noes {v.noes}.
                    {v.vote_type && (
                      <>
                        {" "}
                        <VoteKind vt={v.vote_type} />
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {d.short_items > 0 && (
            <p className="muted small">
              Also {d.short_items} short item{d.short_items === 1 ? "" : "s"} (points of order, announcements and the
              like).
            </p>
          )}
        </section>
      ))}

      {w.written_statements.length > 0 && (
        <section aria-labelledby="h-statements">
          <h2 id="h-statements">Written statements to the Commons</h2>
          <ul>
            {w.written_statements.map((s) => (
              <li key={s.date + s.title}>
                {fmtDate(s.date)}: {s.path ? <a href={s.path}>{s.title}</a> : s.title}
                {s.department && <span className="muted"> ({s.department})</span>}
              </li>
            ))}
          </ul>
          {w.lords_written_statements > 0 && (
            <p className="muted small">The Lords published {w.lords_written_statements} written statements too.</p>
          )}
        </section>
      )}

      <p className="muted small">
        Listed from the record as published, which can take a day or two to appear. Items with fewer than three
        contributions are counted but not listed. Ask about anything here on the <a href="/">main page</a>.
      </p>
      <Elsewhere
        label="Coming up"
        links={[
          { href: ELSEWHERE.whatsOn, text: "Parliament's What's on calendar", note: "business for the days ahead" },
          { href: ELSEWHERE.parliamentTv, text: "Parliament TV", note: "watch live or catch up" },
        ]}
      />
    </article>
  );
}

const m = window.location.pathname.match(/^\/weeks\/(\d{4}-\d{2}-\d{2})\/?$/);
createRoot(document.getElementById("week-root")!).render(
  <StrictMode>
    <Week start={m ? m[1] : null} />
  </StrictMode>,
);

/** The week's votes, saying so when they have not been loaded yet rather than counting none. */
function votesText(w: { divisions: number; votes_loaded_to?: string | null; start: string; days: { date: string }[] }) {
  const lastDay = w.days.at(-1)?.date ?? w.start;
  const loaded = w.votes_loaded_to;
  if (loaded !== undefined && (loaded === null || loaded < w.start) && w.divisions === 0)
    return "votes not loaded yet";
  const n = `${w.divisions} vote${w.divisions === 1 ? "" : "s"}`;
  return loaded && loaded < lastDay ? `${n} (votes loaded up to ${fmtDate(loaded)})` : n;
}
