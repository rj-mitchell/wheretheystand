import { fmtDate, LONG } from "../render";

export interface InterestItem {
  summary: string;
  details: [string, string][];
  registered: string | null;
  published: string | null;
  children: InterestItem[];
}
interface Change {
  category: string;
  summary: string;
}

export interface Interests {
  edition: string | null;
  source: string | null;
  official: string;
  listed: boolean;
  former?: boolean;
  changes?: { since: string; added: Change[]; removed: Change[]; removed_tracked_from?: string | null };
  // registers other than the Commons
  register?: string;
  register_name?: string;
  member_word?: string;
  body?: string;
  categories: { type: string; name: string; items: InterestItem[] }[];
}

const MONEY = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });

function show(name: string, value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return fmtDate(value);
  if (/^(value|amount)$/i.test(name) && /^\d+(\.\d+)?$/.test(value)) return MONEY.format(Number(value));
  return value;
}

function Item({ item }: { item: InterestItem }) {
  return (
    <li className="interest">
      <p className="interest-summary">{item.summary}</p>
      {item.details.length > 0 && (
        <dl className="interest-details small">
          {item.details.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{show(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {item.registered && <p className="muted small">Registered {item.registered}</p>}
      {item.children.length > 0 && (
        <details className="interest-children" open={item.children.length <= LONG}>
          <summary>
            {item.children.length} payment{item.children.length === 1 ? "" : "s"} or specific piece
            {item.children.length === 1 ? "" : "s"} of work
          </summary>
          <ul>
            {item.children.map((c, k) => (
              <Item key={k} item={c} />
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

/** A member's entries in the latest Register of Members' Financial Interests, as they registered them. */
export function InterestsSection({ interests, name, isMP }: { interests: Interests; name: string; isMP: boolean }) {
  if (!interests.listed && !isMP) return null;
  const total = interests.categories.reduce((n, c) => n + c.items.length, 0);
  const register = interests.register_name ?? "Register of Members' Financial Interests";
  const id = `h-interests${interests.register ? `-${interests.register}` : ""}`;
  return (
    <section aria-labelledby={id}>
      <h2 id={id}>Registered interests{interests.body && interests.register !== "lords" ? ` (${interests.body})` : ""}</h2>
      <p>
        {interests.former
          ? `When ${name} left the Commons, they had ${total} entr${total === 1 ? "y" : "ies"} in the Register of ` +
            `Members' Financial Interests (the last edition before they left, published ${interests.edition ? fmtDate(interests.edition) : ""}).`
          : `${
              interests.listed && total > 0
                ? `${name} has ${total} entr${total === 1 ? "y" : "ies"} in the ${register}`
                : `${name} has no entries in the ${register}`
            }${interests.edition ? `, as ${interests.register === "lords" ? "read" : "published"} on ${fmtDate(interests.edition)}` : ""}.`}
      </p>
      <p className="muted small">
        {interests.register
          ? `Members of the ${interests.body} must register their paid work, shareholdings, property, gifts, visits and other interests set out in its rules. `
          : "MPs must register their jobs and earnings, donations, gifts, visits abroad, property and shareholdings. "}
        These are declarations, shown as registered: an entry is not a finding of wrongdoing, and being listed here does
        not mean an interest influenced anything the member said or did. Addresses listed separately are left out. Read
        the <a href={interests.official}>official register</a>.
      </p>
      {interests.changes && <Changes changes={interests.changes} />}
      {interests.categories.map((c) => (
        <details key={c.type + c.name} className="interest-category" open={c.items.length <= LONG}>
          <summary>
            {c.name} ({c.items.length})
          </summary>
          <ul className="interests">
            {c.items.map((it, k) => (
              <Item key={k} item={it} />
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function ChangeList({ items }: { items: Change[] }) {
  return (
    <ul className="interests">
      {items.map((c, k) => (
        <li key={k} className="interest">
          <p className="interest-summary">{c.summary}</p>
          <p className="muted small">{c.category}</p>
        </li>
      ))}
    </ul>
  );
}

/** What is listed now but was not a year earlier, and the reverse, compared by wording. */
function Changes({ changes }: { changes: NonNullable<Interests["changes"]> }) {
  const { added, removed, since } = changes;
  if (added.length === 0 && removed.length === 0) {
    const tracked = changes.removed_tracked_from;
    return tracked && tracked > since ? (
      <p className="small">
        No new entries registered since {fmtDate(since)}. Entries that are removed are tracked from {fmtDate(tracked)}, when
        this site began reading the register.
      </p>
    ) : (
      <p className="small">No entries have been added or removed since {fmtDate(since)}.</p>
    );
  }
  return (
    <details className="interest-category" open={added.length + removed.length <= LONG}>
      <summary>
        Changes since {fmtDate(since)}: {added.length} new, {removed.length} no longer listed
      </summary>
      <p className="muted small">
        {changes.removed_tracked_from !== undefined
          ? `New entries are those registered since ${fmtDate(since)}. Entries no longer listed are counted from ${fmtDate(changes.removed_tracked_from ?? since)}, when this site began reading the register.`
          : `Compared by wording with the edition published on ${fmtDate(since)}, so an entry whose wording was updated appears in both lists.`}{" "}
        Entries usually leave the register when the interest ends or after a set time.
      </p>
      {added.length > 0 && (
        <>
          <h3>New entries</h3>
          <ChangeList items={added} />
        </>
      )}
      {removed.length > 0 && (
        <>
          <h3>No longer listed</h3>
          <ChangeList items={removed} />
        </>
      )}
    </details>
  );
}
