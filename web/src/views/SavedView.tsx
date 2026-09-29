import { useCallback, useMemo } from "react";
import { EventList } from "../components/EventList.tsx";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { EmptyState } from "../components/Notices.tsx";
import { Page } from "../components/Page.tsx";
import { apiUrl } from "../lib/api.ts";
import { activeFilterCount, filtersToParams } from "../lib/filters.ts";
import { hasEnded } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { EventItem } from "../lib/types.ts";
import { useData } from "../state/data.ts";
import { useView } from "../state/view.ts";
import { useFilteredEvents, useNow } from "../state/hooks.ts";
import { LoadingCards, NoEvents } from "./common.tsx";

/** Your plan: events you're going to or saved, wherever they are. */
export function SavedView() {
  const { route, filters } = useView();
  const { catalog, loading } = useData();
  const now = useNow();
  const scope = useCallback((ev: EventItem) => (ev.going || ev.starred) && !hasEnded(ev, now), [now]);
  const withHidden = useMemo(() => ({ ...filters, hidden: true }), [filters]);
  const { events, counts } = useFilteredEvents(withHidden, { scope, area: "all" });
  const narrowed = activeFilterCount(filters) > 0 || !!filters.q;

  return (
    <Page
      title="Saved"
      eyebrow="Going and starred"
      filterable={{ route, filters, counts, resultCount: events.length }}
      controls={<a className="button small" href={apiUrl("feed.ics")} download="my-events.ics"><Icon name="download" size={16} />.ics</a>}
    >
      {loading && !catalog ? <LoadingCards count={3} /> : (
        <EventList
          events={events}
          resetKey={filtersToParams(filters).toString()}
          empty={narrowed ? <NoEvents route={route} filters={filters} what="saved events" /> : (
            <EmptyState title="Nothing saved yet">
              <p>Tap the star on any event to keep it here. Events you RSVP'd to on Luma or Partiful show up automatically.</p>
              <Link className="button" to={href({ name: "agenda", from: null })}>Browse upcoming events</Link>
            </EmptyState>
          )}
        />
      )}
    </Page>
  );
}
