/** The bills page: every bill debated since 1997, newest first, filtered as you type. */
import "./pages";
import { fmtDate } from "./render";

interface BillRow {
  slug: string;
  name: string;
  first: string;
  last: string;
  houses: string[];
  sittings: number;
  divisions: number;
  latest: string;
}

const LIMIT = 100;

function row(b: BillRow): HTMLLIElement {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = `/bills/${b.slug}`;
  a.className = "topic-link";
  a.textContent = b.name;
  const meta = document.createElement("p");
  meta.className = "muted small";
  const years = b.first.slice(0, 4) === b.last.slice(0, 4) ? b.first.slice(0, 4) : `${b.first.slice(0, 4)}–${b.last.slice(0, 4)}`;
  meta.textContent = `${years} · ${b.sittings} sitting${b.sittings === 1 ? "" : "s"} · ${b.divisions} vote${b.divisions === 1 ? "" : "s"} · latest: ${b.latest}, ${fmtDate(b.last)}`;
  li.append(a, meta);
  return li;
}

async function load() {
  const live = document.getElementById("bills-live")!;
  const input = document.getElementById("bills-search") as HTMLInputElement;
  try {
    const { bills } = (await (await fetch("/api/bills")).json()) as { bills: BillRow[] };
    const render = () => {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      const hits = bills.filter((b) => words.every((w) => b.name.toLowerCase().includes(w)));
      const ul = document.createElement("ul");
      ul.className = "topic-list";
      for (const b of hits.slice(0, LIMIT)) ul.append(row(b));
      const count = document.createElement("p");
      count.className = "muted small";
      count.textContent = words.length
        ? `${hits.length.toLocaleString("en-GB")} bill${hits.length === 1 ? "" : "s"} found${hits.length > LIMIT ? `; the ${LIMIT} most recent are shown` : ""}.`
        : `${bills.length.toLocaleString("en-GB")} bills. The ${Math.min(LIMIT, bills.length)} most recently debated are shown; search to find others.`;
      live.replaceChildren(count, ul);
    };
    input.addEventListener("input", render);
    render();
  } catch {
    live.replaceChildren(Object.assign(document.createElement("p"), { className: "form-error", textContent: "The bills could not be loaded just now." }));
  }
}

void load();
