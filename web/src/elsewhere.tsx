/**
 * Links to other free sites that do something this one does not: every mention of a phrase, alerts, contacting a
 * member, a bill's documents, the law as it stands. Only stable address patterns are used; where a site has no
 * search address we can rely on, its front page is linked instead.
 */
import type { ReactNode } from "react";

const q = encodeURIComponent;

export const ELSEWHERE = {
  /** Hansard's own search: every contribution containing the words, from 1800s to today. */
  hansard: (words: string, from?: string | null, to?: string | null) =>
    `https://hansard.parliament.uk/search/Contributions?searchTerm=${q(words)}` +
    (from ? `&startDate=${from}` : "") +
    (to ? `&endDate=${to}` : ""),
  twfySearch: (words: string) => `https://www.theyworkforyou.com/search/?q=${q(words)}`,
  twfyAlert: (words: string) => `https://www.theyworkforyou.com/alert/?alertsearch=${q(words)}`,
  twfyPersonAlert: (personNumber: number) => `https://www.theyworkforyou.com/alert/?pid=${personNumber}`,
  twfyPerson: (personNumber: number, house: "commons" | "lords" | null) =>
    `https://www.theyworkforyou.com/${house === "lords" ? "peer" : "mp"}/?p=${personNumber}`,
  votesPerson: (personNumber: number) => `https://votes.theyworkforyou.com/person/${personNumber}`,
  membersProfile: (parliamentId: string | number) => `https://members.parliament.uk/member/${parliamentId}/contact`,
  writeToThem: (postcode?: string | null) =>
    postcode ? `https://www.writetothem.com/?pc=${q(postcode)}` : "https://www.writetothem.com/",
  commonsLibrary: (words: string) => `https://commonslibrary.parliament.uk/?s=${q(words)}`,
  bills: (name: string) => `https://bills.parliament.uk/?SearchTerm=${q(name)}`,
  legislation: (title: string) => `https://www.legislation.gov.uk/all?title=${q(title)}`,
  writtenQuestions: (words: string) => `https://questions-statements.parliament.uk/written-questions?SearchTerm=${q(words)}`,
  lordsInterests: "https://members.parliament.uk/members/lords/interests/register-of-lords-interests",
  whatsOn: "https://whatson.parliament.uk/",
  parliamentTv: "https://parliamentlive.tv/",
  historicHansard: "https://api.parliament.uk/historic-hansard/",
  electoralCommission: "https://search.electoralcommission.org.uk/",
  appgRegister:
    "https://www.parliament.uk/mps-lords-and-offices/standards-and-financial-interests/parliamentary-commissioner-for-standards/registers-of-interests/register-of-all-party-party-parliamentary-groups/",
  erskineMay: "https://erskinemay.parliament.uk/",
  parliamentGlossary: "https://www.parliament.uk/site-information/glossary/",
};

export interface Link {
  href: string;
  text: string;
  note?: string;
}

/** "Elsewhere: Hansard search (every mention) · …": one short line of outside links. */
export function Elsewhere({ label = "Elsewhere", links, children }: { label?: string; links: Link[]; children?: ReactNode }) {
  if (links.length === 0) return null;
  return (
    <p className="small elsewhere">
      <span className="muted">{label}: </span>
      {links.map((l, i) => (
        <span key={l.href}>
          {i > 0 && " · "}
          <a href={l.href} rel="noreferrer">
            {l.text}
          </a>
          {l.note && <span className="muted"> ({l.note})</span>}
        </span>
      ))}
      {children}
    </p>
  );
}

/** Under a briefing or a set of sources: the same words elsewhere. */
export function ElsewhereForSearch({
  words,
  from,
  to,
  noRecords = false,
}: {
  words: string;
  from?: string | null;
  to?: string | null;
  noRecords?: boolean;
}) {
  const w = words.trim();
  if (!w) return null;
  const links: Link[] = [
    { href: ELSEWHERE.hansard(w, from, to), text: "Hansard search", note: "every mention of these words" },
    { href: ELSEWHERE.twfySearch(w), text: "TheyWorkForYou", note: "including before 1997" },
    { href: ELSEWHERE.twfyAlert(w), text: "email alerts", note: "when these words are next said" },
    { href: ELSEWHERE.commonsLibrary(w), text: "House of Commons Library", note: "impartial background" },
  ];
  return <Elsewhere label={noRecords ? "Try instead" : "Elsewhere"} links={links} />;
}
