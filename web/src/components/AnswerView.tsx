import { useId, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { briefingDocx, downloadName } from "../docx";
import { briefingWithSources, referencesOnly } from "../export";
import { fmtDate, renderAnswer, renderPlain, segmentsToMarkdown } from "../render";
import { stancesFrom, type Stance } from "../stances";
import type { Answer, Highlight, SourceCard } from "../types";
import { ReportProblem } from "./ReportProblem";

interface Props {
  question: string;
  answer?: Answer | null;
  streamText: string;
  sources: SourceCard[];
  briefingKey?: string;
  briefingId?: string;
  preparedAt?: string;
  recordsUpTo?: string | null;
  onCite: (h: Highlight) => void;
}

export function AnswerView({
  question,
  answer,
  streamText,
  sources,
  briefingKey,
  briefingId,
  preparedAt,
  recordsUpTo,
  onCite,
}: Props) {
  const link = briefingId ? `${window.location.origin}/b/${briefingId}` : undefined;
  const [showUncited, setShowUncited] = useState(false);
  const flagged = useMemo(() => new Set(sources.filter((s) => s.interest).map((s) => String(s.n))), [sources]);
  const html = useMemo(
    () => markInterests(answer ? renderAnswer(segmentsToMarkdown(answer.segments)) : renderPlain(streamText), flagged),
    [answer, streamText, flagged],
  );

  function onClick(e: React.MouseEvent<HTMLDivElement>) {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button.cite");
    if (!btn || !answer) return;
    const n = Number(btn.dataset.source);
    const quotes = answer.segments.flatMap((s) => s.cited.filter((c) => c.source === n).map((c) => c.cited_text));
    onCite({ source: n, quotes });
  }

  const body = useRef<HTMLDivElement>(null);
  const checkId = `grounding-${useId().replace(/:/g, "")}`;
  const stances = useMemo(() => stancesFrom(answer), [answer]);

  return (
    <div className="answer">
      <p className="answer-head">
        <span className="answer-label">Briefing · written by AI</span>
        {answer && !answer.no_records && <CheckBadge answer={answer} target={checkId} />}
      </p>
      {stances.length > 0 && <WhereTheyStand stances={stances} body={body} />}
      {/* eslint-disable-next-line react/no-danger -- sanitised with DOMPurify in render.ts */}
      <div
        ref={body}
        className={answer ? (showUncited ? "answer-body show-uncited" : "answer-body") : "answer-body streaming"}
        onClick={onClick}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {answer && (
        <Grounding id={checkId} answer={answer} showUncited={showUncited} onToggle={() => setShowUncited(!showUncited)} />
      )}
      {answer && !answer.no_records && (
        <>
          <p className="ai-note">
            Written by AI (Claude) from the cited Hansard records only. Check the sources before relying on it.
            {preparedAt &&
              ` Prepared ${fmtDate(preparedAt.slice(0, 10))}${recordsUpTo ? ` from records up to ${fmtDate(recordsUpTo)}` : ""}.`}
          </p>
          <div className="answer-actions">
            {link && <ShareButton url={link} title={question} />}
            <details className="export-menu">
              <summary className="secondary">Export</summary>
              <div className="export-list">
                <CopyButton
                  label="Copy with sources"
                  text={() => briefingWithSources(question, answer, sources, new Date(), link)}
                />
                <CopyButton label="Copy references only" text={() => referencesOnly(answer, sources)} />
                <button
                  type="button"
                  className="secondary"
                  onClick={() => downloadWord(question, answer, sources, link ?? window.location.href)}
                >
                  Download Word
                </button>
                <button type="button" className="secondary" onClick={() => window.print()}>
                  Print or save as PDF
                </button>
                {link && <CopyButton label="Copy embed code" text={() => embedCode(link, question)} />}
              </div>
            </details>
            <Feedback question={question} briefingKey={briefingKey} />
          </div>
          <ReportProblem briefingId={briefingId} />
        </>
      )}
    </div>
  );
}

/** Next to the briefing's label: the result of checking every citation and quotation against the record, which is
 * what sets a briefing apart from a chatbot's answer. Points to the full note under the briefing. */
function CheckBadge({ answer, target }: { answer: Answer; target: string }) {
  const problems = answer.unsupported_segments + (answer.quotes_unverified ?? 0) + (answer.loaded_words ?? 0);
  const text = problems
    ? `${problems} point${problems === 1 ? "" : "s"} to check`
    : `✓ ${answer.citations_total} citation${answer.citations_total === 1 ? "" : "s"} checked against the record`;
  return (
    <a className={problems ? "check-badge warn" : "check-badge ok"} href={`#${target}`}>
      {text}
    </a>
  );
}

/** At the top of a briefing: each group and its position, as the briefing's own section headings put it, with a
 * link to that section. Built from the headings (no extra AI), so it always matches the text below. */
function WhereTheyStand({ stances, body }: { stances: Stance[]; body: React.RefObject<HTMLDivElement> }) {
  function go(heading: string) {
    const el = [...(body.current?.querySelectorAll<HTMLElement>("h2, h3, h4, p > strong:only-child") ?? [])].find(
      (e) => e.textContent?.trim() === heading,
    );
    const target = el?.tagName === "STRONG" ? el.parentElement : el;
    if (!target) return;
    target.tabIndex = -1;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.focus({ preventScroll: true });
  }
  return (
    <section className="stances" aria-label="Where they stand, in brief">
      <h3 className="stances-head">Where they stand</h3>
      <table>
        <thead>
          <tr>
            <th scope="col">Who</th>
            <th scope="col">Position</th>
            <th scope="col" className="num">
              Sources
            </th>
          </tr>
        </thead>
        <tbody>
          {stances.map((s) => (
            <tr key={s.heading}>
              <th scope="row">
                <button type="button" className="link" onClick={() => go(s.heading)}>
                  {s.group}
                </button>
              </th>
              <td>{s.position}</td>
              <td className="num">
                {s.sources}
                <span className="unit"> source{s.sources === 1 ? "" : "s"}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** Citation bubbles for sources whose speaker had a registered interest that may relate: shown in another colour
 * (after sanitising, so only these known attributes are added). */
export function markInterests(html: string, flagged: Set<string>): string {
  if (!flagged.size) return html;
  return html.replace(
    /class="cite" data-source="(\d+)" aria-label="Open source \1"/g,
    (m, n: string) =>
      flagged.has(n)
        ? `class="cite interest" data-source="${n}" aria-label="Open source ${n}: the speaker had a registered interest that may relate" title="Registered interest that may relate"`
        : m,
  );
}

/** A snippet for a website or blog: the briefing's preview card, linking back to the briefing. */
export function embedCode(link: string, question: string): string {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  return (
    `<a href="${esc(link)}" style="display:inline-block;max-width:600px">` +
    `<img src="${esc(link)}/card.png" alt="${esc(`Where MPs Stand briefing: ${question}`)}" ` +
    `width="600" style="width:100%;height:auto;border:1px solid #ccc"></a>\n` +
    `<p style="font-size:0.9em">From <a href="${esc(link)}">Where MPs Stand</a>, a briefing on the UK ` +
    `Parliament written by AI from the Hansard record (CC BY 4.0).</p>`
  );
}

function downloadWord(question: string, answer: Answer, sources: SourceCard[], link: string) {
  const blob = new Blob([briefingDocx(question, answer, sources, link).buffer as ArrayBuffer], {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = downloadName(question);
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The phone's share sheet where there is one; otherwise copy the link. */
function ShareButton({ url, title }: { url: string; title: string }) {
  const [done, setDone] = useState(false);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  async function share() {
    try {
      if (canShare) await navigator.share({ url, title: `${title} — Where MPs Stand` });
      else {
        await navigator.clipboard.writeText(url);
        setDone(true);
        setTimeout(() => setDone(false), 2000);
      }
    } catch {
      /* cancelled or unavailable */
    }
  }
  return (
    <button type="button" className="primary" onClick={() => void share()}>
      {done ? "Link copied" : canShare ? "Share" : "Copy link"}
    </button>
  );
}

function CopyButton({ label, text }: { label: string; text: () => string }) {
  const [done, setDone] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text());
      setDone(true);
      setTimeout(() => setDone(false), 2000);
    } catch {
      /* clipboard unavailable: nothing to do */
    }
  }
  return (
    <button type="button" className="secondary" onClick={() => void copy()}>
      {done ? "Copied" : label}
    </button>
  );
}

function Feedback({ question, briefingKey }: { question: string; briefingKey?: string }) {
  const [sent, setSent] = useState<boolean | null>(null);
  if (sent !== null) return <p className="feedback muted small">Thank you for the feedback.</p>;
  const send = (useful: boolean) => {
    setSent(useful);
    void api.feedback(question, useful, briefingKey).catch(() => undefined);
  };
  return (
    <p className="feedback">
      <span>Was this briefing useful?</span>
      <button type="button" className="link" onClick={() => send(true)}>
        Yes
      </button>
      <button type="button" className="link" onClick={() => send(false)}>
        No
      </button>
    </p>
  );
}

export function Grounding({
  id,
  answer,
  showUncited = false,
  onToggle,
}: {
  id?: string;
  answer: Answer;
  showUncited?: boolean;
  onToggle?: () => void;
}) {
  if (answer.no_records) {
    return <p id={id} className="grounding neutral">No matching records in the archive for this question and these filters.</p>;
  }
  const flagged = answer.unsupported_segments;
  const loaded = answer.loaded_words ?? 0;
  const misquoted = answer.quotes_unverified ?? 0;
  return (
    <p id={id} className={flagged || loaded || misquoted ? "grounding warn" : "grounding ok"}>
      {flagged ? (
        <>
          {flagged === 1 ? "One statement in this briefing is" : `${flagged} statements in this briefing are`} not tied
          to a cited source: be wary of anything you cannot see cited.{" "}
          {onToggle && (
            <button type="button" className="link" onClick={onToggle} aria-pressed={showUncited}>
              {showUncited ? "Hide" : flagged === 1 ? "Show it" : "Show them"}
            </button>
          )}
        </>
      ) : (
        `${answer.citations_total} citation${answer.citations_total === 1 ? "" : "s"}, each checked against the source text.`
      )}
      {loaded > 0 &&
        ` ${loaded} judgemental word${loaded === 1 ? "" : "s"} in the briefing's own voice (underlined); the record itself should be checked.`}
      {answer.citations_invalid > 0 && ` ${answer.citations_invalid} citation(s) could not be verified and were removed.`}
      {misquoted > 0 &&
        ` ${misquoted} quotation${misquoted === 1 ? " was" : "s were"} not found word for word in the cited source (marked); check the source before relying on ${misquoted === 1 ? "it" : "them"}.`}
      {!misquoted && (answer.quotes_total ?? 0) > 0 &&
        ` All ${answer.quotes_total} quotations were checked word for word against their sources.`}
    </p>
  );
}
