// JSON API client. URLs are relative to <base href>, so the app works at / or under a proxy prefix.

import type { AreaChoice, EventsPayload, Status } from "./types.ts";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** The portal's sign-in expired: requests get redirected to the login page. */
export class SessionExpired extends Error {
  constructor() {
    super("Your sign-in expired.");
  }
}

export class Offline extends Error {
  constructor() {
    super("You are offline.");
  }
}

export function apiUrl(path: string): string {
  return new URL(path, document.baseURI).toString();
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), { credentials: "same-origin", redirect: "manual", cache: "no-store", ...init });
  } catch {
    throw navigator.onLine === false ? new Offline() : new ApiError(0, "Could not reach the server.");
  }
  if (response.type === "opaqueredirect" || response.status === 401) throw new SessionExpired();
  return response;
}

async function json<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, (data as { error?: string }).error || response.statusText || "Request failed");
  return data as T;
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { ...init.headers, "Content-Type": "application/json" };
  }
  return json<T>(await request(path, init));
}

export async function fetchEvents(etag: string | null): Promise<{ payload: EventsPayload; etag: string | null } | null> {
  const response = await request("api/events", { headers: etag ? { "If-None-Match": etag } : {} });
  if (response.status === 304) return null;
  const payload = await json<EventsPayload>(response);
  return { payload, etag: response.headers.get("ETag") };
}

export const fetchStatus = () => request("api/status").then((r) => json<Status>(r));

export const api = {
  mark: (id: string, change: { starred?: boolean; hidden?: boolean }) =>
    send<{ starred: boolean; hidden: boolean }>("PUT", `api/events/${encodeURIComponent(id)}/mark`, change),
  prefs: (change: { muted_calendars?: string[]; area?: AreaChoice }) => send<Status["prefs"]>("PUT", "api/prefs", change),
  sync: (source?: string) => send<{ queued: number }>("POST", "api/sync", source ? { source } : {}),
  lumaImport: (payload: unknown) =>
    send<{ total: number; added: number; removed: number; going: number; session: boolean }>("POST", "api/luma/import", { payload }),
  lumaAddLinks: (text: string) =>
    send<{ added: { kind: "calendar" | "event"; id: string; name: string }[]; failed: { link: string; error: string }[] }>(
      "POST", "api/luma/links", { text }),
  lumaRemoveCalendar: (id: string) => send<{ ok: true }>("DELETE", `api/luma/calendars/${encodeURIComponent(id)}`),
  lumaRemoveEvent: (id: string) => send<{ ok: true }>("DELETE", `api/luma/events/${encodeURIComponent(id)}`),
  lumaSetSession: (session_key: string) => send<{ ok: true }>("PUT", "api/luma/session", { session_key }),
  lumaClearSession: () => send<{ ok: true }>("DELETE", "api/luma/session"),
  lumaSetIcs: (url: string) => send<{ ok: true; events: number }>("PUT", "api/luma/ics", { url }),
  lumaClearIcs: () => send<{ ok: true }>("DELETE", "api/luma/ics"),
  partifulImport: (payload: unknown) => send<{ uid: string; name: string | null }>("POST", "api/partiful/import", { payload }),
  partifulRemove: (uid: string) => send<{ ok: true }>("DELETE", `api/partiful/accounts/${encodeURIComponent(uid)}`),
  partifulSetFeed: (url: string) => send<{ ok: true; events: number }>("PUT", "api/partiful/feed", { url }),
  partifulClearFeed: () => send<{ ok: true }>("DELETE", "api/partiful/feed"),
};
