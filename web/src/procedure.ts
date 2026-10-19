import type { SourceCard } from "./types";

/** Plain-English notes on each kind of parliamentary record, shown in the source panel. */
const NOTES: Record<string, { title: string; text: string }> = {
  commons: {
    title: "Debates in the House of Commons",
    text:
      "Hansard is a substantially verbatim record: repetition and obvious slips are removed, but nothing of " +
      "substance is changed. Column numbers refer to the printed Official Report.",
  },
  lords: {
    title: "Debates in the House of Lords",
    text:
      "Members of the House of Lords do not represent constituencies; most are appointed for life. Hansard is a " +
      "substantially verbatim record, and column numbers refer to the printed Official Report.",
  },
  westminsterHall: {
    title: "Westminster Hall debates",
    text:
      "Westminster Hall is the Commons' second debating chamber. Debates there are usually on subjects raised by " +
      "backbench MPs, a minister replies, and no decisions on legislation are taken.",
  },
  writtenAnswer: {
    title: "Written questions and answers",
    text:
      "MPs and peers can put questions to government departments for a written answer. The answer is the " +
      "government's formal response; it is not part of a debate.",
  },
  writtenStatement: {
    title: "Written ministerial statements",
    text:
      "Ministers use written statements to tell Parliament about announcements, decisions and updates without a " +
      "debate.",
  },
  publicBill: {
    title: "Public Bill Committee debates",
    text:
      "After a bill's second reading in the Commons, a committee of MPs goes through it line by line and may first " +
      "hear from expert witnesses. Its debates are reported in their own Hansard.",
  },
  devolved: {
    title: "The devolved parliaments",
    text:
      "The Scottish Parliament, the Senedd and the Northern Ireland Assembly make laws for their own nations on " +
      "devolved subjects such as health, education and housing. Their members are not members of the UK Parliament, " +
      "and what they say is recorded in each parliament's own official report.",
  },
  evidence: {
    title: "Oral evidence to committees",
    text:
      "Select committees question witnesses (experts, officials, ministers and others) in public evidence " +
      "sessions. What a witness says is their own evidence to the committee: it is not the committee's view or " +
      "Parliament's, and the committee's conclusions are in its reports.",
  },
  edm: {
    title: "Early day motions",
    text:
      "MPs put down early day motions to record a view and gather support; few are ever debated. Signing one shows " +
      "support for its wording. Ministers, whips and the Speaker do not sign them, so not signing says nothing " +
      "about a member's view.",
  },
  division: {
    title: "Divisions (votes)",
    text:
      "A division is a formal vote. Members walk through the Aye or No lobbies (Content or Not Content in the " +
      "Lords), and each member's vote is recorded. A member is shown here as rebelling when they voted against " +
      "the majority of their own party in that division.",
  },
};

export function aboutRecord(s: Pick<SourceCard, "kind" | "chamber" | "record_type">): { title: string; text: string } {
  if (s.kind === "division") return NOTES.division;
  if (s.kind === "edm") return NOTES.edm;
  if (s.kind === "devolved") return NOTES.devolved;
  if (s.kind === "evidence") return NOTES.evidence;
  if (s.record_type === "question" || s.record_type === "answer" || /written answers/i.test(s.chamber)) {
    return NOTES.writtenAnswer;
  }
  if (/written statements/i.test(s.chamber)) return NOTES.writtenStatement;
  if (/public bill committee/i.test(s.chamber)) return NOTES.publicBill;
  if (/westminster hall/i.test(s.chamber)) return NOTES.westminsterHall;
  if (/lords/i.test(s.chamber)) return NOTES.lords;
  return NOTES.commons;
}
