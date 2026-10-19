/** All-party parliamentary groups: search, the list by subject, and their biggest registered funders. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { groupPath } from "./components/Groups";
import { MONEY } from "./components/Donations";
import { fmtDate } from "./render";

interface Group {
  slug: string;
  title: string;
  category: string | null;
  purpose: string | null;
  officers: number;
  benefits: number;
  has_secretariat: number;
}
interface Index {
  available: boolean;
  parliament?: string;
  groups?: Group[];
  funders?: { funder: string; groups: number; at_least: number | null }[];
  as_of?: string;
}

const PARLIAMENTS: [string, string][] = [
  ["uk", "UK Parliament (all-party groups)"],
  ["scottish-parliament", "Scottish Parliament (cross-party groups)"],
  ["senedd-en", "Senedd (cross-party groups)"],
  ["ni-assembly", "Northern Ireland Assembly (all-party groups)"],
];

function Page() {
  const params = new URLSearchParams(window.location.search);
  const [parliament, setParliament] = useState(params.get("parliament") ?? "uk");
  const [q, setQ] = useState(params.get("q") ?? "");
  const [d, setD] = useState<Index | null>(null);
  useEffect(() => {
    fetch(`/api/groups?parliament=${encodeURIComponent(parliament)}&q=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then(setD)
      .catch(() => setD({ available: false }));
  }, [parliament, q]);
  if (!d) return <p className="muted">Loading…</p>;
  if (!d.available) return <p>The groups have not been loaded yet. They are added at the next weekly update.</p>;
  const groups = d.groups ?? [];
  const byCategory = new Map<string, Group[]>();
  for (const g of groups) {
    const k = g.category || "Subject groups";
    byCategory.set(k, [...(byCategory.get(k) ?? []), g]);
  }
  return (
    <>
      <form
        className="people-search-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          setQ(String(f.get("q") ?? "").trim());
          setParliament(String(f.get("parliament") ?? "uk"));
        }}
      >
        <label htmlFor="group-q">Subject, country or organisation</label>
        <div className="ask-row">
          <input id="group-q" name="q" defaultValue={q} placeholder="For example: cancer, or Saudi Arabia" />
          <button type="submit" className="primary">
            Search
          </button>
        </div>
        <label htmlFor="group-parl">Parliament</label>
        <select id="group-parl" name="parliament" defaultValue={parliament} style={{ maxWidth: "100%" }}>
          {PARLIAMENTS.map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </form>
      <p className="muted small" aria-live="polite">
        {groups.length} group{groups.length === 1 ? "" : "s"}
        {q ? ` matching “${q}”` : ""}
        {d.as_of ? `, as listed on ${fmtDate(d.as_of)}` : ""}.
      </p>
      {d.funders && d.funders.length > 0 && (
        <section aria-labelledby="h-funders">
          <h2 id="h-funders">Biggest registered funders</h2>
          <p className="muted small">
            Money and benefits in kind (such as running a group's secretariat) registered by groups in the current
            register. Values are often given in bands, so totals are the least they could be.
          </p>
          <ol className="edm-list">
            {d.funders.map((f) => (
              <li key={f.funder}>
                <a href={`/groups?funder=${encodeURIComponent(f.funder)}`}>{f.funder}</a>: at least{" "}
                {MONEY.format(f.at_least ?? 0)}, to {f.groups} group{f.groups === 1 ? "" : "s"}
              </li>
            ))}
          </ol>
        </section>
      )}
      {[...byCategory.entries()].map(([cat, items]) => (
        <section key={cat} aria-label={cat}>
          <h2>{cat}</h2>
          <ul className="edm-list">
            {items.map((g) => (
              <li key={g.slug}>
                <a href={groupPath({ parliament, slug: g.slug })}>{g.title}</a>
                {g.benefits > 0 && (
                  <span className="muted small">
                    {" "}
                    · {g.benefits} registered benefit{g.benefits === 1 ? "" : "s"}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

function Funder({ name }: { name: string }) {
  const [rows, setRows] = useState<{ slug: string; parliament: string; title: string; type: string; value: string | null; received: string | null; description: string | null }[] | null>(null);
  useEffect(() => {
    fetch(`/api/groups/funder?name=${encodeURIComponent(name)}`)
      .then((r) => (r.ok ? r.json() : { benefits: [] }))
      .then((d) => setRows(d.benefits))
      .catch(() => setRows([]));
    document.title = `${name}: funding of all-party groups — Where They Stand`;
  }, [name]);
  if (!rows) return <p className="muted">Loading…</p>;
  return (
    <section aria-labelledby="h-funder">
      <p className="eyebrow">
        <a href="/groups">All groups</a>
      </p>
      <h2 id="h-funder">{name}</h2>
      <p>Benefits registered by all-party groups as received from {name}:</p>
      <ul className="edm-list">
        {rows.map((b, i) => (
          <li key={i}>
            <a href={groupPath(b)}>{b.title}</a>: {b.type?.toLowerCase()}
            {b.value ? `, £${b.value}` : ""}
            {b.received ? `, received ${fmtDate(b.received)}` : ""}
            {b.description && <p className="small muted">{b.description}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

const root = document.getElementById("groups-root");
if (root) {
  const funder = new URLSearchParams(window.location.search).get("funder");
  createRoot(root).render(<StrictMode>{funder ? <Funder name={funder} /> : <Page />}</StrictMode>);
}
