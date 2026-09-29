import { useEffect, useMemo } from "react";
import { Shell } from "./components/Shell.tsx";
import { api } from "./lib/api.ts";
import { readImportHash } from "./lib/bookmarklets.ts";
import { monthKey } from "./lib/dates.ts";
import { filtersFromParams } from "./lib/filters.ts";
import { pluralize } from "./lib/format.ts";
import type { PwaController } from "./lib/pwa.ts";
import { href, parseRoute, type Route } from "./lib/routes.ts";
import { data } from "./state/data.ts";
import { navigate, relativePath, useLocation } from "./state/router.ts";
import { toast } from "./state/toasts.ts";
import { ViewContext, type ViewLocation } from "./state/view.ts";
import { AgendaView } from "./views/AgendaView.tsx";
import { CalendarsView, CalendarView } from "./views/CalendarsView.tsx";
import { DayView } from "./views/DayView.tsx";
import { EventPage, EventSheet } from "./views/EventView.tsx";
import { MonthView } from "./views/MonthView.tsx";
import { NotFoundView } from "./views/NotFoundView.tsx";
import { SavedView } from "./views/SavedView.tsx";
import { SourcesView } from "./views/SourcesView.tsx";

function View({ route, pwa }: { route: Route; pwa: PwaController }) {
  switch (route.name) {
    case "agenda": return <AgendaView from={route.from} />;
    case "day": return <DayView date={route.date} />;
    case "month": return <MonthView month={route.month || monthKey(new Date())} />;
    case "event": return <EventPage id={route.id} />;
    case "saved": return <SavedView />;
    case "calendars": return <CalendarsView />;
    case "calendar": return <CalendarView id={route.id} />;
    case "sources": return <SourcesView section={route.section} pwa={pwa} />;
    case "notFound": return <NotFoundView />;
  }
}

/** Bookmarklets come back to the app with their payload in the URL fragment. */
async function handleImport(): Promise<void> {
  const request = readImportHash(location.hash);
  if (!location.hash.startsWith("#import=") && !location.hash.startsWith("#pfimport=")) return;
  const section = request?.kind === "partiful" ? "partiful" : "luma";
  // Drop the fragment right away: it can hold a login token.
  navigate(href({ name: "sources", section }), { replace: true });
  if (!request) {
    toast("That import link was damaged. Run the bookmarklet again.", "error");
    return;
  }
  try {
    if (request.kind === "luma") {
      const r = await api.lumaImport(request.payload);
      const changes = [r.added && `${r.added} new`, r.removed && `${r.removed} unfollowed`].filter(Boolean).join(", ");
      toast(`Imported ${pluralize(r.total, "Luma calendar")}${changes ? ` (${changes})` : ""}. Syncing now…`, "info", 8000);
    } else {
      const r = await api.partifulImport(request.payload);
      toast(`Connected Partiful${r.name ? ` as ${r.name}` : ""}. Pulling your events…`, "info", 8000);
    }
    await data.afterSourcesChanged();
  } catch (e) {
    toast(`Import failed: ${(e as Error).message}`, "error", 10000);
  }
}

export function App({ pwa }: { pwa: PwaController }) {
  const location_ = useLocation();

  useEffect(() => {
    data.start();
    void handleImport();
    // "/" jumps to search, as on most sites with one.
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
      if (event.key !== "/" || typing || event.metaKey || event.ctrlKey || event.altKey) return;
      const search = document.querySelector<HTMLInputElement>(".page-header .search input");
      if (search) {
        event.preventDefault();
        search.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // An event opened from a list keeps the list rendered underneath it.
  const background = useMemo<ViewLocation | null>(() => {
    if (location_.route.name !== "event" || location_.background === null) return null;
    const url = new URL(location_.background, document.baseURI);
    return { route: parseRoute(relativePath(url.pathname)), filters: filtersFromParams(url.searchParams) };
  }, [location_.route, location_.background]);

  const main: ViewLocation = background ?? { route: location_.route, filters: location_.filters };

  useEffect(() => {
    // Keep keyboard and screen-reader users oriented after a page change.
    if (!background) document.getElementById("main")?.focus({ preventScroll: true });
  }, [location_.path, background]);

  return (
    <Shell activeRoute={main.route}>
      <ViewContext.Provider value={main}>
        <View route={main.route} pwa={pwa} />
      </ViewContext.Provider>
      {background && location_.route.name === "event" && (
        <EventSheet id={location_.route.id} background={location_.background ?? "./"} />
      )}
    </Shell>
  );
}
