import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, askBody, askStream } from "./api";
import { AnswerView } from "./components/AnswerView";
import { Controls } from "./components/Controls";
import { Login } from "./components/Login";
import { PlanChips } from "./components/PlanChips";
import { Progress } from "./components/Progress";
import { ElsewhereForSearch } from "./elsewhere";
import { EvidenceBar } from "./components/Evidence";
import { SourceDrawer, SourceList } from "./components/Sources";
import { SupportReminder } from "./components/SupportReminder";
import { TopicHeader } from "./components/TopicHeader";
import { ATTRIBUTION } from "./attribution";
import { BRAND } from "./brand";
import { Mark } from "./components/Mark";
import { fmtDate } from "./render";
import { SiteFooter } from "./components/SiteFooter";
import { followUpsFor } from "./followups";
import { COMPARE_HANDOVER, MENUS, NAV } from "./nav";
import type {
  Answer,
  CompareSide,
  Evidence,
  Highlight,
  InterestFlag,
  Meta,
  Plan,
  Settings,
  SourceCard,
  Turn,
} from "./types";
import { withInterests } from "./types";

/** The name as set in capitals, with "MPs" kept as written. */
const WORDMARK = (
  <>
    Where <span className="keep-case">MPs</span> Stand
  </>
);

const SHARED = /^\/b\/([a-km-z2-9]{10})\/?$/;
const TOPIC = /^\/topics\/([a-z0-9-]+)\/?$/;

/**
 * Example questions, one set a day. Each set spans parties, both Houses, the nations and kinds of seat, and
 * party-specific examples use topics that party itself campaigns on, not other parties' attack lines.
 * Every question was checked to have plenty of evidence in the record (October 2026), and is specific and recent:
 * a named bill, measure or event rather than a broad subject.
 */
export const EXAMPLE_SETS: string[][] = [
  [
    "How have ministers justified ending winter fuel payments for most pensioners?",
    "What have peers said about the Terminally Ill Adults (End of Life) Bill?",
    "What have SNP MPs said about new North Sea oil and gas licences?",
    "What have Conservative MPs said about inheritance tax on family farms?",
  ],
  [
    "How have ministers explained the plan to raise defence spending to 2.5% of GDP?",
    "What have MPs for rural seats said about bus services since 2024?",
    "What have Northern Ireland MPs said about the Windsor Framework?",
    "What have Liberal Democrat MPs said about carer's allowance overpayments?",
  ],
  [
    "What have select committee chairs said about delays to Post Office Horizon compensation?",
    "What have Welsh MPs said about steel jobs at Port Talbot?",
    "What have Reform UK MPs said about small boat crossings?",
    "What have Green Party MPs said about the cost of renting?",
  ],
  [
    "What have Labour MPs said about banning exploitative zero-hours contracts?",
    "What have peers said about protecting creative works from AI training in the Data (Use and Access) Bill?",
    "What have Plaid Cymru MPs said about rail funding for Wales?",
    "What have MPs for town seats said about high street closures?",
  ],
];

export function examplesFor(day: Date): string[] {
  return EXAMPLE_SETS[Math.floor(day.getTime() / 86_400_000) % EXAMPLE_SETS.length];
}

// Settings saved before this version searched the devolved parliaments and committee evidence only on request;
// they now search everywhere by default, so older saved settings are brought up to date once.
const SETTINGS_VERSION = 2;

function loadSettings(defaults?: Meta["defaults"]): Settings {
  try {
    const saved = localStorage.getItem("hgpt.settings");
    if (saved) {
      const s = JSON.parse(saved) as Settings;
      if ((s.version ?? 1) < SETTINGS_VERSION) {
        return { ...s, version: SETTINGS_VERSION, scope: { ...s.scope, devolved: true, evidence: true } };
      }
      return s;
    }
  } catch {
    /* storage unavailable */
  }
  return {
    version: SETTINGS_VERSION,
    mode: "summary",
    recency: defaults?.recency ?? 0.5,
    roleWeight: defaults?.role_weight ?? 1.5,
    scope: { written: true, pbc: true, votes: true, edms: true, devolved: true, evidence: true },
  };
}

export default function App() {
  const [auth, setAuth] = useState<"unknown" | "in" | "out">("unknown");
  const [user, setUser] = useState<string | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [settings, setSettings] = useState<Settings>(() => loadSettings());
  const [turns, setTurns] = useState<Turn[]>([]);
  // a follow-up's new briefing, to bring into view once it is on the page
  const scrollToTurn = useRef<string | null>(null);
  useEffect(() => {
    const id = scrollToTurn.current;
    const el = id ? document.getElementById(`turn-${id}`) : null;
    if (!el) return;
    scrollToTurn.current = null;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
  }, [turns]);
  // a question handed over from another page, such as an MP's page ("What has … said about …?")
  const [question, setQuestion] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).get("q")?.slice(0, 500) ?? "";
    } catch {
      return "";
    }
  });
  // sides to compare, handed over from the Compare page (?compare=party:labour,party:conservative)
  const [compare, setCompare] = useState<CompareSide[]>(() => {
    try {
      const raw = new URLSearchParams(window.location.search).get("compare") ?? "";
      return raw
        .split(",")
        .map((x) => x.split(":"))
        .filter(([k, id]) => ["party", "person", "nation"].includes(k) && id)
        .slice(0, 3)
        .map(([k, id]) => ({ kind: k as CompareSide["kind"], id }));
    } catch {
      return [];
    }
  });
  const compareLabels = (() => {
    try {
      return (new URLSearchParams(window.location.search).get("sides") ?? "").split("|").filter(Boolean).slice(0, 3);
    } catch {
      return [];
    }
  })();
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const compareStarted = useRef(false);
  const [drawer, setDrawer] = useState<{ turn: string; h: Highlight } | null>(null);
  const [showControls, setShowControls] = useState(false);
  // a written briefing, or the sources only (no AI): remembered for the next visit
  const [sourcesOnly, setSourcesOnly] = useState(() => {
    try {
      return localStorage.getItem("wts.kind") === "sources";
    } catch {
      return false;
    }
  });
  const [hint, setHint] = useState(false); // Ask pressed with nothing typed
  const [supportCount, setSupportCount] = useState(0); // > 0: show the note on what the site costs
  const abortRef = useRef<AbortController | null>(null);
  const briefingUrl = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me.email);
      setAuth("in");
      const m = await api.meta();
      setMeta(m);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setAuth("out");
      else setAuth("in");
    }
  }, []);

  useEffect(() => {
    void refresh();
    void api.hit(window.location.pathname, document.referrer); // cookie-free page count
  }, [refresh]);

  // a topic address (/topics/<slug>) opens that topic's current briefing
  useEffect(() => {
    const m = window.location.pathname.match(TOPIC);
    if (!m) return;
    const slug = m[1];
    api
      .topic(slug)
      .then(({ topic, briefing: b, versions }) => {
        document.title = `${topic.title}: where MPs and peers stand — Where MPs Stand`;
        setTurns([
          b
            ? {
                id: b.id,
                question: b.question,
                settings,
                plan: b.plan ?? undefined,
                sources: withInterests(b.sources, b.interests),
                candidates: b.candidates,
                semantic: b.semantic,
                evidence: b.evidence,
                streamText: "",
                answer: b.answer,
                status: "done",
                briefingId: b.id,
                preparedAt: b.created_at,
                recordsUpTo: b.records_up_to,
                topic: { ...topic, versions },
              }
            : {
                id: slug,
                question: topic.question,
                settings,
                sources: [],
                streamText: "",
                status: "error",
                error: "This topic briefing has not been written yet. It will appear here after a coming update.",
                topic: { ...topic, versions },
              },
        ]);
      })
      .catch(() =>
        setTurns([
          {
            id: slug,
            question: "Topic not found",
            settings,
            sources: [],
            streamText: "",
            status: "error",
            error: "There is no topic at this address. The list of topics is at /topics.",
          },
        ]),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on arrival
  }, []);

  // a shared address (/b/<id>) opens that briefing, exactly as it was prepared
  useEffect(() => {
    const m = window.location.pathname.match(SHARED);
    if (!m) return;
    const id = m[1];
    api
      .briefing(id)
      .then((b) =>
        setTurns([
          {
            id,
            question: b.question,
            settings,
            plan: b.plan ?? undefined,
            sources: withInterests(b.sources, b.interests),
            candidates: b.candidates,
            semantic: b.semantic,
            evidence: b.evidence,
            streamText: "",
            answer: b.answer,
            status: "done",
            briefingId: b.id,
            preparedAt: b.created_at,
            recordsUpTo: b.records_up_to,
            shared: true,
            follows: b.settings?.follows ?? undefined,
          },
        ]),
      )
      .catch((e: unknown) =>
        setTurns([
          {
            id,
            question: e instanceof ApiError && e.status === 410 ? "Briefing withdrawn" : "Briefing not found",
            settings,
            sources: [],
            streamText: "",
            status: "error",
            error:
              e instanceof ApiError && e.status === 410
                ? "This briefing has been withdrawn."
                : "There is no briefing at this address. It may have been mistyped.",
          },
        ]),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on arrival
  }, []);

  // An open source counts as a step in the browser's history, so a phone's Back button closes it (returning to the
  // citation) instead of leaving the briefing.
  const drawerOpen = drawer !== null;
  useEffect(() => {
    if (drawerOpen) void api.event("source");
  }, [drawerOpen]);
  useEffect(() => {
    if (!drawerOpen) return;
    if (!(window.history.state as { sourceDrawer?: boolean } | null)?.sourceDrawer) {
      window.history.pushState({ sourceDrawer: true }, "", window.location.href);
    }
    const onPop = () => {
      setDrawer(null);
      // a briefing finished while its source was open: its address applies to the step we are back on too
      if (briefingUrl.current && window.location.pathname !== briefingUrl.current) {
        window.history.replaceState(window.history.state, "", briefingUrl.current);
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [drawerOpen]);
  const closeDrawer = useCallback(() => {
    if ((window.history.state as { sourceDrawer?: boolean } | null)?.sourceDrawer) window.history.back();
    else setDrawer(null);
  }, []);

  // "Change what is searched" under a briefing links to #settings: open the panel there
  useEffect(() => {
    const open = () => {
      if (window.location.hash === "#settings") setShowControls(true);
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("wts.kind", sourcesOnly ? "sources" : "briefing");
    } catch {
      /* ignore */
    }
  }, [sourcesOnly]);

  useEffect(() => {
    try {
      localStorage.setItem("hgpt.settings", JSON.stringify(settings));
    } catch {
      /* ignore */
    }
  }, [settings]);

  const update = (id: string, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));

  const busy = turns.some((t) => !["done", "error"].includes(t.status));

  async function run(q: string, plan?: Plan, replaceId?: string, sourcesOnly = false) {
    const id = replaceId ?? crypto.randomUUID();
    const previous = turns.filter((t) => t.id !== id && t.status === "done").at(-1)?.question;
    const turn: Turn = {
      id,
      question: q,
      settings,
      sources: [],
      streamText: "",
      status: "planning",
      plan,
      follows: previous,
      stages: { start: Date.now() },
    };
    const stage = (key: "plan" | "sources" | "text" | "check") => (t: Turn) => ({
      stages: { ...(t.stages ?? { start: Date.now() }), [key]: t.stages?.[key] ?? Date.now() },
    });
    if (!replaceId && turns.length) scrollToTurn.current = id; // a follow-up: show where its answer is written
    setTurns((ts) => (replaceId ? ts.map((t) => (t.id === id ? turn : t)) : [...ts, turn]));
    const earlier = turns.filter((t) => t.id !== id && t.status === "done");
    // what each earlier question searched for, so a follow-up ("what about the Lords?") keeps the topic and filters
    const history = earlier.slice(-3).map((t) => ({
      question: t.question,
      answer: t.answer?.markdown?.slice(0, 600) ?? "",
      topic: t.plan?.search_text ?? "",
      scope: t.plan?.interpretation ?? "",
    }));
    const follows = earlier.at(-1)?.question;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const sides = plan ? [] : compare;
      if (sides.length) {
        setCompare([]); // the comparison applies to the question it came with
        update(id, { comparing: compareLabels.length === sides.length ? compareLabels : [] });
      }
      for await (const ev of askStream(askBody(q, settings, plan, history, sourcesOnly, sides, follows), ctrl.signal)) {
        if (ev.type === "interests")
          update(id, (t) => ({ sources: withInterests(t.sources, ev.interests as InterestFlag[]) }));
        else if (ev.type === "progress") {
          if (typeof ev.passages === "number") update(id, { passages: ev.passages });
          if (ev.step === "checking") update(id, stage("check"));
        } else if (ev.type === "plan") update(id, (t) => ({ plan: ev.plan as Plan, status: "searching", ...stage("plan")(t) }));
        else if (ev.type === "sources")
          update(id, (t) => ({
            ...stage("sources")(t),
            sources: ev.sources as SourceCard[],
            candidates: ev.candidates as number,
            semantic: ev.semantic as boolean,
            evidence: ev.evidence as Evidence,
            status: "answering",
          }));
        else if (ev.type === "text")
          update(id, (t) => ({ ...stage("text")(t), streamText: t.streamText + (ev.text as string) }));
        else if (ev.type === "final")
          update(id, (t) => ({
            sources: withInterests(t.sources, ev.interests as InterestFlag[] | undefined),
            answer: (ev.answer as Answer | null) ?? null,
            briefingKey: (ev.briefing_key as string | undefined) ?? undefined,
            briefingId: (ev.briefing_id as string | undefined) ?? undefined,
            preparedAt: (ev.prepared_at as string | undefined) ?? undefined,
            recordsUpTo: (ev.records_up_to as string | null | undefined) ?? undefined,
            note: ev.cached
              ? "This briefing was written earlier for the same question and settings, from the same data."
              : (ev.note as string | undefined),
            status: "done",
          }));
        else if (ev.type === "error") update(id, { status: "error", error: ev.message as string });
        if (ev.type === "final" && typeof ev.support_reminder === "number") setSupportCount(ev.support_reminder);
        if (ev.type === "final" && typeof ev.briefing_id === "string") {
          // the address bar becomes this briefing's permanent link (keeping an open source's history step)
          briefingUrl.current = `/b/${ev.briefing_id}`;
          window.history.replaceState(window.history.state, "", briefingUrl.current);
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") update(id, { status: "error", error: (e as Error).message });
    }
  }

  function submit(e?: React.FormEvent) {
    e?.preventDefault();
    const q = question.trim();
    if (busy) return;
    if (q.length < 3) {
      setHint(true);
      questionRef.current?.focus();
      return;
    }
    setHint(false);
    setQuestion("");
    void run(q, undefined, undefined, sourcesOnly);
  }

  // a comparison chosen on the Compare page starts at once. Only from there: a shared link with the same
  // parameters fills in the question but waits for Ask, so following a link never spends money by itself.
  useEffect(() => {
    if (compareStarted.current || auth !== "in" || !meta || compare.length < 2) return;
    let pending: string | null = null;
    try {
      pending = sessionStorage.getItem(COMPARE_HANDOVER);
      sessionStorage.removeItem(COMPARE_HANDOVER);
    } catch {
      pending = null;
    }
    const q = question.trim();
    if (!pending || pending !== q) return;
    compareStarted.current = true;
    setQuestion("");
    void run(q);
  }, [auth, meta, compare, question]); // eslint-disable-line react-hooks/exhaustive-deps

  if (auth === "unknown") return <div className="loading">Loading…</div>;
  if (auth === "out") return <Login onDone={() => void refresh()} />;

  // Once a briefing is being written it comes first; how it was found (the search, its filters, the spread of
  // speakers) folds away under it. Before that, and for sources-only results, the search is the content.
  const answerFirst = (t: Turn) => !!(t.streamText || (t.answer && !t.answer.no_records));
  const howFound = (t: Turn) => (
    <>
      {t.plan && <PlanChips key={JSON.stringify(t.plan)} plan={t.plan} meta={meta} busy={busy} onRerun={(p) => void run(t.question, p, t.id)} />}
      <EvidenceBar
        evidence={t.evidence}
        scope={t.status === "done" && !t.shared && !t.topic ? t.settings.scope : undefined}
        demographics={
          !!t.plan &&
          (["gender", "age"].includes(t.plan.group_by ?? "role") ||
            !!t.plan.filters.genders?.length ||
            !!t.plan.filters.age_bands?.length)
        }
        seats={
          !!t.plan &&
          (["tenure", "majority"].includes(t.plan.group_by ?? "role") ||
            !!t.plan.filters.tenure?.length ||
            !!t.plan.filters.majority_bands?.length)
        }
      />
    </>
  );
  const front = turns.length === 0 && window.location.pathname === "/";
  const drawerTurn = drawer ? turns.find((t) => t.id === drawer.turn) : undefined;
  const drawerSource = drawerTurn?.sources.find((s) => s.n === drawer?.h.source) ?? null;

  const settingsPanel = showControls && (
    <Controls settings={settings} onChange={setSettings} />
  );
  // On the home page the settings open under the question box; under a briefing the box is pinned to the foot of
  // the screen, so they open just above it, inside the pinned area.
  const composer = (className: string) => {
    const form = (
      <form className={className} onSubmit={submit} role="search" aria-label="Ask a question">
        <label htmlFor="q" className="visually-hidden">
          Your question
        </label>
        <textarea
          id="q"
          value={question}
          placeholder={
            turns.length ? "Ask a follow-up, or a new question…" : "Ask a question about the proceedings of Parliament…"
          }
          rows={2}
          ref={questionRef}
          onChange={(e) => {
            setQuestion(e.target.value);
            if (hint) setHint(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          aria-describedby={hint ? "q-hint" : undefined}
        />
        <div className="composer-buttons">
          <button type="submit" className="primary" disabled={busy}>
            {sourcesOnly ? "Find" : "Ask"}
          </button>
          <button
            type="button"
            id="settings"
            className="link small"
            onClick={() => setShowControls(!showControls)}
            aria-expanded={showControls}
            aria-controls="search-settings"
          >
            Settings
          </button>
        </div>
        <fieldset className="answer-kind">
          <legend className="visually-hidden">What to show</legend>
          <label>
            <input
              type="radio"
              name="kind"
              checked={!sourcesOnly && settings.mode === "summary"}
              onChange={() => {
                setSourcesOnly(false);
                setSettings({ ...settings, mode: "summary" });
              }}
            />
            Executive summary <span className="muted">(AI)</span>
          </label>
          <label>
            <input
              type="radio"
              name="kind"
              checked={!sourcesOnly && settings.mode === "comprehensive"}
              onChange={() => {
                setSourcesOnly(false);
                setSettings({ ...settings, mode: "comprehensive" });
              }}
            />
            Chronological briefing <span className="muted">(AI, longer)</span>
          </label>
          <label>
            <input type="radio" name="kind" checked={sourcesOnly} onChange={() => setSourcesOnly(true)} />
            Sources only <span className="muted">(no AI)</span>
          </label>
          {hint && (
            <span id="q-hint" className="q-hint" role="status">
              Type a question first.
            </span>
          )}
        </fieldset>
      </form>
    );
    return className.includes("composer-inline") ? (
      <>
        {form}
        {settingsPanel}
      </>
    ) : (
      <div className="composer-dock">
        {settingsPanel}
        {form}
      </div>
    );
  };

  return (
    <div className={drawerSource ? "app with-drawer" : "app"}>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      {/* the full title page on the front door only; elsewhere the name sits on one line with the menu */}
      <header className={front ? "masthead full-width" : "masthead full-width compact"}>
        <p className="phase-banner">
          <strong className="phase-tag">Beta</strong>{" "}
          <span>
            This is a new service and it is still being tested. <a href="/corrections#report">Tell us about a problem</a>.
          </span>
        </p>
        <div className="running-head">
          <span>
            Parliament's record{meta ? ` since ${meta.archive_start.slice(0, 4)}` : ""}: debates, questions, statements,
            votes, bills, committees and members' interests
          </span>
          <span className="masthead-actions">
            <nav className="page-nav" aria-label="Site">
              {NAV.map((n) => (
                <a key={n.href} href={n.href} aria-current={n.href === "/" ? "page" : undefined}>
                  {n.label}
                </a>
              ))}
              {MENUS.map((m) => (
                <details key={m.label} className="nav-more">
                  <summary>{m.label}</summary>
                  <div className="nav-more-list">
                    {m.items.map((n) => (
                      <a key={n.href} href={n.href}>
                        {n.label}
                      </a>
                    ))}
                  </div>
                </details>
              ))}
            </nav>
            {user && (
              <button type="button" className="link" onClick={() => void api.logout().then(() => setAuth("out"))}>
                Sign out
              </button>
            )}
          </span>
        </div>
        <div className="title-block">
          {front ? (
            <Mark />
          ) : (
            <a href="/" className="home-link" aria-label={`${BRAND.name}: home`}>
              <Mark />
            </a>
          )}
          {/* a topic page's own title is its main heading; elsewhere the site's name is */}
          {TOPIC.test(window.location.pathname) ? (
            <p className="wordmark">{WORDMARK}</p>
          ) : (
            <h1 className="wordmark">{WORDMARK}</h1>
          )}
          <p className="tagline">{BRAND.tagline}</p>
          <p className="imprint">
            <span>Independent research service</span>
            <span>Sourced to Hansard</span>
            {meta?.last_sync?.finished_at && <span>Updated {fmtDate(meta.last_sync.finished_at.slice(0, 10))}</span>}
          </p>
        </div>
      </header>

      {supportCount > 0 && meta?.donate_url && (
        <SupportReminder count={supportCount} donateUrl={meta.donate_url} onClose={() => setSupportCount(0)} />
      )}
      {compare.length > 0 && (
        <aside className="banner" aria-label="Comparison">
          Comparing {compareLabels.length === compare.length ? compareLabels.join(" and ") : "the chosen sides"}: each is
          searched separately and given an equal share of the sources.{" "}
          <button type="button" className="link" onClick={() => setCompare([])}>
            Cancel the comparison
          </button>
        </aside>
      )}
      {meta && !meta.answers_enabled && (
        <aside className="banner" aria-label="Service notice">
          Briefings need an Anthropic API key on the server. Until one is set you will see the balanced evidence set only.
        </aside>
      )}

      <main className="thread" id="main" tabIndex={-1}>
        {turns.length === 0 && (
          <section className="empty">
            <h2>
              Ask about the record of Parliament <span className="ai-tag">AI-powered</span>
            </h2>
            <p className="lede">
              Ask in your own words. Get a short briefing on who said what, from which side and in what role, with every
              point cited to Hansard, or the most relevant records on their own, without AI.
            </p>
            <p className="and-more">
              Not just MPs, despite the name: peers, select committees and their witnesses, and the Scottish
              Parliament, the Senedd and the Northern Ireland Assembly are all in here too.
            </p>
            {composer("composer composer-inline")}
            <h3 className="examples-head">Try</h3>
            <ol className="examples">
              {examplesFor(new Date()).map((ex) => (
                <li key={ex}>
                  <button type="button" className="example" onClick={() => {
                      setQuestion(ex);
                      setHint(false);
                      questionRef.current?.focus();
                    }}>
                    {ex}
                  </button>
                </li>
              ))}
            </ol>
            <nav className="start-from" aria-label="Other ways in">
              <span className="examples-head">Or start from</span>
              <a href="/topics">Topic briefings</a>
              <a href="/this-week">This week</a>
              <a href="/bills">Bills</a>
              <a href="/committees">Committees</a>
              <a href="/timeline">Timelines</a>
              <a href="/compare">Compare</a>
              <a href="/developers">Data and API</a>
            </nav>
            <p className="ai-caution">
              AI can make mistakes: check the cited sources before relying on a briefing. Prefer no AI? Choose
              &ldquo;Sources only&rdquo; to see the most relevant records without a written briefing, or use{" "}
              <a href="https://hansard.parliament.uk/search" target="_blank" rel="noreferrer">
                Hansard's own search
              </a>{" "}
              to find every mention of a word. The <a href="/resources">other resources</a> page lists more sites
              worth using alongside this one.
            </p>
            <h3 className="examples-head">How this differs from a search</h3>
            <ul className="ask-why">
              <li>
                <strong>Answers, not a list of hits.</strong> Hansard's own search and TheyWorkForYou list every
                mention of the words you type, in date order, for you to read through. Here the question is read for
                its meaning, so the MPs and peers who spoke on it are found even when they put it differently, and the
                speeches, answers, statements and votes since {meta ? meta.archive_start.slice(0, 4) : "1997"} that
                answer it are brought together in one briefing.
              </li>
              <li>
                <strong>Balanced by design.</strong> Each briefing draws on ministers and the opposition, backbenchers
                and peers, across parties and parts of the country, and shows that spread. It takes no view of its
                own.
              </li>
              <li>
                <strong>Checkable.</strong> Each point links to the record it comes from, with the speaker's party,
                seat and role on the day. Every citation and quotation is checked word for word against the source,
                and the few statements not tied to a source are counted under the briefing, so you know what to check.
              </li>
            </ul>
          </section>
        )}
        {turns.map((t) => (
          <article key={t.id} id={`turn-${t.id}`} className="turn">
            {t.topic && <TopicHeader topic={t.topic} preparedAt={t.preparedAt} recordsUpTo={t.recordsUpTo} />}
            {t.comparing !== undefined && (
              <p className="eyebrow">
                Comparison{t.comparing.length ? `: ${t.comparing.join(" and ")}` : ""} ·{" "}
                <a href="/compare">Compare something else</a>
              </p>
            )}
            {t.follows && t.follows !== t.question && (
              <p className="eyebrow follows">Follow-up to &ldquo;{t.follows}&rdquo;</p>
            )}
            <h2 className="question">{t.question}</h2>
            {t.shared && t.preparedAt && (
              <p className="shared-note">
                A saved briefing, prepared {fmtDate(t.preparedAt.slice(0, 10))}. The record may have changed since.{" "}
                <button type="button" className="link" disabled={busy} onClick={() => void run(t.question)}>
                  Ask again for an up-to-date briefing
                </button>
              </p>
            )}
            <Progress turn={t} />
            {!answerFirst(t) && howFound(t)}
            {t.status === "done" && (
              <p className="visually-hidden" role="status">
                {t.answer ? "Briefing ready." : "Sources ready."}
              </p>
            )}
            {(t.streamText || t.answer) && (
              <AnswerView
                question={t.question}
                answer={t.answer}
                streamText={t.streamText}
                sources={t.sources}
                briefingKey={t.briefingKey}
                briefingId={t.briefingId}
                preparedAt={t.preparedAt}
                recordsUpTo={t.recordsUpTo}
                onCite={(h) => setDrawer({ turn: t.id, h })}
              />
            )}
            {t.note && <p className="muted small">{t.note}</p>}
            {answerFirst(t) && (
              <details className="page-more how-found">
                <summary>How this was found: the search, its filters and who the sources are from</summary>
                {howFound(t)}
              </details>
            )}
            <InterestsNote sources={t.sources} />
            {t.status === "done" && t.id === turns.at(-1)?.id && !busy && followUpsFor(t).length > 0 && (
              <nav className="follow-ups" aria-label="Follow-up questions">
                <span className="muted small">Ask a follow-up:</span>
                {followUpsFor(t).map((f) => (
                  <button key={f} type="button" className="follow-up" onClick={() => void run(f)}>
                    {f}
                  </button>
                ))}
                <span className="muted small">or type your own below.</span>
              </nav>
            )}
            {t.status === "error" && (
              <p className="error" role="alert">
                {t.error}
              </p>
            )}
            <SourceList
              sources={t.sources}
              onOpen={(h) => setDrawer({ turn: t.id, h })}
              open={t.status === "answering" || (!t.answer && t.status === "done")}
              writing={t.status === "answering"}
            />
            {t.status === "done" && t.plan && (
              <ElsewhereForSearch
                words={t.plan.search_text}
                from={t.plan.filters.date_from}
                to={t.plan.filters.date_to}
                noRecords={!!t.answer?.no_records || t.sources.length === 0}
              />
            )}
            {t.status === "done" && t.semantic === false && (
              <p className="muted small">Meaning-based search is still being built; these results used keyword search.</p>
            )}
          </article>
        ))}
      </main>

      {turns.length > 0 && composer("composer")}

      <SourceDrawer source={drawerSource} quotes={drawer?.h.quotes ?? []} onClose={closeDrawer} />

      <SiteFooter donateUrl={meta?.donate_url} attribution={meta?.attribution ?? ATTRIBUTION}>
        <p className="print-only">
          From {BRAND.name} (beta), wheretheystand.uk. Briefings are written by AI from the cited Hansard records; check the
          sources.
        </p>
      </SiteFooter>
    </div>
  );
}

/** Under a briefing: which quoted members had registered interests that may relate to what they said. */
function InterestsNote({ sources }: { sources: SourceCard[] }) {
  const flagged = sources.filter((s) => s.interest);
  if (!flagged.length) return null;
  const names = [...new Map(flagged.map((s) => [s.interest!.person_id, s.interest!.name])).values()];
  const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
  return (
    <p className="interests-note">
      <span className="interest-flag">Registered interests</span> {list}{" "}
      {names.length === 1 ? "had an entry" : "had entries"} in the register of interests, made before they spoke, that
      may relate to what they said ({flagged.length === 1 ? "source" : "sources"} {flagged.map((s) => s.n).join(", ")}). Open a
      source for the details.
      Matched by AI; a link to check, not a finding of wrongdoing.
    </p>
  );
}
