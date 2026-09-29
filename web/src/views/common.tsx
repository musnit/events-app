import { activeFilterCount, EMPTY_FILTERS, type Filters } from "../lib/filters.ts";
import { href, type Route } from "../lib/routes.ts";
import { useData } from "../state/data.ts";
import { navigate } from "../state/router.ts";
import { Link } from "../components/Link.tsx";
import { EmptyState } from "../components/Notices.tsx";

export function LoadingCards({ count = 6 }: { count?: number }) {
  return (
    <div className="event-list" aria-busy="true" aria-label="Loading events">
      <div className="cards">
        {Array.from({ length: count }, (_, i) => <div key={i} className="event-card skeleton" />)}
      </div>
    </div>
  );
}

/** What to show when a list is empty: first-run setup, or a way out of over-narrow filters. */
export function NoEvents({ route, filters, what = "events" }: { route: Route; filters: Filters; what?: string }) {
  const { catalog, status } = useData();
  const syncing = Object.values(status?.workers ?? {}).some((w) => w.busy);
  if (catalog && catalog.events.length === 0) {
    return (
      <EmptyState title={syncing ? "Pulling your events…" : "No events yet"}>
        <p>{syncing ? "Events appear here as each calendar finishes syncing." : "Connect Luma or Partiful to fill this calendar."}</p>
        {!syncing && <Link className="button primary" to={href({ name: "sources", section: null })}>Connect sources</Link>}
      </EmptyState>
    );
  }
  const narrowed = activeFilterCount(filters) > 0 || !!filters.q;
  return (
    <EmptyState title={`No ${what} match`}>
      {narrowed ? (
        <>
          <p>Try fewer filters or a different search.</p>
          <button type="button" className="button" onClick={() => navigate(href(route, { ...EMPTY_FILTERS }), { replace: true })}>
            Clear filters
          </button>
        </>
      ) : (
        <p>Nothing here yet.</p>
      )}
    </EmptyState>
  );
}
