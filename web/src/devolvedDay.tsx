/** One sitting of a devolved parliament: every contribution in order, with each member's party and seat on the day. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { fmtDate } from "./render";

interface Speaker {
  name: string | null;
  party: string | null;
  seat: string | null;
  role: string | null;
  number: number | null;
  slug: string | null;
}
type Item =
  | { kind: "heading"; id: string; text: string }
  | { kind: "said" | "procedural"; id: string; minor: string | null; time: string | null; url: string | null; speaker: Speaker | null; paragraphs: string[] };
interface Day {
  id: string;
  body: string;
  member: string;
  key: string;
  date: string;
  items: Item[];
  speakers: number;
  contributions: number;
  official_url: string | null;
}

function Sitting({ path }: { path: string }) {
  const [d, setD] = useState<Day | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api${path}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "There is no sitting at this address." : "Could not load.");
        return r.json() as Promise<Day>;
      })
      .then((x) => {
        setD(x);
        document.title = `${x.body}, ${fmtDate(x.date)} — Where MPs Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load."));
  }, [path]);
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
        {error} <a href="/devolved">Devolved parliaments</a>
      </p>
    );
  if (!d) return <p className="muted">Loading…</p>;
  const headings = d.items.filter((i) => i.kind === "heading");
  let minor: string | null = null;
  return (
    <article className="debate">
      <p className="eyebrow">
        <a href="/devolved">Devolved parliaments</a> · {d.body}
      </p>
      <h1>{fmtDate(d.date)}</h1>
      <p className="lede">
        {d.contributions} contributions from {d.speakers} members, as recorded in the official report. Each{" "}
        {d.member} is shown with their party and seat on the day.
      </p>
      {d.official_url && (
        <p className="small">
          <a href={d.official_url} target="_blank" rel="noreferrer">
            The official report
          </a>
        </p>
      )}
      {headings.length > 1 && (
        <nav className="toc" aria-label="In this sitting">
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
          const sub = i.minor && i.minor !== minor ? i.minor : null;
          minor = i.minor;
          const s = i.speaker;
          return (
            <div key={i.id}>
              {sub && <h3 className="debate-subsection">{sub}</h3>}
              <div id={i.id} className={i.kind === "said" ? "contrib" : "contrib procedural"}>
                {s && (
                  <p className="contrib-speaker">
                    <strong>{s.number && s.slug ? <a href={`/people/${s.number}/${s.slug}`}>{s.name}</a> : s.name}</strong>
                    {(s.party || s.seat) && <span className="muted"> ({[s.party, s.seat].filter(Boolean).join(", ")})</span>}
                    {s.role && <span className="contrib-role">{s.role}</span>}
                  </p>
                )}
                {i.paragraphs.map((p, k) => (
                  <p key={k}>{p}</p>
                ))}
                {i.kind === "said" && (
                  <p className="contrib-ref muted small">
                    {i.time ? `${i.time} · ` : ""}
                    <a href={`#${i.id}`}>Link</a>
                    {i.url && (
                      <>
                        {" · "}
                        <a href={i.url} target="_blank" rel="noreferrer">
                          Official report
                        </a>
                      </>
                    )}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </article>
  );
}

const path = window.location.pathname.match(/^\/devolved\/[a-z-]+\/[A-Za-z0-9_-]+\/?$/)?.[0];
createRoot(document.getElementById("devolvedday-root")!).render(
  <StrictMode>{path ? <Sitting path={path.replace(/\/$/, "")} /> : <p role="alert">No sitting was given.</p>}</StrictMode>,
);
