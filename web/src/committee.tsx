/** One committee: its remit, inquiries and the organisations it hears from most. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { HOUSE_NAME, getJSON } from "./committeeApi";
import { fmtDate } from "./render";

interface Committee {
  id: number;
  name: string;
  house: string;
  category: string | null;
  start: string | null;
  end: string | null;
  departments: string | null;
  purpose: string | null;
  url: string;
  inquiries: { id: number; title: string; type: string | null; open: string | null; close: string | null; sessions: number; reports: number }[];
  reports?: number;
  subcommittees: { id: number; name: string }[];
  top_organisations: { organisation: string; n: number }[];
}

function Page({ id }: { id: string }) {
  const [c, setC] = useState<Committee | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Committee>(`/api/committees/${id}`, "There is no committee with that number.")
      .then((x) => {
        setC(x);
        document.title = `${x.name} — Where MPs Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load."));
  }, [id]);
  if (error)
    return (
      <p role="alert">
        {error} <a href="/committees">All committees</a>
      </p>
    );
  if (!c) return <p className="muted">Loading…</p>;
  const current = c.inquiries.filter((i) => !i.close);
  const past = c.inquiries.filter((i) => i.close);
  const list = (items: Committee["inquiries"]) => (
    <ul className="edm-list">
      {items.map((i) => (
        <li key={i.id}>
          <a href={`/inquiries/${i.id}`}>{i.title}</a>
          <span className="muted small">
            {" "}
            {i.type && i.type !== "Inquiry" ? `${i.type} · ` : ""}
            {i.open && i.close && i.open === i.close
              ? `on ${fmtDate(i.open)}`
              : `${i.open ? `from ${fmtDate(i.open)}` : ""}${i.close ? ` to ${fmtDate(i.close)}` : ""}`}
            {` · ${i.sessions} evidence session${i.sessions === 1 ? "" : "s"}`}
            {i.reports ? `, ${i.reports} report${i.reports === 1 ? "" : "s"}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
  return (
    <article>
      <p className="eyebrow">
        <a href="/committees">Committees</a> · {HOUSE_NAME[c.house] ?? c.house}
      </p>
      <h1>{c.name}</h1>
      {c.purpose && <p className="lede">{c.purpose}</p>}
      {c.departments && <p>Scrutinises: {c.departments}.</p>}
      <p className="small">
        <a href={c.url} target="_blank" rel="noreferrer">
          The committee's page on Parliament's website
        </a>
        {c.end ? ` · This committee ended on ${fmtDate(c.end)}.` : ""}
      </p>
      {!!c.reports && (
        <p className="small">
          {c.reports} report{c.reports === 1 ? "" : "s"} in all
          {(() => {
            const inInquiries = c.inquiries.reduce((n, i) => n + i.reports, 0);
            const other = c.reports - inInquiries;
            return other > 0 ? `, ${other} of them not part of an inquiry (annual and special reports, for example)` : "";
          })()}
          .
        </p>
      )}
      {c.subcommittees.length > 0 && (
        <p className="small">
          Sub-committees:{" "}
          {c.subcommittees.map((s, k) => (
            <span key={s.id}>
              {k > 0 && "; "}
              <a href={`/committees/${s.id}`}>{s.name}</a>
            </span>
          ))}
        </p>
      )}
      {current.length > 0 && (
        <>
          <h2 id="h-current">Current inquiries</h2>
          {list(current)}
        </>
      )}
      {past.length > 0 && (
        <>
          <h2 id="h-past">Past inquiries</h2>
          {list(past)}
        </>
      )}
      {c.top_organisations.length > 0 && (
        <>
          <h2 id="h-heard">Heard from most often</h2>
          <p className="muted small">Organisations by the number of oral evidence sessions they gave evidence at.</p>
          <ul className="evidence-groups">
            {c.top_organisations.map((o) => (
              <li key={o.organisation}>
                <a href={`/witnesses?q=${encodeURIComponent(o.organisation)}`}>{o.organisation}</a>{" "}
                <span className="count">{o.n}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

const id = window.location.pathname.match(/^\/committees\/(\d+)\/?$/)?.[1];
createRoot(document.getElementById("committee-root")!).render(
  <StrictMode>{id ? <Page id={id} /> : <p role="alert">No committee was given.</p>}</StrictMode>,
);
