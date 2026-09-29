// Installability and offline state, adapted from the devbox web-app starter.
// The service worker never caches app data; see public/sw.js.

const INSTALLED_DISPLAY = "(display-mode: standalone), (display-mode: minimal-ui), (display-mode: fullscreen), (display-mode: window-controls-overlay)";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export interface PwaState {
  standalone: boolean;
  showPromotion: boolean;
  canPrompt: boolean;
  installing: boolean;
  installError: boolean;
  workerStatus: "disabled" | "registering" | "ready" | "unsupported" | "failed";
  online: boolean;
}

export interface PwaController {
  getSnapshot: () => PwaState;
  subscribe: (listener: () => void) => () => void;
  dismiss: () => void;
  install: () => Promise<void>;
}

export function createPwaController(env: Window, { registerServiceWorker = false } = {}): PwaController {
  const display = env.matchMedia(INSTALLED_DISPLAY);
  const nav = env.navigator as Navigator & { standalone?: boolean };
  const isStandalone = () => display.matches || nav.standalone === true;
  let promptEvent: InstallPromptEvent | null = null;
  let installedThisSession = false;
  let dismissed = false;
  const listeners = new Set<() => void>();
  let state: PwaState = {
    standalone: isStandalone(),
    showPromotion: !isStandalone(),
    canPrompt: false,
    installing: false,
    installError: false,
    workerStatus: registerServiceWorker ? "registering" : "disabled",
    online: nav.onLine,
  };

  function update(changes: Partial<PwaState> = {}) {
    const standalone = isStandalone();
    state = { ...state, ...changes, standalone, showPromotion: !standalone && !installedThisSession && !dismissed, canPrompt: promptEvent !== null };
    for (const listener of listeners) listener();
  }

  env.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    if (isStandalone() || installedThisSession || dismissed || state.installing) return;
    promptEvent = event as InstallPromptEvent;
    update({ installError: false });
  });
  env.addEventListener("appinstalled", () => {
    // A session flag avoids a stale "installed" claim after a later uninstall.
    installedThisSession = true;
    promptEvent = null;
    update({ installing: false, installError: false });
  });
  env.addEventListener("online", () => update({ online: nav.onLine }));
  env.addEventListener("offline", () => update({ online: nav.onLine }));
  display.addEventListener("change", () => {
    if (isStandalone()) promptEvent = null;
    update({ installError: false });
  });

  if (registerServiceWorker) {
    if ("serviceWorker" in nav) {
      const scope = new URL("./", env.document.baseURI);
      Promise.resolve()
        .then(() => nav.serviceWorker.register(new URL("sw.js", scope), { scope: scope.pathname, updateViaCache: "none" }))
        .then(() => update({ workerStatus: "ready" }))
        .catch(() => update({ workerStatus: "failed" }));
    } else {
      update({ workerStatus: "unsupported" });
    }
  }

  return {
    getSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    dismiss() {
      dismissed = true;
      promptEvent = null;
      update({ installError: false });
    },
    async install() {
      if (!promptEvent || !state.showPromotion) return;
      // Browsers allow one prompt per event. Consume it before awaiting the dialog.
      const event = promptEvent;
      promptEvent = null;
      update({ installing: true, installError: false });
      try {
        await event.prompt();
        const choice = await event.userChoice;
        if (choice.outcome === "accepted") installedThisSession = true;
        else dismissed = true;
        update({ installing: false });
      } catch {
        dismissed = true;
        update({ installing: false, installError: !installedThisSession && !isStandalone() });
      }
    },
  };
}
