import { useEffect, useState } from "react";
import type { Turn } from "../types";

/** When each stage of a briefing began, by the reader's clock (milliseconds), for the progress list. */
export interface Stages {
  start: number;
  plan?: number;
  sources?: number;
  text?: number;
  check?: number;
}

interface Step {
  key: string;
  label: string;
  detail?: string;
  began?: number;
  ended?: number;
}

const n = (x: number) => x.toLocaleString("en-GB");
const secs = (ms: number) => `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;

function plural(count: number, one: string, many = `${one}s`) {
  return `${n(count)} ${count === 1 ? one : many}`;
}

export function progressSteps(t: Turn, now: number): Step[] {
  const s = t.stages ?? { start: now };
  const ev = t.evidence;
  const extras = ev
    ? [
        ev.divisions ? plural(ev.divisions, "vote") : "",
        ev.edms ? plural(ev.edms, "early day motion") : "",
        ev.devolved ? plural(ev.devolved, "contribution") + " in the devolved parliaments" : "",
        ev.committee_evidence ? plural(ev.committee_evidence, "witness", "witnesses") + " to committees" : "",
      ].filter(Boolean)
    : [];
  const parties = ev ? Object.keys(ev.parties ?? {}).length : 0;
  const words = t.streamText ? t.streamText.trim().split(/\s+/).length : 0;
  return [
    {
      key: "read",
      label: "Reading the question",
      detail: t.plan && s.plan ? `Looking for “${t.plan.search_text}”` : undefined,
      began: s.start,
      ended: s.plan,
    },
    {
      key: "search",
      label: t.passages
        ? `Searching ${n(t.passages)} passages of Hansard by words and by meaning`
        : "Searching Hansard by words and by meaning",
      detail: s.sources && t.candidates ? `${n(t.candidates)} possible matches` : undefined,
      began: s.plan,
      ended: s.sources,
    },
    {
      key: "choose",
      label: "Choosing a balanced set of contributions",
      detail:
        s.sources && ev
          ? `${plural(ev.records, "contribution")} from ${plural(ev.speakers, "speaker")}` +
            (parties ? ` in ${plural(parties, "party", "parties")}` : "") +
            (extras.length ? `, with ${extras.join(", ")}` : "")
          : undefined,
      began: s.plan,
      ended: s.sources,
    },
    {
      key: "write",
      label: "Writing the briefing from those sources only",
      detail: words ? `${n(words)} words so far` : s.sources ? "Claude is reading the sources" : undefined,
      began: s.sources,
      ended: s.check ?? (t.status === "done" ? now : undefined),
    },
    {
      key: "check",
      label: "Checking every citation and quotation against the record",
      began: s.check,
      ended: t.status === "done" ? now : undefined,
    },
  ];
}

/** What is happening while a briefing is prepared: shown the moment the question is sent, each step ticked off with
 * what it found, so the wait shows the work being done. Once the briefing starts to appear, only the current step
 * is shown, under the question. */
export function Progress({ turn }: { turn: Turn }) {
  const [now, setNow] = useState(() => Date.now());
  const active = !["done", "error"].includes(turn.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [active]);
  if (!active) return null;
  const steps = progressSteps(turn, now);
  const current = steps.findIndex((st) => st.ended === undefined);
  const shown = turn.streamText ? steps.filter((_, i) => i === current) : steps;
  return (
    <>
    <p className="visually-hidden" role="status">
      {current >= 0 ? `${steps[current].label}…` : ""}
    </p>
    <ol className={turn.streamText ? "progress compact" : "progress"} aria-label="Progress">
      {shown.map((st) => {
        const i = steps.indexOf(st);
        const state = i < current || current === -1 ? "done" : i === current ? "active" : "pending";
        return (
          <li key={st.key} className={state} aria-current={state === "active" ? "step" : undefined}>
            <span className="progress-label">
              {st.label}
              {state === "done" && <span className="visually-hidden"> (done)</span>}
            </span>
            {st.began !== undefined && state !== "pending" && (
              <span className="progress-time" aria-hidden="true">
                {secs((state === "done" ? (st.ended ?? now) : now) - st.began)}
              </span>
            )}
            {st.detail && state !== "pending" && (
              <span className="progress-detail" aria-hidden={st.key === "write" ? "true" : undefined}>
                {st.detail}
              </span>
            )}
          </li>
        );
      })}
    </ol>
    </>
  );
}
