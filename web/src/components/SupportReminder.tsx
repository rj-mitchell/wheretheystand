import { useEffect, useRef } from "react";

/**
 * A note on what the site costs, shown after every tenth new briefing a visitor has written in a day. It is not a
 * paywall: closing it carries on as before. Going through to the donation page stops it for the rest of the day.
 */
export function SupportReminder({ count, donateUrl, onClose }: { count: number; donateUrl: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const later = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    later.current?.focus(); // pressing Enter by habit should carry on, not open the donation page
  }, []);

  const donated = () => {
    void fetch("/api/support-clicked", { method: "POST" }).catch(() => undefined);
    ref.current?.close();
  };

  return (
    <dialog ref={ref} className="support-reminder" aria-labelledby="support-title" onClose={onClose}>
      <h2 id="support-title">Help keep Where MPs Stand free</h2>
      <p>
        You have had {count} new briefings written today. Each one costs the site about 10p to write, and the server
        that searches sixteen years of Parliament's record costs money every month.
      </p>
      <p>
        There are no adverts, and no money is taken from parties, campaigns or lobbyists, so the site depends on its
        readers. If it has been useful to you, a small donation keeps it free for everyone.
      </p>
      <div className="support-actions">
        <a className="button" href={donateUrl} target="_blank" rel="noopener" onClick={donated}>
          Support the site on Ko-fi
        </a>
        <button type="button" className="secondary" ref={later} onClick={() => ref.current?.close()}>
          Not now, carry on
        </button>
      </div>
      <p className="muted small">
        This is not a paywall: you can keep asking. You will see this note once every ten new briefings, and not again
        today if you go through to donate.
      </p>
    </dialog>
  );
}
