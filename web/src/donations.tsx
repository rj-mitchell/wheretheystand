/** Donations to MPs: search donors, the biggest donors of the last five years, the latest donations, one donor's. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { type Donation, donorLink, MONEY, what } from "./components/Donations";
import { fmtDate } from "./render";

interface Donor {
  donor: string;
  donor_status: string | null;
  total: number;
  n: number;
  mps: number;
  last?: string;
}
interface Index {
  available: boolean;
  top_donors?: Donor[];
  recent?: Donation[];
  built?: string;
}

function Member({ d }: { d: Donation }) {
  const m = d.member;
  if (!m) return <>{d.recipient}</>;
  return (
    <>
      {m.number ? <a href={`/people/${m.number}/${m.slug}`}>{m.name}</a> : m.name}
      {m.party && <span className="muted small"> ({m.party})</span>}
    </>
  );
}

function DonationTable({ items, showDonor }: { items: Donation[]; showDonor: boolean }) {
  return (
    <div className="table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Accepted</th>
            {showDonor && <th scope="col">Donor</th>}
            <th scope="col">MP</th>
            <th scope="col">Value</th>
            <th scope="col">What</th>
            <th scope="col">Record</th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.ec_ref}>
              <td>{d.accepted ? fmtDate(d.accepted) : "—"}</td>
              {showDonor && <td>{donorLink(d.donor)}</td>}
              <td>
                <Member d={d} />
                {d.kind === "local_party" && <span className="muted small"> (to their local party: {d.unit})</span>}
              </td>
              <td>{d.value != null ? MONEY.format(d.value) : "—"}</td>
              <td className="small">{what(d)}</td>
              <td className="small">
                <a href={d.url}>{d.ec_ref}</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DonorList({ donors }: { donors: Donor[] }) {
  return (
    <ol className="edm-list">
      {donors.map((d) => (
        <li key={`${d.donor}-${d.donor_status}`}>
          {donorLink(d.donor)}
          {d.donor_status && <span className="muted small"> ({d.donor_status.toLowerCase()})</span>}:{" "}
          {MONEY.format(d.total)} in {d.n} donation{d.n === 1 ? "" : "s"} to {d.mps} MP{d.mps === 1 ? "" : "s"}
          {d.last ? `, most recently ${fmtDate(d.last)}` : ""}
        </li>
      ))}
    </ol>
  );
}

function OneDonor({ name }: { name: string }) {
  const [rows, setRows] = useState<Donation[] | null>(null);
  useEffect(() => {
    fetch(`/api/donations/donor?name=${encodeURIComponent(name)}`)
      .then((r) => (r.ok ? r.json() : { donations: [] }))
      .then((d: { donations: Donation[] }) => setRows(d.donations))
      .catch(() => setRows([]));
    document.title = `${name}: donations to MPs — Where They Stand`;
  }, [name]);
  if (!rows) return <p className="muted">Loading…</p>;
  const total = rows.reduce((t, d) => t + (d.value ?? 0), 0);
  const personal = rows.filter((d) => d.kind !== "local_party");
  const local = rows.filter((d) => d.kind === "local_party");
  return (
    <section aria-labelledby="h-donor">
      <p className="eyebrow">
        <a href="/donations">All donations</a>
      </p>
      <h2 id="h-donor">{name}</h2>
      <p>
        {rows.length === 0
          ? "No donations from this donor are matched to MPs."
          : `${rows.length} donation${rows.length === 1 ? "" : "s"} matched to MPs or their local parties, worth ${MONEY.format(total)} in all.`}
      </p>
      {personal.length > 0 && (
        <>
          <h3>To MPs</h3>
          <DonationTable items={personal} showDonor={false} />
        </>
      )}
      {local.length > 0 && (
        <>
          <h3>To MPs' local parties</h3>
          <DonationTable items={local} showDonor={false} />
        </>
      )}
    </section>
  );
}

function Search() {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Donor[] | null>(null);
  useEffect(() => {
    if (!q) return;
    fetch(`/api/donations/donors?q=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : { donors: [] }))
      .then((d: { donors: Donor[] }) => setFound(d.donors))
      .catch(() => setFound([]));
  }, [q]);
  return (
    <section aria-labelledby="h-search">
      <h2 id="h-search">Find a donor</h2>
      <form
        className="people-search-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const v = new FormData(e.currentTarget).get("q");
          setQ(typeof v === "string" ? v.trim() : "");
        }}
      >
        <label htmlFor="donor-q">Company, union, organisation or person</label>
        <div className="ask-row">
          <input id="donor-q" name="q" placeholder="For example: Unite" />
          <button type="submit" className="primary">
            Search
          </button>
        </div>
      </form>
      {found && (
        <>
          <p className="muted small" aria-live="polite">
            {found.length} donor{found.length === 1 ? "" : "s"} found.
          </p>
          <DonorList donors={found} />
        </>
      )}
    </section>
  );
}

function Page() {
  const donor = new URLSearchParams(window.location.search).get("donor");
  const [d, setD] = useState<Index | null>(null);
  useEffect(() => {
    if (donor) return;
    fetch("/api/donations")
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then(setD)
      .catch(() => setD({ available: false }));
  }, [donor]);
  if (donor) return <OneDonor name={donor} />;
  if (!d) return <p className="muted">Loading…</p>;
  if (!d.available) return <p>The register of donations has not been loaded yet. It is added at the next weekly update.</p>;
  return (
    <>
      <Search />
      <section aria-labelledby="h-top">
        <h2 id="h-top">Biggest donors to MPs in the last five years</h2>
        <p className="muted small">Donations to MPs personally (not to parties), by total value.</p>
        <DonorList donors={d.top_donors ?? []} />
      </section>
      <section aria-labelledby="h-recent">
        <h2 id="h-recent">Latest donations to MPs</h2>
        <DonationTable items={d.recent ?? []} showDonor />
        {d.built && <p className="muted small">Register last downloaded {fmtDate(d.built.slice(0, 10))}.</p>}
      </section>
    </>
  );
}

const root = document.getElementById("donations-root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Page />
    </StrictMode>,
  );
}
