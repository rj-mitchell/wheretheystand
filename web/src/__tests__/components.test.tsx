import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AnswerView } from "../components/AnswerView";
import { Controls, recencyLabel } from "../components/Controls";
import { chipsFor, PlanChips } from "../components/PlanChips";
import { SourceDrawer } from "../components/Sources";
import type { Answer, Plan, SourceCard } from "../types";

const plan: Plan = {
  question: "q",
  search_text: "agricultural planning",
  filters: {
    date_from: "2018-01-01",
    date_to: "2022-12-31",
    houses: ["commons"],
    regions: ["North East"],
    parties: [],
    person_ids: [],
    offices: ["gov"],
    record_types: [],
  },
  portfolios: ["environment"],
  wants_votes: true,
  speakers: [],
  unresolved_speakers: [],
  interpretation: "Northern MPs, 2018-2022",
  parser: "model",
};

describe("filter chips", () => {
  it("lists every applied filter", () => {
    const labels = chipsFor(plan, null).map((c) => c.label);
    expect(labels).toEqual([
      "From 1 January 2018",
      "To 31 December 2022",
      "House of Commons",
      "MPs in North East",
      "Government ministers",
      "Boost: environment",
    ]);
  });

  it("re-runs with a removed chip", () => {
    const onRerun = vi.fn();
    render(<PlanChips plan={plan} meta={null} onRerun={onRerun} busy={false} />);
    fireEvent.click(screen.getByLabelText("Remove MPs in North East"));
    fireEvent.click(screen.getByText("Re-run with these filters"));
    expect(onRerun.mock.calls[0][0].filters.regions).toEqual([]);
  });
});

describe("controls", () => {
  it("describes the recency slider in plain words", () => {
    expect(recencyLabel(0)).toMatch(/Off/);
    expect(recencyLabel(1)).toBe("Half-life 2.0 years");
  });

  it("changes the role weighting", () => {
    const onChange = vi.fn();
    render(
      <Controls
        settings={{ mode: "summary", recency: 0.5, roleWeight: 1.5, scope: { written: true, pbc: true, votes: true } }}
        onChange={onChange}
        answersEnabled
      />,
    );
    fireEvent.change(screen.getByLabelText(/Role weighting/), { target: { value: "2" } });
    expect(onChange.mock.calls[0][0].roleWeight).toBe(2);
    fireEvent.click(screen.getByLabelText(/Written answers/));
    expect(onChange.mock.calls[1][0].scope.written).toBe(false);
  });
});

const answer: Answer = {
  segments: [
    { text: "The minister blamed inflation", sources: [1], cited: [{ source: 1, cited_text: "inflation in costs" }], status: "cited" },
  ],
  markdown: "",
  no_records: false,
  citations_total: 1,
  citations_invalid: 0,
  grounded_ratio: 1,
  unsupported_segments: 0,
};

describe("answer view", () => {
  it("opens the cited source with its quotes", () => {
    const onCite = vi.fn();
    render(<AnswerView question="q" answer={answer} streamText="" sources={[]} onCite={onCite} />);
    fireEvent.click(screen.getByRole("button", { name: "Open source 1" }));
    expect(onCite).toHaveBeenCalledWith({ source: 1, quotes: ["inflation in costs"] });
    expect(screen.getByText(/1 citation, each checked against the source text/)).toBeInTheDocument();
    expect(screen.getByText(/Written by AI \(Claude\) from the cited Hansard records only/)).toBeInTheDocument();
  });

  it("notes uncited statements after the answer and marks them only on request", () => {
    const uncited: Answer = {
      ...answer,
      segments: [...answer.segments, { text: " Everyone agreed.", sources: [], cited: [], status: "unsupported" }],
      unsupported_segments: 1,
    };
    const { container } = render(<AnswerView question="q" answer={uncited} streamText="" sources={[]} onCite={vi.fn()} />);
    expect(screen.getByText(/One statement in this briefing is/)).toBeInTheDocument();
    expect(container.querySelector(".answer-body.show-uncited")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show it" }));
    expect(container.querySelector(".answer-body.show-uncited .unsupported")).not.toBeNull();
  });

  it("flags judgemental words in the briefing's own voice", () => {
    const flagged: Answer = {
      ...answer,
      segments: [{ ...answer.segments[0], text: "The minister admitted inflation", loaded: [{ start: 13, end: 21, word: "admitted" }] }],
      loaded_words: 1,
    };
    const { container } = render(<AnswerView question="q" answer={flagged} streamText="" sources={[]} onCite={vi.fn()} />);
    expect(container.querySelector(".loaded")?.textContent).toBe("admitted");
    expect(screen.getByText(/1 judgemental word in the briefing's own voice/)).toBeInTheDocument();
  });

  it("asks whether the briefing was useful and sends the answer once", () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    render(<AnswerView question="q" answer={answer} streamText="" sources={[]} briefingKey="k1" onCite={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(screen.getByText("Thank you for the feedback.")).toBeInTheDocument();
    expect(JSON.parse(spy.mock.calls[0][1]!.body as string)).toEqual({ question: "q", useful: false, briefing_key: "k1" });
    spy.mockRestore();
  });
});

const source: SourceCard = {
  n: 1,
  kind: "record",
  title: "t",
  url: "https://hansard.parliament.uk/Commons/2018-03-07",
  date: "2018-03-07",
  chamber: "House of Commons",
  text: "We face inflation in costs.\n\nSecond paragraph.",
  twfy_url: "https://www.theyworkforyou.com/debates/?id=2018-03-07a.1.1",
  official_url: "https://hansard.parliament.uk/Commons/2018-03-07",
  url_kind: "sitting",
  colnum: "827",
  heading: "HS2",
  speaker: {
    person_id: "uk.org.publicwhip/person/10669",
    date: "2018-03-07",
    name: "Hilary Benn",
    house: "commons",
    constituency: "Leeds Central",
    region: "Yorkshire and The Humber",
    party: "Labour",
    offices: [{ kind: "chair", title: "Chair, Exiting the European Union Committee" }],
    other_names: [],
  },
};

describe("source drawer", () => {
  it("shows speaker, role on the date, column, links and highlighted verbatim text", () => {
    render(<SourceDrawer source={source} quotes={["inflation in costs"]} onClose={() => {}} />);
    expect(screen.getByText("Chair, Exiting the European Union Committee")).toBeInTheDocument();
    expect(screen.getByText("827")).toBeInTheDocument();
    expect(screen.getByText("Leeds Central (Yorkshire and The Humber)")).toBeInTheDocument();
    expect(screen.getByText("inflation in costs").tagName).toBe("MARK");
    expect(screen.getByRole("link", { name: /Official Hansard/ })).toHaveAttribute("href", source.official_url);
    expect(screen.getByRole("link", { name: /TheyWorkForYou/ })).toHaveAttribute("href", source.twfy_url);
    expect(screen.getByText(/Open Parliament Licence/)).toBeInTheDocument();
  });

  it("shows a speaker's own declaration of an interest", () => {
    const declared = "I refer the House to my entry in the Register of Members' Financial Interests.";
    render(<SourceDrawer source={{ ...source, declared }} quotes={[]} onClose={() => {}} />);
    expect(screen.getByText("Declared an interest")).toBeInTheDocument();
    expect(screen.getByText(`“${declared}”`, { exact: false })).toBeInTheDocument();
  });

  it("labels a witness's evidence to a committee as theirs, not Parliament's", () => {
    const evidence: SourceCard = {
      n: 3,
      kind: "evidence",
      title: "Ann Expert",
      url: "/evidence/77#t1",
      date: "2024-03-05",
      chamber: "Committee evidence",
      text: "Oral evidence to the Transport Committee...\n\nFares have risen faster than wages.",
      twfy_url: "",
      committee: "Transport Committee",
      inquiry: "Rail fares",
      inquiry_id: 900,
      session_id: 77,
      witness: { name: "Ann Expert", role: "Chief Executive", organisation: "Passenger Watch" },
    };
    render(<SourceDrawer source={evidence} quotes={["faster than wages"]} onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: "Ann Expert" })).toBeInTheDocument();
    expect(screen.getByText("Chief Executive, Passenger Watch")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Rail fares" })).toHaveAttribute("href", "/inquiries/900");
    expect(screen.getByText("faster than wages").tagName).toBe("MARK");
    expect(screen.getByText(/not the committee's view or Parliament's/)).toBeInTheDocument();
  });
});

describe("example questions", () => {
  it("rotate daily through balanced sets", async () => {
    const { EXAMPLE_SETS, examplesFor } = await import("../App");
    const days = [0, 1, 2, 3].map((d) => examplesFor(new Date(Date.UTC(2026, 9, 10 + d))));
    expect(new Set(days.map((d) => d.join("|"))).size).toBe(EXAMPLE_SETS.length);
    for (const set of EXAMPLE_SETS) expect(set).toHaveLength(4);
    const all = EXAMPLE_SETS.flat().join(" ");
    for (const name of ["Labour", "Conservative", "Liberal Democrat", "SNP", "Reform UK", "Plaid Cymru", "peers", "Welsh", "Northern Ireland"]) {
      expect(all).toContain(name);
    }
  });
});

describe("shared briefings", () => {
  it("shows when the briefing was prepared and offers its link", () => {
    render(
      <AnswerView
        question="q"
        answer={answer}
        streamText=""
        sources={[]}
        briefingId="abcdefghjk"
        preparedAt="2026-10-10T09:00:00+00:00"
        recordsUpTo="2026-10-08"
        onCite={vi.fn()}
      />,
    );
    expect(screen.getByText(/Prepared 10 October 2026 from records up to 8 October 2026/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Copy link|Share/ })).toBeInTheDocument();
  });

  it("opens the briefing at a shared address", async () => {
    window.history.pushState(null, "", "/b/abcdefghjk");
    const replies: Record<string, unknown> = {
      "/api/me": { email: null, is_admin: false, auth_required: false },
      "/api/briefings/abcdefghjk": {
        id: "abcdefghjk", question: "What has been said about jury trials?", created_at: "2026-10-10T09:00:00+00:00",
        records_up_to: "2026-10-08", plan: null, sources: [], answer,
      },
    };
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const body = replies[String(url)];
      return new Response(JSON.stringify(body ?? {}), { status: body ? 200 : 404 });
    });
    const { default: App } = await import("../App");
    render(<App />);
    expect(await screen.findByText("What has been said about jury trials?")).toBeInTheDocument();
    expect(screen.getByText(/A saved briefing, prepared 10 October 2026/)).toBeInTheDocument();
    spy.mockRestore();
    window.history.pushState(null, "", "/");
  });
});

describe("elsewhere", () => {
  it("offers Hansard search for the same words, labelled as an alternative when nothing was found", async () => {
    const { ElsewhereForSearch } = await import("../elsewhere");
    const { container } = render(<ElsewhereForSearch words="rail fares" from="2023-01-01" noRecords />);
    expect(container.textContent).toMatch(/^Try instead:/);
    expect(screen.getByRole("link", { name: "Hansard search" }).getAttribute("href")).toContain("searchTerm=rail%20fares&startDate=2023-01-01");
    expect(screen.getByRole("link", { name: "email alerts" })).toBeInTheDocument();
  });
});
