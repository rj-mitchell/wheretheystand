/** A member's page: who they are, seats and majorities, roles, recent contributions and voting record. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { Timeline } from "./components/Timeline";
import { EdmList, type EdmRow } from "./components/EdmList";
import { DirectorshipsSection, type MemberDirectorships } from "./components/Directorships";
import { DonationsSection, type MemberDonations } from "./components/Donations";
import { GroupsSection, type MemberGroup } from "./components/Groups";
import { ELSEWHERE, Elsewhere, type Link } from "./elsewhere";
import { type Interests, InterestsSection } from "./components/Interests";
import { hasVoteKind, VoteKind } from "./components/VoteKind";
import { fmtDate } from "./render";
import type { VoteType } from "./types";

interface Result {
  election: string;
  date: string;
  majority: number | null;
  pct: number | null;
  band: string | null;
  party: string | null;
}
interface Contribution {
  record_id: string;
  date: string;
  chamber: string;
  record_type: string;
  heading: string;
  excerpt: string;
  twfy_url: string;
  official_url: string | null;
  debate_path: string | null;
}

interface PersonData {
  number: number;
  name: string;
  slug: string;
  other_names: string[];
  status: string;
  age: number | null;
  age_approximate: boolean | null;
  first_elected: string | null;
  parliament_id?: string | null;
  current: { house: string; constituency: string | null; party: string | null } | null;
  memberships: { house: string; constituency: string | null; party: string | null; start: string; end: string | null }[];
  offices: { kind: string; title: string; start: string; end: string | null }[];
  seats: { constituency: string; start: string; end: string | null; results: Result[] }[];
  contributions?: Contribution[];
  contributions_more?: boolean;
  voting?: {
    votes: number;
    rebellions: number;
    free_vote_differences?: number;
    since: string | null;
    until: string | null;
    method: string;
    recent_rebellions: {
      date: string;
      title: string;
      vote: string;
      party_majority: string;
      twfy_url: string;
      vote_type?: VoteType;
    }[];
  } | null;
  edms?: { tabled: number; signed: number; recent: EdmRow[] } | null;
  interests?: Interests | null;
  donations?: MemberDonations | null;
  directorships?: MemberDirectorships | null;
  registers?: Interests[];
  groups?: MemberGroup[];
  devolved?: {
    memberships: { legislature: string; body: string; member: string; seat: string | null; party: string | null; start: string; end: string | null }[];
    counts: Record<string, number>;
    recent: { legislature: string; key: string; date: string; ord: number; major: string | null; excerpt: string }[];
  } | null;
  committee_evidence?: { id: number; date: string | null; role: string | null; committees: string | null; inquiry: string | null; url: string }[];
  topics?: { topics: { subject: string; days: number }[]; since: string | null; until: string | null };
}

const BAND: Record<string, string> = { marginal: "marginal seat", middle: "majority 10% to 20%", safe: "safe seat" };
const KIND: Record<string, string> = {
  gov: "Government",
  shadow: "Opposition",
  chair: "Committee chair",
  whip: "Whip",
  parliamentary: "Parliamentary",
};
const span = (start: string, end: string | null) => `${fmtDate(start)} – ${end ? fmtDate(end) : "now"}`;

function Person({ number }: { number: string }) {
  const [shown, setShown] = useState("");
  const [core, setCore] = useState<PersonData | null>(null);
  // the slower parts arrive separately, so who they are shows at once
  const [activity, setActivity] = useState<Partial<PersonData> | "error" | null>(null);
  const [extras, setExtras] = useState<Partial<PersonData> | "error" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  useEffect(() => {
    const once = (name: string) =>
      fetch(`/api/people/${number}?part=${name}`).then(async (r) => {
        if (!r.ok) {
          const err = new Error((await r.json().catch(() => ({}))).detail ?? "This member could not be loaded.");
          (err as Error & { status: number }).status = r.status;
          throw err;
        }
        return r.json() as Promise<PersonData>;
      });
    // a busy moment on the server (or a dropped connection) is tried again once before the page says so
    const part = (name: string) =>
      once(name).catch((e: Error & { status?: number }) =>
        e.status === 404 ? Promise.reject(e) : new Promise((r) => setTimeout(r, 1500)).then(() => once(name)),
      );
    part("core")
      .then((d) => {
        setCore(d);
        document.title = `${d.name} — Where They Stand`;
        const want = `/people/${d.number}/${d.slug}`;
        if (window.location.pathname !== want) window.history.replaceState(null, "", want);
      })
      .catch((e: Error) => setError(e.message));
    part("activity").then(setActivity, () => setActivity("error"));
    part("interests").then(setExtras, () => setExtras("error"));
  }, [number]);

  if (error) return <p role="alert">{error}</p>;
  if (!core) return <p className="muted">Loading…</p>;
  const p: PersonData = {
    ...core,
    ...(activity && activity !== "error" ? activity : {}),
    ...(extras && extras !== "error" ? extras : {}),
  };
  const pending = (part: Partial<PersonData> | "error" | null, what: string) =>
    part === null ? (
      <p className="muted small">Loading {what}…</p>
    ) : part === "error" ? (
      <p className="muted small">The {what} could not be loaded. Please try again later.</p>
    ) : null;
  const ask = (q: string) => `/?q=${encodeURIComponent(q)}`;
  return (
    <article>
      <p className="eyebrow">{p.current?.house === "lords" ? "House of Lords" : p.current ? "House of Commons" : "Former member"}</p>
      <h1>{p.name}</h1>
      <p className="lede">
        {p.status}
        {p.age != null && `. Age ${p.age_approximate ? "about " : ""}${p.age}`}
        {p.first_elected && p.memberships.some((m) => m.house === "commons") && `. First elected ${fmtDate(p.first_elected)}`}.
      </p>
      {p.other_names.length > 0 && <p className="muted small">Also recorded as {p.other_names.join("; ")}.</p>}
      <Glance
        p={p}
        activity={activity}
        onTopic={(t) => {
          setTopic(t);
          setShown(t);
          setTimeout(() => document.getElementById("h-timeline")?.scrollIntoView({ behavior: "smooth" }), 50);
        }}
      />

      <form
        className="ask-about"
        onSubmit={(e) => {
          e.preventDefault();
          if (!topic.trim()) return document.getElementById("topic")?.focus();
          window.location.href = ask(`What has ${p.name} said about ${topic.trim()}?`);
        }}
      >
        <label htmlFor="topic">What has {p.name} said about…</label>
        <div className="ask-row">
          <input id="topic" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="a topic, e.g. housing" />
          <button type="submit" className="primary">
            Ask
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => (topic.trim() ? setShown(topic.trim()) : document.getElementById("topic")?.focus())}
          >
            Timeline
          </button>
        </div>
        <p className="muted small">
          Ask writes a sourced briefing. Timeline lists what they said and how they voted on it, in date order.
        </p>
      </form>
      {shown && (
        <>
          <h2 id="h-timeline">Timeline: {shown}</h2>
          <Timeline query={`person=${number}&q=${encodeURIComponent(shown)}`} showSpeaker={false} />
        </>
      )}

      <section aria-labelledby="h-recent">
        <h2 id="h-recent">Recent contributions</h2>
        {pending(activity, "contributions and voting record")}
        {p.contributions && (
          <Contributions number={p.number} first={p.contributions} more={!!p.contributions_more} />
        )}
      </section>

      {p.voting && p.voting.votes > 0 && (
        <section aria-labelledby="h-votes">
          <h2 id="h-votes">Voting record</h2>
          <p>
            {p.voting.votes.toLocaleString("en-GB")} votes recorded between {fmtDate(p.voting.since)} and{" "}
            {fmtDate(p.voting.until)}, of which {p.voting.rebellions.toLocaleString("en-GB")} were against the majority of
            their own party
            {p.voting.free_vote_differences
              ? ` (not counting ${p.voting.free_vote_differences} vote${p.voting.free_vote_differences === 1 ? "" : "s"} where their party allowed a free vote, as reported by TheyWorkForYou Votes)`
              : ""}
            .
          </p>
          {p.voting.recent_rebellions.length > 0 && (
            <>
              <h3>Most recent votes against their party's majority</h3>
              <ul>
                {p.voting.recent_rebellions.map((r) => (
                  <li key={r.date + r.title}>
                    {fmtDate(r.date)}: <a href={r.twfy_url}>{r.title || "Division"}</a>
                    {hasVoteKind(r.vote_type) && (
                      <>
                        {" "}
                        (<VoteKind vt={r.vote_type} />)
                      </>
                    )}
                    . Voted {r.vote}; most of their party voted {r.party_majority}.
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="muted small">{p.voting.method}</p>
        </section>
      )}

      {p.offices.length > 0 && (
        <section aria-labelledby="h-roles">
          <h2 id="h-roles">Roles</h2>
          <ul className="roles">
            {p.offices.slice(0, 30).map((o) => (
              <li key={o.title + o.start}>
                <strong>{o.title}</strong> <span className="muted small">{KIND[o.kind] ?? o.kind}, {span(o.start, o.end)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {p.seats.length > 0 && (
        <section aria-labelledby="h-seats">
          <h2 id="h-seats">Seats and majorities</h2>
          {p.seats.map((s) => (
            <div key={s.constituency + s.start} className="seat">
              <h3>
                {s.constituency} <span className="muted small">({span(s.start, s.end)})</span>
              </h3>
              {s.results.length > 0 ? (
                <ul>
                  {s.results.map((r) => (
                    <li key={r.date}>
                      {r.election[0].toUpperCase() + r.election.slice(1)}:{" "}
                      {r.majority != null
                        ? `majority ${r.majority.toLocaleString("en-GB")}`
                        : "won (majority not known: the source counts smaller parties and independents together)"}
                      {r.pct != null && `, ${r.pct}% of votes cast`}
                      {r.band && <span className="muted"> · {BAND[r.band]}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">The election result for this seat has not been loaded yet.</p>
              )}
            </div>
          ))}
        </section>
      )}

      <section aria-labelledby="h-history">
        <h2 id="h-history">Membership</h2>
        <ul>
          {[...p.memberships].reverse().map((m) => (
            <li key={m.house + m.start}>
              {m.house === "commons" ? `MP for ${m.constituency ?? "—"}` : "House of Lords"}
              {m.party ? `, ${m.party}` : ""} <span className="muted small">({span(m.start, m.end)})</span>
            </li>
          ))}
        </ul>
      </section>
      {pending(extras, "all-party groups, interests and donations")}
      {p.interests && (
        <InterestsSection interests={p.interests} name={p.name} isMP={p.current?.house === "commons"} />
      )}
      {p.registers?.map((r) => (
        <InterestsSection key={r.register} interests={r} name={p.name} isMP={false} />
      ))}
      {p.donations && <DonationsSection donations={p.donations} name={p.name} />}
      {p.directorships && <DirectorshipsSection d={p.directorships} name={p.name} />}

      {p.groups && <GroupsSection groups={p.groups} name={p.name} />}

      {p.edms && p.edms.tabled + p.edms.signed > 0 && (
        <section aria-labelledby="h-edms">
          <h2 id="h-edms">Early day motions</h2>
          <p>
            Tabled {p.edms.tabled.toLocaleString("en-GB")} and signed {p.edms.signed.toLocaleString("en-GB")} early
            day motion{p.edms.signed === 1 ? "" : "s"} since 1997.{" "}
            <a href={`/edms?person=${p.number}`}>See them all</a>
          </p>
          <h3>Most recent</h3>
          <EdmList items={p.edms.recent} />
        </section>
      )}

      {p.devolved && (
        <section aria-labelledby="h-devolved">
          <h2 id="h-devolved">In the devolved parliaments</h2>
          <ul>
            {p.devolved.memberships.map((m, k) => (
              <li key={k}>
                {m.member} for {m.seat ?? "an unknown seat"} ({m.body}){m.party ? `, ${m.party}` : ""}{" "}
                <span className="muted small">
                  ({fmtDate(m.start)} – {m.end ? fmtDate(m.end) : "now"})
                </span>
              </li>
            ))}
          </ul>
          {p.devolved.recent.length > 0 && (
            <>
              <h3>Recent contributions</h3>
              <ol className="contributions">
                {p.devolved.recent.map((c) => (
                  <li key={`${c.key}-${c.ord}`}>
                    <p className="muted small">
                      {fmtDate(c.date)}
                      {c.major ? ` · ${c.major}` : ""}
                    </p>
                    <p>{c.excerpt.length >= 320 ? `${c.excerpt.trimEnd()}…` : c.excerpt}</p>
                    <p className="small">
                      <a href={`/devolved/${c.legislature}/${c.key}#s${c.ord}`}>Read it in the sitting</a>
                    </p>
                  </li>
                ))}
              </ol>
            </>
          )}
          <p className="muted small">
            Collected from 1998 (Northern Ireland), 1999 (Scotland) and 2016 (Wales); see <a href="/devolved">devolved parliaments</a>.
          </p>
        </section>
      )}

      {p.committee_evidence && p.committee_evidence.length > 0 && (
        <section aria-labelledby="h-evidence">
          <h2 id="h-evidence">Evidence to committees</h2>
          <p className="muted small">Sessions where {p.name} answered a committee's questions as a witness, most recent first.</p>
          <ul className="edm-list">
            {p.committee_evidence.map((s) => (
              <li key={s.id}>
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.date ? fmtDate(s.date) : "Session"}
                </a>
                : {s.committees}
                {s.inquiry ? ` · ${s.inquiry}` : ""}
                {s.role && <span className="muted small"> ({s.role})</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="h-elsewhere">
        <h2 id="h-elsewhere">Elsewhere</h2>
        <Elsewhere label={`More on ${p.name}`} links={elsewhereFor(p)} />
      </section>
      <p className="small feed-link">
        <a href={`/feeds/people/${p.number}.xml`}>Follow what {p.name} says in a feed reader (RSS)</a>
      </p>
      <p className="muted small">
        From Hansard, mySociety's member records and the UK Parliament's Members data. See the{" "}
        <a href="/methodology">methodology</a>.
      </p>
    </article>
  );
}

/** At the top of a member's page: the essentials, each linking to its section below. Counted from the record and
 * the registers; no AI. */
function Glance({
  p,
  activity,
  onTopic,
}: {
  p: PersonData;
  activity: Partial<PersonData> | "error" | null;
  onTopic: (subject: string) => void;
}) {
  const seat = p.current?.house === "commons" ? p.seats.find((s) => s.end === null) : undefined;
  const result = seat?.results.reduce<Result | undefined>((a, r) => (!a || r.date > a.date ? r : a), undefined);
  const roles = p.offices.filter((o) => o.end === null);
  const interests = [p.interests, ...(p.registers ?? [])]
    .filter((i): i is Interests => !!i)
    .reduce((n, i) => n + i.categories.reduce((m, c) => m + c.items.length, 0), 0);
  const donations = (p.donations?.totals.personal_count ?? 0) + (p.donations?.totals.local_count ?? 0);
  const topics = p.topics?.topics ?? [];
  return (
    <dl className="glance">
      {seat && (
        <div>
          <dt>Seat</dt>
          <dd>
            {seat.constituency}
            {result?.majority != null && (
              <>
                : majority {result.majority.toLocaleString("en-GB")}
                {result.pct != null && ` (${result.pct}%)`} at the {result.election}
                {result.band && <span className="muted"> · {BAND[result.band]}</span>}
              </>
            )}{" "}
            <a href="#h-seats" className="small">
              Seats
            </a>
          </dd>
        </div>
      )}
      {roles.length > 0 && (
        <div>
          <dt>Now</dt>
          <dd>
            {roles
              .slice(0, 3)
              .map((o) => o.title)
              .join("; ")}
            {roles.length > 3 && ` and ${roles.length - 3} more`}{" "}
            <a href="#h-roles" className="small">
              Roles
            </a>
          </dd>
        </div>
      )}
      <div>
        <dt>Speaks most about</dt>
        <dd>
          {activity === null ? (
            <span className="muted small">Counting…</span>
          ) : !p.topics ? (
            <span className="muted small">Not available yet.</span>
          ) : topics.length > 0 ? (
            <>
              <span className="glance-topics">
                {topics.map((t) => (
                  <button key={t.subject} type="button" className="chip-button" onClick={() => onTopic(t.subject)}>
                    {t.subject} <span className="count">{t.days}</span>
                  </button>
                ))}
              </span>
              <span className="muted small">
                {" "}
                Days they spoke or asked about each
                {p.topics?.since && p.topics.until
                  ? `, ${fmtDate(p.topics.since)} to ${fmtDate(p.topics.until)}`
                  : ""}
                ; choose one for a timeline.
              </span>
            </>
          ) : (
            <span className="muted small">Not enough in the record to say.</span>
          )}
        </dd>
      </div>
      {p.voting && p.voting.votes > 0 && (
        <div>
          <dt>Votes</dt>
          <dd>
            {p.voting.votes.toLocaleString("en-GB")} recorded, {p.voting.rebellions.toLocaleString("en-GB")} against
            most of their party{p.voting.until ? ` (votes loaded to ${fmtDate(p.voting.until)})` : ""}{" "}
            <a href="#h-votes" className="small">
              Voting record
            </a>
          </dd>
        </div>
      )}
      {(interests > 0 || donations > 0) && (
        <div>
          <dt>Interests</dt>
          <dd>
            {[
              interests > 0 && `${interests} registered interest${interests === 1 ? "" : "s"}`,
              donations > 0 && `${donations} political donation${donations === 1 ? "" : "s"}`,
            ]
              .filter(Boolean)
              .join(", ")}{" "}
            <a href={p.interests ? "#h-interests" : "#h-donations"} className="small">
              Details
            </a>
          </dd>
        </div>
      )}
      <div>
        <dt>Follow</dt>
        <dd>
          <a href={`/feeds/people/${p.number}.xml`}>New contributions by {p.name}, as a feed (RSS)</a>
        </dd>
      </div>
    </dl>
  );
}

/** Five at first; "Show more" opens the rest already loaded, then fetches ten at a time. Slight interventions
 * ("Will the hon. Member give way?") are left out by the server. */
function Contributions({ number, first, more }: { number: number; first: Contribution[]; more: boolean }) {
  const [items, setItems] = useState(first);
  const [shown, setShown] = useState(5);
  const [hasMore, setHasMore] = useState(more);
  const [busy, setBusy] = useState(false);
  if (items.length === 0) return <p className="muted">No contributions in the record since 1997.</p>;
  async function showMore() {
    if (shown < items.length) {
      setShown(Math.min(items.length, shown + 10));
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(`/api/people/${number}/contributions?offset=${items.length}&limit=10`);
      const body = (await r.json()) as { contributions: Contribution[]; more: boolean };
      setItems([...items, ...body.contributions]);
      setShown(items.length + body.contributions.length);
      setHasMore(body.more);
    } catch {
      setHasMore(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <ol className="contributions">
        {items.slice(0, shown).map((c) => (
          <li key={c.record_id}>
            <p className="muted small">
              {fmtDate(c.date)} · {c.chamber}
              {c.heading ? ` · ${c.heading}` : ""}
            </p>
            <p>{c.excerpt.length >= 320 ? `${c.excerpt.trimEnd()}…` : c.excerpt}</p>
            <p className="small">
              <a href={c.debate_path ?? c.twfy_url}>Read in full</a>
              {c.official_url && (
                <>
                  {" · "}
                  <a href={c.official_url}>Official record</a>
                </>
              )}
            </p>
          </li>
        ))}
      </ol>
      {(shown < items.length || hasMore) && (
        <p>
          <button type="button" className="secondary" onClick={() => void showMore()} disabled={busy}>
            {busy ? "Loading…" : "Show more contributions"}
          </button>
        </p>
      )}
    </>
  );
}

/** Other free sites with what this page does not have: contact details, every vote, alerts, writing to them. */
function elsewhereFor(p: PersonData): Link[] {
  const house = p.current?.house === "lords" ? "lords" : p.current ? "commons" : (p.memberships.at(-1)?.house as "commons" | "lords" | undefined) ?? null;
  const links: Link[] = [];
  if (p.parliament_id) links.push({ href: ELSEWHERE.membersProfile(p.parliament_id), text: "Parliament's profile", note: "contact details" });
  if (p.number) {
    links.push({ href: ELSEWHERE.twfyPerson(p.number, house), text: "TheyWorkForYou", note: "earlier speeches and voting summaries" });
    links.push({ href: ELSEWHERE.votesPerson(p.number), text: "TheyWorkForYou Votes", note: "every recorded vote" });
    links.push({ href: ELSEWHERE.twfyPersonAlert(p.number), text: "email alerts", note: `when ${p.name} speaks` });
  }
  if (p.current) links.push({ href: ELSEWHERE.writeToThem(), text: "WriteToThem", note: `write to ${p.name}` });
  return links;
}

const number = window.location.pathname.match(/^\/people\/(\d+)/)?.[1];
createRoot(document.getElementById("person-root")!).render(
  <StrictMode>{number ? <Person number={number} /> : <p role="alert">No member was given.</p>}</StrictMode>,
);
