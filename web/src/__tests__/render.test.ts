import { describe, expect, it } from "vitest";
import { parseChunk } from "../api";
import { demoteHeadings, highlightParts, renderAnswer, segmentsToMarkdown, typographic } from "../render";
import type { Segment } from "../types";

const seg = (text: string, status: Segment["status"], sources: number[] = []): Segment => ({
  text,
  status,
  sources,
  cited: sources.map((n) => ({ source: n, cited_text: "x" })),
});

describe("answer rendering", () => {
  it("turns citations into clickable source badges", () => {
    const html = renderAnswer(segmentsToMarkdown([seg("Ministers cited costs", "cited", [1, 3]), seg(".", "connective")]));
    expect(html).toContain('data-source="1"');
    expect(html).toContain('aria-label="Open source 3">3</button>');
    expect(html).not.toContain("⟦");
  });

  it("highlights unsupported text", () => {
    const html = renderAnswer(segmentsToMarkdown([seg("An uncited claim about 2023.", "unsupported")]));
    expect(html).toContain('class="unsupported"');
  });

  it("marks quotations not found in their sources, and loaded words, in the same segment", () => {
    const text = 'He said "we will never do that" and called it a disgrace.';
    const seg: Segment = {
      text,
      sources: [1],
      cited: [],
      status: "cited",
      misquotes: [{ start: 8, end: 31, text: "we will never do that" }],
      loaded: [{ start: 49, end: 57, word: "disgrace" }],
    };
    const html = renderAnswer(segmentsToMarkdown([seg]));
    expect(html).toContain('<span class="misquote"');
    expect(html).toContain("\u201cwe will never do that\u201d</span>"); // shown with printers' quotation marks
    expect(html).toContain('<span class="loaded"');
  });

  it("sanitises model output", () => {
    const html = renderAnswer(segmentsToMarkdown([seg('<img src=x onerror="alert(1)"> text', "cited", [1])]));
    expect(html).not.toContain("onerror");
  });

  it("keeps markdown lists intact around citations", () => {
    const md = segmentsToMarkdown([seg("- First point", "cited", [1]), seg("\n- Second point", "cited", [2])]);
    const html = renderAnswer(md);
    expect(html.match(/<li>/g)?.length).toBe(2);
  });
});

describe("highlighting", () => {
  it("marks exact cited quotes in the verbatim text", () => {
    const parts = highlightParts("The line to Crewe is delayed. Costs rose.", ["Costs rose."]);
    expect(parts).toEqual([
      { text: "The line to Crewe is delayed. ", hit: false },
      { text: "Costs rose.", hit: true },
    ]);
  });
});

describe("server-sent events", () => {
  it("parses data lines", () => {
    const evs = [...parseChunk('event: text\ndata: {"type":"text","text":"Hi"}')];
    expect(evs).toEqual([{ type: "text", text: "Hi" }]);
  });
});

describe("people", () => {
  it("attributes speakers the way Hansard prints them", async () => {
    const { attribution, seatTypeLabel, partyAbbr, houseOf } = await import("../people");
    const sp = {
      person_id: "p",
      date: "2024-01-01",
      name: "Hilary Benn",
      house: "commons",
      constituency: "Leeds Central",
      region: "Yorkshire and The Humber",
      party: "Labour",
      settlement: "city",
      urban_area: "West Yorkshire",
      urban_area_pop: 1_864_516,
      offices: [],
      other_names: [],
    };
    expect(attribution(sp)).toEqual({ name: "Hilary Benn", tail: "(Leeds Central) (Lab)" });
    expect(seatTypeLabel(sp)).toBe("city seat (West Yorkshire urban area, 1.9m people)");
    expect(seatTypeLabel({ ...sp, settlement: "town", urban_area_pop: 172_345 })).toBe("town seat (urban area of 172,000)");
    expect(partyAbbr("Labour/Co-operative")).toBe("Lab/Co-op");
    expect(houseOf({ chamber: "Lords written answers", speaker: null })).toBe("lords");
    expect(houseOf({ chamber: "Westminster Hall", speaker: null })).toBe("commons");
  });
});

describe("references", () => {
  const base = {
    n: 3, kind: "record" as const, title: "t", url: "u", date: "2023-03-09", text: "", twfy_url: "tw",
  };
  it("formats Hansard-style references", async () => {
    const { formalReference } = await import("../export");
    expect(formalReference({ ...base, chamber: "House of Commons", colnum: "412" })).toBe("HC Deb 9 March 2023, col 412");
    expect(formalReference({ ...base, chamber: "House of Lords", colnum: "1050" })).toBe("HL Deb 9 March 2023, col 1050");
    expect(formalReference({ ...base, chamber: "Westminster Hall", colnum: "120WH" })).toBe(
      "HC Deb 9 March 2023, col 120WH (Westminster Hall)");
    expect(formalReference({ ...base, chamber: "Written answers", record_type: "answer", uin: "12345" })).toBe(
      "Written question 12345, answered 9 March 2023");
  });
});

describe("demoteHeadings", () => {
  it("starts a briefing's headings below the question", () => {
    expect(demoteHeadings("<h1 id=x>T</h1><h2>S</h2><h5>Deep</h5>")).toBe("<h3 id=x>T</h3><h4>S</h4><h6>Deep</h6>");
    expect(demoteHeadings("<header>ok</header>")).toBe("<header>ok</header>");
  });
});

describe("stageQuestion", () => {
  it("asks about one sitting of a bill, naming the stage, House and date", async () => {
    const { stageQuestion } = await import("../stage");
    expect(stageQuestion("Rail Bill", { date: "2024-03-05", house: "commons", stages: ["Second reading"] })).toBe(
      "What arguments were made at the second reading of the Rail Bill in the House of Commons on 5 March 2024?",
    );
    expect(stageQuestion("Rail Bill", { date: "2024-03-05", house: "lords", stages: ["Debate"] })).toBe(
      "What arguments were made on the Rail Bill in the House of Lords on 5 March 2024?",
    );
    expect(stageQuestion("Rail Bill", { date: "2024-03-05", house: "commons", stages: ["Public Bill Committee"] })).toContain(
      "at the Public Bill Committee of",
    );
  });
});

describe("links to other sites", () => {
  it("builds searches with the words and dates", async () => {
    const { ELSEWHERE } = await import("../elsewhere");
    expect(ELSEWHERE.hansard("onshore wind", "2020-01-01", "2024-12-31")).toBe(
      "https://hansard.parliament.uk/search/Contributions?searchTerm=onshore%20wind&startDate=2020-01-01&endDate=2024-12-31",
    );
    expect(ELSEWHERE.hansard("wind")).toBe("https://hansard.parliament.uk/search/Contributions?searchTerm=wind");
    expect(ELSEWHERE.twfyPerson(10777, "lords")).toBe("https://www.theyworkforyou.com/peer/?p=10777");
    expect(ELSEWHERE.writeToThem("SW1A 0AA")).toBe("https://www.writetothem.com/?pc=SW1A%200AA");
  });
});

describe("typographic quotes", () => {
  it("turns typewriter quotes and apostrophes into printers' ones, in text only", () => {
    expect(typographic('<p>She said "it\'s too high" and the Lords\' view.</p>')).toBe(
      "<p>She said “it’s too high” and the Lords’ view.</p>",
    );
    expect(typographic('<p>&quot;Quoted&quot;<button class="cite" data-source="2" aria-label="Open source 2">2</button>.</p>')).toBe(
      '<p>“Quoted”<button class="cite" data-source="2" aria-label="Open source 2">2</button>.</p>',
    );
    expect(typographic('<p>"One"</p><p>"Two"</p>')).toBe("<p>“One”</p><p>“Two”</p>");
  });
});

describe("group labels", () => {
  it("stand on their own line above the text that follows", () => {
    const html = renderAnswer("**Committee witnesses - supportive**\nDr Price-Forbes said it works.");
    expect(html).toMatch(/<p><strong>Committee witnesses - supportive<\/strong><\/p>\s*<p>Dr Price-Forbes/);
  });
  it("leave a bold phrase inside a sentence alone", () => {
    expect(renderAnswer("A **bold** word.\nNext line.")).toContain("<strong>bold</strong> word.");
  });
});
