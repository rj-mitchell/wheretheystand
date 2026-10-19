import { fmtDate, LONG } from "../render";

export interface Donation {
  ec_ref: string;
  kind: "mp" | "leadership" | "party_member" | "local_party";
  match: string | null;
  recipient: string;
  unit: string | null;
  constituency: string | null;
  donor: string | null;
  donor_status: string | null;
  value: number | null;
  donation_type: string | null;
  nature: string | null;
  purpose: string | null;
  accepted: string | null;
  sponsorship: boolean;
  url: string;
  member?: { name: string; number: number | null; slug: string; party: string | null; constituency: string | null } | null;
}

export interface MemberDonations {
  personal: Donation[];
  local: Donation[];
  totals: { personal?: number; personal_count?: number; local?: number; local_count?: number };
  top_donors?: { donor: string; value: number }[];
  source: string;
}

export const MONEY = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 });

const KIND: Record<Donation["kind"], string> = {
  mp: "",
  leadership: "for a party leadership campaign",
  party_member: "as a party member (for example a deputy leadership campaign)",
  local_party: "to the local party",
};

/** What the donation was: cash, or what was given in kind. */
export function what(d: Donation): string {
  const kind = d.donation_type === "Visit" && d.purpose ? `Visit: ${d.purpose}` : d.donation_type;
  const bits = [kind, d.nature, d.sponsorship ? "sponsorship" : null];
  return bits.filter((b, i) => b && bits.indexOf(b) === i).join("; ");
}

export function donorLink(name: string | null) {
  return name ? <a href={`/donations?donor=${encodeURIComponent(name)}`}>{name}</a> : "Not stated";
}

function Rows({ items, local = false }: { items: Donation[]; local?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Accepted</th>
            <th scope="col">Donor</th>
            <th scope="col">Value</th>
            <th scope="col">What</th>
            {local && <th scope="col">Local party</th>}
            <th scope="col">Record</th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.ec_ref}>
              <td>{d.accepted ? fmtDate(d.accepted) : "—"}</td>
              <td>
                {donorLink(d.donor)}
                {d.donor_status && <span className="muted small"> ({d.donor_status.toLowerCase()})</span>}
              </td>
              <td>{d.value != null ? MONEY.format(d.value) : "—"}</td>
              <td className="small">
                {what(d)}
                {KIND[d.kind] && <span className="muted"> {KIND[d.kind]}</span>}
              </td>
              {local && <td className="small">{d.unit}</td>}
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

/** Donations registered with the Electoral Commission: to the MP, and (separately) to their local party. */
export function DonationsSection({ donations, name }: { donations: MemberDonations; name: string }) {
  const { personal, local, totals } = donations;
  if (personal.length === 0 && local.length === 0) return null;
  return (
    <section aria-labelledby="h-donations">
      <h2 id="h-donations">Political donations</h2>
      <p>
        {personal.length > 0
          ? `${totals.personal_count} donation${totals.personal_count === 1 ? "" : "s"} to ${name}, worth ${MONEY.format(totals.personal ?? 0)} in all, ` +
            "are registered with the Electoral Commission"
          : `No donations to ${name} personally are matched in the Electoral Commission's register`}
        {local.length > 0
          ? `, and ${totals.local_count} to their local party, worth ${MONEY.format(totals.local ?? 0)}.`
          : "."}
      </p>
      <p className="muted small">
        Donations over the reporting limit must be reported to the Electoral Commission; it publishes them, with the
        donor's name. A donation is not a finding of wrongdoing, and does not show that it influenced anything the
        member said or did. Records are matched to members by name and date, so a few may be missing; each links to
        the Commission's own record. See <a href="/methodology#donations">how donations are matched</a>. Loans, and
        donations to parties nationally, are on the{" "}
        <a href="https://search.electoralcommission.org.uk/" rel="noreferrer">
          Electoral Commission's register
        </a>
        . Contains Electoral Commission Information © Electoral Commission and/or database right.
      </p>
      {donations.top_donors && donations.top_donors.length > 1 && (
        <p className="small">
          Largest donors to {name}:{" "}
          {donations.top_donors.map((t, i) => (
            <span key={t.donor}>
              {i > 0 && "; "}
              {donorLink(t.donor)} ({MONEY.format(t.value)})
            </span>
          ))}
          .
        </p>
      )}
      {personal.length > 0 && (
        <details className="interest-category" open={personal.length <= LONG}>
          <summary>Donations to {name} ({personal.length})</summary>
          <Rows items={personal} />
        </details>
      )}
      {local.length > 0 && (
        <details className="interest-category" open={local.length <= LONG}>
          <summary>Donations to their local party ({local.length})</summary>
          <p className="muted small">
            Given to their party's local organisation in the constituency they represented, while they were its MP:
            not to the MP personally.
          </p>
          <Rows items={local} local />
        </details>
      )}
    </section>
  );
}
