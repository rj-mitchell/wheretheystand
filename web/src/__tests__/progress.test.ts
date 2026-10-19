import { describe, expect, it } from "vitest";
import { progressSteps } from "../components/Progress";
import type { Turn } from "../types";

const base: Turn = {
  id: "1",
  question: "q",
  settings: {} as Turn["settings"],
  sources: [],
  streamText: "",
  status: "planning",
  stages: { start: 1000 },
};

describe("progress steps", () => {
  it("starts on reading the question, with the archive size once known", () => {
    const steps = progressSteps({ ...base, passages: 7_855_744 }, 1500);
    expect(steps.map((s) => s.key)).toEqual(["read", "search", "choose", "write", "check"]);
    expect(steps[0].ended).toBeUndefined();
    expect(steps[1].label).toBe("Searching 7,855,744 passages of Hansard by words and by meaning");
  });

  it("ticks steps off with what they found", () => {
    const t: Turn = {
      ...base,
      status: "answering",
      plan: { search_text: "school uniforms" } as Turn["plan"],
      candidates: 512,
      evidence: { records: 24, speakers: 19, divisions: 2, parties: { Labour: 9, Conservative: 8, Reform: 1 } } as never,
      streamText: "One two three",
      stages: { start: 1000, plan: 3000, sources: 4500, text: 5600 },
    };
    const [read, search, choose, write, check] = progressSteps(t, 6000);
    expect(read.detail).toBe("Looking for “school uniforms”");
    expect(read.ended).toBe(3000);
    expect(search.detail).toBe("512 possible matches");
    expect(choose.detail).toBe("24 contributions from 19 speakers in 3 parties, with 2 votes");
    expect(write.detail).toBe("3 words so far");
    expect(write.ended).toBeUndefined();
    expect(check.began).toBeUndefined();
  });
});
