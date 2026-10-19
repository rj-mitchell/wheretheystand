/** Early day motions: search, newest first, or those a member tabled or signed. Free: no AI. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { EdmList, type EdmRow } from "./components/EdmList";

interface Listing {
  total: number;
  page: number;
  per_page: number;
  items: EdmRow[];
  available: boolean;
}

function Edms() {
  const params = new URLSearchParams(window.location.search);
  const person = params.get("person");
  const [q, setQ] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(Number(params.get("page") ?? "1") || 1);
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const p = new URLSearchParams({ q, page: String(page) });
    if (person) p.set("person", person);
    setError(null);
    fetch(`/api/edms?${p}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "There is no member with that number." : "Could not load.");
        return r.json() as Promise<Listing>;
      })
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load."));
    window.history.replaceState(null, "", `/edms?${p}`);
  }, [q, page, person]);

  const pages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1;
  return (
    <>
      <form
        className="people-search-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const v = new FormData(e.currentTarget).get("q");
          setPage(1);
          setQ(typeof v === "string" ? v : "");
        }}
      >
        <label htmlFor="edm-q">Search the motions</label>
        <div className="ask-row">
          <input id="edm-q" name="q" defaultValue={q} placeholder="For example: hospices, rail fares, Gaza" />
          <button type="submit" className="primary">
            Search
          </button>
        </div>
      </form>
      {person && (
        <p className="small">
          Showing motions tabled or signed by one member. <a href="/edms">Show all motions</a>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {data && !data.available && (
        <p className="muted">
          The motions are being collected from Parliament and will appear here after the next update.
        </p>
      )}
      {data && data.available && (
        <>
          <p aria-live="polite" className="muted small">
            {data.total.toLocaleString("en-GB")} motion{data.total === 1 ? "" : "s"}
            {q ? ` matching “${q}”` : ""}, newest first.
          </p>
          <EdmList items={data.items} />
          {pages > 1 && (
            <nav className="week-nav" aria-label="Pages">
              {page > 1 ? (
                <button type="button" className="secondary" onClick={() => setPage(page - 1)}>
                  ← Newer
                </button>
              ) : (
                <span />
              )}
              <span className="muted small">
                Page {page} of {pages}
              </span>
              {page < pages ? (
                <button type="button" className="secondary" onClick={() => setPage(page + 1)}>
                  Older →
                </button>
              ) : (
                <span />
              )}
            </nav>
          )}
        </>
      )}
    </>
  );
}

const root = document.getElementById("edms-root");
if (root)
  createRoot(root).render(
    <StrictMode>
      <Edms />
    </StrictMode>,
  );
