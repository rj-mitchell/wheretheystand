/// <reference types="vitest" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

/** Static pages share one header and footer: <!--#include header--> pulls in partials/header.html. */
function partials(): Plugin {
  return {
    name: "html-partials",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => {
        // partials may include other partials (the header includes the seal)
        for (let depth = 0; depth < 5 && html.includes("<!--#include "); depth++) {
          html = html.replace(/<!--#include ([\w-]+)-->/g, (_m, name: string) =>
            readFileSync(here(`./partials/${name}.html`), "utf8"),
          );
        }
        return html;
      },
    },
  };
}

export default defineConfig({
  plugins: [partials(), react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      input: {
        main: here("./index.html"),
        methodology: here("./methodology.html"),
        status: here("./status.html"),
        corrections: here("./corrections.html"),
        accessibility: here("./accessibility.html"),
        resources: here("./resources.html"),
        glossary: here("./glossary.html"),
        about: here("./about.html"),
        privacy: here("./privacy.html"),
        terms: here("./terms.html"),
        person: here("./person.html"),
        people: here("./people.html"),
        topics: here("./topics.html"),
        timeline: here("./timeline.html"),
        bills: here("./bills.html"),
        bill: here("./bill.html"),
        debate: here("./debate.html"),
        week: here("./week.html"),
        edms: here("./edms.html"),
        edm: here("./edm.html"),
        committees: here("./committees.html"),
        committee: here("./committee.html"),
        inquiry: here("./inquiry.html"),
        witnesses: here("./witnesses.html"),
        donations: here("./donations.html"),
        directorships: here("./directorships.html"),
        groups: here("./groups.html"),
        group: here("./group.html"),
        devolved: here("./devolved.html"),
        devolvedday: here("./devolvedday.html"),
        developers: here("./developers.html"),
        press: here("./press.html"),
        evidence: here("./evidence.html"),
        charts: here("./charts.html"),
        lighter: here("./lighter-side.html"),
        compare: here("./compare.html"),
        notfound: here("./404.html"),
        admin: here("./admin.html"),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
