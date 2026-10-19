import { describe, expect, it } from "vitest";
import { stancesFrom } from "../stances";
import type { Answer, Segment } from "../types";

const seg = (text: string, sources: number[] = []): Segment => ({
  text,
  sources,
  cited: sources.map((source) => ({ source, cited_text: "x" })),
  status: sources.length ? "cited" : "connective",
});

const answer = (segments: Segment[]): Answer => ({
  segments,
  markdown: segments.map((s) => s.text).join(""),
  no_records: false,
  citations_total: 4,
  citations_invalid: 0,
  grounded_ratio: 1,
  unsupported_segments: 0,
});

describe("where they stand", () => {
  it("reads each group's position from the section headings, skipping the title, counting sources", () => {
    const rows = stancesFrom(
      answer([
        seg("**Cost of living: positions in Parliament**\n\nSummary.", [1]),
        seg("\n\n**Ministers: setting out measures**\n\nThey said this.", [2]),
        seg(" And this.", [3, 2]),
        seg("\n\n**Opposition MPs: critical**\n\nThey disagreed.", [4]),
      ]),
    );
    expect(rows).toEqual([
      { heading: "Ministers: setting out measures", group: "Ministers", position: "setting out measures", sources: 2 },
      { heading: "Opposition MPs: critical", group: "Opposition MPs", position: "critical", sources: 1 },
    ]);
  });

  it("keeps a first heading that follows text, and reads ## headings", () => {
    const rows = stancesFrom(
      answer([seg("Summary first.", [1]), seg("\n\n## Peers: cautious\n\nText.", [2]), seg("\n\n## MPs: keen\n\nMore.", [3])]),
    );
    expect(rows.map((r) => r.group)).toEqual(["Peers", "MPs"]);
  });

  it("shows nothing with fewer than two group sections, or no records", () => {
    expect(stancesFrom(answer([seg("**Title: x**\n\nText", [1]), seg("\n\n**Ministers: a**\n\nText", [2])]))).toEqual([]);
    expect(stancesFrom({ ...answer([]), no_records: true })).toEqual([]);
    expect(stancesFrom(null)).toEqual([]);
  });
});
