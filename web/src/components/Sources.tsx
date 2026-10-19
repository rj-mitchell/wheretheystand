import { useEffect, useRef } from "react";
import { aboutRecord } from "../procedure";
import { VoteKind } from "./VoteKind";
import { ageLabel, attribution, houseOf, majorityText, reference, roleTitles, seatTypeLabel } from "../people";
import { fmtDate, highlightParts } from "../render";
import type { Highlight, InterestFlag, SourceCard, Speaker } from "../types";

export { roleTitles };

/** One-line speaker description for labels, e.g. "Hilary Benn (Leeds Central) (Lab)". */
export function speakerLine(sp: Speaker | null | undefined, raw?: string | null): string {
  const a = attribution(sp, raw);
  return a.tail ? `${a.name} ${a.tail}` : a.name;
}

function Attribution({ sp, raw }: { sp: Speaker | null | undefined; raw?: string | null }) {
  const a = attribution(sp, raw);
  return (
    <span className="att">
      <b>{a.name}</b>
      {a.tail && ` ${a.tail}`}
    </span>
  );
}

export function SourceList({
  sources,
  onOpen,
  open,
  writing = false,
}: {
  sources: SourceCard[];
  onOpen: (h: Highlight) => void;
  open: boolean;
  writing?: boolean;
}) {
  if (!sources.length) return null;
  const groups: [string, SourceCard[]][] = [];
  for (const s of sources) {
    const g = s.kind === "division" ? "Votes (divisions)" : s.group ?? "Other";
    const hit = groups.find(([name]) => name === g);
    if (hit) hit[1].push(s);
    else groups.push([g, [s]]);
  }
  return (
    <details className="sources" open={open}>
      <summary>
        {writing
          ? `The ${sources.length} source${sources.length === 1 ? "" : "s"} the briefing is being written from, by speaker group`
          : `${open ? "All" : "Show all"} ${sources.length} source${sources.length === 1 ? "" : "s"}, by speaker group`}
      </summary>
      <div className="sources-body">
        {groups.map(([g, items]) => (
          <section key={g} className="source-group">
            <h3>{g}</h3>
            <ol>
              {items.map((s) => (
                <li key={s.n}>
                  <SourceItem source={s} onOpen={onOpen} />
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>
    </details>
  );
}

function SourceItem({ source: s, onOpen }: { source: SourceCard; onOpen: (h: Highlight) => void }) {
  const roles = roleTitles(s.speaker);
  const seat = seatTypeLabel(s.speaker);
  const ref = s.kind === "edm" ? null : reference(s);
  return (
    <button
      type="button"
      className={s.interest ? "source-card has-interest" : "source-card"}
      data-house={houseOf(s)}
      onClick={() => onOpen({ source: s.n, quotes: [] })}
    >
      <span className="badge">{s.n}</span>
      <span className="source-meta">
        {s.kind === "division" ? (
          <span className="att">
            <b>Division {s.number}</b> {s.major_heading}
          </span>
        ) : s.kind === "devolved" ? (
          <span className="att">
            <b>{s.member?.name}</b>
            {s.member && (s.member.party || s.member.seat) ? ` (${[s.member.party, s.member.seat].filter(Boolean).join(", ")})` : ""}
          </span>
        ) : s.kind === "evidence" ? (
          <span className="att">
            <b>{s.witness?.name}</b>
            {s.witness && (s.witness.role || s.witness.organisation)
              ? ` (${[s.witness.role, s.witness.organisation].filter(Boolean).join(", ")})`
              : ""}
          </span>
        ) : s.kind === "edm" ? (
          <span className="att">
            <b>{s.uin ? `EDM ${s.uin}` : "Early day motion"}</b> {s.edm_title}
          </span>
        ) : (
          <Attribution sp={s.speaker} raw={s.speaker_name_raw} />
        )}
        {roles.length > 0 && <span className="role">{roles.join("; ")}</span>}
        {seat && <span className="seat">{seat}</span>}
        {s.declared && <span className="seat">Declared an interest when speaking</span>}
        {s.interest && <span className="interest-flag">Registered interest that may relate to this</span>}
        <span className="where">
          <time dateTime={s.date}>{fmtDate(s.date)}</time> · {s.chamber}
          {s.heading ? ` · ${s.heading}` : ""}
        </span>
      </span>
      {ref && <span className="colref">{ref}</span>}
      <span className="print-only">{s.official_url ?? s.url}</span>
    </button>
  );
}

export function SourceDrawer({
  source,
  quotes,
  onClose,
}: {
  source: SourceCard | null;
  quotes: string[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const isOpen = source !== null;
  // remember what opened the drawer, and return focus there when it closes (keyboard and screen reader users)
  useEffect(() => {
    if (!isOpen) return;
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && !ref.current?.contains(active) ? active : null;
    return () => {
      if (opener.current?.isConnected) opener.current.focus();
      opener.current = null;
    };
  }, [isOpen]);
  useEffect(() => {
    if (!source) return;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [source, onClose]);
  useEffect(() => {
    // a speaker's registered interests come first, so the panel opens on them; otherwise on the quoted words
    if (source?.interest) return;
    const first = ref.current?.querySelector("mark");
    if (first && typeof first.scrollIntoView === "function") first.scrollIntoView({ block: "center" });
  }, [source, quotes]);

  if (!source) return null;
  const sp = source.speaker;
  const seat = seatTypeLabel(sp);
  const colRef = reference(source);
  return (
    <aside
      className="drawer"
      data-house={houseOf(source)}
      role="dialog"
      aria-modal="false"
      aria-label={`Source ${source.n}`}
      tabIndex={-1}
      ref={ref}
    >
      <header className="drawer-head">
        <span className="badge">{source.n}</span>
        <h2>
          {source.kind === "division"
            ? `Division ${source.number}`
            : source.kind === "edm"
              ? `Early day motion${source.uin ? ` ${source.uin}` : ""}`
              : source.kind === "devolved"
                ? (source.member?.name ?? "A member")
              : source.kind === "evidence"
                ? (source.witness?.name ?? "A witness")
              : speakerLine(sp, source.speaker_name_raw)}
        </h2>
        <button type="button" className="close" onClick={onClose} aria-label="Close source">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 3l10 10M13 3L3 13" />
          </svg>
        </button>
      </header>

      <div className="drawer-body">
        {source.interest && <InterestBox flag={source.interest} date={source.date} />}
        {source.kind === "edm" ? (
          <EdmDetail source={source} quotes={quotes} />
        ) : source.kind === "devolved" ? (
          <DevolvedDetail source={source} quotes={quotes} />
        ) : source.kind === "evidence" ? (
          <EvidenceDetail source={source} quotes={quotes} />
        ) : (
          <>
        <dl className="facts">
          {source.kind === "record" && (
            <>
              <dt>Speaker</dt>
              <dd>
                {sp?.name ?? source.speaker_name_raw ?? "Unattributed"}
                {sp?.other_names && sp.other_names.length > 0 && (
                  <span className="muted small"> (also recorded as {sp.other_names.join("; ")})</span>
                )}
              </dd>
              <dt>Party</dt>
              <dd>{sp?.party ?? "—"}</dd>
              <dt>{sp?.house === "lords" ? "House" : "Constituency"}</dt>
              <dd>
                {sp?.constituency
                  ? `${sp.constituency}${sp.region ? ` (${sp.region})` : ""}`
                  : sp?.house === "lords"
                    ? "House of Lords"
                    : "—"}
              </dd>
              {seat && (
                <>
                  <dt>Seat type</dt>
                  <dd>{seat[0].toUpperCase() + seat.slice(1)}</dd>
                </>
              )}
              <dt>Role on {fmtDate(source.date)}</dt>
              <dd>{roleTitles(sp).length ? roleTitles(sp).join("; ") : "Backbencher (no ministerial or committee-chair post)"}</dd>
              {source.declared && (
                <>
                  <dt>Declared an interest</dt>
                  <dd>
                    “{source.declared}”{" "}
                    <span className="muted small">
                      (in their own words)
                      {sp?.house === "commons" && sp.person_id && (
                        <>
                          {" · "}
                          <a href={`/people/${sp.person_id.split("/").pop()}#h-interests`}>Their registered interests</a>
                        </>
                      )}
                    </span>
                  </dd>
                </>
              )}
              {sp?.house === "commons" && sp.majority_then && (
                <>
                  <dt>Majority then</dt>
                  <dd>{majorityText(sp.majority_then)}</dd>
                </>
              )}
              {sp?.majority_now && sp.majority_now.date !== sp.majority_then?.date && (
                <>
                  <dt>Majority now</dt>
                  <dd>
                    {majorityText(sp.majority_now)}
                    {sp.majority_now.constituency !== sp.constituency && ` (${sp.majority_now.constituency})`}
                  </dd>
                </>
              )}
              {sp?.house === "commons" && sp.first_elected && (
                <>
                  <dt>First elected</dt>
                  <dd>
                    {fmtDate(sp.first_elected)}
                    {sp.first_term != null && (
                      <span className="muted small">{sp.first_term ? " (in their first term on this date)" : " (re-elected since)"}</span>
                    )}
                  </dd>
                </>
              )}
              {sp?.person_id && (
                <>
                  <dt>Age on that date</dt>
                  <dd>
                    {ageLabel(sp) ?? "Not known"}
                    {sp.age_approximate && <span className="muted small"> (only the year of birth is known)</span>}
                  </dd>
                  <dt>Gender</dt>
                  <dd>
                    {sp.gender === "F" ? "Female" : sp.gender === "M" ? "Male" : "Not known"}
                    {sp.gender && <span className="muted small"> (as recorded by Parliament)</span>}
                  </dd>
                </>
              )}
            </>
          )}
          <dt>Record</dt>
          <dd>
            {source.chamber}, {fmtDate(source.date)}
            {source.time ? `, ${source.time}` : ""}
            {source.heading ? ` — ${source.heading}` : ""}
          </dd>
          <dt>{source.colnum ? "Hansard column" : source.uin ? "Reference" : "Column"}</dt>
          <dd className="num">{source.colnum ?? source.uin ?? "Not given in the source data"}</dd>
          <dt>Links</dt>
          <dd className="links">
            {source.debate_path && <a href={source.debate_path}>The whole debate on this site</a>}
            {source.official_url && (
              <a href={source.official_url} target="_blank" rel="noreferrer">
                {source.url_kind === "sitting" ? "Official Hansard (sitting day)" : "Official Parliament record"}
              </a>
            )}
            <a href={source.twfy_url} target="_blank" rel="noreferrer">
              TheyWorkForYou (exact record)
            </a>
          </dd>
        </dl>

        <AboutRecord source={source} />

        {source.kind === "division" ? (
          <DivisionDetail source={source} />
        ) : (
          <section className="verbatim" aria-label="Verbatim text">
            <p className="page-head">
              <span>{source.chamber}</span>
              {colRef && <span>{colRef}</span>}
            </p>
            {source.heading && <h3 className="page-heading">{source.heading}</h3>}
            {source.context && (
              <blockquote className="question">
                <strong>Question asked:</strong> {source.context}
              </blockquote>
            )}
            {source.text.split("\n\n").map((para, i) => (
              <p key={i}>
                {i === 0 && (
                  <>
                    <Attribution sp={sp} raw={source.speaker_name_raw} />
                    {": "}
                  </>
                )}
                {highlightParts(para, quotes).map((part, j) =>
                  part.hit ? <mark key={j}>{part.text}</mark> : <span key={j}>{part.text}</span>,
                )}
              </p>
            ))}
          </section>
        )}

          </>
        )}

        {source.kind === "record" && <AskAbout source={source} />}

        {source.ranking && (
          <details className="ranking">
            <summary>Why this source ranked here</summary>
            <dl>
              <dt>Relevance</dt>
              <dd>
                {source.ranking.relevance.toFixed(2)} (keyword {source.ranking.keyword?.toFixed(2) ?? "—"}, semantic{" "}
                {source.ranking.semantic?.toFixed(2) ?? "—"})
              </dd>
              <dt>Recency factor</dt>
              <dd>×{source.ranking.recency.toFixed(2)}</dd>
              <dt>Role factor</dt>
              <dd>
                ×{source.ranking.authority.toFixed(2)} — {source.ranking.authority_reason}
              </dd>
              <dt>Score</dt>
              <dd>{source.ranking.score.toFixed(3)}</dd>
            </dl>
          </details>
        )}
        <p className="muted small licence">Contains Parliamentary information licensed under the Open Parliament Licence v3.0.</p>
      </div>
      <footer className="drawer-foot">
        <button type="button" className="secondary" onClick={onClose}>
          <span aria-hidden="true">← </span>Back to the briefing
        </button>
      </footer>
    </aside>
  );
}

// headings that name a kind of business rather than a subject
const GENERIC_HEADING =
  /^(business of the house|oral answers to questions|engagements|topical questions|points? of order|prime minister|business without debate|petitions?|prayers|speaker's statement|deferred divisions?|adjournment|bill presented|bills presented)$/i;

/** Follow-up questions about this speech's speaker and subject, opened on the main page to edit and ask. */
function AskAbout({ source }: { source: SourceCard }) {
  const name = source.speaker?.name ?? source.speaker_name_raw;
  const parts = (source.heading ?? "")
    .replace(/\s*[—-]\s*\[[^\]]*\]\s*$/, "") // "— [Sir Gary Streeter in the Chair]"
    .split(" — ");
  // written questions are headed "Department — Subject: detail"; debates by their subject first
  const written = ["question", "answer", "statement"].includes(source.record_type ?? "");
  const candidates = (written ? [parts[parts.length - 1].split(":")[0]] : parts).map((p) =>
    p.replace(/\s+-\s+(Question|Motion|Statement)$/i, "").trim(),
  );
  const subject = candidates.find((c) => c && !GENERIC_HEADING.test(c));
  if (!subject) return null;
  const party = source.speaker?.party;
  const options = [
    name && `What else has ${name} said about ${subject}?`,
    `What have other MPs and peers said about ${subject}?`,
    party && party !== "Speaker" && `What have ${party} members said about ${subject}?`,
  ].filter((o): o is string => Boolean(o));
  const id = `ask-about-${source.n}`;
  return (
    <form className="ask-about drawer-ask" action="/" method="get">
      <label htmlFor={id}>Ask about this</label>
      <div className="ask-row">
        <select id={id} name="q" defaultValue={options[0]}>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <button type="submit" className="primary">
          Ask
        </button>
      </div>
      <p className="muted small">Opens the question on the main page, where you can change it before asking.</p>
    </form>
  );
}

function DivisionDetail({ source }: { source: SourceCard }) {
  return (
    <section className="division">
      <p>
        <strong>{[source.major_heading, source.minor_heading].filter(Boolean).join(" — ")}</strong>
      </p>
      {source.vote_type && (
        <p className="small">
          <VoteKind vt={source.vote_type} detail />
        </p>
      )}
      <p className="tally">
        <span>
          Ayes <b>{source.ayes}</b>
        </span>
        <span>
          Noes <b>{source.noes}</b>
        </span>
      </p>
      {source.party_votes && (
        <table>
          <caption className="muted small">Votes by party ({source.scope_note})</caption>
          <thead>
            <tr>
              <th scope="col">Party</th>
              <th scope="col">Aye</th>
              <th scope="col">No</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(source.party_votes).map(([p, v]) => (
              <tr key={p}>
                <th scope="row">{p}</th>
                <td>{v.aye}</td>
                <td>{v.no}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Voted against their own party's majority</h3>
      {source.rebels && source.rebels.length > 0 ? (
        <ul>
          {source.rebels.map((r, i) => (
            <li key={i}>
              {r.name} ({r.party}
              {r.constituency ? `, ${r.constituency}` : ""}) voted {r.vote}; party majority {r.majority}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nobody in scope.</p>
      )}
    </section>
  );
}

/** An early day motion: who tabled it, its words and who signed, by party on the day they signed. */
function EdmDetail({ source, quotes }: { source: SourceCard; quotes: string[] }) {
  const sp = source.sponsor;
  const signed = (source.party_counts ?? []).reduce((n, [, k]) => n + k, 0);
  return (
    <>
      <dl className="facts">
        <dt>Motion</dt>
        <dd>{source.edm_title}</dd>
        <dt>Tabled by</dt>
        <dd>
          {sp?.number && sp.slug ? <a href={`/people/${sp.number}/${sp.slug}`}>{sp.name}</a> : (sp?.name ?? "—")}
          {sp?.party ? ` (${sp.party})` : ""}
        </dd>
        <dt>Tabled on</dt>
        <dd>{source.date ? fmtDate(source.date) : "—"}</dd>
        <dt>Signatures</dt>
        <dd>
          {signed || source.sponsors_count || 0}
          {source.party_counts && source.party_counts.length > 0 && (
            <span className="muted small">
              {" "}
              ({source.party_counts.map(([p, k]) => `${p} ${k}`).join("; ")}, by party on the day they signed)
            </span>
          )}
        </dd>
        <dt>Links</dt>
        <dd className="links">
          <a href={source.url}>Who signed, on this site</a>
          {source.official_url && (
            <a href={source.official_url} target="_blank" rel="noreferrer">
              Parliament's early day motions site
            </a>
          )}
        </dd>
      </dl>
      <AboutRecord source={source} />
      <section className="verbatim" aria-label="Text of the motion">
        {(source.motion_text ?? "").split("\n\n").map((para, i) => (
          <p key={i}>
            {highlightParts(para, quotes).map((part, j) =>
              part.hit ? <mark key={j}>{part.text}</mark> : <span key={j}>{part.text}</span>,
            )}
          </p>
        ))}
      </section>
    </>
  );
}

/** A contribution in a devolved parliament: who, their party and seat on the day, and their words. */
function DevolvedDetail({ source, quotes }: { source: SourceCard; quotes: string[] }) {
  const m = source.member;
  const words = source.text.split("\n\n").slice(1);
  return (
    <>
      <dl className="facts">
        <dt>Member</dt>
        <dd>{m?.number && m.slug ? <a href={`/people/${m.number}/${m.slug}`}>{m.name}</a> : (m?.name ?? "—")}</dd>
        <dt>Party</dt>
        <dd>{m?.party ?? "—"}</dd>
        <dt>Seat</dt>
        <dd>{m?.seat ?? "—"}</dd>
        <dt>Where</dt>
        <dd>
          {source.chamber}, {fmtDate(source.date)}
          {source.heading ? ` — ${source.heading}` : ""}
        </dd>
        <dt>Links</dt>
        <dd className="links">
          <a href={source.url}>The whole sitting on this site</a>
          {source.official_url && (
            <a href={source.official_url} target="_blank" rel="noreferrer">
              The official report
            </a>
          )}
        </dd>
      </dl>
      <AboutRecord source={source} />
      <section className="verbatim" aria-label="Verbatim text">
        {words.map((para, i) => (
          <p key={i}>
            {highlightParts(para, quotes).map((part, j) =>
              part.hit ? <mark key={j}>{part.text}</mark> : <span key={j}>{part.text}</span>,
            )}
          </p>
        ))}
      </section>
    </>
  );
}

/** A witness's answer to a committee: who they are, which committee and inquiry, and their words. */
function EvidenceDetail({ source, quotes }: { source: SourceCard; quotes: string[] }) {
  const w = source.witness;
  const words = source.text.split("\n\n").slice(1);
  return (
    <>
      <dl className="facts">
        <dt>Witness</dt>
        <dd>{w?.name ?? "—"}</dd>
        <dt>Role</dt>
        <dd>{[w?.role, w?.organisation].filter(Boolean).join(", ") || "—"}</dd>
        <dt>Committee</dt>
        <dd>
          {source.committee}, {fmtDate(source.date)}
        </dd>
        {source.inquiry && (
          <>
            <dt>Inquiry</dt>
            <dd>
              {source.inquiry_id ? <a href={`/inquiries/${source.inquiry_id}`}>{source.inquiry}</a> : source.inquiry}
            </dd>
          </>
        )}
        <dt>Links</dt>
        <dd className="links">
          <a href={source.url}>The whole transcript on this site</a>
          {source.official_url && (
            <a href={source.official_url} target="_blank" rel="noreferrer">
              The session on Parliament's website
            </a>
          )}
        </dd>
      </dl>
      <AboutRecord source={source} />
      <section className="verbatim" aria-label="Verbatim text">
        {words.map((para, i) => (
          <p key={i}>
            {highlightParts(para, quotes).map((part, j) =>
              part.hit ? <mark key={j}>{part.text}</mark> : <span key={j}>{part.text}</span>,
            )}
          </p>
        ))}
      </section>
    </>
  );
}

function AboutRecord({ source }: { source: SourceCard }) {
  const note = aboutRecord(source);
  return (
    <details className="about-record">
      <summary>About this record: {note.title}</summary>
      <p>{note.text}</p>
    </details>
  );
}

/** At the top of the source panel: the speaker's register entries (and donations) that may relate to what they said. */
function InterestBox({ flag, date }: { flag: InterestFlag; date: string }) {
  return (
    <section className="interest-box" aria-labelledby="interest-box-title">
      <h3 id="interest-box-title">Registered interests that may relate to this</h3>
      <ul>
        {flag.items.map((i) => (
          <li key={`${i.summary}-${i.registered}`}>
            <b>{i.summary}</b>{" "}
            <span className="muted small">
              ({i.category}, {i.kind === "donation" ? "accepted" : "registered"} {fmtDate(i.registered)}
              {i.url && (
                <>
                  {" · "}
                  <a href={i.url}>the record</a>
                </>
              )}
              )
            </span>
            <br />
            {i.connection}
          </li>
        ))}
      </ul>
      <p className="muted small">
        From the {[...new Set(flag.items.map((i) => i.register ?? flag.register))].join(" and the ")}: entries made before
        this contribution on {fmtDate(date)} that an AI judged to concern the same subject. A link to check, not a
        finding of wrongdoing.
        {flag.link && (
          <>
            {" "}
            <a href={flag.link}>All their registered interests and donations</a>
          </>
        )}
      </p>
    </section>
  );
}
