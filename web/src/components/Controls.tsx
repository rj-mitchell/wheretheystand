import type { Settings } from "../types";

interface Props {
  settings: Settings;
  onChange: (s: Settings) => void;
  answersEnabled: boolean;
}

export function recencyLabel(v: number): string {
  if (v <= 0) return "Off — all years ranked on relevance alone";
  const halfLife = 2 / v;
  return `Half-life ${halfLife >= 10 ? Math.round(halfLife) : halfLife.toFixed(1)} years`;
}

export function Controls({ settings, onChange, answersEnabled }: Props) {
  const set = (patch: Partial<Settings>) => onChange({ ...settings, ...patch });
  const scope = (patch: Partial<Settings["scope"]>) => set({ scope: { ...settings.scope, ...patch } });
  return (
    <section className="controls" id="search-settings" aria-label="Search settings">
      <fieldset className="mode">
        <legend>Answer</legend>
        <label>
          <input
            type="radio"
            name="mode"
            checked={settings.mode === "summary"}
            onChange={() => set({ mode: "summary" })}
            disabled={!answersEnabled}
          />
          Executive summary
        </label>
        <label>
          <input
            type="radio"
            name="mode"
            checked={settings.mode === "comprehensive"}
            onChange={() => set({ mode: "comprehensive" })}
            disabled={!answersEnabled}
          />
          Chronological briefing
        </label>
      </fieldset>

      <div className="slider">
        <span>
          <label htmlFor="recency">Recency bias</label>
          <output htmlFor="recency">{recencyLabel(settings.recency)}</output>
        </span>
        <input
          id="recency"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={settings.recency}
          onChange={(e) => set({ recency: Number(e.target.value) })}
          aria-valuetext={recencyLabel(settings.recency)}
        />
        <small className="muted">Older records are down-weighted but never dropped.</small>
      </div>

      <div className="slider">
        <span>
          <label htmlFor="role-weight">Role weighting</label>
          <output htmlFor="role-weight">{settings.roleWeight.toFixed(2)}×</output>
        </span>
        <input
          id="role-weight"
          type="range"
          min={1}
          max={2}
          step={0.05}
          value={settings.roleWeight}
          onChange={(e) => set({ roleWeight: Number(e.target.value) })}
          aria-valuetext={`${settings.roleWeight.toFixed(2)} times`}
        />
        <small className="muted">Boosts ministers and committee chairs for the topic; 1.0× treats everyone equally.</small>
      </div>

      <fieldset className="scope">
        <legend>Include</legend>
        <label>
          <input type="checkbox" checked={settings.scope.written} onChange={(e) => scope({ written: e.target.checked })} />
          Written answers &amp; statements
        </label>
        <label>
          <input type="checkbox" checked={settings.scope.pbc} onChange={(e) => scope({ pbc: e.target.checked })} />
          Public Bill Committees
        </label>
        <label>
          <input type="checkbox" checked={settings.scope.votes} onChange={(e) => scope({ votes: e.target.checked })} />
          Votes (divisions)
        </label>
        <label>
          <input
            type="checkbox"
            checked={settings.scope.edms ?? true}
            onChange={(e) => scope({ edms: e.target.checked })}
          />
          Early day motions
        </label>
        <label>
          <input
            type="checkbox"
            checked={settings.scope.devolved ?? false}
            onChange={(e) => scope({ devolved: e.target.checked })}
          />
          Devolved parliaments (Holyrood, the Senedd, the NI Assembly)
        </label>
        <label>
          <input
            type="checkbox"
            checked={settings.scope.evidence ?? false}
            onChange={(e) => scope({ evidence: e.target.checked })}
          />
          Witnesses' evidence to committees
        </label>
      </fieldset>
    </section>
  );
}
