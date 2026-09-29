// The worker stores nothing. Portal sign-in must run on every visit, and events are private,
// so navigations always go to the network; when that fails the user sees a generic offline page.
const offlinePage = `<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>You're offline</title>
<style>body{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;padding:24px;text-align:center}</style>
<main><h1>You're offline</h1>
<p>Reconnect to see your events. Nothing private is saved on this device.</p>
<p><a href="./">Try again</a></p></main></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || request.mode !== "navigate" || url.origin !== self.location.origin) return;
  event.respondWith(
    fetch(request).catch(() => new Response(offlinePage, {
      status: 503,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
      },
    })),
  );
});
