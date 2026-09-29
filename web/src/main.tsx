import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { createPwaController } from "./lib/pwa.ts";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";

// Registered only in the built app: the worker gives an offline page, never cached data.
const pwa = createPwaController(window, { registerServiceWorker: import.meta.env.PROD });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App pwa={pwa} />
  </StrictMode>,
);
