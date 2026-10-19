import { useState } from "react";
import { api } from "../api";
import { SiteFooter } from "./SiteFooter";
import { BRAND } from "../brand";
import { Mark } from "./Mark";

export function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <div className="cover" aria-hidden="true">
        <Mark className="mark cover-mark" />
        <p className="cover-kicker">Parliamentary Debates</p>
        <p className="cover-title">{BRAND.name}</p>
        <p className="cover-years">Sourced briefings since 1997</p>
      </div>
      <form onSubmit={submit} className="login-card" aria-label="Sign in">
        <h1>Sign in to {BRAND.name}</h1>
        <p className="muted">Briefings on what Parliament has said. Every claim is cited to Hansard.</p>
        <label>
          Email
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small">Accounts are by invitation. Ask the administrator for access.</p>
      </form>
      <SiteFooter />
    </main>
  );
}
