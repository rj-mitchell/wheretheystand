/** The devolved parliaments: recent sitting days for each, and a search of what members said. Free: no AI. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { fmtDate } from "./render";

interface Day {
  key: string;
  date: string;
  speeches: number;
  speakers: number;
  headings: string[];
}
interface Body {
  id: string;
  name: string;
  member: string;
  speeches: number;
  from?: string;
  to?: string;
  days: Day[];
}
interface Hit {
  id: number;
  legislature: string;
  body: string;
  key: string;
  date: string;
  ord: number;
  major: string | null;
  snippet: string;
  speaker: { name: string | null; party: string | null; seat: string | null };
}

/** "[word]" marks the matches in a snippet: shown as <mark>, the rest as text. */
function Snippet({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\[[^\]]*\])/).map((part, k) =>
        part.startsWith("[") ? <mark key={k}>{part.slice(1, -1)}</mark> : <span key={k}>{part}</span>,
      )}
    </>
  );
}

function Search({ bodies }: { bodies: Body[] }) {
  const params = new URLSearchParams(window.location.search);
  const [q, setQ] = useState(params.get("q") ?? "");
  const [leg, setLeg] = useState(params.get("leg") ?? "");
  const [res, setRes] = useState<{ total: number; items: Hit[] } | null>(null);
  useEffect(() => {
    if (!q) {
      setRes(null);
      return;
    }
    const p = new URLSearchParams({ q });
    if (leg) p.set("leg", leg);
    fetch(`/api/devolved/search?${p}`)
      .then((r) => r.json())
      .then(setRes)
      .catch(() => setRes({ total: 0, items: [] }));
    window.history.replaceState(null, "", `/devolved?${p}`);
  }, [q, leg]);
  return (
    <section aria-labelledby="h-search">
      <h2 id="h-search">Search</h2>
      <form
        className="people-search-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          setQ(String(f.get("q") ?? "").trim());
          setLeg(String(f.get("leg") ?? ""));
        }}
      >
        <label htmlFor="dv-q">Words to find</label>
        <div className="ask-row">
          <input id="dv-q" name="q" defaultValue={q} placeholder="For example: waiting times, ferries, teacher pay" />
          <label className="visually-hidden" htmlFor="dv-leg">
            Parliament
          </label>
          <select id="dv-leg" name="leg" defaultValue={leg}>
            <option value="">All three</option>
            {bodies.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
          <button type="submit" className="primary">
            Search
          </button>
        </div>
      </form>
      {res && (
        <>
          <p className="muted small" aria-live="polite">
            {res.total.toLocaleString("en-GB")} contribution{res.total === 1 ? "" : "s"} found, newest first.
          </p>
          <ol className="edm-list">
            {res.items.map((h) => (
              <li key={h.id}>
                <p className="timeline-date">
                  {h.body} · <a href={`/devolved/${h.legislature}/${h.key}#s${h.ord}`}>{fmtDate(h.date)}</a>
                  {h.major ? ` · ${h.major}` : ""}
                </p>
                <p>
                  <strong>{h.speaker.name}</strong>
                  {(h.speaker.party || h.speaker.seat) && (
                    <span className="muted"> ({[h.speaker.party, h.speaker.seat].filter(Boolean).join(", ")})</span>
                  )}
                  : <Snippet text={h.snippet} />
                </p>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function Index() {
  const [bodies, setBodies] = useState<Body[] | null>(null);
  useEffect(() => {
    fetch("/api/devolved")
      .then((r) => r.json())
      .then((d: { legislatures: Body[] }) => setBodies(d.legislatures))
      .catch(() => setBodies([]));
  }, []);
  // a link to one parliament (/devolved#h-senedd) arrives before its section exists: go there once it is drawn
  useEffect(() => {
    if (bodies && window.location.hash) document.getElementById(window.location.hash.slice(1))?.scrollIntoView();
  }, [bodies]);
  if (!bodies) return <p className="muted">Loading…</p>;
  const empty = bodies.every((b) => !b.speeches);
  return (
    <>
      {empty && <p className="muted">The records are being collected and will appear after the next update.</p>}
      <Search bodies={bodies} />
      {bodies.map((b) => (
        <section key={b.id} aria-labelledby={`h-${b.id}`}>
          <h2 id={`h-${b.id}`}>{b.name}</h2>
          {b.days.length === 0 ? (
            <p className="muted">Not collected yet.</p>
          ) : (
            <>
              <p className="muted small">
                {b.speeches.toLocaleString("en-GB")} contributions
                {b.from && b.to ? `, ${fmtDate(b.from)} to ${fmtDate(b.to)}` : ""}. The most recent sittings:
              </p>
              <ul className="edm-list">
                {b.days.map((d) => (
                  <li key={d.key}>
                    <a href={`/devolved/${b.id}/${d.key}`}>{fmtDate(d.date)}</a>
                    <span className="muted small">
                      {" "}
                      · {d.headings.slice(0, 4).join("; ")}
                      {d.headings.length > 4 ? "…" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      ))}
    </>
  );
}

createRoot(document.getElementById("devolved-root")!).render(
  <StrictMode>
    <Index />
  </StrictMode>,
);
