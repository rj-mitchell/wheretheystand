import type { ReactNode } from "react";
import { ATTRIBUTION } from "../attribution";
import { BRAND } from "../brand";
import { FOOTER } from "../nav";

/** The footer every page shows (partials/footer.html is the same for the static pages). */
export function SiteFooter({
  donateUrl,
  attribution = ATTRIBUTION,
  children,
}: {
  donateUrl?: string | null;
  attribution?: { text: string; url: string }[];
  children?: ReactNode;
}) {
  return (
    <footer className="footer full-width">
      <p className="independence">{BRAND.independence}</p>
      {children}
      <nav className="footer-nav" aria-label="About this site">
        {FOOTER.map((l) => (
          <a key={l.href} href={l.href}>
            {l.label}
          </a>
        ))}
        {donateUrl && <a href={donateUrl}>Support this site</a>}
      </nav>
      <p className="social">
        Follow:{" "}
        {BRAND.social.map((s, i) => (
          <span key={s.url}>
            {i > 0 && " · "}
            <a href={s.url} rel="me noreferrer" target="_blank">
              {s.name} {s.handle}
            </a>
          </span>
        ))}
      </p>
      <p className="licence">
        Briefings are licensed under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>; see the{" "}
        <a href="/terms">terms</a> for reuse.
      </p>
      {attribution.map((a) => (
        <a key={a.url} href={a.url} target="_blank" rel="noreferrer">
          {a.text}
        </a>
      ))}
    </footer>
  );
}

