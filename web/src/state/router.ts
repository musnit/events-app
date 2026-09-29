// A small history-API router. The URL is the source of truth for the current view and filters.

import { useSyncExternalStore } from "react";
import { filtersFromParams, type Filters } from "../lib/filters.ts";
import { parseRoute, type Route } from "../lib/routes.ts";

export interface AppLocation {
  /** Path relative to the app root, without a leading slash. */
  path: string;
  search: string;
  route: Route;
  filters: Filters;
  /** For an event opened over a list: the list's relative URL, drawn underneath. */
  background: string | null;
  key: string;
}

interface HistoryState {
  key: string;
  background: string | null;
}

const listeners = new Set<() => void>();
const scrollPositions = new Map<string, number>();

function basePath(): string {
  return new URL(document.baseURI).pathname;
}

export function relativePath(pathname: string): string {
  const base = basePath();
  return pathname.startsWith(base) ? pathname.slice(base.length) : pathname.replace(/^\/+/, "");
}

function readLocation(): AppLocation {
  const state = (history.state || {}) as Partial<HistoryState>;
  const path = relativePath(location.pathname);
  return {
    path,
    search: location.search,
    route: parseRoute(path),
    filters: filtersFromParams(new URLSearchParams(location.search)),
    background: state.background ?? null,
    key: state.key ?? "initial",
  };
}

let current = readLocation();

function emit(): void {
  current = readLocation();
  for (const listener of listeners) listener();
}

function newKey(): string {
  return Math.random().toString(36).slice(2, 10);
}

if (typeof window !== "undefined") {
  history.scrollRestoration = "manual";
  if (!history.state?.key) history.replaceState({ key: "initial", background: null } satisfies HistoryState, "");
  window.addEventListener("popstate", () => {
    emit();
    const y = scrollPositions.get(current.key) ?? 0;
    // Wait for the view to render before restoring where the reader was.
    requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, y)));
  });
}

export interface NavigateOptions {
  replace?: boolean;
  /** Keep this relative URL rendered underneath (used for the event sheet). */
  background?: string | null;
  /** Keep the scroll position, e.g. when only filters change. */
  keepScroll?: boolean;
}

export function navigate(to: string, options: NavigateOptions = {}): void {
  const url = new URL(to, document.baseURI);
  if (url.origin !== location.origin) {
    location.assign(url);
    return;
  }
  scrollPositions.set(current.key, window.scrollY);
  const state: HistoryState = { key: options.replace ? current.key : newKey(), background: options.background ?? null };
  const target = url.pathname + url.search + url.hash;
  if (options.replace) history.replaceState(state, "", target);
  else history.pushState(state, "", target);
  emit();
  if (!options.keepScroll && !options.background && !options.replace) window.scrollTo(0, 0);
}

/** Close an overlay: go back if we came from inside the app, otherwise go to ``fallback``. */
export function goBack(fallback: string): void {
  if (current.background !== null && history.length > 1) history.back();
  else navigate(fallback, { replace: true });
}

export function useLocation(): AppLocation {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}

/** The current relative URL (path + query), e.g. for use as an overlay background. */
export function currentRelativeUrl(): string {
  return (current.path || "./") + current.search;
}
