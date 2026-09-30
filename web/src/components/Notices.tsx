import type { ReactNode } from "react";
import { useData } from "../state/data.ts";
import { dismissToast, useToasts } from "../state/toasts.ts";
import { Icon } from "./Icon.tsx";

/** Shows app-wide conditions: an expired portal sign-in, no connection, or a server that cannot be reached. */
export function Banners() {
  const { sessionExpired, offline, error, catalog } = useData();
  if (sessionExpired) {
    return (
      <div className="banner warn" role="alert">
        <Icon name="alert" />
        <span>Your sign-in expired.</span>
        <button type="button" className="button small" onClick={() => location.reload()}>Sign in again</button>
      </div>
    );
  }
  if (offline) {
    return (
      <div className="banner" role="status">
        <Icon name="offline" />
        <span>{catalog ? "You're offline. Showing what was already loaded." : "You're offline. Reconnect to load your events."}</span>
      </div>
    );
  }
  if (error && catalog) {
    return (
      <div className="banner" role="status">
        <Icon name="alert" />
        <span>Couldn't refresh: {error}</span>
      </div>
    );
  }
  return null;
}

export function Toasts() {
  const toasts = useToasts();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
          <span>{t.text}</span>
          <button type="button" className="icon-button small" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>
            <Icon name="close" size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {children}
    </div>
  );
}
