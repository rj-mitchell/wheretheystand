/** The lighter side: what made the House laugh, and other moments from the record, as Hansard has them. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { fmtDate } from "./render";

interface Moment {
  date: string;
  speaker: string | null;
  member: { name: string; number: number | null; slug: string; party: string | null } | null;
  heading: string | null;
  chamber: string | null;
  text: string;
  twfy_url: string | null;
  debate_path: string | null;
}
interface Edm {
  id: number;
  uin: string | null;
  title: string;
  date: string;
  tabled_by: string | null;
}
interface Data {
  laughter: Moment[];
  on_this_day: Moment[];
  edms: Edm[];
}

function Moments({ items, laugh }: { items: Moment[]; laugh: boolean }) {
  if (!items.length) return <p className="muted">None found in the record yet.</p>;
  return (
    <ol className="contributions moments">
      {items.map((m) => {
        const who = m.member?.name ?? m.speaker ?? "A member";
        return (
          <li key={`${m.date}-${who}-${m.text.slice(0, 20)}`}>
            <blockquote>
              <p>
                {m.text}
                {laugh && <span className="muted"> [Laughter.]</span>}
              </p>
            </blockquote>
            <p className="muted small">
              {m.member?.number ? <a href={`/people/${m.member.number}/${m.member.slug}`}>{who}</a> : who}
              {m.member?.party ? ` (${m.member.party})` : ""}, {fmtDate(m.date)}
              {m.heading ? ` · ${m.heading}` : ""} ·{" "}
              {m.debate_path ? <a href={m.debate_path}>read it in context</a> : m.twfy_url && <a href={m.twfy_url}>read it in context</a>}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function Lighter() {
  const [d, setD] = useState<Data | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    fetch("/api/lighter-side")
      .then((r) => (r.ok ? (r.json() as Promise<Data>) : Promise.reject(new Error())))
      .then(setD, () => setError(true));
  }, []);
  if (error) return <p role="alert">This page could not be loaded. Please try again later.</p>;
  if (!d) return <p className="muted">Loading…</p>;
  return (
    <>
      <section aria-labelledby="h-laughter">
        <h2 id="h-laughter">What made the House laugh</h2>
        <p className="muted small">The most recent moments the Official Report marks as laughter, newest first.</p>
        <Moments items={d.laughter} laugh />
      </section>
      <section aria-labelledby="h-this-week">
        <h2 id="h-this-week">This week in earlier years</h2>
        <Moments items={d.on_this_day} laugh />
      </section>
      <section aria-labelledby="h-edms">
        <h2 id="h-edms">Early day motions you might not expect</h2>
        <p className="muted small">
          Motions any MP can table to draw attention to something: here, those about food and drink, animals,
          birthdays, centenaries and records. Signing one costs nothing and binds no one.
        </p>
        {d.edms.length === 0 ? (
          <p className="muted">None found in the record yet.</p>
        ) : (
          <ul className="edm-list">
            {d.edms.map((e) => (
              <li key={e.id}>
                <a href={`/edms/${e.id}`}>{e.title}</a>
                <span className="muted small">
                  {" "}
                  {fmtDate(e.date)}
                  {e.tabled_by ? `, tabled by ${e.tabled_by}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

createRoot(document.getElementById("lighter-root")!).render(
  <StrictMode>
    <Lighter />
  </StrictMode>,
);
