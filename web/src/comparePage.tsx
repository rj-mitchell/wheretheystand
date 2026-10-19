/** The Compare page: choose what to compare and the topic; the comparison is then written on the main page. */
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { COMPARE_HANDOVER } from "./nav";
import "./pages";

type Kind = "party" | "person" | "nation";
interface Option {
  id: string;
  label: string;
}
const NATIONS: Option[] = [
  { id: "england", label: "England" },
  { id: "scotland", label: "Scotland" },
  { id: "wales", label: "Wales" },
  { id: "northern-ireland", label: "Northern Ireland" },
];
const MAIN = ["labour", "conservative", "liberal-democrat", "scottish-national-party", "reform", "green", "plaid-cymru",
  "dup", "sinn-fein", "social-democratic-and-labour-party", "uup", "alliance", "crossbench"];
const KINDS: [Kind, string][] = [
  ["party", "Parties"],
  ["person", "MPs and peers"],
  ["nation", "Nations (MPs for seats there)"],
];

function MemberPicker({ id, label, value, onChange }: { id: string; label: string; value: Option | null; onChange: (o: Option | null) => void }) {
  const [text, setText] = useState(value?.label ?? "");
  const [options, setOptions] = useState<Option[]>([]);
  useEffect(() => {
    const q = text.trim();
    if (q.length < 3 || q === value?.label) return;
    const t = setTimeout(() => {
      fetch(`/api/people?everyone=true&q=${encodeURIComponent(q)}`)
        .then((r) => r.json())
        .then((b: { people: { number: number; name: string; constituency: string | null; party: string | null }[] }) =>
          setOptions(b.people.slice(0, 30).map((p) => ({
            id: String(p.number),
            label: `${p.name}${p.constituency || p.party ? ` (${[p.constituency, p.party].filter(Boolean).join(", ")})` : ""}`,
          }))),
        )
        .catch(() => setOptions([]));
    }, 250);
    return () => clearTimeout(t);
  }, [text, value]);
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        list={`${id}-list`}
        value={text}
        placeholder="type a name"
        autoComplete="off"
        onChange={(e) => {
          setText(e.target.value);
          onChange(options.find((o) => o.label === e.target.value) ?? null);
        }}
      />
      <datalist id={`${id}-list`}>
        {options.map((o) => (
          <option key={o.id} value={o.label} />
        ))}
      </datalist>
    </>
  );
}

function Compare() {
  const [kind, setKind] = useState<Kind>("party");
  const [parties, setParties] = useState<Option[]>([]);
  const [picks, setPicks] = useState<(Option | null)[]>([null, null, null]);
  const [topic, setTopic] = useState("");
  useEffect(() => {
    fetch("/api/meta")
      .then((r) => r.json())
      .then((m: { parties: { id: string; name: string }[] }) => {
        const skip = new Set(["bishop", "judge", "speaker", "other", "non-affiliated", "independent"]);
        const list = m.parties.filter((p) => !skip.has(p.id)).map((p) => ({ id: p.id, label: p.name }));
        list.sort((a, b) => {
          const ia = MAIN.indexOf(a.id), ib = MAIN.indexOf(b.id);
          return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.label.localeCompare(b.label);
        });
        setParties(list);
      })
      .catch(() => setParties([]));
  }, []);

  const chosen = picks.filter((p): p is Option => !!p);
  const distinct = new Set(chosen.map((p) => p.id)).size === chosen.length;
  const ready = chosen.length >= 2 && distinct && topic.trim().length >= 3;
  const set = (i: number, o: Option | null) => setPicks((ps) => ps.map((p, k) => (k === i ? o : p)));
  const options = kind === "party" ? parties : NATIONS;
  const name = (o: Option) => o.label.replace(/ \(.*\)$/, "");

  return (
    <form
      className="ask-about compare-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        const names = chosen.map(name);
        const q = `How do ${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} compare on ${topic.trim()}?`;
        const sides = chosen.map((o) => `${kind}:${o.id}`).join(",");
        try {
          sessionStorage.setItem(COMPARE_HANDOVER, q); // the main page starts this comparison straight away
        } catch {
          /* storage unavailable: the question is filled in, ready to ask */
        }
        window.location.href = `/?q=${encodeURIComponent(q)}&compare=${encodeURIComponent(sides)}&sides=${encodeURIComponent(names.join("|"))}`;
      }}
    >
      <fieldset className="chart-options">
        <legend>Compare</legend>
        {KINDS.map(([k, label]) => (
          <label key={k}>
            <input type="radio" name="kind" checked={kind === k} onChange={() => { setKind(k); setPicks([null, null, null]); }} /> {label}
          </label>
        ))}
      </fieldset>
      {[0, 1, 2].map((i) =>
        kind === "person" ? (
          <MemberPicker key={`${kind}-${i}`} id={`pick-${i}`} label={i === 2 ? "Third (optional)" : i === 0 ? "First" : "Second"} value={picks[i]} onChange={(o) => set(i, o)} />
        ) : (
          <div key={`${kind}-${i}`}>
            <label htmlFor={`pick-${i}`}>{i === 2 ? "Third (optional)" : i === 0 ? "First" : "Second"}</label>
            <select id={`pick-${i}`} value={picks[i]?.id ?? ""} onChange={(e) => set(i, options.find((o) => o.id === e.target.value) ?? null)}>
              <option value="">{i === 2 ? "None" : "Choose"}</option>
              {options.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        ),
      )}
      <label htmlFor="compare-topic">On</label>
      <div className="ask-row">
        <input id="compare-topic" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="a topic, such as immigration" />
        <button type="submit" className="primary" disabled={!ready}>
          Compare
        </button>
      </div>
      {!distinct && <p className="form-error">Choose different sides.</p>}
      <p className="muted small">
        The comparison is written straight away on the main page (it costs the site about the same as any other
        briefing).
        Comparisons of members work best for people who have both spoken on the topic.
      </p>
    </form>
  );
}

createRoot(document.getElementById("compare-root")!).render(
  <StrictMode>
    <Compare />
  </StrictMode>,
);
