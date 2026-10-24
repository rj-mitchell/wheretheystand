/** One oral evidence session: the transcript, turn by turn, with the witnesses and the committee. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { getJSON } from "./committeeApi";
import { fmtDate } from "./render";

interface Transcript {
  session_id: number;
  title: string | null;
  header: string[];
  turns: { ord: number; speaker: string; question: string | null; text: string }[];
  date?: string;
  committees?: string;
  committee_id?: number;
  inquiry_id?: number;
  inquiry?: string;
  url?: string;
  witnesses?: { name: string | null; organisation: string | null; role: string | null }[];
}

function Page({ id }: { id: string }) {
  const [t, setT] = useState<Transcript | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Transcript>(`/api/evidence/${id}`, "There is no transcript for that session here yet.")
      .then((x) => {
        setT(x);
        document.title = `${x.title ?? "Oral evidence"} — Where MPs Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load."));
  }, [id]);
  useEffect(() => {
    if (!t || !window.location.hash) return;
    const el = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    if (el) {
      el.classList.add("is-target");
      el.scrollIntoView({ block: "start" });
    }
  }, [t]);
  if (error)
    return (
      <p role="alert">
        {error} <a href="/committees">Committees</a>
      </p>
    );
  if (!t) return <p className="muted">Loading…</p>;
  const witnessNames = new Set((t.witnesses ?? []).map((w) => w.name));
  return (
    <article className="debate">
      <p className="eyebrow">
        <a href="/committees">Committees</a>
        {t.committee_id && t.committees ? (
          <>
            {" · "}
            <a href={`/committees/${t.committee_id}`}>{t.committees}</a>
          </>
        ) : null}
      </p>
      <h1>{t.inquiry ?? t.title ?? "Oral evidence"}</h1>
      <p className="lede">
        Oral evidence{t.date ? `, ${fmtDate(t.date)}` : ""}. Witnesses answer the committee's questions; what they
        say is their evidence, not a view of Parliament.
      </p>
      <p className="small">
        {t.inquiry_id && <a href={`/inquiries/${t.inquiry_id}`}>The inquiry</a>}
        {t.url && (
          <>
            {" · "}
            <a href={t.url} target="_blank" rel="noreferrer">
              The transcript on Parliament's website
            </a>
          </>
        )}
      </p>
      {t.witnesses && t.witnesses.length > 0 && (
        <>
          <h2 id="h-witnesses">Witnesses</h2>
          <ul>
            {t.witnesses.map((w, k) => (
              <li key={k}>
                {w.name}
                {(w.role || w.organisation) && <span className="muted">, {[w.role, w.organisation].filter(Boolean).join(", ")}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      <details className="same-day">
        <summary>About this session (as published)</summary>
        {t.header.map((h, k) => (
          <p key={k} className="small">
            {h}
          </p>
        ))}
      </details>
      <h2 id="h-transcript">Transcript</h2>
      <div className="debate-record">
        {t.turns.map((u) => (
          <div key={u.ord} id={`t${u.ord}`} className="contrib">
            <p className="contrib-speaker">
              <strong>{u.speaker}</strong>
              {witnessNames.has(u.speaker) && <span className="week-kind"> Witness</span>}
              {u.question && <span className="muted small"> {u.question}</span>}
            </p>
            {u.text.split("\n\n").map((p, k) => (
              <p key={k}>{p}</p>
            ))}
            <p className="contrib-ref muted small">
              <a href={`#t${u.ord}`}>Link</a>
            </p>
          </div>
        ))}
      </div>
      <p className="muted small">
        Transcripts marked “uncorrected” may still be revised by Parliament. Parliamentary information licensed under the
        Open Parliament Licence v3.0.
      </p>
    </article>
  );
}

const id = window.location.pathname.match(/^\/evidence\/(\d+)\/?$/)?.[1];
createRoot(document.getElementById("evidence-root")!).render(
  <StrictMode>{id ? <Page id={id} /> : <p role="alert">No session was given.</p>}</StrictMode>,
);
