/** Script for the static pages (methodology, status and the like): styles, a cookie-free page count, live status. */
import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "./styles.css";
import { api } from "./api";
import { setUpNavMore } from "./navMore";
import { fmtDate } from "./render";

void api.hit(window.location.pathname, document.referrer);

// "Support this site" links stay hidden until a donations page is configured on the server
async function showDonate() {
  const links = document.querySelectorAll<HTMLAnchorElement>("a[data-donate]");
  if (!links.length) return;
  try {
    const { donate_url } = (await (await fetch("/api/site")).json()) as { donate_url: string | null };
    if (!donate_url) return;
    for (const a of links) {
      a.href = donate_url;
      a.hidden = false;
    }
    for (const el of document.querySelectorAll<HTMLElement>("[data-donate-text]")) el.hidden = false;
  } catch {
    /* no link: nothing to show */
  }
}
void showDonate();

// the current page's link; of links to parts of one page (the devolved parliaments), only the part shown
for (const a of document.querySelectorAll<HTMLAnchorElement>(".page-nav a")) {
  if (a.pathname === window.location.pathname.replace(/\.html$/, "") && (!a.hash || a.hash === window.location.hash))
    a.setAttribute("aria-current", "page");
}
setUpNavMore();

interface Status {
  archive_start: string;
  latest_record: string | null;
  latest_vote?: string | null;
  last_update: { finished_at: string | null; status: string } | null;
  briefings: "available" | "paused" | "unavailable";
  briefings_note: string | null;
  passages: number;
  meaning_search_coverage: number;
}

const BRIEFINGS: Record<Status["briefings"], string> = {
  available: "Available",
  paused: "Paused",
  unavailable: "Unavailable (sources only)",
};

function row(dl: HTMLElement, term: string, value: string, state?: string) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = value;
  if (state) dd.dataset.state = state;
  dl.append(dt, dd);
}

async function showStatus(el: HTMLElement) {
  try {
    const resp = await fetch("/api/status");
    if (!resp.ok) throw new Error(String(resp.status));
    const s = (await resp.json()) as Status;
    const dl = document.createElement("dl");
    dl.className = "status-list";
    row(dl, "Briefings", BRIEFINGS[s.briefings] + (s.briefings_note ? `. ${s.briefings_note}` : ""), s.briefings);
    row(dl, "Search", "Available", "available");
    row(dl, "Most recent record", s.latest_record ? fmtDate(s.latest_record) : "Not yet loaded");
    if (s.latest_vote !== undefined) {
      // votes more than a month behind the debates mean the vote import has stalled
      const behind =
        !!s.latest_vote && !!s.latest_record && Date.parse(s.latest_record) - Date.parse(s.latest_vote) > 31 * 86_400_000;
      row(
        dl,
        "Most recent vote",
        s.latest_vote ? fmtDate(s.latest_vote) + (behind ? " (behind the debates: votes are being caught up)" : "") : "Not yet loaded",
        behind ? "paused" : undefined,
      );
    }
    if (s.last_update?.finished_at) {
      const at = new Date(s.last_update.finished_at);
      row(
        dl,
        "Last update",
        `${fmtDate(s.last_update.finished_at.slice(0, 10))} at ${at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" })} (${s.last_update.status === "ok" ? "completed" : s.last_update.status})`,
        s.last_update.status === "ok" ? "available" : "paused",
      );
    }
    row(dl, "Archive", `From ${fmtDate(s.archive_start)}: ${s.passages.toLocaleString("en-GB")} passages`);
    row(
      dl,
      "Meaning-based search",
      `Covers ${Math.round(s.meaning_search_coverage * 100)}% of passages so far (newest first); keyword search covers all of them`,
    );
    el.replaceChildren(dl);
  } catch {
    el.textContent = "The status service could not be reached. If this page loads but briefings do not, the site may be restarting.";
  }
}

const live = document.getElementById("status-live");
if (live) void showStatus(live);

// ---- corrections page --------------------------------------------------------------------------------

interface Correction {
  date: string;
  kind: "withdrawn" | "correction" | "complaint" | "general";
  briefing_id: string | null;
  question: string | null;
  note: string;
}

async function showCorrections(el: HTMLElement) {
  try {
    const resp = await fetch("/api/corrections");
    if (!resp.ok) throw new Error(String(resp.status));
    const { corrections } = (await resp.json()) as { corrections: Correction[] };
    if (!corrections.length) {
      const p = document.createElement("p");
      p.textContent = "No corrections so far.";
      el.replaceChildren(p);
      return;
    }
    const ol = document.createElement("ol");
    ol.className = "corrections-list";
    for (const c of corrections) {
      const li = document.createElement("li");
      const when = document.createElement("time");
      when.dateTime = c.date;
      when.textContent = fmtDate(c.date);
      const what = document.createElement("strong");
      what.textContent = c.kind === "withdrawn" ? "Briefing withdrawn" : "Correction";
      const body = document.createElement("p");
      body.textContent = (c.question ? `“${c.question}”. ` : "") + c.note;
      li.append(when, " ", what, body);
      ol.append(li);
    }
    el.replaceChildren(ol);
  } catch {
    el.textContent = "The corrections log could not be loaded. Please try again later.";
  }
}

const correctionsEl = document.getElementById("corrections-live");
if (correctionsEl) void showCorrections(correctionsEl);

const reportForm = document.getElementById("report-form") as HTMLFormElement | null;
reportForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = new FormData(reportForm);
  const errorEl = document.getElementById("report-error")!;
  const message = String(data.get("message") ?? "").trim();
  if (message.length < 10) {
    errorEl.textContent = "Please describe the problem in a sentence or two.";
    errorEl.hidden = false;
    return;
  }
  const briefing = String(data.get("briefing") ?? "").match(/\/b\/([a-km-z2-9]{10})/)?.[1] ?? null;
  const button = reportForm.querySelector("button[type=submit]") as HTMLButtonElement;
  button.disabled = true;
  try {
    const resp = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: data.get("kind"),
        message,
        briefing_id: briefing,
        contact: String(data.get("contact") ?? "").trim() || null,
        website: String(data.get("website") ?? ""),
      }),
    });
    if (!resp.ok) {
      const detail = (await resp.json().catch(() => ({}))).detail;
      throw new Error(typeof detail === "string" ? detail : "failed");
    }
    reportForm.hidden = true;
    document.getElementById("report-sent")!.hidden = false;
  } catch (err) {
    errorEl.textContent =
      err instanceof Error && err.message !== "failed"
        ? err.message
        : "The report could not be sent. Please try again, or email admin@wherempsstand.com.";
    errorEl.hidden = false;
    button.disabled = false;
  }
});
