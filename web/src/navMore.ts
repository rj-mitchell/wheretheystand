/** The header's menus: each closes on Escape, a click elsewhere or another menu opening, and is marked when the
 * page is inside it. */
export function setUpNavMore(): void {
  const here = window.location.pathname.replace(/\.html$/, "");
  const menus = [...document.querySelectorAll<HTMLDetailsElement>(".nav-more")];
  for (const menu of menus) {
    if ([...menu.querySelectorAll("a")].some((a) => a.pathname === here)) menu.classList.add("current");
    menu.addEventListener("toggle", () => {
      if (menu.open) for (const other of menus) if (other !== menu) other.open = false;
    });
  }
  document.addEventListener("click", (e) => {
    for (const menu of document.querySelectorAll<HTMLDetailsElement>(".nav-more[open]")) {
      if (!menu.contains(e.target as Node)) menu.open = false;
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    for (const menu of document.querySelectorAll<HTMLDetailsElement>(".nav-more[open]")) {
      menu.open = false;
      menu.querySelector("summary")?.focus();
    }
  });
}
