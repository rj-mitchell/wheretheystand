/** The topic briefings page: every topic, grouped by area, with its latest summary; then featured briefings. */
import "./pages";
import { fmtDate } from "./render";

interface TopicRow {
  slug: string;
  title: string;
  area: string;
  description: string;
  current: { briefing_id: string; prepared_at: string; summary: string } | null;
}

interface Featured {
  id: string;
  title: string;
  blurb: string;
  prepared_at: string;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function item(href: string, title: string, lines: string[], dated: string | null): HTMLLIElement {
  const li = el("li");
  const a = el("a", "topic-link", title);
  a.href = href;
  li.append(a);
  for (const line of lines.filter(Boolean)) li.append(el("p", "topic-summary", line));
  li.append(el("p", "muted small", dated ?? "Not written yet: it will appear after a coming update."));
  return li;
}

async function load() {
  const live = document.getElementById("topics-live")!;
  try {
    const r = await fetch("/api/topics");
    if (!r.ok) throw new Error();
    const { topics, featured } = (await r.json()) as { topics: TopicRow[]; featured: Featured[] };
    live.replaceChildren();
    const areas = [...new Set(topics.map((t) => t.area))];
    for (const area of areas) {
      const ul = el("ul", "topic-list");
      for (const t of topics.filter((x) => x.area === area)) {
        ul.append(
          item(
            `/topics/${t.slug}`,
            t.title,
            [t.current?.summary || t.description],
            t.current ? `Updated ${fmtDate(t.current.prepared_at.slice(0, 10))}` : null,
          ),
        );
      }
      live.append(el("h2", undefined, area), ul);
    }
    if (featured.length) {
      const list = document.getElementById("featured-list")!;
      for (const f of featured) {
        list.append(item(`/b/${f.id}`, f.title, [f.blurb], `Prepared ${fmtDate(f.prepared_at.slice(0, 10))}`));
      }
      document.getElementById("featured")!.hidden = false;
    }
  } catch {
    live.replaceChildren(el("p", "form-error", "The topics could not be loaded just now. Please try again later."));
  }
}

void load();
