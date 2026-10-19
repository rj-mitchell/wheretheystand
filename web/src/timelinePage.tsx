/** The party timelines page: pick a party and a topic; the address keeps both, so a timeline can be shared. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./pages";
import { Timeline } from "./components/Timeline";

// the parties most members have sat for since 1997 come first; the rest follow alphabetically
const MAIN = ["labour", "conservative", "liberal-democrat", "scottish-national-party", "reform", "green", "plaid-cymru",
  "dup", "sinn-fein", "social-democratic-and-labour-party", "uup", "alliance", "crossbench"];

function Page() {
  const params = new URLSearchParams(window.location.search);
  const [parties, setParties] = useState<{ id: string; name: string }[]>([]);
  const [party, setParty] = useState(params.get("party") ?? "");
  const [topic, setTopic] = useState(params.get("q") ?? "");
  const [shown, setShown] = useState<{ party: string; q: string } | null>(
    params.get("party") && params.get("q") ? { party: params.get("party")!, q: params.get("q")! } : null,
  );

  useEffect(() => {
    fetch("/api/meta")
      .then((r) => r.json())
      .then((m: { parties: { id: string; name: string }[] }) => {
        const skip = new Set(["bishop", "judge", "speaker", "other", "non-affiliated", "independent"]);
        const list = m.parties.filter((p) => !skip.has(p.id));
        list.sort((a, b) => {
          const ia = MAIN.indexOf(a.id), ib = MAIN.indexOf(b.id);
          return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.name.localeCompare(b.name);
        });
        setParties(list);
      })
      .catch(() => setParties([]));
  }, []);

  return (
    <>
      <form
        className="ask-about"
        onSubmit={(e) => {
          e.preventDefault();
          if (!party || !topic.trim()) return;
          const next = { party, q: topic.trim() };
          setShown(next);
          window.history.replaceState(null, "", `/timeline?party=${encodeURIComponent(next.party)}&q=${encodeURIComponent(next.q)}`);
        }}
      >
        <label htmlFor="party">Party</label>
        <select id="party" value={party} onChange={(e) => setParty(e.target.value)} required>
          <option value="">Choose a party</option>
          {parties.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <label htmlFor="topic">Topic</label>
        <div className="ask-row">
          <input id="topic" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="for example tuition fees" required />
          <button type="submit" className="primary" disabled={!party || !topic.trim()}>
            Show timeline
          </button>
        </div>
      </form>
      {shown && (
        <>
          <h2>
            {parties.find((p) => p.id === shown.party)?.name ?? shown.party}: {shown.q}
          </h2>
          <Timeline query={`party=${encodeURIComponent(shown.party)}&q=${encodeURIComponent(shown.q)}`} showSpeaker />
        </>
      )}
    </>
  );
}

createRoot(document.getElementById("timeline-root")!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
);
