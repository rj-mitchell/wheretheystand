/** Company roles of MPs and peers, by company, from Companies House: search by company or member. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { fmtDate } from "./render";

interface Holder {
  name: string;
  number: number | null;
  slug: string;
  current: boolean;
  role: string;
  from: string | null;
}
interface Company {
  company: string | null;
  number: string;
  status: string | null;
  url: string;
  members: Holder[];
}
interface Index {
  companies: Company[];
  total_companies: number;
  members_checked: number;
  members_with_directorships: number;
  last_run: string | null;
}

function Holders({ c }: { c: Company }) {
  return (
    <>
      {c.members.map((m, i) => (
        <span key={`${m.number}-${m.role}`}>
          {i > 0 && "; "}
          {m.number ? (
            <a href={`/people/${m.number}/${m.slug}`}>{m.name}</a>
          ) : (
            m.name
          )}
          <span className="muted small">
            {" "}
            ({m.role.toLowerCase()}
            {m.from ? ` since ${fmtDate(m.from)}` : ""}
            {m.current ? "" : "; no longer a member"})
          </span>
        </span>
      ))}
    </>
  );
}

function Page() {
  const [q, setQ] = useState(
    () => new URLSearchParams(window.location.search).get("q") ?? "",
  );
  const [d, setD] = useState<Index | null>(null);
  useEffect(() => {
    fetch(`/api/directorships?q=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setD)
      .catch(() => setD(null));
  }, [q]);
  return (
    <>
      <form
        className="people-search-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const v = new FormData(e.currentTarget).get("q");
          setQ(typeof v === "string" ? v.trim() : "");
        }}
      >
        <label htmlFor="dir-q">Company or member</label>
        <div className="ask-row">
          <input
            id="dir-q"
            name="q"
            defaultValue={q}
            placeholder="A company or a name"
          />
          <button type="submit" className="primary">
            Search
          </button>
        </div>
      </form>
      {!d ? (
        <p className="muted">Loading…</p>
      ) : d.members_checked === 0 ? (
        <p>
          Company roles have not been collected yet. They are added a few
          hundred members at a time at each update.
        </p>
      ) : (
        <>
          <p className="muted small" aria-live="polite">
            {q
              ? `${d.total_companies} compan${d.total_companies === 1 ? "y" : "ies"} found.`
              : `${d.members_with_directorships} of ${d.members_checked} members checked so far hold or have held a company role; ` +
                `${d.total_companies} companies have a current one.`}
            {d.last_run && ` Last checked ${fmtDate(d.last_run.slice(0, 10))}.`}
          </p>
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th scope="col">Company</th>
                  <th scope="col">Members with a current role</th>
                </tr>
              </thead>
              <tbody>
                {d.companies.map((c) => (
                  <tr key={c.number}>
                    <td>
                      <a href={c.url}>{c.company ?? c.number}</a>
                      {c.status && c.status !== "active" && (
                        <span className="muted small"> ({c.status})</span>
                      )}
                    </td>
                    <td>
                      <Holders c={c} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.companies.length < d.total_companies && (
            <p className="muted small">
              The first {d.companies.length} are shown; search to narrow them
              down.
            </p>
          )}
        </>
      )}
    </>
  );
}

const root = document.getElementById("directorships-root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Page />
    </StrictMode>,
  );
}
