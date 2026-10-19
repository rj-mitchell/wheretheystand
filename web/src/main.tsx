import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { setUpNavMore } from "./navMore";
import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "./styles.css";

// print every source list in full, not just the open ones
window.addEventListener("beforeprint", () => {
  for (const d of document.querySelectorAll("details.sources")) (d as HTMLDetailsElement).open = true;
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
setUpNavMore();
