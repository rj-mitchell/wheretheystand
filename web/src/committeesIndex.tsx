/** Current committees of both Houses, grouped by House, the most recently active first. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { HOUSE_NAME, getJSON } from "./committeeApi";
import { fmtDate } from "./render";

interface Row {
  id: number;
  name: string;
  house: string;
  category: string | null;
  departments: string | null;
  inquiries: number;
  reports: number;
  last_session: string | null;
  last_active?: string | null;
}

function Index() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getJSON<{ committees: Row[] }>("/api/committees", "Not found.")
      .then((d) => setRows(d.committees))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load."));
  }, []);
  if (error) return <p role="alert">{error}</p>;
  if (!rows) return <p className="muted">Loading…</p>;
  const houses = ["Commons", "Lords", "Joint"].filter((h) => rows.some((r) => r.house === h));
  return (
    <>
      <form className="people-search-form" role="search" action="/witnesses" method="get">
        <label htmlFor="w-q">Who gave evidence? Search an organisation or person</label>
        <div className="ask-row">
          <input id="w-q" name="q" placeholder="For example: NHS England, Which?, Greenpeace" />
          <button type="submit" className="primary">
            Search
          </button>
        </div>
      </form>
      {houses.map((h) => (
        <section key={h} aria-labelledby={`h-${h}`}>
          <h2 id={`h-${h}`}>{HOUSE_NAME[h] ?? h}</h2>
          <ul className="edm-list committee-list">
            {rows
              .filter((r) => r.house === h)
              .map((r) => (
                <li key={r.id}>
                  <a href={`/committees/${r.id}`}>{r.name}</a>
                  <span className="muted small">
                    {" "}
                    {r.inquiries} inquir{r.inquiries === 1 ? "y" : "ies"}, {r.reports} report{r.reports === 1 ? "" : "s"}
                    {r.last_active ? ` · last active ${fmtDate(r.last_active)}` : ""}
                  </span>
                </li>
              ))}
          </ul>
        </section>
      ))}
    </>
  );
}

createRoot(document.getElementById("committees-root")!).render(
  <StrictMode>
    <Index />
  </StrictMode>,
);
