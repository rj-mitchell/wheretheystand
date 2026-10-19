/** One all-party or cross-party group: purpose, officers, members, secretariat and registered benefits. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { fmtDate } from "./render";

interface Member {
  person_id: string | null;
  name: string;
  role: string | null;
  is_officer: number;
  source_label: string;
  list_url: string | null;
  member?: { name: string; number: number | null; slug: string; party: string | null } | null;
}
interface Benefit {
  funder: string | null;
  type: string | null;
  value: string | null;
  received: string | null;
  registered: string | null;
  description: string | null;
}
interface Group {
  slug: string;
  parliament: string;
  parliament_name: string;
  title: string;
  purpose: string | null;
  category: string | null;
  secretariat: string | null;
  website: string | null;
  source_url: string | null;
  agm: string | null;
  members: Member[];
  benefits: Benefit[];
  official: string;
  dataset: string;
  as_of: string | null;
}

function Person({ m }: { m: Member }) {
  const p = m.member;
  return (
    <>
      {p?.number ? <a href={`/people/${p.number}/${p.slug}`}>{p.name}</a> : m.name}
      {p?.party && <span className="muted small"> ({p.party})</span>}
      {m.role && <span>: {m.role}</span>}
    </>
  );
}

/** Links written into the register's text, made clickable. */
function Linked({ text }: { text: string }) {
  return (
    <>
      {text.split(/(https?:\/\/\S+)/).map((part, i) =>
        /^https?:\/\//.test(part) ? (
          <a key={i} href={part.replace(/[.,;)]+$/, "")} rel="noreferrer">
            {part.replace(/[.,;)]+$/, "")}
          </a>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

function Page({ parliament, slug }: { parliament: string; slug: string }) {
  const [g, setG] = useState<Group | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/groups/${encodeURIComponent(parliament)}/${encodeURIComponent(slug)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "There is no group at this address." : "The group could not be loaded.");
        return r.json() as Promise<Group>;
      })
      .then((d) => {
        setG(d);
        document.title = `${d.title} — Where They Stand`;
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "The group could not be loaded."));
  }, [parliament, slug]);
  if (error) return <p role="alert">{error} <a href="/groups">All groups</a></p>;
  if (!g) return <p className="muted">Loading…</p>;
  const officers = g.members.filter((m) => m.is_officer);
  const members = g.members.filter((m) => !m.is_officer);
  const linked = members.filter((m) => m.member);
  const others = members.filter((m) => !m.member);
  return (
    <article>
      <p className="eyebrow">
        <a href={`/groups${g.parliament === "uk" ? "" : `?parliament=${g.parliament}`}`}>Groups</a> · {g.parliament_name}
      </p>
      <h1>{g.title}</h1>
      {g.purpose && <p className="lede">{g.purpose}</p>}
      <p className="small">
        {g.category && <>{g.category}. </>}
        {g.agm && <>Last annual general meeting {fmtDate(g.agm)}. </>}
        {g.website && (
          <>
            <a href={g.website} rel="noreferrer">
              The group's website
            </a>
            {" · "}
          </>
        )}
        {g.source_url && <a href={g.source_url}>The official register entry</a>}
      </p>

      {g.secretariat && (
        <section aria-labelledby="h-sec">
          <h2 id="h-sec">Secretariat</h2>
          <p>
            <Linked text={g.secretariat} />
          </p>
        </section>
      )}

      <section aria-labelledby="h-benefits">
        <h2 id="h-benefits">Registered benefits</h2>
        {g.benefits.length === 0 ? (
          <p>No benefits over the registration threshold are registered.</p>
        ) : (
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th scope="col">From</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Value (£)</th>
                  <th scope="col">Received</th>
                </tr>
              </thead>
              <tbody>
                {g.benefits.map((b, i) => (
                  <tr key={i}>
                    <td>
                      {b.funder ? <a href={`/groups?funder=${encodeURIComponent(b.funder)}`}>{b.funder}</a> : "—"}
                      {b.description && <p className="small muted">{b.description}</p>}
                    </td>
                    <td className="small">{b.type}</td>
                    <td>{b.value ?? "—"}</td>
                    <td>{b.received ? fmtDate(b.received) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">Benefits in kind are registered in bands of £1,500.</p>
      </section>

      <section aria-labelledby="h-officers">
        <h2 id="h-officers">Officers</h2>
        <ul className="edm-list">
          {officers.map((m, i) => (
            <li key={i}>
              <Person m={m} />
            </li>
          ))}
        </ul>
      </section>

      {members.length > 0 && (
        <section aria-labelledby="h-members">
          <h2 id="h-members">Members</h2>
          <p className="muted small">
            From {members[0].source_label}
            {members[0].list_url && (
              <>
                {" "}
                (<a href={members[0].list_url}>source</a>)
              </>
            )}
            . Lists supplied by groups may be incomplete or out of date.
          </p>
          <ul className="edm-list">
            {linked.map((m, i) => (
              <li key={i}>
                <Person m={m} />
              </li>
            ))}
          </ul>
          {others.length > 0 && (
            <details className="interest-category">
              <summary>Also listed, not matched to a member of Parliament ({others.length})</summary>
              <p className="small">{others.map((m) => m.name).join("; ")}</p>
            </details>
          )}
        </section>
      )}
      <p className="muted small">
        From the Register of All-Party Parliamentary Groups{g.as_of ? `, as at ${fmtDate(g.as_of)}` : ""}, via{" "}
        <a href={g.dataset}>mySociety's APPG dataset</a> (CC BY 4.0). Belonging to a group is not an endorsement of its
        funders.
      </p>
    </article>
  );
}

const root = document.getElementById("group-root");
if (root) {
  const [, , parliament, slug] = window.location.pathname.split("/");
  createRoot(root).render(
    <StrictMode>
      {parliament && slug ? <Page parliament={parliament} slug={slug} /> : <p role="alert">No group was given.</p>}
    </StrictMode>,
  );
}
