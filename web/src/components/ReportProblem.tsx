import { useId, useState } from "react";
import { api, ApiError, type ReportInput } from "../api";

const KINDS: { value: ReportInput["kind"]; label: string }[] = [
  { value: "correction", label: "An error in this briefing" },
  { value: "complaint", label: "Someone is represented unfairly" },
  { value: "general", label: "Something else" },
];

/** Report a problem with a briefing. Reports are private; corrections are listed publicly on /corrections. */
export function ReportProblem({ briefingId }: { briefingId?: string }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ReportInput["kind"]>("correction");
  const [message, setMessage] = useState("");
  const [contact, setContact] = useState("");
  const [website, setWebsite] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  if (state === "sent") {
    return (
      <p className="report-sent muted small" role="status">
        Thank you. Your report has been received{contact ? " and we will reply if we need to" : ""}. Corrections are
        listed on the <a href="/corrections">corrections page</a>.
      </p>
    );
  }
  if (!open) {
    return (
      <button type="button" className="link small report-open" onClick={() => setOpen(true)}>
        Report a problem
      </button>
    );
  }
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (message.trim().length < 10) {
      setError("Please describe the problem in a sentence or two.");
      return;
    }
    setState("sending");
    try {
      await api.report({ kind, message, briefing_id: briefingId ?? null, contact: contact || null, website });
      setState("sent");
    } catch (err) {
      setState("idle");
      setError(err instanceof ApiError ? err.message : "The report could not be sent. Please try again.");
    }
  };
  return (
    <form className="report-form" onSubmit={submit} aria-labelledby={`${id}-title`}>
      <h3 id={`${id}-title`}>Report a problem</h3>
      <fieldset>
        <legend>What is wrong?</legend>
        {KINDS.map((k) => (
          <label key={k.value}>
            <input type="radio" name={`${id}-kind`} checked={kind === k.value} onChange={() => setKind(k.value)} />
            {k.label}
          </label>
        ))}
      </fieldset>
      <label>
        Details
        <textarea
          required
          minLength={10}
          maxLength={4000}
          rows={4}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="For example, which statement is wrong and what the record says."
        />
      </label>
      <label>
        Your email (optional, only so we can reply)
        <input type="email" maxLength={200} autoComplete="email" value={contact} onChange={(e) => setContact(e.target.value)} />
      </label>
      <label className="trap" aria-hidden="true">
        Leave this empty
        <input tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" className="secondary" disabled={state === "sending"}>
          {state === "sending" ? "Sending…" : "Send report"}
        </button>
        <button type="button" className="link small" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      <p className="muted small">
        Reports are private. Your email is deleted once the report is dealt with. See the{" "}
        <a href="/privacy">privacy notice</a>.
      </p>
    </form>
  );
}
