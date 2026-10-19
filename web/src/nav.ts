/** The site's links, in one place. The header holds Ask and This week on their own, then three menus by
 * theme; the footer holds pages about the site. partials/header.html and partials/footer.html carry the same lists
 * for the static pages; a test keeps them in step. */
export interface NavLink {
  href: string;
  label: string;
}

export interface NavMenu {
  label: string;
  items: NavLink[];
}

export const NAV: NavLink[] = [
  { href: "/", label: "Ask" },
  { href: "/this-week", label: "This week" },
];

export const MENUS: NavMenu[] = [
  {
    label: "MPs and peers",
    items: [
      { href: "/people", label: "Find an MP or peer" },
      { href: "/donations", label: "Donations" },
      { href: "/directorships", label: "Company roles" },
      { href: "/groups", label: "All-party groups" },
    ],
  },
  {
    label: "Parliaments",
    items: [
      { href: "/bills", label: "Bills" },
      { href: "/committees", label: "Committees" },
      { href: "/witnesses", label: "Committee witnesses" },
      { href: "/edms", label: "Early day motions" },
      { href: "/devolved#h-scottish-parliament", label: "Scottish Parliament" },
      { href: "/devolved#h-senedd", label: "Senedd" },
      { href: "/devolved#h-ni-assembly", label: "Northern Ireland Assembly" },
    ],
  },
  {
    label: "Explore",
    items: [
      { href: "/topics", label: "Topics" },
      { href: "/compare", label: "Compare" },
      { href: "/timeline", label: "Timelines" },
      { href: "/charts", label: "Charts" },
      { href: "/lighter-side", label: "The lighter side" },
    ],
  },
];

/** Every header link, in order (for the test that keeps the static pages' header in step). */
export const HEADER_LINKS: NavLink[] = [...NAV, ...MENUS.flatMap((m) => m.items)];

export const FOOTER: NavLink[] = [
  { href: "/about", label: "About" },
  { href: "/methodology", label: "Methodology" },
  { href: "/glossary", label: "Glossary" },
  { href: "/resources", label: "Other resources" },
  { href: "/developers", label: "Data and API" },
  { href: "/press", label: "Press and contact" },
  { href: "/status", label: "Status" },
  { href: "/corrections", label: "Corrections and problems" },
  { href: "/accessibility", label: "Accessibility" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "https://github.com/rj-mitchell/wheretheystand", label: "Source code" },
];

/** Set by the Compare page just before it opens the comparison on the main page, which then starts it at once. */
export const COMPARE_HANDOVER = "wts.compare";
