import { EventDetail } from "../components/EventDetail.tsx";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { EmptyState } from "../components/Notices.tsx";
import { Sheet } from "../components/Sheet.tsx";
import { href } from "../lib/routes.ts";
import { useData } from "../state/data.ts";
import { goBack } from "../state/router.ts";
import { useEffect } from "react";

function Missing({ loading }: { loading: boolean }) {
  if (loading) return <div className="event-detail skeleton-detail" aria-busy="true" />;
  return (
    <EmptyState title="Event not found">
      <p>It may have been cancelled, or it ended a while ago.</p>
      <Link className="button" to={href({ name: "agenda", from: null })}>See upcoming events</Link>
    </EmptyState>
  );
}

/** An event on its own page (opened from a link or bookmark). */
export function EventPage({ id }: { id: string }) {
  const { catalog, loading } = useData();
  const ev = catalog?.byId.get(id);
  useEffect(() => {
    if (ev) document.title = `${ev.name} · Events`;
  }, [ev]);
  return (
    <div className="page event-page">
      <div className="page-header back-row">
        <Link className="button subtle" to={href({ name: "agenda", from: null })}>
          <Icon name="chevronLeft" size={18} /> Upcoming
        </Link>
      </div>
      {ev ? <EventDetail ev={ev} /> : <Missing loading={loading || !catalog} />}
    </div>
  );
}

/** An event over the list it was opened from. Closing returns to that list and its scroll position. */
export function EventSheet({ id, background }: { id: string; background: string }) {
  const { catalog, loading } = useData();
  const ev = catalog?.byId.get(id);
  useEffect(() => {
    if (!ev) return;
    const previous = document.title;
    document.title = `${ev.name} · Events`;
    return () => {
      document.title = previous;
    };
  }, [ev]);
  return (
    <Sheet label={ev?.name ?? "Event"} onClose={() => goBack(background)}>
      {ev ? <EventDetail ev={ev} /> : <Missing loading={loading || !catalog} />}
    </Sheet>
  );
}
