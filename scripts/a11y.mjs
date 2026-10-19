// Accessibility check for CI: loads the site's main pages in Chromium, light and dark, and fails on any serious or
// critical WCAG 2.2 AA problem found by axe. Usage: node scripts/a11y.mjs http://localhost:8000
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const base = process.argv[2] ?? "http://localhost:8000";
const pages = ["/", "/about", "/methodology", "/glossary", "/developers", "/press", "/topics", "/bills", "/people",
  "/charts", "/this-week", "/committees", "/witnesses", "/devolved", "/edms", "/timeline", "/compare", "/corrections",
  "/accessibility", "/privacy", "/terms", "/status", "/groups", "/donations", "/directorships",
  "/resources", "/lighter-side", "/no-such-page"];

const browser = await chromium.launch();
let failures = 0;
for (const scheme of ["light", "dark"]) {
  for (const width of [1280, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: scheme, bypassCSP: true });
    const page = await ctx.newPage();
    for (const path of pages) {
      await page.goto(base + path, { waitUntil: "networkidle" }).catch(() => {});
      await page.addScriptTag({ content: axe });
      const result = await page.evaluate(async () =>
        window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }));
      for (const v of result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
        failures++;
        console.log(`${scheme} ${width}px ${path}: ${v.id} (${v.impact}) ${v.help}`);
        for (const n of v.nodes.slice(0, 3)) console.log(`    ${n.target.join(" ")}`);
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (overflow > 1) {
        failures++;
        console.log(`${scheme} ${width}px ${path}: the page scrolls sideways by ${overflow}px`);
      }
    }
    await ctx.close();
  }
}
await browser.close();
console.log(failures ? `${failures} accessibility problem(s) found` : `No serious accessibility problems on ${pages.length} pages`);
process.exit(failures ? 1 : 0);
