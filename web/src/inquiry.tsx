/** One inquiry: its reports and the government's responses, and every oral evidence session with its witnesses. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { getJSON } from "./committeeApi";
import { fmtDate } from "./render";

interface Witness {
  name: string | null;
  organisation: string | null;
  role: string | null;
}
interface Inquiry {
  id: number;
  title: string;
  type: string | null;
  open: string | null;
  close: string | null;
  url: string;
  committees: { id: number; name: string; house: string }[];
  publications: { id: number; type: string; title: string; date: string | null; hc: string | null; hl: string | null; url: string; days_after_report?: number }[];
  sessions: { id: number; date: string | null; url: string; witnesses: Witness[]; transcript?: boolean }[];
}

function Page({ id }: { id: string }) {
  const [d, setD] = useState<Inquiry | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Inquiry>(`/api/inquiries/${id}`, "There is no inquiry with that number.")
      .then((x) => {
        setD(x);
        document.title = `${x.title} — Where MPs Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load."));
  }, [id]);
  if (error)
    return (
      <p role="alert">
        {error} <a href="/committees">All committees</a>
      </p>
    );
  if (!d) return <p className="muted">Loading…</p>;
  const question = `What have MPs and peers said about ${d.title.replace(/\.$/, "")}?`;
  return (
    <article>
      <p className="eyebrow">
        <a href="/committees">Committees</a>
        {d.committees.map((c) => (
          <span key={c.id}>
            {" · "}
            <a href={`/committees/${c.id}`}>{c.name}</a>
          </span>
        ))}
      </p>
      <h1>{d.title}</h1>
      <p className="lede">
        {d.type ?? "Inquiry"}
        {d.open ? ` opened ${fmtDate(d.open)}` : ""}
        {d.close ? `, closed ${fmtDate(d.close)}` : ""}. {d.sessions.length} oral evidence session
        {d.sessions.length === 1 ? "" : "s"}.
      </p>
      <p className="small">
        <a href={d.url} target="_blank" rel="noreferrer">
          The inquiry on Parliament's website
        </a>{" "}
        (written evidence and other papers are there)
      </p>

      <form className="ask-about" action="/" method="get">
        <label htmlFor="inq-ask">Ask about this subject</label>
        <div className="ask-row">
          <input id="inq-ask" name="q" defaultValue={question} />
          <button type="submit" className="primary">
            Ask
          </button>
        </div>
        <p className="muted small">Opens the question on the main page, where you can change it before asking.</p>
      </form>

      {d.publications.length > 0 && (
        <>
          <h2 id="h-reports">Reports and responses</h2>
          <ul className="edm-list">
            {d.publications.map((p) => (
              <li key={p.id}>
                <span className="week-kind">{p.type}</span>
                <a href={p.url} target="_blank" rel="noreferrer">
                  {p.title}
                </a>
                <span className="muted small">
                  {" "}
                  {p.date ? fmtDate(p.date) : ""}
                  {p.hc ? ` · ${/^HC/i.test(p.hc) ? p.hc : `HC ${p.hc}`}` : ""}
                  {p.hl ? ` · ${/^HL/i.test(p.hl) ? p.hl : `HL Paper ${p.hl}`}` : ""}
                  {p.days_after_report != null ? ` · ${p.days_after_report} days after the report` : ""}
                </span>
              </li>
            ))}
          </ul>
          <p className="muted small">The government usually aims to respond to a committee report within two months.</p>
        </>
      )}

      {d.sessions.length > 0 && (
        <>
          <h2 id="h-sessions">Who gave oral evidence</h2>
          <ol className="edm-list">
            {d.sessions.map((s) => (
              <li key={s.id}>
                <p className="timeline-head">
                  {s.transcript ? (
                    <a href={`/evidence/${s.id}`}>{s.date ? fmtDate(s.date) : "Session"}: read the transcript</a>
                  ) : (
                    <a href={s.url} target="_blank" rel="noreferrer">
                      {s.date ? fmtDate(s.date) : "Session"}
                    </a>
                  )}
                </p>
                <ul className="witness-list">
                  {s.witnesses.map((w, k) => (
                    <li key={k}>
                      {w.name}
                      {(w.role || w.organisation) && (
                        <span className="muted">
                          {", "}
                          {[w.role, w.organisation].filter(Boolean).join(", ")}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </>
      )}
    </article>
  );
}

const id = window.location.pathname.match(/^\/inquiries\/(\d+)\/?$/)?.[1];
createRoot(document.getElementById("inquiry-root")!).render(
  <StrictMode>{id ? <Page id={id} /> : <p role="alert">No inquiry was given.</p>}</StrictMode>,
);
