import { fmtDate, LONG } from "../render";

export interface Appointment {
  company: string | null;
  number: string | null;
  status: string | null;
  role: string;
  from: string | null;
  from_before: boolean;
  until: string | null;
  while_member: boolean;
  declared?: boolean;
  current: boolean;
  company_url: string | null;
}

export interface MemberDirectorships {
  appointments: Appointment[];
  officers: { name: string; url: string }[];
  checked: string | null;
  source: string;
}

function when(a: Appointment): string {
  const from = a.from
    ? `${a.from_before ? "before " : ""}${fmtDate(a.from)}`
    : "date not given";
  if (a.until) return `${from} to ${fmtDate(a.until)}`;
  return a.current ? `since ${from}` : `from ${from} until the company closed`;
}

export const DIRECTORSHIPS_NOTE =
  "Companies House does not link its records to Parliament. A record is shown only when the surname, a first or " +
  "middle name and the month and year of birth all match the member's; a person can still share all three with " +
  "someone else, so check the Companies House record. Holding a company role is not a finding of wrongdoing; " +
  "what members must declare is in their register of interests.";

function Rows({ items }: { items: Appointment[] }) {
  return (
    <div className="table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Company</th>
            <th scope="col">Role</th>
            <th scope="col">When</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={`${a.number}-${a.role}-${a.from}`}>
              <td>
                {a.company_url ? (
                  <a href={a.company_url}>{a.company ?? a.number}</a>
                ) : (
                  a.company
                )}
                {a.status && a.status !== "active" && (
                  <span className="muted small"> ({a.status})</span>
                )}
                {a.declared && (
                  <span className="muted small">
                    {" "}
                    (also in their register of interests)
                  </span>
                )}
              </td>
              <td>{a.role}</td>
              <td className="small">
                {when(a)}
                {a.while_member && a.until && (
                  <span className="muted"> (partly while a member)</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Company roles from the Companies House register, matched by name and month and year of birth. */
export function DirectorshipsSection({
  d,
  name,
}: {
  d: MemberDirectorships;
  name: string;
}) {
  if (d.appointments.length === 0) return null;
  const now = d.appointments.filter((a) => a.current);
  const past = d.appointments.filter((a) => !a.current);
  return (
    <section aria-labelledby="h-directorships">
      <h2 id="h-directorships">Company roles</h2>
      <p>
        {now.length > 0
          ? `Companies House lists ${name} as holding ${now.length} company role${now.length === 1 ? "" : "s"} now`
          : `Companies House lists no current company roles for ${name}`}
        {past.length > 0 ? `, and ${past.length} earlier.` : "."}
      </p>
      <p className="muted small">
        {DIRECTORSHIPS_NOTE} Records used:{" "}
        {d.officers.map((o, i) => (
          <span key={o.url}>
            {i > 0 && ", "}
            <a href={o.url}>{o.name}</a>
          </span>
        ))}
        {d.checked && `; checked ${fmtDate(d.checked)}`}. See{" "}
        <a href="/methodology#directorships">how they are matched</a>, and{" "}
        <a href="/corrections#report">tell us</a> if one is not theirs.
      </p>
      {now.length > 0 && (
        <details className="interest-category" open={now.length <= LONG}>
          <summary>Current ({now.length})</summary>
          <Rows items={now} />
        </details>
      )}
      {past.length > 0 && (
        <details className="interest-category" open={past.length <= LONG}>
          <summary>Earlier ({past.length})</summary>
          <Rows items={past} />
        </details>
      )}
    </section>
  );
}
